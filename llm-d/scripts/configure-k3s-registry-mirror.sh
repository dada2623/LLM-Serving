#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo '请以 root 身份运行此脚本' >&2
  exit 1
fi

registry=${K3S_REGISTRY_MIRROR:?请设置 K3S_REGISTRY_MIRROR}
username=${K3S_REGISTRY_USER:?请设置 K3S_REGISTRY_USER}
service=${1:-}
case "$service" in
  server) k3s_service=k3s ;;
  agent) k3s_service=k3s-agent ;;
  *) echo '用法：configure-k3s-registry-mirror.sh <server|agent>' >&2; exit 2 ;;
esac

read -r -s -p "${registry} 密码（用户 ${username}）：" password
printf '\n'
[[ -n "$password" ]] || { echo '密码不能为空' >&2; exit 1; }

manifest_url="https://${registry}/v2/rancher/mirrored-pause/manifests/3.10.2"
if ! curl -fsSIL --connect-timeout 10 --max-time 20 \
  -u "${username}:${password}" \
  -H 'Accept: application/vnd.docker.distribution.manifest.v2+json' \
  "$manifest_url" >/dev/null; then
  echo "无法使用提供的凭据访问 ${manifest_url}" >&2
  exit 1
fi

auth=$(printf '%s' "${username}:${password}" | base64 -w0)
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
      auth: "${auth}"
CONFIG
install -m 0600 "$tmp_file" "$config_file"
unset password auth

systemctl restart "$k3s_service"
systemctl is-active --quiet "$k3s_service"

printf '%s\n' '=== K3s registry mirror configured ==='
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
