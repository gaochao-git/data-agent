# complaint_app

## 1. 模块定位
面向“举报/投诉材料”的处理与分析模块，覆盖上传解析、OCR/正文提取、摘要生成、经验/法条分析以及对话式输出。

## 2. 主要功能
- 文档/图片解析：PDF 拆页转图、图片落盘，结合 OCR 提取文字。
- 文档转 Markdown：基于 `pandoc` 将 doc/docx 等转 Markdown，并对图片进行 OCR 替换。
- 摘要生成：生成结构化摘要与会话标题，支持流式输出。
- 分析代理：经验匹配、法条分析与综合结论输出（多阶段流式事件）。
- 对话与历史：SSE 事件与会话历史落盘。

## 3. 入口与调用

### 3.1 API 路由
- `/api/chat/*`：投诉/专家对话与 SSE 事件输出（见 `server/routes/chat_routes.py`）。
- `/api/complaint/complaint-analysis-uploaded`：上传文件列表与下载（见 `fastapi_routes.py`）。
- `/api/complaint/complaint-analysis`：举报件主分析入口（见 `fastapi_routes.py`）。
- `/api/complaint/complaint-analysis/list`：同一举报件的历史分析结果列表（见 `complaint_api_analysis_list.md`）。
- `/api/complaint/reply-draft*`：答复意见书生成、查询、保存与下载（见 `fastapi_routes.py`）。

### 3.2 关键管线/函数
- `TextPipeline`（`services/text/text_pipeline.py`）
  - 处理上传文件、OCR/正文生成、落盘与摘要生成。
- `generate_summary_and_save`（`services/complaint_api_service.py`）
  - 基于当前 LLM 摘要服务生成并保存结构化摘要版本，主链路优先消费 `structured_payload`。
- `run_complaint_analysis`（`services/analysis_agent/agent.py`）
  - 经验匹配、法条分析与结论生成的多阶段流。
- `stream_chat`（`services/chats/chat.py`）
  - 投诉问答的流式聊天入口。

## 4. 关键流程（简化）
1. 上传/解析文件 → PDF 拆页或图片落盘。
2. OCR 识别 → 正文落盘（新举报分析接口使用 `DATA_ROOT/complaints/<base_id>/<scope_path>/`，需配置 `DATA_ROOT`）。
3. 生成结构化摘要结果（主链路优先复用 `structured_payload`，可读摘要允许为空）。
4. 经验/法条分析 → 综合结论。

### 4.1 举报分析 scope 存储

- 新接口以 `base_id + scope` 为主路径，`scope_path` 为 `default`、`<org_id>`、`<org_id>/<entity_id>`；传入 `reporter_name` 时追加举报人维度，例如 `<org_id>/<entity_id>/<reporter_name>`。
- `user_id` 只作为操作者写入 `meta` 与日志，不作为新产物主路径。
- `original/texts/summary` 默认写 `default`；`issue_analysis/reply` 写当前 scope。
- 读取覆盖层按 `entity -> org -> default` 回退，写入只写当前 scope。
- 当前实现只做 `org_id/entity_id` 路径安全校验，组织权限需由调用方或上游鉴权层保证。

## 5. 配置项（环境变量）

### 5.1 OCR 相关
- `OCR_PROMPT`：OCR 提示词覆盖
- `OCR_MAX_CONCURRENCY`：OCR 并发上限
- `OPENAI_API_KEY` / `OPENAI_BASE_URL`
- `OCR_LLM_MODEL` / `OCR_LLM_TIMEOUT` / `OCR_LLM_TEMPERATURE` / `OCR_LLM_TOP_P`
- `OCR_CACHE_DIR` / `OCR_TEXT_FALLBACK_DIR`

### 5.2 文件与并发
- `DATA_ROOT`：数据根目录（必填）
- `MAX_UPLOAD_SIZE_MB`：上传大小限制

### 5.3 模型切换（可选）
- `OPENAI_ANALYSIS_MODEL`
- `COMPLAINT_RESPONSE_LLM_MODEL`
- `COMPLAINT_THOUGHT_LLM_MODEL`
- `COMPLAINT_EXTRACT_ITEMS_MODEL`
- `COMPLAINT_MATCH_EXPERIENCE_MODEL`
- `COMPLAINT_LAW_ANALYSIS_MODEL`
- `COMPLAINT_FINAL_CONCLUSION_MODEL`
- `COMPLAINT_REPLY_DRAFT_MODEL`

## 6. 依赖与注意事项
- `pandoc`：用于 doc/docx 转 Markdown（`tools/doc_markdown.py`）。
- `pdf2image` + `poppler`：PDF 转图片（`complaint_files_handler.py`）。
- `diskcache`：OCR 缓存。
- 若 OCR 失败或文件无法解析，通常会以错误文本形式落盘并返回。

## 7. 耗时日志
- 对外接口会记录统一格式的耗时日志：`[timing] app=complaint_app kind=route ...`
- 长链路接口还会记录阶段耗时：`[timing] app=complaint_app kind=phase ...`
- 当前已覆盖的总耗时接口：
  - `/api/complaint/complaint-analysis`
  - `/api/complaint/complaint-analysis/list`
  - `/api/complaint/reply-draft`
  - `/api/complaint/reply-draft/save`
  - `/api/complaint/reply-draft`
  - `/api/complaint/reply-draft/download`
  - `/api/complaint/complaint-analysis-uploaded*`
  - `/api/files/{full_path:path}`
- 当前已覆盖的关键阶段：
  - `complaint-analysis`：`prepare_original`、`extract_text`、`generate_summary`、`issue_analysis`
  - `complaint-analysis` 内部节点：`issue_analysis_extract_items`、`issue_analysis_match_experience`、`issue_analysis_match_experience_stage1`、`issue_analysis_match_experience_stage2`、`issue_analysis_law_analysis`、`issue_analysis_final_conclusion`
  - `reply-draft`：`load_issue_analysis`、`cache_lookup`、`generate_reply`、`save_reply`
- 日志字段统一为：
  - `route`
  - `phase`（仅阶段日志）
  - `duration_ms`
  - `success`
  - `user_id`
  - `file_id`
  - `base_id`

## 8. 代码风格补充
- 日志调用避免多行参数列表写法，尽量保持单行（例如 `logger.error("xxx: %s", err)`），便于阅读与检索。
- 对外函数/方法签名保持单行，不使用多行参数列表形式。
- 新增或改造的模型调用统一使用异步方式。
