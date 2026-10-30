# mcp_servers — data-agent 自维护的 MCP server 集合

故障分析插件（`plugins/fault-analysis/plugin.json`）声明的诊断类 MCP server，
从 fullstack-langgraph 平移至本仓库自维护，基于官方 `mcp==2.2.0` 的
`MCPServer`（MCP 协议 2026-07-28 世代 SDK）实现，streamable-http 传输。

| server | 端口（默认） | 工具 | 职责 |
| --- | --- | --- | --- |
| `db_query_server` | `MCP_DB_QUERY_PORT`（3001） | `db_query` | MySQL 只读诊断 |
| `ssh_exec_server` | `MCP_SSH_EXEC_PORT`（3002） | `ssh_exec` | Linux 只读巡检 |

单工具命令模型：

```text
db_query(action="help")     → 查看各 action 的 action_payload 字段与示例
db_query(action="slowlog", ip="10.0.0.12", action_payload={"since": "1h", "limit": 20})

ssh_exec(action="help")     → 查看各 action 的 action_payload 字段与示例
ssh_exec(action="file_grep", ip="10.0.0.12", action_payload={"file_path": "/var/log/app.log", "pattern": "ERROR"})
```

公共参数（`ip` / `port` / `timeout`）放在顶层；命令参数统一放 `action_payload`，由服务端按 action 校验未知字段和必填字段。参数错误时结果会带 `hint` 和 `action_help`，未知 action 会带 `available_actions`，模型可据此自行修正后重试。

## 启动（需与主应用同一环境：CHAT_DATA_HOME 必填，依赖见 requirements.txt）

```bash
# 仓库根目录执行（CHAT_DATA_HOME 见 server/.env.example）
CHAT_DATA_HOME=<数据根> python -m mcp_servers.db_query_server
CHAT_DATA_HOME=<数据根> python -m mcp_servers.ssh_exec_server
```

## 配置

- `__main__` 启动时自动加载 **`mcp_servers/.env`**（本文件不入库；真实凭据在此维护，
  占位样例见 `server/.env.example`）。`CHAT_DATA_HOME` 仍来自运行环境（与主应用一致）。
- 端口：`MCP_DB_QUERY_PORT` / `MCP_SSH_EXEC_PORT`
- 目标库只读账号：`MCP_DIAG_DB_USER` / `MCP_DIAG_DB_PASSWORD`
- SSH 默认连接：`MCP_DIAG_SSH_PORT` / `MCP_DIAG_SSH_USERNAME` / `MCP_DIAG_SSH_KEY_FILE`

## 目录

```
mcp_servers/
├── common/               # 配置、输出截断、参数校验、SSH/DB 客户端（两 server 共享）
├── db_query_server/      # server.py（MCPServer + db_query 单工具）+ tools/（诊断实现）
└── ssh_exec_server/      # server.py（MCPServer + ssh_exec 单工具）+ tools/（巡检实现）
```

## 注意

- 本机系统代理会劫持 localhost 请求（表现为 502）：开发机联调需设置
  `NO_PROXY=127.0.0.1,localhost`，生产部署同理。
- 客户端为 `apps/chat_data_app/agent/mcp/`（SDK 2.x 门面 `mode=auto`），
  对 2.x server 与旧协议 server 均兼容。
