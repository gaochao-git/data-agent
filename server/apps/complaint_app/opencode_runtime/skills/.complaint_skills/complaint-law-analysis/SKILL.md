---
name: complaint-law-analysis
description: 基于已知合理举报事项和前置合理性分析给出的法律线索，批量生成调阅材料和核实思路。
allowed-tools: []
metadata:
  short-description: 生成调阅材料和核实思路
---

# 举报事项核查材料与思路生成

你只做一件事：根据输入中的合理举报事项、证据线索，以及 `law_rationality` 已给出的法律/条款，生成每个事项的 `document_checklist` 和 `verification_points`。

不要检索法规库，不要判断法律是否存在，不要重新判断事项是否合理，不要输出法律适用分析。法律名称、条款和合理性结论以上游 `law_rationality` 为准。

## 输入

调用方传入 JSON：

```json
{
  "case_profile": {
    "case_summary": ""
  },
  "complaint_items": [
    {
      "index": 0,
      "id": "complaint_1",
      "label": "",
      "claim_text": "",
      "original_quotes": [""],
      "evidence_materials": [""],
      "user_cited_laws": [
        {
          "raw_text": "",
          "normalized_law": "",
          "cited_clause_text": ""
        }
      ],
      "law_rationality": {
        "case_type": "law_exists_related",
        "reasonable_status": "合理",
        "scene_keywords": [""],
        "related_articles": [
          {
            "law": "",
            "article": "",
            "text_summary": ""
          }
        ],
        "candidate_laws": [""],
        "conclusion": ""
      }
    }
  ]
}
```

- `complaint_items` 只包含服务端过滤后的合理事项。
- `law_rationality.related_articles` 是已知相关法律条款。
- `law_rationality.candidate_laws` 是未明确条款时的候选法律。
- `original_quotes`、`evidence_materials`、`claim_text` 用于判断应调取哪些材料、应核实哪些问题。

## 输出要求

最终只输出 JSON 对象，不能输出 Markdown 代码块或解释文字：

```json
{
  "items": [
    {
      "complaint_id": "complaint_1",
      "document_checklist": [""],
      "verification_points": [""]
    }
  ]
}
```

## 生成规则

1. `items` 必须覆盖输入中的每个 `complaint_items[]`，不得遗漏。
2. `complaint_id` 必须等于输入事项的 `id`。
3. `document_checklist` 只写建议调取的材料，例如协议、交易流水、授权记录、系统日志、风控记录、客服工单、入网资料、结算资料、投诉处理记录等。
4. `verification_points` 只写需要核实的问题，例如主体关系、授权链路、交易性质、风控处置、通知告知、投诉处理、资金流向、责任边界等。
5. 每条使用短句，不写长段解释，不写法律条文原文。
6. 不输出“已违法”“构成违法”“事实成立”等确定性结论。
7. 所有 `items[].document_checklist[]` 的总字数尽量控制在 900 字以内，绝不能超过 1000 字；优先保留关键材料，合并同类材料，删除重复材料。
8. 每个事项的 `document_checklist` 建议 3 到 8 条，`verification_points` 建议 3 到 8 条。
