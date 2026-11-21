# LLM-Serving

面向异构 GPU 的 LLM 服务工程仓库，聚焦把 NVIDIA 与 AMD 推理节点组织进三节点 Linux K3s 集群，并通过 LLM-D Router 提供统一服务入口。仓库把部署清单、节点准备、运行时配置、服务验证、网络路径诊断和单卡基准放在同一套可复核流程中。

当前项目的主要工作包括

- 使用 Kubernetes GPU device plugin 管理 NVIDIA 与 AMD 节点资源。
- 使用 vLLM 部署 `Qwen3-0.6B` 和 `QuantTrio/Qwen3.5-9B-AWQ` 模型服务。
- 使用 LLM-D Router、Envoy 和 Gateway API Inference Extension 进行端点发现与请求路由。
- 通过 K3s、Pod-to-Pod 网络测试和传输校验，分析异构 Prefill/Decode 的部署条件与链路代价。
- 用可审阅的单卡基准结果记录吞吐、延迟、SLO 和资源占用，支持后续 PD 对照。

当前已完成三节点 K3s 的模型服务路由验证。Prefill/Decode 与 KV Cache 传输仍按独立实验路径推进，网络正确性、服务可用性和性能收益分别记录。

## 当前项目状态

- Linux 原生 K3s 部署入口位于 [`llm-d/`](llm-d/)。
- 当前验证拓扑是一台 K3s Server 加两台 K3s Agent，GPU 节点分别使用 NVIDIA 与 AMD device plugin。
- 小模型路径使用 `Qwen3-0.6B`，9B 路径使用 `QuantTrio/Qwen3.5-9B-AWQ`。
- 模型服务使用 vLLM，Router 使用 LLM-D Router standalone chart 和 Gateway API Inference Extension。
- 节点选择通过 `llm-d.ai/gpu-vendor` 标签完成，AMD 资源使用产品级 extended resource，避免调度到非目标 GPU。
- 公开文件只保留通用路径、占位符和可复核结果。集群地址、SSH 别名、用户名、认证信息和本地文件路径通过执行环境提供。

## K3s 部署入口

先确认当前 `kubectl` context 指向目标集群，并在两个 GPU 节点准备相同的模型目录。节点标签示例

```bash
kubectl label node <nvidia-node> llm-d.ai/gpu-vendor=nvidia --overwrite
kubectl label node <amd-node> llm-d.ai/gpu-vendor=amd --overwrite
```

检查集群和安装 CRD

```bash
./llm-d/scripts/check-cluster.sh
./llm-d/scripts/setup.sh
```

部署小模型或 9B 模型

```bash
./llm-d/scripts/deploy.sh small linux
./llm-d/scripts/deploy.sh 9b linux
```

查看状态、执行请求验证和停止实验资源

```bash
./llm-d/scripts/status.sh
./llm-d/scripts/smoke.sh
./llm-d/scripts/stop.sh
```

完整的模型目录、device plugin、Router values、离线镜像导入和节点准备说明见 [`llm-d/README.md`](llm-d/README.md)。

## 配置方式

集群相关值通过环境变量或执行端的 Kubernetes 配置提供。脚本不预置本地主机名、地址或用户名。常用配置项包括

- `LLMD_NAMESPACE` 和 `LLMD_RELEASE` 控制资源命名空间与 Helm release 名称。
- `LLMD_MODEL`、`LLMD_ROUTER_SERVICE` 和 `LLMD_SMOKE_PORT` 控制 smoke test。
- `LLMD_KUBECTL_SSH_HOST` 仅在执行端没有 `kubectl` 时指定一个由用户自行配置的 SSH target。
- `MODEL_SOURCE_DIR` 指定 GPU 节点上的模型暂存目录。
- `DOCKER_CONFIG_FILE`、`K3S_REGISTRY_MIRROR` 和 `K3S_REGISTRY_USER` 用于私有 registry 配置。
- 离线镜像导入脚本的主机、归档路径和 SHA256 值都必须显式传入。

## 基准结果

单卡 Control 的 P4 正式矩阵保存在 [`benchmark/p4/control/`](benchmark/p4/control/)。三个输入输出负载、两个推理框架、五档闭环并发和三轮正式重复已经完成。正式矩阵共 90 个单元，成功请求 10098 条，错误数为 0。

三轮均满足本批次 SLO 的最大已测并发如下

| 框架 | 128/128 | 512/256 | 2048/256 |
| --- | ---: | ---: | ---: |
| vLLM | 16 | 16 | 2 |
| SGLang | 16 | 16 | 8 |

2048/256 在并发 16 时仍未达到本批次 SLO。这个结果用于规划后续 Prefill/Decode 对照，不能直接解释为跨机 PD 的收益。

## 网络路径诊断

跨主机传输诊断的脱敏摘要见 [`docs/network-path-diagnosis.md`](docs/network-path-diagnosis.md)。已观察到同一套 GPU、CPU buffer 和 raw TCP 测试在不同封装路径上呈现不同方向性。当前证据优先指向 WSL、Docker、TUN、SSH carrier 或其组合带来的路径差异，因此后续 PD 测试需要同时测量两个方向的实际 Pod-to-Pod 路径。

## 目录结构

- `llm-d/` 保存 K3s manifests、device plugin、Router values 和部署脚本。
- `benchmark/` 保存单卡基准计划、脚本和已审阅汇总结果。
- `docker/` 保存历史单机镜像定义与运行时探针。
- `experiments/` 仅保留在本地工作区，用于实验过程、原始日志和故障诊断。
- `docs/` 保存可以公开发布的脱敏说明。

原始运行目录、逐请求日志、环境快照和本地身份信息不属于发布内容。复用部署入口时，应在目标集群侧补齐模型、device plugin、registry、Kubernetes context 和节点标签。
