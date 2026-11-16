# LLM-Serving：异构双机 PD 推理研究

这个项目记录我围绕 LLM serving 做的两类工作：先用 vLLM 和 SGLang 建立单卡基线，定位 CPU 权重卸载、显存池和并发尾延迟问题；再把同一模型拆成跨机 Prefill/Decode（PD）服务，验证 NVIDIA RTX 5070 Ti 与 AMD RX 7800 XT 能否通过 SGLang/Mooncake TCP 协同完成推理。

我把每个结论绑定到输入、配置、请求统计、资源采样和校验结果。报告保留失败边界，因此读者可以区分“请求完成”“缓存传输正确”和“性能值得采用”这三个问题。

## 我做了什么

### 建立可比较的单卡基线

- 用相同的模型、tokenizer、模板和合成请求比较 vLLM 与 SGLang。
- 在启动阶段按 storage 指针去重统计参数、缓冲区和缓存池，避免用显存比例代替实际资源。
- 把校准、预热、筛选、正式测试和资源生命周期测量分开，重算请求级 P50/P90/P95、吞吐、Goodput 和 SLO 达标率。
- 通过 CPU 卸载消融、源码检查和拷贝探针定位 Decode 变慢的原因。

### 打通异构双机 PD 路径

- 为 AMD RX 7800 XT 准备 WSL/ROCm 运行环境，补充 `gfx1101` 编译目标和 Triton/AWQ 路径。
- 采用 SGLang 的 Mooncake TCP 后端，让 NVIDIA 端执行 Prefill，AMD 端接收 Attention KV 与 Mamba/GDN 状态并继续 Decode。
- 处理动态端口可达、bootstrap 角色切换、SSE 请求、分块 Prefill、多轮消息和状态槽对应关系。
- 增加发送字节、返回码和 GPU 缓存校验探针，并把诊断校验与正式计时分开。

### 设计可复核的 PD 比较

| 比较对象 | NVIDIA RTX 5070 Ti | AMD RX 7800 XT | 用来回答的问题 |
| --- | --- | --- | --- |
| N：NVIDIA 单卡 | 完整推理 | 空闲 | NVIDIA 单卡能达到什么水平 |
| A：AMD 单卡 | 空闲 | 完整推理 | AMD 单卡能达到什么水平 |
| NP-AD：PD 方向一 | Prefill | Decode | NVIDIA Prefill 是否适合这条链路 |
| AP-ND：PD 方向二 | Decode | Prefill | 交换角色后是否更合适 |
| REPLICAS：双副本 | 完整副本 | 完整副本 | 两卡独立服务是否比 PD 更划算 |

| 负载 | 输入 / 输出 token | 观察重点 |
| --- | ---: | --- |
| S：短请求 | 128 / 512 | Decode、输出吞吐、短请求尾延迟 |
| L：长输入隔离 | 14,336 / 256 | TTFT、Prefill 时间、状态传输等待 |
| C：编码助手 | 14,336 / 1,024 | TTFT、TPOT、E2E、Goodput 和排队 |
| M-code：主混合负载 | 80% C + 20% 2,048 / 256 | 长上下文请求占多数时，PD 是否改善整体服务 |
| M-interference：干扰负载 | 80% S + 20% C | 少量长 Prefill 是否拖慢短请求 |

| 比较阶段 | 必须通过的检查 | 失败时的处理 |
| --- | --- | --- |
| 部署与模型 | 权重、tokenizer、模板、源码和补丁一致 | 不进入性能比较 |
| 请求正确性 | 输出、SSE、分块 Prefill、多轮消息 | 保留失败证据，停止该方向 |
| 状态完整性 | Attention KV、Mamba/GDN 状态槽和 GPU 校验一致 | 不把结果解释为缓存传输正确 |
| 并发隔离 | 并发 1/2/4 下请求与状态不串线 | 不运行正式矩阵 |
| 性能测量 | 报告 TTFT、TPOT、E2E、吞吐、Goodput、SLO 和资源峰值 | PD 输给单卡也保留并解释瓶颈 |

## 已得到的结论

### 跨机 PD 的可行性边界

- SGLang/Mooncake TCP 已完成跨机 GPU buffer 传输和 Qwen3.5 Prefill/Decode 请求。
- 双向 GPU buffer 写入校验 4/4 通过；接收端读回的 SHA-256 与发送端一致。
- 短请求两轮均为 3/3 通过，HTTP 状态、24-token 输出和输出 token ID 与 AMD 单卡基线一致。
- 分块 Prefill、P/D Chat SSE 和三条消息的多轮请求均完成；状态传输包含 Attention KV、Mamba/GDN 状态和辅助 metadata。
- 长请求和多轮请求在 Decode 前的 GPU 区域校验通过。长请求覆盖 64 个区域、约 68.3 MiB；多轮请求覆盖 64 个区域、约 50.7 MiB。

这些结果证明链路和状态搬运可以工作。它们不证明 PD 已经带来吞吐收益。

### 存在输出差异

613-token 输入的 PD 输出前 110 个 token 与 AMD 单卡基线一致，从第 111 个 token 开始出现分歧。启用缓存校验后，发送端和接收端仍得到相同校验值，说明本轮没有发现缓存搬运损坏。
跨 CUDA/ROCm 的算子、运行时和归一化实现仍可能造成计算差异；当前证据不足以把差异归因于某一个原因，也不足以宣称两种硬件逐项语义一致。

### 单卡基线的性能结论

- 在现有 Control 配置和三个最大达标工作点，SGLang 输出吞吐比 vLLM 高约 22.3%–38.4%，E2E P95 更低；部分工作点 vLLM 的 TTFT P95 更低。
- 两框架在 128/128、512/256、2048/256 负载下，三轮均达标的最大已测闭环并发为 16、16、8。
- 请求全部成功不等于满足延迟 SLO。最长负载的并发 16 仍未达标。
- CPU 卸载诊断中，vLLM Decode 中位数从 3.53 tok/s 变为 28.75 tok/s。源码、日志和拷贝探针支持逐层 CPU→GPU 权重搬运是主要开销来源之一；这组实验同时调整了显存预算，不能把全部变化归因于单一开关。
- 逻辑 KV/SSM 占用归零后，预分配池仍可能驻留约 1.5 GiB。逻辑占用、预分配池、PyTorch allocator 和整卡显存必须分开解释。

### PD 目前还没有性能结论

当前筛选完成 69/76 个单元，正式测试完成 3/最多 78 个单元。方向交换、长输入容量、混合负载和反向 PD 仍需要完整矩阵。现阶段可以确认部署可行性和链路代价，不能宣称 PD 优于单卡或两卡副本。

## 证据入口

- [技术细节与运行时配置](benchmark/README.md)
- [异构 PD 研究计划](benchmark/pd/PLAN.md)
- [PD 结果报告](benchmark/pd/REPORT.md)
- [PD 研究入口](benchmark/pd/README.md)
- [校准与正式矩阵证据](benchmark/pd/evidence/)
- [传输与混合负载图表](benchmark/pd/figures/)
- [AMD WSL 部署适配说明](docker/amd-wsl/README.md)
- [单卡采集验收](benchmark/reports/P0-REPORT.md)
- [CPU 卸载诊断](benchmark/reports/OFFLOAD-ABLATION.md)
- [正式并发报告](benchmark/p2/control/REPORT.md)

原始实验目录、逐请求 SSE、压缩轨迹、运行日志、进程锁、个人配置和本地身份信息不属于发布版。发布版只保留能支撑结论的报告、汇总数据、图表、验证脚本和脱敏部署证据。
