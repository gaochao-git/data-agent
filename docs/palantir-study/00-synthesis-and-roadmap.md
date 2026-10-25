# Palantir 本体研读综合与落地路线图

> 日期：2026-10-25
> 分支：`feat/ontology-rebuild`（HEAD `603e3a01`）
> 目的：先按官方文档建立依据，再决定下一轮本体开发做什么，避免凭想象补能力。
> 证据口径：官方原文引用统一为 `文件:行号`，基准是本地副本 `docs/palantir-ontology/`（抓取 2026-10-17）；
> 项目现状引用统一为仓库根路径。四份分主题证据包见文末 §6。

## 1. 官方本体的六层结构

官方文档不是「对象/关系/动作」三件套，而是六层（各层职责不能混）：

| 层 | 官方内容 | 依据 |
|---|---|---|
| 语义层 | object type / property / link type / interface / shared property / struct / value type | `ontology/core-concepts.md:14-19` |
| 动力层 | action type（编辑 + 副作用）、function、dynamic security | `action-types/overview.md:10-14`、`object-backend/overview.md:10` |
| 存储层 | 持久层是 Foundry dataset，object database 只是可重建索引 | `object-edits/how-edits-applied.md:73`、`object-backend/overview.md:43,74` |
| 写入层 | 用户编辑经 Actions → Funnel → 对象索引；编辑与源数据按冲突策略合并 | `object-edits/how-edits-applied.md:16-20,82-119` |
| 查询层 | Object Set Service：list/search/aggregate/load + object set（static/dynamic、temporary/permanent） | `object-backend/overview.md:47-63` |
| 权限层 | ontology resource 权限 + 对象/链接权限 + 提交条件 + read/write authorization | `object-permissioning/overview.md:10-24`、`action-types/submission-criteria.md:10` |

这条分层直接决定「差什么」：我们目前有语义层（子集）+ 查询层（子集）+ 很薄的写入层（覆盖层），**没有独立的存储层/对象集合层/权限层**。

## 2. 能力对照矩阵（官方 → 本项目）

| 官方能力 | 本项目现状 | 判定 | 依据 |
|---|---|---|---|
| Object type：主键必填、确定性、唯一 | 有，禁自增行号；但直连投影无唯一性校验 | 部分对齐 | `object-link-types/create-object-type.md:76,86,95` |
| Property 基类型矩阵（能否作主键/标题键） | 只有零散服务端规则，无显式矩阵 | 缺失 | `object-link-types/properties-overview.md:22-38` |
| Value type / Struct / Shared property / Interface | 均未实现（enum 只是子集） | 缺失 | `value-types-overview.md:10`、`structs-overview.md:10-20`、`shared-property-overview.md:10`、`interfaces/interface-overview.md:10-28` |
| Link type：FK / join table / object-backed 三形态 | 只有 FK→PK，基数 many_to_one/one_to_one | 子集 | `object-link-types/create-link-type.md:54-56,80-117,125-136` |
| Link type：一对一是意图非约束 | 我们按 one_to_one 做了约束语义 | 偏严（需改口径） | `create-link-type.md:62` |
| Action 规则：create/modify/create-or-modify/delete/create-link/delete-link/function | 只有 set_property + create_object，且二者互斥 | 1/12 | `action-types/rules.md:18-29` |
| 规则合成：每对象一条编辑、后写覆盖、非法组合 | 无合成，单一操作类型 | 缺失 | `rules.md:52-56` |
| 提交条件 + 失败文案 | 无 | 缺失 | `submission-criteria.md:10-18,101-103` |
| Action log：一 action type 一日志对象类型、一次提交链全部被编辑对象、版本自增、只记成功 | 只有执行审计表 | 缺失（语义层不同） | `action-log.md:32,51,63-68` |
| Revert | 无；官方也仅 OSv2、仅执行者、需最近一次编辑 | 缺失（不建议先做） | `action-reverts.md:14,18,42` |
| 冲突策略：用户编辑优先（默认）/最近值优先 | 覆盖层隐式「用户值永胜」 | 部分对齐（需写清） | `object-edits/how-edits-applied.md:88-91,95-119` |
| Object Set：static/dynamic、temporary/permanent | 无，查询结果只活在当轮 | 缺失（本轮落地 static） | `object-backend/overview.md:53-63` |
| 分页：pageSize 是建议值、看 nextPageToken、超限显式报错 | 自建 total/limit/offset；total_count 与 data 可能不一致 | 契约偏离 | `api/general/overview/paging.md:8,34-44`、`oss-limitations.md:91` |
| 关系下钻：left-semi join 后的对端 distinct 集合 | 按关系 join 取行；单关系硬上限 20 条 | 语义不同 + 本地硬上限 | `oss-limitations.md:106`、`services/ontology/README.md:136` |
| 对象级/动态权限 | 只有数据源组/member 授权 | 缺失 | `object-permissioning/overview.md:20-24` |
| Object Storage / OSDK | 直连源表投影 | 架构差异（缺可重建索引） | `object-backend/overview.md:43-49` |

