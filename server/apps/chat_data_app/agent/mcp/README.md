# agent/mcp — MCP client 能力

> 协议对齐：MCP 规范 **2026-07-28**（SDK ``mcp==2.2.0``，2.x Client 门面 ``mode=auto``）。
> 新协议 server 走 ``server/discover`` + 按请求模型；旧协议 server 自动回退 initialize 握手。

宿主作为 **MCP client**，在 agent 装配时把外部 MCP server 的工具枚举进来，
与内置注册表工具（`tools/registry.py`）一起挂到模型上。插件（`agent/plugins/`）
声明的 MCP server 也经此链路生效。

## 模块

| 文件 | 职责 |
| --- | --- |
| `config.py` | server 配置解析与校验：当前来源为**已启用插件的 `mcp_servers` 声明**（独立挂载后续提供）；单条非法/未通过准入只告警跳过 |
| `manager.py` | 会话管理（仅 streamable_http，2.x 门面按需建连用完即关）、工具枚举 TTL 缓存（采纳服务端 ``ttlMs`` 提示）、调用（内建 MRTR ``input_required`` 处理）、健康检查（``tools/list`` 探针——``ping`` 已随 2026-07-28 移除）、LangChain `StructuredTool` 包装 |
| `catalog.py` | 工具检索目录（手写 BM25，零新增依赖）+ `mcp_tool_search` 按需检索工具；两级加载的第二级，见下文 |

## 两级加载（目录 + 按需检索）

插件/MCP server 增多后，全量挂载工具 schema 会造成 token 风暴与选择噪声。
装配规格（`AgentAssemblySpec`）通过 `mcp_tool_search` 控制加载策略：

- `off`：全量挂载（默认行为，当前规模下 `auto` 判定也落在此档，行为零变化）。
- `auto`：估算全部工具 schema token（复用 `agent/context/` 估算器），超过
  模型窗口 `mcp_tool_search_threshold`（默认 0.10）比例才启用检索模式。
- `forced`：无条件启用检索模式。

检索模式下的运行方式：

1. **执行注册全量、模型可见收敛**：全部 MCP 工具仍注册进 ToolNode（可执行），
   但每次模型请求只暴露"内置工具 + `mcp_tool_search` + 始终挂载 + 已发现"。
   过滤由 `agent/middleware/mcp_tool_discovery.py` 在请求侧完成（注册表与
   可见性分离，参照 Anthropic Tool Search Tool 的 defer_loading 思路）。
2. **先检索后调用**：模型先调 `mcp_tool_search(query, server_id?, limit?)`
   拿到命中工具摘要（JSON：`results[].name/server_id/description`），再按
   `name` 直接调用；命中名单由发现中间件并入 state 的 `discovered_tool_names`
   （会话累积、随 checkpoint 持久），后续轮次免检索直出。
3. **始终挂载**：规格 `always_mounted_tools` 声明的 MCP 限定工具名
   （`mcp_<server>_<tool>`）免检索始终可见，用于核心高频工具。
4. **目录失效**：`MCPCatalog.sync` 按工具清单内容指纹（qualified_name +
   描述 + 入参 schema + 展示名）判断是否重建索引；枚举结果沿用 manager 的 TTL 缓存
   与插件变更 reset 机制，本模块不新增缓存。
5. `mcp_server_ids`：server 白名单（`None` 全量），在 `MCPToolInfo` 层按
   server_id 精确过滤，白名单外 server 不枚举不挂载。

装配可观测性：装配完成日志输出 builtin/mcp/search 工具数量与工具 schema
token 估算；MCP 工具描述超过 500 字符时告警（提示插件作者精简以降低成本）。

## 工具命名

挂到模型的工具名统一为 `mcp_<server_id>_<tool>`（清洗为 `[A-Za-z0-9_-]`，上限 64 字符），
与内置工具名空间隔离，杜绝撞名。因此 MCP 工具不会进入子智能体
（子智能体只继承内置工具，见 `data_agent.create_agent_from_spec`）。

