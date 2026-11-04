# P0：采集验收


本报告展示 2 组控制候选。每组启动一次，发出一条预热和三条小负载请求，用于核对模型、token、计时与资源采样。现有部署组保留历史归档。

## 配置与结果

两边统一 BF16、16K、显存比例 0.65、单请求、MLP 卸载预算上限 5 GiB、prefill 分块 512，关闭 CUDA Graph。5 GiB 是预算，实际卸载量以各 run 日志为准。

| 框架 / 配置 | 结果 | 成功请求 | 到 ready / s | KV token 池 | 整卡采样峰值 / MiB |
| --- | --- | --- | --- | --- | --- |
| vllm / control | passed | 3/3 | 144.2 | 57775 | 14741.0 |
| sglang / control | passed | 3/3 | 275.0 | 16384 | 13817.0 |

共同参数通过了启动与请求检查。两框架的显存比例语义、实际卸载字节、KV/状态池和依赖栈仍不同；P0 尚未完成资源校准。每个长度只有一条请求，样本不足以估计尾延迟或作框架排名。

后续 P2 将 CPU 卸载改为 0 GiB、服务端并发上限改为 16，并按实际张量字节校准缓存池。P3/P4 使用[冻结配置](../p2/control/config.json)，与本报告分批统计。

## 模型与运行环境

显卡为 RTX 5070 Ti，16303 MiB；系统为 Windows / WSL 2 / Docker Desktop。模型使用同一 AWQ 4bit、group_size=128 checkpoint，部分模块未量化。19 个文件的 SHA256 与本地清单一致，清单 revision 为 `2438fefd72e07f4378f0e6c567b1db9e6d6e1e37`；本次未查询远端 revision。

| 框架 | 框架版本 | PyTorch | CUDA 构建 | Transformers | Tokenizers | FlashInfer | Triton |
| --- | --- | --- | --- | --- | --- | --- | --- |
| vllm | 0.32.0 | 2.13.0+cu130 | 13.0 | 5.19.0 | 0.23.3 | 0.7.0.post1 | 3.7.1 |
| sglang | 0.5.22 | 2.14.1+cu130 | 13.0 | 5.19.0 | 0.23.2 | 0.7.1 | 3.8.0 |

vLLM 使用 AutoAWQMarlinLinearMethod / MarlinLinearKernel，SGLang 使用 awq_marlin。两镜像对 128/512/2048 输入生成的完整 token IDs 一致。

请求直连本机 OpenAI 流式 API，固定 seed=20261030、temperature=0、top_p=1、输出上限 32，关闭 thinking。测试未下载模型、修改生产 `.env` 或使用私人聊天数据；SGLang 在测试 override 中启用 metrics。

## 逐请求结果

| 框架 / 配置 | 输入 tokens | 输出 tokens | TTFT / ms | E2E / ms |
| --- | --- | --- | --- | --- |
| vllm / control | 128 | 32 | 565.5 | 9026.0 |
| vllm / control | 512 | 32 | 655.6 | 9306.1 |
| vllm / control | 2048 | 32 | 2668.3 | 11397.5 |
| sglang / control | 128 | 32 | 946.8 | 4996.4 |
| sglang / control | 512 | 32 | 271.6 | 4319.6 |
| sglang / control | 2048 | 32 | 899.0 | 4943.8 |

## 计时与计数

脚本记录发送、首个非空内容、最后内容及响应结束的单调时钟和 UTC。role、空 delta 与心跳不计作首 token，TTFB、TTFT、E2E 分开记录。TPOT 结合内容 chunk 时间和服务端 usage 估算；一个 chunk 可能包含多个 token，无法还原精确逐 token ITL。

输出计数优先用流式最终 usage。脚本另存可见文本的重编码数量，EOS/特殊 token 可能造成差异，重编码值不覆盖 usage。固定种子和聊天模板开头可能带来缓存复用，各 run 保留原生缓存计数。

脚本按发送量核对原生请求、输入和输出计数。SGLang 的内部 health/warmup 可能增加 prefill 计数，业务对账使用 num_requests/prompt_tokens/generation_tokens。缺失指标记为 unavailable。

SGLang 的 mamba_*_tokens 指标实际计状态槽；mamba_usage 是不可回收槽位占比。vLLM 的 kv_cache_usage_perc=1 表示 100%。单位和 HELP 见 `../processed/p0-metrics-catalog.csv`，全量指标审计保留在 `../processed/p0-metrics-deltas.csv`。

## 资源采样与限制

GPU、Windows CPU/RAM 目标间隔 1 秒，WSL/cgroup 和 Docker stats 为 2 秒；文件记录实际间隔与查询耗时。容器时钟按主机查询前后时间界限核对，延迟使用主机单调时钟。

GPU 显存为整卡采样，包含桌面和其他进程，短峰值可能落在采样间隔之间。WSL/WDDM 无法提供的进程显存记为 N/A；实际池结构需分别看 KV 与线性状态。无服务和预热空闲段各约 5 秒，后续 P3 另做 60 秒基线。

ready 包含容器重建、权重读取及引擎预热。测试未清空操作系统文件缓存，首次请求也可能包含内核编译。P0 没有 profiler，瓶颈诊断见后续 P1。

## 数据入口

- [配置 CSV](../processed/p0-control-runs.csv)、[逐请求 CSV](../processed/p0-control-requests.csv)、[阶段资源 CSV](../processed/p0-control-resources.csv)。
- [候选组 JSON](../processed/p0-control-summary.json)、[资源曲线](../figures/p0-control-resources.png)。

原始 run：

- `run-003-vllm-p0-control-c17a9fed-r01`：`../runs/run-003-vllm-p0-control-c17a9fed-r01/`。
- `run-004-sglang-p0-control-ee0ce0f8-r01`：`../runs/run-004-sglang-p0-control-ee0ce0f8-r01/`。

## 验收与恢复

候选通过 2/2，本报告含 6 条测试请求和 2 条预热。历史全量验收为 **passed**，其范围还包含未展示的现有部署组；原生计数、时间、采样与容器时钟检查见 `../processed/p0-validation.json`。历史检查中 API Key 明文残留文件数为 0。

当次恢复检查为 **passed**：生产文件未变，两套容器恢复原参数并保持停止。实时状态请查询 Docker。

当前阶段编号与报告入口见 [PLAN](../PLAN.md)。后续阶段重新验证并冻结参数，P0 小样本不并入正式矩阵。
