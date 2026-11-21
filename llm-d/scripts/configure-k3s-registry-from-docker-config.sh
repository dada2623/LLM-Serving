#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo '请以 root 身份运行此脚本' >&2
  exit 1
fi

service=${1:-}
case "$service" in
  server) k3s_service=k3s ;;
  agent) k3s_service=k3s-agent ;;
  *) echo '用法：configure-k3s-registry-from-docker-config.sh <server|agent>' >&2; exit 2 ;;
esac

registry=${K3S_REGISTRY_MIRROR:?请设置 K3S_REGISTRY_MIRROR}
docker_config=${DOCKER_CONFIG_FILE:?请设置 DOCKER_CONFIG_FILE}
[[ -r "$docker_config" ]] || { echo "无法读取 Docker 配置：$docker_config" >&2; exit 1; }

registry_auth=$(python3 - "$docker_config" "$registry" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
auths = payload.get("auths", {})
entry = auths.get(sys.argv[2]) or auths.get("https://" + sys.argv[2])
if not entry or not entry.get("auth"):
    raise SystemExit("Docker 配置中没有可直接使用的 auth 字段")
print(entry["auth"], end="")
PY
)

manifest_url="https://${registry}/v2/rancher/mirrored-pause/manifests/3.10.2"
if ! curl -fsSIL --connect-timeout 10 --max-time 20 \
  -H "Authorization: Basic ${registry_auth}" \
  -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
  "$manifest_url" >/dev/null; then
  echo "Docker 配置中的凭据无法访问 ${manifest_url}" >&2
  exit 1
fi

config_dir=/etc/rancher/k3s
config_file=${config_dir}/registries.yaml
install -d -m 0755 "$config_dir"
tmp_file=$(mktemp)
trap 'rm -f "$tmp_file"' EXIT
umask 077
cat >"$tmp_file" <<CONFIG
mirrors:
  docker.io:
    endpoint:
      - "https://${registry}"
configs:
  "${registry}":
    auth:
      auth: "${registry_auth}"
CONFIG
install -m 0600 "$tmp_file" "$config_file"
unset registry_auth

systemctl restart "$k3s_service"
systemctl is-active --quiet "$k3s_service"

printf '%s\n' '=== K3s registry mirror configured from Docker login ==='
printf 'host=%s\n' "$(hostname)"
printf 'service=%s\n' "$k3s_service"
printf 'config=%s\n' "$config_file"
printf 'mirror=https://%s\n' "$registry"
printf '%s\n' '=== K3s CRI pause image pull test ==='
# ctr does not use the CRI mirror resolution path. K3s kubelet pulls through
# CRI, so use crictl for a test that matches the runtime path used by Pods.
crictl_bin=$(command -v crictl || true)
if [[ -z "$crictl_bin" ]]; then
  crictl_bin=/usr/local/bin/crictl
fi
[[ -x "$crictl_bin" ]] || { echo '找不到 crictl，无法验证 K3s CRI 镜像拉取' >&2; exit 1; }
"$crictl_bin" pull docker.io/rancher/mirrored-pause:3.10.2
printf '%s\n' 'K3s CRI 镜像拉取测试通过'
