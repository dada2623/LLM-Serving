"""CPU-offload ablation: does per-token PCIe weight traffic explain ~3.5 tok/s?

This is a focused follow-up to P0.  It changes exactly one serving knob
(``--cpu-offload-gb``: 5 / 2 / 0 GiB) while keeping weights, tokenizer,
prompt set, sampling, dtype, context length, batching limits and CUDA-graph
state fixed.  One run per arm, same 128/512/2048-token requests as P0.

It also measures the effective host<->device copy / UVA read bandwidth in the
same image and container runtime, so the decode speed can be checked against
the cost of moving the offloaded weights across PCIe.

Writes each arm to benchmark/runs/<run_id>/ and a cross-arm comparison to
benchmark/processed/offload-ablation.{csv,json}.  No model download, no
production .env change, no private data.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_p0 as p0  # noqa: E402


SERVICE = "vllm"
IMAGE = "llm-deployment/vllm:local"


def replace_flag(command, flag, value):
    if flag in command:
        command[command.index(flag) + 1] = str(value)
    else:
        command.extend([flag, str(value)])


def remove_flag(command, flag):
    if flag in command:
        index = command.index(flag)
        del command[index:index + 2]


def probe_pcie(folder):
    """Measure large-copy H2D/D2H and UVA zero-copy read bandwidth."""
    folder.mkdir(parents=True, exist_ok=True)
    code = r"""
import json, statistics, time
import torch
import vllm  # registers torch.ops._C.get_cuda_view_from_cpu_tensor
n = 256 * 1024 * 1024
out = {"torch": torch.__version__, "cuda_build": torch.version.cuda,
       "device": torch.cuda.get_device_name(0), "bytes": n}
def bench(fn, iters=15, warmup=3):
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        t = time.perf_counter()
        fn()
        torch.cuda.synchronize()
        ts.append(time.perf_counter() - t)
    med = statistics.median(ts)
    return {"median_ms": med * 1000, "best_ms": min(ts) * 1000,
            "gib_per_s": (n / 2**30) / med, "gb_per_s": (n / 1e9) / med,
            "samples_ms": [round(x * 1000, 3) for x in ts]}
cpu = torch.empty(n, dtype=torch.uint8, pin_memory=True)
gpu = torch.empty(n, dtype=torch.uint8, device="cuda")
out["pinned_h2d"] = bench(lambda: gpu.copy_(cpu, non_blocking=True))
out["pinned_d2h"] = bench(lambda: cpu.copy_(gpu, non_blocking=True))
cpu_pageable = torch.empty(n, dtype=torch.uint8)
gpu2 = torch.empty(n, dtype=torch.uint8, device="cuda")
out["pageable_h2d"] = bench(lambda: gpu2.copy_(cpu_pageable, non_blocking=True))
try:
    view = torch.ops._C.get_cuda_view_from_cpu_tensor(cpu)
    out["uva_view"] = {"device": str(view.device), "shape": list(view.shape),
                       "is_cuda": view.is_cuda}
    out["uva_read"] = bench(lambda: gpu.copy_(view, non_blocking=True))
except Exception as exc:
    out["uva_view"] = {"error": repr(exc)}
