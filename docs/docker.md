# 启动与配置模型服务

本项目用 Docker Desktop 的 WSL 2 后端运行两个服务。模型为 `QuantTrio/Qwen3.5-9B-AWQ`，本地目录为 `./models\Qwen3.5-9B-AWQ`；共享缓存位于 `./.cache/huggingface`。下载脚本按 SHA256 校验文件。

## 启动、切换和检查

在 `<project-root>` 打开 PowerShell：

```powershell
.\start.cmd                         # 默认 vLLM
.\start.cmd -Service sglang         # 先停止 vLLM，再启动 SGLang
.\stop.cmd

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\containers.ps1 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\containers.ps1 -Action logs -Service vllm
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\containers.ps1 -Action verify -Service sglang
```

单卡一次运行一个模型服务。启动包括容器初始化、权重加载、引擎预热和健康检查；`Waiting` 的秒数会持续增加。用日志判断进度，出现 traceback、OOM 或退出再处理故障。

修改 `.env` 后重新执行对应服务的启动命令，让 Compose 重建容器并读取新值。`docker restart` 不会更新容器定义中的环境变量和命令参数。模型、缓存和镜像层可复用。

## API 地址

| 服务 | 官方基础镜像 | OpenAI Base URL | Anthropic Base URL |
| --- | --- | --- | --- |
| vLLM | `docker.io/vllm/vllm-openai:v0.32.0` | `http://127.0.0.1:8000/v1` | `http://127.0.0.1:8000` |
| SGLang | `docker.io/lmsysorg/sglang:v0.5.22` | `http://127.0.0.1:30000/v1` | `http://127.0.0.1:30000` |

模型名为 `Qwen3.5-9B-AWQ`，密钥读取 `.env` 的 `API_KEY`。端口绑定 `127.0.0.1`。

PowerShell 对话示例：

```powershell
$apiKey = ((Get-Content .env | Where-Object { $_ -match '^API_KEY=' }) -split '=', 2)[1]
$body = @{
    model = 'Qwen3.5-9B-AWQ'
    messages = @(@{role = 'user'; content = '用中文介绍一下你自己。'})
    max_tokens = 128
    chat_template_kwargs = @{enable_thinking = $false}
} | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/v1/chat/completions' `
    -Method Post -Headers @{Authorization = "Bearer $apiKey"} `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

## CC Switch 接入 Claude Code

在 Claude Code 页面添加自定义供应商，填写：

| 字段 | 值 |
| --- | --- |
| 名称 | 本地 Qwen |
| Base URL | 使用上表的 Anthropic Base URL |
| 上游格式 | `Anthropic Messages` |
| 认证变量 | `ANTHROPIC_AUTH_TOKEN` |
| API Key | 项目 `.env` 中的 `API_KEY` |
| 主模型、Opus、Sonnet、Haiku、子代理模型 | 均填写 `Qwen3.5-9B-AWQ` |

保存并启用，使用直连模式。Claude Code 请求 `/v1/messages`，Base URL 填服务根地址。切换框架时同步修改端口。

输入 token 与输出预算之和必须小于服务上下文。Claude Code 的工具定义、系统提示词和聊天历史都会占用输入。服务报超长时，先核对错误中的输入数和输出预留；客户端变量是否生效，要以实际发送的请求为准。

## `.env` 默认配置

| 参数 | vLLM | SGLang |
| --- | --- | --- |
| 计算精度 | FP16 | BF16 |
| 上下文 | 16384 | 32768 |
| 显存比例 | 0.60 | 0.65 |
| CPU 卸载预算上限 | 5 GiB，仅 MLP | 5 GiB，仅 MLP |
| 服务端并发上限 | 1 | 1 |
| Prefill 分块 | 512 | 512 |
| CUDA Graph | 关闭 | 关闭 |
| 缓存池限制 | 引擎按预算分配混合池 | 32768 tokens、5 个线性状态槽 |

两边使用同一 AWQ checkpoint，部署文本对话，不加载视觉编码器。FP16/BF16 是计算精度，不改变 checkpoint 的 AWQ 4bit 格式。

这些参数来自早期本机部署调试。vLLM 设为 0.60，目的是给桌面应用留显存；部署记录写明，SGLang 在 0.60 下无法容纳当时的默认状态槽，因此提高到 0.65。
记录没有给出这两个比例的精确推导或最优性验证。SGLang 后来将槽数设为 5，以便容纳 32K KV 池；此前 12 槽时只有 22912 tokens。

显存比例按整卡容量计算，两框架的预算范围不同。剩余比例不能保证桌面可用显存。CPU 卸载量也是上限，实际驻留要看日志或张量账本。
vLLM 的卸载诊断测得 5 GiB 预算实际卸载 2.55 GiB，Decode 中位数为 3.53 tok/s；关闭卸载并提高显存预算后为 28.75 tok/s。方法和限制见[报告](../benchmark/reports/OFFLOAD-ABLATION.md)。

基准测试另用 0 GiB 卸载的 Control 配置，详见[测试计划](../benchmark/PLAN.md)。
默认 `.env` 保留原部署设置。

## 镜像兼容处理

vLLM 通过 `--cpu-offload-params mlp` 卸载 MLP，保留预热内核访问的 GDN 参数在 GPU。

SGLang v0.5.22 需要以下处理：

- 使用 BF16，避免 GDN 分块预填充的状态缓存与 FP16 结果类型冲突。
- 通过 `--json-model-override-args '{"language_model_only":true}'` 启用 Qwen3.5 文本模式。
- 用 `docker/sglang/patch_offload.py` 将官方 V1 卸载器限制到 MLP，避免归一化参数的 CPU/GPU 设备冲突。环境变量为 `SGLANG_CPU_OFFLOAD_MLP_ONLY=1`。

补丁会检查源码是否匹配。升级基础镜像后，若构建失败，需复核补丁。两框架使用 `qwen3_coder` 工具解析器、`qwen3` 推理解析器，默认关闭 thinking。