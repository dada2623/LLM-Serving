# EPP-Opt

EPP-Opt 在 llm-d Router 的 EPP 进程中运行 Go `ProfileHandler`。输入包括候选端点、估算的提示词长度、前缀命中和 vLLM 负载快照。没有足够的估算输入时，由普通 Router 的选点结果处理整请求。

- [TTFT 配置](epp-opt-plugins.yaml) 比较 NVIDIA 整请求与 AMD Prefill → NVIDIA Decode 的预测首 token 延迟。
- [E2E 实验配置](epp-opt-benefit-plugins.yaml) 以原 Router 的落点为基准，仅当原落点是 AMD、预测拆分 E2E 更短且预测 TTFT 满足 SLO 时切换到拆分路径。

EPP 选定 Prefill 与 Decode 后，`pd-sidecar` 将 KV 交接参数传给 NVIDIA 模型服务。Decode Pod 通过 NIXL 从 AMD Prefill Pod 拉取 KV。集群内入口是 `epp-opt.llm-d-serving.svc.cluster.local:80`。

## 构建与部署

准备 `upstream.rev` 指定的 llm-d-router checkout，在具有 Docker 的构建机上从本目录执行以下命令。

```bash
./build.sh /path/to/llm-d-router epp-opt:local
go run ./cmd/calibrate -data /path/to/calibration > profiles.json
```

校准目录包含本地采集的 `idle-calibration.json` 和 `queued-calibration.json`。可在命令末尾附上本地配对试验 JSON 作为补充。将镜像导入调度节点的 K3s containerd。先按 [部署指南](../README.md) 部署普通 Router，再执行下列命令。

```bash
workdir=$(mktemp -d)
kubectl -n llm-d-serving get deploy llm-serving-epp -o json > "$workdir/router.json"
kubectl -n llm-d-serving get cm llm-serving-envoy \
  -o 'jsonpath={.data.envoy\.yaml}' > "$workdir/envoy.yaml"
python3 render-deployment.py \
  --deployment "$workdir/router.json" --envoy "$workdir/envoy.yaml" \
  --profiles profiles.json --image epp-opt:local > "$workdir/epp-opt.json"
kubectl apply -f "$workdir/epp-opt.json"
kubectl -n llm-d-serving rollout status deploy/epp-opt
```

渲染器生成 TTFT 版本并挂载本地校准表。E2E 实验配置另需用目标模型的实测数据准备独立的延迟模型。
