"""Sequential, low-load P0 acceptance; uses installed images and synthetic data only.

No downloads, global settings edits, client hooks, or private conversation data.
Latency uses perf_counter_ns; UTC timestamps correlate raw samples across processes.
"""
import argparse
import csv
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"
SETTINGS = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#") and "=" in line)
KEY = SETTINGS["API_KEY"]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
COMPOSE = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(ROOT / ".env"),
           "-f", str(ROOT / "compose.yaml")]
SENSITIVE_FIELDS = {"api_key", "llm_api_key", "hf_token", "token", "authorization", "auth_token", "password", "secret"}


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def clean(value):
    if isinstance(value, dict):
        return {key: "<REDACTED>" if key.lower() in SENSITIVE_FIELDS else clean(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        result = [clean(item) for item in value]
        for index, item in enumerate(result[:-1]):
            if item in ("--api-key", "--api_key"):
                result[index + 1] = "<REDACTED>"
        return result
    if isinstance(value, str):
        return value.replace(KEY, "<REDACTED>") if KEY else value
    return value


def save(path, data):
    path.write_text(json.dumps(clean(data), ensure_ascii=False, indent=2), encoding="utf-8")


def append(path, data):
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(clean(data), ensure_ascii=False) + "\n")


def shell(command, timeout=60, stdin=None, check=True):
    result = subprocess.run(command, input=stdin, capture_output=True, timeout=timeout)
    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError(clean(f"command failed ({result.returncode}): {out}\n{err}"))
    return {"exit_code": result.returncode, "stdout": clean(out), "stderr": clean(err)}


def get(base, path, timeout=3):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + KEY})
    with OPENER.open(req, timeout=timeout) as response:
        return response.read().decode("utf-8")


def inspect(name):
    return json.loads(shell(["docker", "inspect", name])["stdout"])[0]


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
        (field, ctypes.c_ulonglong) for field in ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "avail_extended")]


def host_sample(previous):
    mem = MemoryStatus()
    mem.length = ctypes.sizeof(mem)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
        raise ctypes.WinError()
    idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    vals = tuple((v.dwHighDateTime << 32) | v.dwLowDateTime for v in (idle, kernel, user))
    cpu = None
    if previous:
        elapsed = vals[1] + vals[2] - previous[1] - previous[2]
        cpu = 100 * (1 - (vals[0] - previous[0]) / elapsed) if elapsed else None
    return {"windows_cpu_percent": cpu, "windows_ram_used_bytes": mem.total_phys - mem.avail_phys,
            "windows_ram_total_bytes": mem.total_phys, "windows_available_bytes": mem.avail_phys,
            "windows_commit_used_bytes": mem.total_page - mem.avail_page,
            "windows_commit_limit_bytes": mem.total_page}, vals


