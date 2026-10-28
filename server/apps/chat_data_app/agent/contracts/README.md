# agent/contracts

Harness 内部跨层契约（Port）层。

- 只放 `Protocol` 和纯类型，不 import 任何 `agent` 实现子包；
- 上层依赖接口，具体实现由 `data_agent`/`run` 装配时注入；
- `InboxPort`：`middleware/steering` 不再直接依赖 `run/agent_inbox_service`；
- `SkillSourcePort`：技能服务不再直接依赖 `plugins.loader`；
- `SkillSnapshotPort`：workspace 技能快照不再直接依赖 `skills.skill_service`；
- `McpServerSourcePort`：MCP 配置加载不再直接依赖 `plugins.loader`。

约定：只有跨层、易变、存在反向依赖或环的能力才抽 Port；普通工具函数不抽。
