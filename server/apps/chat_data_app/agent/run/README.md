# Agent Run

Run 级运行时服务：事件流（Redis Stream 直播 + MySQL 消息级快照）、调度循环、inbox 账本、
会话事件服务。持久化分层、游标协议与断点恢复矩阵的规格契约见
`services/agent_run/README.md`——修改事件流相关代码前先读该规格。