class Collector:
    def __init__(self, folder, base, container):
        self.folder, self.base, self.container = folder, base, container
        self.stop = threading.Event()
        self.phase = "pre_start"
        self.origin = time.perf_counter_ns()
        self.threads = []
        self.gpus, self.hosts, self.metrics = [], [], []
        self.errors = []

    def stamp(self):
        return {"utc": utc(), "monotonic_ns": time.perf_counter_ns(), "phase": self.phase}

    def launch(self):
        for target in (self.resources, self.container_stats, self.scrapes):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.threads.append(thread)

    def resources(self):
        previous = None
        fields = "index,uuid,memory.used,memory.free,memory.total,utilization.gpu,utilization.memory,power.draw,temperature.gpu,clocks.sm,clocks.mem"
        gpu_names = ["gpu_index", "gpu_uuid", "memory_used_mib", "memory_free_mib", "memory_total_mib", "gpu_util_percent", "memory_busy_percent", "power_w", "temperature_c", "sm_clock_mhz", "memory_clock_mhz"]
        with (self.folder / "gpu.csv").open("w", newline="", encoding="utf-8") as gf, (self.folder / "host.csv").open("w", newline="", encoding="utf-8") as hf:
            gw = csv.DictWriter(gf, fieldnames=["utc", "monotonic_ns", "phase", "query_elapsed_ms"] + gpu_names)
            hw = csv.DictWriter(hf, fieldnames=["utc", "monotonic_ns", "phase", "windows_cpu_percent", "windows_ram_used_bytes", "windows_ram_total_bytes", "windows_available_bytes", "windows_commit_used_bytes", "windows_commit_limit_bytes"])
            gw.writeheader(); hw.writeheader()
            while not self.stop.is_set():
                begin = time.perf_counter()
                stamp = self.stamp()
                try:
                    host, previous = host_sample(previous)
                    row = stamp | host
                    hw.writerow(row); hf.flush(); self.hosts.append(row)
                    out = shell(["nvidia-smi", "--query-gpu=" + fields, "--format=csv,noheader,nounits"], timeout=8)["stdout"]
                    for values in csv.reader(out.strip().splitlines()):
                        row = stamp | {"query_elapsed_ms": (time.perf_counter() - begin) * 1000} | dict(zip(gpu_names, (v.strip() for v in values)))
                        gw.writerow(row); gf.flush(); self.gpus.append(row)
                except Exception as exc:
                    self.errors.append({"sampler": "host_gpu", "utc": utc(), "error": clean(str(exc))})
                self.stop.wait(max(0, 1 - (time.perf_counter() - begin)))

    def container_stats(self):
        code = """import json, pathlib, time
p=pathlib.Path('/sys/fs/cgroup')
names=('memory.current','memory.max','memory.peak','memory.swap.current','memory.swap.max','memory.stat','memory.events','cpu.stat','cpu.max','pids.current')
print(json.dumps({'utc_epoch_ns':time.time_ns(),'proc_meminfo':pathlib.Path('/proc/meminfo').read_text(),'proc_stat':pathlib.Path('/proc/stat').read_text().splitlines()[0],'proc_vmstat':pathlib.Path('/proc/vmstat').read_text(),'cgroup':{n:(p/n).read_text() if (p/n).exists() else None for n in names}}))
"""
        while not self.stop.is_set():
            begin = time.perf_counter()
            stamp = self.stamp()
            try:
                result = shell(["docker", "exec", self.container, "python3", "-c", code], timeout=8, check=False)
                if not result["exit_code"]:
                    append(self.folder / "wsl-container.jsonl", stamp | json.loads(result["stdout"]))
                result = shell(["docker", "stats", "--no-stream", "--format", "{{json .}}", self.container], timeout=8, check=False)
                if not result["exit_code"] and result["stdout"].strip():
                    append(self.folder / "docker-stats.jsonl", stamp | {"stats": json.loads(result["stdout"])})
            except Exception as exc:
                self.errors.append({"sampler": "container", "utc": utc(), "error": clean(str(exc))})
            self.stop.wait(max(0, 2 - (time.perf_counter() - begin)))

    def scrapes(self):
        with (self.folder / "metrics.prom").open("w", encoding="utf-8") as file:
            while not self.stop.is_set():
                begin = time.perf_counter()
                stamp = self.stamp()
                try:
                    body = get(self.base, "/metrics", timeout=1)
                    file.write("# P0_SCRAPE " + json.dumps(stamp) + "\n" + clean(body) + "\n")
                    file.flush()
                    self.metrics.append(stamp | {"body": body})
                except (OSError, urllib.error.URLError):
                    pass  # Unavailable before readiness; final metrics gate is explicit.
                self.stop.wait(max(0, 1 - (time.perf_counter() - begin)))

    def finish(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=12)
        save(self.folder / "collector-errors.json", self.errors)


def metric_schema(body):
    entries = {}
    for line in body.splitlines():
        match = re.match(r"# (HELP|TYPE|UNIT) (\S+) (.*)", line)
        if match:
            field, name, value = match.groups()
            entries.setdefault(name, {"name": name})[field.lower()] = value
    for name, entry in entries.items():
        if "unit" not in entry:
            if name.endswith("_seconds"):
                entry["unit"] = "seconds (name suffix)"
            elif name.endswith("_bytes"):
                entry["unit"] = "bytes (name suffix)"
            elif "percent" in name or "usage" in name or "utilization" in name:
                entry["unit"] = "see installed HELP/source; no cross-engine unit assumption"
            else:
                entry["unit"] = "see HELP; counts retain named token/request/block denominator"
    return list(entries.values())


