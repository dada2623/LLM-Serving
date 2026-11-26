# Qwen3.5-9B-AWQ 四路径校准压测设计

用 inference-perf 重放同一批请求，分别实测 AMD 整请求、NVIDIA 整请求和两个跨厂商 P/D 方向。每条完成请求产出一条带真实路径、调度特征、首 token 时间（TTFT）和完成时间（E2E）的校准样本，供 EPP-Opt 按路径训练 TTFT 和 TPOT。用每条路径的实测值和独立验收请求检查预测能否覆盖预期的输入、输出、缓存和负载范围。

## 目标环境与固定路径

本轮以现网 `Qwen3.5-9B-AWQ`、`runtime=9b-v033-rxe` 为目标。AMD 与 NVIDIA 各一份 vLLM 0.33.0 实例，当前 `max-model-len=16384`、`max-num-seqs=8`、`max-num-batched-tokens=1152`。P/D 经 NIXL 从 Prefill 端拉取 KV，数据走 CPU buffer 和 Soft-RoCE。开跑时记录两端实际镜像、模型、推理参数、KV 布局、EPP 和预测服务版本，并冻结这次配置。 [Soft-RoCE 配置](../../llm-d/pd/soft-roce/README.md)

| 路径 ID | Prefill | Decode | 固定执行方式 |
| --- | --- | --- | --- |
| `amd` | AMD | AMD | EPP-Opt 选 AMD，Envoy 直发该 vLLM |
| `nvidia` | NVIDIA | NVIDIA | EPP-Opt 选 NVIDIA，Envoy 直发该 vLLM |
| `ap-nd` | AMD | NVIDIA | EPP-Opt 选两端，现有 P/D 代理协调 |
| `np-ad` | NVIDIA | AMD | EPP-Opt 选两端，现有 P/D 代理协调 |

