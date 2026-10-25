# 本体构建与分库查询方法论

> 日期：2026-10-25
> 定位：把当前本体（Ontology）实现方案、数据源组/分片约束、分库查询链路沉淀成可复用的方法论。
> 官方依据：`docs/palantir-ontology/` 本地副本；项目现状：`server/apps/chat_data_app/`。

## 0. 一句话定位

```text
本体 = 逻辑语义层（对象/属性/关系/Action）
     + 逻辑数据层（Dataset）
     + 分片映射（数据源组 → 多个 member）
     + 统一查询/结果契约（semantic 与 ontology 共用）
```

本体不是"再建一套存储"，而是把已有数据映射成可被智能体和业务人员稳定引用的对象契约。

## 1. 核心不变量

这五条是后续所有实现的前提，违反任何一条都会让链路失控：

| # | 不变量 | 含义 |
|---|---|---|
| 1 | **组内同构** | 一个数据源组内所有 member 使用同一套模板（表/列/口径一致）；不一致必须新建数据源组 |
| 2 | **单组查询** | 一次查询只能属于一个数据源组；跨组查询需要独立的联邦/对比场景，不进默认链路 |
| 3 | **逻辑与物理分离** | 对象只引用 Dataset 逻辑列；物理库/表由 binding 决定，本体不重复定义物理结构 |
| 4 | **member 是分片** | member 只表示物理分片/执行目标，不进入查询语义；业务层只应看到"范围" |
| 5 | **统一结果契约** | 多分片结果统一 `results[]`，成员状态由 `status` 单一表达，语义层与本体层同口径 |

第 1 条对应官方"一个 datasource 只支撑一个对象类型"的约束（`object-link-types/create-object-type.md:176`），
但本项目额外要求"同一数据源组内模板一致"，这是分库场景下的工程约束。

## 2. 分层模型

```text
逻辑层     Object Type / Property / Link Type / Action Type
              ↓ 只引用
数据层     Dataset（逻辑表 + 逻辑列 + 主键）
              ↓ binding（组 + member + 物理表）
分片层     member_datasource_id → 物理库/表（org_code1..N）
              ↓
执行层     每个 member 生成 SQL，并行下发（QueryOrchestrator）
              ↓
结果层     results[]：member_id / status / sql / columns / data / total_count / error
```

官方同样把"语义元素 + 动力元素 + 动态安全"作为本体基础（`object-backend/overview.md:10`），
并把持久层与索引层分开（`object-edits/how-edits-applied.md:73`）；本项目用"直连投影"替代对象存储，
因此更依赖上面的分层与不变量来保证一致性。

## 3. 本体建模方案

### 3.1 对象类型

- 必须有标识属性（业务主键），禁自增行号；官方同义（`create-object-type.md:76,95`）；
- 标题键只用于展示，不承担身份；
- 状态机 `draft → experimental → certified`，`deprecated` 终态；
- certified 前置：至少一个标识属性，且 source binding 覆盖全部标识属性；
- 一个 Dataset 只能被一个活跃对象类型绑定（一源一对象类型）；
- 对象身份 = `member:object_type:local_key`，分片内唯一。

### 3.2 属性

- 值类型内建 6 种 + enum；派生属性查询时计算、只读；
- 派生属性：单跳、非自关联、单列关联；aggregate/select 子集；
- 物理列只通过 Dataset 逻辑列映射，不在本体重复定义；
- 官方基类型矩阵（能否作主键/标题键，`properties-overview.md:22-38`）后续落成显式声明。

### 3.3 关系类型

- 当前只支持 FK→PK 的 many_to_one / one_to_one，双向导航；
- 多对多走 junction 对象类型；
- 跨 member 关系不支持（分片隔离），官方 MDO/对象存储才具备跨源统一对象能力。

### 3.4 Action Type

- `set_property`（覆盖层）+ `create_object`（动作对象表），写性门禁 `read_only/action_writable/action_created`；
- 官方 Action 是"编辑定义 + 副作用定义"（`action-types/overview.md:10-12`），规则共 12 种（`action-types/rules.md:18-29`）；
- 当前只覆盖最小闭环；Action L2 补齐 create/modify/delete/link 规则、提交条件、action log 对象化。

### 3.5 对象集合

- static 主键列表 + temporary 24h；不支持 dynamic/permanent 共享；
- 用途：跨轮传递查询结果、后续批量动作的输入集合。

## 4. 分库查询方法论（同构分片）

前提：组内同构。因此**同一个逻辑查询在每个 member 上结构一致，只是执行目标不同**。

```text
S0 逻辑模型：对象/属性 → Dataset 逻辑列
S1 分片映射：组模板 + member → 物理连接/表
S2 范围解析：业务 scope → 组内最小 member 集合
S3 查询规划：SQL 生成一次，绑定 N 个执行目标；按分片键裁剪
S4 并行执行：并发上限、超时、取消、鉴权、失败隔离
S5 结果汇聚：concat 默认；sum/topk/global_page 需显式声明
S6 身份关系：member 内对象身份；跨 member 关系不支持
S7 观测：分片数、空分片率、P95、失败率、结果大小
```

### 4.1 范围解析（S2）

```text
default  = 会话/当前机构（默认，通常 1 个分片）
members  = 显式指定机构列表
all      = 用户明确确认"全部机构"时，取当前用户有权限的全部 member
```

规则：

- 默认**不能**是 all，避免 N 条无用 SQL；
- 未指定且上下文有多个机构 → 先澄清，不静默全查；
- scope 解析时一次完成授权校验，未授权 member 不进入执行列表。

