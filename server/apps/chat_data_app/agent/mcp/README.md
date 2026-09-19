# agent/mcp — MCP client 能力

宿主作为 **MCP client**，在 agent 装配时把外部 MCP server 的工具枚举进来，
与内置注册表工具（`tools/registry.py`）一起挂到模型上。插件（`agent/plugins/`）
声明的 MCP server 也经此链路生效。

## 模块

| 文件 | 职责 |
| --- | --- |
| `config.py` | server 配置解析与校验：`CHAT_DATA_MCP_SERVERS`（JSON 数组）+ 已启用插件的 `mcp_servers` 声明合并；单条非法只告警跳过 |
| `manager.py` | 会话管理（stdio / streamable_http / sse 三种传输，按需建连用完即关）、工具枚举 TTL 缓存、调用、握手级健康检查、LangChain `StructuredTool` 包装 |

## 工具命名

挂到模型的工具名统一为 `mcp_<server_id>_<tool>`（清洗为 `[A-Za-z0-9_-]`，上限 64 字符），
与内置工具名空间隔离，杜绝撞名。因此 MCP 工具不会进入子智能体
（子智能体只继承内置工具，见 `data_agent.create_agent_from_spec`）。

## 配置

```bash
# server/.env
CHAT_DATA_MCP_SERVERS=[{"id":"fs","transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp/ro"]}]
CHAT_DATA_MCP_TOOL_TIMEOUT_SECONDS=60       # 单次 initialize/list/call 超时
CHAT_DATA_MCP_TOOLS_CACHE_TTL_SECONDS=300   # 工具枚举缓存 TTL
```

transport 支持 `stdio`（`command`/`args`/`env`）、`streamable_http` 与 `sse`（`url`/`headers`）。
`enabled: false` 的配置项不参与装配。

## 与插件的关系

`agent/plugins/` 的 `plugin.json` 可声明 `mcp_servers`（与环境配置同 schema），
由 `config.load_mcp_server_configs()` 合并；环境配置 id 优先，插件同 id 声明被忽略。

## 明确不做（v1）

- 不做常驻连接池：每次枚举/调用按需建立会话、用完即关；枚举结果有 TTL 缓存兜开销。
- MCP 工具不进子智能体；如需只读约束，在 MCP server 侧自行收紧。
- MCP 工具不携带内置工具的 `intent` 参数（那是内置工具的用户展示约定）。