## 3. 结论：先补「对象集合」，再做「Action L2」

按「正确性 > 基础能力 > 扩展能力」排序，得到三个判断：

1. **Action L2 是最大能力缺口，但不能直接做。** 官方 action 的批量入口是「对象引用列表参数」（`action-types/use-actions.md:12`），删除/修改也只能作用于对象引用参数定位的对象（`rules.md:19,21`）。我们当前 execute 只接受单个 `object_key` 字符串，没有「对象集合」这一层，直接做 create/delete/link 会退化成单对象 demo，且无法表达批量、无法承接「对刚才查询结果执行动作」的真实场景。
2. **Object Set 是查询层与写入层之间的缺失环节。** 官方 Object Set 是「为后续引用而保存的对象列表」（`object-backend/overview.md:53`），static 存主键、dynamic 存过滤条件，temporary 跨应用传递（24h、仅创建者可见），permanent 长期共享（`:57-63`）。补齐 static/temporary 后：查询结果可跨轮传递、可作为 Action 的输入集合、也是后续 bulk action 的对象引用来源。
3. **读侧契约先按官方收敛，避免在错误契约上叠功能。** 官方 pageSize 只是建议值、短页不代表结束、终止只看 nextPageToken，超限必须显式报错（`api/general/overview/paging.md:8,34-44`、`oss-limitations.md:91`）。我们自建的 `total_count + limit/offset` 与官方不同，且 total 与 data 可能不同源（列表带派生 LEFT JOIN、count 不带，见 `services/ontology/README.md:190`），这既解释了一部分「少返行」现象，也会在对象集合分页时继续制造歧义。**本轮先在新能力上采用官方分页口径，存量查询接口的 total_count 保留兼容但在文档里标注为非官方契约。**

因此本轮动手项定为 **Phase A：Object Set（static + temporary）**；Action L2 放到 Phase B，并明确其前置是对象引用/对象集合。

## 4. 路线图

### Phase A（本轮）：Object Set static + temporary

官方依据：`object-backend/overview.md:53-63`、`api/v2/.../ontology-object-sets/ontology-object-set-basics.md:8`、`create-temporary-object-set.md`。

范围：

- 新增本体对象集合表（static 定义：object_type + member + primary keys；temporary 带 24h 过期）；
- 服务端保存/列表/读取/删除；保存时校验对象类型、member、key 数量（<=100）与归属格式；
- 读取时按主键批量回源投影，返回对象属性（不含 links；派生值复用现有查询口径）；
- 智能体新增 `save_object_set` / `load_object_set` / `list_object_sets` 三个 action，让「刚才查到的对象」可保存、下一轮可加载；
- 过期集合读取显式报错，不静默返回空。

不做（并写入边界说明）：dynamic object set（存过滤条件）、permanent 共享/权限、interface/跨类型集合、object set 作为 Action 参数的批量执行。

验收：API 层保存/读取/过期校验通过；智能体在浏览器中完成「查询→保存集合→下一轮加载集合」；`scripts/verify --fast` 通过。

