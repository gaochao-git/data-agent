# Agent Run

Run 级运行时服务：事件流（Redis Stream 直播 + MySQL 消息级快照）、调度循环、inbox 账本、
会话事件服务、运行参数解析（`completion_options.py`，sql options → CompletionRunOptions）。持久化分层、游标协议与断点恢复矩阵的规格契约见
`services/agent_run/README.md`——修改事件流相关代码前先读该规格。

## 多机房执行准入

用户对话和定时任务共用 `executor_gate.py` 的同一套判断，不允许各自复制白名单解析：

- 执行单元身份：`CHAT_DATA_NODE_ID`，必须显式配置，不回退 hostname；同一机房节点可共用同一个名字，需要单节点隔离时再配唯一节点名；
- 执行名单：`CHAT_DATA_AGENT_EXECUTOR_NODES`（后台运行配置可动态修改，空表示不限制）；
- 判定：名单非空且本节点不在名单内时禁止执行；名单读取失败按 fail-close 处理；
- 入口：`AgentRunRuntime` 的轮询/执行、`AgentScheduleRuntime` 的 tick/立即触发、`trigger-now` 路由都调用同一判断。

Standby/C 节点不参与 pending 轮询、过期 Run 回收、定时任务调度和立即触发；切换 Active 机房时必须同步停止旧执行、切换执行名单并等待配置缓存生效。

## 后台子智能体任务

`task(background=true)` 的任务真值落在 `chat_data_agent_inbox` 的 `record_type='task'` 行，
进程内只保留 asyncio 执行句柄。任务状态：`pending → running → completed/failed/timeout/
cancelled/interrupted/lost`；`cancelling` 是跨 worker 的取消请求，执行节点在下一次心跳
（固定 5 秒）停止协程并收口。租约过期由
活动节点的 `agent_run_runtime` 统一回收为 `lost`，避免多副本重复判断。

- 完成通知写成隐藏的 `next-step` inbox 消息（`message_id=ntf-<task_id>`，`hide_from_ui=true`）；
  会话空闲时用 `adopt_pending_messages=False + selected_message_id` 创建通知 Run，只领取
  自己的通知，不劫持用户排队消息。隐藏消息不进用户历史/气泡。
- 通知会**自动唤醒**通知 Run：会话空闲时由调度器直接创建（`query` 为空、
  `disable_user_interaction=true`），会占用一次模型轮次但不产生用户气泡；会话忙时挂到
  当前 Run 的下一轮消息。用户不发消息也会推进，不需要手动轮询。
- 任务结论始终先落在账本的 `record_content`；通知里只是预览。终态任务用
  `task(action="check", task_id=...)` 一次读取完整报告。
- 报告超过父上下文预算或 `TOOL_RESULT_OFFLOAD_SIZE` 时，账本仍保存全文，同时额外落一份
  隐藏文件并写入 `result_meta.report_path`；通知/`task(action="check")` 会返回 `result_path`，
  主智能体直接用 `file_read` 读取完整正文，不再对报告做分页。
- 状态口径：终态优先，收口时 `progress.phase` 同步改为终态；`progress.tool_calls` 是累计
  调用过的工具总数，`progress.running_tools` 才是当前仍在执行的工具数。
- 实时推送走 SSE `subagent_task_updated`（只带紧凑快照，不带报告）；断线、空闲或刷新后由
  `GET /conversations/{id}/subagent-tasks` 轮询/列表恢复。详情接口返回完整报告与
  `result_meta`（过程快照、上下文/token 用量、压缩记录），列表接口不返回大字段。
