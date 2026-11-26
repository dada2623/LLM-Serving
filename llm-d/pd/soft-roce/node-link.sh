#!/usr/bin/env bash
set -euo pipefail
action=$1
nic=$2
local_ip=$3
device=rxe_llm
case "$action" in
  up)
    ip -4 -o addr show dev "$nic" | awk -v ip="$local_ip" '
      { split($4, address, "/"); if (address[1] == ip) found=1 }
      END { exit !found }'
    modprobe ib_uverbs
    modprobe rdma_rxe
    if [[ ! -d /sys/class/infiniband/$device ]]; then
      rdma link add "$device" type rxe netdev "$nic"
    fi
    rdma link show "$device/1" | awk -v nic="$nic" '
      /state ACTIVE/ && $NF == nic { found=1 }
      END { exit !found }'
    udevadm settle --timeout=15
    test "$(cat /sys/class/infiniband_verbs/uverbs0/ibdev)" = "$device"
    test -c /dev/infiniband/uverbs0
    read -r a b c d <<< "${local_ip//./ }"
    printf -v gid '0000:0000:0000:0000:0000:ffff:%02x%02x:%02x%02x' "$a" "$b" "$c" "$d"
    test "$(cat "/sys/class/infiniband/$device/ports/1/gids/1")" = "$gid"
    test "$(cat "/sys/class/infiniband/$device/ports/1/gid_attrs/types/1")" = 'RoCE v2'
    ;;
  down)
    if [[ -d /sys/class/infiniband/$device ]]; then
      rdma link delete "$device"
    fi
    ;;
  *) exit 2 ;;
esac
