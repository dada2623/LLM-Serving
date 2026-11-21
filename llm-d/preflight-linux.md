# Linux K3s 节点前置核验

这份清单用于在一个 K3s Server 加两个 GPU Agent 的三节点服务集群中执行部署前检查。文档只描述检查项和目标状态，不保存节点名称、地址、用户名或本地 SSH 配置。

## 集群状态

在拥有集群访问权限的执行端运行

```bash
kubectl cluster-info
kubectl get nodes -o wide
kubectl get nodes -L llm-d.ai/gpu-vendor
kubectl get pods -A -o wide
```

目标状态如下

- Server 节点为 `Ready`，并承担控制面职责。
- NVIDIA 与 AMD Agent 均为 `Ready`。
- NVIDIA 节点带有 `llm-d.ai/gpu-vendor=nvidia`。
- AMD 节点带有 `llm-d.ai/gpu-vendor=amd`。
- K3s 系统 Pod、网络插件、CoreDNS、ServiceLB 和 local-path-provisioner 正常运行。

## NVIDIA 节点

以 root 身份检查

```bash
nvidia-smi
systemctl is-enabled k3s-agent
systemctl is-active k3s-agent
ls -l /usr/bin/nvidia-container-runtime
```

确认节点 allocatable 中存在 `nvidia.com/gpu`，并且 K3s containerd 配置包含 NVIDIA runtime。

## AMD 节点

以 root 身份检查

```bash
rocminfo
ls -l /dev/kfd /dev/dri
systemctl is-enabled k3s-agent
systemctl is-active k3s-agent
```

确认 AMD device plugin 注册了产品级资源，例如 `amd.com/gpu-rx-7800-xt`。如果同一主机存在集成显卡，资源声明需要继续保持产品级筛选。

## 模型和镜像

两个 GPU 节点都需要能够读取对应模型目录

```bash
find /srv/llm-serving/models/Qwen3-0.6B -maxdepth 1 -type f -print
find /srv/llm-serving/models/Qwen3.5-9B-AWQ -maxdepth 1 -type f -print
```

离线部署时，在每个节点的 K3s containerd 中确认 vLLM 与 Router 所需镜像。若使用 registry mirror，先使用脚本验证 CRI 路径的 pause 镜像拉取，再部署模型服务。

## 标签和资源核对

```bash
kubectl get nodes -L llm-d.ai/gpu-vendor
kubectl get nodes -o custom-columns='NAME:.metadata.name,NVIDIA:.status.allocatable.nvidia\.com/gpu,AMD:.status.allocatable.amd\.com/gpu'
```

资源检查通过后，再执行 `setup.sh`、`deploy.sh` 和 `smoke.sh`。集群健康只说明基础调度和服务路由可用，不能替代跨节点 Prefill/Decode 传输验证。
