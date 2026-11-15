# Agent Middleware

create_agent 中间件。按装配顺序承担：消息完整性修复、工具调用归一化与历史修复、执行围栏、
工具结果卸载（大结果写入隐藏缓存并返回 result_path）、上下文压缩与预算控制、图片注入、
steering 插话、工具计时与单会话调用频率告警、历史消息时间。

约定：中间件不直接触达工作区文件系统，统一经 `agent.workspace.runtime` 的运行时门面；
新增中间件需评估与 `tool_result`、`context_usage` 的先后依赖。

工具调用频率告警（`tool_call_rate_alert.py`）按会话维护滑动窗口计数，窗口内调用数超过
`CHAT_DATA_TOOL_CALL_RATE_ALERT_THRESHOLD`（默认 100 次 / 60 秒）时写 alarm 日志，同一窗口
内每个会话最多一条；只统计通过租约围栏的真实调用，且任何统计异常都不影响工具执行。

## 后台子智能体

`subagent` 工具默认 `background=true`，转入持久任务账本（见 `agent/run/README.md`），立即返回
`task_id`；执行体在独立 asyncio task 中做子图心跳、超时与取消观察。同一个 `subagent` 工具
用 `action` 统一管理后台任务：`list` 列任务、`check` 查单个、`wait` 等终态、`cancel` 取消；
都按当前会话与用户隔离读取账本，不依赖进程内注册表。`check` 对终态任务返回完整
报告；超长报告会落盘并返回 `result_path`，可直接用 `file_read` 读取完整正文，
不对报告做分页。通知里的报告只是预览，不代表任务结论被截断。

同步模式（`start` 或 `follow_up` 传 `background=false`）同样登记 `execution_mode=sync`
的任务行，并在任务详情中展示与后台任务一致的过程、报告、上下文与 token 用量；区别只在
执行体阻塞当前轮、结果直接作为 ToolMessage 返回，并且收口后不生成完成通知、不自动唤醒
主会话。追问的同步/后台模式可逐轮切换，但始终沿用同一 `agent_session_id`、线性
`parent_task_id` 与 `turn_index`。同步过程事件同时写父对话流与任务详情流，父 Run 内
打开的详情页不会出现空过程。

每个主对话创建的子智能体总数（同步与后台任务都计入）由
`CHAT_DATA_SUBAGENT_MAX_SESSIONS_PER_CONVERSATION` 限制；该额度按
`agent_session_id` 去重，追问沿用原会话不占新额度。任务相似时，提示词要求主智能体
先 `subagent(action="list")` 找到已有子智能体并用 `follow_up` 续写；相似度不做代码层
匹配，避免误合并不同目标或交付物。
