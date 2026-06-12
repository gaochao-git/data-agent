# 举报件接口设计（举报事项分析 + 辅助文书撰写）

## 1. 目标与范围

本文定义举报件模块新增的 4 个 API：

1. `POST /api/complaint/complaint-analysis`（SSE）
2. `POST /api/complaint/reply-draft`（SSE）
3. `POST /api/complaint/reply-draft/save`（JSON）
4. `GET /api/complaint/reply-draft/download`（Word 下载）

统一约束：

- 主入口参数：`user_id`（必填）
- 主入口参数：`file_id`（上传模式可选）
- 可选参数：`force`，默认 `false`
- 举报事项分析接口支持开关：`use_uploaded_pdf`（默认 `false`）
- SSE 返回风格沿用 `ComplaintSSEEvent`（`task/status/message/progress/extra`）

---

## 2. 统一设计原则

### 2.1 file_id 与版本复用

- `file_id` 作为业务主键输入。
- 服务内将 `file_id` 映射为稳定 `base_id`（建议 hash 映射）以复用现有版本目录。
- 若 `force=false` 且已有可用版本，则优先复用，不重复重跑。

#### 2.1.1 file_id -> 文件（下载与定位口径）

说明：当 `use_uploaded_pdf=true` 时，本节下载与定位逻辑可跳过，直接使用上传文件。

为避免歧义，`file_id` 到文件路径按以下顺序解析：

1. 外部下载（主路径，复用 `word_fit_app` 的 `fetch_file_node` 思路）：
   - 请求：`GET ${FILE_DOWNLOAD_API_URL}`
   - Query 参数：
     - `filePath = file_id`
     - `user = FILE_DOWNLOAD_USER`
     - `clusterList = FILE_DOWNLOAD_CLUSTER`
     - `token = FILE_DOWNLOAD_TOKEN`
   - 本地保存：`${DATA_ROOT}/complaints/.../{safe_file_id}`

2. 兜底解析（复用 `resolve_file_node` 思路）：
   - `get_file_path_by_id(file_id)`（DB 映射）
   - 本地路径 `${DATA_ROOT}/complaints/.../{file_id}`
   - 若 `file_id` 本身是可访问路径，则直接使用

3. 安全与校验：
   - `file_id` 先做路径净化（去掉前导 `/`、禁止 `..` 路径穿越）
   - 仅允许 PDF 进入本次分析/文书流程（非 PDF 返回 `400`）
   - 下载超时与重试：`timeout=30s`，最多 `2` 次重试

4. 失败返回：
   - 无法下载/定位：`404`
   - 文件存在但不可读/格式不支持：`400`
   - 其他异常：`500`

#### 2.1.2 base_id 映射与现有目录对齐

- `base_id` 使用稳定映射：`sha1(file_id).hexdigest()[:16]`。
- 目录沿用现有举报件版本化：
  - `complaints/{user_id}/{base_id}/texts/vN`
  - `complaints/{user_id}/{base_id}/summary/vN`
  - `complaints/{user_id}/{base_id}/analysis/vN`
  - `complaints/{user_id}/{base_id}/classification/vN`
- 文书新增：
  - `complaints/{user_id}/{base_id}/reply/vN`

### 2.2 分析策略（已定）

采用“**专家优先 + 法律补强**”：

- 专家经验结果作为主骨架：
  - 分类
  - 调阅清单
  - 核实要点
  - 已有关联法条
- 法律补强只增量补充，不覆盖专家主结果。

---

## 3. API 详细定义（独立）

## 3.1 举报事项分析接口

### 3.1.1 路由

`POST /api/complaint/complaint-analysis`

### 3.1.2 请求体

模式 A（默认，JSON）：

```json
{
  "user_id": "string",
  "file_id": "string",
  "force": false,
  "use_uploaded_pdf": false
}
```

字段说明：

- `file_id`：`use_uploaded_pdf=false` 时必填，文件标识。
- `user_id`：必填，用户标识，用于版本目录隔离。
- `force`：可选，默认 `false`。`true` 时忽略缓存并重跑分析链路。
- `use_uploaded_pdf`：可选，默认 `false`。`true` 时启用上传 PDF 模式。

模式 B（上传，`multipart/form-data`）：

- 字段：
  - `user_id=xxx`（必填）
  - `use_uploaded_pdf=true`
  - `force=false|true`
  - `file=<pdf binary>`（必填）
  - `file_id`（可选，用于稳定复用 base_id）

### 3.1.3 处理流程（实现口径）

1. 判断输入模式：
   - `use_uploaded_pdf=false`：通过 `file_id` 下载原始 PDF（复用现有 fetch_file_node 下载参数与配置）。
   - `use_uploaded_pdf=true`：直接使用上传 PDF，不走 file 下载。
2. 提取文本（复用 complaint 现有文本提取链路：OCR/文档解析）。
3. 生成摘要（复用 `SummaryPipeline`，并写入 `summary/vN`）。
4. 执行事项分析（复用 `run_complaint_analysis` 主链路）：
   - `complaint_extract_items`
   - `complaint_match_experience`
   - `complaint_law_analysis`
   - `complaint_final_conclusion`
