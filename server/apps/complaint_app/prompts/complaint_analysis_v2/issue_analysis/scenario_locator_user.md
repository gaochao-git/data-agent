# 场景定位任务

## 举报人
{{reporter_name}}

## 被举报机构
{{reported_institution}}

## 举报信结构化摘要
{{summary_markdown}}

## 举报信原文
{{complaint_text}}

## 任务说明

请按你的技能定义执行：
1. 从摘要中提取举报事项清单（锚定模式下仅保留与"被举报机构"直接相关的事项）
2. 对每项事项独立识别支付场景，逐项准备一个 `search_law` 调用（不合并同场景的事项）
3. 在同一轮一次性 emit 所有 `search_law` 调用，形成调用数组；每条 `sentence` 采用结构化格式（场景类型 + 适用法规范围 + 单项举报事项描述 + 检索目标）

至少 emit 一个 `search_law` 调用。不要输出分析报告正文。
