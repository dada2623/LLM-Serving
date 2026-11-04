"""Regenerate P0 CSV/JSON, plots, and Chinese report from immutable run files."""
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def jsonlines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def samples(text):
    result = []
    for line in text.splitlines():
        match = re.match(r'^([^#\s{]+)(?:\{(.*)\})?\s+([^\s]+)', line)
        if not match:
            continue
        name, label_text, val = match.groups()
        labels = {key: json.loads('"' + value + '"') for key, value in re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', label_text or "")}
        try:
            result.append({"name": name, "labels": labels, "value": float(val)})
        except ValueError:
            pass
    return result


def totals(rows):
    result = {}
    for row in rows:
        result[row["name"]] = result.get(row["name"], 0) + row["value"]
    return result


def csvwrite(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def numeric(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def describe(values):
    values = [v for v in values if v is not None]
    return {"n": len(values), "min": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None}


def extract_runtime(folder, service):
    log = (folder / "server.log").read_text(encoding="utf-8") if (folder / "server.log").exists() else ""
    result = {}
    if service == "vllm":
        result["quantization_kernel"] = "AutoAWQMarlinLinearMethod / MarlinLinearKernel" if "Using MarlinLinearKernel" in log else "unavailable"
        match = re.search(r"Model loading took ([\d.]+) GiB", log)
        result["model_loading_gpu_gib"] = float(match[1]) if match else None
        match = re.search(r"GPU KV cache size: ([\d,]+) tokens", log)
        result["kv_cache_tokens"] = int(match[1].replace(",", "")) if match else None
        match = re.search(r"Total CPU offloaded parameters: ([\d.]+)", log)
        result["cpu_offloaded_log_value"] = float(match[1]) if match else None
        # The unit comes from installed source; retain the original line if not explicit.
        result["cpu_offload_evidence"] = next((line for line in log.splitlines() if "Total CPU offloaded parameters:" in line), None)
        metrics = samples((folder / "metrics-after.prom").read_text(encoding="utf-8")) if (folder / "metrics-after.prom").exists() else []
        cache = next((row["labels"] for row in metrics if row["name"] == "vllm:cache_config_info"), {})
        result["hybrid_cache_config"] = cache
        result["independent_mamba_slots"] = "unavailable; hybrid block cache, see num_gpu_blocks/mamba_block_size/mode"
    else:
        result["quantization_kernel"] = "awq_marlin" if "Using awq_marlin kernel" in log else "unavailable"
        match = re.search(r"MLP-only CPU offload: ([\d.]+) GiB", log)
        result["cpu_offloaded_gib"] = float(match[1]) if match else None
        if (folder / "server-info.json").exists():
            info = load(folder / "server-info.json")
            result["server_info"] = info
        for label, pattern in (
            ("kv_cache_tokens", r"max_total_num_tokens[=:]\s*([\d,]+)"),
            ("model_loading_gpu_gib", r"Load weight end[^\n]*mem usage[=:]\s*([\d.]+) GB"),
            ("mamba_slots", r"Mamba Cache is allocated[^\n]*max_mamba_cache_size:\s*(\d+)"),
            ("mamba_ssm_pool_gib", r"Mamba Cache is allocated[^\n]*ssm_state size:\s*([\d.]+)GB"),
            ("mamba_conv_pool_gib", r"Mamba Cache is allocated[^\n]*conv_state size:\s*([\d.]+)GB"),
        ):
            match = re.search(pattern, log, re.I)
            if match:
                result[label] = float(match[1].replace(",", "")) if "gib" in label else int(match[1].replace(",", ""))
    return result


def metric_audit(folder, service, requests):
    if not (folder / "metrics-after.prom").exists():
        return {"status": "unavailable"}
    before_text = (folder / "metrics-before.prom").read_text(encoding="utf-8")
    after_text = (folder / "metrics-after.prom").read_text(encoding="utf-8")
    before, after = totals(samples(before_text)), totals(samples(after_text))
    delta = {name: value - before.get(name, 0) for name, value in after.items()
             if name.endswith("_total") or name.endswith("_count")}
    selected = {key: val for key, val in delta.items() if any(word in key for word in ("request", "token", "cache_hit", "cache_quer", "preempt")) and "bucket" not in key}
    good = [r for r in requests if r["status"] == "success"]
    expected_input = sum(r.get("usage", {}).get("prompt_tokens", 0) for r in good)
    expected_output = sum(r.get("usage", {}).get("completion_tokens", 0) for r in good)
    request_name = "vllm:request_success_total" if service == "vllm" else "sglang:num_requests_total"
    prompt_name = "vllm:prompt_tokens_total" if service == "vllm" else "sglang:prompt_tokens_total"
    output_name = "vllm:generation_tokens_total" if service == "vllm" else "sglang:generation_tokens_total"
    result = {"status": "collected", "deltas": selected, "expected_success_requests_including_warmup": len(good),
              "expected_prompt_tokens_including_warmup": expected_input,
              "expected_completion_tokens_including_warmup": expected_output,
              "request_counter_name": request_name, "observed_request_delta": delta.get(request_name),
              "request_counter_matches": delta.get(request_name) == len(good) if request_name in delta else None,
              "prompt_counter_name": prompt_name, "prompt_counter_delta": delta.get(prompt_name),
              "output_counter_name": output_name, "output_counter_delta": delta.get(output_name),
              "note": "missing native metric is unavailable, never treated as zero; consult installed-source.json/metrics-schema.json"}
    return result


def make_figures(runs, stem="p0-resources"):
    sys.path.insert(0, str(ROOT / ".tools/benchmark-plot"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    n = len(runs)
    if not n:
        return
    fig, axes = plt.subplots(n, 2, figsize=(13, max(3.2 * n, 4)), squeeze=False, constrained_layout=True)
    colors = {"startup": "#0f766e", "warmup": "#ea580c", "p0-input-128": "#2563eb", "p0-input-512": "#7c3aed", "p0-input-2048": "#be123c"}
    for row_index, run in enumerate(runs):
        folder = BENCH / "runs" / run["run_id"]
        with (folder / "gpu.csv").open(encoding="utf-8") as file:
            rows = list(csv.DictReader(file))
        if not rows:
            continue
        origin = int(rows[0]["monotonic_ns"])
        seconds = [(int(row["monotonic_ns"]) - origin) / 1e9 for row in rows]
        ax = axes[row_index, 0]
        ax.plot(seconds, [float(row["memory_used_mib"]) / 1024 for row in rows], color="#0f172a", linewidth=1.4)
        ax.axhline(float(rows[0]["memory_total_mib"]) / 1024, color="#dc2626", linestyle="--", linewidth=.8, label="GPU capacity")
        previous, begin = rows[0]["phase"], seconds[0]
        for second, item in list(zip(seconds[1:], rows[1:])) + [(seconds[-1], {"phase": None})]:
            if item["phase"] != previous:
                if previous in colors:
                    ax.axvspan(begin, second, color=colors[previous], alpha=.13, label=previous)
                previous, begin = item["phase"], second
        ax.set_title(f"{run['service']} / {run['profile']} / {run['status']}", loc="left", fontweight="bold")
        ax.set(xlabel="Seconds since first host sample", ylabel="Whole-GPU memory (GiB)")
        ax.grid(alpha=.2)
        if row_index == 0:
            ax.legend(fontsize=7, ncol=3, loc="lower right")
        ax2 = axes[row_index, 1]
        with (folder / "host.csv").open(encoding="utf-8") as file:
            host = list(csv.DictReader(file))
        hs = [(int(item["monotonic_ns"]) - origin) / 1e9 for item in host]
        ax2.plot(hs, [float(item["windows_ram_used_bytes"]) / 1024**3 for item in host], color="#2563eb", label="Windows used RAM")
        ax2.axhline(float(host[0]["windows_ram_total_bytes"]) / 1024**3, color="#dc2626", linestyle="--", linewidth=.8)
        ax2.set(xlabel="Seconds since first host sample", ylabel="Host RAM (GiB)")
        right = ax2.twinx()
        right.plot(seconds, [numeric(item["gpu_util_percent"]) for item in rows], color="#0f766e", alpha=.55, label="GPU utilization")
        right.set(ylabel="GPU utilization (%)", ylim=(0, 105))
        ax2.grid(alpha=.2)
    fig.suptitle("P0 instrumentation smoke runs — sequential, small samples; not a performance ranking", fontsize=13, fontweight="bold")
    fig.savefig(BENCH / f"figures/{stem}.png", dpi=160)
    fig.savefig(BENCH / f"figures/{stem}.svg", metadata={"Date": None})
    plt.close(fig)
    save(BENCH / f"figures/{stem}.sources.json", {"run_ids": [r["run_id"] for r in runs], "script": "benchmark/scripts/summarize_p0.py", "inputs": ["gpu.csv", "host.csv"],
                                                    "plotting_packages": {p: md.version(p) for p in ("matplotlib", "numpy")}})


def main():
    summaries, request_rows, resource_rows, runs, metric_catalog = [], [], [], [], []
    for folder in sorted((BENCH / "runs").iterdir()):
        if not (folder / "summary.json").exists():
            continue
        summary, manifest = load(folder / "summary.json"), load(folder / "manifest.json")
        if manifest.get("stage") != "P0":
            continue
        requests = jsonlines(folder / "requests.jsonl")
        runtime = extract_runtime(folder, summary["service"])
        audit = metric_audit(folder, summary["service"], requests)
        if (folder / "metrics-schema.json").exists():
            for metric in load(folder / "metrics-schema.json"):
                name = metric["name"]
                if name.endswith("_created"):
                    continue
                unit = "unavailable; read installed HELP/source before use"
                if name.endswith("_seconds"):
                    unit = "seconds"
                elif name in ("vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:request_success_total", "sglang:num_running_reqs", "sglang:num_queue_reqs", "sglang:num_requests_total"):
                    unit = "requests"
                elif name in ("vllm:kv_cache_usage_perc", "sglang:token_usage", "sglang:full_token_usage", "sglang:mamba_usage", "sglang:cache_hit_rate"):
                    unit = "ratio (0..1); denominators differ by pool/counter"
                elif name in ("sglang:mamba_available_tokens", "sglang:mamba_evictable_tokens", "sglang:mamba_used_tokens"):
                    unit = "Mamba state slots, despite metric name containing tokens; see pool_stats_observer.py"
                elif name.endswith("_tokens_total") or name in ("vllm:prefix_cache_queries_total", "vllm:prefix_cache_hits_total", "sglang:max_total_num_tokens", "sglang:page_size"):
                    unit = "tokens; preserve labels/HELP denominator"
                elif name.endswith("_memory_usage_gb"):
                    unit = "GB as named by installed metric; do not silently convert to bytes"
                metric_catalog.append({"run_id": summary["run_id"], "service": summary["service"], "name": name,
                                       "type": metric.get("type"), "unit": unit, "help": metric.get("help")})
        save(folder / "metrics-audit.json", audit)
        save(folder / "runtime-extracted.json", runtime)
        result = {"run_id": summary["run_id"], "service": summary["service"], "profile": summary["profile"],
                  "status": summary["status"], "ready_observed_s": summary.get("ready_observed_seconds"),
                  "smoke_success": summary["requests_successful"], "smoke_planned": summary["requests_planned"],
                  "baseline_gpu_median_mib": summary["baseline_gpu"]["median_mib"],
                  "warm_idle_gpu_median_mib": summary["warm_idle_gpu"]["median_mib"],
                  "sampled_gpu_peak_mib": summary["sampled_gpu_peak_mib"],
                  "kv_cache_tokens": runtime.get("kv_cache_tokens"), "quantization_kernel": runtime.get("quantization_kernel"),
                  "metrics_gate": summary.get("metrics_gate"), "input_token_gate": summary.get("input_token_gate"),
                  "native_request_counter_matches": audit.get("request_counter_matches"),
                  "production_env_unchanged": manifest.get("production_env_unchanged"), "error": summary.get("error")}
        summaries.append(result)
        counts = {r["request_id"]: r for r in load(folder / "output-token-recount.json")} if (folder / "output-token-recount.json").exists() else {}
        for request in requests:
            usage = request.get("usage") or {}
            request_rows.append({"run_id": summary["run_id"], "service": summary["service"], "profile": summary["profile"],
                                 "case_id": request["case_id"], "warmup": request["warmup"], "status": request["status"],
                                 "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"),
                                 "input_count_matches": request.get("input_token_count_matches"),
                                 "retokenized_output_tokens": counts.get(request["request_id"], {}).get("decoded_text_retokenized_tokens"),
                                 "ttft_ms": request.get("ttft_ms"), "ttfb_body_ms": request.get("ttfb_body_ms"), "e2e_ms": request.get("e2e_ms"),
                                 "tpot_chunk_observed_ms": request.get("tpot_chunk_observed_ms") if request.get("content_chunks", 0) > 1 else None,
                                 "decode_chunk_observed_tokens_s": request.get("decode_chunk_observed_tokens_per_s"),
                                 "content_chunks": request.get("content_chunks"), "finish_reason": request.get("finish_reason")})
        with (folder / "gpu.csv").open(encoding="utf-8") as file:
            gpu = list(csv.DictReader(file))
        with (folder / "host.csv").open(encoding="utf-8") as file:
            host = list(csv.DictReader(file))
        timestamps = [int(row["monotonic_ns"]) for row in gpu]
        intervals = [(b - a) / 1e9 for a, b in zip(timestamps, timestamps[1:])]
        for phase in dict.fromkeys(row["phase"] for row in gpu):
            gr = [row for row in gpu if row["phase"] == phase]
            hr = [row for row in host if row["phase"] == phase]
            item = {"run_id": summary["run_id"], "phase": phase, "samples": len(gr)}
            for field in ("memory_used_mib", "gpu_util_percent", "power_w", "temperature_c"):
                for stat, val in describe([numeric(row[field]) for row in gr]).items():
                    item[field + "_" + stat] = val
            item["windows_ram_used_bytes_peak"] = max((int(row["windows_ram_used_bytes"]) for row in hr), default=None)
            for stat, val in describe([numeric(row["windows_cpu_percent"]) for row in hr]).items():
                item["windows_cpu_percent_" + stat] = val
            resource_rows.append(item)
        wsl_rows = jsonlines(folder / "wsl-container.jsonl")
        linux_ram, linux_swap, major_faults, container_ram, container_swap = [], [], [], [], []
        for row in wsl_rows:
            info = {name: int(value.strip().split()[0]) * 1024 for name, value in
                    (line.split(":", 1) for line in row["proc_meminfo"].splitlines()) if value.strip().split()[0].isdigit()}
            linux_ram.append(info["MemTotal"] - info["MemAvailable"])
            linux_swap.append(info["SwapTotal"] - info["SwapFree"])
            vmstat = dict(line.split() for line in row["proc_vmstat"].splitlines())
            major_faults.append(int(vmstat.get("pgmajfault", 0)))
            container_ram.append(numeric(row["cgroup"].get("memory.current")))
            container_swap.append(numeric(row["cgroup"].get("memory.swap.current")))
        wsl_summary = {"wsl_used_ram_bytes": describe(linux_ram), "wsl_swap_used_bytes": describe(linux_swap),
                       "container_cgroup_memory_bytes": describe(container_ram), "container_cgroup_swap_bytes": describe(container_swap),
                       "wsl_pgmajfault_delta": major_faults[-1] - major_faults[0] if major_faults else None,
                       "note": "WSL counters cover the guest; cgroup memory includes charged file cache and is not pure model weight residency"}
        save(folder / "wsl-resource-summary.json", wsl_summary)
        runtime_report = {"summary": result, "runtime": runtime, "metric_audit": audit,
                          "sampling_interval_seconds": describe(intervals),
                          "wsl_resources": wsl_summary,
                          "clock": load(folder / "clock-alignment.json") if (folder / "clock-alignment.json").exists() else None,
                          "manifest": manifest}
        runs.append(summary | {"details": runtime_report})
    csvwrite(BENCH / "processed/p0-runs.csv", summaries)
    csvwrite(BENCH / "processed/p0-requests.csv", request_rows)
    csvwrite(BENCH / "processed/p0-resources.csv", resource_rows)
    csvwrite(BENCH / "processed/p0-metrics-catalog.csv", metric_catalog)
    csvwrite(BENCH / "processed/p0-metrics-deltas.csv", [
        {"run_id": run["run_id"], "metric": key, "delta": val}
        for run in runs for key, val in run["details"]["metric_audit"].get("deltas", {}).items()])
    final = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "stage": "P0", "runs": runs,
             "model_asset_check": load(BENCH / "configs/model-assets.json")["all_files_match"],
             "tokenizer_comparison": load(BENCH / "configs/tokenizer-comparison.json"),
             "planned_profiles": ["vllm/existing", "sglang/existing", "vllm/control", "sglang/control"],
             "scope": "Instrumentation acceptance only; no P1–P8 capacity/steady-state/quality claims"}
    save(BENCH / "processed/p0-summary.json", final)
    make_figures(runs)
    write_control_report(summaries, request_rows, resource_rows, final)
    print(json.dumps({"runs": len(summaries), "passed": sum(row["status"] == "passed" for row in summaries),
                      "report": str(BENCH / "reports/P0-REPORT.md")}, ensure_ascii=False))


def write_control_report(summaries, request_rows, resource_rows, final):
    summaries = [row for row in summaries if row["profile"] == "control"]
    run_ids = {row["run_id"] for row in summaries}
    request_rows = [row for row in request_rows if row["run_id"] in run_ids]
    resource_rows = [row for row in resource_rows if row["run_id"] in run_ids]
    control_runs = [run for run in final["runs"] if run["run_id"] in run_ids]
    csvwrite(BENCH / "processed/p0-control-runs.csv", summaries)
    csvwrite(BENCH / "processed/p0-control-requests.csv", request_rows)
    csvwrite(BENCH / "processed/p0-control-resources.csv", resource_rows)
    save(BENCH / "processed/p0-control-summary.json", final | {
        "runs": control_runs, "planned_profiles": ["vllm/control", "sglang/control"],
        "scope": "P0 control candidates only; not resource-aligned or a formal performance comparison"})
    make_figures(control_runs, stem="p0-control-resources")
    lines = ["# P0：采集验收", "",
             
             f"本报告展示 {len(summaries)} 组控制候选。每组启动一次，发出一条预热和三条小负载请求，用于核对模型、token、计时与资源采样。现有部署组保留历史归档。", "",
             "## 配置与结果", "",
             "两边统一 BF16、16K、显存比例 0.65、单请求、MLP 卸载预算上限 5 GiB、prefill 分块 512，关闭 CUDA Graph。5 GiB 是预算，实际卸载量以各 run 日志为准。", "",
             "| 框架 / 配置 | 结果 | 成功请求 | 到 ready / s | KV token 池 | 整卡采样峰值 / MiB |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in summaries:
        ready = f"{row['ready_observed_s']:.1f}" if row["ready_observed_s"] is not None else "N/A"
        lines.append(f"| {row['service']} / {row['profile']} | {row['status']} | {row['smoke_success']}/3 | {ready} | {row['kv_cache_tokens'] or '见 runtime-extracted.json'} | {row['sampled_gpu_peak_mib']} |")
    lines += ["", "共同参数通过了启动与请求检查。两框架的显存比例语义、实际卸载字节、KV/状态池和依赖栈仍不同；P0 尚未完成资源校准。每个长度只有一条请求，样本不足以估计尾延迟或作框架排名。", "",
              "后续 P2 将 CPU 卸载改为 0 GiB、服务端并发上限改为 16，并按实际张量字节校准缓存池。P3/P4 使用[冻结配置](../p2/control/config.json)，与本报告分批统计。", "",
              "## 模型与运行环境", "",
              "显卡为 RTX 5070 Ti，16303 MiB；系统为 Windows / WSL 2 / Docker Desktop。模型使用同一 AWQ 4bit、group_size=128 checkpoint，部分模块未量化。19 个文件的 SHA256 与本地清单一致，清单 revision 为 `2438fefd72e07f4378f0e6c567b1db9e6d6e1e37`；本次未查询远端 revision。", "",
              "| 框架 | 框架版本 | PyTorch | CUDA 构建 | Transformers | Tokenizers | FlashInfer | Triton |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for service in ("vllm", "sglang"):
        installed = load(BENCH / f"configs/{service}-installed.json")
        packages = installed["packages"]
        lines.append(f"| {service} | {packages[service]} | {packages['torch']} | {installed['torch_cuda_build']} | {packages['transformers']} | {packages['tokenizers']} | {packages['flashinfer-python']} | {packages['triton']} |")
    lines += ["", "vLLM 使用 AutoAWQMarlinLinearMethod / MarlinLinearKernel，SGLang 使用 awq_marlin。两镜像对 128/512/2048 输入生成的完整 token IDs 一致。", "",
              "请求直连本机 OpenAI 流式 API，固定 seed=20261030、temperature=0、top_p=1、输出上限 32，关闭 thinking。测试未下载模型、修改生产 `.env` 或使用私人聊天数据；SGLang 在测试 override 中启用 metrics。", "",
              "## 逐请求结果", "",
              "| 框架 / 配置 | 输入 tokens | 输出 tokens | TTFT / ms | E2E / ms |",
              "| --- | --- | --- | --- | --- |"]
    for row in request_rows:
        if row["warmup"]:
            continue
        ttft = f"{row['ttft_ms']:.1f}" if row["ttft_ms"] is not None else "N/A"
        e2e = f"{row['e2e_ms']:.1f}" if row["e2e_ms"] is not None else "N/A"
        lines.append(f"| {row['service']} / {row['profile']} | {row['input_tokens']} | {row['output_tokens']} | {ttft} | {e2e} |")
    lines += ["", "## 计时与计数", "",
              "脚本记录发送、首个非空内容、最后内容及响应结束的单调时钟和 UTC。role、空 delta 与心跳不计作首 token，TTFB、TTFT、E2E 分开记录。TPOT 结合内容 chunk 时间和服务端 usage 估算；一个 chunk 可能包含多个 token，无法还原精确逐 token ITL。", "",
              "输出计数优先用流式最终 usage。脚本另存可见文本的重编码数量，EOS/特殊 token 可能造成差异，重编码值不覆盖 usage。固定种子和聊天模板开头可能带来缓存复用，各 run 保留原生缓存计数。", "",
              "脚本按发送量核对原生请求、输入和输出计数。SGLang 的内部 health/warmup 可能增加 prefill 计数，业务对账使用 num_requests/prompt_tokens/generation_tokens。缺失指标记为 unavailable。", "",
              "SGLang 的 mamba_*_tokens 指标实际计状态槽；mamba_usage 是不可回收槽位占比。vLLM 的 kv_cache_usage_perc=1 表示 100%。单位和 HELP 见 `../processed/p0-metrics-catalog.csv`，全量指标审计保留在 `../processed/p0-metrics-deltas.csv`。", "",
              "## 资源采样与限制", "",
              "GPU、Windows CPU/RAM 目标间隔 1 秒，WSL/cgroup 和 Docker stats 为 2 秒；文件记录实际间隔与查询耗时。容器时钟按主机查询前后时间界限核对，延迟使用主机单调时钟。", "",
              "GPU 显存为整卡采样，包含桌面和其他进程，短峰值可能落在采样间隔之间。WSL/WDDM 无法提供的进程显存记为 N/A；实际池结构需分别看 KV 与线性状态。无服务和预热空闲段各约 5 秒，后续 P3 另做 60 秒基线。", "",
              "ready 包含容器重建、权重读取及引擎预热。测试未清空操作系统文件缓存，首次请求也可能包含内核编译。P0 没有 profiler，瓶颈诊断见后续 P1。", "",
              "## 数据入口", "",
              "- [配置 CSV](../processed/p0-control-runs.csv)、[逐请求 CSV](../processed/p0-control-requests.csv)、[阶段资源 CSV](../processed/p0-control-resources.csv)。",
              "- [候选组 JSON](../processed/p0-control-summary.json)、[资源曲线](../figures/p0-control-resources.png)。", "",
              "原始 run：", ""]
    lines += [f"- `{row['run_id']}`：`../runs/{row['run_id']}/`。" for row in summaries]
    validation_path = BENCH / "processed/p0-validation.json"
    if validation_path.exists():
        validation = load(validation_path)
        smoke = sum(not row["warmup"] for row in request_rows)
        warmup = sum(bool(row["warmup"]) for row in request_rows)
        passed = sum(row["status"] == "passed" for row in summaries)
        lines += ["", "## 验收与恢复", "",
                  f"候选通过 {passed}/{len(summaries)}，本报告含 {smoke} 条测试请求和 {warmup} 条预热。历史全量验收为 **{validation['status']}**，其范围还包含未展示的现有部署组；原生计数、时间、采样与容器时钟检查见 `../processed/p0-validation.json`。历史检查中 API Key 明文残留文件数为 {len(validation['plaintext_api_key_files'])}。"]
    restoration_path = BENCH / "processed/p0-restoration.json"
    if restoration_path.exists():
        restoration = load(restoration_path)
        lines += ["", f"当次恢复检查为 **{restoration['status']}**：生产文件未变，两套容器恢复原参数并保持停止。实时状态请查询 Docker。"]
    lines += ["", "当前阶段编号与报告入口见 [PLAN](../PLAN.md)。后续阶段重新验证并冻结参数，P0 小样本不并入正式矩阵。", ""]
    (BENCH / "reports/P0-REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
