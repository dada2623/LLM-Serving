# P4 Control 批次：正式并发测试

本批次保存 P4 的筛选与正式并发测试产物，正式结果见 [`REPORT.md`](REPORT.md)。P4 使用 [`P2 冻结配置`](../../p2/control/config.json) 和 [`资源账本`](../../p2/control/resource-alignment.json)。

公开版保留去标识的报告、配置和汇总表。sessions、锁文件及日志属于复现实验生成的本地数据，未随公开版发布；重新测量前需准备本地环境与模型。

## 工作量与统计

负载为 128/128、512/256、2048/256 输入/输出 tokens。筛选和正式各包含 2 框架 × 3 负载 × 5 并发 × 3 轮，即各 90 单元。

筛选每单元至少 30 条成功请求；正式每单元至少 100 条且至少 60 秒。最终分别成功 2700、10098 条，请求错误均为 0。校准、预热和中断单元不计入完成数。每单元开始前另发同长度、同并发的一波 8-token 预热，再读取原生计数基线。

脚本使用 `ignore_eos=true` 和 `max_tokens` 固定输出，逐条核对 usage 与 `finish_reason=length`。汇总 P50/P90/P95 时合并原始请求；吞吐窗口从第一条提交到最后一条响应结束，包含收尾时间。TPOT 结合内容 chunk 时间与最终 usage 估算，不能当逐 token ITL。

## SLO

测试前冻结以下合成阈值，尚未按生产需求验收：

| 项目 | 阈值 |
| --- | --- |
| TTFT | 输入 128/512/2048/8192 分别不超过 2/3/5/10 秒 |
| TPOT | 不超过 100 ms |
| E2E | 不超过对应 TTFT 阈值 + 输出目标 × 100 ms |
| 错误率 | 不超过 1% |
| 延迟达标请求比例 | 不低于 95% |

Goodput 为同时满足三项延迟阈值的成功请求数除以完整窗口。容量指三轮均达标的最大已测闭环并发。8192/128、混合长度、开环到达率和静态 batch 尚未执行。

## 进度和断点

[`execution.json`](execution.json) 保存执行状态；`RUNNING.lock` 保存活跃进程 PID。判断运行与否时须核对 PID 对应进程，遗留锁或 `running` 字段可能来自异常退出。

每条完成请求写入 `sessions/<run>/<cell>/request-*.json`。完成单元写入 [`cell-results.csv`](cell-results.csv) 和汇总文件，续跑会跳过；中断单元保留证据后重测。`runner-*.stdout.log` 显示当前单元。

需要在单元边界停止时，在本批次目录创建空文件 `STOP_AFTER_CELL`。脚本完成当前单元后生成报告并恢复生产容器定义。续跑前移除停止标记，确认旧 PID 已退出；存在遗留锁时先核实 Docker 和进程状态，再处理锁。

公开汇总已移除绝对计时字段和本机配置指纹，不能直接续跑原实验。复测应先在 `benchmark/p4/local/` 准备新批次，启动脚本可显式指定该目录：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmark\scripts\start_p2.ps1 -RunDirectory benchmark\p4\local
```

该脚本会准备 `%TEMP%\llm-p2-python`。有其他测试使用该 Python 时不要重复启动，以免覆盖活跃运行时。

## 退出检查

正常完成或失败退出时，脚本生成报告和校验文件，再恢复生产容器定义并保持停止。[`restoration.json`](restoration.json) 记录恢复命令退出码、`.env`/Compose 哈希和容器状态。强制终止可能跳过清理，需按这些证据补查。

