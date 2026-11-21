# 超步失败轮次步数保留 — 验证记录

- 日期：2026-11-21 12:49~12:55
- 改动：提交 `6af3d663 fix(chat-data): 保留执行失败时的图步数`（工作区验证时为用户未提交改动，验证期间用户提交）
- 背景：上一轮（`2026-11-21-supersteps.md`）发现把 `CHAT_DATA_AGENT_RECURSION_LIMIT` 调到 7 时，`GraphRecursionError` 中断回答、失败气泡上**步数为空**。本次优化补上失败轮次的步数展示与持久化。

## 改动内容（读代码确认）
- 后端 `agent_run_support.py`：新增 `_failure_generation_stats()`（从 stream_adapter.supersteps + config.recursion_limit 取**已完成**步数，不补造未完成步骤）和 `_merge_generation_stats_into_latest_ai()`（把统计补到最后一条 AI 消息的 additional_kwargs）；`_persist_partial_agent_turn_history()` 失败补写时带上 generation_stats。
- 前端 `Conversation.vue`：新增 `attachLiveSuperstepsOnFailure()`，在流式 error 回调（无 message_end 收口）把实时步数落到当前助手气泡，由 stopLive 清理；`submitAnalysisQuery` 的两处错误出口都调用。
- 单测：后端 +1（`test_partial_history_keeps_failure_supersteps`）、前端 +1（失败消息 `[data-message-steps]` 显示 `1 / 1000 步`）。

## 验证结果：全部 PASS
| 项 | 方式 | 结果 |
|---|---|---|
| 后端单测 | `pytest test_run_supersteps.py` | **6 passed**（新增失败持久化用例通过） |
| 前端单测 | vitest 3 个 spec（generationSpeed/SubagentTrail/ConversationMessageList） | **53 passed** |
| type-check | `vue-tsc --build --force` | exit 0 |
| **失败实时展示** | limit=7 真点问候 → `GraphRecursionError` | 失败气泡显示 **`3 / 7 步`**，错误探针 0（上一轮此处为空） |
| **后端持久化** | 读 `GET /conversations/{id}` | 消息 `generation_stats={supersteps:3, recursion_limit:7}` 已落库 |
| **失败原因确认** | 后端日志 | `GraphRecursionError: Recursion limit of 7 reached`（12:51:43） |
| **历史回放** | 重载页面重新打开失败会话 | 仍显示 `3 / 7 步`（证明读的是持久化数据而非仅内存实时值） |
| **配置改回** | 软删覆盖行 → 等 32s | runtime=1000，新会话底栏 `3 / 1000 步`，错误探针 0 |

## 关键证据
- 失败会话 id `8b52098d-2fcd-445d-97c5-35249fd650a5`，标题「简单打招呼咨询」；消息落库 `generation_stats: {supersteps: 3, recursion_limit: 7}`。
- 日志：`langgraph.errors.GraphRecursionError: Recursion limit of 7 reached without hitting a stop condition`，随后 `智能问数执行失败结束`。
- 截图：`.workbuddy/screenshots/supersteps-failure-verify-20261121/failed-with-steps-7.png`。

## 口径与恢复
- 配置优先级 `chat_data_setting` 覆盖行 > env(1000) > 默认(40)，EFFECT_NEXT_RUN，缓存 TTL 30s（改值后等 32s）。
- 临时覆盖行 id=999999 用 `delete_flag=1` 软删除恢复（fxcjg_rw 无 DELETE 权限，项目惯例）。**DB 无残留、.env-dev 未动、未改产品代码**。
- 失败轮次只取已完成的图步数（supersteps=3），不补造未完成步骤；终态与非失败轮次一致以落库 generation_stats 为准。

---

## 追加：超步失败文案专门化（13:10~13:14，工作区未提交改动）
- 背景：上一轮发现 `GraphRecursionError` 落到 `_model_failure_user_message()` 兜底「智能问数执行失败，请稍后重试。」，用户看不到"步数超限"这个真因。
- 改动（读 diff 确认）：`agent_stream_events.py` 新增 `_graph_recursion_failure_message()`——`isinstance(exc, GraphRecursionError)` 后用正则 `Recursion limit of (\d+)` 提取上限，返回「本轮执行已达到 N 步上限，任务被中止。请缩小任务范围、拆分任务，或新开会话后重试。」；无匹配数字时退回「步数上限」泛化文案；`agent_stream.py::_model_failure_user_message()` 兜底前先调它（`_graph_recursion_failure_message(exc) or 兜底`）。单测 +2（映射 + SSE 流内文案）。
- 结果全 PASS：
  - `pytest test_agent_stream.py` **59 passed**（含 2 新用例）。
  - 真点 limit=7 问候 → 前端报错文案变为 **「本轮执行已达到 7 步上限，任务被中止。请缩小任务范围、拆分任务，或新开会话后重试。」**，旧兜底文案**不再出现**；步数 `3 / 7 步` 同屏正常；错误探针 0。
  - 后端日志确认失败原因 `GraphRecursionError: Recursion limit of 7 reached`。
  - 历史回放（重载重新打开会话）：新文案在、旧文案无、步数 `3 / 7 步` 一致。
  - 恢复：软删覆盖行 → runtime=1000。DB 无残留、未改产品代码。
- 验证环境：服务端进程 13:11:22/13:11:59 启动，晚于代码 mtime 13:10:36，新代码已加载（worker 刚起缓存为空，插配置后无需等 30s 即生效）。

---

