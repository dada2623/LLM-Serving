#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo '请以 root 身份运行此脚本' >&2
  exit 1
fi

source=${MODEL_SOURCE_DIR:?请设置 MODEL_SOURCE_DIR，例如 /path/to/Qwen3-0.6B}
destination=/srv/llm-serving/models/Qwen3-0.6B
config=/var/lib/rancher/k3s/agent/etc/containerd/config.toml

[[ -d "$source" ]] || { echo "模型暂存目录不存在：$source" >&2; exit 1; }
[[ -x /usr/bin/nvidia-container-runtime ]] || { echo '缺少 /usr/bin/nvidia-container-runtime' >&2; exit 1; }

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

if [[ -r "$config" ]] && ! grep -q 'nvidia' "$config"; then
  echo '当前 K3s containerd 配置未发现 nvidia runtime，重启 k3s-agent 触发重新生成'
  systemctl restart k3s-agent
  sleep 5
fi

printf '%s\n' '=== NVIDIA 节点准备结果 ==='
hostname
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
printf 'model=%s\n' "$destination"
printf 'model_sha256=%s\n' "$destination_sha"
findmnt /srv
systemctl is-enabled k3s-agent
systemctl is-active k3s-agent
if [[ -r "$config" ]]; then
  grep -n -i 'nvidia' "$config" || true
else
  echo "无法读取 K3s containerd 配置：$config" >&2
  exit 1
fi
