# 异构双机 PD 压测

本目录记录 NVIDIA RTX 5070 Ti 与 AMD RX 7800 XT 之间的 Prefill/Decode（PD）研究。核心目标是把部署验收、模型一致性、并发隔离、长输入正确性、双向 GPU 传输和性能测量串成可复核证据链，再判断 PD、单卡和双副本部署的取舍。

## 研究对象

| 代号 | NVIDIA 端 | AMD 端 | 作用 |
| --- | --- | --- | --- |
| N | 完整推理 | 空闲 | NVIDIA 单卡基线 |
| A | 空闲 | 完整推理 | AMD 单卡基线 |
| NP-AD | Prefill | Decode | 一种 PD 方向 |
| AP-ND | Decode | Prefill | 交换方向 |
| REPLICAS | 完整副本 | 完整副本 | 两卡副本对照 |

## 研究方法

- 五种方案使用同一模型、tokenizer、模板和脱敏输入；先做长输入与固定输出正确性验收，再做性能矩阵。
- 冻结 AWQ/FP16、32K context、CPU 权重卸载 0、chunked prefill 256、最大运行请求 4、Mamba 槽 8，并记录实际 token 池、状态槽、权重、缓存和峰值显存。
- 传输测量覆盖 1/16/64/128/512 MiB，双向重复，报告有效带宽、完成时间、错误和校验结果。
- 负载包含短请求、长输入隔离、编码助手长请求，以及 M-code/M-interference 混合轨迹；分别报告 TTFT、TPOT、E2E、吞吐、goodput 和类别 P95。
- 闭环并发与开环到达率分开解释；未完成或失败单元保留为证据，不填零、不外推。

## 当前结果快照

结果快照显示：四种部署的并发隔离、长输入与固定输出验收通过，GPU 传输校准 100/100 次通过；筛选完成 69/76 个单元，正式测试完成 3/最多 78 个单元。性能矩阵仍在进行，项目不宣称 PD 已经优于单卡。

双向 512 MiB 传输中位结果：

| 方向 | 有效带宽 | 传输时间 |
| --- | ---: | ---: |
| NVIDIA → AMD | 107.16 MiB/s | 4.778 s |
| AMD → NVIDIA | 12.84 MiB/s | 39.875 s |

链路存在明显方向不对称；长输入的状态量会成为 PD 必须单独核算的成本项。

## 目录

- 研究计划：PLAN.md
- 结果快照：REPORT.md
- 校准与矩阵证据：evidence/
- 图表：figures/
- 测量与验证脚本：run.py、measure.py、report.py、network_measure.py、validate.py 等

原始运行目录、逐请求 SSE、压缩轨迹、命令输出、SSH/TUN 日志、进程 PID 和中间重试结果不在发布版中。
