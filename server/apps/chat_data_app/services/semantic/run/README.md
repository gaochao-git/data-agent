# Semantic Run

本目录承载语义模型的业务运行服务，包括模型加载、检索、配置生命周期、会话上下文、快照物料化和查询编排。

查询编译链路分四层，改动时注意职责边界：

- `semantic_query_service.py`：对外门面。编译入口（`compile_semantic_ai_dsl` / `compile_semantic_bi_dsl` / `compile_semantic_builder_sql`）、执行转发与结果装配；适配器与私有助手的存量 re-export 也在这里，调用方不要绕过门面直接依赖内部模块。
- `semantic_ai_query_adapter.py`：AI DSL（智能问数 agent）输入适配器。上游已过 InputValidator 校验，对畸形节点宽松。
- `semantic_bi_query_adapter.py`：BI Builder DSL（可视化查询）输入适配器。字段支持语义名或物理 table/column/expression 模糊解析，直面原始前端输入，校验严格、报错文案被测试断言。
- `semantic_query_plan_builder.py`：两条入口共享的查询计划组装原语（表达式解析、节点组装、无校验树遍历）。影响两条入口的横切语义（新过滤操作符、行级权限注入等）优先落在这里或 `../core/semantic_sql/`，不要在单个适配器里各改一份。

两条入口把各自的 DSL 翻译成统一的 builder 风格查询计划，再经 `../core/` 的 `SemanticSQLCompiler` 编译；SQL 执行仍由 `services/sql_run/` 负责。校验与报错契约差异是有意设计（AI 宽松 / BI 严格），统一差异前先用评测用例验证，不要在重构里顺手抹平。
