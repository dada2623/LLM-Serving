"""Freeze local P0 assets without downloading models or changing deployment settings."""
import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, timeout=120):
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    encoding = "utf-16-le" if b"\x00" in result.stdout[:100] else "utf-8"
    return {"exit_code": result.returncode, "stdout": result.stdout.decode(encoding, errors="replace").strip(),
            "stderr": result.stderr.decode("utf-8", errors="replace").strip()}


def save(name, obj):
    (BENCH / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def sanitize(value, secrets):
    if isinstance(value, dict):
        return {key: "<REDACTED>" if key.upper().endswith(("_KEY", "_TOKEN", "_PASSWORD", "_SECRET"))
                else sanitize(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "<REDACTED>")
    return value


def main():
    for folder in ("configs", "datasets", "runs", "processed", "figures", "reports"):
        (BENCH / folder).mkdir(exist_ok=True)
    settings = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines()
                    if line.strip() and not line.lstrip().startswith("#") and "=" in line)
    model = Path(settings["LOCAL_MODELS_DIR"]) / "Qwen3.5-9B-AWQ"
    catalog = json.loads((model / ".modelscope-manifest.json").read_text())
    rows = []
    for record in catalog["Data"]["Files"]:
        path = model / record["Path"]
        actual = sha(path)
        rows.append({"path": record["Path"], "bytes": path.stat().st_size,
                     "revision": record["Revision"], "expected_sha256": record["Sha256"],
                     "actual_sha256": actual, "matches": actual == record["Sha256"]})
        print(f"sha256 {path.name}: {'OK' if rows[-1]['matches'] else 'MISMATCH'}", flush=True)
    save("configs/model-assets.json", {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(), "model_directory": str(model),
        "requested_repository": "QuantTrio/Qwen3.5-9B-AWQ",
        "revision_source": "locally saved ModelScope file manifest; not revalidated against remote HEAD",
        "manifest_sha256": sha(model / ".modelscope-manifest.json"),
        "all_files_match": all(row["matches"] for row in rows), "files": rows,
        "model_config": json.loads((model / "config.json").read_text()),
    })
    compose = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(ROOT / ".env"),
               "-f", str(ROOT / "compose.yaml")]
    info = json.loads(subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True))
    hardware = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "nvidia_smi": run(["nvidia-smi", "-q"]),
        "gpu_processes": run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory", "--format=csv"]),
        "gpu_processes_all": run(["nvidia-smi"]),
        "wsl_version": run(["wsl", "--version"]), "wsl_distributions": run(["wsl", "--list", "--verbose"]),
        "docker_version": json.loads(subprocess.check_output(["docker", "version", "--format", "{{json .}}"], text=True)),
        "docker_engine": {key: info.get(key) for key in ("ServerVersion", "OperatingSystem", "OSType", "Architecture", "KernelVersion", "NCPU", "MemTotal", "DefaultRuntime")},
        "docker_runtimes": {key: val.get("path") for key, val in info["Runtimes"].items()},
        "windows": run(["powershell", "-NoProfile", "-Command",
                        "$c=Get-CimInstance Win32_ComputerSystem; $p=Get-CimInstance Win32_Processor | Select-Object -First 1; $o=Get-CimInstance Win32_OperatingSystem; [pscustomobject]@{OS=$o.Caption;Build=$o.BuildNumber;CPU=$p.Name;Cores=$p.NumberOfCores;LogicalProcessors=$p.NumberOfLogicalProcessors;PhysicalMemoryBytes=$c.TotalPhysicalMemory;FreePhysicalMemoryKB=$o.FreePhysicalMemory;Hypervisor=$c.HypervisorPresent} | ConvertTo-Json"]),
        "client_processes": run(["powershell", "-NoProfile", "-Command",
                                 "Get-Process | Where-Object {$_.ProcessName -match 'claude|cc-switch|ollama|lm.?studio'} | Select-Object ProcessName,Id | ConvertTo-Json"]),
        "wslconfig": (Path.home() / ".wslconfig").read_text(encoding="utf-8-sig") if (Path.home() / ".wslconfig").exists() else None,
        "initial_containers": run(["docker", "ps", "-a", "--filter", "name=llm-deployment", "--format", "{{.Names}} | {{.Status}}"]),
    }
    save("configs/host-environment.json", hardware)
    config = json.loads(subprocess.check_output(compose + ["config", "--format", "json"], text=True))
    for name, service in config["services"].items():
        for key in tuple(service.get("environment", {})):
            if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                service["environment"][key] = "<REDACTED>"
        command = service["command"]
        for index, item in enumerate(command):
            if item in ("--api-key", "--api_key"):
                command[index + 1] = "<REDACTED>"
    secrets = [value for key, value in settings.items()
               if key.upper().endswith(("_KEY", "_TOKEN", "_PASSWORD", "_SECRET"))]
    save("configs/deployment-sanitized.json", sanitize(config, secrets))
    local_files = ["compose.yaml", ".env.example", "docker/vllm/Dockerfile", "docker/sglang/Dockerfile",
                   "docker/sglang/patch_offload.py", "docker/gpu_probe.py", "docker/api_probe.py"]
    save("configs/local-image-customizations.json", [
        {"path": name, "sha256": sha(ROOT / name), "source": (ROOT / name).read_text(encoding="utf-8")}
        for name in local_files])
    for name in ("vllm", "sglang"):
        base = settings[f"{name.upper()}_BASE_IMAGE"]
        references = [base, f"llm-deployment/{name}:local"]
        images = []
        for ref in references:
            data = json.loads(subprocess.check_output(["docker", "image", "inspect", ref], text=True))[0]
            images.append({key: data.get(key) for key in ("Id", "RepoTags", "RepoDigests", "Created", "Architecture", "Os", "Size", "RootFS")})
        save(f"configs/{name}-images.json", images)
        result = subprocess.run(["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "python3",
                                 "-v", f"{model}:/models/Qwen3.5-9B-AWQ:ro", f"llm-deployment/{name}:local",
                                 "-", "--service", name, "--dataset"],
                                input=(BENCH / "scripts/container_probe.py").read_bytes(), capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        metadata = json.loads(result.stdout.decode().splitlines()[-1])
        dataset = metadata.pop("dataset")
        save(f"configs/{name}-installed.json", metadata)
        save(f"datasets/p0-{name}-tokenized.json", dataset)
        help_cmd = ["-m", "sglang.launch_server", "--help"] if name == "sglang" else ["-m", "vllm.entrypoints.openai.api_server", "--help"]
        help_result = run(["docker", "run", "--rm", "--network", "none", "--gpus", "all", "--entrypoint", "python3", f"llm-deployment/{name}:local"] + help_cmd, timeout=180)
        (BENCH / f"configs/{name}-installed-help.txt").write_text(help_result["stdout"] + "\n" + help_result["stderr"], encoding="utf-8")
        if help_result["exit_code"]:
            raise RuntimeError(f"Installed {name} CLI help failed; inspect configs/{name}-installed-help.txt")
        print(f"Frozen {name} image, packages, tokenizer, and installed CLI help", flush=True)
    a = json.loads((BENCH / "datasets/p0-vllm-tokenized.json").read_text(encoding="utf-8"))
    b = json.loads((BENCH / "datasets/p0-sglang-tokenized.json").read_text(encoding="utf-8"))
    save("datasets/p0-requests.json", a)
    comparison = {"identical_messages_and_input_ids": a == b,
                  "dataset_sha256": sha(BENCH / "datasets/p0-requests.json"),
                  "input_lengths": [x["expected_input_tokens"] for x in a["cases"]],
                  "seed": a["seed"]}
    save("configs/tokenizer-comparison.json", comparison)
    if not comparison["identical_messages_and_input_ids"]:
        raise RuntimeError("Tokenizer/template mismatch; inspect per-image dataset before proceeding")
    print(json.dumps(comparison), flush=True)


if __name__ == "__main__":
    main()
