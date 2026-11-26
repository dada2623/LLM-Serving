#!/usr/bin/env bash
set -euo pipefail

namespace=${LLMD_NAMESPACE:-llm-d-serving}
model=${LLMD_MODEL:-Qwen3-0.6B}
router_service=${LLMD_ROUTER_SERVICE:-llm-serving-epp}
port=${LLMD_SMOKE_PORT:-18080}
ssh_host=${LLMD_KUBECTL_SSH_HOST:-}

run_local_smoke() {
  local ns=$1
  local service=$2
  local model_name=$3
  local forward_port=$4
  local log=/tmp/llm-d-router-port-forward.log

  kubectl -n "$ns" port-forward "svc/$service" "$forward_port":80 >"$log" 2>&1 &
  local port_forward_pid=$!
  cleanup() {
    kill "$port_forward_pid" >/dev/null 2>&1 || true
    wait "$port_forward_pid" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT

  local models_json=''
  for _ in $(seq 1 20); do
    if models_json=$(curl -fsS "http://127.0.0.1:$forward_port/v1/models" 2>/dev/null); then
      break
    fi
    sleep 1
  done
  [[ -n "$models_json" ]] || {
    echo 'Router /v1/models 请求失败' >&2
    cat "$log" >&2 || true
    return 1
  }
  printf '%s\n' "$models_json"

  local payload
  payload=$(printf '{"model":"%s","prompt":"请用一句话说明 Router 已经把请求转发到模型服务。","max_tokens":24}' "$model_name")
  curl -fsS -X POST "http://127.0.0.1:$forward_port/v1/completions" \
    -H 'Content-Type: application/json' \
    -d "$payload"
  printf '\nRouter smoke test passed\n'
}

if command -v kubectl >/dev/null 2>&1 && [[ -z "$ssh_host" ]]; then
  run_local_smoke "$namespace" "$router_service" "$model" "$port"
elif [[ -n "$ssh_host" ]]; then
  ssh -T -o ConnectTimeout=10 "$ssh_host" bash -s -- "$namespace" "$router_service" "$model" "$port" <<'REMOTE'
set -euo pipefail
namespace=$1
router_service=$2
model_name=$3
forward_port=$4
log=/tmp/llm-d-router-port-forward.log
kubectl -n "$namespace" port-forward "svc/$router_service" "$forward_port":80 >"$log" 2>&1 &
port_forward_pid=$!
cleanup() {
  kill "$port_forward_pid" >/dev/null 2>&1 || true
  wait "$port_forward_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT
models_json=''
for _ in $(seq 1 20); do
  if models_json=$(curl -fsS "http://127.0.0.1:$forward_port/v1/models" 2>/dev/null); then
    break
  fi
  sleep 1
done
[[ -n "$models_json" ]] || {
  echo 'Router /v1/models 请求失败' >&2
  cat "$log" >&2 || true
  exit 1
}
printf '%s\n' "$models_json"
payload=$(printf '{"model":"%s","prompt":"请用一句话说明 Router 已经把请求转发到模型服务。","max_tokens":24}' "$model_name")
curl -fsS -X POST "http://127.0.0.1:$forward_port/v1/completions" \
  -H 'Content-Type: application/json' \
  -d "$payload"
printf '\nRouter smoke test passed\n'
REMOTE
else
  echo '本机没有 kubectl。请设置 LLMD_KUBECTL_SSH_HOST，或在本机安装 kubectl。' >&2
  exit 127
fi
