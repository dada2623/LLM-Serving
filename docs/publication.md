# 公开发布

公开仓库包含源码、可移植的部署模板，以及经过审核的实验报告。保留实际部署使用的配置在 `.env`、`.private/` 或 `*.local.yaml`。示例文件使用通用值。节点地址、SSH 别名、用户名和凭据由执行环境提供。

`benchmark/baseline/`、所有层级的 `runs/` 以及原始归档留在本地。已发布的阶段报告可从 [benchmark 目录](../benchmark/README.md) 查阅。复现实验需要自行准备模型、运行环境并重新采集。

按文件暂存并核对发布变更。

```bash
git add -- path/to/reviewed-file
git diff --cached --check
git diff --cached
git commit -m "Describe the change"
git push
```

发布检查器可在已准备好公开内容的检出目录中检查候选文件和导出 ZIP。

```bash
python3 scripts/check_publication.py
python3 scripts/check_publication.py --output outputs/LLM-Serving-public.zip
```

检查器会报告命中的文件和行号。导出使用固定的 ZIP 时间字段。