**固定校准由同一个 EPP-Opt Deployment 提供。** 在部署配置中按需开启一个集群内校准 Service，四个端口分别对应表中的四条路径，指向同一 Pod 中的 Envoy。Envoy 按监听端口给 EPP 写入固定路径信息。校准端口只允许授权的压测客户端访问，路径取自服务端的端口映射。普通入口 `epp-opt:80` 运行自动选路。四臂共用同一模型池，按波串行执行。 [EPP-Opt 候选与执行链](../../llm-d/epp-opt/README.md#路径执行资格)

仓库提供四份标准 inference-perf 配置模板与 DOE 生成器。正式波的配置把请求发到压测 Pod 内的 HTTP relay，由 relay 按该臂选定的校准 Service 端口转发。EPP-Opt 根据端口确定执行路径。请求 ID 用于关联计时、日志与实际执行证据。 [配置和脚本](inference-perf/)

固定路径模式在同一个 Handler 内冻结特征，从合格端点中选择目标，并复用 Envoy、P/D 代理和流式回包观测。目标端点不可用时记录校准失败。该模式可采集预测库冷启动时自动选路尚未覆盖的方向。校准观测先保存到本轮文件，验收后导入预测库。装载校准数据后，关闭校准入口，在 `epp-opt:80` 重放独立流量，从 EPP 日志核对预测 RPC 开销和实际去向。校准端口、Handler 分支、压测配置和导出脚本已实现，集群实测按以下预检与矩阵执行。

## 运行和导出

1. 按 [EPP-Opt 部署说明](../../llm-d/epp-opt/README.md#开启四路固定校准) 开启校准，并使用启用两个 P/D 方向的真实运行配置。对授权 Pod 验证四个端口，对无标签 Pod 验证校准端口受限。对普通入口带入任意校准头，确认继续走自动选路。短流式预检核对四臂执行地址和 NIXL 证据后，再扩大负载。
2. 将生成的压测目录放入授权的压测 Pod。先运行 `preflight`，再按阶段运行正式矩阵。脚本逐臂调用标准 inference-perf CLI，控制放行时刻，并记录响应中的 Envoy 请求 ID、usage、背景 SSE 与端点指标。正式波的两种到达形状都使用逐请求 relay 放行时刻。客户端原始计时包含在 relay 内等候的时间，报告客户端发出后的时延时使用 relay 转发、首 token 和结束时间戳。
3. 开跑前记下 UTC 起始时间。每波保存 EPP 副本日志，汇总前按波次和请求 ID 标记目标样本，避免把预热、背景和预检混入目标波。容器日志中的 `EPP_OPT_CALIBRATION` 行包含 Envoy `x-request-id`、EPP 内部 ID、两个端点和完整观测。四路只导出完成且带有效 usage 的样本，P/D 样本另外要求代理回报的 Prefill 和 Decode 地址与调度结果一致。

```bash
kubectl -n llm-d-serving logs -l app.kubernetes.io/name=epp-opt -c epp \
  --since-time="$RUN_START_UTC" --timestamps --tail=-1 > "$RUN_DIR/epp.log"

python3 benchmark/pd/export-calibration.py \
  --model Qwen3.5-9B-AWQ --runtime 9b-v033-rxe --require-all-routes \
  --request-ids "$RUN_DIR/accepted-request-ids.txt" "$RUN_DIR/epp.log" \
  > "$RUN_DIR/calibration-seed.jsonl"
```

`accepted-request-ids.txt` 每行填一个经核验的校准样本 Envoy 或 EPP 请求 ID。独立验收波使用单独的 ID 清单和 JSONL。保留源日志用于审阅两个端点和单请求证据。将导出的校准 JSONL 用 [预测 Pod 挂载参数](../../llm-d/epp-opt/README.md#构建与部署命令) 导入，关闭校准入口后另跑自动选路验收。

### 生成 inference-perf 文件

生成的 `manifest.json` 保存在本地运行目录，列出 66 个正式区组和 4 个预检区组。每个区组包含四臂 YAML、共用的 ShareGPT 语料、预热或背景请求以及每条请求的计划偏移。预热使用当前两个后端的 576-token KV 对齐粒度。背景流使用每端 4 条 `512/2048`，并在预检检查重叠窗口。需要调整背景长度时，用 `--background-input` 和 `--background-output` 重新生成一个目录。

```bash
# 在工作区根目录，TOKENIZER_DIR 指向与两端部署一致的模型 tokenizer。
python3 benchmark/pd/inference-perf/prepare-doe.py \
  --tokenizer "$TOKENIZER_DIR" --output benchmark/pd/runs/my-doe-run

# 在 client-pod.yaml 定义的授权 Pod 内。两个 METRICS 变量指向真实 vLLM 端点。
python /work/my-doe-run/run-doe.py /work/my-doe-run --phase preflight \
  --amd-metrics "$AMD_METRICS" --nvidia-metrics "$NVIDIA_METRICS"
```

预检通过后按 `core → arrival → warm → single-busy → dual-busy → holdout` 执行。使用 `--wave` 和 `--route` 复跑指定臂。已完成的臂可加 `--rerun` 重采，上一轮结果归档到该臂的 `attempts/`。每臂结果含 `arm.json`、inference-perf 逐请求报告和候选 `target-request-ids.txt`。导出种子前按 EPP 完成日志、到达时刻和 NIXL 证据筛选。Pod 使用临时工作卷，运行产物通过 `kubectl cp` 取回压测目录。

## inference-perf 与请求轨迹

复用历史压测已验证的 **inference-perf v0.7.0** 和同一镜像 digest，版本、配置和 tokenizer 哈希写入 `manifest.json`。沿用 ShareGPT 格式的预生成语料、Completion 流式接口、并发模式和逐请求报告。执行前核对实际请求体包含 `stream: true`、`stream_options.include_usage: true`、`ignore_eos: true` 以及当条的 `max_tokens`。使用部署模型的 tokenizer 验证输入长度，用服务端 usage 核对实际输入、输出 token。请求超时统一设为 240 秒，重试设为 0。标准 v0.7.0 Completion 请求使用服务端温度默认值，四臂冻结相同的生成配置。

- 以同一工况的四臂作为一个配对区组，共享 prompt、输出预算、请求次序、到达时间和背景流轨迹。为每条请求生成全局唯一的请求 ID，用共同的配对 ID 关联四臂。每阶段用固定种子安排区组次序并轮换四臂顺序，同一条件重复测量时反转四臂顺序。阶段前预初始化客户端和语料，复用外部门控对齐发出时刻。保存计划与实际到达时间、客户端在途峰值和分批偏差。
- 单臂每波发出 `C` 条目标请求，主扫描使用 **1、2、4、8、16、24、32** 七档并发。默认在 `t=0` 成簇发出。均匀分批仅在冷前缀的 `C=8、16` 增测，分别覆盖单端配置的运行容量和可能出现排队的并发区间。分批在 `t=0、1、2、3 s` 等量发出，各自构成完整的四臂配对。以实际 `running`、`waiting` 确认端点负载。
- 每臂前等待上一臂结束，并观测两端请求队列回落。使用按轮次与路径隔离的缓存盐，校准请求的文本和前缀结构保持配对。冷档在客户端构造互不共享的前缀。热档用同盐前导整请求建立缓存，两档分别只在 AMD 和只在 NVIDIA 预热，便于区分 P/D 的 Prefill 命中与 Decode 命中。前导请求通过同一 EPP-Opt 固定到预热端点，前导与目标波各自记录。先核对 EPP 估算与 vLLM 实际命中的块粒度，再选择接近输入 3/4 的可实现前缀长度。热档以实测命中和端点快照为准。

## DOE 负载矩阵

保留路由收益相关的输入长度、输出预算、缓存落点和两端负载。主扫描覆盖七档并发，到达形状只在两个代表档做配对对照。热前缀分别在 AMD 和 NVIDIA 建立，单端忙分别压 AMD 和 NVIDIA。两端都忙只保留在 `2048/2048`，用于检查长 Decode 在两端都受干扰时的选路。

| 阶段 | 输入 / 输出预算 | 目标并发 | 工况和波数 | 四臂区组 | 每路径目标请求 |
| --- | --- | --- | --- | ---: | ---: |
| 核心扫描 | 512/128、2048/256、2048/2048、4096/512 | 1、2、4、8、16、24、32 | 冷前缀、无背景流，各一波成簇 | 28 | 348 |
| 到达形状对照 | 同核心扫描 | 8、16 | 冷前缀、无背景流，各增一波均匀分批 | 8 | 96 |
| 热前缀 | 2048/256、2048/2048、4096/512 | 1、8、16 | 只热 AMD、只热 NVIDIA，各一波成簇 | 18 | 150 |
| 单端负载 | 2048/2048、4096/512 | 4 | 冷前缀、成簇。AMD 忙、NVIDIA 忙各两波 | 8 | 32 |
| 双端负载 | 2048/2048 | 4 | 冷前缀、成簇。两端都忙，两波 | 2 | 8 |
| **校准小计** | | | | **64** | **634** |
| 独立验收 | 2048/256、3072/1024 | 4 | 分别为仅 AMD 热、冷前缀且 NVIDIA 忙。各一波成簇，使用独立语料种子 | 2 | 8 |
| **合计** | | | | **66** | **642** |

四路径共 **2568 条计划计时请求**，其中 **2536 条用于校准采集，32 条用于独立验收**。低并发用于采样与配对比较，P95 同时标注样本量。若独立验收发现关键工况缺少预测或路径排序不稳，按实际缺失的缓存、运行或排队特征增测对应的四臂区组，并用新一波验收数据复核。分阶段保留可续跑的 manifest，按预检耗时估算维护窗口。

### 背景 Decode 与目标波同步

1. 每臂先启动 inference-perf，加载 tokenizer、语料和连接配置。等本波全部 4 条目标请求到达发压门控后，再经 EPP-Opt 的整请求校准端口放行背景流。AMD 忙和 NVIDIA 忙各在指定端点放行 4 条长输出请求。两端都忙时各放行 4 条。
2. 压测端按请求 ID 跟踪背景 SSE。指定端点的 4 条背景流都已收到第一个生成 token 且仍在生成时，立即放行目标波。两端都忙时要求两端同时满足。记录 vLLM 的 running 和 waiting，作为目标到达时的负载证据。首 token 和流结束事件用于确定背景 Decode 的重叠窗口。
3. 预检用相同的背景语料和输出预算测两端的重叠窗口。暂以放行到最后一条目标请求实际发出不超过 100 ms 为验收线，并核对最后一条目标请求到达 EPP 时，所需的背景流仍在 Decode。记录背景流的开始与结束、目标波放行和实发、EPP 到达时刻及两端调度快照，计算实际重叠。若背景流提前结束、等待超时或发压超出验收线，保留原始记录并重跑该臂，失配样本从此负载档的校准种子中排除。

四臂使用相同的背景请求和输出预算，并以同一个 Decode 条件对齐目标波。客户端并发档描述在途上限，运行和等待计数来自端点调度快照。 [EPP-Opt 特征读取](../../llm-d/epp-opt/handler/handler.go#L276)

## 路径验收与样本生成

1. **预检。** 四臂各做一次短流式请求，再跑一组 `2048/256 C=1` 和 `2048/2048 C=8` 的完整配对。两条整请求按 EPP 选点与实际后端请求 ID 核对。两条 P/D 按代理执行标记核对 Prefill、Decode 地址，再查 Decode 侧 NIXL 成功传输、字节数、外部 KV tokens 和失败计数，以及 UCX 的 `rc_verbs/rxe_llm:1` 日志。并发波的聚合计数用于交叉核对，逐请求标签以执行证据为准。 [P/D 验收方法](../../llm-d/pd/soft-roce/README.md#验收与回滚) · [代理执行标记](../../llm-d/epp-opt/sidecar/epp_opt.go#L42)
2. **冻结预测特征。** 按 EPP 决策时刻记录实际输入 token、请求输出预算，以及实际路径两端各自的 EPP 估算 `cached_tokens` 和指标快照 `running`、`waiting`。快照新鲜度沿用当前配置的 10 秒。整请求的两阶段字段复制同一端点快照。目标并发、计划前缀长度和请求完成后的指标用于解释结果。 [部署参数](../../llm-d/epp-opt/epp-opt-benefit-plugins.yaml) · [样本结构](../../llm-d/epp-opt/model/e2e.go)
3. **采集标签。** EPP 的 TTFT 从收到请求到首个生成 delta，E2E 从收到请求到流正常结束。保存实际生成 token 数，计算 `TPOT = (E2E − TTFT) / (实际生成 token 数 − 1)`。只生成一个 token 时样本仅提供 TTFT 标签。成功且路径、特征、usage 均可验证的请求导出为 `model.Observation` JSONL，时间单位为毫秒。超时、缺失字段、路径不明和输出长度偏离均在报告注明，未达到标签验收的记录保留原始证据。 [EPP 回包观测](../../llm-d/epp-opt/handler/prediction.go#L68)
4. **性能报告。** 对四臂按输入、输出、并发和缓存状态报告完成率、TTFT/E2E/TPOT P50/P95、输出 token/s、请求/s，并报告 P/D 的 KV 字节、传输时延与失败。用配对请求分析 Prefill 或 KV 交接增加的 TTFT 能否由 Decode 节省的时间抵消。记录 inference-perf 客户端时延和 EPP 计时之间的差值。用固定路径实测值分别对照 AMD 和 NVIDIA 整请求，筛选 E2E 更低且 TTFT 增量至多 5000 ms 的场景，并标明各臂实际端点负载。 [四路径比较代码](../../llm-d/epp-opt/model/e2e.go#L97)

## 预测模型验收与产物

- 每条样本按 `model=Qwen3.5-9B-AWQ`、`runtime=9b-v033-rxe`、`route` 分组。导出前检查样本 schema、ID 唯一性和四组的输入、预算、冷热缓存、双端负载覆盖。每组至少要有 3 条训练和 2 条内置验证样本。当前模型按请求 ID 划分内置验证，并要求预测特征落在该路径的训练覆盖范围内。用矩阵中的独立验收波核对插值误差和路径排序。 [训练与特征边界](../../llm-d/epp-opt/predictor/regression.go)
- 预测器状态 PVC 当前为 **1 GiB**。代码在该卷保存最近 **8192 条在线观测**和模型快照。挂载的校准种子与在线观测合并去重后，每个模型、运行配置、路径分组最多取 **8192 条训练样本和 256 条内置验证样本**。压测预算按工况覆盖和执行耗时安排。 [状态持久化与训练](../../llm-d/epp-opt/predictor/service.go)
- 校准采集中通过验收的样本生成 `calibration-seed.jsonl`，挂载到预测 Pod 供 `--calibration` 冷启动。根据导出文件的实际字节数选择挂载方式，超过渲染器的 1 MiB ConfigMap 阈值时改用只读卷。导入前先备份现有预测状态，用独立预测实例调用 `/v1/predict` 检查四组覆盖、模型版本、TTFT/TPOT/E2E 误差及缺失原因。启用预测后，用独立混合流量核验实际自动改判、TTFT 和 goodput。 [校准输入](../../llm-d/epp-opt/README.md)
- 执行记录保存到本目录的 `runs/<run-id>/`。每轮包含冻结 manifest、原始 inference-perf 配置和逐请求报告、特征快照、路径证据、校准 JSONL、覆盖与预测误差表及 `REPORT.md`。