def stream_request(base, case, run_id, folder, warmup=False):
    record = {"run_id": run_id, "request_id": f"{run_id}/{case['case_id']}", "case_id": case["case_id"],
              "warmup": warmup, "expected_input_tokens": case.get("expected_input_tokens"),
              "max_output_tokens": case["max_output_tokens"], "status": "started", "send_utc": utc()}
    payload = {"model": SETTINGS.get("SERVED_MODEL_NAME", "Qwen3.5-9B-AWQ"), "messages": case["messages"],
               "temperature": 0, "top_p": 1, "seed": 20261030,
               "max_tokens": case["max_output_tokens"], "stream": True,
               "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    start = time.perf_counter_ns()
    record["send_monotonic_ns"] = start
    first_content, last_content = None, None
    chunks, output, usage, finish = [], [], None, None
    done = False
    try:
        with OPENER.open(req, timeout=180) as response:
            record["http_headers_ms"] = (time.perf_counter_ns() - start) / 1e6
            first_byte = response.read(1)
            record["ttfb_body_ms"] = (time.perf_counter_ns() - start) / 1e6
            first_line = first_byte + response.readline() if first_byte else b""
            while True:
                raw = first_line if first_line is not None else response.readline()
                first_line = None
                arrived = time.perf_counter_ns()
                if not raw:
                    break
                line = raw.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                event = json.loads(data)
                if event.get("error"):
                    raise RuntimeError(json.dumps(event["error"]))
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices", []):
                    delta = choice.get("delta", {})
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        if first_content is None:
                            first_content = arrived
                            record["first_content_utc"] = utc()
                        last_content = arrived
                        chunks.append({"monotonic_ns": arrived, "text": text})
                        output.append(text)
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        record["status"] = "success" if output and usage and done else "invalid_stream"
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            record["error"] = f"HTTP {exc.code}: " + exc.read().decode(errors="replace")
        else:
            record["error"] = str(exc)
        record["status"] = "error"
    end = time.perf_counter_ns()
    record.update({"completed_utc": utc(), "completed_monotonic_ns": end, "e2e_ms": (end - start) / 1e6,
                   "first_content_monotonic_ns": first_content, "last_content_monotonic_ns": last_content,
                   "ttft_ms": (first_content - start) / 1e6 if first_content else None,
                   "content_chunks": len(chunks), "output_text": "".join(output), "usage": usage,
                   "finish_reason": finish, "received_done": done,
                   "output_count_source": "server_final_usage" if usage else "unavailable"})
    if usage and first_content and last_content:
        tokens = usage.get("completion_tokens", 0)
        record["tpot_chunk_observed_ms"] = (last_content - first_content) / 1e6 / (tokens - 1) if tokens > 1 and last_content > first_content else None
        record["decode_chunk_observed_tokens_per_s"] = (tokens - 1) * 1e9 / (last_content - first_content) if tokens > 1 and last_content > first_content else None
        record["e2e_output_tokens_per_s"] = tokens * 1e9 / (end - start)
        record["input_token_count_matches"] = usage.get("prompt_tokens") == case.get("expected_input_tokens") if not warmup else None
    append(folder / "requests.jsonl", record)
    append(folder / "stream-chunks.jsonl", {"request_id": record["request_id"], "chunks": chunks})
    print(json.dumps(clean({key: record.get(key) for key in ("case_id", "status", "usage", "ttft_ms", "e2e_ms", "content_chunks", "input_token_count_matches", "error")})), flush=True)
    return record


def command_profile(service, profile):
    config = json.loads(subprocess.check_output(COMPOSE + ["config", "--format", "json"], text=True))
    command = config["services"][service]["command"][:]
    command[command.index("--api-key") + 1] = "${API_KEY}"

    def replace(flag, value):
        if flag in command:
            command[command.index(flag) + 1] = str(value)
        else:
            command.extend([flag, str(value)])

    if service == "sglang":
        command.append("--enable-metrics")
    if profile == "control":
        replace("--dtype", "bfloat16")
        replace("--gpu-memory-utilization" if service == "vllm" else "--mem-fraction-static", "0.65")
        replace("--max-model-len" if service == "vllm" else "--context-length", 16384)
        if service == "sglang":
            replace("--max-total-tokens", 16384)
    # Preserve existing caching settings and measure cached-token increments explicitly.
    return command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=("vllm", "sglang"), required=True)
    parser.add_argument("--profile", choices=("existing", "control"), required=True)
    parser.add_argument("--ready-timeout", type=int, default=1200)
    args = parser.parse_args()
    service, profile = args.service, args.profile
    if not json.loads((BENCH / "configs/model-assets.json").read_text(encoding="utf-8"))["all_files_match"]:
        raise RuntimeError("Model hashes failed")
    if not json.loads((BENCH / "configs/tokenizer-comparison.json").read_text(encoding="utf-8"))["identical_messages_and_input_ids"]:
        raise RuntimeError("Tokenizers are not comparable")
    command = command_profile(service, profile)
    config_hash = hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()[:8]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{service}-p0-{profile}-{config_hash}-r01"
    folder = BENCH / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "frozen-configs").mkdir()
    for path in (BENCH / "configs").iterdir():
        if path.is_file():
            shutil.copy2(path, folder / "frozen-configs" / path.name)
    shutil.copy2(BENCH / "datasets/p0-requests.json", folder / "dataset.json")
    override = {"services": {service: {"command": command}}}
    override_file = folder / "compose.override.json"
    # Only an environment-variable placeholder is persisted, never the actual API key.
    override_file.write_text(json.dumps(override, ensure_ascii=False, indent=2), encoding="utf-8")
    compose = COMPOSE + ["-f", str(override_file)]
    container = f"llm-deployment-{service}-1"
    port = SETTINGS.get("VLLM_PORT" if service == "vllm" else "SGLANG_PORT", "8000" if service == "vllm" else "30000")
    base = "http://127.0.0.1:" + port
    dataset = json.loads((BENCH / "datasets/p0-requests.json").read_text(encoding="utf-8"))
    manifest = {"run_id": run_id, "stage": "P0", "service": service, "profile": profile,
                "created_at_utc": utc(), "status": "running", "command": command,
                "dataset_sha256": hashlib.sha256((BENCH / "datasets/p0-requests.json").read_bytes()).hexdigest(),
                "production_env_sha256": hashlib.sha256((ROOT / ".env").read_bytes()).hexdigest(),
                "collection_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "sampling": {"temperature": 0, "top_p": 1, "seed": 20261030, "thinking": False, "stream": True, "retry": False},
                "scope": "low-load instrumentation acceptance; not P1/P2 performance or stability evidence",
                "cache_policy": "existing cache settings; distinct synthetic prefixes; record hit counters and usage",
                "startup_condition": "installed images, warm OS file cache possible; no filesystem cache eviction",
                "ttft_definition": "send to first non-empty content delta; role/empty/reasoning deltas excluded",
                "tpot_limit": "last/first content-chunk timing divided by server completion_tokens-1; approximate when chunks batch tokens; no token ITL claim",
                "resource_limit": "whole GPU includes desktop; per-process GPU memory may be unavailable under WSL/WDDM"}
    save(folder / "manifest.json", manifest)
    print(f"RUN {run_id}", flush=True)

    def event(kind, **extra):
        append(folder / "events.jsonl", {"utc": utc(), "monotonic_ns": time.perf_counter_ns(), "event": kind} | extra)

    collector = Collector(folder, base, container)
    log_since = datetime.now(timezone.utc).isoformat()
    records = []
    try:
        shell(COMPOSE + ["stop", "vllm", "sglang"], timeout=90)
        save(folder / "gpu-processes-before.json", shell(["nvidia-smi", "--query-compute-apps=pid,process_name,used_gpu_memory", "--format=csv"]))
        collector.launch()
        event("no_model_baseline_begin", seconds=5)
        time.sleep(5)
        collector.phase = "startup"
        event("start_command")
        startup = time.perf_counter()
        result = shell(compose + ["up", "-d", "--no-build", "--pull", "never", "--force-recreate", service], timeout=120)
        save(folder / "compose-start.json", result)
        deadline = time.perf_counter() + args.ready_timeout
        while True:
            try:
                get(base, "/health", timeout=2)
                break
            except (OSError, urllib.error.URLError):
                state = inspect(container)["State"]
                if not state["Running"]:
                    raise RuntimeError(f"Container exited before ready: exit={state['ExitCode']}, OOMKilled={state.get('OOMKilled')}")
                if time.perf_counter() > deadline:
                    raise TimeoutError("P0 readiness timeout")
                time.sleep(2)
        manifest["ready_observed_seconds"] = time.perf_counter() - startup
        event("ready", ready_observed_seconds=manifest["ready_observed_seconds"])
        print(f"READY {service}/{profile} {manifest['ready_observed_seconds']:.1f}s", flush=True)
        collector.phase = "ready_metadata"
        state = inspect(container)
        save(folder / "container.json", {"Id": state["Id"], "Image": state["Image"], "State": state["State"],
                                          "Path": state["Path"], "Args": state["Args"], "Mounts": state["Mounts"],
                                          "HostConfig": {key: state["HostConfig"].get(key) for key in ("DeviceRequests", "Memory", "MemorySwap", "NanoCpus", "CpuQuota", "CpuPeriod", "ShmSize", "Runtime")}})
        probe = shell(["docker", "exec", "-i", container, "python3", "-", "--service", service],
                      stdin=(BENCH / "scripts/container_probe.py").read_bytes(), timeout=120)
        save(folder / "runtime.json", json.loads(probe["stdout"].splitlines()[-1]))
        module = "sglang.launch_server" if service == "sglang" else "vllm.entrypoints.openai.api_server"
        installed_help = shell(["docker", "exec", container, "python3", "-m", module, "--help"], timeout=120)
        (folder / "installed-help.txt").write_text(installed_help["stdout"] + installed_help["stderr"], encoding="utf-8")
        source_code = """import hashlib,importlib.util,json,pathlib
service=SERVICE
root=pathlib.Path(importlib.util.find_spec(service).origin).parent
paths=([root/'v1/metrics/loggers.py',root/'model_executor/layers/quantization/auto_awq.py',root/'model_executor/layers/quantization/awq_marlin.py',root/'model_executor/offloader/uva.py'] if service=='vllm' else [root/'srt/observability/metrics_collector.py',root/'srt/managers/scheduler_components/metrics_reporter.py',root/'srt/utils/offloader.py',root/'srt/layers/quantization/awq/awq.py',root/'srt/layers/quantization/awq/schemes/awq_marlin.py'])
print(json.dumps([{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source':p.read_text()} for p in paths if p.exists()]))
""".replace("SERVICE", repr(service))
        sources = shell(["docker", "exec", container, "python3", "-c", source_code], timeout=30)
        save(folder / "installed-source.json", json.loads(sources["stdout"]))
        for route, filename in (("/v1/models", "models.json"), ("/get_model_info", "model-info.json"), ("/get_server_info", "server-info.json")):
            try:
                save(folder / filename, json.loads(get(base, route, timeout=10)))
            except (OSError, ValueError):
                pass
        before = get(base, "/metrics", timeout=5)
        if "# TYPE" not in before:
            raise RuntimeError("No valid Prometheus metrics at /metrics")
        (folder / "metrics-before.prom").write_text(clean(before), encoding="utf-8")
        save(folder / "metrics-schema.json", metric_schema(before))
        manifest["metrics_gate"] = "passed"
        manifest["metrics_auth_test"] = {"authenticated_get": 200}
        try:
            with OPENER.open(base + "/metrics", timeout=3) as response:
                manifest["metrics_auth_test"]["unauthenticated_get"] = response.status
        except urllib.error.HTTPError as exc:
            manifest["metrics_auth_test"]["unauthenticated_get"] = exc.code
        time_a = time.time_ns()
        clock = json.loads(shell(["docker", "exec", container, "python3", "-c", "import time,json; print(json.dumps({'utc_epoch_ns':time.time_ns()}))"])["stdout"])
        time_b = time.time_ns()
        save(folder / "clock-alignment.json", {"host_before_ns": time_a, "container_ns": clock["utc_epoch_ns"], "host_after_ns": time_b,
                                               "container_minus_host_midpoint_ms": (clock["utc_epoch_ns"] - (time_a + time_b) / 2) / 1e6,
                                               "uncertainty_half_rtt_ms": (time_b - time_a) / 2e6,
                                               "latency_clock": "host perf_counter_ns only"})
        collector.phase = "warmup"
        warmup = {"case_id": "warmup", "messages": [{"role": "user", "content": "P0 warmup f6d71a2. Reply with just hello."}], "max_output_tokens": 8}
        event("warmup_begin")
        records.append(stream_request(base, warmup, run_id, folder, warmup=True))
        if records[-1]["status"] != "success":
            raise RuntimeError("Warmup request failed")
        collector.phase = "warm_idle"
        event("warm_idle_begin", seconds=5)
        time.sleep(5)
        (folder / "metrics-after-warmup.prom").write_text(clean(get(base, "/metrics")), encoding="utf-8")
        for case in dataset["cases"]:
            collector.phase = case["case_id"]
            event("request_begin", case_id=case["case_id"])
            records.append(stream_request(base, case, run_id, folder))
            event("request_end", case_id=case["case_id"], status=records[-1]["status"])
            if records[-1]["status"] != "success":
                raise RuntimeError(f"Smoke request failed: {case['case_id']}")
        collector.phase = "post_request"
        time.sleep(3)
        after = get(base, "/metrics")
        (folder / "metrics-after.prom").write_text(clean(after), encoding="utf-8")
        # Recount generated text with the exact tokenizer; no substitution for authoritative usage.
        code = """import json,sys
from transformers import AutoTokenizer
t=AutoTokenizer.from_pretrained('/models/Qwen3.5-9B-AWQ',local_files_only=True)
rows=json.load(sys.stdin)
print(json.dumps([{'request_id':r['request_id'],'decoded_text_retokenized_tokens':len(t.encode(r['output_text'],add_special_tokens=False))} for r in rows]))
"""
        counts = shell(["docker", "exec", "-i", container, "python3", "-c", code], stdin=json.dumps(records).encode(), timeout=60)
        save(folder / "output-token-recount.json", json.loads(counts["stdout"].splitlines()[-1]))
        bad = [r["case_id"] for r in records if not r["warmup"] and not r.get("input_token_count_matches")]
        manifest["input_token_gate"] = "passed" if not bad else "failed: " + ", ".join(bad)
        manifest["stream_gate"] = "passed"
        manifest["status"] = "passed" if not bad else "failed_token_mismatch"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = clean(str(exc))
        save(folder / "failure.json", {"error": str(exc), "traceback": traceback.format_exc()})
        event("failure", error=str(exc))
        print(f"FAILED {run_id}: {clean(str(exc))}", flush=True)
    finally:
        collector.phase = "stopping"
        try:
            logs = shell(["docker", "logs", "--timestamps", "--since", log_since, container], timeout=30, check=False)
            (folder / "server.log").write_text(clean(logs["stdout"] + logs["stderr"]), encoding="utf-8")
            manifest["final_container_state"] = inspect(container)["State"]
            shell(COMPOSE + ["stop", service], timeout=90)
            event("service_stopped")
        except Exception as exc:
            manifest["cleanup_error"] = clean(str(exc))
        collector.finish()
        manifest["completed_at_utc"] = utc()
        manifest["production_env_unchanged"] = manifest["production_env_sha256"] == hashlib.sha256((ROOT / ".env").read_bytes()).hexdigest()
        manifest["resource_samples"] = {"gpu": len(collector.gpus), "host": len(collector.hosts), "metrics": len(collector.metrics)}
        log = (folder / "server.log").read_text(encoding="utf-8") if (folder / "server.log").exists() else ""
        interesting = [line for line in log.splitlines() if re.search(r"marlin|quantiz|mamba|ssm|cache size|KV cache|KV.*token|offload|weights|model loading took|memory pool|dtype", line, re.I)]
        (folder / "runtime-evidence.txt").write_text("\n".join(interesting), encoding="utf-8")
        save(folder / "manifest.json", manifest)
        valid = [r for r in records if not r["warmup"] and r["status"] == "success"]
        def gpu_phase(phase):
            rows = [float(r["memory_used_mib"]) for r in collector.gpus if r["phase"] == phase and re.match(r"^\d+(\.\d+)?$", r["memory_used_mib"])]
            return {"n": len(rows), "median_mib": statistics.median(rows) if rows else None, "peak_mib": max(rows) if rows else None}
        summary = {"run_id": run_id, "service": service, "profile": profile, "status": manifest["status"],
                   "ready_observed_seconds": manifest.get("ready_observed_seconds"),
                   "requests_planned": 3, "requests_successful": len(valid), "warmups": sum(r["warmup"] for r in records),
                   "baseline_gpu": gpu_phase("pre_start"), "warm_idle_gpu": gpu_phase("warm_idle"),
                   "sampled_gpu_peak_mib": max((float(r["memory_used_mib"]) for r in collector.gpus if re.match(r"^\d+(\.\d+)?$", r["memory_used_mib"])), default=None),
                   "requests": [{key: r.get(key) for key in ("case_id", "usage", "ttft_ms", "e2e_ms", "tpot_chunk_observed_ms", "decode_chunk_observed_tokens_per_s", "content_chunks", "finish_reason", "input_token_count_matches")} for r in valid],
                   "metrics_gate": manifest.get("metrics_gate"), "input_token_gate": manifest.get("input_token_gate"),
                   "error": manifest.get("error"), "collector_errors": len(collector.errors),
                   "measurement_class": "P0 smoke; one request per length, no performance ranking or tails"}
        save(folder / "summary.json", summary)
        print(f"COMPLETE {run_id}: {manifest['status']}", flush=True)
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
