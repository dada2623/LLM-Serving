#!/usr/bin/env bash
set -euo pipefail

profile=${1:-small}
mode=${2:-linux}
namespace=${LLMD_NAMESPACE:-llm-d-serving}
release=${LLMD_RELEASE:-llm-serving}
router_chart=${ROUTER_STANDALONE_CHART:-oci://ghcr.io/llm-d/charts/llm-d-router-standalone}
router_version=${ROUTER_CHART_VERSION:-v0}
router_node=${LLMD_ROUTER_NODE:-}
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

case "$profile" in
  small) model_overlay=small; router_values="$root/router/router-small.values.yaml" ;;
  9b) model_overlay=9b; router_values="$root/router/router-9b.values.yaml" ;;
  *) echo '用法：deploy.sh <small|9b> linux' >&2; exit 2 ;;
esac

case "$mode" in
  linux) kustomization="$root/manifests/modelserver/overlays/$model_overlay" ;;
  *) echo '用法：deploy.sh <small|9b> linux' >&2; exit 2 ;;
esac

command -v kubectl >/dev/null || { echo '缺少 kubectl' >&2; exit 127; }
command -v helm >/dev/null || { echo '缺少 helm' >&2; exit 127; }

"$root/scripts/setup.sh"
kubectl apply -k "$kustomization"
helm upgrade --install "$release" "$router_chart" \
  --namespace "$namespace" \
  --create-namespace \
  --version "$router_version" \
  -f "$router_values"

if [[ -n "$router_node" ]]; then
  kubectl -n "$namespace" patch deployment "${release}-epp" --type=json -p="[{\"op\":\"add\",\"path\":\"/spec/template/spec/nodeSelector\",\"value\":{\"kubernetes.io/hostname\":\"$router_node\"}},{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/1/imagePullPolicy\",\"value\":\"IfNotPresent\"}]"
fi

echo "已部署 profile=$profile mode=$mode namespace=$namespace"
