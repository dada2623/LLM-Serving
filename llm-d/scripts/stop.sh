#!/usr/bin/env bash
set -euo pipefail
namespace=${LLMD_NAMESPACE:-llm-d-serving}
release=${LLMD_RELEASE:-llm-serving}

command -v kubectl >/dev/null || { echo '缺少 kubectl' >&2; exit 127; }
command -v helm >/dev/null || { echo '缺少 helm' >&2; exit 127; }

helm uninstall "$release" --namespace "$namespace" 2>/dev/null || true
kubectl delete namespace "$namespace" --ignore-not-found=true

echo "已删除 LLM-D 实验资源：$namespace"
