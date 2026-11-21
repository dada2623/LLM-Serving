#!/usr/bin/env bash
set -euo pipefail

archive=${1:-}
expected_image=${2:-}
containerd_namespace=${K3S_CONTAINERD_NAMESPACE:-k8s.io}

if [[ -z "$archive" || -z "$expected_image" ]]; then
  echo "用法：$0 <image-archive.tar> <完整镜像名:tag>" >&2
  exit 2
fi

[[ -f "$archive" ]] || { echo "镜像归档不存在：$archive" >&2; exit 1; }
command -v k3s >/dev/null || { echo '找不到 k3s' >&2; exit 127; }

sudo k3s ctr --namespace "$containerd_namespace" images import "$archive"

if ! sudo k3s ctr --namespace "$containerd_namespace" images list --quiet \
  | grep -Fxq "$expected_image"; then
  echo "导入后没有找到预期镜像：$expected_image" >&2
  echo '当前 k8s.io 镜像列表：' >&2
  sudo k3s ctr --namespace "$containerd_namespace" images list >&2
  exit 1
fi

echo "K3s 镜像导入并校验成功：$expected_image"
