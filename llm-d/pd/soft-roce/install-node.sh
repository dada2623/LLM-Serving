#!/usr/bin/env bash
# Run as root on each K3s worker during initial provisioning.
set -euo pipefail
if [[ $# != 6 ]]; then
  echo "Usage: $0 {amd|nvidia} <nic> <local-ip> <peer-ip> <pod-cidr> <device-plugin-binary>" >&2
  exit 2
fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case "$1" in amd|nvidia) ;; *) exit 2 ;; esac
[[ "$2" =~ ^[a-zA-Z0-9_.-]+$ && "$3" =~ ^[0-9.]+$ && "$4" =~ ^[0-9.]+$ ]] || exit 2
test "$(id -u)" = 0
test -x "$6"
install -Dm0755 "$root/node-link.sh" /usr/local/libexec/llm-serving-rxe
install -Dm0755 "$root/node-firewall.sh" /usr/local/libexec/llm-serving-rxe-firewall
install -Dm0644 "$root/llm-serving-rxe.service" /etc/systemd/system/llm-serving-rxe.service
install -Dm0755 "$6" /opt/llm-serving/generic-device-plugin
if [[ "$1" == nvidia ]]; then
  install -d -m0755 /opt/llm-serving/nvidia-rxe-verbs
  ln -sfn /usr/lib/x86_64-linux-gnu/libibverbs.so.1 \
    /opt/llm-serving/nvidia-rxe-verbs/libibverbs-1de1263b.so.1.14.48.0
fi
printf 'RXE_NIC=%s\nRXE_LOCAL_IP=%s\n' "$2" "$3" > /etc/llm-serving-rxe.conf
chmod 0644 /etc/llm-serving-rxe.conf
systemctl daemon-reload
systemctl enable --now llm-serving-rxe.service
/usr/local/libexec/llm-serving-rxe up "$2" "$3"
/usr/local/libexec/llm-serving-rxe-firewall add "$2" "$3" "$4" "$5"
