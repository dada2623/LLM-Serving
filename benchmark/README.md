# 本机模型测试

测试对象为 RTX 5070 Ti 上的 `QuantTrio/Qwen3.5-9B-AWQ`。两框架共用模型、tokenizer 和请求数据，分别使用各自的官方镜像栈。

P0–P4 共 5 项工作已完成。编号、完成标准和未测范围见 [PLAN.md](PLAN.md)。

## 找报告

| 要查的内容 | 文件 |
| --- | --- |
| P0：候选配置能否运行，采集是否准确 | [采集验收](reports/P0-REPORT.md) |
| P1：vLLM CPU 卸载为何拖慢 Decode | [卸载诊断](reports/OFFLOAD-ABLATION.md) |
| P2：两框架实际占用如何对齐 | [冻结配置与资源](p2/control/README.md)、[张量账本](p2/control/resource-alignment.json) |
| P3：启动、空闲和停服后的资源 | [基线报告](baseline/control/REPORT.md) |
| P4：正式并发、延迟、吞吐和 SLO | [正式报告](p2/control/REPORT.md) |

公开目录 `p2/control/` 同时包含 P2 校准和 P4 正式测试。任务编号以 PLAN 为准。报告与汇总已去除实际日期、绝对时间戳及主机信息，原始采集材料仅保留在本机；发布范围见[说明](../docs/publication.md)。

## 三批配置分开看

| 配置 | 用途 | 关键设置 |
| --- | --- | --- |
| 生产默认 `.env` | 日常启动、早期功能验收 | vLLM FP16/16K/0.60；SGLang BF16/32K/0.65；卸载预算均 5 GiB，并发上限 1 |
| P0 控制候选 | 验证共同参数与采集链路 | BF16/16K/0.65，卸载预算 5 GiB，并发上限 1 |
| P2 冻结 Control | P3 基线与 P4 正式统计 | BF16/16K、0 GiB 卸载、并发上限 16，按实际张量字节校准缓存池 |

P0 报告展示两组控制候选的 6 条小负载请求和 2 条预热。现有部署组保留原始归档，未纳入报告展示。P0 每个长度只请求一次，可用于核对 token 和计时，样本量不足以作性能排名。

P4 筛选完成 90 单元、2700 条成功请求；正式完成 90 单元、10098 条成功请求，错误均为 0。正式 6 个单元未达延迟 SLO。按 128/128、512/256、2048/256 三档负载，两框架三轮均达标的最大已测闭环并发都是 16/16/8。

生产默认配置尚无正式并发矩阵和三轮资源基线。SGLang 卸载消融、8192/128、混合长度、开环到达率和静态 batch 对照均未执行。

## 指标怎么读

| 指标 | 定义 |
| --- | --- |
| TTFT | 请求发出到首个非空内容 chunk |
| E2E | 请求发出到响应结束 |
| TPOT | 首末内容 chunk 的间隔结合最终 usage 估算的每输出 token 时间 |
| 输出吞吐 | 总输出 tokens / 第一条提交到最后一条结束的时间 |
| Goodput | 同时满足 TTFT、TPOT、E2E 阈值的成功请求数 / 完整测量窗口 |
| 已测闭环容量 | 三轮都达标的最大已测客户端并发 |

TPOT 不是逐 token ITL。正式报告从原始请求重算 P50/P90/P95，保留逐轮吞吐与标准差。SLO 使用测试前冻结的合成阈值，尚未对应生产需求。

## 数据放在哪里

下列原始采集目录描述复现实验生成的数据，未包含在公开版中。公开版保留报告引用的去标识统计表和图表。

- `runs/`：P0 和卸载诊断的逐请求、日志、资源采样及 manifest。
- `processed/p0-control-*`：P0 报告使用的候选组数据；`processed/p0-*` 原有全量文件保留归档。
- `p2/control/sessions/`：校准、筛选、正式测试和中断记录。
- `baseline/control/sessions/`：六次启动、24 个资源观察窗口与原生计数。
- 每批 `validation.json`、`restoration.json`：数据校验、生产文件哈希和退出恢复检查。

整卡 GPU 采样包含桌面占用，约 1 秒的间隔可能漏掉短峰值。实际参数和缓存池字节以张量账本为准。框架依赖版本、池结构、工作区与 WSL/WDDM 差异见各报告。

## 复现入口

以下命令会启动测试容器。执行前打开 Docker Desktop，停止本地模型客户端，避免外部请求混入原生计数。

```powershell
# P0 历史全量验收；报告仅展示 control 候选
.\.venv\Scripts\python.exe .\benchmark\scripts\prepare_p0.py
.\.venv\Scripts\python.exe .\benchmark\scripts\run_p0.py --service vllm --profile existing
.\.venv\Scripts\python.exe .\benchmark\scripts\run_p0.py --service sglang --profile existing
.\.venv\Scripts\python.exe .\benchmark\scripts\run_p0.py --service vllm --profile control
.\.venv\Scripts\python.exe .\benchmark\scripts\run_p0.py --service sglang --profile control
.\.venv\Scripts\python.exe .\benchmark\scripts\summarize_p0.py
.\.venv\Scripts\python.exe .\benchmark\scripts\validate_p0.py
# 恢复生产容器定义，保持停止
docker compose create --no-build --pull never vllm sglang
```

`validate_p0.py` 按历史四组配置验收，所以全量复现仍需运行 existing 组。只复核候选数据可读取 `p0-control-*` 文件，不必启动服务。

Control 续跑和断点处理见[批次说明](p2/control/README.md)。离线报告入口为 `summarize_p0.py`、`report_p2.py`、`report_baseline.py` 和 `report_baseline_details.py`。这些脚本会重写对应派生产物；阅读报告无需运行它们。

绘图库隔离放在 `.tools/benchmark-plot`；生产依赖和锁文件不包含绘图工具。
