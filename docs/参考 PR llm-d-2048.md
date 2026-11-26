# 参考 PR llm-d-2048

**PR #2048 把预测延迟路由接入已有的 Prefill/Decode 分离部署。** EPP 先选 Decode，再选 Prefill。Decode 以预测 TPOT 为主打分，Prefill 使用缓存亲和和预测 TTFT。两套压测策略共用原有的 vLLM、P/D routing proxy 和 NIXL。 [PR #2048](https://github.com/llm-d/llm-d/pull/2048) · [合并版 P/D 配置](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/router/predicted-latency-pd.values.yaml)

## 1. 改动范围与源码版本

部署配置在 `llm-d`，Go 插件和 P/D proxy 在 `llm-d-router`，Python 训练与预测服务在 `llm-d-latency-predictor`。 [PR 文件列表](https://github.com/llm-d/llm-d/pull/2048/files) · [合并提交](https://github.com/llm-d/llm-d/commit/97fb21ea01d0a88764216421a0e30349889c8244)

| 文件 | 作用 |
|---|---|
| `router/predicted-latency-pd.values.yaml` | 新增 P/D 策略，启用 predictor 容器，配置 Prefill 和 Decode 两个 profile |
| `router/predicted-latency.values.yaml` | 更新普通预测路由，缓存亲和过滤器改用预测 TTFT |
| `router/predicted-latency-slo.values.yaml` | 更新带 SLO 准入的预测路由，缓存亲和过滤器同样改用预测 TTFT |
| `benchmark-templates/guide.yaml` | 新增 Qwen3-32B 案例的压测模板 |
| `benchmark-results/pd_disaggregation_gpu_gptoss120b.png` | 新增 gpt-oss-120b P/D 压测结果图 |
| `README.md` | 补充 P/D 部署、压测命令、结果和冷启动说明 |

本文按合并时 `guides/env.sh` 指定的 **Router v0.9.0** 追踪 Go 实现。Python 源码用 **predictor v0.9.0** 作参照。Router Chart 默认引用训练和预测各自的 `*-dev:latest` 镜像，PR 没有给出压测镜像的 digest。 [Router 版本声明](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/env.sh#L8-L9) · [Router Chart 默认值](https://github.com/llm-d/llm-d-router/blob/v0.9.0/config/charts/routerlib/values.yaml#L158-L239) · [Python predictor](https://github.com/llm-d/llm-d-latency-predictor/tree/v0.9.0)

## 2. EPP 如何选择端点

`modelServers.matchLabels` 选用带 `llm-d.ai/guide=pd-disaggregation` 的模型服务。Helm values 嵌入一份 `EndpointPickerConfig`。实际运行时，`Director` 先对请求的候选端点运行 DataProducer，准备前缀匹配与延迟预测，随后才进入 Scheduler。角色过滤、亲和过滤、打分和终选按各 profile 执行。YAML 里的 profile 列表先写 Prefill，`disagg-profile-handler.Pick()` 实际先运行 Decode。 [P/D values](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/router/predicted-latency-pd.values.yaml) · [Director](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/requestcontrol/director.go#L296-L320) · [SchedulerProfile.Run](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/scheduling/scheduler_profile.go#L117-L127) · [Handler.Pick](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/profilehandler/disagg/disagg_profile_handler.go#L263-L340)

~~~mermaid
flowchart TD
    A["EPP 收到请求，生成缓存匹配和延迟预测"] --> B["Decode profile 选端点，TPOT 权重为 1"]
    B --> C["always-disagg 请求运行 Prefill profile"]
    C --> D["Prefill profile 选端点，缓存亲和与 TTFT"]
    D --> E["Decode 为主端点，Header 指定 Prefill"]
    E --> F["Decode Pod 的 routing-proxy 发起 Prefill 请求"]
    F --> G["proxy 将 KV 元数据交给本地 Decode vLLM"]
    G --> H["Decode 拉取远端 KV，生成并流式返回"]
~~~

| 阶段 | 插件 | 行为 |
|---|---|---|
| 准备输入 | `approx-prefix-cache-producer`、`predicted-latency-producer` | 生成前缀匹配和候选端点的延迟预测 |
| 选 Decode | `decode-filter` → `tpot-scorer` → `weighted-random-picker` | `ttftWeight: 0`，`tpotWeight: 1` |
| 判断是否运行 Prefill profile | `always-disagg-pd-decider` | Decode 成功选出之后，固定返回 `true` |
| 选 Prefill | `prefill-filter` → `loose-affinity-filter` → `ttft-scorer` → `weighted-random-picker` | `ttftWeight: 1`，`tpotWeight: 0`，前缀亲和仅用于 Prefill |
| 写回结果 | `disagg-profile-handler`、`disagg-headers-handler` | `ProcessResults()` 设定主端点。`PreRequest()` 写入 `x-prefiller-host-port` |

`latency-scorer` 使用预测延迟相对于 SLO 的余量。实现会先根据是否存在负余量分桶，某一维度无差异时还可重归一化权重。所有候选都没有预测时，scorer 回退到 KV 利用率、队列和前缀缓存组成的复合评分。终选按分数加权抽样，因此多个候选可用时，预测延迟最低的候选未必被选中。 [AlwaysDisaggPDDecider](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/profilehandler/disagg/always_disagg_pd_decider.go) · [LatencyScorer](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/scorer/latency/plugin.go#L153-L335) · [WeightedRandomPicker](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/picker/weightedrandom/picker.go#L109)

### 缓存亲和的 5 秒阈值

`loose-affinity-filter` 将前缀匹配块占比达到 `0.8` 的候选归为亲和组。没有亲和候选时保留全体。有亲和候选时通常保留亲和组。若两组都存在，`ttftSource: latencyPredictor` 让过滤器比较两组各自最低的预测 TTFT。**亲和组最低 TTFT − 非亲和组最低 TTFT > 5000 ms** 时，过滤器重新放开全体候选。比如两组最低预测分别为 6.2 s 和 0.8 s，差值为 5.4 s，非亲和候选重新参与打分。 [PrefixCacheAffinity.Filter](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/filter/prefixcacheaffinity/plugin.go#L165-L210) · [TTFT 数据来源](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/filter/prefixcacheaffinity/plugin.go#L240-L267)

`maxTTFTPenaltyMs: 5000` 是**优先复用缓存时容许的额外延迟**。本仓库的 `ttftSLOSeconds: 5` 约束**整个 P/D 路径的预测 TTFT**。上游另外提供 `predicted-latency-slo.values.yaml`，其中声明了 `latency-slo-admitter`。 [P/D 配置](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/router/predicted-latency-pd.values.yaml) · [SLO 配置](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/router/predicted-latency-slo.values.yaml) · [本地 EPP-Opt 配置](../llm-d/epp-opt/epp-opt-benefit-plugins.yaml)

## 3. 两种 sidecar 与 KV 交接

| 组件 | 位置 | 职责 |
|---|---|---|
| EPP | Router Pod | 决定 P/D 端点。把 Prefill 的 `IP:port` 写入 Header |
| `training-server`、`prediction-server-1` | EPP 所在 Pod 的两个容器 | 训练延迟模型和提供预测。Chart 默认通过 `localhost:8000` 访问训练服务，通过 `localhost:8001` 访问预测服务 |
| `routing-proxy` | Decode Pod | 在 `:8000` 接收请求，按 Header 协调远端 Prefill 和本地 `:8200` 的 Decode |
| vLLM `NixlConnector` | 两端 vLLM 进程 | 在标准 NIXLv2 路径中，由 Decode 远程读取 Prefill 的 KV |

**选址先选 Decode，推理先执行 Prefill。** Decode Pod 的 proxy 读到 Prefill 地址后，先发限量、非流式的 Prefill HTTP 请求，从返回体提取 `kv_transfer_params`。随后把参数交给本地 Decode 请求，并恢复原请求的输出预算与流式设置。HTTP 携带 prompt、控制字段及 KV 元数据。KV 数据在两个 vLLM 进程之间通过 NIXL 传输。 [Handler.ProcessResults](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/profilehandler/disagg/disagg_profile_handler.go#L342-L375) · [Header 写入](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/scheduling/profilehandler/disagg/disagg_headers_handler.go#L90-L136) · [Decode proxy 配置](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/recipes/modelserver/base/single-host/pd/vllm/patch-sidecar.yaml) · [读取 Header](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/sidecar/proxy/chat_completions.go#L61) · [NIXLv2 请求处理](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/sidecar/proxy/connector_nixlv2.go#L54)

## 4. 预测闭环和存储

1. **批量预测。** `Produce()` 收集输入长度、前缀匹配率、端点状态、在途 Prefill token 等特征，批量获取各候选端点的 TTFT 和 TPOT。结果以 `LatencyPredictionInfo` 供本次调度读取，同时放进 EPP 请求上下文供后续观测。 [Produce](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/requestcontrol/dataproducer/predictedlatency/dataproducer_hooks.go#L38) · [预测特征](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/requestcontrol/dataproducer/predictedlatency/prediction.go#L45)
2. **采集实测。** `PreRequest` 记录调度状态，`ResponseBody` 观察首输出与流式 token 间隔。P/D 的 TTFT 样本是 **EPP 收到请求到看到首输出**的时间，这个区间包括排队、Prefill、KV 交接与首输出。训练时将该值关联到所选 Prefill 的状态。TPOT 从 Decode 流式响应采样，配置中的 `streamingMode: true` 要求测试请求发送 `stream: true`。 [请求/响应钩子](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/requestcontrol/dataproducer/predictedlatency/requestcontrol_hooks.go#L42) · [P/D TTFT 样本归因](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/requestcontrol/dataproducer/predictedlatency/requestcontrol_hooks.go#L253-L294)
3. **训练并同步模型。** 训练服务把回归模型落到 `/models/ttft.joblib` 和 `/models/tpot.joblib` 等文件。预测服务通过训练端 HTTP 接口把模型同步到自己的 `/server_models/` 再加载。Chart 给两边挂独立的 `emptyDir`，替换 EPP Pod 会丢失模型文件。 [容器和卷模板](https://github.com/llm-d/llm-d-router/blob/v0.9.0/config/charts/routerlib/templates/_latency-predictor.tpl) · [模型路径](https://github.com/llm-d/llm-d-router/blob/v0.9.0/config/charts/routerlib/values.yaml#L185-L239) · [训练端写模型](https://github.com/llm-d/llm-d-latency-predictor/blob/v0.9.0/training/training_server.py#L1303) · [预测端下载与同步](https://github.com/llm-d/llm-d-latency-predictor/blob/v0.9.0/prediction/prediction_server.py#L155)
4. **查看预测结果。** 单次请求的候选预测在 EPP 内存中供调度使用。Prometheus 暴露统计分布，例如 `inference_objective_request_predicted_ttft_seconds` 和 `inference_objective_request_ttft_seconds`。容器里持久到磁盘的 `.joblib` 文件保存的是回归模型。 [预测指标实现](https://github.com/llm-d/llm-d-router/blob/v0.9.0/pkg/epp/framework/plugins/requestcontrol/dataproducer/predictedlatency/metrics.go) · [指南验证步骤](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/README.md#verify)

## 5. 压测设计与结果

两组共用 `openai/gpt-oss-120b` P/D 后端。16 张 H200 分给 8 个 Prefill 实例（每实例 TP=1）和 2 个 Decode 实例（每实例 TP=4）。GKE DRA/DRANet 上的 KV 传输使用 RDMA/RoCE。实验变量是 EPP 选址策略。 [P/D 压测报告](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/README.md#L384-L396)

| 项目 | 负载与前缀基线 | 预测延迟策略 |
|---|---|---|
| Prefill | `prefix-cache-scorer` 权重 3，`queue-scorer` 权重 2，`kv-cache-utilization-scorer` 权重 2 | 缓存亲和过滤，预测 TTFT 为主 |
| Decode | `active-request-scorer` 权重 2，`prefix-cache-scorer` 权重 3 | 预测 TPOT 为主 |
| P/D | 固定启用 | 固定启用 |

报告摘要强调基线 Decode 的活跃请求数。基线 YAML 还给 Decode 配了权重为 3 的 `prefix-cache-scorer`。 [基线 P/D values](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/pd-disaggregation/router/pd-disaggregation.values.yaml#L19-L42)

运行 `llmdbenchmark` 时指定 `--harness inference-perf --workload guide_pd-disaggregation_1.yaml`。该模板发送随机且不重复的流式 completion 请求，输入 5,000 token、输出 250 token，`ignore_eos: true`。固定到达速率从 10 扫到 45 req/s，每档 120 s。P/D 模板默认 45 req/s，可按档修改。PR 新增的 `benchmark-templates/guide.yaml` 对应 Qwen3-32B 案例。指南要求 harness v0.7.0 或更新版本，旧版多个 worker 会重复发送相同 prompt，导致虚高的前缀缓存命中。 [压测命令](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/README.md#L337-L358) · [P/D 模板](https://github.com/llm-d/llm-d-benchmark/blob/v0.7.0/workload/profiles/inference-perf/guide_pd-disaggregation_1.yaml.in) · [Qwen3-32B 模板](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/benchmark-templates/guide.yaml)

| 45 req/s 档 | 基线 | 预测路由 | 降幅 |
|---|---|---|---|
| TTFT P50 | 401 ms | 320 ms | 约 20% |
| TTFT P90 | 794 ms | 595 ms | 约 25% |
| TTFT P99 | 2.1 s | 812 ms | 约 62% |

上游报告显示，两组均在约 44.3 req/s 饱和，总 token 吞吐约 234K/s，TPOT 基本重合。低于约 30 req/s 时，两组差异在噪声内。逐档升压让预测模型进入高负载前已经预热，冷启动直接施加高负载会有额外代价。这些数字衡量的是**固定 P/D 拓扑下的选址效果**。 [结果与冷启动说明](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/README.md#L384-L396) · [原始结果图](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/benchmark-results/pd_disaggregation_gpu_gptoss120b.png)

## 6. 与本地 EPP-Opt 的接入点对照

| 比较项 | PR #2048 | 本地 EPP-Opt `20261125-benefit` |
|---|---|---|
| 决策 | 固定启用 P/D，选择各角色的实例 | 基线选中 AMD 时，比较 AMD 整请求和 AMD Prefill → NVIDIA Decode |
| 接入点 | 保留 `disagg-profile-handler`，配置 producer、filter、scorer 和 picker | `epp-opt-profile-handler.ProcessResults()` 读取基线结果和候选，决定最终路径及 Decode 落点 |
| 目标 | Prefill 以 TTFT 为主，Decode 以 TPOT 为主 | 预测 P/D E2E 严格低于 AMD 整请求，且 P/D TTFT ≤ 5 s |
| 校准 | 上游指南以同类候选为前提，已发布 P/D 数据来自 H200 | AMD 和 NVIDIA 用本地测量校准，P/D 路径估算需包含跨机 KV 交接 |

**可以把 EPP-Opt 作为与默认 Prefix Cache + Load 并列的可选策略。** 配置层并列两份 `EndpointPickerConfig`。代码层保留 `epp-opt-profile-handler`，由它组合主端点和可选的 Prefill 端点，再复用已有请求执行链路。`Picker` 接口处理单个 profile 的候选终选，`ProfileHandler` 可以结合多个 profile 的结果决定路径。`EPP-Opt` 可以作为项目名，对外策略名则可直接体现异构 P/D 决策。 [本地 Handler](../llm-d/epp-opt/handler/handler.go) · [本地配置](../llm-d/epp-opt/epp-opt-benefit-plugins.yaml) · [上游适用条件](https://github.com/llm-d/llm-d/blob/97fb21ea01d0a88764216421a0e30349889c8244/guides/predicted-latency-routing/README.md#when-to-pick-this-path)

本地每个角色只有一个可用端点时，角色内评分没有第二个候选可选，逐请求路径决策仍可生效。固定 P/D 对照测选址策略，混合路径对照测是否拆分。
