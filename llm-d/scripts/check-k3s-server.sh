#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo '请以 root 身份运行此脚本' >&2
  exit 1
fi

printf '%s\n' '=== K3s Server ==='
hostname
k3s --version
systemctl is-enabled k3s
systemctl is-active k3s
printf '%s\n' '=== Nodes ==='
k3s kubectl get nodes -o wide
printf '%s\n' '=== Labels and GPU resources ==='
k3s kubectl get nodes -L llm-d.ai/gpu-vendor
k3s kubectl get nodes -o custom-columns='NAME:.metadata.name,READY:.status.conditions[-1].status,NVIDIA:.status.allocatable.nvidia\.com/gpu,AMD:.status.allocatable.amd\.com/gpu,CONTAINERD:.status.nodeInfo.containerRuntimeVersion'
printf '%s\n' '=== kube-system workloads ==='
k3s kubectl -n kube-system get ds,pods -o wide
printf '%s\n' '=== kube-system events ==='
k3s kubectl get events --all-namespaces --sort-by=.lastTimestamp | tail -n 120
printf '%s\n' '=== device plugin details ==='
for pod in $(k3s kubectl -n kube-system get pods -l name=nvidia-device-plugin-ds -o name 2>/dev/null); do
  k3s kubectl -n kube-system describe "$pod" | tail -n 100
done
for pod in $(k3s kubectl -n kube-system get pods -l name=amdgpu-device-plugin-ds -o name 2>/dev/null); do
  k3s kubectl -n kube-system describe "$pod" | tail -n 100
done
printf '%s\n' '=== K3s images ==='
ctr -n k8s.io images list | sed -n '1,160p'
printf '%s\n' '=== registry configuration ==='
if [[ -f /etc/rancher/k3s/registries.yaml ]]; then
  sed -n '1,200p' /etc/rancher/k3s/registries.yaml
else
  echo '没有 /etc/rancher/k3s/registries.yaml'
fi
printf '%s\n' '=== containerd configuration ==='
find /var/lib/rancher/k3s/agent/etc/containerd -maxdepth 1 -type f -printf '%f\n' | sort