## 追加 2：步数口径改为真实引擎步（13:36~13:44，工作区未提交改动）
- 背景：上一轮发现 `3 / 7 步` 分子分母口径不一致——分子原是 `values` 流帧数（跳过回显/interrupt），分母是 LangGraph 引擎 tick 上限（Pregel `self.stop = step + recursion_limit + 1`，middleware hook 各消耗一 tick）。另一智能体按此改造。
- 改动（读 diff 确认）：
  - `agent_stream_events.py` 新增 `_GraphStepTracker(AsyncCallbackHandler)`：`wrap_config()` 给 astream 配置追加 callback；`on_chain_start()` 取 `metadata.langgraph_step`，排除子图（`langgraph_checkpoint_ns` 含 `|`），以首个节点步为基线归一到本轮 1，同一步并行节点取 max 只计一次。
  - `agent_stream.py`：`_AgentSseStreamAdapter` 用 tracker，`_sync_graph_steps()` 在每次事件/流结束后把真实步数转成 run_steps（同值只发一次），异常路径也先同步再抛；删除原 `_track_superstep`（values 计数）与 `_values_echo_seen`。
  - 单测：`test_run_supersteps.py` 改写计数用例 + 新增 `test_adapter_stream_emits_true_graph_steps_at_recursion_limit`（真 create_agent + 6 个 middleware + fake model，limit=7，验证步数序列 4→7、终值 7）。
- 结果全 PASS：
  - `pytest test_run_supersteps.py + test_agent_stream.py` **66 passed**。
  - 真点 limit=7 问候 → 步数显示 **`7 / 7 步`**（上版是 `3 / 7 步`），**口径对齐**；报错文案仍是「已达到 7 步上限…」；错误探针 0。
  - 后端日志 `GraphRecursionError: Recursion limit of 7 reached`；落库 `generation_stats: {supersteps: 7, recursion_limit: 7}`；历史回放步数 `7 / 7 步`。
  - 恢复：软删覆盖行 → runtime=1000。
- 注意（非本次回归）：错误文案**不落库**（`assistant_message.error` 为 None），它是流式 error 事件实时写前端气泡的，真正重载后历史回放不显示错误文案——这是既有设计。上一轮记的"回放有新文案"是不完全重载、气泡还在内存所致，已订正。
- 验证环境：服务进程 13:42 启动，晚于代码 mtime 13:37，新代码已加载。

---

## 追加 3：失败原因持久化（另一智能体改动，工作区未提交 14:00，14:05-14:09 验证）
- 背景：追加 2 的「注意」项——失败文案只实时写前端气泡、重载后历史回放丢失。本次改动把终止原因落成历史事件，刷新/历史会话都能看到。
- 改动（读 diff 确认）：
  - 后端 `conversation_event_service.py` 新增 `EVENT_TURN_ERROR="turn_error"` + `persist_turn_error()`：写一条历史事件 payload={error, timestamp, error_type?, supersteps?, recursion_limit?}，`event_key=turn_error:{turn_id}:{run_id}` 保证同一 Run 重复收口幂等、重试新 Run 追加。只进历史不进模型消息。
  - `agent_run_support.py` 新增 `_TurnFailureRequest` + `_persist_agent_failure()`：失败收口时尽力落库（失败不阻断原始异常），返回 error SSE。
  - `agent_stream.py`：`_AgentRunPreparationError` / `ContextInputLimitExceededError` / 通用 `Exception` 三个失败出口统一改走 `_persist_agent_failure()`；`_model_failure_user_message` 等函数移到 `agent_stream_events.py`。
  - 前端 `useConversationHistory.ts`：`buildHistoryMessagesFromEvents` 处理 `turn_error` → 还原到当前助手气泡 `assistant.error` 并置 `isStreaming=false`；无前置助手消息时造 error-only 气泡；后续 `assistant_message` 会清旧 error（重试成功场景）。
- 结果全 PASS：
  - 后端单测 4 文件（conversation_event_service + agent_run_support_branch_coverage + agent_stream + test_run_supersteps）**136 passed**；前端 `useConversationHistory.spec.ts` **23 passed**（新增 3 例：还原错误/仅错误气泡/重试清除）；`vue-tsc --build --force` exit 0。
  - **实时**：limit=7 新会话问候 → 气泡显示「本轮执行已达到 7 步上限…」+ `7 / 7 步`，错误探针 0。
  - **落库实证**：`GET /conversations/{id}` 的 `history_events` 含 1 条 `turn_error`，payload={error, error_type:GraphRecursionError, supersteps:7, recursion_limit:7, timestamp}；消息 `assistant.error=None`（错误不进模型上下文，设计正确）。
  - **刷新回放**：刷新页面回空态→从侧栏重开会话，错误文案 + `7 / 7 步` 均从落库还原（上一版此处错误消失）。
  - **跨会话往返**：切到「月报2」（无错误）再切回，错误仍稳定还原；错误全程探针 0。
  - **runtime 恢复**：软删覆盖行 → 等 33s → 新会话底栏 `7 / 1000 步`、无错误、探针 0。
- 失败会话 id `14563bcb-8304-4934-99b5-0b2465778d13`（标题「你好简单打招呼问候」）；截图 `.workbuddy/screenshots/turn-error-persist-20261121/history-replay-error.png`。
- 未端到端覆盖：单测里的「重试成功后清除旧 error」支路（需同会话 error→成功 assistant_message 事件序列，逻辑层已由 3 个前端单测覆盖，未做真机注入以免污染共享会话数据）。
- 环境：worker 14:02:50 启动，晚于改动 mtime 14:00:21，新代码已加载。DB 覆盖行已软删无残留、.env-dev 未动、未改产品代码。
