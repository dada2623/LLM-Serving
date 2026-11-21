# LLM-D 三节点 K3s 服务集群

这里是项目的集群部署主线，用于把 NVIDIA 与 AMD 模型服务放进同一个 Linux K3s 集群，并通过 LLM-D Router 暴露统一的 OpenAI 兼容入口。目录中的 manifests、Router values、节点准备脚本和验证脚本共同描述从 GPU 节点就绪到模型请求通过的完整路径。

当前拓扑由一个 K3s Server 和两个 GPU K3s Agent 组成。NVIDIA 与 AMD 模型服务都在同一个集群中运行，由 Router 统一暴露服务入口。

## 拓扑和服务角色

| 角色 | 数量 | 运行内容 |
| --- | ---: | --- |
| K3s Server | 1 | Kubernetes API、调度和集群控制面 |
| NVIDIA GPU Agent | 1 | NVIDIA device plugin 与 vLLM 模型服务 |
| AMD GPU Agent | 1 | AMD device plugin 与 vLLM 模型服务 |
| Router | 1 | LLM-D Router standalone chart、Envoy 和 endpoint picker |

GPU 节点通过 `llm-d.ai/gpu-vendor` 标签区分。AMD 服务请求产品级 `amd.com/gpu-rx-7800-xt` 资源，NVIDIA 服务请求 `nvidia.com/gpu`。节点数量、地址和名称由目标集群提供，仓库不保存这些值。

## 目录

- `manifests/modelserver/base` 保存 NVIDIA 与 AMD 模型服务的共同定义。
- `manifests/modelserver/overlays/small` 部署 `Qwen3-0.6B` 双 GPU 节点验证。
- `manifests/modelserver/overlays/9b` 部署 `QuantTrio/Qwen3.5-9B-AWQ` 双 GPU 节点验证。
- `manifests/device-plugin` 保存 NVIDIA 与 AMD device plugin 清单。
- `manifests/gateway-api-inference-extension` 保存 Router 依赖的 CRD 清单。
- `router` 保存不同模型 profile 的 LLM-D Router values。
- `scripts` 保存集群检查、节点准备、部署、状态、smoke test 和停止入口。

## 前置条件

执行端需要安装并配置 `kubectl`、`helm`、`curl` 和 `python3`。GPU 节点需要完成对应驱动、device plugin、K3s Agent 和模型目录准备。

集群中先设置节点标签

```bash
kubectl label node <nvidia-node> llm-d.ai/gpu-vendor=nvidia --overwrite
kubectl label node <amd-node> llm-d.ai/gpu-vendor=amd --overwrite
```

每个 GPU 节点都应准备以下模型目录，具体 host path 可以通过修改 manifest 或建立相同路径提供

- `/srv/llm-serving/models/Qwen3-0.6B`
- `/srv/llm-serving/models/Qwen3.5-9B-AWQ`

模型服务以离线模式启动。模型目录需要在 Pod 所调度到的节点上可读。

## 节点准备

节点准备脚本需要以 root 身份在对应 GPU 节点执行。模型暂存目录通过 `MODEL_SOURCE_DIR` 指定

```bash
sudo MODEL_SOURCE_DIR=/path/to/Qwen3-0.6B ./llm-d/scripts/prepare-nvidia-node.sh
sudo MODEL_SOURCE_DIR=/path/to/Qwen3-0.6B ./llm-d/scripts/prepare-amd-node.sh
```

脚本会复制模型、核对 `model.safetensors` 的 SHA256，并检查 GPU 运行时和 K3s Agent 状态。AMD 节点还会检查 `rocminfo`，NVIDIA 节点还会检查 `nvidia-smi` 和 containerd runtime 配置。

## Registry 和离线镜像

优先使用目标环境自己的 registry 配置。两个 registry 脚本都要求通过环境变量提供 registry 信息和 Docker 配置路径，不保存登录凭据

```bash
sudo K3S_REGISTRY_MIRROR=registry.example.com \
  DOCKER_CONFIG_FILE=/path/to/docker/config.json \
  ./llm-d/scripts/configure-k3s-registry-from-docker-config.sh agent
```

如果必须导入离线镜像，先为每个归档设置 SSH target、归档路径和 SHA256，再运行脚本

```bash
NVIDIA_SSH_HOST=<nvidia-ssh-target> \
AMD_SSH_HOST=<amd-ssh-target> \
NVIDIA_ARCHIVE=<nvidia-archive> \
AMD_VLLM_ARCHIVE=<amd-vllm-archive> \
AMD_ENVOY_ARCHIVE=<envoy-archive> \
NVIDIA_VLLM_SHA256=<sha256> \
AMD_VLLM_SHA256=<sha256> \
AMD_ENVOY_SHA256=<sha256> \
./llm-d/scripts/import-prebuilt-images.sh
```

SSH target 使用当前执行环境的 SSH 配置解析。仓库不提供本地别名或默认目标。

## 部署流程

检查集群、安装 CRD 和命名空间

```bash
./llm-d/scripts/check-cluster.sh
./llm-d/scripts/setup.sh
```

部署小模型或 9B 模型

```bash
./llm-d/scripts/deploy.sh small linux
./llm-d/scripts/deploy.sh 9b linux
```

检查 Pod、InferencePool 和 Service，并执行 Router smoke test

```bash
./llm-d/scripts/status.sh
./llm-d/scripts/smoke.sh
```

`smoke.sh` 默认使用本地 `kubectl`。如果执行端没有 `kubectl`，设置 `LLMD_KUBECTL_SSH_HOST` 为用户配置的远程 SSH target，脚本会在远端执行 port-forward 和请求验证。

停止实验资源

```bash
./llm-d/scripts/stop.sh
```

## 资源语义

缩小模型不会自动把一张 GPU 切分成多个 Kubernetes GPU 资源。当前标准 overlay 每个 GPU 节点运行一个模型服务 Pod。若需要同一 GPU 上运行多个 Pod，先部署和验证 device plugin sharing 或 time-slicing，再调整副本数和资源请求。

## 运行时边界

这个入口用于三节点 K3s 集群中的普通模型服务路由验证。Prefill/Decode 与 KV Cache 传输仍需要单独的网络、Pod-to-Pod、CPU buffer 和 GPU buffer 验证。单节点或单方向测试不能替代跨节点双向测量。
