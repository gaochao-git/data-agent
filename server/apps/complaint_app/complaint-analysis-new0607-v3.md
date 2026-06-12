# complaint-analysis-v2 当前代码执行流程

本文档整理当前 `complaint-analysis-v2` 接口在代码中的真实执行路径，包含三阶段编排、analysis 阶段 `search_law` 的双层调用链、SSE 输出方式、缓存与版本落盘、以及日志与审核信息保存位置。

---

## 1. 入口路由

文件：`server/apps/complaint_app/fastapi_routes.py`

接口：`POST /api/complants/complaint-analysis-v2`

### 路由处理逻辑
1. 读取 `content-type`
2. 兼容两种输入方式：
   - `multipart/form-data`
   - JSON body
3. 复用现有解析逻辑：
   - `_build_analysis_payload_from_form()`
   - `_build_analysis_payload_from_json()`
   - `_parse_uploaded_pdf()`
   - `_validate_analysis_payload()`
4. 计算：
   - `user_id`
   - `base_id`
   - `scope_context`
5. 进入：
   - 正常流：`stream_complaint_analysis_v2(...)`
   - 不支持文件类型时：`stream_mock_complaint_analysis_v2(...)`

---

## 2. prompt 文件组织方式

目录：`server/apps/complaint_app/prompts/complaint_analysis_v2/`

当前已按阶段拆分：

```text
common/
  system.md

summary/
  user.md
  guidance.md

issue_analysis/
  user.md
  guidance.md

final/
  user.md
```

对应装配文件：`server/apps/complaint_app/services/complaint_analysis_v2/prompt_loader.py`

### 阶段映射
- summary 阶段：
  - `common/system.md`
  - `summary/user.md`
  - `summary/guidance.md`
- issue_analysis 阶段：
  - `common/system.md`
  - `issue_analysis/user.md`
  - `issue_analysis/guidance.md`
- final 阶段：
  - `common/system.md`
  - `final/user.md`

---

## 3. 主编排入口

文件：`server/apps/complaint_app/services/complaint_analysis_v2/orchestrator.py`

主函数：`stream_complaint_analysis_v2(...)`

整体流程分成四段：
1. 文本准备
2. summary 阶段
3. analysis 阶段
4. final 阶段

并通过 SSE 依次输出：
- `Start`
- `Reasoning`
- `Acting`
- `Answer`
- `Reference`
- `Done`

当前约定：
- `Answer` 返回每阶段真正内容
- `Reference` 在 analysis/final 阶段返回固定结构的法规引用内容
- `Done.result` 返回聚合结果

---

## 4. 文本准备阶段

入口逻辑在：`stream_complaint_analysis_v2(...)`

### 4.1 发送 Start
先发送一次：
- `Start`

内容包含：
- `base_id`
- `user_id`
- `scope_context`
- `file_ids`
- `route`

### 4.2 文本提取
调用：`_ensure_text_content(...)`

内部逻辑：
1. 先在 default scope 读取 `text` 缓存
2. 若无缓存：
   - 上传文件：`save_uploaded_pdf()`
   - file_id/file_ids：`resolve_pdf_file()`
   - 再走 `extract_texts_and_save()`

### 4.3 日志
此阶段会记录：
- `request_input`
- `extract_text` phase timing

---

## 5. summary 阶段

函数：`_run_summary_stage(...)`

### 5.1 输入
由 `prompt_loader.build_summary_messages(...)` 组装：
- `system = common/system.md`
- `user = summary/user.md + summary/guidance.md`

user 里注入：
- `complaint_text`
- `reporter_name`
- `reported_institution`

### 5.2 执行方式
summary 阶段当前仍是：
- 直接 `AsyncLLMClient.chat(...)`
- **不接 skill**
- **不接 tool**

### 5.3 缓存与落盘
阶段名：`summary_v2`

保存内容：
- `final.md`
- `final.json`
- `meta.json`

meta 中会记录：
- `input_hash`
- `model`
- `prompt_versions`

### 5.4 SSE
此阶段输出：
- `Reasoning(phase=summary)`
- `Acting(phase=summary)`
- `Answer(phase=summary, content=summary_markdown)`
- `Reference(phase=summary)`

---

## 6. analysis 阶段（当前最关键）

函数：`_run_analysis_stage(...)`

这是当前 v2 与普通三阶段直调最大的区别。

### 6.1 输入消息形态
由 `prompt_loader.build_issue_analysis_messages(...)` 组装：
- 第一条：`system = common/system.md`
- 第二条：`user = issue_analysis/user.md + issue_analysis/guidance.md`

user 中注入：
- `complaint_text`
- `summary_markdown`
- `tool_info`

当前 `tool_info` 会说明：
- analysis 阶段可调用 `search_law`
- `search_law` 内部是独立技能执行

---

## 7. analysis 阶段的双层调用链

