# AI 辅助撰写接口

## 1. 生成答复草稿（SSE）

### Request

```bash
curl -N -X POST "http://127.0.0.1:5001/api/complaint/reply-draft" \
  -H "Accept: text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "api_user",
    "org_id": "ORG_001",
    "entity_id": "ENTITY_001",
    "reporter_name": "张三",
    "scope": "entity",
    "file_ids": "demo_reply_case_001,demo_reply_case_002",
    "force": true,
    "verification_content": "经调查，杉德支付公司在部分支付业务开展过程中存在商户管理不到位、交易监测措施执行不严格等问题，相关内部控制机制仍有待完善。",
    "violation_found": true,
    "disposal_measures": "我行已责令其改正，要求其完善业务管理，并对其持续加强监管。",
    "draft_mode": "skill",
    "review_report": "review_report.docx",
    "appeal_responses": [
      {
        "appeal_id": "appeal_1",
        "type": "civil",
        "custom_text": ""
      },
      {
        "appeal_id": "appeal_2",
        "type": "document_access",
        "custom_text": ""
      }
    ]
  }'
```

`appeal_responses` 暂时无法提供时可以不传。若传入 `review_report`，服务端会下载并读取 doc/docx 调查报告，生成时逐条比对诉求与报告内容；能对应的诉求会生成答复，不能对应的诉求保留占位。若未传 `review_report`，服务端会从最新举报分析结果中的 `items_and_appeals.appeals` 读取诉求，并在答复意见书中生成待人工补充的占位段：

```text
二、关于你举报信中的其他诉求

（一）提取出来的第一个诉求。

{请补充需要答复的内容}
```

### Response

```text
data: {"task": "reply_draft", "status": "processing", "message": "正在整理案件信息", "progress": 20, "base_id": "8291df989e30f5aa"}

data: {"task": "reply_draft", "status": "processing", "message": "正在组织文书结构", "progress": 40, "base_id": "8291df989e30f5aa"}

data: {"task": "reply_draft", "status": "processing", "message": "正在生成答复内容", "progress": 60, "base_id": "8291df989e30f5aa"}

data: {"task": "reply_draft", "status": "processing", "message": "正在校验并保存草稿", "progress": 80, "base_id": "8291df989e30f5aa"}

data: {"task": "reply_draft", "status": "completed", "message": "生成完成", "progress": 100, "result": {"base_id": "8291df989e30f5aa", "title": "答复意见书", "content": "答复意见书\n\n编号：{请补充文书编号}\n\n王志武：\n本单位已收到你的举报信。你举报招商银行信用卡中心违反信用卡业务管理相关规定。根据属地监管原则，我行依法对你举报的事项进行了调查，现答复如下：\n\n一、关于你举报招商银行信用卡中心违规情况的调查\n\n（一）关于招商银行信用卡中心信用卡业务管理情况\n经调查，招商银行信用卡中心在部分业务办理过程中存在管理不到位、审核不严格等问题，内部控制机制仍需进一步完善。我行已责令其改正，要求其完善业务管理，并对其持续加强监管。\n\n二、关于你举报信中的其他诉求\n\n（一）关于要求退还相关损失。\n{请补充需要答复的内容}\n\n感谢你对我行工作的支持。\n\n{请补充落款机关}\n2026年4月2日", "source_versions": {"issue_analysis_version": 1}, "source": "auto", "saved_at": "2026-04-02T05:34:23Z"}, "reply_version": 1, "cache_hit": false, "base_id": "8291df989e30f5aa"}
```

### 说明

- 该接口会先读取最新 `issue_analysis` 结果；如果还没有完成举报分析，会直接报错。
- `scope` 可选，支持 `default`、`org`、`entity`；不传时按 `org_id/entity_id` 自动推断。`org` 必须传 `org_id`，`entity` 必须同时传 `org_id` 和稳定的 `entity_id`。传入 `reporter_name` 时按举报人姓名追加隔离维度。
- 读取 `issue_analysis/reply` 时按 `entity -> org -> default` 回退；保存新草稿只写当前 scope，不隐式写回上级 scope。
- `user_id` 仍必填，但只作为操作者写入 `meta` 和日志。
- `file_id` 和 `file_ids` 二选一；多文件时按 `file_ids` 集合生成与举报分析一致的 `base_id`；POST 场景下 `file_ids` 支持数组或逗号分隔字符串。
- 如果 `issue_analysis` 中未抽到举报日期，正文会自动退化为“我行已收到你的举报信”，不会生成病句。
- 调用方需要补充 `verification_content`、`violation_found`；`disposal_measures` 可选，无处理措施时允许为空或不传。
- `appeal_responses` 可选；不传时服务端只带入已提取诉求；若 `review_report` 为空，则生成 `{请补充需要答复的内容}` 占位，不自动编造逐条诉求答复。
- `review_report` 可选，含义与 `file_id` 一致，是可通过文件下载接口下载的 doc/docx 调查报告文件标识；读取失败时不阻断生成，自动退回占位逻辑。
- `draft_mode` 可选，取值 `prompt` 或 `skill`；不传时默认 `skill`，使用 `complaint-reply-draft` 技能生成同结构草稿。传 `prompt` 时使用旧版 prompt 生成能力。
- 正文 `content` 的第一行也必须保留标题 `答复意见书`；若模型遗漏，服务端会判失败并重试。
- 正文会尽量按 `docs/答复意见书模板.docx` 的结构生成；补充材料收到情况会从举报件原文中抽取并自动拼接，未识别到则不显示占位；当前接口拿不到的文书编号、落款机关等信息，会以 `{请补充...}` 占位输出，供人工修改。
- 模型可以基于举报事项生成调查部分标题和事项小标题，但不得编造调查事实、法律适用结论、处置措施或诉求答复内容。
- 当 `violation_found=true` 且传入了 `disposal_measures` 时，正文必须显式包含该处置措施原文；若模型遗漏，服务端会判失败并重试。
- 传入 `appeal_responses` 时，需要按诉求逐条传递答复分类，至少提供 `appeal_id` 或 `appeal_content`。
- `force=false` 时，如果 `file_id + issue_analysis_version + 文书入参 + review_report + review_report文本hash + prompt版本 + model + 非默认生成模式` 的输入指纹一致，会直接复用已有草稿。
- `force=true` 时始终重新生成并保存新版本。
- 接口只支持 JSON，不支持上传 PDF。