**落地记录（2026-10-25）**：已实现（`services/ontology/object_set_service.py`、`daos/ontology_object_set_dao.py`、
`routers/ontology_admin.py` 对象集合端点、`ontology_tool` 三个 action，DDL 已在开发库执行）。

- 浏览器验证：真实对话「查财付通前 3 个客户 → 保存临时集合 → 加载集合」通过，集合名
  `ftpay_customer_top3`、`object_key_count=3`、加载 `total=3 / returned=3 / missing_count=0`；
  第二轮「加载刚才的集合」同样命中（跨轮持久化），证据
  `docs/palantir-study/evidence/objset-done.png`、`objset-cross-turn.png`；
- API 验证：保存→列表→加载→删除全链路通过（临时集合 `expires_at` 为 24h 后）；
- 门禁：`scripts/verify --fast` 全部通过；新增单测 13 + 工具/API 契约测试 5；
- 同轮复跑 QA-010 得到订单明细 3/2 条、发货单 1/1 张，**DEF-01 在当前 HEAD 不可复现**
  （原评测环境为旧进程/旧分支），证据 `evidence/qa010-rerun.png`。

### Phase B：Action L2（下一步，需先设计对象引用参数）

按证据包 `03-action-types.md` 的 4 步：

1. 契约升级：`modify_object`（多属性）/`create_object`（主键必填）/`delete_object`；规则按对象合成、后写覆盖、非法组合校验（`rules.md:52-56`）；
2. 提交条件最小内核：参数条件 + 算子 + 失败文案（`submission-criteria.md:22,101-103`）；
3. 执行语义：一次 action 一事务、执行记录补 `edited_object_keys[]` 与 `action_type_version`（`action-log.md:63-68`）；
4. test-run 升级为 proposed edits diff（`test-run.md:12,38`）。

前置依赖：对象引用参数/对象集合（本轮产物）、覆盖层与源行消失的冲突语义写清（`object-edits/how-edits-applied.md:86-119`）、批量上限显式化（`scale-property-limits.md:26-28,32`）。

### Phase C：类型系统对齐

把 `properties-overview.md:22-38` 的基类型矩阵做成服务端单一可机检声明，供 certified 前置、属性校验、派生类型推导、前端下拉共同读取；补 `Long/Float/Double`，修派生 `count→Long`、`sum/avg` 加宽（`derived-properties-reference.md:22,39-47`）。依据：`01-object-model.md` §1.3、`02-link-derived-storage.md` §2。

### Phase D：关系形态与图

join table many-to-many（`create-link-type.md:105-117`）、object-backed link（`:125-136`）；明确我们的 Nebula `find_graph_paths` 在官方无对位，属自建能力，对外表述要与官方 Search Around 区分（`02-link-derived-storage.md` 发现 4）。

### Phase E：权限与 OSDK

对象/行级权限、动态安全（`object-permissioning/overview.md:20-24`）、OSDK/OSv2 索引（`object-backend/overview.md:96-115`）。短期不做，先把语义层与动力层稳定。

## 5. 证据索引

| 证据包 | 内容 |
|---|---|
| `docs/palantir-study/01-object-model.md` | 对象/属性/值类型/Struct/Shared/Interface/类型组/变更管理，29 条约束清单 |
| `docs/palantir-study/02-link-derived-storage.md` | Link Type 三形态、基数语义、派生属性、编辑落库、对象存储与索引 |
| `docs/palantir-study/03-action-types.md` | Action 12 种规则、参数、提交条件、副作用、action log、revert、一致性、权限 |
| `docs/palantir-study/04-query-objectset-api.md` | 查询/分页/关联对象/对象集合/聚合 API 语义，含「少返行」判定捷径 |
| `docs/palantir-study/05-ontology-construction-methodology.md` | 本体构建方案与同构分库查询方法论：不变量、S0-S7、merge 语义、结果契约与检查清单 |

使用约定：引用官方约束前先回读原文件行号；本目录笔记只作为索引与对照，不作为对外规范原文。
