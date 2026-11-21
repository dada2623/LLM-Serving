#!/usr/bin/env bash
set -euo pipefail

nvidia_host=${NVIDIA_SSH_HOST:?请设置 NVIDIA_SSH_HOST}
amd_host=${AMD_SSH_HOST:?请设置 AMD_SSH_HOST}
zt_device=${NVIDIA_ZT_DEVICE:-/dev/sda2}
zt_mount=${NVIDIA_ZT_MOUNT:-/mnt/zt}
nvidia_archive=${NVIDIA_ARCHIVE:?请设置 NVIDIA_ARCHIVE}
amd_vllm_archive=${AMD_VLLM_ARCHIVE:?请设置 AMD_VLLM_ARCHIVE}
amd_envoy_archive=${AMD_ENVOY_ARCHIVE:?请设置 AMD_ENVOY_ARCHIVE}

nvidia_sha256=${NVIDIA_VLLM_SHA256:?请设置 NVIDIA_VLLM_SHA256}
amd_vllm_sha256=${AMD_VLLM_SHA256:?请设置 AMD_VLLM_SHA256}
amd_envoy_sha256=${AMD_ENVOY_SHA256:?请设置 AMD_ENVOY_SHA256}

remote_script=''
cleanup_remote_script() {
  if [[ -n "$remote_script" ]]; then
    ssh "$1" "rm -f '$remote_script'" >/dev/null 2>&1 || true
  fi
}
run_remote_script() {
  local host=$1
  local name=$2
  local body=$3
  local local_script
  remote_script="/tmp/llm-d-${name}-$$.sh"
  local_script=$(mktemp)
  printf '%s\n' "$body" >"$local_script"
  chmod 700 "$local_script"
  trap 'rm -f "$local_script"' RETURN
  scp -q -o ConnectTimeout=10 "$local_script" "$host:$remote_script"
  ssh -tt -o ConnectTimeout=10 "$host" "bash '$remote_script'"
  cleanup_remote_script "$host"
  remote_script=''
  trap - RETURN
}

printf '%s\n' '=== NVIDIA 节点离线镜像导入 ==='
run_remote_script "$nvidia_host" nvidia "$(cat <<REMOTE
#!/usr/bin/env bash
set -euo pipefail
zt_device=$(printf '%q' "$zt_device")
zt_mount=$(printf '%q' "$zt_mount")
archive_rel=$(printf '%q' "$nvidia_archive")
expected_sha256=$(printf '%q' "$nvidia_sha256")
image='docker.io/vllm/vllm-openai:v0.33.0'

sudo -v
if sudo /usr/local/bin/k3s ctr -n k8s.io images list --quiet | grep -Fxq "\$image"; then
  echo "NVIDIA 镜像已存在，跳过导入：\$image"
else
  if ! mountpoint -q "\$zt_mount"; then
    sudo mkdir -p "\$zt_mount"
    sudo mount -t ntfs3 -o "uid=\$(id -u),gid=\$(id -g),umask=022" "\$zt_device" "\$zt_mount"
  fi
  archive="\$zt_mount/\$archive_rel"
  [[ -f "\$archive" ]] || { echo "找不到 NVIDIA 镜像归档：\$archive" >&2; exit 1; }
  actual_sha256=\$(sha256sum "\$archive" | awk '{print \$1}' | tr '[:upper:]' '[:lower:]')
  expected_sha256=\$(printf '%s' "\$expected_sha256" | tr '[:upper:]' '[:lower:]')
  [[ "\$actual_sha256" == "\$expected_sha256" ]] || {
    echo 'NVIDIA 镜像 SHA256 不匹配' >&2
    echo "expected=\$expected_sha256" >&2
    echo "actual=\$actual_sha256" >&2
    exit 1
  }
  sudo /usr/local/bin/k3s ctr -n k8s.io images import "\$archive"
fi
sudo /usr/local/bin/k3s ctr -n k8s.io images list --quiet | grep -Fx "\$image"
REMOTE
)

printf '%s\n' '=== AMD 节点离线镜像导入 ==='
run_remote_script "$amd_host" amd "$(cat <<REMOTE
#!/usr/bin/env bash
set -euo pipefail
vllm_archive=$(printf '%q' "$amd_vllm_archive")
expected_vllm_sha256=$(printf '%q' "$amd_vllm_sha256")
envoy_archive=$(printf '%q' "$amd_envoy_archive")
expected_envoy_sha256=$(printf '%q' "$amd_envoy_sha256")
vllm_image='docker.io/vllm/vllm-openai-rocm:v0.31.0'
envoy_image='docker.io/envoyproxy/envoy:distroless-v1.40.2'

sudo -v
import_if_missing() {
  local image=$1
  local archive=$2
  local expected_sha256=$3
  if sudo /usr/local/bin/k3s ctr -n k8s.io images list --quiet | grep -Fxq "\$image"; then
    echo "镜像已存在，跳过导入：\$image"
    return
  fi
  [[ -f "\$archive" ]] || { echo "找不到镜像归档：\$archive" >&2; exit 1; }
  local actual_sha256
  actual_sha256=\$(sha256sum "\$archive" | awk '{print \$1}' | tr '[:upper:]' '[:lower:]')
  expected_sha256=\$(printf '%s' "\$expected_sha256" | tr '[:upper:]' '[:lower:]')
  [[ "\$actual_sha256" == "\$expected_sha256" ]] || {
    echo "镜像 SHA256 不匹配：\$archive" >&2
    echo "expected=\$expected_sha256" >&2
    echo "actual=\$actual_sha256" >&2
    exit 1
  }
  sudo /usr/local/bin/k3s ctr -n k8s.io images import "\$archive"
}
import_if_missing "\$vllm_image" "\$vllm_archive" "\$expected_vllm_sha256"
import_if_missing "\$envoy_image" "\$envoy_archive" "\$expected_envoy_sha256"
sudo /usr/local/bin/k3s ctr -n k8s.io images list --quiet | grep -FxE "\$vllm_image|\$envoy_image"
REMOTE
)

printf '%s\n' '离线镜像导入和校验完成。'
