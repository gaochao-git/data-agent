# ChatData Agent

ChatData 的业务 Agent 运行域：装配、工具、中间件、提示词、工作区文件系统、技能与运行时服务。

装配为声明式规格驱动：`AgentAssemblySpec`（`data_agent.py`）声明 agent 名、system prompt
与工具选择（include/exclude），`create_agent_from_spec(spec, ...)` 按规格装配；
`chat_data` 是第一个规格（`CHAT_DATA_AGENT_SPEC`）。新增装配型智能体（如智能运维）按同一
规格接入、随发版启用；资源型扩展（技能包/MCP server）走 `plugins/`，动态安装。

## 目录结构

| 子包/文件 | 职责 |
|---|---|
| `data_agent.py` | Agent 装配入口：`AgentAssemblySpec` / `create_agent_from_spec` / `create_data_agent` / `get_data_agent_graph`，工具按 `tools/registry.py` 注册表 + 规格筛选装配 |
| `agent_state.py` | LangGraph 状态定义（`ChatDataAgentState`） |
| `agent_runtime.py` | 共享运行时工具（模型获取等） |
| `runtime_options.py` | 运行选项：`workspace_backend`（local/sandbox）、`bash_enabled`（默认 False） |
| `sql_assist_agent.py` | SQL 辅助小 Agent |
| `tool_results.py` | 工具结果通用结构 |
| `tools/` | 模型可见工具（见 `tools/README.md`） |
| `middleware/` | create_agent 中间件（上下文压缩、工具结果卸载、图片注入等） |
| `prompts/` | 提示词包；`chat_data.py` 为 system prompt 构建 |
| `workspace/` | 会话工作区虚拟文件系统（见 `workspace/README.md`，重点） |
| `run/` | Run 事件流与调度（事件持久化规格见 `services/agent_run/README.md`） |
| `skills/` | 技能加载与技能服务 |
| `context/` | 上下文预算估算 |
| `mcp/` | 宿主内置 MCP client：工具枚举缓存/调用/健康检查 + 检索目录（两级加载，见 `mcp/README.md`） |
| `plugins/` | 技能包插件：manifest + zip 安装 + 启停卸载（见 `plugins/README.md`） |

## 扩展边界

- **内置工具**：`tools/registry.py` 单一事实源，随发版；
- **MCP 工具**：环境变量 `CHAT_DATA_MCP_SERVERS` 或插件声明，agent 装配时动态枚举
  （工具名 `mcp_<server>_<tool>`），只挂主智能体，不进子智能体；工具数量大时
  按 `AgentAssemblySpec.mcp_tool_search` 两级加载（目录 + 按需检索），见 `mcp/README.md`；
- **插件技能**：插件 `skills/` 声明经 `skill_service.list_runtime_skill_sources_async`
  进入会话技能快照（与系统/用户技能同名时跳过）。

## 依赖方向约定

- `agent/` 内部允许依赖 `services/` 的**公共服务**（`llm_config`、`runtime_config`）与**领域服务**
  （`semantic.*`、`sql_run`）；这是 agent 消费服务层的正常方向。
- 禁止 agent 工具/中间件反向依赖 `services.agent_run.conversation_workspace` 等编排层——
  工作区核心统一从 `agent/workspace/runtime.py` 导入（`test_agent_boundary.py` 有边数护栏）。
- `services/` 对 `agent/` 的依赖边数只许通过搬迁减少（护栏测试锁定上限）。

## 运行选项与安全形态

- `workspace_backend=local`：宿主机直接 IO，信任部署假设；**建议保持 `bash_enabled=False`**。
- `workspace_backend=sandbox`：OpenSandbox 容器执行，强隔离形态。
- `bash_enabled=True` 且 `workspace_backend=local` 是最危险组合（宿主机裸执行），开启前必须确认部署环境可信。