### 第一层：analysis 主会话
analysis 不再是简单的 `AsyncLLMClient.chat(...)`，而是：
- 创建 `Session`
- 创建 `ToolExecutor`
- 只注册一个外层工具：`search_law`
- 用 `SessionPrompt.prompt(...)` 运行 analysis 主会话

也就是说，analysis 主调用的语义是：
1. `system`
2. `user`
3. assistant 可以触发 `search_law`
4. tool 返回 `search_law` 结果
5. assistant 再继续生成最终分析文本

### 第二层：search_law 独立 skill 会话
当 analysis 主会话调用 `search_law` 时，执行器会进入：
- `server/apps/complaint_app/services/complaint_analysis_v2/tool_runtime.py`
- `search_law_tool()`

其 `_executor` 会真正调用：
- `server/apps/complaint_app/services/complaint_analysis_v2/search_law_runner.py:search_law(...)`

而 `search_law(...)` 自己又会：
1. 创建一个新的独立 `Session`
2. 创建新的 `ToolExecutor`
3. 注册：
   - `document_read`
4. 读取并渲染：
   - `server/apps/complaint_app/opencode_runtime/skills/.complaint_skills/complaint-search-law/SKILL.md`
5. 再次调用 `SessionPrompt.prompt(...)`

因此 `search_law` 的内部 messages 和 analysis 主 messages **完全独立**。

这正对应需求里的要求：
- `law_search` 是独立大模型调用
- 内部反复调用 `document read`
- 内部执行过程不要混进 analysis 主流程
- analysis 主流程只接收 `law_search` 的结果

---

## 8. analysis 阶段 tool 细节

### 8.1 外层工具
文件：`server/apps/complaint_app/services/complaint_analysis_v2/tool_runtime.py`

当前外层只暴露：
- `search_law`

它的参数：
- `sentence`

### 8.2 内层 skill
文件：
- `server/apps/complaint_app/opencode_runtime/skills/.complaint_skills/complaint-search-law/SKILL.md`

skill 只允许调用：
- `document_read`

### 8.3 底层只读工具
文件：
- `server/apps/complaint_app/opencode_runtime/tools/builtin/document_read_tool.py`

作用：
- `document_read`：读取法规文本

并有路径根目录限制：
- 默认法规根目录来自 `COMPLAINT_LAWS_ROOT`
- 防止越界读取

---

## 9. search_law 的 trace 与审核信息

这是当前新增的审计能力。

### 9.1 trace_context 传递
在 analysis 主会话调用 `search_law` 时，会带：
- `request_id`
- `base_id`
- `user_id`
- `file_id`
- `stage=analysis_v2`

随后：
- 外层 `SessionPrompt` 会把 `trace_context` 放进 `ToolContext.metadata`
- `search_law_tool()` 再把这些 metadata 传给 `search_law_runner.search_law(...)`
- 内层 `search_law` skill 会话继续复用这些 trace 信息

### 9.2 search_law audit 返回结构
`search_law_runner.search_law(...)` 最终返回：
- `query`
- `summary`
- `audit`

其中 `audit` 当前包含：
- `session_id`
- `query`
- `skill_name`
- `tool_rounds`
- `parallel_rounds`
- `tool_calls`
  - 每次工具名
  - arguments
  - output
- `trace_context`

### 9.3 analysis 阶段保存位置
analysis 阶段保存时：
- `final.json` 中保留 `law_search`
- `law_search` 内含 `audit`

同时在 `meta.json` 中还额外记录：
- `enabled_tools`
- `law_search_hit_count`
- `law_search_summary`
- `law_search_audit`

这样后续审计时，可以直接在 analysis 阶段产物里还原：
- 这次是否触发了 `search_law`
- 命中了多少法规
- 内层 skill 会话用了哪些 document 工具
- 工具返回了什么

---

## 10. analysis 阶段缓存
阶段名：`analysis_v2`

缓存 hash 目前包含：
- `text_version`
- `summary_version`
- `summary_markdown_hash`
- `prompt_versions`
- `model`
- `enabled_tools=[search_law]`

这保证了：
- 打开/关闭工具会影响缓存
- analysis prompt 更新会影响缓存

---

## 11. final 阶段

函数：`stream_complaint_analysis_v2(...)`

### 11.1 输入
直接消费 analysis 阶段产物：
- `analysis_markdown`
- `reference_content`

### 11.2 执行方式
final 阶段不再调用模型，而是由后端直接从 analysis 结果中提取并组装：
- `3.3 调阅材料清单汇总` → `### 调阅材料清单`
- `3.4 后续工作建议` → `### 核实思路`

### 11.3 SSE
此阶段输出：
- `Answer(phase=final, content=final_markdown)`
- `Reference(phase=final, content=reference_content)`

其中：
- `final_markdown` 为 Markdown 字符串
- `reference_content` 为固定结构数组：

