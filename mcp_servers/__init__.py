"""data-agent 自维护的 MCP server 集合。

当前承载故障分析插件（plugins/fault-analysis）声明的诊断类 server：
- ``db_query_server``：MySQL 只读诊断（streamable-http，默认 :3001）
- ``ssh_exec_server``：Linux 只读巡检（streamable-http，默认 :3002）

这些是独立进程，不属于 FastAPI 应用（``apps/chat_data_app``）；
启动方式见各 server 的 ``python -m`` 入口与 README。
"""
