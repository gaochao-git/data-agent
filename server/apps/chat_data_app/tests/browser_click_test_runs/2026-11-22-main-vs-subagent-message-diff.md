# 主消息 vs 子消息差异分析 + Qwen3.6 复测（2026-11-22 21:35–21:40）

## 目的
用户问「主的消息和子的消息哪里不一样」：先看代码定位差异，再真浏览器复测验证，不重复造轮子
（复用 11-22 已验证的 fetch+tee 抓帧法与测试会话，未重设计抓帧方案）。

## 代码层差异（先看代码）

| 维度 | 主对话消息 | 子智能体消息 |
|---|---|---|
| 事件名 | `message`（正文）、`thinking`（思考） | `subagent_message` / `subagent_message_delta` / `subagent_thinking_delta` |
| 正文 payload | 整个 chunk 的 `model_dump()`（12 字段：content/additional_kwargs/response_metadata/type/name/id/tool_calls/invalid_tool_calls/usage_metadata/tool_call_chunks/chunk_position/timestamp），由 `agent_stream.py:953-955` `_handle_message_chunk` yield | delta 只有 5 字段：`event_type/message_id/delta/truncated/parent_tool_call_id`（`subagent_stream.py:194-200` `_stream_delta_event`）；完整帧 `subagent_message` 带 `content/streamed/truncated/parent_tool_call_id`，可选 `thinking/thinking_truncated`（`subagent_stream.py:309-320`） |
| 增量方式 | 无独立 delta 事件；前端把每个 chunk 的 content 直接 `+=` 累加到助手正文（`Conversation.vue:1965-1977 appendAssistantContent`） | token 级 delta 事件逐条下发，前端 trail 按 message_id 累加（`subagentTrail.ts:40-58`） |
| 收敛策略 | 无收敛帧；消息结束靠 `message_end` | 完整帧 `subagent_message` 携带 `streamed` 标记 + 内容预览，前端「更长者优先」合并（delta 拼出的全文不被截断预览覆盖），供晚加入订阅者补齐（`subagentTrail.ts:61-102`、`subagent_stream.py:253-256/295-305`） |
| 归属/挂接 | 顶层助手消息 | 均带 `parent_tool_call_id`，挂在外层 subagent 卡片下，不作顶层渲染（`subagent_stream.py:250-251`） |
| 标记清洗 | `sanitize_message_payload`（常规清洗） | 正文 delta 经 `_StreamingToolCallMarkupFilter` 流式剥除裸 `<tool_call>`；完整帧经 `_sanitize_report_text`（`subagent_stream.py:122-134/303-306`） |
| 封顶/限速 | 无预览封顶 | 有 `SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS` / `SUBAGENT_THINKING_PREVIEW_MAX_CHARS` 封顶，`truncated` 标记；speed 采样用封顶前文本（`subagent_stream.py:136-138/191`） |

一句话：**主消息=「每 chunk 全量 dump + 前端累加」，子消息=「token 级精简 delta + 带 streamed 标记的完整帧收敛 + 归属挂接 + 封顶/清洗」**。子消息机制明显更复杂，因为它要支持过程面板的快照+游标订阅、晚加入者补齐、内层工具挂接。

## 真浏览器复测（Qwen3.6-35B）
- `message` 事件实测 payload（4 帧样本）：确为 12 字段完整 model_dump，content 逐 token（"我"→"准备"→"调用"→" sub"），id 全程同一 `lc_run--...`。
- `thinking` 事件实测 payload：`{content, timestamp}` 仅 2 字段（"用户"→"要求"），与主消息正文 payload 形态不同，印证代码。
- 子任务过程面板实测：`subagent_thinking_delta` 1713 帧、`subagent_message_delta` 1 帧、`subagent_message` 1 帧，页面正文完整渲染（SQL 结果 + 6 状态业务含义），无 token 堆叠、无卡死。Qwen3.6 短正文倾向单 chunk 输出，故 message_delta 帧少——内容形态差异，非代码问题（上轮长报告任务曾抓到 1400 帧 message_delta）。
- 错误探针全程 0 条。

## 结论
代码差异与实测一致，两类消息机制不同属**设计如此**（共享磁盘快照/游标订阅场景需要子消息的收敛与归属机制），非缺陷。本轮未改产品代码，未发现新问题。
