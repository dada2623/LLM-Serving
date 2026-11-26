# vLLM 跨平台 P/D 生产验证

2026 年 11 月 23 日，AMD 与 NVIDIA 两个 Linux 节点使用 vLLM 0.33.0、NIXL connector 协议版本 15 和 `TRITON_ATTN`，在生产 Deployment 上通过了双向 Prefill/Decode（P/D）验证。两次传输均通过默认 NIXL 握手检查，Decode 通过 UCX TCP 从远端拉取 KV Cache，传输使用 CPU buffer。

## 9B 生产 Soft-RoCE

2026 年 11 月 26 日，现网 Qwen3.5-9B-AWQ 已切换到 `9b-rxe`。双向 P/D 通过默认 NIXL 握手，Decode 经 `rc_verbs/rxe_llm:1` 读取 KV。下文保留 Qwen3-0.6B 的 TCP 验证记录。[9B Soft-RoCE 生产配置](soft-roce/README.md)

## 生产实测结果

| KV Cache 方向（Prefill → Decode） | Prefill / Decode HTTP | NIXL 成功传输次数 | KV Cache 字节数 | NIXL 传输耗时 | Decode 使用的外部 KV tokens |
| --- | --- | ---: | ---: | ---: | ---: |
| NVIDIA → AMD | 200 / 200 | 1 | 3,670,016 | 15.028 ms | 31 |
| AMD → NVIDIA | 200 / 200 | 1 | 1,835,008 | 9.248 ms | 15 |

两端日志均记录 `NIXL compatibility check passed`
两个方向的失败传输、失败通知和过期 KV 请求计数增量均为 0。

验证时两个模型 Deployment 均为 `1/1 Ready`，EPP Pod 为 `2/2 Ready`。两个模型端点的 `/v1/models` 均返回 HTTP 200，Router 普通推理的 `smoke.sh` 通过。

## 生产配置

生产对象位于 `llm-d-serving` 命名空间，两个 Deployment 都使用 `Qwen3-0.6B`，单卡、TP=1、PP=1。每个节点提供一份 GPU 资源，Deployment 使用 `Recreate` 更新。部署参数见 [Linux 小模型 overlay](../manifests/modelserver/overlays/small/kustomization.yaml)。

| Deployment | K3s 节点 | GPU | 生产镜像 | 容器内运行时 |
| --- | --- | --- | --- | --- |
| `vllm-nvidia` | `nvidia-node` | RTX 5070 Ti | `docker.io/vllm/vllm-openai:v0.33.0` | vLLM `0.33.0`、PyTorch `2.14.0+cu130`、CUDA `13.0` |
| `vllm-amd` | `amd-node` | Radeon RX 7800 XT（gfx1101） | `docker.io/vllm/vllm-openai-rocm:v0.33.0` | vLLM `0.33.0+rocm100`、PyTorch `2.13.0+rocm10.0.0`、HIP `7.15.26333` |

| 配置项 | 生产设置 |
| --- | --- |
| Attention backend | 双端 `TRITON_ATTN` |
| KV connector | `NixlConnector`，协议版本 `15`，`kv_role=kv_both` |
| 数据传输 | `kv_buffer_device=cpu`，UCX backend，双端使用 `eth0` |
| `UCX_TLS` | NVIDIA 为 `tcp,self,cuda_copy`，AMD 为 `tcp,self,rocm_copy` |
| NIXL side channel | 当前 Pod IP，端口 `18080` |
| 兼容性检查 | 使用默认设置 |

AMD 通过 `hostPath` 挂载 `/opt/rocm` 和 `/srv/llm-serving/amd-runtime` 下的 vLLM、NIXL 1.5.0 ROCm、UCX 1.23.2 ROCm100 运行时。`NIXL_PLUGIN_DIR` 指向挂载的插件目录，`LD_PRELOAD` 加载同一套 UCX 的 `libucs.so.0`、`libucp.so.0` 和 `libuct.so.0`。具体路径见 [AMD Deployment 清单](../manifests/modelserver/base/amd-deployment.yaml)。

## P/D 测试方法

使用生产配置和默认 NIXL 握手检查，从能访问两个 Pod IP 的客户端直连模型 Pod 的 HTTP `8000` 端口。按 `NVIDIA Prefill → AMD Decode` 和 `AMD Prefill → NVIDIA Decode` 各测一次。

1. 确认两个 Deployment 均为 Ready，且各自的 `/v1/models` 返回 `Qwen3-0.6B`。获取本轮 Pod IP 和 NIXL engine ID。
2. 在每个方向发请求前，采集 Decode Pod 的 `/metrics` 作为基线。测试客户端需要 Prefill 节点名、Prefill URL、Decode 节点名、Decode URL、Decode engine ID 和本轮请求 ID。
3. 客户端先向 Prefill Pod 发送请求，获取包含远端 engine、Pod IP 和 KV block ID 的 `kv_transfer_params`。随后将这些参数和相同提示词交给 Decode Pod，Decode 以 `transfer_mode=pull` 拉取 KV Cache。
4. 确认两阶段 HTTP 均返回 200，并在 Decode 日志中检查握手通过。对照 Decode Pod 请求前后的指标，确认成功传输次数、传输字节数和使用的外部 KV tokens 均有正增量，失败传输、失败通知和过期 KV 请求计数增量均为 0。

## 证据与验证范围

生产配置升级、临时参数回收和部署操作记录保存在本地运维记录中。

本轮验证覆盖生产 Pod 之间的默认握手和 UCX TCP CPU-buffer KV Cache 传输。Router 的自动 P/D 调度、跨厂商 GPU buffer 直传和负载性能属于独立验证范围。

## Qwen3.5-9B-AWQ 诊断

在 vLLM 0.33.0 的混合缓存 CPU staging 实验中，Mamba 状态页的填充与 NIXL 注册区间不一致。两端应用同一份[诊断补丁](patches/vllm-v033-nixl-mamba-host-staging.patch)，按填充后的页大小分配 CPU buffer，并统一缓存布局后，固定 AMD Prefill → NVIDIA Decode 的直连与 EPP-Opt 冒烟均通过。标准 9B overlay 提供整请求部署。该补丁单独用于诊断验证。