### 4.2 查询规划（S3）

同构前提下：

- SQL 只生成一次，执行 N 次（目标不同）；
- 问题里带机构/分片键时，只路由到对应 member（分片裁剪）；
- count 与 data 的拆分策略可配置，避免每分片固定两条 SQL。

### 4.3 合并语义（S5）

| 算子 | 语义 | 代价 |
|---|---|---|
| `concat` | 每分片结果直接拼接（当前默认） | 低 |
| `sum` | total_count 等可加指标求和 | 低 |
| `topk` | 各分片前 K 再全局归并 | 中 |
| `global_page` | 全局排序 + offset/limit | 高 |
| `distinct` | 跨分片去重 | 高 |

**没有声明合并算子，就不对外声称支持全局排序/全局分页。**

### 4.4 结果契约（S5 之后）

```json
{
  "status": "succeeded",
  "sql": "...",
  "total_count": 2000,
  "execution_time_ms": 12,
  "error": null,
  "results": [
    {
      "member_datasource_id": "ORG_CODE1",
      "member_name": "财付通",
      "status": "succeeded",
      "columns": [],
      "data": [],
      "total_count": 1200,
      "execution_time_ms": 6,
      "error": null
    }
  ]
}
```

- 成员成功与否只看 `status`（`succeeded/failed/cancelled/running`），不用 success 布尔；
- 单成员失败只影响该成员，其它照常返回；
- 工具结果外部化后 `meta` 只保留 `member_datasource_ids`（名称由调用方按已有映射补），不再重复携带 `member_names`。

## 5. 查询模式与智能体接入

```text
query_mode（唯一选择入口）
  ├─ semantic：语义模型 DSL/SQL，面向全局指标分析
  └─ ontology：对象/关系/Action，面向个体与关联分析
```

- 本体模式下唯一数据查询入口是 `ontology_tool`，禁止回退语义查询；
- 目录物化到 `_ontology_catalog.yaml`，工具按对象类型导航；
- 多机构查询：一次调用传 `member_datasource_ids` 数组，工具内部按分片并行下发，返回 `results[]`；
- 提示词与技能文档必须与工具行为一致（本次已把"逐机构调用"改成"一次传列表"）；
- 技能修改后需同步到运行目录 `CHAT_DATA_HOME/skills/system/`，会话快照会在下一轮自动重建。

## 6. 当前实现映射

| 能力 | 代码 |
|---|---|
| 本体定义/校验 | `services/ontology/ontology_service.py`、`ontology_validator.py` |
| 一致性巡检 | `services/ontology/ontology_consistency.py` |
| 对象投影/多机构查询 | `services/ontology/object_projection.py` |
| 单库 SQL 构造 | `services/ontology/object_query_builder.py` |
| 统一查询编排 | `services/query/orchestrator.py`、`services/sql_run/result.py` |
| Action | `services/ontology/action_type_service.py` |
| 对象集合 | `services/ontology/object_set_service.py` |
| 智能体工具 | `agent/tools/ontology_tool.py` |
| 查询模式 | `services/ontology/query_mode.py` |
| 结果外部化/契约 | `agent/middleware/tool_result.py` |

## 7. 设计决策记录（ADR 摘要）

| 决策 | 结论 | 理由 |
|---|---|---|
| 直连投影 vs 对象存储 | 先用直连投影 + 覆盖层 | 成本低；官方持久层是 Dataset、索引可重建（`how-edits-applied.md:73`），对象存储可后置 |
| 分片执行 | 每 member 一条 SQL | 组内同构但连接/权限/失败隔离按 member；UNION 作为后续同集群优化 |
| 结果契约 | 统一 `results[]` | 与语义层一致，消费方一套逻辑 |
| meta 字段 | 只留 member ids | 名称映射调用方已有，避免重复和口径漂移 |
| 默认范围 | 当前机构，不是全部 | 避免 N 条无用 SQL；全部需显式确认 |
| 跨组查询 | 不支持 | scope 必须单组，避免联邦查询复杂度失控 |
| 对象身份 | 含 member | 分片内主键可能重复，跨片不串对象 |
| 技能同步 | 代码目录 cp 到运行目录 | 运行时只读部署目录，应用不自动复制 |

## 8. 演进路线

```text
P0 稳定读侧：口径、分页/总数、对象/属性补全、真实问题回归
P1 Action L2：create/modify/delete/link 规则 + 提交条件 + action log
P2 类型系统：基类型矩阵、Long/Float/Double、Value Type
P3 关系形态：join table many-to-many、object-backed link
P4 权限与存储：对象/行级权限、动态安全、对象存储/索引（按需）
```

## 9. 落地检查清单

- [ ] 数据源组内 schema 一致性校验（建组/加数据源时拒绝不一致）
- [ ] scope 必须单组，跨组查询显式拒绝
- [ ] 默认 scope = 当前机构，不是全部
- [ ] 同组 SQL 生成一次、多 member 执行
- [ ] 合并算子（concat/sum/topk/global_page）显式声明
- [ ] 结果只返回 member ids，名称由调用方映射
- [ ] 单成员失败隔离，`results[]` 逐成员状态
- [ ] 提示词/技能与工具行为一致，技能目录同步
- [ ] 观测：分片数、空分片率、P95、失败率

## 10. 反模式

```text
默认查全部机构，产生大量空 SQL
跨数据源组混查，把不同模板的字段拼在一起
没有 merge 语义就对外声称支持全局排序/分页
把 member_datasource_id 当业务参数暴露给用户
结果同时带 member id 和 member name，造成双份口径
能力改了但技能文档、提示词没同步
```
