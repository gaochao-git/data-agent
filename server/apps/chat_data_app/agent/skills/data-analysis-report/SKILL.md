---
name: data-analysis-report
title: 数据分析报告
description: 基于真实指标查询结果完成数据分析、结论核验、图表设计和可编辑 Office 报告交付；适用于经营分析、专题分析、领导汇报和周期复盘。
metadata:
  domain: 数据分析
  category: analysis_report
  output_type: report
---

# 数据分析报告技能

## 适用目标

将用户的分析目标整理为一份可核验的数据分析报告。报告可以包含：

- 管理摘要、关键指标和结论
- 趋势、对比、结构、转化和异常分析
- 查询明细或汇总表
- 前端可预览的 `chart_spec`
- 使用 OfficeCLI 生成的可编辑 `.docx` 报告

本技能只负责分析流程和产物编排，不替代 `search_dataset`、`dsl2sql_tool`、`semantic_query_tool`、`chart-processing` 或 `officecli` 的工具契约。

## 触发与前置条件

仅在用户明确要求“生成分析报告 / 汇报材料 / 经营分析 / 专题报告 / Word 报告”等完整分析产物时使用。只查一个指标、只要一句结论或只要前端图表时，使用普通问数或 `chart-processing`。

生成 `.docx`、`.xlsx` 或 `.pptx` 时，本轮必须存在 `bash_run`，并且工作区后端必须是 sandbox；否则先完成可交付的文本或图表分析，不要伪造 Office 文件。

## 目录与产物

```text
/mnt/user-data/workspace/
├── query-*.json              # semantic_query_tool 的真实结果
├── analysis-*.json           # 脚本生成的确定性中间结果
├── build_report.py           # 需要清洗、对齐或派生计算时使用
└── report-draft.docx         # OfficeCLI 草稿

/mnt/user-data/outputs/
├── <主题>.chart.json         # 需要前端图表预览时生成
└── <主题>.docx               # 最终报告
```

`outputs` 只放面向用户的最终产物；原始查询结果和脚本中间文件放 `workspace`。不要把查询结果、脚本或临时文件放入 `.tool-offload`，也不要直接修改 `uploads` 或技能目录。

## 标准流程

### 1. 明确报告范围

先确定分析对象、时间范围、指标、维度、对比口径和交付格式。信息不足时只询问影响查询正确性的关键问题，不自行假设指标定义、时间粒度、金额单位或状态码含义。

### 2. 选择指标集并核验口径

1. 调用 `search_dataset` 查找可用指标集。
2. 指标集结果被卸载时，先按系统提示词要求用 `file_read` 读取 `result_path`；未完成读取前不得构造 DSL。
3. 对状态码、枚举、时间存储格式、金额单位或关联关系不明确的字段，先查询少量明细核验，再做正式聚合。

### 3. 查询真实数据

1. 使用同一份 DSL 依次调用 `dsl2sql_tool` 和 `semantic_query_tool`；禁止手写 SQL 或编造查询结果。
2. 每个查询用明确的业务文件名保存到 `workspace`，例如 `workspace/current.json`、`workspace/previous.json`。
3. 工具结果被卸载时，前端可能已经展示轻量结果；只有后续分析确实需要原始内容时，才按最小必要范围使用 `file_read` 分页读取。
4. 发现结果字段、粒度或数据量不符合预期时，停止写报告，先修正 DSL 或重新查询。

### 4. 分析与确定性计算

- 单查询、无需派生计算时，可直接基于真实结果生成结论和图表。
- 同比、环比、占比、排名、累计值、目标达成率、多查询对齐或缺失值处理，必须写 `workspace/build_report.py`，读取真实查询结果后计算。
- 脚本不得硬编码业务数据；必须显式记录排序、对齐、缺失值和单位处理规则。
- 结论只引用查询结果或脚本计算结果。不能因为图表或查询结果被卸载就猜测数值。
- 重要结论至少抽查对应原始结果；异常结论要说明判断口径和证据。

### 5. 生成前端图表

需要聊天框内预览时，按需加载 `chart-processing`，读取其主指南，并把真实数据转换为 `chart_spec`。最终图表文件写入 `outputs/<主题>.chart.json`，然后调用 `present_artifacts`。不要把截图当作图表产物。

### 6. 生成 Office 报告

需要 Word 汇报材料时，按需加载 `officecli`，并读取：

- Word 基础操作：`/mnt/skills/officecli/references/word.md`
- Word 原生图表：`/mnt/skills/officecli/references/word-charts.md`

报告建议按以下顺序组织：标题、分析范围与口径、管理摘要、关键指标、趋势/对比/结构图表、明细或汇总表、结论与建议、数据来源和生成时间。

图表优先使用 Word 原生可编辑图表：趋势用 `line`，分类对比用 `column` 或 `bar`，占比用 `pie`，阶段转化用 `funnel`，季度连续趋势可用 `area`。只有 OfficeCLI 当前版本不支持目标表达时，才考虑其他已验证的产物形式；不要把前端截图嵌入 Word 作为默认方案。

文档编辑必须遵循：

1. 先在 `workspace` 创建或复制草稿。
2. 用 `officecli view`、`get` 或 `query` 确认结构后再编辑。
3. `officecli save` 后执行 `officecli validate` 和 `officecli view ... issues`。
4. 验证通过后复制到 `outputs`，再次确认文件存在。
5. 调用 `present_artifacts(files=["/mnt/user-data/outputs/<主题>.docx"])` 呈现给用户。

### 7. 用户输出

正文只给出简洁的分析结论、关键口径和已生成的产物，不粘贴完整 SQL、脚本、原始查询行或完整 JSON。报告未完成校验时，必须明确说明失败环节，不得声称已交付。

## 失败边界

- 指标集无法唯一确定：先澄清，不凭名称相似猜测。
- DSL 校验失败：根据工具错误修正 DSL，不能改成手写 SQL。
- 查询失败或结果为空：说明真实状态；除非用户确认，否则不编造替代数据。
- 需要脚本但没有 `bash_run`：可以完成受控的简单文本/图表分析，但不能声称已执行脚本或生成完整 Office 报告。
- OfficeCLI 校验失败：停止交付并报告文件未通过校验。
- 文件过大或预览失败：保留已生成文件，说明预览限制，不重复生成无效副本。

## 完成判定

完整数据分析报告必须同时满足：

1. 报告范围和数据口径明确。
2. 数据来自真实指标查询，必要的派生计算已由脚本完成。
3. 关键结论有查询结果或计算结果支撑。
4. 请求图表时已生成并呈现 `chart_spec`。
5. 请求 Office 文件时已通过 OfficeCLI 校验，并已呈现 `outputs` 文件。
