#!/usr/bin/env bash
set -euo pipefail

command -v kubectl >/dev/null || { echo '缺少 kubectl' >&2; exit 127; }

kubectl cluster-info
kubectl get nodes -o wide
printf '\n-- GPU extended resources --\n'
kubectl get nodes -o json | python3 -c '
import json, sys
payload = json.load(sys.stdin)
for item in payload.get("items", []):
    name = item["metadata"]["name"]
    alloc = item.get("status", {}).get("allocatable", {})
    gpu = {k: v for k, v in alloc.items() if k.endswith("/gpu") or "gpu" in k.lower()}
    display = gpu if gpu else "未发现 GPU extended resource"
    print(f"{name}: {display}")
'

printf '\n-- expected node labels --\n'
kubectl get nodes -L llm-d.ai/gpu-vendor
