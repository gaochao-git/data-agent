# Semantic Run

本目录承载语义模型的业务运行服务，包括模型加载、检索、配置生命周期、会话上下文、快照物料化和查询编排。

`semantic_query_service.py` 负责把已加载并授权的业务 DSL 适配为语义内核可处理的输入，并调用 `../core/` 编译；SQL 执行仍由 `services/sql_run/` 负责。
