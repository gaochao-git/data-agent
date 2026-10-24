# query

查询层契约与编排入口。

- `contract.py`：`QuerySpec` / `QueryResult`，可序列化，供回放、审计、迁移。
- `orchestrator.py`：唯一查询编排入口，调用 `sql_run.runtime`，归一化 member 结果。
- 调用方不得直接连数据源或自行拼结果；迁移顺序见 `docs/query-layer-design.md`。

当前为兼容薄门面，尚未替换既有 object/semantic/SQL/看板调用方。