print(json.dumps(out))
"""
    probe = p0.shell(
        ["docker", "run", "--rm", "--gpus", "all", "--entrypoint", "python3", IMAGE, "-c", code],
        timeout=300, check=False)
    payload = {"utc": p0.utc(), "image": IMAGE, "stdout": probe["stdout"],
               "stderr": probe["stderr"], "exit_code": probe["exit_code"]}
    parsed = None
    if probe["stdout"].strip():
        try:
            parsed = json.loads(probe["stdout"].strip().splitlines()[-1])
            payload["result"] = parsed
        except ValueError:
            pass
    p0.save(folder / "pcie-probe.json", payload)
    (folder / "pcie-probe.txt").write_text(probe["stdout"] + "\n" + probe["stderr"], encoding="utf-8")
    return parsed


def run_arm(offload_gib, gpu_mem, probe, ready_timeout):
    command = p0.command_profile(SERVICE, "existing")
    replace_flag(command, "--gpu-memory-utilization", gpu_mem)
    if offload_gib <= 0:
        remove_flag(command, "--cpu-offload-gb")
        remove_flag(command, "--cpu-offload-params")
    else:
        replace_flag(command, "--cpu-offload-gb", offload_gib)
        replace_flag(command, "--cpu-offload-params", "mlp")

    config_hash = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()[:8]
    run_id = (p0.datetime.now(p0.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
              + f"-vllm-offload-g{offload_gib}-{config_hash}-r01")
    folder = p0.BENCH / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "frozen-configs").mkdir()
    for path in (p0.BENCH / "configs").iterdir():
        if path.is_file():
            shutil.copy2(path, folder / "frozen-configs" / path.name)
    shutil.copy2(p0.BENCH / "datasets/p0-requests.json", folder / "dataset.json")
    (folder / "compose.override.json").write_text(
        json.dumps({"services": {SERVICE: {"command": command}}}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if probe is not None:
        p0.save(folder / "pcie-probe.json", probe)

    compose = p0.COMPOSE + ["-f", str(folder / "compose.override.json")]
    container = f"llm-deployment-{SERVICE}-1"
    port = p0.SETTINGS.get("VLLM_PORT", "8000")
    base = "http://127.0.0.1:" + port
    dataset = json.loads((p0.BENCH / "datasets/p0-requests.json").read_text(encoding="utf-8"))

    manifest = {
        "run_id": run_id, "stage": "P0-followup-offload-ablation", "service": SERVICE,
        "arm": f"offload-{offload_gib}gib", "cpu_offload_gb_arg": offload_gib,
        "gpu_memory_utilization": gpu_mem, "created_at_utc": p0.utc(),
        "status": "running", "command": command,
        "dataset_sha256": hashlib.sha256((p0.BENCH / "datasets/p0-requests.json").read_bytes()).hexdigest(),
        "production_env_sha256": hashlib.sha256((p0.ROOT / ".env").read_bytes()).hexdigest(),
        "collection_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sampling": {"temperature": 0, "top_p": 1, "seed": 20261030, "thinking": False,
                     "stream": True, "retry": False},
        "scope": "single-variable CPU-offload ablation; one request per length, not a tail-latency study",
        "hypothesis": "UVA-offloaded MLP weights are read over PCIe on every decode step; "
                      "decode tok/s should rise in proportion to the removed offload bytes",
    }
    p0.save(folder / "manifest.json", manifest)
    print(f"RUN {run_id} offload={offload_gib}GiB gpu_mem={gpu_mem}", flush=True)

    def event(kind, **extra):
        p0.append(folder / "events.jsonl",
                  {"utc": p0.utc(), "monotonic_ns": time.perf_counter_ns(), "event": kind} | extra)

    collector = p0.Collector(folder, base, container)
    log_since = p0.datetime.now(p0.timezone.utc).isoformat()
    records = []
    try:
        p0.shell(p0.COMPOSE + ["stop", "vllm", "sglang"], timeout=90)
        p0.save(folder / "gpu-processes-before.json",
                p0.shell(["nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory",
                          "--format=csv"]))
        collector.launch()
        event("no_model_baseline_begin", seconds=5)
        time.sleep(5)
        collector.phase = "startup"
        event("start_command")
        startup = time.perf_counter()
        result = p0.shell(compose + ["up", "-d", "--no-build", "--pull", "never",
                                     "--force-recreate", SERVICE], timeout=120)
        p0.save(folder / "compose-start.json", result)
        deadline = time.perf_counter() + ready_timeout
        while True:
            try:
                p0.get(base, "/health", timeout=2)
                break
            except (OSError, p0.urllib.error.URLError):
                state = p0.inspect(container)["State"]
                if not state["Running"]:
                    raise RuntimeError(f"Container exited before ready: exit={state['ExitCode']}, "
                                       f"OOMKilled={state.get('OOMKilled')}")
                if time.perf_counter() > deadline:
                    raise TimeoutError("readiness timeout")
                time.sleep(2)
        manifest["ready_observed_seconds"] = time.perf_counter() - startup
        event("ready", ready_observed_seconds=manifest["ready_observed_seconds"])
        print(f"READY offload={offload_gib} {manifest['ready_observed_seconds']:.1f}s", flush=True)
        collector.phase = "ready_metadata"
        state = p0.inspect(container)
        p0.save(folder / "container.json", {
            "Id": state["Id"], "Image": state["Image"], "State": state["State"],
            "Args": state["Args"], "Mounts": state["Mounts"],
            "HostConfig": {k: state["HostConfig"].get(k) for k in
                           ("DeviceRequests", "Memory", "MemorySwap", "NanoCpus",
                            "CpuQuota", "CpuPeriod", "ShmSize", "Runtime")}})
        # Capture the offloader source actually shipped in the image.
        source_code = r"""
