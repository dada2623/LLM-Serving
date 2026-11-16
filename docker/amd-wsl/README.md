# 异构 PD 的 AMD 端部署证据

本目录记录 AMD RX 7800 XT 在 Windows + WSL2 环境中运行 vLLM 的部署适配，用于双机 Prefill/Decode（PD）实验。发布版不包含个人凭据、远端地址、SSH 密钥或本地路径。

## 已验证的部署链

- 使用 ROCm 运行时、vLLM ROCm wheel、PyTorch 和 Triton，模型为 QuantTrio/Qwen3.5-9B-AWQ。
- 通过 WSL ROCm 平台适配器，在 AMDSMI 不可用时改用 PyTorch/HIP 获取 GPU 名称、UUID 和设备数量。
- Compose 以单卡可见设备运行，关闭不适用的 AITER 路径，AWQ 使用 Triton。
- 验证普通生成、SSE、多轮输入、鉴权、长输入和模型文件一致性；跨机 PD 的正确性与性能按 benchmark/pd/PLAN.md 单独验收。
- 传输校准记录双向 GPU buffer 的成功率、有效带宽和校验结果；传输成功本身不等于 PD 性能收益。

## 证据入口

- WSL ROCm 平台适配：wsl-rocm-plugin/llm_serving_wsl_rocm/platform.py
- 容器构建文件：Dockerfile.vllm-wsl
- Compose 配置：compose.vllm.yaml
- 运行时环境：runtime-env.sh
- 服务入口：start-vllm.sh

模型目录、缓存目录、API 密钥和远端主机通过部署环境注入。