```json
[
  {
    "law_name": "商业银行信用卡业务监督管理办法",
    "articles": ["第七十条", "第七十三条"]
  },
  {
    "law_name": "进一步促进信用卡业务规范健康发展的通知",
    "articles": ["第二条"]
  }
]
```

---

## 12. Done 聚合结果

主编排完成后，发送：
- `Done`

其中 `Done.result` 当前包含：
- `api_kind`
- `base_id`
- `summary_markdown`
- `analysis_markdown`
- `reference_content`
- `final_markdown`
- `scope_context`
- `versions`
  - `text_version`
  - `summary_v2_version`
  - `analysis_v2_version`
  - `complaint_analysis_v2_version`

说明：
- `reference_content` 为法规引用固定结构，供 SSE `Reference.data.content` 和 list 接口 `reference.content` 复用
- `final_markdown` 为从 analysis 结果直接提取出的 Markdown，供 SSE `Answer.data.content` 与 list 接口 `final.content` 复用

---

## 13. 日志风格

整体继续对齐现有 complaint-analysis：
- `[complaint_api] request_input`
- `[complaint_api] response_output`
- `[complaint_api] response_error`
- `log_phase_timing(...)`
- `log_route_timing(...)`

### 当前关键 phase
- `extract_text`
- `summary_v2`
- `analysis_v2`
- `complaint_analysis_v2`

### analysis 阶段额外记录
- `enabled_tools=search_law`
- `law_search_hit_count`
- `law_search_summary`
- `law_search_audit`

### search_law 独立日志
`search_law_runner.py` 还会单独记录：
- user_id
- base_id
- file_id
- stage
- query
- laws 数量
- tool_rounds

---

## 15. curl 调用示例

> 当前后端 **不直接接收 `base_id` 作为请求参数**，而是根据前端传入的 `file_id` / `file_ids` 计算 `base_id`。因此前端调用时，应继续传文件标识，而不是传 `base_id`。

### 15.1 前端常用 JSON 请求示例（单文件）

```bash
curl -N -X POST 'http://127.0.0.1:5001/api/complants/complaint-analysis-v2' \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "test_user",
    "file_id": "your-file-id.pdf",
    "force": false,
    "scope": "default",
    "reporter_name": "张三",
    "reported_entities": ["某支付机构"]
  }'
```

### 15.2 前端常用 JSON 请求示例（多文件合案）

```bash
curl -N -X POST 'http://127.0.0.1:5001/api/complants/complaint-analysis-v2' \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "test_user",
    "file_ids": ["file-a.pdf", "file-b.pdf"],
    "force": false,
    "scope": "default",
    "reporter_name": "张三",
    "reported_entities": ["某支付机构"]
  }'
```

说明：
- 前端场景下通常不走上传举报件文件，而是传已有的 `file_id` / `file_ids`。
- `base_id` 会由后端基于 `file_id` / `file_ids` 自动计算。
- `force=false` 更贴近前端正常调用；只有在你想强制重跑时才设为 `true`。

### 15.3 预期观察点

执行后建议重点观察：
- 是否按顺序收到 `Start / Reasoning / Acting / Answer / Reference / Done`
- analysis 阶段是否明显比 summary/final 阶段耗时更长（因为会触发 `search_law`）
- analysis 阶段 `Reference.data.content` 是否返回法规引用固定结构数组
- final 阶段 `Answer.data.content` 是否直接返回 Markdown
- 最终 `Done.result` 中是否包含：
  - `summary_markdown`
  - `analysis_markdown`
  - `reference_content`
  - `final_markdown`
- analysis 阶段落盘产物中是否存在：
  - `law_search`
  - `reference_content`
  - `law_search.audit`
  - `meta.json` 中的 `law_search_audit`

### 15.4 SSE data 结构

#### Reference 事件

```json
{
  "phase": "analysis",
  "content": [
    {
      "law_name": "商业银行信用卡业务监督管理办法",
      "articles": ["第七十条", "第七十三条"]
    },
    {
      "law_name": "进一步促进信用卡业务规范健康发展的通知",
      "articles": ["第二条"]
    }
  ],
  "ts": "2026-06-08T12:00:00Z"
}
```

说明：
- `phase` 可能为 `analysis` 或 `final`
- `content` 为固定结构数组，每项表示一部法律及其命中的多个条款

#### Answer(final) 事件

```json
{
  "phase": "final",
  "content": "### 调阅材料清单\n\n| 序号 | 调阅对象 | 调阅材料 | 对应举报事项 | 法定义务来源 | 核实要点 |\n|------|---------|---------|-------------|-------------|---------|\n| 1 | 某银行 | 开户资料 | 事项1 | 《xx办法》第x条 | 核对开户真实性 |\n\n### 核实思路\n\n1. 优先核查开户资料真实性。",
  "ts": "2026-06-08T12:00:00Z"
}
```
