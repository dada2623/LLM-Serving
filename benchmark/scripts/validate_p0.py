"""Validate persisted P0 evidence, including timing, native counters and secret exclusion."""
import json
from pathlib import Path
from datetime import datetime, timezone
import csv

BENCH = Path(__file__).resolve().parents[1]
ROOT = BENCH.parent


def main():
    summary = json.loads((BENCH / "processed/p0-summary.json").read_text(encoding="utf-8"))
    issues = []
    profiles = {(r["service"], r["profile"]) for r in summary["runs"]}
    expected = {(s, p) for s in ("vllm", "sglang") for p in ("existing", "control")}
    if profiles != expected:
        issues.append("Not all four planned service/profile combinations are present")
    if not summary["model_asset_check"] or not summary["tokenizer_comparison"]["identical_messages_and_input_ids"]:
        issues.append("Frozen model/tokenizer comparison failed")
    for run in summary["runs"]:
        ident = run["run_id"]
        folder = BENCH / "runs" / ident
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        audit = json.loads((folder / "metrics-audit.json").read_text(encoding="utf-8"))
        requests = [json.loads(line) for line in (folder / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
        if run["status"] != "passed" or run["requests_successful"] != 3 or len(requests) != 4:
            issues.append(ident + ": request completion gate failed")
        if not manifest["production_env_unchanged"]:
            issues.append(ident + ": production env changed")
        if not audit.get("request_counter_matches"):
            issues.append(ident + ": native request counter mismatch")
        if audit.get("prompt_counter_delta") != audit.get("expected_prompt_tokens_including_warmup"):
            issues.append(ident + ": native prompt-token counter mismatch")
        if audit.get("output_counter_delta") != audit.get("expected_completion_tokens_including_warmup"):
            issues.append(ident + ": native generated-token counter mismatch")
        if json.loads((folder / "collector-errors.json").read_text(encoding="utf-8")):
            issues.append(ident + ": collector errors exist")
        for req in requests:
            times = [req.get(x) for x in ("send_monotonic_ns", "first_content_monotonic_ns", "last_content_monotonic_ns", "completed_monotonic_ns")]
            if any(x is None for x in times) or times != sorted(times):
                issues.append(req["request_id"] + ": invalid monotonic timing")
            if not req["warmup"] and (not req.get("input_token_count_matches") or req.get("usage", {}).get("completion_tokens") != 32):
                issues.append(req["request_id"] + ": workload token mismatch")
            if not req.get("received_done") or not req.get("output_text"):
                issues.append(req["request_id"] + ": incomplete SSE stream")
        with (folder / "gpu.csv").open(encoding="utf-8") as file:
            gpu = list(csv.DictReader(file))
        if len(gpu) < 20 or not {"p0-input-128", "p0-input-512", "p0-input-2048"}.issubset({r["phase"] for r in gpu}):
            issues.append(ident + ": GPU sampling coverage insufficient")
        clock = json.loads((folder / "clock-alignment.json").read_text(encoding="utf-8"))
        if not clock["host_before_ns"] <= clock["container_ns"] <= clock["host_after_ns"]:
            issues.append(ident + ": container clock outside host bracketing interval")
    settings = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines()
                    if line.strip() and not line.lstrip().startswith("#") and "=" in line)
    secret = settings["API_KEY"].encode()
    secret_files = []
    for path in BENCH.rglob("*"):
        if path.is_file() and path.suffix not in (".png", ".pyc") and secret in path.read_bytes():
            secret_files.append(str(path.relative_to(BENCH)))
    if secret_files:
        issues.append("API key appeared in persisted files: " + ", ".join(secret_files))
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "status": "passed" if not issues else "failed",
              "profiles": len(profiles), "runs": len(summary["runs"]),
              "smoke_requests": sum(run["requests_successful"] for run in summary["runs"]),
              "warmup_requests": sum(run["warmups"] for run in summary["runs"]), "native_counter_and_timing_checks": "passed" if not issues else "see issues",
              "plaintext_api_key_files": secret_files, "issues": issues}
    (BENCH / "processed/p0-validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
