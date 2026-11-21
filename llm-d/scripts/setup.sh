#!/usr/bin/env bash
set -euo pipefail

namespace=${LLMD_NAMESPACE:-llm-d-serving}
gaie_version=${GAIE_VERSION:-latest}
if [[ "$gaie_version" == "latest" ]]; then
  gaie_url='releases/latest/download'
else
  gaie_url="releases/download/${gaie_version}"
fi

command -v kubectl >/dev/null || { echo '缺少 kubectl' >&2; exit 127; }
command -v helm >/dev/null || { echo '缺少 helm' >&2; exit 127; }

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
gaie_manifest_file=${GAIE_MANIFEST_FILE:-$root/manifests/gateway-api-inference-extension/v1-manifests.yaml}
gaie_manifest_url=${GAIE_MANIFEST_URL:-https://github.com/kubernetes-sigs/gateway-api-inference-extension/${gaie_url}/v1-manifests.yaml}

if [[ "${GAIE_SKIP_CRD:-0}" != "1" ]]; then
  if [[ -f "$gaie_manifest_file" ]]; then
    kubectl apply -f "$gaie_manifest_file"
  else
    kubectl apply -f "$gaie_manifest_url"
  fi
fi
kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -

echo "LLM-D CRD 和命名空间已准备完成：$namespace"
