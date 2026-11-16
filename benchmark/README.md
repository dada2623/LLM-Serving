# Benchmark 技术记录

这个目录记录模型 serving 的测量口径、运行时组合、验证方法和证据组织。

## 研究范围

项目包含两条线：

1. vLLM/SGLang 单卡对照，关注 CPU 权重卸载、实际张量资源、并发尾延迟和资源生命周期。
2. NVIDIA RTX 5070 Ti 与 AMD RX 7800 XT 的异构双机 PD，关注 Prefill/Decode 角色切分、GPU 状态传输和混合负载下的取舍。

单卡对照的公开报告位于 `reports/`、`p2/control/` 和 `baseline/control/`。异构 PD 的计划、结果和图表位于 `pd/`。

## 异构 PD 的技术路径

### 服务角色

| 方案 | NVIDIA RTX 5070 Ti | AMD RX 7800 XT | 作用 |
| --- | --- | --- | --- |
| N | 完整推理 | 空闲 | NVIDIA 单卡基线 |
| A | 空闲 | 完整推理 | AMD 单卡基线 |
| NP-AD | Prefill | Decode | 已完成可行性验证的方向 |
| AP-ND | Decode | Prefill | 反向角色验证 |
| REPLICAS | 完整副本 | 完整副本 | 两卡独立副本对照 |

成功的 PD 路径使用 SGLang 的 `mooncake_tcp` 后端。NVIDIA 端负责 Prefill，AMD 端接收 Attention KV、Mamba/GDN 状态和辅助 metadata，再继续 Decode。两端通过可路由的测试网络互相访问；只转发固定服务端口无法覆盖 Mooncake 发布的动态端口。

关键配置为：

```text
--disaggregation-transfer-backend mooncake_tcp
MC_FORCE_TCP=1
MOONCAKE_PROTOCOL=tcp
--cpu-offload-gb 0
```

实验使用 TCP、主机网络缓冲、TUN/SSH 转发和 SSH 加密。没有把这条路径当作 RDMA 或 GPUDirect 结果。主机侧 TCP 缓冲属于网络路径，而非模型权重 CPU offload。

## 实验环境与软件栈

| 项目 | NVIDIA 端 | AMD 端 |
| --- | --- | --- |
| 执行环境 | 独立 Docker 容器 | WSL 内独立虚拟环境 |
| GPU | RTX 5070 Ti，16 GB | RX 7800 XT，16 GB |
| 框架 | SGLang 0.5.22 | SGLang 0.5.22 |
| SGLang 源码 | 同一固定 revision | 同一固定 revision |
| PyTorch | 2.14.1 + CUDA 13.0 | 2.13.0 + ROCm 10.0 |
| 模型 | QuantTrio/Qwen3.5-9B-AWQ | 同一 checkpoint |
| dtype / 量化 | FP16 / AWQ | FP16 / AWQ，Triton 路径 |
| context / token 池 | 8K / 8K（可行性批次） | 8K / 8K（可行性批次） |
| 静态显存比例 | 0.90 | 0.90 |
| 最大运行请求 / Mamba 槽 | 1 / 5 | 1 / 5 |
| chunked prefill | 256 | 256 |
| Attention / GDN 后端 | Triton | Triton |
| CUDA Graph / overlap / radix cache | 关闭 | 关闭 |
| CPU 权重卸载 | 0 GiB | 0 GiB |

PD 正式计划把 context 提升到 32K，并重新校准 token 池、状态槽、角色专用缓冲和峰值显存。可行性批次的 8K 设置不能直接当作正式容量结论。

AMD 侧不是现成的官方消费级显卡镜像。实验源码放开了 `gfx1101` 编译目标，构建 HIP AOT kernel，并验证 AWQ 反量化、GEMM 和 SiLU。AITER 路径没有作为本次结果的前提；Triton 用于 AMD 的 AWQ 和算子路径。

## 做过的适配和诊断

### 归一化接口适配

首个推理请求暴露出 SGLang 调用侧和已安装 vLLM 依赖之间的 RMSNorm 参数签名差异。实验补丁在运行时检查签名，选择 SGLang 原生归一化回退。生产 vLLM 安装包没有修改。

### 传输和缓存校验

