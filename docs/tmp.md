# 初次问答思考块与内容块设计（举报件分析）

## 目标
用于“初次问答”时的 AI 回复结构：思考块 + 内容块。

## 思考块结构

### Step 1（只展示一句话）
前端展示的数据结构：

```
{
  "id": "<message_id>-intro",
  "label": "",
  "label_hidden": true,
  "status": "done",
  "detail": "我来帮您分析文件。"
}
```

补充说明：前端需识别 `label_hidden: true`（或 `label: ""`）并隐藏 label。

### Step 2（阅读）
前端展示的数据结构：

```
{
  "id": "<message_id>-read",
  "label": "阅读（举报件文件名称.pdf, xxxx.pdf....）",
  "status": "done",
  "detail": "{摘要最终内容}"
}
```

补充说明：括号内为“用户本次上传的所有文件名列表”，用逗号拼接，超长（20个字符）时截断并以 `...` 结尾。

### Step 3（检索）
前端展示的数据结构：

```
{
  "id": "<message_id>-search",
  "label": "检索（国家法律、行政法规、部门规章、规范性文件）",
  "status": "done",
  "detail": "{检索结果描述}"
}
```

### Step 4（分析）
前端展示的数据结构：

```
{
  "id": "<message_id>-analysis",
  "label": "分析（举报事项{i}）",
  "status": "done",
  "detail": "{该事项的综合分析文字}"
}
```

补充说明：有几个举报事项，就生成几条 Step 4。
后端仍按 `docs/complaint-analysis-thinking-block.md` 的 5 个子步骤执行分析，但前端仅展示这一段综合分析内容。

## 内容块（content）
- 输出最终“结构化分析结果”，只展示结论。
- 流式输出内容，不包含过程描述。
