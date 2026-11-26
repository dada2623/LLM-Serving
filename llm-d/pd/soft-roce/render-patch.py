#!/usr/bin/env python3
"""Render a reversible transport change for an existing 9B P/D Deployment."""
import argparse
import json
import sys

RESOURCE = "llm-d.ai/soft-roce"
RXE_UCX = "/srv/llm-serving/pd-runtime/ucx-rxe-rocm100"
TCP_UCX = "/srv/llm-serving/pd-runtime/ucx-1.23.2-rocm100"
AMD_VENV = "/srv/llm-serving/venvs/vllm-rocm100-v033"
CUDA_VERBS = "/opt/llm-serving/nvidia-rxe-verbs"
CUDA_UCX_MODULES = "/usr/local/lib/python3.12/dist-packages/nixl_cu13.libs/ucx"


def host_volume(name, path):
    return {"name": name, "hostPath": {"path": path, "type": "Directory"}}


def render(deployment, transport):
    name = deployment["metadata"]["name"]
    vendor = name.removeprefix("vllm-")
    if name not in ("vllm-amd", "vllm-nvidia"):
        raise ValueError("Expected vllm-amd or vllm-nvidia")
    spec = deployment["spec"]
    if spec["strategy"]["type"] != "Recreate" or spec["replicas"] != 1:
        raise ValueError("Soft-RoCE requires one replica per node and a Recreate strategy")
    template = spec["template"]["spec"]
    container = next(c for c in template["containers"] if c["name"] == "modelserver")
    args = container["args"]
    if args[args.index("--served-model-name") + 1] != "Qwen3.5-9B-AWQ":
        raise ValueError("Expected Qwen3.5-9B-AWQ")
    kv_config = json.loads(args[args.index("--kv-transfer-config") + 1])
    extra = kv_config["kv_connector_extra_config"]
    if (
        kv_config["kv_connector"] != "NixlConnector"
        or kv_config["kv_role"] != "kv_both"
        or kv_config["kv_buffer_device"] != "cpu"
        or not extra.get("enforce_handshake_compat", True)
        or "UCX" not in extra["backends"]
    ):
        raise ValueError("Expected CPU staging with UCX and default NIXL handshake")
    env = {entry["name"]: entry for entry in container["env"]}
    if env["VLLM_NIXL_SIDE_CHANNEL_PORT"]["value"] != "18080":
        raise ValueError("Expected NIXL side-channel TCP 18080")
    if env["VLLM_NIXL_SIDE_CHANNEL_HOST"]["valueFrom"]["fieldRef"]["fieldPath"] != "status.podIP":
        raise ValueError("Expected side-channel address from status.podIP")
    if env["VLLM_SSM_CONV_STATE_LAYOUT"]["value"] != "DS":
        raise ValueError("Expected DS hybrid KV layout")
    if "nixl-diagnostic" not in {v["name"] for v in template["volumes"]}:
        raise ValueError("Expected the production 9B Mamba CPU-staging patch")

    enabled = transport == "rxe"
    copy_tls = "rocm_copy" if vendor == "amd" else "cuda_copy"
    env_patch = {
        "UCX_TLS": f"{'rc' if enabled else 'tcp'},self,{copy_tls}",
        "UCX_NET_DEVICES": "rxe_llm:1" if enabled else "eth0",
        "UCX_IB_GID_INDEX": "1" if enabled else None,
        "UCX_LOG_LEVEL": "info" if enabled else None,
        "UCX_PROTO_INFO": "y" if enabled else None,
    }
    volume_name = "rxe-verbs" if vendor == "nvidia" else "ucx-runtime"
    if vendor == "amd":
        runtime = RXE_UCX if enabled else TCP_UCX
        env_patch.update(
            LD_PRELOAD=":".join(f"{runtime}/lib/{lib}.so.0" for lib in ("libucs", "libucp", "libuct")),
            LD_LIBRARY_PATH=env["LD_LIBRARY_PATH"]["value"].replace(TCP_UCX, runtime).replace(RXE_UCX, runtime),
            UCX_MODULE_DIR=f"{runtime}/lib/ucx",
        )
        current_host = next(v["hostPath"]["path"] for v in template["volumes"] if v["name"] == volume_name)
        host_path = current_host.rsplit("/", 1)[0] + "/" + runtime.rsplit("/", 1)[-1]
        volume = host_volume(volume_name, host_path)
        mount = {"name": volume_name, "mountPath": runtime, "readOnly": True}
    elif enabled:
        runtime = CUDA_VERBS
        env_patch.update(
            LD_LIBRARY_PATH=f"{runtime}:/usr/local/nvidia/lib64:/usr/local/nvidia/lib:/usr/local/cuda/lib64",
            UCX_MODULE_DIR=CUDA_UCX_MODULES,
        )
        volume = host_volume(volume_name, runtime)
        mount = {"name": volume_name, "mountPath": runtime, "readOnly": True}
    else:
        env_patch.update(LD_LIBRARY_PATH=None, UCX_MODULE_DIR=None)
        volume = {"name": volume_name, "$patch": "delete"}
        mount = None

    caps = container.get("securityContext", {}).get("capabilities", {}).get("add", [])
    capabilities = sorted(set(caps) | {"IPC_LOCK"}) if enabled else [cap for cap in caps if cap != "IPC_LOCK"]
    # Mount paths are the strategic merge key. Remove the old path when swapping AMD runtime directories.
    mounts = []
    for entry in container["volumeMounts"]:
        if entry["name"] == volume_name and (mount is None or entry["mountPath"] != mount["mountPath"]):
            mounts.append({"mountPath": entry["mountPath"], "$patch": "delete"})
    if mount is not None:
        mounts.append(mount)

    value = 1 if enabled else None
    return {
        "spec": {
            "template": {
                "metadata": {"annotations": {"llm-d.ai/kv-transport": transport}},
                "spec": {
                    "hostNetwork": enabled,
                    "dnsPolicy": "ClusterFirstWithHostNet" if enabled else "ClusterFirst",
                    "nodeSelector": {RESOURCE: "true" if enabled else None},
                    "volumes": [volume],
                    "containers": [{
                        "name": "modelserver",
                        "env": [
                            {"name": key, "value": val} if val is not None else {"name": key, "$patch": "delete"}
                            for key, val in env_patch.items()
                        ],
                        "resources": {"requests": {RESOURCE: value}, "limits": {RESOURCE: value}},
                        "securityContext": {"capabilities": {"add": capabilities}},
                        "volumeMounts": mounts,
                    }],
                },
            },
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transport", choices=("rxe", "tcp"))
    args = parser.parse_args()
    try:
        json.dump(render(json.load(sys.stdin), args.transport), sys.stdout)
        print()
    except (KeyError, ValueError, StopIteration) as exc:
        parser.exit(2, f"incompatible Deployment: {exc}\n")