## 2. 查询草稿

### Request

查询最新版本：

```bash
curl -X GET "http://127.0.0.1:5001/api/complaint/reply-draft?file_ids=YOUR_FILE_ID_1,YOUR_FILE_ID_2&user_id=api_user&org_id=ORG_001&entity_id=ENTITY_001&reporter_name=%E5%BC%A0%E4%B8%89&scope=entity&version=latest"
```

查询指定版本：

```bash
curl -X GET "http://127.0.0.1:5001/api/complaint/reply-draft?file_ids=YOUR_FILE_ID_1,YOUR_FILE_ID_2&user_id=api_user&version=4"
```

### Response

```json
{
  "base_id": "a79100eeba420af1",
  "title": "答复意见书",
  "content": "这里是答复正文",
  "source_versions": {
    "issue_analysis_version": 1,
    "scope_context": {
      "scope": "entity",
      "org_id": "ORG_001",
      "entity_id": "ENTITY_001",
      "reporter_name": "张三",
      "scope_path": "ORG_001/ENTITY_001/张三"
    }
  },
  "scope_context": {
    "scope": "entity",
    "org_id": "ORG_001",
    "entity_id": "ENTITY_001",
    "reporter_name": "张三",
    "scope_path": "ORG_001/ENTITY_001/张三"
  },
  "source": "auto",
  "saved_at": "2026-03-12T10:00:00Z",
  "reply_version": 5
}
```

### 说明

- `file_id` 必填。
- `file_id` 和 `file_ids` 二选一；GET 场景下 `file_ids` 支持逗号分隔或重复 query 参数，例如 `file_ids=A,B` 或 `file_ids=A&file_ids=B`。
- `user_id` 必填。
- `version` 可选，默认 `latest`。
- 返回的是已保存草稿内容，不是文件流。

## 3. 保存草稿

### Request

```bash
curl -X POST "http://127.0.0.1:5001/api/complaint/reply-draft/save" \
    -H "Content-Type: application/json" \
    -d '{
      "user_id": "api_user",
      "org_id": "ORG_001",
      "entity_id": "ENTITY_001",
      "reporter_name": "张三",
      "scope": "entity",
      "file_ids": "YOUR_FILE_ID_1,YOUR_FILE_ID_2",
      "title": "答复意见书",
      "content": "这里是你编辑后的答复正文"
    }'
```

### Response

```json
{
  "base_id": "a79100eeba420af1",
  "reply_version": 6,
  "saved_at": "2026-03-12T10:05:00Z",
  "scope_context": {
    "scope": "entity",
    "org_id": "ORG_001",
    "entity_id": "ENTITY_001",
    "reporter_name": "张三",
    "scope_path": "ORG_001/ENTITY_001/张三"
  }
}
```

### 说明

- `file_id` 和 `file_ids` 二选一；多文件时按 `file_ids` 集合生成与举报分析一致的 `base_id`；POST 场景下 `file_ids` 支持数组或逗号分隔字符串。
- `source_versions` 为可选字段，不传也可以保存。
- 每次保存都会生成新的 `reply_version`。
- 草稿实际落盘在服务端 `reply/v{N}/final.json`，路径只打印服务端日志，不返回前端。

## 4. 下载 Word

### Request

下载最新版本：

```bash
curl -X GET "http://127.0.0.1:5001/api/complaint/reply-draft/download?file_ids=YOUR_FILE_ID_1,YOUR_FILE_ID_2&user_id=api_user&org_id=ORG_001&entity_id=ENTITY_001&reporter_name=%E5%BC%A0%E4%B8%89&scope=entity&version=latest" \
  -o "答复意见书.docx"
```

下载指定版本：

```bash
curl -X GET "http://127.0.0.1:5001/api/complaint/reply-draft/download?file_ids=YOUR_FILE_ID_1,YOUR_FILE_ID_2&user_id=api_user&version=4" \
  -o "答复意见书_v4.docx"
```

### Response

- 成功时返回 `.docx` 文件流。
- 下载 Word 时会优先基于 `server/apps/complaint_app/docs/答复意见书模板.docx` 生成，以尽量复用模板页面与样式。
- `Content-Type` 为 `application/vnd.openxmlformats-officedocument.wordprocessingml.document`。

### 说明

- `file_id` 必填。
- `user_id` 必填。
- `version` 可选，默认 `latest`。
