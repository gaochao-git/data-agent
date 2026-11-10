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
