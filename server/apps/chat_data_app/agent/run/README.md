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

`subagent` 的任务真值落在 `chat_data_agent_inbox` 的 `record_type='task'` 行，
进程内只保留 asyncio 执行句柄。默认 `background=true`；显式 `background=false` 的同步执行
同样落一行，但 `execution_mode=sync`、`notify_on_finish=false`，结果直接回到当前轮，不创建
完成通知 Run。任务状态：`pending → running → completed/failed/timeout/
cancelled/interrupted/lost`；`cancelling` 是跨 worker 的取消请求，执行节点在下一次心跳
（固定 5 秒）停止协程并收口。租约过期由活动节点的 `agent_run_runtime` 统一扫描，避免多副本
重复判断。后台 pending/running 任务通过「旧 owner + 旧状态 + 旧租约」原子认领后自动接管，
同一 `task_id` 最多恢复两次，并在 meta 记录 `execution_attempt/recovery_count`。恢复执行器
按任务绑定在创建它的进程内；进程重启后没有该 runner，最多等待 120 秒，超时仍无执行器
则收口为 `lost`，不会让任务无限期停在 running。pending 使用启动前持久化的白名单上下文
重新启动，running 从独立检查点续跑；缺失检查点、超过恢复上限或无法确认工具副作用时仍收口为 `lost`。
同步任务、cancelling、waiting_approval 和普通终态不进入自动崩溃恢复。

- 所有需要主智能体感知的生命周期变化统一写入 `chat_data_subagent_event` outbox：
  `terminal`、`waiting_approval`、`resumed`，以及未来用户直聊子智能体的
  `user_message`。状态变更与事件同一事务落库，投递状态按事件独立记录，不再受
  「一任务一通知」限制，也不再依赖任务行的 `consumed_time` 表达全部语义。
- 事件有三种投递策略：`wake` 在会话空闲时创建隐藏通知 Run 自动唤醒，忙时挂当前 Run；
  `attach` 只挂活跃 Run，不唤醒空闲会话；`silent` 只留审计。审批和终态用 `wake`，
  审批恢复与用户直聊事件用 `attach`。
- 通知写成隐藏的 `next-step` inbox 消息（`message_id=evt-<hash>`，`hide_from_ui=true`）；
  会话空闲时用 `adopt_pending_messages=False + selected_message_id` 创建通知 Run，只领取
  自己的通知，不劫持用户排队消息。隐藏消息不进用户历史/气泡。
- 通知 Run 的 `runtime_context` 携带 `subagent_notification_event_id/event_type/task_id/status`，
  会话空闲时由调度器直接创建（`query` 为空、`disable_user_interaction=true`），会占用一次
  模型轮次但不产生用户气泡；会话忙时挂到当前 Run 的下一轮消息。用户不发消息也会推进。
- 投递成功后事件标记 `delivered` 并维持终态任务 `consumed_time/notified_at` 口径；失败保留
  `pending` 由调度重试，超过重试上限标记 `failed`。`subagent(manage={"action": "check"/"wait"})` 已读到
  终态会取消尚未投递的终态事件和尚未消费的隐藏消息；恢复/取消会让旧的审批事件失效。
- 同步任务仍在当前轮返回，不生成终态事件或唤醒 Run。
- 任务结论始终先落在账本的 `record_content`；通知里只是预览。终态任务用
  `subagent(manage={"action": "check", "task_id": ...})` 一次读取完整报告。
- 报告超过父上下文预算或 `TOOL_RESULT_OFFLOAD_SIZE` 时，账本仍保存全文，同时额外落一份
  隐藏文件并写入 `result_meta.report_path`；通知/`subagent(manage={"action": "check"})` 会返回 `result_path`，
  主智能体直接用 `file_read` 读取完整正文，不再对报告做分页。
- 状态口径：终态优先，收口时 `progress.phase` 同步改为终态；`progress.tool_calls` 是累计
  调用过的工具总数，`progress.running_tools` 才是当前仍在执行的工具数。
- 运行期上下文快照写入 `progress.context_usage` 随心跳持久化：列表接口不返回
  `result_meta`，前端任务卡靠这份轻量快照展示运行中、取消、超时和失联任务的上下文占比。
- 实时过程流对同步/后台任务同构：任务详情都从任务事件流读取过程；同步任务额外把同一批
  事件写回父对话流，保证当前轮的工具卡与详情页都能实时展示。
- 实时推送走 SSE `subagent_task_updated`（只带紧凑快照，不带报告）；断线、空闲或刷新后由
  `GET /conversations/{id}/subagent-tasks` 轮询/列表恢复。详情接口返回完整报告与
  `result_meta`（过程快照、上下文/token 用量、压缩记录），列表接口不返回大字段。

### 多轮追问（send）

子智能体按“独立子会话 + 每轮一条任务”组织，不是把上一轮报告拼进新任务书：

- 首轮 `task_id` 同时是 `root_task_id`（兼容字段）与 `agent_session_id`；追问生成新的
  `task_id`，通过 `parent_task_id` 指向子会话最新一轮，`turn_index` 从 1 线性递增。
- 完整消息历史保存在 `chat_data_subagent_session`：`session_id` 唯一，`messages_json`
  在每轮收口时整体覆盖，避免按轮次追加造成 O(n²) 增长。任务账本仍每轮一行，便于审计、
  取消和追溯报告。