## 配置

```bash
# server/.env（MCP 连接相关的全局参数）
CHAT_DATA_MCP_TOOL_TIMEOUT_SECONDS=60       # 全局默认工具超时；插件可按工具覆盖 timeout_ms
CHAT_DATA_MCP_TOOLS_CACHE_TTL_SECONDS=300   # 工具枚举缓存 TTL
```

**配置来源：当前仅插件 `plugin.json` 的 `mcp_servers` 声明**（随插件安装/启停生效）。
独立的 MCP server 挂载与管理页面后续提供，届时再恢复部署级/DB 配置来源。

transport 只支持 `streamable_http`（`url`/`headers`）。`stdio`/`sse` 不再接受：前者等同宿主任意进程执行，后者已废弃。
`enabled: false` 的配置项不参与装配。

## 与插件的关系

`agent/plugins/` 的 `plugin.json` 可声明 `mcp_servers`（字段 schema 见上），
由 `config.load_mcp_server_configs()` 读取并逐条准入；重复 id 后到者忽略。

## 安全边界与信任级别

**装配本模块的配置等于向宿主进程授予两类能力，插件安装入口必须受控（仅管理员，网关侧校验）：**

  声明方（环境变量或插件 manifest）只能来自可信管理员；绝不能把插件安装能力开放给普通用户。
- `streamable_http` 的 `url` 仍构成**内网访问面（SSRF）**，需 https + host 白名单约束。
  上线前确认部署网络的边界策略，必要时在网关出口限制可达网段。

**准入检查（`admission.py`，不满足即"不支持"）**：

| 检查 | 默认策略 |
|---|---|
| 远程 URL | 必须 https（loopback 例外，`CHAT_DATA_MCP_REQUIRE_HTTPS=true`）；禁止 URL 内嵌凭据 |
| host 白名单 | `CHAT_DATA_MCP_ALLOWED_HOSTS`（逗号分隔，支持 `*.example.com`），留空不限制 |
| headers | 含控制字符拒绝（防头注入） |
| 工具面 | `tools/list` 空 → 拒绝；工具数/schema 字节数超限 → 拒绝；重名/非法 schema → 拒绝 |
| 工具描述 | 超长/含控制字符 → 告警（按截断/原样展示，不阻断挂载） |

配置级准入在 `load_mcp_server_configs()` 逐条执行（env 与插件声明同口径），硬错误
只跳过该 server 并记录原因；工具级准入在 `tools/list` 之后执行。插件安装时同口径校验
声明的 `mcp_servers`，未通过直接拒绝安装。

**MCP 工具策略（B 端固定策略）**：MCP 工具默认执行前审批；每工具的 `label/approval/timeout_ms` 统一由插件 `mcp_servers[].tools.<工具名>` 配置，未配置的工具走默认（工具名 / ask / 全局超时）。审批档位只有 `ask/allow`；不需要的工具应在插件声明或装配层直接不挂载、不可检索。

服务端无用户角色模型，`/admin/plugins` 与其他 admin 接口同口径：靠 service token +
可信代理入口控制；service token 不得下发到普通用户侧。

## 明确不做（v2）

- 不做常驻连接池：每次枚举/调用按需建立会话、用完即关；枚举结果有 TTL 缓存兜开销
  （2026-07-28 远程传输已无状态化，该取舍与新协议方向一致）。
- MCP 工具不进子智能体；如需只读约束，在 MCP server 侧自行收紧。
- 准入只校验顶层 schema（properties/required/类型合法性），嵌套结构与 `$ref` 仍按 Any 透传——
  2026-07-28 已将 inputSchema 放宽到完整 JSON Schema 2020-12，递归校验留待后续版本。
- MCP 工具不携带内置工具的 `intent` 参数（那是内置工具的用户展示约定）。
