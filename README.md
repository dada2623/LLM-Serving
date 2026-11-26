# LLM-Serving

在三节点 Linux K3s 集群上部署异构 GPU 推理服务。一个 K3s Server 负责控制面，两个 GPU Agent 分别使用 NVIDIA RTX 5070 Ti 和 AMD RX 7800 XT 运行 vLLM。llm-d Router 通过 OpenAI 兼容 API 为请求选择后端。

| 组件 | 职责 |
| --- | --- |
| vLLM 模型服务 | 通过 NVIDIA 和 AMD device plugin 调度，每个 GPU 节点运行一个模型 Pod，使用节点上的模型权重 |
| llm-d Router | 通过 Helm 部署 Envoy 和 Endpoint Picker，在两个 vLLM 副本间分配整请求 |
| EPP-Opt | 独立的 Go 路由器，评估 AMD Prefill → NVIDIA Decode 的拆分请求路径 |

## 部署

准备 K3s、GPU 驱动和节点标签，按目标节点配置镜像来源、模型目录与 AMD 运行时。具体参数和离线导入流程见 [K3s 部署指南](llm-d/README.md)。

```bash
./llm-d/scripts/check-cluster.sh
./llm-d/scripts/setup.sh
./llm-d/scripts/deploy.sh small linux
./llm-d/scripts/status.sh
./llm-d/scripts/smoke.sh
```

`small` 使用 Qwen3-0.6B 验证双路由和 KV Cache 传输。Qwen3.5-9B-AWQ 的整请求部署使用 `./llm-d/scripts/deploy.sh 9b linux`。尝试大模型拆分前，参阅 [9B P/D 诊断](llm-d/pd/README.md#qwen35-9b-awq-诊断)。

## 跨 GPU 拆分与路由

Qwen3-0.6B 已验证 NVIDIA 和 AMD 可互换 Prefill/Decode 角色。Decode 端通过 NIXL 和 UCX TCP 拉取 KV Cache，两端使用 CPU buffer 暂存并启用默认握手检查。部署配置和验证方法见 [P/D 验证](llm-d/pd/README.md)。

[EPP-Opt](llm-d/epp-opt/README.md) 提供基于 TTFT 的选路，以及同时评估整请求与拆分路径的 E2E 策略。E2E 策略在原始落点为 AMD 时评估拆分，要求预测 E2E 更短且首 token 延迟满足 SLO。Qwen3.5-9B-AWQ 混合负载验证中，优化入口的 288 条请求均沿用整请求路径。

## 仓库目录

| 目录 | 内容 |
| --- | --- |
| [`llm-d/`](llm-d/) | K3s manifests、Router values、部署脚本与 EPP-Opt |
| [`benchmark/`](benchmark/) | 基准工具与已审核的实验报告 |
| [`docker/`](docker/) | 单机推理与容器环境 |
| [`docs/`](docs/) | 网络诊断和公开发布流程 |

本地凭据、节点专用配置、`benchmark/baseline/` 和各级 `runs/` 留在工作目录。发布步骤见 [公开发布](docs/publication.md)。
