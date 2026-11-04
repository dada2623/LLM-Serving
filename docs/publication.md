# 公开发布

公开版包含源码、部署配置模板、去标识的实验报告和统计数据。实际日期、时刻、时间戳、主机标识及认证信息已移除；实验耗时、延迟、吞吐、采样间隔、随机种子与协议版本仍保留。

`.env`、模型权重、本地工具、缓存、原始日志、主机快照及旧批次目录由 `.gitignore` 排除。原始材料仍在本机，包含私人配置或运行时间，不属于公开版。公开报告入口为 `benchmark/p2/control/` 和 `benchmark/baseline/control/`。原始 run 标识已替换为匿名编号，公开版不能据此定位本机原始目录。

公开统计中的校验和恢复结果描述原实验。公开数据已去掉绝对计时字段和私人配置指纹，不能代替原始证据重新执行全部验收。复现实验需自行准备 `.env`、模型及 Python/Docker 环境，再采集数据。报告生成脚本依赖原始采样；新采集或重新生成报告后，应再次执行发布检查。

新实验使用 `benchmark/p2/local/` 和 `benchmark/baseline/local/`。启动脚本默认使用这些本地目录，基线脚本从本地 Control 批次读取冻结配置；公开的 `control/` 汇总目录只用于阅读结果。

## 准备本地配置

复制 `.env.example` 为 `.env`，设置自己的随机 `API_KEY`，按需填写 `HF_TOKEN` 和模型路径。模板中的路径相对项目根目录。

## 检查与导出

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\check_publication.py
.\.venv\Scripts\python.exe .\scripts\check_publication.py --output .\outputs\LLM-Serving-public.zip
```

检查器检查 Git 跟踪文件与未忽略的新文件，发现密钥、私人路径或文档时间记录时退出失败；输出只显示文件位置和类别。它还会与本机 `.env` 中的认证值比对，避免把同一个密钥误复制到其他文件。导出只包含通过检查的文件，并使用固定压缩包时间字段。

可解压公开包后上传其内容，或在当前项目使用普通 `git add .` 提交。不要强制添加被忽略的本地文件，也不要将整个工作目录直接打包上传。
