# 跨主机传输路径诊断摘要

这份文档只保留可公开发布的测速结论，不包含主机地址、SSH 别名、用户名、原始环境快照或逐次运行目录。速度单位为 MiB/s，payload 为 512 MiB，所有纳入摘要的测试均完成接收端 SHA-256 校验。

## 已观察结果

| 测试路径 | NVIDIA 到 AMD | AMD 到 NVIDIA | 结果 |
| --- | ---: | ---: | --- |
| GPU buffer，Mooncake TCP，TUN 和 SSH carrier | 69.49 | 12.03 | 100/100 校验通过 |
| CPU buffer，Mooncake TCP，同一路径 | 78.90 | 11.21 | 100/100 校验通过 |
| raw TCP，同一 TUN 路径 | 98.23 | 12.83 | 30/30 校验通过 |
| 普通 host IP，保留 WSL/Docker 转发 | 95.60 | 260.35 | 18/18 校验通过 |
| Windows 原生 TCP | 270.792 | 258.894 | 18/18 校验通过 |

## 结论边界

同一个方向性差异在 GPU buffer、CPU buffer 和 raw TCP 上都出现时，优先检查承载路径。普通 host IP 转发和 Windows 原生 TCP 的方向性又不同，说明 WSL、Docker、TUN、SSH carrier 或组合封装会改变瓶颈位置。

`NP-AD` 和 `AP-ND` 分别代表 NVIDIA Prefill 到 AMD Decode、AMD Prefill 到 NVIDIA Decode。跨节点 K3s 部署需要从实际 Pod 或服务命名空间同时测量两个方向，不能用单方向 host TCP 结果替代 Pod-to-Pod 结果。

## 复测要求

- 先做普通 TCP 控制，再做 Mooncake CPU buffer，最后做 Mooncake GPU buffer。
- 每个方向都记录 payload、重复次数、发送与接收端 SHA-256、连接路径和失败类型。
- 把 K3s API、Flannel VXLAN、kubelet、Windows 防火墙、WSL 地址映射、Pod CIDR、MTU 和可能的 WireGuard 通道作为独立检查项。
- 将传输正确性、服务可用性和性能收益分别报告。
