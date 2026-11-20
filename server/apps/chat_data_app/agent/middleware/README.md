# Agent Middleware

create_agent 中间件。按装配顺序承担：消息完整性修复、工具调用归一化与历史修复、执行围栏、
工具结果卸载（大结果写入隐藏缓存并返回 result_path）、上下文压缩与预算控制、图片注入、
steering 插话、工具计时与单会话调用频率告警、历史消息时间。

约定：中间件不直接触达工作区文件系统，统一经 `agent.workspace.runtime` 的运行时门面；
新增中间件需评估与 `tool_result`、`context_usage` 的先后依赖。

装配统一走 `assembly.py` 的「规范顺序表 + 槽位」原语：主图用
`MIDDLEWARE_CANONICAL_ORDER`，子图用 `SUBAGENT_MIDDLEWARE_CANONICAL_ORDER`。
新增中间件只需在对应顺序表登记，不再手写 `insert` / `append`；子图的租约围栏
固定在序列最外层，避免租约丢失被 `ToolErrorMiddleware` 转成普通工具失败。

工具调用频率告警（`tool_call_rate_alert.py`）按会话维护滑动窗口计数，窗口内调用数超过
`CHAT_DATA_TOOL_CALL_RATE_ALERT_THRESHOLD`（默认 100 次 / 60 秒）时写 alarm 日志，同一窗口
内每个会话最多一条；只统计通过租约围栏的真实调用，且任何统计异常都不影响工具执行。

## 后台子智能体

`subagent` 工具默认 `background=true`，转入持久任务账本（见 `agent/run/README.md`），立即返回
`task_id`；执行体在独立 asyncio task 中做子图心跳、超时与取消观察。同一个 `subagent` 工具
用 `manage.action` 统一管理后台任务：`list` 列任务、`check` 查单个、`wait` 等终态、`cancel` 取消；
都按当前会话与用户隔离读取账本，不依赖进程内注册表。`check` 对终态任务返回完整
报告；超长报告会落盘并返回 `result_path`，可直接用 `file_read` 读取完整正文，
不对报告做分页。通知里的报告只是预览，不代表任务结论被截断。

同步模式（`dispatch` 或 `send` 开启新轮时传 `background=false`）同样登记 `execution_mode=sync`
的任务行，并在任务详情中展示与后台任务一致的过程、报告、上下文与 token 用量；区别只在
执行体阻塞当前轮、结果直接作为 ToolMessage 返回，并且收口后不生成完成通知、不自动唤醒
主会话。追问的同步/后台模式可逐轮切换，但始终沿用同一 `agent_session_id`、线性
`parent_task_id` 与 `turn_index`。同步过程事件同时写父对话流与任务详情流，父 Run 内
打开的详情页不会出现空过程。

同步任务不挂子图 checkpointer：每次同步派发都写检查点会拖慢主链路，而同步任务本身
没有暂停/审批的恢复需求。因此运行中详情页首连拿不到检查点快照，会回退到事件流全量
重放；收口后的历史过程仍由 `result_meta.subagent_trail` 还原。后台任务保持独立
checkpointer，运行中首连走快照 + 游标续传。

后台执行器同时按任务注册为失联恢复 runner（生命周期限于创建它的进程）。pending 任务启动
前会把身份、数据域和工作区白名单上下文持久化到任务行；running 任务租约过期后从同一
`agent_session_id + task_id` 的检查点续跑，并通过 `Command(update=...)` 轮换子图 owner。
进程重启后没有该任务的 runner，回收器等待 120 秒后按 `lost` 收口。审批恢复也会同时更新 owner。后台子图
使用自己的 task owner 工具围栏，不依赖已经结束的父 Run；旧执行体失去归属后不能继续调用
工具。写型任务若停在工具结果尚未落入检查点的位置，副作用是否发生无法确定，任务会以
`lost` fail-closed，不会盲目重放；explore 的只读工具可以重放。恢复失败且没有完整最终消息时
保留原有子会话快照，不用本轮任务书覆盖历史。

控制动作是否可用由 `contracts/subagent_capabilities.py` 按「执行模式 × 状态」统一推导，
`list`/`check` 输出会带「可操作」。同步任务只支持取消（打断当前轮）和终态追问，不支持
插话；后台任务按状态开放取消、插话与追问。审批中断/恢复只保留内部能力，不再作为
模型可见参数；等待审批的存量任务只能取消后重新派发。

所有 `subagent` 动作用 `subagent_tool_schema.py` 的三组互斥参数契约分发：`SubagentToolArgs`
校验器强制必选且只选一组（`dispatch`/`send`/`manage`），执行体沿用现有同步/后台
适配器。契约层统一的是“命令进入与能力边界”，不重写同步/后台各自的终态收口语义。

关闭子智能体是**用户侧动作**，不是 `subagent` 工具能力：用户在管理面板关闭终态且无活跃
轮次的逻辑子会话后，历史仍可读，但 `send` 会拒绝，能力矩阵不再显示追问；关闭同时
释放当前主对话的一个子智能体额度，供后续新派发使用。会话状态来自
`chat_data_subagent_session`，不是任务状态，不能用取消代替关闭。主智能体只能提示用户
关闭不再使用的子智能体，不能自行关闭。

每个主对话创建的子智能体总数（同步与后台任务都计入）由
`CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION` 限制，活跃任务数由
`CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE` 限制；子智能体总额度按 `agent_session_id` 去重，
追问沿用原会话不占新额度，已关闭会话不计入总额度。任务相似时，提示词要求主智能体先 `subagent(manage={"action": "list"})`
找到已有子智能体并用 `send` 续写；相似度不做代码层匹配，避免误合并不同目标或交付物。
两项限制都只作用于当前主对话，不同主对话互不影响。

并发写采用软控制：会写 workspace 的任务在 `dispatch` 时可选声明 `write_scopes`
（工作区相对文件或目录前缀，例如 `outputs/word`、`outputs/ppt`）；终态追问沿用上一轮范围，
不支持在 `send` 中改写，范围变化应重新 `dispatch`。`workspace:*` 表示
shell、git、构建等全局独占操作。任务账本保存该声明，`list` 会展示活跃任务的写范围及
重叠提示；主智能体据此决定并行还是串行。系统不建写租约、不加全局文件锁，也不阻断
重叠派发，最终合并与 diff review 由主智能体负责。未声明写范围的写任务视为可能冲突，
不应与其他写任务并行。
