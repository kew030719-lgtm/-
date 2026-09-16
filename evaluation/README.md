# 真实评测集

只在获得本人同意后放入脱敏简历。简历文件和真实岗位页面不得提交到 Git；仓库只保留清单格式和评测工具。

复制 `manifest.example.json` 为仓库外的清单，登记至少 10 份简历及 Top 5 人工判定。三个站点各完成至少 50 个真实岗位快照后运行：

```bash
python scripts/generate_evaluation_report.py --manifest /path/to/manifest.json
```

报告会生成 `reports/evaluation.json` 和 `reports/evaluation.md`。缺少数据或人工判定时，对应发布门槛明确显示“未通过”。
