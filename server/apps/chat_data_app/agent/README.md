# ChatData Agent

ChatData 唯一的业务 Agent。本目录承载 Agent 的全部运行域实现：装配、工具、中间件、提示词、
工作区文件系统、技能与运行时服务。

## 目录结构

| 子包/文件 | 职责 |
|---|---|
| `data_agent.py` | Agent 装配入口：`create_data_agent` / `get_data_agent_graph`，按 `runtime_options` 组装工具集 |
| `agent_state.py` | LangGraph 状态定义（`ChatDataAgentState`） |
| `agent_runtime.py` | 共享运行时工具（模型获取等） |
| `runtime_options.py` | 运行选项：`workspace_backend`（local/sandbox）、`bash_enabled`（默认 False） |
| `tool_results.py` | 工具结果通用结构 |
| `tools/` | 模型可见工具（见 `tools/README.md`） |
| `middleware/` | create_agent 中间件（上下文压缩、工具结果卸载、图片注入等） |
| `prompts/` | 提示词包；`chat_data.py` 为 system prompt 构建 |
| `workspace/` | 会话工作区虚拟文件系统（见 `workspace/README.md`，重点） |
| `run/` | Run 事件流与调度（事件持久化规格见 `services/agent_run/README.md`） |
| `skills/` | 技能加载与技能服务 |
| `context/` | 上下文预算估算 |

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