- 主智能体统一用 `subagent(send={"task_id": ..., "message": ...})` 续聊：对终态任务
  自动开启下一轮，只允许对终态任务发起，同一子会话同时只能有一个活跃轮次。
  运行中的同一调用会自动转为插话；停止用 `subagent(manage={"action": "cancel", "task_id": ...})`。
- 每个主对话新建子智能体的总数受 `CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION`
  约束，按 `agent_session_id` 去重；追问复用原会话不占新额度。该限制只兜底“无限新开”，
  相似任务是否合并由提示词约束主智能体判断，不在后端硬编码任务相似度。
- 单个主对话的活跃任务数受 `CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE` 约束。创建任务时先锁定
  当前主对话行，再在该事务内完成容量检查与任务写入；不同主对话互不影响，不设全局或节点容量。
- 即使用户从旧任务卡发起追问，后端也会锚定该子会话最新一轮作为 `parent_task_id`，上下文
  取当前会话快照，保证轮次与任务链不分叉。
- 会话历史单条封顶 100k 字符、总量封顶 2M 字符；超限时从旧到新丢弃并插入省略说明。旧任务
  没有快照时退回“上一轮任务书 + 最终报告”，仍可继续追问一轮。
- 新增表 DDL 只维护在 `server/apps/chat_data_app/next_version.md`，由发布流程/DBA 上线前
  手工执行；应用启动不会自动建表。生产库未执行建表时，任务收口与读取都会降级为无快照模式，
  不阻断主任务；但追问只能使用任务书+报告兜底，部署时应先建表。

### 用户关闭子会话

- 关闭由用户在子智能体管理面板发起，通过
  `POST /conversations/{conversation_id}/subagent-tasks/{task_id}/close-session` 把该任务所属的
  `chat_data_subagent_session` 置为 `session_status=closed` 并记录 `closed_time`；关闭是幂等的。
- 只允许终态且没有活跃轮次的子会话关闭。仍有 `pending/running/cancelling/waiting_approval`
  轮次时拒绝，用户应先等待或取消。关闭后释放当前主对话的一个子智能体额度。
- 关闭不删除历史：`messages_json`、任务账本、报告和过程快照仍可查询；但 `follow_up`
  会在创建新一轮前被拒绝，如需同类任务必须 `start` 新子智能体。
- 关闭状态是会话级事实，不是任务状态；任务卡仍展示各自的终态，能力矩阵在会话关闭后
  不再给出「追问」。主智能体没有关闭工具，只能在额度打满时提示用户去管理面板操作。

### 身份语义（任务 / 会话 / 智能体分层）

任务账本同时承载三种身份，修改字段前必须区分清楚，不能互相复用：

- `conversation_id`：用户可见的主对话，顶层作用域。
- `agent_id`：子智能体实例身份。当前固定一层：`depth=1`、`parent_agent_id` 为空、
  `root_agent_id` 指向自身；这些字段只做稳定标识与审计，不代表支持嵌套派发。
- `agent_session_id`：该智能体的记忆容器，同一智能体的所有轮次共享；等于
  `chat_data_subagent_session.session_id`。
- `task_id`：一次执行轮次，承载租约、取消、审计与报告；`turn_index` 是该智能体内的轮次。

任务 `record_meta` 还保存 `write_scopes`：主智能体在派发会写 workspace 的子任务时声明的
工作区相对文件/目录前缀。它只用于主智能体并发分发判断，不是锁：不阻止重叠任务启动，
不参与租约，也不会自动串行。`list`/`check` 返回该声明及活跃任务重叠提示；
`workspace:*` 表示全局写操作。未声明写范围的写任务按可能冲突处理，由主智能体在派发前
自行串行并承担最终整合。

`parent_task_id` 只表示“任务链上一轮”，**不是**父智能体；父子智能体关系看
`parent_agent_id` / `root_agent_id` / `depth`。旧任务没有新字段时按
`agent_session_id → root_task_id → task_id`、`agent_id → agent_session_id`、
`depth → 1` 的顺序回退，历史数据不需要回填。

### 明确非目标：子智能体不得再派发

- 子图工具白名单不包含 `subagent`，`SubagentMiddleware` 只挂主图；即使主图工具列表
  误带 `subagent`，也会被白名单过滤。相关回归见 `test_tools_registry.py` 与
  `test_data_agent_graph_flow.py`。
- 因此不建设嵌套派发、树形 UI、子树取消、树级预算/查询；`depth` /
  `parent_agent_id` 仅作身份元数据，当前恒为 1 / 空。

后续只做单层模型内的能力增强：

- 已实现：子图独立 checkpointer（线程按 `agent_session_id`、命名空间按
  `task_id`），以及 `waiting_approval` 状态与统一的
  `steer/approve/reject/cancel` 控制命令。账本仍是业务真值，
  checkpoint 只负责执行恢复；审批落在模型调用前的安全点，恢复用
  `Command(resume=...)`。等待审批和审批恢复都会写生命周期事件，按
  wake/attach 策略同步给主会话；用户明确允许/拒绝后由主智能体调用
  `subagent(decide={"task_id": ..., "decision": "allow"/"deny"})` 继续。普通崩溃恢复使用
  `Command(update={subagent_owner_token: ...})`，与审批 resume 是不同语义；两种恢复都会轮换
  子图 owner，后台工具围栏只认当前 task owner，不认旧执行体或父 Run owner。
- 阶段 2：通知聚合、运行期子智能体类型注册与预算配置。
- 阶段 3：单层任务的查询/可观测性增强；API 只暴露智能体视图，不把 inbox
  扁平行泄露给前端。
