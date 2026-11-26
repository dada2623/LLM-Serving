# AMD 主机的 vLLM

验证环境使用 RX 7800 XT 16 GB、Windows 11 和 WSL2 Ubuntu 24.04。

## 已安装

| 组件 | 版本或路径 |
| --- | --- |
| Windows 驱动 | Adrenalin 26.10.1 / 32.0.32015.7029 |
| 服务使用的 ROCm SDK / ROCDXG | 10.0.0 / 1.2.2 |
| vLLM 官方 ROCm wheel | `0.33.0+rocm100`（运行时版本 `0.33.0`） |
| PyTorch / Triton | `2.13.0+rocm10.0.0` / `3.8.0+git669b31ac` |
| Transformers | 5.20.2 |
| vLLM venv | `/srv/llm-serving/venvs/vllm-rocm100-v033` |
| Docker 镜像 | `llm-serving/vllm-wsl:0.33.0-rocm100` |

服务使用官方 ROCm 10.0 wheel，运行环境由 `runtime-env.sh` 配置。

系统 `/opt/rocm` 仍为 7.2.1，旧 venv 和镜像保留用于回退。服务从新 venv 加载 SDK 10.0 的 HIP/HSA/BLAS 库。SDK 自带的 ROCDXG 1.1.0 在此机误识别架构并报 `hipErrorInvalidImage`，因此从原系统复制已验证的 1.2.2 到 `/srv/llm-serving/rocm100-wsl-host-bridge`，并优先加载它。镜像需要挂载这台主机的 venv、桥接库和 WSL 库，不能直接复制到其他电脑使用。

已在 RX 7800 XT 上运行完整 `QuantTrio/Qwen3.5-9B-AWQ`，容器状态为 healthy。普通回答、SSE 流式、多轮对话和 API Key 鉴权已通过；控制电脑也已通过 SSH 隧道生成 192 个 token，并收到可解析的 `get_weather(city="北京")` 工具调用。工具调用测试只检查模型输出，没有执行天气查询。

模型来自控制电脑已有的权重，5 个分片的 SHA256 与 Hugging Face 修订 `938f8e3ef86c9d1e9bec3705e149694c172592f1` 全部匹配。18 个运行文件共 12,399,583,771 字节。容器内路径由 `start-vllm.sh` 的 `model` 指定，对应宿主的 `/srv/llm-serving/models`。

安装时还验证了宿主和容器的 FP16 矩阵乘法、编译版 RMSNorm、Triton AWQ 算子，以及缩小版 Qwen3.5 的引擎执行。完整权重的 HTTP 验证在这些检查之后完成。

## 从控制电脑访问

- OpenAI Base URL：`http://127.0.0.1:18000/v1`
- Claude Code / Anthropic Base URL：`http://127.0.0.1:18000`
- 模型名：`Qwen3.5-9B-AWQ`
- API Key：使用本地客户端环境中的 `OPENAI_API_KEY`。

以下地址使用本地 SSH 隧道端口 18000。

`127.0.0.1` 指向运行客户端的那台电脑。本机 SSH 进程接收 `127.0.0.1:18000` 的连接，再通过 SSH 转发至 AMD 主机的 `127.0.0.1:8000`，由 WSL/Docker 中的 vLLM 执行推理。其他电脑也需要建立自己的隧道，才能使用同样的 localhost 地址。

Claude Code 使用 `ANTHROPIC_AUTH_TOKEN` 填写同一密钥。新版已通过 Bearer 普通及 SSE 检查；只发送 `x-api-key` 仍返回 401。修改客户端配置后重新启动 Claude Code。

在客户端建立到 AMD 主机的端口转发：

```bash
ssh -N -L 127.0.0.1:18000:127.0.0.1:8000 "$AMD_SSH_TARGET"
```

`AMD_SSH_TARGET` 由执行环境指定。

## 已验证参数与显存

当前使用与 NVIDIA 端对齐的 FP16 + AWQ、16K（16384 token）上下文、单请求、prefill 分块 512、显存预算 0.80、CPU 卸载 0 GiB、eager、仅文本、Triton Attention。

当前 0.33.0/ROCm 10.0 启动日志记录：模型加载约 10.52 GiB，KV/状态缓存预算约 1.74 GiB，缓存池容量约 46565 token。缓存池容量不能直接当作单条请求的上限；服务按 `max-model-len=16384` 检查输入 token 与输出预留的总和。此前 32K 配置的验证证据仍保留在升级记录中。

初次部署的 8K 上限导致 Claude Code 在预留 4096 输出 token 后无法容纳提示词；此前曾临时验证 32K 配置。当前为了与 NVIDIA 无卸载压测配置保持可比，恢复为 16K；验证输入区间为 8K 到 12K。尚未进行 16K 边界压力、并发或吞吐基准测试。

## WSL 适配

这台主机有 7800 XT 和 780M 两个适配器。设置 `ROCR_VISIBLE_DEVICES` 只暴露独显，并开启 `HSA_ENABLE_DXG_DETECTION=1`。

`ROCPROFILER_REGISTER_ENABLED=0` 解决了新 PyTorch 在 WSL 中查询 profiler agent 时的初始化失败。

WSL 的 HIP 可用，但 AMDSMI 初始化失败。`wsl-rocm-plugin` 通过 vLLM 的平台插件入口，用 PyTorch 查询 GPU 名称、UUID 和设备数量。插件只允许显式启用的单 GPU WSL 环境；导入 ROCm 平台期间将日志按进程输出，避免回退提示触发循环导入，随后恢复原来的日志行为。GPU 算子使用安装包中的实现。

Compose 用 `VLLM_PLUGINS=llm_serving_wsl_rocm` 仅加载这个平台插件，避免无关 Quark 插件的导入报错。当前关闭 AITER，AWQ 使用 Triton 实现。

## 检查与启动

在 AMD 主机 Ubuntu 中，从本目录启动或查看日志：

```bash
docker compose -f compose.vllm.yaml up -d
docker compose -f compose.vllm.yaml logs --tail 100 -f
```

在宿主机的服务环境文件中设置 API 密钥。

当前重启策略为 `unless-stopped`；Docker 启动时会恢复未被手动停止的容器，Windows 和 WSL 仍需运行。缺少模型配置时入口脚本退出并说明路径，不会隐式下载模型。
