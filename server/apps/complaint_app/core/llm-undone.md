# complaint_app LLM 调用待完善清单

> 说明：仅覆盖 OpenAI-style 调用点（含 AsyncOpenAI / OpenAI SDK）。
> 目的：将“最合理的调用方式”落地为仓库级待办。

## 全局约定（建议统一到 core 或 utils）

1. **统一重试与退避**
   - 对网络错误 / 5xx / 429 做指数退避重试。
   - 限定最大重试次数与总耗时。

2. **统一超时策略**
   - 明确 connect/read/total 超时配置，并在所有调用点一致。
   - SDK 侧 timeout 与 httpx client timeout 要一致或有清晰优先级。

3. **统一取消机制**
   - 流式调用在迭代时检查取消标记并及时中断。
   - 取消后落盘或写入状态，避免前端“悬挂”。

4. **统一日志字段**
   - 记录模型名、耗时、请求 id（如有）、输出长度。
   - 禁止记录敏感原文或超长内容。

5. **统一输出兜底**
   - JSON/结构化输出需要校验与失败兜底。
   - 解析失败时返回空结构 + 可读错误提示。


## 分文件待补齐

### services/chats/chat.py
- `chat.completions.create` 失败重试/退避。
- 流式中断时写入明确状态。
- 确保 `message_end`/`done` 只在流结束后发送一次。
- 增加模型/耗时/输出长度日志。

### services/summary/llm_summary.py
- 增加取消检测（与 chat 同步）。
- 超大输出截断策略（避免异常大 payload）。

### services/summary/conversation_title.py
- 流式版本补重试/退避。
- 空结果写入明确错误状态。

### services/text/text_pipeline.py (_stream_tool_summary)
- `create` 失败重试/退避。
- 增加取消检测。
- delta 拼接异常的兜底处理。

### services/analysis_agent/agent.py
- `complaint_extract_items` / `_match_experience_single` / `complaint_final_conclusion` 补重试/超时。
- 结构化输出解析失败的兜底处理。
- 增加取消检测。

### services/analysis_agent/llm_fc.py
- `api_key/base_url` 传入参数目前未使用，确认是否需要生效。
- 失败重试 + 输出兜底。

### tools/image_text_extractor_llm.py
- SDK timeout 统一策略（client 级 vs 调用级）。
- OCR 取消/超时方案（目前已异步化，但无显式取消/超时控制）。
