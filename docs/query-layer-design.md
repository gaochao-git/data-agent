# 查询层设计契约

- 日期：2026-10-24
- 目标：查询构造、执行、结果装配、权限、缓存、可观测性各只有一处权威，便于后续数据源/存储/引擎整体迁移
- 现状：执行入口已统一到 `sql_run.runtime`，但查询构造与结果装配仍分散在 object / semantic / sql / dashboard / pipeline

## 1. 分层

```text
调用方
  Agent / 可视化 / SQL 页面 / 看板 / Pipeline
        │
        ▼
查询编排层 QueryOrchestrator
  1. 生成 QuerySpec
  2. 选 member / 权限
  3. 调执行层
  4. 归一化 QueryResult
        │
        ▼
执行层 QueryRuntime
  execute_sql_for_member / execute_agent_sql
  SQL guard / limit / timeout / cache / cancel / executor
        │
        ▼
数据源适配器
  Hive direct / Hive HTTP / MySQL
```

## 2. 唯一契约

### QuerySpec（可序列化，用于回放/迁移/审计）

```python
@dataclass
class QuerySpec:
    source: Literal["ontology", "semantic", "sql", "dashboard", "pipeline"]
    datasource_group_id: str
    member_datasource_ids: list[str]
    sql: str
    limit: int
    timeout: int
    user_id: str
    username: str
    query_mode: str = ""              # ontology_find_objects 等
    semantic_config_id: str | None = None
    object_type: str | None = None
    dataset_id: int | None = None
    dataset_version_id: str | None = None
    trace_id: str = ""
```

### QueryResult（所有入口统一）

```python
@dataclass
class QueryResult:
    query_mode: str
    source: str
    sql: str
    members: list[MemberResult]       # 复用现有 results[] 协议
    total_count: int
    execution_time_ms: int
    lineage: dict                     # dataset/version/object_type/semantic_config
    warnings: list[str]
```

`MemberResult` 继续使用现有字段：

```text
member_datasource_id / member_name / status / sql / columns / data /
total_count / execution_time_ms / error
```

## 3. 职责边界

### 查询编排层负责

```text
对象/语义/DSL 编译成 SQL
选择数据源组与 member
调用执行层
把结果归一化成 QueryResult
缓存 key 的查询语义部分
记录 query_mode / lineage / trace
```

### 执行层负责

```text
成员数据源解析与授权
SQL guard
limit / max_rows
timeout / cancel
结果缓存
执行器选择
统一 member 结果
```

### 数据源适配器负责

```text
连接、会话、协议、方言差异
```

## 4. 强制规则

1. **只有 QueryOrchestrator 调 QueryRuntime**
   - 业务服务、路由、Agent 工具不得直接 `execute_sql_for_member` / `execute_agent_sql`。
2. **只有 QueryRuntime 碰数据源连接**
   - 查询层禁止 import `pyhive` / `pymysql` / `create_engine`。
   - `pymysql.converters.escape_string` 这类纯转义工具允许。
3. **结果装配只有一处**
   - 禁止各入口自行拼 `results[]`/状态/错误字段。
4. **QuerySpec 可序列化**
   - 同一 spec 可回放；迁移数据源/引擎不改变调用方。
5. **权限在 QueryOrchestrator / Runtime 边界**
   - 不在页面、工具、编译器里各写一份。
6. **写路径独立**
   - Pipeline 物化走 `WriteAdapter`，不复用查询运行时。

## 5. 迁移顺序

```text
P0 定义 QuerySpec / QueryResult / QueryOrchestrator 接口（行为不变）
P1 object_projection → Orchestrator
P2 semantic query → Orchestrator
P3 SQL / 看板 → Orchestrator
P4 Pipeline 读取 → Orchestrator
P5 Pipeline 物化 → WriteAdapter
P6 查询日志/回放/lineage/指标
```

## 6. 统一后能解决什么

```text
换数据源：只改执行层/适配器
换引擎：只改 Runtime/Adapter
加权限：只改 Orchestrator 边界
加缓存：只改 Runtime
加审计：只记录 QuerySpec
加限流：只改 Runtime
迁移对象存储/图查询：查询层仍输出 QueryResult
```

## 7. 反模式

```text
各业务服务自己 new 连接
各入口自己拼 member 结果
各编译器自己处理 timeout/cache
页面直接调执行器
Pipeline 物化绕过写适配器
```

## 8. 近期落地

```text
1. 加架构测试：查询/语义/对象服务禁止直接连库与直接调 runtime
2. 抽出 QueryOrchestrator 薄门面（不改变现有行为）
3. 先迁移 object_projection
4. 再迁移 semantic query
5. 最后迁移 SQL/看板/Pipeline
```
