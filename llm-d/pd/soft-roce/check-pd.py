#!/usr/bin/env python3
"""Verify a fixed 9B pull request from an in-cluster client."""
import argparse
import json
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

MODEL = "Qwen3.5-9B-AWQ"
REQUIRED = (
    "nixl_xfer_time_seconds_count",
    "nixl_bytes_transferred_sum",
    "external_prefix_cache_hits_total",
)
FAILURES = (
    "nixl_num_failed_transfers_total",
    "nixl_num_failed_notifications_total",
    "nixl_num_kv_expired_reqs_total",
)
METRICS = REQUIRED + FAILURES + ("nixl_xfer_time_seconds_sum",)


def get_metrics(endpoint):
    with urlopen(endpoint + "/metrics", timeout=5) as response:
        raw = response.read().decode()
    counters = dict.fromkeys(METRICS, 0.0)
    for line in raw.splitlines():
        if not line.startswith("vllm:") or f'model_name="{MODEL}"' not in line:
            continue
        name = line.split("{", 1)[0].split(":", 1)[1]
        if name in counters:
            counters[name] += float(line.rsplit(maxsplit=1)[1])
    return counters


def generate(endpoint, body):
    req = Request(
        endpoint + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    with urlopen(req, timeout=75) as response:
        return {
            "http_status": response.status,
            "elapsed_seconds": time.monotonic() - start,
            "body": json.load(response),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefill_url")
    parser.add_argument("decode_url")
    parser.add_argument("decode_engine_id")
    args = parser.parse_args()
    source = args.prefill_url.rstrip("/")
    target = args.decode_url.rstrip("/")
    request_id = f"rxe-check-{uuid4()}"
    prompt = request_id + "\n" + (
        "Software RoCE transfers registered host memory across Ethernet. "
        "Prefill writes the KV cache. The decode node reads the remote blocks. "
        * 55
    ) + "\nSummarize the passage in one sentence."
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 1,
        "stream": False,
        "kv_transfer_params": {
            "do_remote_decode": True,
            "remote_engine_id": args.decode_engine_id,
            "remote_request_id": request_id,
            "remote_host": urlsplit(target).hostname,
            "remote_port": 18080,
            "tp_size": 1,
            "dcp_size": 1,
            "pp_size": 1,
        },
    }
    before = get_metrics(target)
    prefill = generate(source, body)
    params = prefill["body"].get("kv_transfer_params")
    if not params or not any(params.get("remote_block_ids", [])):
        raise SystemExit(f"No remote KV blocks in Prefill response: {prefill}")
    decode = generate(target, {
        **body,
        "max_tokens": 8,
        "kv_transfer_params": {
            **params,
            "do_remote_prefill": True,
            "do_remote_decode": False,
        },
    })
    for _ in range(12):
        after = get_metrics(target)
        delta = {name: after[name] - before[name] for name in METRICS}
        if all(delta[name] > 0 for name in REQUIRED) or any(delta[name] for name in FAILURES):
            break
        time.sleep(1)
    passed = all(delta[name] > 0 for name in REQUIRED) and all(delta[name] == 0 for name in FAILURES)
    print(json.dumps({
        "prefill_url": source,
        "decode_url": target,
        "request_id": request_id,
        "prefill": prefill,
        "decode": decode,
        "decode_metrics_delta": delta,
        "passed": passed,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
