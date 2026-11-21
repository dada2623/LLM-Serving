#!/usr/bin/env bash
set -euo pipefail
namespace=${LLMD_NAMESPACE:-llm-d-serving}

command -v kubectl >/dev/null || { echo '缺少 kubectl' >&2; exit 127; }

kubectl get nodes -L llm-d.ai/gpu-vendor
kubectl get pods -n "$namespace" -o wide
kubectl get inferencepools -n "$namespace" 2>/dev/null || true
kubectl get svc -n "$namespace" 2>/dev/null || true