import json, pathlib
paths = [pathlib.Path("/usr/local/lib/python3.12/dist-packages/vllm/model_executor/offloader/uva.py"),
         pathlib.Path("/usr/local/lib/python3.12/dist-packages/vllm/model_executor/offloader/base.py")]
print(json.dumps([{"path": str(p), "source": p.read_text()} for p in paths if p.exists()]))
"""
        p0.save(folder / "offloader-source.json",
                json.loads(p0.shell(["docker", "exec", container, "python3", "-c", source_code],
                                    timeout=30)["stdout"]))
        before = p0.get(base, "/metrics", timeout=5)
        (folder / "metrics-before.prom").write_text(p0.clean(before), encoding="utf-8")
        p0.save(folder / "metrics-schema.json", p0.metric_schema(before))
        collector.phase = "warmup"
        warmup = {"case_id": "warmup",
                  "messages": [{"role": "user", "content": "Ablation warmup 7c19e2. Reply with just hello."}],
                  "max_output_tokens": 8}
        event("warmup_begin")
        records.append(p0.stream_request(base, warmup, run_id, folder, warmup=True))
        if records[-1]["status"] != "success":
            raise RuntimeError("Warmup request failed")
        collector.phase = "warm_idle"
        event("warm_idle_begin", seconds=5)
        time.sleep(5)
        for case in dataset["cases"]:
            collector.phase = case["case_id"]
            event("request_begin", case_id=case["case_id"])
            records.append(p0.stream_request(base, case, run_id, folder))
            event("request_end", case_id=case["case_id"], status=records[-1]["status"])
            if records[-1]["status"] != "success":
                raise RuntimeError(f"Request failed: {case['case_id']}")
        collector.phase = "post_request"
        time.sleep(3)
        after = p0.get(base, "/metrics")
        (folder / "metrics-after.prom").write_text(p0.clean(after), encoding="utf-8")
        manifest["status"] = "passed"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = p0.clean(str(exc))
        p0.save(folder / "failure.json", {"error": str(exc), "traceback": p0.traceback.format_exc()})
        event("failure", error=str(exc))
        print(f"FAILED offload={offload_gib}: {p0.clean(str(exc))}", flush=True)
    finally:
        collector.phase = "stopping"
        try:
            logs = p0.shell(["docker", "logs", "--timestamps", "--since", log_since, container],
                            timeout=30, check=False)
            (folder / "server.log").write_text(p0.clean(logs["stdout"] + logs["stderr"]), encoding="utf-8")
            manifest["final_container_state"] = p0.inspect(container)["State"]
            p0.shell(p0.COMPOSE + ["stop", SERVICE], timeout=90)
            event("service_stopped")
        except Exception as exc:
            manifest["cleanup_error"] = p0.clean(str(exc))
        collector.finish()
        log = (folder / "server.log").read_text(encoding="utf-8") if (folder / "server.log").exists() else ""
        interesting = [line for line in log.splitlines()
                       if re.search(r"offload|UVA|model loading took|KV cache|init engine|Marlin", line, re.I)]
        (folder / "runtime-evidence.txt").write_text("\n".join(interesting), encoding="utf-8")

        def log_value(pattern, cast=float):
            match = re.search(pattern, log)
            return cast(match.group(1).replace(",", "")) if match else None

        manifest["offloaded_declared_gib"] = log_value(r"Total CPU offloaded parameters:\s*([\d.]+)")
        manifest["model_loading_gpu_gib"] = log_value(r"Model loading took ([\d.]+) GiB")
        manifest["kv_cache_gib"] = log_value(r"Available KV cache memory: ([\d.]+) GiB")
        manifest["kv_cache_tokens"] = log_value(r"GPU KV cache size: ([\d,]+) tokens", int)
        manifest["consumed_memory_gib"] = log_value(r"Actual usage is ([\d.]+) GiB for consumed memory")
        manifest["engine_init_seconds"] = log_value(r"init engine .*? took ([\d.]+) s")
        manifest["offloader"] = (re.search(r"Offloader set to (\w+)", log).group(1)
                                 if re.search(r"Offloader set to (\w+)", log) else None)
        manifest["production_env_unchanged"] = (
            manifest["production_env_sha256"] == hashlib.sha256((p0.ROOT / ".env").read_bytes()).hexdigest())
        manifest["resource_samples"] = {"gpu": len(collector.gpus), "host": len(collector.hosts),
                                        "metrics": len(collector.metrics)}
        p0.save(folder / "manifest.json", manifest)

        valid = [r for r in records if not r["warmup"] and r["status"] == "success"]
        decode = [r["decode_chunk_observed_tokens_per_s"] for r in valid
                  if r.get("decode_chunk_observed_tokens_per_s")]
        gpu_peak = max((float(r["memory_used_mib"]) for r in collector.gpus
                        if re.match(r"^\d+(\.\d+)?$", r["memory_used_mib"])), default=None)
        summary = {
            "run_id": run_id, "service": SERVICE, "arm": f"offload-{offload_gib}gib",
            "status": manifest["status"], "cpu_offload_gb_arg": offload_gib,
            "gpu_memory_utilization": gpu_mem,
            "offloaded_declared_gib": manifest.get("offloaded_declared_gib"),
            "ready_observed_seconds": manifest.get("ready_observed_seconds"),
            "model_loading_gpu_gib": manifest.get("model_loading_gpu_gib"),
            "consumed_memory_gib": manifest.get("consumed_memory_gib"),
            "kv_cache_gib": manifest.get("kv_cache_gib"),
            "kv_cache_tokens": manifest.get("kv_cache_tokens"),
            "sampled_gpu_peak_mib": gpu_peak,
            "offloader": manifest.get("offloader"),
            "median_decode_chunk_tokens_per_s": statistics.median(decode) if decode else None,
            "requests_successful": len(valid), "requests_planned": 3,
            "requests": [{k: r.get(k) for k in
                          ("case_id", "usage", "ttft_ms", "e2e_ms", "tpot_chunk_observed_ms",
                           "decode_chunk_observed_tokens_per_s", "e2e_output_tokens_per_s",
                           "content_chunks", "finish_reason", "input_token_count_matches")}
                         for r in valid],
            "error": manifest.get("error"),
        }
        if summary["median_decode_chunk_tokens_per_s"] and manifest.get("offloaded_declared_gib"):
            summary["implied_pcie_gib_per_s"] = (
                summary["median_decode_chunk_tokens_per_s"] * manifest["offloaded_declared_gib"])
        p0.save(folder / "summary.json", summary)
        print(f"ARM_COMPLETE offload={offload_gib} status={manifest['status']} "
              f"decode={summary['median_decode_chunk_tokens_per_s']}", flush=True)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offloads", default="5,2,0",
                        help="comma-separated --cpu-offload-gb values to run in order")
    parser.add_argument("--gpu-mem", type=float, default=0.72)
    parser.add_argument("--gpu-mem-zero", type=float, default=None,
                        help="override --gpu-mem for the 0 GiB arm (it needs more KV headroom)")
    parser.add_argument("--ready-timeout", type=int, default=1200)
    parser.add_argument("--skip-probe", action="store_true")
    parser.add_argument("--append", action="store_true",
                        help="merge this run's arm(s) into the existing processed comparison")
    args = parser.parse_args()
    offloads = [float(v) for v in args.offloads.split(",") if v.strip()]

    if not json.loads((p0.BENCH / "configs/model-assets.json").read_text(encoding="utf-8"))["all_files_match"]:
        raise RuntimeError("Model hashes failed")
    if not json.loads((p0.BENCH / "configs/tokenizer-comparison.json").read_text(encoding="utf-8"))[
            "identical_messages_and_input_ids"]:
        raise RuntimeError("Tokenizers are not comparable")

    stamp = p0.datetime.now(p0.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch_id = f"{stamp}-offload-ablation"
    probe_dir = p0.BENCH / "runs" / f"{batch_id}-pcie-probe-r01"
    probe = None
    if not args.skip_probe:
        print("PROBE pcie bandwidth", flush=True)
        probe = probe_pcie(probe_dir)
        if probe:
            for key in ("pinned_h2d", "pinned_d2h", "pageable_h2d", "uva_read"):
                if isinstance(probe.get(key), dict) and "gib_per_s" in probe[key]:
                    print(f"PROBE {key}: {probe[key]['gib_per_s']:.2f} GiB/s", flush=True)

    summaries = []
    for offload in offloads:
        arm_mem = args.gpu_mem_zero if (offload <= 0 and args.gpu_mem_zero is not None) else args.gpu_mem
        summaries.append(run_arm(offload, arm_mem, probe, args.ready_timeout))

    processed = p0.BENCH / "processed"
    processed.mkdir(exist_ok=True)
    prior = None
    if args.append and (processed / "offload-ablation.json").exists():
        prior = json.loads((processed / "offload-ablation.json").read_text(encoding="utf-8"))
    arms = {arm["arm"]: arm for arm in (prior or {}).get("arms", [])}
    for summary in summaries:
        arms[summary["arm"]] = summary
    arms = sorted(arms.values(), key=lambda arm: arm["cpu_offload_gb_arg"], reverse=True)
    probe_out = probe if probe is not None else (prior or {}).get("pcie_probe")

    rows = []
    for summary in arms:
        for request in summary.get("requests", []):
            rows.append({
                "run_id": summary["run_id"], "arm": summary["arm"],
                "cpu_offload_gb_arg": summary["cpu_offload_gb_arg"],
                "offloaded_declared_gib": summary.get("offloaded_declared_gib"),
                "gpu_memory_utilization": summary["gpu_memory_utilization"],
                "ready_s": summary.get("ready_observed_seconds"),
                "kv_cache_tokens": summary.get("kv_cache_tokens"),
                "gpu_peak_mib": summary.get("sampled_gpu_peak_mib"),
                "case_id": request["case_id"],
                "input_tokens": (request.get("usage") or {}).get("prompt_tokens"),
                "output_tokens": (request.get("usage") or {}).get("completion_tokens"),
                "ttft_ms": request.get("ttft_ms"), "e2e_ms": request.get("e2e_ms"),
                "tpot_ms": request.get("tpot_chunk_observed_ms"),
                "decode_tok_s": request.get("decode_chunk_observed_tokens_per_s"),
                "e2e_tok_s": request.get("e2e_output_tokens_per_s"),
                "implied_pcie_gib_per_s": summary.get("implied_pcie_gib_per_s"),
            })
    with (processed / "offload-ablation.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    p0.save(processed / "offload-ablation.json", {
        "batch_id": batch_id, "generated_at_utc": p0.utc(),
        "hypothesis": "per-token UVA reads of MLP weights across PCIe dominate decode latency",
        "fixed": {"service": SERVICE, "dtype": "float16", "max_model_len": 16384,
                  "max_num_seqs": 1, "max_num_batched_tokens": 512,
                  "enforce_eager": True, "gpu_memory_utilization": args.gpu_mem,
                  "gpu_memory_utilization_zero_arm": args.gpu_mem_zero,
                  "dataset": "benchmark/datasets/p0-requests.json"},
        "pcie_probe": probe_out, "arms": arms})
    print(f"BATCH_COMPLETE {batch_id}", flush=True)
    return 0 if all(s["status"] == "passed" for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
