# Semantic Services

语义能力按依赖方向分为两层：

- `core/`：纯语义校验、查询计划和 DSL/Builder DSL 到 SQL 的编译内核，不依赖 ChatData 业务服务、数据库、Agent 或 LLM。
- `run/`：语义模型加载、权限、配置管理、模型检索、快照和查询编排等应用服务。

依赖方向只能是 `run -> core`。数据源、SQL 执行和会话等能力通过 `run` 的适配层调用，不能进入 `core`。
