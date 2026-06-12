# 举报事项合理性分析 TODO

## 目标

在现有 `complaint-analysis` 的 `law_skill` 前新增一个独立前置阶段：`complaint-law-rationality`。

该阶段用于对每个举报事项生成一段面向用户的“合理性分析结论”，暂时不把结果传给后续 `law_skill`，只用于 SSE 展示和版本化保存。

## 当前确认口径

1. `extract` 只负责 OCR/字段抽取，并把 OCR 文本缓存到 `default/texts`。
2. `complaint-analysis` 复用 `default/texts`，按当前 `scope/org_id/entity_id` 生成或复用 `summary`。
3. 举报事项来自当前 scope 的 `summary/final.json -> structured_payload.complaints`，不再二次调用 LLM 提取举报事项。
4. `complaint-law-rationality` 放在现有 `law_skill` 前执行。
5. 合理性分析结果暂时不传给 `law_skill`。
6. `force=true` 只影响 `issue_analysis` 是否重跑，不影响 text/summary 复用。

## 合理性分析输出口径

最终给用户看到的是一段话。

### case1：用户提及的法律存在，且举报事件与该法律相关

示例：

> 该举报事项中举报人称违反了反电信网络诈骗法，场景中涉及诈骗、网络支付等关键信息。经分析，该法律存在，且与如下相关条款相关：第 i 条 xxxx；第 y 条 xxxx。建议进一步分析。

### case2：用户提及的法律存在，但举报事件与该法律不相关

示例：

> 该举报事项中举报人称违反了反电信网络诈骗法，场景中涉及诈骗、网络支付等关键信息。经分析，该法律存在，但法律业务方向与该举报无相关，建议酌情处理。

### case3：用户提及的法律不存在

示例：

> 该举报事项中举报人称违反了反电信网络诈骗法，场景中涉及诈骗、网络支付等关键信息。经分析，该法律不存在，请进一步确认，或要求举报人进一步补充材料。

### case4：用户未提及法律

示例：

> 该举报事项没有明确提及相关法律，场景中涉及诈骗、网络支付等关键信息。经分析，可能涉及哪些法律法规。

注意：对前端和用户展示时，不要出现 `law_skill` 等内部技术名。

## 前端 SSE 输出

沿用现有固定 SSE 包装，不新增临时 `kind=rationality` 给前端。

每个举报事项合理性分析完成后，返回：

```json
{
  "task": "complaint-law-rationality",
  "status": "processing",
  "message": "第 1 个举报事项合理性分析完成",
  "result": {
    "index": 0,
    "complaint_id": "complaint_1",
    "conclusion": "该举报事项中举报人称违反了反电信网络诈骗法，场景中涉及诈骗、网络支付等关键信息。经分析，该法律存在，且与如下相关条款相关：第 i 条 xxxx；第 y 条 xxxx。建议进一步分析。"
  }
}
```

前端只依赖 `result.index`、`result.complaint_id`、`result.conclusion`。

其他字段如 `case_type`、`mentioned_law`、`related_articles`、`candidate_laws` 可以保存在后端版本文件里，暂不作为前端必需字段。

## 版本化存储

新增 stage：`law_rationality`。

路径：

```text
DATA_ROOT/complaints/<base_id>/<scope_path>/law_rationality/vN/
```

保存文件：

```text
final.json
meta.json
```

`final.json` 建议结构：

```json
{
  "api_kind": "complaint_law_rationality",
  "base_id": "...",
  "scope_context": {},
  "summary_version": 1,
  "items": [
    {
      "index": 0,
      "complaint_id": "complaint_1",
      "case_type": "law_exists_related",
      "mentioned_law": "中华人民共和国反电信网络诈骗法",
      "scene_keywords": ["诈骗", "网络支付"],
      "related_articles": [],
      "candidate_laws": [],
      "conclusion": "..."
    }
  ]
}
```

`issue_analysis` 保存时应引用：

```json
{
  "versions": {
    "law_rationality_version": 1
  }
}
```

可按需要冗余保存一份 `law_rationality` 快照，但暂时不传给后续 `law_skill`。

## 技能设计

新增技能：`complaint-law-rationality`。

能力：

1. 允许使用 shell/tool。
2. 针对单个举报事项执行。
3. 分析 `user_cited_laws` 中用户提及的法律是否存在。
4. 如果提及法律存在，判断该法律及相关条款是否与举报事项相关。
5. 如果未提及法律，检索并归纳可能涉及的法律法规。
6. 最终输出 JSON，服务端取 `conclusion` 给前端。

输入示例：

```json
{
  "index": 0,
  "complaint_id": "complaint_1",
  "complaint": {
    "label": "",
    "description": "",
    "claim_text": "",
    "original_quotes": [],
    "evidence_materials": [],
    "user_cited_laws": []
  },
  "incident": "",
  "reported_entities": []
}
```

输出示例：

```json
{
  "case_type": "law_exists_related",
  "mentioned_law": "",
  "scene_keywords": [],
  "related_articles": [],
  "candidate_laws": [],
  "conclusion": ""
}
```

## 并发要求

每个举报事项独立执行合理性分析技能。

配置：

```text
COMPLAINT_LAW_RATIONALITY_MAX_CONCURRENCY
```

默认值：`100`。

任意一个举报事项分析完成后，应立即 SSE 返回该事项结果。

## trace 要求

合理性分析技能会使用 shell/tool，因此需要保留工具调用轮次：

1. 每个举报事项的工具调用命令。
2. 工具调用结果。
3. 模型输出。
4. trace 页面沿用“左侧工具调用、右侧模型输出”的展示方式。

## 待实现清单

- [ ] 新增 `complaint-law-rationality` skill 文件。
- [ ] 新增合理性分析 skill runner。
- [ ] 新增并发执行函数，读取 `COMPLAINT_LAW_RATIONALITY_MAX_CONCURRENCY`，默认 `100`。
- [ ] 在 `run_complaint_analysis` 中插入 `complaint-law-rationality` 阶段，位置在现有 `law_skill` 前。
- [ ] 合理性分析结果暂时不传给 `law_skill`。
- [ ] 新增 `law_rationality` 版本化保存：`final.json`、`meta.json`。
- [ ] `issue_analysis` 保存时引用 `law_rationality_version`。
- [ ] SSE 映射为固定 `task/status/message/result` 结构。
- [ ] trace 页面展示合理性分析的工具调用轮次。
- [ ] 补测试：并发执行、SSE 输出、版本保存、issue 引用、不传给 `law_skill`。
