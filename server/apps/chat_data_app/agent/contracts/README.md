# agent/contracts

Harness 内部跨层契约（Port）层。

- 只放 `Protocol` 和纯类型，不 import 任何 `agent` 实现子包；
- 上层依赖接口，具体实现由 `data_agent`/`run` 装配时注入；
- `InboxPort`：`middleware/steering` 不再直接依赖 `run/agent_inbox_service`；
- `SkillSourcePort`：技能服务不再直接依赖 `plugins.loader`；
- `SkillSnapshotPort`：workspace 技能快照不再直接依赖 `skills.skill_service`；
- `McpServerSourcePort`：MCP 配置加载不再直接依赖 `plugins.loader`；
- `WorkspacePreviewPort`：技能 Office 预览不再直接依赖 workspace 实现；
- `mcp_display` / `mcp_tools`：run 的事件序列化不再直接依赖 mcp.manager/catalog。

端口默认实现不再在模块 import 时自动注册，由 `agent/port_config.py` 的
`configure_default_harness_ports()` 在 composition root 显式绑定，避免隐式副作用。

约定：只有跨层、易变、存在反向依赖或环的能力才抽 Port；普通工具函数不抽。
