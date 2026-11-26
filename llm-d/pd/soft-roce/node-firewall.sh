#!/usr/bin/env bash
set -euo pipefail
action=$1
nic=$2
local_ip=$3
peer=$4
pod_cidr=$5
args=(allow)
case "$action" in
  add) ;;
  remove) args=(--force delete allow) ;;
  *) exit 2 ;;
esac
ufw "${args[@]}" in on "$nic" proto udp from "$peer" to "$local_ip" port 4791 comment 'llm-serving-rxe'
ufw "${args[@]}" in on "$nic" proto tcp from "$peer" to "$local_ip" port 18080 comment 'llm-serving-rxe'
# Flannel masquerades Pod traffic to the other node's host-network endpoint.
ufw "${args[@]}" in on "$nic" proto tcp from "$peer" to "$local_ip" port 8000 comment 'llm-serving-rxe'
for overlay in cni0 flannel.1; do
  ufw "${args[@]}" in on "$overlay" proto tcp from "$pod_cidr" to "$local_ip" port 8000 comment 'llm-serving-rxe'
done