5. 生成结构化结果（举报人/事项/诉求/法条/调阅清单/核实思路）。
6. 落盘版本目录并更新 `manifest`。

### 3.1.7 分析实现细节（针对“到底怎么实现”）

- 分类与法律不是二选一，采用“融合输出”：
  - 分类/核实要点/调阅清单：以专家经验为主
  - 法律法规条款：专家经验 + 法律补强并集
- 法律补强位置：
  - 在 `complaint_law_analysis` 后执行（或并行补强后合并）
- 合并策略：
  - 法条按 `law_name + item` 去重
  - 调阅清单与核实要点仅增量补充，不覆盖专家项
- 最终输出是固定结构化 JSON，前端无需解析自由文本。

### 3.1.4 SSE 阶段事件（建议）

- `10%`：正在生成摘要
- `30%`：正在提取举报事项与诉求
- `60%`：正在匹配专家经验并补强法律依据
- `80%`：正在生成核实思路
- `100%`：生成完成

### 3.1.5 SSE 事件示例

```text
data: {"task":"complaint_analysis","status":"processing","message":"正在生成摘要","progress":10,"base_id":"xxx"}
```

完成态示例：

```text
data: {
  "task":"complaint_analysis",
  "status":"completed",
  "message":"生成完成",
  "progress":100,
  "base_id":"xxx",
  "result":{
    "complaint_profile":{
      "reporter":"张三",
      "report_date":"2026-02-20",
      "reported_institution":"某银行"
    },
    "items_and_appeals":{
      "complaint_items":[{"id":"complaint_1","content":"..."}],
      "appeals":[{"id":"appeal_1","content":"..."}]
    },
    "law_basis":[
      {
        "law_name":"《支付结算办法》",
        "articles":[
          {"item":"第X条","item_text":"..."},
          {"item":"第XX条","item_text":"..."}
        ]
      }
    ],
    "document_checklist":[
      {"category":"账户资料","items":["...","..."],"source":"expert"}
    ],
    "verification_points":[
      {"point":"核对交易授权链路","source":"expert"},
      {"point":"补充核查签约告知凭证","source":"law_enhanced"}
    ],
    "versions":{
      "text_version":1,
      "summary_version":2,
      "analysis_version":3,
      "classification_version":4
    }
  }
}
```

### 3.1.6 返回内容定义（业务字段）

1. 举报事项及举报人诉求：
- 举报人
- 举报日期
- 被举报机构
- 举报事项
- 举报诉求

2. 违反的法律法规条款：
- 按“法律名称 -> 条款列表”输出
- 来源可含专家经验法条与法律补强法条

3. 调阅资料清单：
- 专家经验为主，法律补强仅追加缺失项

4. 核实思路：
- 专家经验为主，法律补强仅增量补点

---

## 3.2 辅助文书撰写接口

### 3.2.1 路由

`POST /api/complaint/reply-draft`

### 3.2.2 请求体

```json
{
  "user_id": "string",
  "file_id": "string",
  "force": false,
  "verification_content": "string",
  "violation_found": true,
  "disposal_measures": "",
  "appeal_responses": [
    {
      "appeal_id": "appeal_1",
      "type": "civil",
      "custom_text": ""
    }
  ]
}
```

### 3.2.3 处理流程（实现口径）

1. 基于 `file_id` 获取 `base_id`。
2. 读取最新 `issue_analysis` 结果，复用：
   - 举报人
   - 举报日期
   - 被举报机构
   - 举报事项
   - 诉求列表
3. 校验前端补充字段：
   - `verification_content`
   - `violation_found`
   - `disposal_measures`（可选，无处理措施时允许为空）
   - `appeal_responses`
4. 生成固定业务标题：`举报答复意见书`。
5. 组装固定模板与变量，调用大模型输出 JSON：
   - `title`
   - `content`
6. 服务端校验输出结构，并以固定标题覆盖 `title`。
7. `force=false` 时按输入指纹复用缓存；否则生成新版本。
8. SSE 返回，并落盘 `reply/vN/final.json`。

### 3.2.6 文书接口的大模型输入/输出契约（明确）

`/api/complaint/reply-draft` 必须调用大模型，输入来源明确如下：

输入（服务端组装）：
- `expected_title`：固定为 `举报答复意见书`
- `reporter`：举报人姓名
- `report_date`：举报日期
- `reported_institution`：被举报机构
- `complaint_summary`：举报事项综合表述
- `appeals`：诉求列表及其答复分类
- `verification_content`：调查核实情况
- `violation_found`：是否存在违规
- `disposal_measures`：处理措施，可选；无处理措施时允许为空
- `document_date`：服务端当天日期

模型调用：
- 生成模型：新增 `COMPLAINT_REPLY_DRAFT_MODEL`（未配置时回退 `COMPLAINT_FINAL_CONCLUSION_MODEL`）
- 输出必须是 JSON，格式为 `{"title":"","content":""}`

输出（固定）：
- `title`：`举报答复意见书`
- `content`：固定模板正文，包含抬头、举报事项综述、核查结论、逐条诉求答复、结尾

### 3.2.4 SSE 阶段（需求指定）

