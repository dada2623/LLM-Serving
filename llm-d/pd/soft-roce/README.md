# 9B 生产 Soft-RoCE

生产配置 `9b-rxe` 更新现有两个 9B P/D Deployment 的网络、UCX 环境与设备配额。每端运行一个 vLLM，Envoy/EPP 和 EPP-Opt 通过现有 InferencePool 访问模型端点。

## 数据路径

- 节点服务 `llm-serving-rxe.service` 在各自的 2.5 GbE 网卡上创建 `rxe_llm`，核验 IPv4 RoCE v2 GID index 1 和 `/dev/infiniband/uverbs0`。vLLM 使用 `hostNetwork` 和 `ClusterFirstWithHostNet`，每个节点占用一个模型端口 8000 和一个 NIXL 控制端口 18080。
- `device-plugin.yaml` 在两个已标记的节点分配 `llm-d.ai/soft-roce`。每个 vLLM Pod 申请一份设备和 `IPC_LOCK`。AMD 使用启用 verbs 的 UCX 1.23.0，NVIDIA 的 NIXL CUDA wheel 通过独立库目录加载镜像自带的 verbs 实现。两端使用默认 NIXL 兼容检查。
- Decode 经 `rc_verbs/rxe_llm:1` 从 Prefill 节点读 KV。数据走 Prefill GPU → CPU buffer → Ethernet → Decode CPU buffer → GPU。NIXL 元数据用 TCP 18080。
- UFW 在物理网卡允许对端访问 UDP 4791、TCP 18080 和 TCP 8000。TCP 8000 也允许集群 Pod 从 CNI 接口访问本机地址。Flannel 将发往另一台节点物理地址的 Pod 流量伪装为本节点 IP，所以跨节点 EPP 到 vLLM 也需要对端 TCP 8000 规则。

## 初次安装

1. 在两个节点准备现有 `Qwen3.5-9B-AWQ` P/D Deployment。它需要 CPU NIXL、DS 状态布局、挂载 `nixl-9b-diagnostic` 和 `Recreate` 更新策略。`deploy.sh 9b` 应用整请求 overlay。为现有 9B P/D 切换传输时使用 `9b-rxe`。
2. 运行 `llm-d/pd/soft-roce/build-device-plugin.sh /tmp/generic-device-plugin` 生成已锁定上游版本的二进制。在 AMD vLLM 0.33.0 构建容器中准备 UCX 1.23.0 和 rdma-core 50 开发包，运行 `build-amd-ucx.sh <ucx-source> <rdma-core-usr>`。在现有 AMD TCP UCX 的同级 hostPath 目录安装生成的 `ucx-rxe-rocm100`。
3. 把本目录和设备插件二进制传到两个节点。以 root 分别运行 `install-node.sh <amd|nvidia> <nic> <local-ip> <peer-ip> <pod-cidr> <binary>`。核对 `systemctl is-active llm-serving-rxe` 和 `rdma link show rxe_llm/1`。
4. 用 `kubectl label node <amd-node> <nvidia-node> llm-d.ai/soft-roce=true` 标记节点，再应用 [设备插件清单](device-plugin.yaml)。核对 DaemonSet Ready 和两个节点的 `llm-d.ai/soft-roce` 可分配量各为 1。
5. 在维护窗口先 dry run，再逐端切换。可通过 `LLMD_KUBECTL_SSH_HOST` 指定 K3s 控制面 SSH 别名。脚本保留两端前后模板，出现 rollout 失败时按逆序恢复本轮改动。更新和恢复前都会检查模板，并发修改会使补丁停止。

```bash
llm-d/pd/soft-roce/deploy.sh rxe --dry-run
llm-d/scripts/deploy.sh 9b-rxe linux
```

## 验收与回滚

核对两端 `/v1/models` 和 Envoy/EPP 的普通生成。在集群内执行 `check-pd.py <prefill-url> <decode-url> <decode-engine-id>`，两个方向各运行一次。Decode 端应有成功传输、传输字节和外部 KV tokens 的增量，失败计数保持不变。日志中应看到默认 NIXL 握手通过及 `ucp_get` 使用 `rc_verbs/rxe_llm:1`。验收用的 UCX 协议日志由 `UCX_PROTO_INFO=y` 打开。

```bash
llm-d/pd/soft-roce/deploy.sh tcp --dry-run
llm-d/pd/soft-roce/deploy.sh tcp
```

完整卸载时先确认两端已回到 TCP 并正常推理，再删除设备插件 DaemonSet 和节点标签。用 `node-firewall.sh remove` 撤回对应 UFW 规则，在两节点停用 `llm-serving-rxe.service`。
