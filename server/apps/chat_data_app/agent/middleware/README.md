# Agent Middleware

create_agent 中间件。按装配顺序承担：消息完整性修复、工具调用归一化与历史修复、执行围栏、
工具结果卸载（大结果写入隐藏缓存并返回 result_path）、上下文压缩与预算控制、图片注入、
steering 插话、工具计时与单会话调用频率告警、历史消息时间。

约定：中间件不直接触达工作区文件系统，统一经 `agent.workspace.runtime` 的运行时门面；
新增中间件需评估与 `tool_result`、`context_usage` 的先后依赖。

工具调用频率告警（`tool_call_rate_alert.py`）按会话维护滑动窗口计数，窗口内调用数超过
`CHAT_DATA_TOOL_CALL_RATE_ALERT_THRESHOLD`（默认 100 次 / 60 秒）时写 alarm 日志，同一窗口
内每个会话最多一条；只统计通过租约围栏的真实调用，且任何统计异常都不影响工具执行。
