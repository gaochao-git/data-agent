# 举报事项分析历史列表接口

## 接口说明

用于查询同一个举报件的历次举报事项分析结果。服务端根据 `file_id` 或 `file_ids` 计算 `base_id`，读取该 `base_id` 下已保存的 `issue_analysis/vN/final.json`，按版本号倒序返回。

接口不会触发 OCR、摘要生成或重新分析，只读取已有结果。若字段提取接口已异步预热举报事项分析，且摘要阶段已产出但完整举报事项分析还未保存，列表会返回一条 `status=running` 的临时分析项，便于前端展示“分析中”并避免用户重复点击 AI 分析。

## 请求

- 方法：`POST`
- 路径：`/api/complaint/complaint-analysis/list`
- Content-Type：`application/json`

### 入参

```json
{
  "user_id": "xxx",
  "org_id": "ORG_001",
  "entity_id": "ENTITY_001",
  "scope": "entity",
  "file_id": "case.pdf",
  "file_ids": ["case-a.pdf", "case-b.pdf"],
  "page": 1,
  "page_size": 50
}
```

字段说明：

- `user_id`：必填，用户标识。
- `org_id`：可选，组织稳定标识；`scope=org/entity` 时必填。
- `entity_id`：可选，被举报主体稳定标识；`scope=entity` 时必填。
- `scope`：可选，支持 `default`、`org`、`entity`；不传时按 `org_id/entity_id` 自动推断。
- `file_id`：可选，单文件标识。
- `file_ids`：可选，多文件标识数组。
- `page`：可选，页码，默认 `1`。
- `page_size`：可选，每页条数，默认 `50`，最大 `100`。

规则：

- `file_id` 和 `file_ids` 二选一。
- 如果同时传 `file_id` 和 `file_ids`，`file_id` 必须包含在 `file_ids` 中。
- `file_ids` 会复用现有逻辑去空、去重、排序后生成稳定 `base_id`。
- 文件标识必须是支持的举报分析文件类型：`pdf`、`png`、`jpg`、`jpeg`。
- 查询 `issue_analysis` 时按 `entity -> org -> default` 回退；响应中的 `scope_context` 表示实际命中的 scope。

## 响应

正常响应：

```json
{
  "base_id": "82f4d02f9ab1cdef",
  "scope_context": {
    "scope": "entity",
    "org_id": "ORG_001",
    "entity_id": "ENTITY_001",
    "scope_path": "ORG_001/ENTITY_001"
  },
  "items": [
    {
      "api_kind": "complaint_analysis",
      "base_id": "82f4d02f9ab1cdef",
      "incident": "",
      "complaint_profile": {
        "reporter": "",
        "report_date": "",
        "reported_institution": "",
        "title_base": ""
      },
      "items_and_appeals": {
        "complaint_items": [
          {
            "id": "complaint_1",
            "label": "未履行风控义务",
            "content": "举报人称银行未履行风险管理义务。",
            "reasonable_status": "合理"
          }
        ],
        "appeals": []
      },
      "law_basis": [],
      "document_checklist": [],
      "verification_points": [],
      "conclusion_text": "",
      "versions": {
        "text_version": 1,
        "summary_version": 1,
        "analysis_version": 0,
        "classification_version": 0,
        "law_rationality_version": 1
      },
      "law_rationality": {
        "law_rationality_version": 1,
        "items": [
          {
            "index": 0,
            "complaint_id": "complaint_1",
            "reasonable_status": "合理",
            "conclusion": "该举报事项中举报人称违反了反电信网络诈骗法，场景中涉及诈骗、网络支付等关键信息。经分析，该法律存在，且与如下相关条款相关：第十条 条款摘要。建议进一步分析。"
          }
        ]
      },
      "issue_analysis_version": 3,
      "created_at": "2026-05-26T03:20:00Z"
    }
  ],
  "total": 3,
  "page": 1,
  "page_size": 50,
  "user_id": "xxx",
  "file_ids": ["case-a.pdf", "case-b.pdf"]
}
```

字段说明：