传输探针记录发送字节数和返回码。缓存校验探针在发送前和接收后读取选定 GPU 区域，在 CPU 上计算 Adler-32；独立 buffer 探针使用 SHA-256。探针与 CPU 参考值一致，修改一个字节可以被发现。校验会增加复制和同步开销，因此只用于验收，不用于性能计时。

## 已完成的可行性验证

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| Mooncake GPU buffer 双向写入 | 4/4 通过 | 1 MiB、16 MiB 两档，两个方向，接收端 SHA-256 一致 |
| AMD 单机模型 | 通过 | 普通 Chat 和 SSE 均能完成，保留短生成和长生成基线 |
| 短请求 P/D | 两轮均 3/3 通过 | HTTP 200，24-token 输出与 AMD 单机 token ID 一致 |
| 分块 Prefill | 完成 | 613-token 输入按 256/256/101 分块，完成 128-token 输出 |
| 混合状态传输 | 已观测 | 每次 48 个 Mamba/GDN 状态块，共 51,511,296 字节，返回码为 0 |
| P/D Chat SSE | 通过 | 返回内容、`finish_reason=stop` 和 `[DONE]` |
| 长请求缓存校验 | 2/2 通过 | 长请求和多轮消息在 Decode 前的 64 个 GPU 区域一致 |
| 多轮消息 Chat SSE | 通过 | `user / assistant / user` 三条历史消息正常返回 |

这些结果验证了请求链路和状态传输。它们没有测量吞吐、容量或稳定性。

## 输出差异的解释边界

613-token 请求的 PD 输出前 110 个 token 与 AMD 单机基线一致，从第 111 个 token 开始分歧。启用校验后，长请求覆盖 64 个区域、约 71,598,080 字节；多轮请求覆盖 64 个区域、约 53,149,696 字节。发送端与接收端校验值一致，且没有触发接收校验失败。

Adler-32 不是强校验，整份缓存没有做逐字节导出比较；独立 buffer 探针只覆盖选定区域并使用 SHA-256。当前证据排除了本轮观测范围内的缓存搬运损坏，但没有在浮点算子、运行时和归一化实现之间定位剩余差异，也没有证明 CUDA 与 ROCm 逐项语义一致。

## 单卡 benchmark 的测量口径

| 指标 | 定义 |
| --- | --- |
| TTFT | 请求发出到首个非空内容 chunk |
| TPOT | 首末内容 chunk 的间隔结合最终 usage 估算的每输出 token 时间 |
| E2E | 请求发出到响应结束 |
| 输出吞吐 | 总输出 token 除以第一条提交到最后一条结束的完整窗口 |
| Goodput | 同时满足 TTFT、TPOT、E2E 阈值的成功请求数除以完整测量窗口 |
| 已测闭环容量 | 三轮都满足合成 SLO 的最大已测客户端并发 |

TPOT 不是逐 token ITL。正式报告从请求级数据重算 P50/P90/P95，同时保留逐轮吞吐、标准差、SLO 达标率和 Goodput。整卡 GPU 采样包含桌面和驱动占用，短时峰值可能被采样间隔遗漏；实际参数和缓存池字节以启动张量账本为准。

## PD 计划的负载与验收

PD 计划使用最终模板化后的完整输入 token，覆盖：

- S：128/512，观察短请求 Decode 和输出吞吐。
- L：14,336/256，隔离长 Prefill、TTFT 和传输等待。
- C：14,336/1024，模拟编码助手长请求。
- M-code：80% C 加 20% 2048/256 中等请求。
- M-interference：80% 128/512 短请求加 20% C，观察长 Prefill 对短请求尾延迟的影响。

验收要求包括反向角色、长输入固定答案、多轮消息、SSE、KV/GDN 状态、并发隔离、模型和 tokenizer 一致性，以及两方向的 GPU 传输校验。性能比较前关闭诊断 checksum 和逐传输 trace；若出现状态损坏、错误答案或请求串扰，停止该批次。

完整计划见 [pd/PLAN.md](pd/PLAN.md)，当前结果见 [pd/REPORT.md](pd/REPORT.md)。

## 发布边界

发布版保留报告、汇总 JSON/CSV、图表、验证脚本和脱敏部署适配。原始请求 SSE、逐传输 trace、压缩轨迹、运行日志、进程锁、重试中间结果、实验源码副本、个人配置、IP、日期记录和本地路径不进入 GitHub。