- `20%`：正在整理案件信息
- `40%`：正在组织文书结构
- `60%`：正在生成答复内容
- `80%`：正在校验并保存草稿
- `100%`：生成完成

### 3.2.5 完成态结果结构

```json
{
  "title":"举报答复意见书",
  "content":"（正文内容）",
  "source_versions":{
    "issue_analysis_version":1
  }
}
```

输出要求：

- `title`：服务端固定输出 `举报答复意见书`
- `content`：按固定模板生成实际答复内容

---

## 3.3 保存草稿接口

### 3.3.1 路由

`POST /api/complaint/reply-draft/save`

### 3.3.2 请求体

```json
{
  "user_id":"string",
  "file_id":"string",
  "file_ids":"file_id_1,file_id_2",
  "title":"string",
  "content":"string",
  "source_versions":{
    "text_version":1,
    "summary_version":2,
    "analysis_version":3,
    "classification_version":4
  }
}
```

`file_id` 和 `file_ids` 二选一；多文件保存时使用 `file_ids` 生成与举报分析一致的 `base_id`。POST 场景下 `file_ids` 支持数组或逗号分隔字符串。

### 3.3.3 响应体

```json
{
  "base_id":"xxx",
  "reply_version":1,
  "saved_at":"2026-02-25T10:00:00Z"
}
```

### 3.3.4 存储路径约定

沿用举报件版本化逻辑，新增目录：

- `complaints/{user_id}/{base_id}/reply/v{N}/final.json`
- `complaints/{user_id}/{base_id}/reply/v{N}/meta.json`

并更新 `manifest.reply.current`。

---

## 3.4 下载 Word 接口

### 3.4.1 路由

`GET /api/complaint/reply-draft/download?file_ids={file_id_1},{file_id_2}&user_id={user_id}&version=latest`

### 3.4.2 入参

- `file_id` / `file_ids`：二选一；`file_ids` 支持逗号分隔或重复 query 参数
- `user_id`：必填
- `version`：可选，默认 `latest`

### 3.4.3 行为

1. 按 `file_id` 或 `file_ids` 定位 `base_id`。
2. 找到目标 `reply/vN/final.json`。
3. 使用 docx 模板填充标题与正文。
4. 返回 `.docx` 文件流。

### 3.4.4 响应

- `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `Content-Disposition: attachment; filename="xxx答复意见书.docx"`

---

## 3.5 查询草稿接口

### 3.5.1 路由

`GET /api/complaint/reply-draft?file_ids={file_id_1},{file_id_2}&user_id={user_id}&version=latest`

### 3.5.2 入参

- `file_id` / `file_ids`：二选一；`file_ids` 支持逗号分隔或重复 query 参数
- `user_id`：必填
- `version`：可选，默认 `latest`

### 3.5.3 行为

1. 按 `file_id` 定位 `base_id`。
2. 读取目标 `reply/vN/final.json`。
3. 返回标题、正文、来源版本、来源类型、保存时间，并补充 `reply_version`。

### 3.5.4 响应

```json
{
  "base_id":"xxx",
  "reply_version":1,
  "title":"xxx的答复意见书",
  "content":"正文",
  "source_versions":{
    "text_version":1,
    "summary_version":2,
    "analysis_version":3,
    "classification_version":4
  },
  "source":"user",
  "saved_at":"2026-02-25T10:00:00Z"
}
```

---

## 4. 融合策略详细规则（关键）

### 4.1 法律依据 law_basis

- 合并来源：专家经验 + 法律补强
- 去重键：`law_name + article`
- 输出按法律分组

### 4.2 调阅资料清单 document_checklist

- 专家经验列表为主
- 法律补强仅追加专家未覆盖项
- 每项增加 `source`：`expert` / `law_enhanced`

### 4.3 核实思路 verification_points

- 专家经验为主
- 法律补强补充缺失核查点
- 不覆盖专家已有点

---

## 5. 错误码与失败处理

建议：

- `400`：参数错误（缺 user_id / 缺 file_id / 上传模式缺 file）
- `404`：file_id 对应文件不存在
- `415`：上传文件非 PDF
- `500`：内部异常
- SSE 失败统一返回：
  - `status=error`
  - `message=具体失败阶段+原因`

降级策略：

- 法律补强失败时，不阻断主流程：
  - 保留专家结果
  - 标记 `law_enhance_failed=true`

---

## 6. 验收标准

1. 分析接口可稳定输出 4 大块内容：
- 举报事项及诉求
- 法律依据
- 调阅清单
- 核实思路

2. 文书接口可输出：
- 标题
- 正文

3. `force=false` 命中复用，不重复重跑。

4. 草稿保存后可按版本下载 Word。

---

## 7. 本轮补充与仍待确认项

### 7.1 已补充（本次完善）

- 明确了 `file_id -> 文件` 的下载、兜底、校验与失败返回。
- 明确了接口一的实际实现链路与复用函数位置。
- 明确了接口二“必须调用大模型”时的输入字段与输出契约。

### 7.2 仍待你后续补充（不影响接口先开发）

- 答复意见书正文的固定章节模板与段落口径（你之前已说明后续补）。