- `base_id`：由 `file_id` 或 `file_ids` 计算得到的举报件标识。
- `scope_context`：本次查询实际命中的 scope。
- `items`：历史分析结果列表，按 `issue_analysis_version` 倒序排列；如果存在未完成的预热分析，`status=running` 的临时项会排在最前。
- `items[]`：每一项都是举报事项分析接口保存的 `result` 内容，并额外补充：
  - `issue_analysis_version`：分析版本号，对应 `issue_analysis/vN`。
  - `created_at`：该版本保存时间，优先取 `meta.json` 的 `ts`。
- `items[].law_rationality`：合理性分析结果，对应 `law_rationality/vN/final.json`；其中 `items[]` 与举报分析 SSE 中单个合理性分析完成时的 `phase_result` 保持一致，只包含 `index`、`complaint_id`、`reasonable_status`、`conclusion`。
- `items[].items_and_appeals.complaint_items[].reasonable_status`：列表查询时按同名字段补充的合理性结论，取值为 `合理` 或 `不合理`。
- `items[].status`：可选。缺省或不存在表示已完成历史结果；`running` 表示该项来自摘要阶段产物，后续举报事项分析仍在后台生成中。
- `total`：该 `base_id` 下可读取的历史分析结果总数。
- `page`：当前页码。
- `page_size`：当前每页条数。
- `user_id`：请求用户标识。
- `file_ids`：规范化后的文件标识数组。

### running 临时项

当 `summary/<version>/final.json` 已保存结构化摘要，但对应的 `issue_analysis` 结果尚未保存时，接口会把摘要阶段已提取出的举报人、举报日期、被举报机构、事件摘要、举报事项和诉求组装成一条临时项返回。该项结构尽量与完成版分析结果一致，差异如下：

- `status` 固定为 `running`。
- `issue_analysis_version` 固定为 `0`，表示尚未生成正式分析版本。
- `law_basis`、`document_checklist`、`verification_points`、`conclusion_text` 为空，等待后台分析完成后由正式结果填充。
- `versions.summary_version` 指向当前摘要版本。

示例：

```json
{
  "api_kind": "complaint_analysis",
  "base_id": "82f4d02f9ab1cdef",
  "status": "running",
  "issue_analysis_version": 0,
  "created_at": "",
  "incident": "举报人称相关账户被违规使用。",
  "complaint_profile": {
    "reporter": "张三",
    "report_date": "2026-05-01",
    "reported_institution": "某银行",
    "title_base": "关于某银行的举报"
  },
  "items_and_appeals": {
    "complaint_items": [
      {
        "id": "complaint_1",
        "label": "未履行风控义务",
        "content": "举报人称银行未履行风险管理义务。",
        "claim_text": "银行未履行风控义务。",
        "original_quotes": ["未风控"],
        "evidence_materials": ["交易流水"],
        "user_cited_laws": [],
        "reasonable_status": "合理"
      }
    ],
    "appeals": [
      {
        "id": "appeal_1",
        "content": "依法查处"
      }
    ]
  },
  "law_basis": [],
  "document_checklist": [],
  "verification_points": [],
  "conclusion_text": "",
  "versions": {
    "text_version": 1,
    "summary_version": 1,
    "analysis_version": 0,
    "classification_version": 0
  }
}
```

前端建议：只要列表首项 `status === "running"`，就把 AI 分析按钮展示为“分析中”或禁用状态，并轮询 list 接口；等正式 `issue_analysis_version > 0` 的完成项返回后再恢复正常展示。

异常或参数非法时，接口仍返回 HTTP 200，并返回空列表结构，避免前端收到未处理异常：

```json
{
  "base_id": "",
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 50,
  "user_id": "xxx",
  "file_ids": [],
  "message": "file_id 或 file_ids 不能为空",
  "exists": false
}
```

## 排序逻辑

列表只查询同一个 `base_id` 下的历史版本，因此按 `issue_analysis/vN` 的 `N` 倒序返回，最新分析版本在前。若存在 `status=running` 的临时项，该项排在完成版历史结果之前。

## curl 示例

单文件：

```bash
curl -X POST 'http://localhost:8000/api/complaint/complaint-analysis/list' \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "test_user",
    "file_id": "case.pdf",
    "page": 1,
    "page_size": 50
  }'
```

多文件：

```bash
curl -X POST 'http://localhost:8000/api/complaint/complaint-analysis/list' \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "test_user",
    "file_ids": ["case-a.pdf", "case-b.pdf"],
    "page": 1,
    "page_size": 50
  }'
```
