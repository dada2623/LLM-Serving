#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo '请以 root 身份运行此脚本' >&2
  exit 1
fi

export PATH=/opt/rocm/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

source=${MODEL_SOURCE_DIR:?请设置 MODEL_SOURCE_DIR，例如 /path/to/Qwen3-0.6B}
destination=/srv/llm-serving/models/Qwen3-0.6B

pacman -S --needed rocminfo helm kustomize

[[ -d "$source" ]] || { echo "模型暂存目录不存在：$source" >&2; exit 1; }
install -d -m 0755 /srv/llm-serving/models
install -d -m 0755 "$destination"
cp -a "$source/." "$destination/"
chown -R root:root "$destination"

source_sha=$(sha256sum "$source/model.safetensors" | awk '{print $1}')
destination_sha=$(sha256sum "$destination/model.safetensors" | awk '{print $1}')
[[ "$source_sha" == "$destination_sha" ]] || {
  echo "模型校验失败：源=$source_sha 目标=$destination_sha" >&2
  exit 1
}

printf '%s\n' '=== AMD 节点准备结果 ==='
hostname
rocminfo_bin=$(command -v rocminfo || true)
[[ -n "$rocminfo_bin" ]] || { echo '找不到 rocminfo' >&2; exit 1; }
printf 'rocminfo=%s\n' "$rocminfo_bin"
"$rocminfo_bin" | sed -n '1,45p'
printf 'model=%s\n' "$destination"
printf 'model_sha256=%s\n' "$destination_sha"
findmnt /srv
systemctl is-enabled k3s-agent
systemctl is-active k3s-agent
