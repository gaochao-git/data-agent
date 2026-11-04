# 本体 Action L2 设计：编辑事务语义（对齐 Palantir Action Type）

- 状态：设计稿（v2，已按官方文档校正）
- 分支：`feat/ontology-rebuild`（合并 master 后 `79c6a1c1`）
- 日期：2026-11-04
- 代码依据：`services/ontology/action_type_service.py`、`daos/ontology_action_execution_dao.py`、`services/ontology/object_projection.py`、`utils/models.py`、`next_version.md`
- 引用口径：① 官方副本一律 `action-types/xxx.md:行号`，基准是 `docs/palantir-ontology/`（行号为本地副本行号）；② 本项目引用写成站点相对路径如 `services/ontology/README.md:行号`，基准是**仓库根**；③ 研读结论引用 `palantir-study/03-action-types.md:行号`（该文已逐条核对官方原文，本文沿用其定位，并对关键条目做了二次抽查）。
- 范围：Action 动词层（定义 → 预演 → 执行 → 撤销）。不改源库、不动语义层指标、不涉及对象级权限（另立设计）。

## 0. 一句话结论

Action 缺的不是「更多编辑类型」，而是**编辑事务语义**：谁在什么基线上改、改完和数据源冲突怎么算、改错了怎么退。L2 把「一次覆盖写 + 审计」升级为 **定义 → test run → 提交 → 可撤销**，且每一步的语义都对齐官方 Action Type，而不是自造一套。

## 1. 官方口径（本文的判断题基准）

先把「官方到底怎么定义」摆出来，后面所有设计都以这张表为准，避免再靠直觉推导。

| 机制 | 官方定义（要点） | 出处 |
| --- | --- | --- |
| Action 的粒度 | 「An action is a single transaction that changes the properties of one or more objects」——一次 Action 是**单事务**，可跨对象 | `action-types/overview.md:10` |
| Action Type 的组成 | 「the definition of a set of changes or edits to objects, property values, and links … also includes the side effect behaviors」——编辑定义 + 副作用定义 | `action-types/overview.md:12` |
| 规则类型 | 12 种：create / modify / create-or-modify / delete / create link / delete link / function + 5 种 interface 版 | `action-types/rules.md:18-29` |
| 编辑编译（关键） | 多条规则**编译成「每个对象一条编辑」**；同对象冲突时「the resulting edit would just update the property to "B". The order of rules affects the final object edit」 | `action-types/rules.md:52` |
| 硬约束 | 主键必填；modify/delete **不能引用本次新建的对象**；FK 关系必须用 modify 改外键，m2m 才用 link 规则 | `action-types/rules.md:18-23` |
| 非法组合 | 不能先删后建、不能先改后建、同一对象不能在一次提交里创建两次 | `action-types/rules.md:54-56` |
| 原子性 | 「applied as a single, all-or-nothing batch」，且不覆盖副作用 | `action-types/consistency-guarantees.md:18` |
| 并发冲突 | 「detects write-write conflicts at the **object level** … even if the actions write to **different properties**」；冲突时「the failing action's **entire edit is discarded**」 | `action-types/consistency-guarantees.md:55,59` |
| 隔离级别 | Legacy / Snapshot（默认）/ Read committed（未上线）；snapshot **不检测读写冲突**（write skew 盲区） | `action-types/consistency-guarantees.md:67,73-75,107-123` |
| 冲突解决（读时） | 两策略：**Apply user edits（默认）** / **Apply most recent value**（需 timestamp 属性、UTC、只比较输入数据源时间戳；edit-only 属性始终生效） | `object-edits/how-edits-applied.md:88-91,121-139,169-187` |
| 删除/行消失 | 「**Deletions are not considered an edit**」：行消失→对象不可见但编辑**保留**；行回来→旧编辑**复现**；用户 Create 出来的对象在源行消失后**仍在**；对已删除对象 Modify **必失败** | `object-edits/how-edits-applied.md:86,105-119` |
| 提交条件 | 「the conditions that determine whether an action can be submitted」，是**把业务逻辑编码进编辑权限**的机制，带失败文案 | `action-types/submission-criteria.md:10,103`；`action-types/permissions.md:20` |
| test run | 返回 action **would** make 的编辑；「edits … are **not** applied」；输出 Proposed changes + Details（execution log 五步）；错误分 admin-facing / end-user-facing | `action-types/test-run.md:12,38,44,50-53` |
| action log | log 对象类型与 action type **一一对应**；一次提交**一条**，自动链到全部被编辑对象；必记 action RID / type RID / **type version** / commit timestamp / user / **被编辑对象主键** | `action-types/action-log.md:51,63-68` |
| revert | 仅 OSv2；**仅执行者本人**；仅当该 action 是对象**最近一次**编辑（哪怕别的属性）；**不撤销副作用** | `action-types/action-reverts.md:14,18,42,47` |
| 重试 | 瞬时错误重试**从头执行并重算**；带外部调用的 action 不重试 | `action-types/consistency-guarantees.md:170` |

三条最容易做错的判断：

1. **「冲突」有两个完全不同的含义**。官方把「数据源更新 vs 用户编辑」的冲突放在**读时解决**（`how-edits-applied.md`），把「并发 Action」的冲突放在**提交时检测**（`consistency-guarantees.md`）。二者不是一套机制，不能合并成一个 `conflict_policy`。
2. **官方没有「因为数据源变了就拒绝这次 Action」这种默认行为**。默认是「用户编辑永远胜」，拒绝只发生在并发写写冲突与对已删除对象执行 modify。
3. **官方没有 `preview` 这个动作**，只有 **test run**；也没有 `idempotency_key`，它是我们的扩展。

## 2. 上一版设计稿的七处偏差与修正

上一版（v1）是在没吃透官方文档的情况下写的，逐条对比如下。这张表是本版的改动依据。

| # | 偏差点 | v1 写法 | 官方口径 | 本版修正 |
| --- | --- | --- | --- | --- |
| 1 | 冲突策略定义错位 | 自造 `reject`（默认）/ `overlay_wins` / `source_wins`，把「提交时拒绝」当主策略 | 冲突解决是**读时**、对象类型/数据源级、两策略：用户编辑优先（默认）/ 最近值优先 | 拆成**读时解决**（对齐官方两策略，用官方命名）+ **提交时检测**（对象级写写冲突）；`reject` 降级为可选严格模式 |
| 2 | 行消失处理过度 | `orphan_flag=1` 落库 + 读路径永久过滤，「变成可回收状态」 | 删除不是编辑：编辑**保留**，行回来**复现**；用户创建的对象**不随**源行消失 | 不落 `orphan_flag`，改为**读时派生可见性**；编辑永不因行消失而失效 |
| 3 | 编辑粒度 | 逐属性追加 edit 行，同属性重复**定义期拒绝** | 规则**编译为每对象一条编辑**，同属性后者覆盖前者 | **对外**编译为每对象一条编辑（`compiled_edits`）；**落库仍按属性行**；同属性重复保持拒绝——见 §6.2 的取舍说明 |
| 4 | 撤销判定 | 「属性未被后续执行再次编辑」，且未限制操作人 | **对象级**最近一次编辑；**仅执行者本人** | 改为对象级判定 + 执行者限定（管理员例外需显式开关并审计） |
| 5 | 预演形态 | 新增 `preview` 端点，返回 `diff[] + warnings[]` | 叫 **test run**，输出 Proposed changes（current vs proposed）+ execution log 五步 + 错误分级 | **扩展现有 `test-run`**，不新增概念；输出结构对齐官方 |
| 6 | 缺提交条件 | 完全没提，把前置条件留给调用方 | 是 action type 的一等组成，且是权限代管机制 | 补最小内核（参数/对象属性 + 比较算子 + 失败文案 + all/any） |
| 7 | 审计字段不足 | execution 只有参数与 edits | 必记 type version、被编辑对象主键集合 | 补 `action_type_version`、`edited_object_keys`；不建独立 log 对象类型（差异项） |

另补两处 v1 未覆盖的：**隔离级别**（我们只能做 legacy 等价，见 §5.5）与**批量上限**（本期显式降级，见 §6.5）。

## 3. 当前实现（代码级事实）

| 能力 | 实现位置 | 当前事实 |
| --- | --- | --- |
| 编辑契约 | `_normalize_edits:161-195` | 只允许 `set_property` 与 `create_object`，且 operation 集合**必须恰为其中之一**（`:173`/`:179`/`:195`），不能混合 |
| set_property 校验 | `_normalize_edit_item:103-123` | 标识属性禁改（`:115`）、派生属性禁改（`:117`）、**同属性重复直接报错**（`:119`） |
| create_object 校验 | `_normalize_create_object_edit:126-158` | 标识属性**必填**（`:152`），属性去重（`:147`） |
| 执行 | `execute_action_type_async:413-482` | 先读对象（`:454`），再在 `transaction_scope()` 内写 execution + 覆盖层（`:458-476`），原子性已经成立 |
| 覆盖层写入 | `_append_overlay_edits:484-506` | **逐属性** append |
| 覆盖值读取 | `latest_object_values_async`（DAO `:89-111`） | 按 `id desc` 取每属性首条，append-only |
| test run | `test_run_action_type_async:575-587` | 只校验参数 + 解析编辑，返回 `executed=false`；**不读对象、不算 diff** |
| 审计 | `create_execution_async`（DAO `:35-60`） | 一次执行一行，`execution_status` 直接写 `succeeded`，**无中间态**、无 version、无被编辑对象集合 |
| 撤销 | — | 不存在，覆盖层只增不减 |
| 列表口径 | `object_projection.py:482` | `_apply_object_edits` **全仓只被调用一次**（单对象读），列表/搜索**不合并**覆盖值 |
| 幂等 | — | 无幂等键，重复点击产生两条 execution |
| 写性门控 | `ontology_validator.py:29` | `read_only` / `action_writable` / `action_created` 三态；`action_created` 与 Dataset 绑定互斥（`ontology_service.py:321-328`） |

三条推论：

1. `create_object` 已落地，L2 的首要工作**不是补编辑类型**，而是补事务语义；
2. 覆盖层与源数据之间没有任何基线记录，源值改了、源行没了都无从发现；
3. 列表/详情口径不一致是**已经存在的缺陷**，与官方「单一读路径」不符（官方对象存储统一读，见 `how-edits-applied.md:20`）。

## 4. 目标与非目标

**目标**

- G1 一次 Action 支持多属性编辑，并可附带关系编辑（`create_link` / `delete_link`）；**`create_object` 与 `set_property` 不混合**（官方禁止修改本次新建的对象，`action-types/rules.md:19`）；编译语义对齐官方（每对象一条编辑）
- G2 test run 返回字段级 proposed changes 与 execution log，零写入
- G3 冲突分两层落地：读时解决策略（官方两档）+ 提交时对象级检测
- G4 撤销：追加反向编辑，语义对齐官方（仅执行者、对象级最近一次、不撤副作用）
- G5 列表与详情口径一致
- G6 提交条件最小内核（能否提交 + 失败文案）

**非目标（本期不做，理由见 §14）**

- 回写源库 / 语义模型（官方靠 Pipeline + OSv2 的 merged dataset，`how-edits-applied.md:75`，不在我们层面）
- 副作用（notification / webhook）、函数式 Action、interface / struct
- 参数系统增强（默认值、override、filter、type class）
- 批量 Action（Object Set 入参）、多对象多类型单事务
- 完整 BPM 审批流（只做占位）

## 5. 两层冲突模型（本设计的核心）

### 5.1 层一：读时解决（对齐官方两策略）

官方把「数据源更新 vs 用户编辑」的冲突放在读时解决，配置在**对象类型 / 每个数据源**上（`how-edits-applied.md:127-129`）。我们在对象类型上加两个字段：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `conflict_resolution` | `apply_user_edits`（默认）/ `apply_most_recent_value` | 命名直接沿用官方两策略 |
| `conflict_timestamp_property` | 属性名（可空） | 选 `apply_most_recent_value` 时**必填**（官方要求 timestamp 类型，`how-edits-applied.md:129`） |

行为：

- **`apply_user_edits`**：覆盖值恒胜（= 当前隐含行为，本版只是把它显式化、可配）。
- **`apply_most_recent_value`**：仅当「覆盖记录的 `applied_at` > 输入数据源该行的 timestamp 属性值」才采用覆盖值；源行值为空 → 覆盖值生效（对齐 `how-edits-applied.md:167`）；**只比较输入数据源的值**，不比较被覆盖过的 timestamp（`:185`）。
- **未绑定物理列的属性**（本项目等价于官方 edit-only property）：覆盖值**始终生效**（`:169-171`）。
- 定义期校验：选该策略但 `conflict_timestamp_property` 未配置 / 类型不是 timestamp / 未被绑定到数据源 → 拒绝保存。

> 与 v1 的差别：v1 的 `source_wins` 是「源赢了就跳过这次写入」，官方没有这种写入期丢弃；官方是**值仍然写进去，只是读的时候被源值盖住**。后者才可逆、可解释。

### 5.2 层二：提交时检测（对齐官方对象级写写冲突）

官方：对象级、失败则整单丢弃、默认 snapshot 隔离（`consistency-guarantees.md:55,59,67`）。我们没有 MVCC 与对象版本列，用**基线指纹**做等价物：

- test run / 执行开始时，读目标对象并记录 `baseline`：`{ "row_hash": "sha256:…", "read_at": "…" }`
- 提交时重读同一对象，重算 `row_hash`
- 不一致 → 冲突：按 `conflict_check_level` 处理

| `conflict_check_level` | 含义 | 对应官方 |
| --- | --- | --- |
| `object`（默认） | 对象**任一被绑定属性**变化即冲突 | 对齐 `consistency-guarantees.md:55`（对象级，不同属性也算） |
| `property` | 只有**被编辑属性**变化才冲突 | 比官方宽松，用于源表频繁刷新但业务上无关的场景 |
| `off` | 不检测 | 等价 Read committed（官方尚未上线，`:75`） |

冲突后行为：默认**整单丢弃并报错**（官方语义），不写任何覆盖值。

> 与 v1 的差别：v1 默认 `reject` 是「数据源漂移就拒绝」，与官方默认「用户编辑胜出」相反；现在默认按官方来，严格模式是可选开关。

### 5.3 行消失（对齐官方 T1/T2/T5/T12/T14）

官方用一张时序表把这件事讲得很清楚（`how-edits-applied.md:103-119`）；我们逐条对位：

| 场景 | 官方行为 | 我们的实现 |
| --- | --- | --- |
| 源行消失（T1/T4） | 对象 `deleted=true`，**不再可见**；编辑保留 | 读时派生 `visibility=hidden`，覆盖行不动 |
| 源行重新出现（T2/T5） | 对象回来，**旧编辑仍生效** | 读时重新合并覆盖值，无需任何补偿 |
| 只改未覆盖属性（T6） | 源值直接生效 | 现状已符合（`{**row, **edits}`） |
| 用户 Create 的对象（T9/T12） | 源行消失后**对象仍在**（因为它由用户编辑创建） | `action_created` 对象走 `chat_data_ontology_action_object`，本就不依赖源行，已符合 |
| 对已消失对象 Modify（T14） | **直接失败** | 提交前 `read_object_projection` 失败即整单失败，已符合 |
| 删除后重建（`:86`） | 新对象**不继承**旧编辑 | 已按官方实现：覆盖层带 `object_generation`，实例表推进代际，旧代际不参与合并（§13.1） |

关键修正：**不落 `orphan_flag`，不做读路径永久过滤**。孤儿是「暂时不可见」而不是「数据死亡」，否则行回来时编辑就永久丢了，与官方 T5 相反。

### 5.4 write skew 盲区（必须写在文档里）

官方明确：snapshot 隔离只检测**写写**冲突，两个 Action 各读同一集合、各写不同对象，都能提交（`consistency-guarantees.md:107-123`）。我们同样做不到跨对象不变量保护，表达方式也是官方建议的那条：**把不变量收敛到单个共享对象上写**（`:123`）。这是能力边界，不是 bug。

### 5.5 隔离级别对照

| 官方级别 | 我们 | 说明 |
| --- | --- | --- |
| Legacy | ✅ 等价 | 读不保证同一时点，写按对象检测 |
| Snapshot（默认） | ❌ | 需要对象版本或 MVCC；列为扩展项 |
| Read committed | ⚠️ 近似 | `conflict_check_level=off` 语义接近，但不保证并发读最新 |

因此 `execution` 记录里要写 `isolation_level=legacy_equivalent`，避免对外宣称有快照读。

## 6. 编辑契约 v2 与编译语义

### 6.1 结构

```json
{ "operation": "set_property", "property": "amount", "value": { "kind": "parameter", "name": "new_amount" } }
```

`operation` 扩到四种：`set_property`、`create_object`、`create_link`、`delete_link`。

值来源对齐官方四类（`action-types/rules.md:37-40`）：`parameter` / `object_property`（对象引用参数的属性）/ `static` / `current_user_or_time`。本期先做前三类，第四类留接口位（官方限定它「固定于执行开始」且不能用于 action 其它部分）。

### 6.2 编译规则（对齐 `rules.md:52`）

编译发生在 `plan` 阶段，产出 `compiled_edits = {object_key, property_changes, effects}`：

```text
edits[] --compile--> { object_key, property_changes{name: value}, effects[] }
```

- 对外**每对象一条编辑**：test run 的 `proposed_changes` 按对象展示、执行审计存
  `compiled_edits_json`，与官方「compile rules to a single edit per object」对齐；
- 落库仍按属性行展开（读取侧天然按属性合并，不必改存储）；
- **同属性重复仍按定义期拒绝**（保留现有 `_normalize_edit_item:119`）：官方「后者覆盖前者」
  来自**多条规则按序合成**（`rules.md:52` 讲的是 rules 的顺序），而我们的契约是扁平
  edit 列表、没有规则对象，重复属性只可能是配置错误。等 L3 引入真正的规则数组时，
  再实现「规则顺序决定最终值」；届时 `compiled_edits` 的结构不用变。

### 6.3 非法组合（逐条对齐 `rules.md:54-56`）

- 同一次提交不能先删后建 / 先改后建（本期无 delete_object，该分支留空实现位）
- 同一对象不能在一次提交里创建两次
- 主键不可改（`rules.md:18` 与 `scale-property-limits`；我们已由 `_normalize_edit_item:115` 保证）
- modify/delete **不能引用本次新建的对象**（`rules.md:19,21`）——本期单对象目标下由「编译后 object_key 必须已存在」保证

### 6.4 写性矩阵

| 对象类型 `write_mode` | set_property | create_object | create_link / delete_link |
| --- | --- | --- | --- |
| `read_only` | 拒绝 | 拒绝 | 拒绝 |
| `action_writable` | 覆盖层 | 拒绝 | 关系覆盖层 |
| `action_created` | 写动作对象自身属性 | 建对象 | 关系覆盖层 |

定义期校验一次、执行期再校验一次（纵深防御，沿用现有两处 `write_mode` 判断位置）。

### 6.5 关系编辑边界与批量上限

- 关系覆盖只影响**读取时的 link 组装**，不改源表外键（对齐官方「FK 关系必须改外键属性」的反面：我们两样都不改源表）
- 图查询（Nebula）**不消费**关系覆盖层 → `traverse_objects` 必须返回 `source=sql_overlay`，否则「建了关系图里没有」一定被当 bug
- 批量：官方 50 类型 / 10,000 对象 / 3MB 单编辑（`scale-property-limits.md`，见 `palantir-study/03-action-types.md:47`）。本期显式降级为：**单对象、`_MAX_EDITS=20`**，并在 API 文档与 README 写明，不假装支持批量

## 7. 提交条件（最小内核）

对齐官方机制（`submission-criteria.md:10,18,103`），只做能在我们身份体系里落地的那部分：

```json
{
  "submission_criteria": {
    "logic": "all",
    "conditions": [
      { "subject": { "kind": "object_property", "property": "status" },
        "operator": "is", "value": { "kind": "static", "value": "open" },
        "failure_message": "只有 open 状态可以改优先级" }
    ]
  }
}
```

- 条件模板：`parameter` / `object_property`（本期对象固定为动作目标对象）；**不做** `current_user`（无 multipass，官方 `:28-32`）、`execution_context`（`:44`）
- 算子：`is` / `is_not` / `gt` / `gte` / `lt` / `lte` / `in` / `includes_any` / `is_null`；多值语义官方是 **OR**（`parameters-filter.md:40`）
- 逻辑：`all` / `any`，本期不做多层嵌套（官方可嵌套，`:99`）
- 失败即不可提交，返回**根级失败文案**（官方 `:101-103`）
- 与权限的关系：官方把提交条件当「业务权限」（`permissions.md:20`），我们的执行授权仍沿用数据源 member 口径，提交条件是叠加上去的一层

## 8. test run（对齐官方，而非新造 preview）

扩展现有 `POST /admin/meta/ontology/action-types/{name}/test-run`，**不新增端点**。输出结构对齐官方三块：

```json
{
  "executed": false,
  "proposed_changes": {
    "objects": [
      { "kind": "modified", "object_key": "member:order:1001",
        "property_changes": [ { "property": "amount", "current": 100, "proposed": 120 } ] }
    ],
    "links": [ { "kind": "created", "relation": "...", "from": "...", "to": "..." } ]
  },
  "execution_log": [
    { "step": "metadata_loading", "status": "ok" },
    { "step": "dependency_validation", "status": "ok" },
    { "step": "submission_criteria", "status": "ok", "detail": [] },
    { "step": "parameter_validation", "status": "ok" },
    { "step": "edits_calculation", "status": "ok", "detail": { "compiled_edits": 1 } }
  ],
  "errors": { "admin_facing": [], "end_user_facing": [] }
}
```

硬要求：

- **零写入**：用例断言执行前后 `object_edit` / `action_execution` 行数不变（官方 `test-run.md:12`）
- **权限口径一致**：test run 与 execute 用同一套对象读取授权与提交条件（`:24`）
- **错误分级**：内部异常进 `admin_facing`，业务拒绝进 `end_user_facing`（`:50-53`）
- 「No proposed changes」是合法结果，不是错误（`:38`）

## 9. 执行、状态机与审计

`execution_status` 从「直接写 succeeded」改为状态机：

```text
pending ──approve──> applying ──> succeeded
   │                     └──────> failed
   ├──reject──> rejected
   └────────────(无需审批)──────> applying
succeeded ──revert──> reverted
```

审计字段对齐官方必记项（`action-log.md:63-68`）：

| 官方必记 | 我们的列 |
| --- | --- |
| Action RID | `id` |
| Action type RID | `action_type_name` |
| **Action type version** | 新增 `action_type_version`（定义每次更新自增，官方 `action-log.md:65`） |
| commit timestamp | `create_time` |
| Current user | `create_by` |
| **Edited objects（主键）** | 新增 `edited_object_keys`（JSON 数组） |
| 是否被 revert 及 revert 的 user/time | `revert_of_execution_id` + `reverted_by` / `reverted_at` |

只记**成功**的提交（官方 `action-log.md:32`）：失败执行也落行但 `status=failed`，且不产生 edit。

差异项：官方是「action type ↔ log 对象类型」一一对应、日志本身是对象、可被查询与聚合；我们**不建 log 对象类型**，用 execution 表 + 管理端只读视图承担。理由是对象化会立刻牵出「日志对象类型的定义、权限、编辑」三件事，收益低于成本（本期）。

## 10. 撤销

对齐官方三条硬约束（`action-reverts.md:14,18,42`）：

1. **仅执行者本人**可撤销（管理员例外需 `CHAT_DATA_ONTOLOGY_ACTION_REVERT_ADMIN=1` 显式开启并记审计）
2. 仅当该 action 是对象**最近一次编辑**——判定用「该对象是否存在 id 更大的 edit」，**对象级**，不区分属性
3. 撤销**不触发也不回滚副作用**（我们本期无副作用，写进文档避免以后误解）

执行方式：

```text
revert(execution_id)
  → 校验：状态 succeeded、未撤销过、所有被编辑对象无更晚 edit
  → 对每个被编辑属性追加一条反向 edit：value = 执行前的有效值（取自 baseline 快照）
  → 原 execution 置 status=reverted，记 reverted_by / reverted_at
  → 追加一条 revert 类型 execution，revert_of_execution_id 指向原执行
```

不物理删除任何行；覆盖层读取天然「取最新」，因此反向 edit 落地即生效，**不需要 `revoke_flag`**（这也是 v1 的一个多余设计）。

## 11. 读路径口径一致（本项目缺陷，官方无对应问题）

官方对象存储是单一读路径（`how-edits-applied.md:20`），不存在列表/详情不一致；我们的列表根本没合并覆盖值（`object_projection.py:482` 唯一调用点）。

- **L2.1 轻量版**：列表返回 `has_overrides: true` + 详情拉取，UI 给「已编辑」角标
- **L2.2 完整版**：`objects/search` 按当前页 `object_keys` 批量取最新覆盖值合并
- 约束：只查当前页 key，不做全表；合并入口统一收敛到 `latest_object_values_async`，禁止其他模块直查 `object_edit`

## 12. 数据模型与 DDL

改动落 `next_version.md`（新环境直接建表，存量执行同一 DDL），`utils/models.py` 同步。

```sql
-- 对象类型：读时冲突解决（对齐 how-edits-applied 两策略）
ALTER TABLE `chat_data_ontology_object_type`
  ADD COLUMN `conflict_resolution` varchar(32) NOT NULL DEFAULT 'apply_user_edits'
      COMMENT '冲突解决: apply_user_edits/apply_most_recent_value',
  ADD COLUMN `conflict_timestamp_property` varchar(128) NOT NULL DEFAULT ''
      COMMENT 'apply_most_recent_value 时用于比较的 timestamp 属性';

-- Action Type：提交条件 + 版本
ALTER TABLE `chat_data_ontology_action_type`
  ADD COLUMN `submission_criteria_json` text COMMENT '提交条件JSON',
  ADD COLUMN `config_version` int NOT NULL DEFAULT '1' COMMENT '定义版本，每次更新自增';

-- 执行审计：状态机 + 基线 + 编译快照 + 撤销溯源
ALTER TABLE `chat_data_ontology_action_execution`
  ADD COLUMN `action_type_version` int NOT NULL DEFAULT '0' COMMENT '执行时的定义版本',
  ADD COLUMN `edited_object_keys` varchar(2048) NOT NULL DEFAULT '' COMMENT '被编辑对象主键JSON数组',
  ADD COLUMN `baseline_json` text COMMENT '执行前基线（row_hash/属性值快照）',
  ADD COLUMN `compiled_edits_json` text COMMENT '编译后的每对象一条编辑',
  ADD COLUMN `conflict_check_level` varchar(16) NOT NULL DEFAULT 'object' COMMENT 'object/property/off',
  ADD COLUMN `isolation_level` varchar(32) NOT NULL DEFAULT 'legacy_equivalent',
  ADD COLUMN `idempotency_key` varchar(64) NOT NULL DEFAULT '' COMMENT '幂等键（本平台扩展，非官方概念）',
  ADD COLUMN `revert_of_execution_id` bigint NOT NULL DEFAULT '0',
  ADD COLUMN `reverted_by` varchar(100) NOT NULL DEFAULT '',
  ADD COLUMN `reverted_at` datetime NULL,
  ADD KEY `idx_action_execution_idem` (`action_type_name`,`idempotency_key`);
```

`object_edit` **不加** `revoke_flag` / `orphan_flag`（理由见 §5.3、§10）；只补 `applied_at`（`apply_most_recent_value` 比较用）与 `value_source`（`parameter` / `static` / `object_property`，便于 test run 复现）：

```sql
ALTER TABLE `chat_data_ontology_object_edit`
  ADD COLUMN `applied_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '覆盖生效时间（读时策略比较用）',
  ADD COLUMN `value_source` varchar(32) NOT NULL DEFAULT 'parameter' COMMENT '值来源: parameter/static/object_property';
```

## 13. API 与工具变更

| 变更 | 端点 | 说明 |
| --- | --- | --- |
| 扩展 | `POST .../action-types/{name}/test-run` | 返回 proposed_changes / execution_log / errors（原返回结构向后兼容保留 `edits`） |
| 扩展 | `PUT .../action-types/{name}` | 支持混合 edits、`submission_criteria`；写入时 `config_version+1` |
| 扩展 | `POST .../action-types/{name}/execute` | 增加 `idempotency_key`、`expected_baseline`；返回 `compiled_edits` 与冲突信息 |
| 新增 | `POST .../action-types/{name}/executions/{id}/revert` | 撤销 |
| 扩展 | `ontology_tool` | `execute_action` 增加 `dry_run`（走 test run）；新增 `revert_action` |

工具侧策略：**智能体默认先 test run**。`execute_action` 未带 `expected_baseline` 时先内联跑一次 test run 并把 `proposed_changes` 一并返回，模型据此决定是否提交——避免「凭想象改数据」。

## 14. 对齐与偏差清单

**已对齐（本版按官方实现）**：单事务原子性、每对象一条编辑的编译语义、对象级写写冲突检测、读时两策略冲突解决、行消失不失效、提交条件机制、test run 结构、action log 必记字段、revert 三条硬约束。

**有意偏离（写清理由，避免被当 bug）**

| 项 | 官方 | 我们 | 理由 |
| --- | --- | --- | --- |
| 对象存储 | OSv2 + Funnel merged dataset | 覆盖层表 + 读时合并 | 无对象存储与写回链路 |
| 快照隔离 | 默认 snapshot | legacy 等价 | 需要对象版本列 |
| 日志形态 | log 对象类型（可查询聚合） | execution 表 | 对象化牵出定义/权限/编辑三件事 |
| 幂等键 | 无（重试重算） | 有 | 我们是 HTTP 调用，重试会重复点击 |
| 提交条件模板 | 含 user / execution context | 仅参数 + 对象属性 | 无 multipass 身份体系 |
| 批量 | 50 类型 / 10,000 对象 | 单对象 / 20 编辑 | 先保证语义正确 |
| edit-only 属性 | 独立概念 | 用「未绑定物理列」等价 | 数据模型差异 |

## 15. 分期与验收

### L2.1 定义与预演（已实现，2026-11-04）

- 契约 v2：`submission_criteria` 定义与校验（参数/对象属性 × 9 个算子 × all/any）
- 编译语义：`compiled_edits`（每对象一条）+ 执行审计落 `compiled_edits_json`
- test run 扩展：`proposed_changes` / `execution_log` 五步 / `errors{admin_facing,end_user_facing}`
- `config_version` 自增，执行记录 `action_type_version`、`edited_object_keys`
- `idempotency_key` 幂等（重复提交返回既有执行）
- agent 侧 `execute_action` 支持 `dry_run`（只跑 test run，不受执行开关门控）
- 代码：`services/ontology/action_type_service.py`、两个 DAO、`schema.py`、`routers/ontology_admin.py`、
  `agent/tools/ontology_tool.py`；DDL 见 `next_version.md`「本体 Action L2.1」
- 验收：新增 9 条用例（test run 差异/零写入/条件失败文案/条件校验/幂等/版本），
  本体全量 399 passed

### L2.2 冲突与撤销

**L2.2-a 对象实例可见性与代际（已实现，2026-11-04）**

- `delete_object` 规则（官方 Delete object(s)）：删除只改可见性，不删覆盖值
- `chat_data_ontology_object_instance`：`lifecycle_status` + `generation`
- 覆盖层 `object_generation`：读路径只合并当前代际，重建不继承旧编辑
- `set_property` 放开到 `action_created`（动作对象可 Modify）
- 启动自检新增实例表与代际列
- 验收：新增 9 条用例（删除不可见/零覆盖写入/已删对象再操作失败/重建代际 +1/旧代际不合并/自检缺表缺列），
  本体全量 408 passed

**L2.2-b 冲突解决与撤销（已实现，2026-11-04）**

- `conflict_resolution` 两策略 + `conflict_timestamp_property` 校验 → 读时合并（见 §19.1）
- `conflict_check_level` 三级 + 基线比对（test run 出 `baseline.row_hash`，提交回传 `expected_baseline`）
- revert（执行者本人 + 对象级最近一次 + 追加反向 edit + 标记原执行）
- 列表 `has_overrides` 标记（覆盖值合并仍只在详情）
- 验收：基线冲突/口径不符/关检测放行、指纹归一、撤销六类拒绝路径、删除撤销恢复可见、
  创建撤销标删除；Action 单测 54 passed，本体相关 5 文件 203 passed
- 未做（见 §19.5）：源绑定类型的 Create（对象只存在于编辑层）、列表合并覆盖值、聚合排除已删对象

### L2.3 审批与关系（后续）

- `requires_approval` 状态机（pending 不写覆盖层、不参与读合并）
- `create_link` / `delete_link` + 图查询 `source=sql_overlay` 标注
- 验收：pending 不生效、通过后生效；关系覆盖详情可见而图查询标注来源

## 16. 风险与对策

| 风险 | 说明 | 对策 |
| --- | --- | --- |
| 认知错位 | 用户以为在改源库，其实只改覆盖层 | 详情标注「已覆盖（值来自 Action，非源数据）」；执行返回带 `source_of_truth: overlay` |
| 基线假冲突 | 浮点/时间格式差异导致哈希不一致 | 规范化后再哈希（Decimal 去尾零、时间统一 UTC 秒级、字符串 trim）；严格模式留 `force=true` 逃生口并记审计 |
| 读路径漏收敛 | 读时策略分散在多处实现 | 覆盖值读取只允许走 `latest_object_values_async` 单一出口，禁止直查 `object_edit` |
| 关系覆盖与图库不一致 | 图查询读 Nebula | 返回标注 `source=sql_overlay`，文档写清边界 |
| 列表合并性能 | 批量 IN + 派生 JOIN 叠加 | 先做轻量标记版；合并版限定当前页 key |
| 编译语义改动面 | last-wins 会改变现有「重复即报错」的行为 | 现有用例若依赖报错需同步调整；在 README 与 API 文档标注行为变更 |

## 17. 待确认（三件）

1. **行删除后同 key 重建是否继承旧编辑** —— **已定：按官方口径，不继承**（2026-11-04 实现，见 §18）。做法是引入「对象实例代际」：`chat_data_ontology_object_instance` 记录可见性与 `generation`，覆盖层带 `object_generation`，读路径只取当前代际。顺带补齐了官方 Delete object(s) 规则（此前只有 Modify/Create），否则「重建」无从发生。
2. **撤销权限**：官方仅执行者本人。**已定：不开管理员例外**（默认只有 `create_by` 本人可撤销），理由是无审批/角色体系时「管理员」边界不可靠，开例外反而让审计口径变模糊。若后续上审批体系再评估。
3. **`conflict_resolution` 的默认值**：**已定：默认 `apply_user_edits`**，与官方一致，且不改变现有对象类型的实际行为（此前隐含就是覆盖值恒胜）。需要「源数据为准」的对象类型按类型单独改，不做全局默认变更。

## 18. 实现记录：L2.2-a 对象实例可见性与代际（2026-11-04）

**目标**：把 §5.3 里「删除不是编辑」「重建不继承」两条从设计变成可执行语义。

### 18.1 为什么必须先有 Delete

官方 `rules.md:19-21` 的规则顺序是 Create / Modify / Delete，删除是独立规则，不是「把属性改成空」。
我们此前只有 `set_property` 与 `create_object`，**没有删除动作**，所以「删除后重建」这个场景在平台里根本不会发生，
代际也就无从谈起。因此本次一并补上 `delete_object`。

### 18.2 三条官方语义的落点

| 官方 | 我们的实现 | 代码 |
| --- | --- | --- |
| 删除不是编辑：删除后不可见，与数据源状态无关 | 实例表 `lifecycle_status=deleted`；读取先判可见性，命中即抛「对象不存在」，不再合并覆盖值 | `object_projection.py` `_object_instance_state_async` |
| 删除时编辑保留 | `delete_object` **不写、不删** `object_edit` 任何行；只更新实例状态 | `action_type_service.py` `_execute_delete_object` |
| 重建不继承旧编辑 | 重建时代际 +1；覆盖层读取带 `object_generation` 过滤 | `ontology_object_instance_dao.py` `revive_async`、`latest_object_values_async` |
| 对已删对象 Modify 必失败（T14） | 读取失败 → 整单失败，不会产生执行与覆盖写入 | `execute_action_type_async` 先读后写 |

源行消失 / 复现（T1/T2/T5）无需额外代码：覆盖层本就独立于源行，行消失时读取报「不存在」、编辑仍在；
行回来后读取重新合并，旧编辑自然复现——这正是 §5.3 要求的「不落孤儿标记」。

### 18.3 顺带修掉的一个能力缺口

`set_property` 原先只允许 `action_writable`，意味着**动作对象（`action_created`）创建后无法再修改**，
与官方 Modify object 规则不符。本次放开到两种写性，动作对象读取改为「存储属性 + 当前代际覆盖值」。
这也让「创建 → 修改 → 删除 → 重建 → 旧修改不生效」成为可端到端验证的场景。

### 18.4 边界（写清，避免当 bug）

- **源绑定对象类型的「重建」仍不可达**：`create_object` 目前只支持 `action_created` 类型，
  所以源绑定类型删除后是终态（官方同样是「删除后不可见」，只是官方允许用 Create 规则重建）。
  等 L2.3 支持「在源绑定类型上 Create（对象仅存在于编辑层）」后，代际机制可直接复用。
- **可见性已覆盖列表/搜索，但覆盖值仍只在详情合并**：列表与搜索按当前页主键批量剔除已删除实例
  （一次 IN 查询，`deleted_keys_async`），但**不合并覆盖值**（既有口径缺陷，L2.2-b 的 `has_overrides` 一并处理）。
- **聚合/分组不排除已删除对象**：过滤要在 SQL 层注入 `NOT IN` 子查询，与派生 JOIN 叠加会加重超时风险，
  留到 L2.2-b 与「列表合并」一起做。
- **多动作混合仍未放开**：官方允许一条 Action 内 Create→Modify→Delete，我们仍是「一条 Action 一种操作」，
  因为扁平契约没有规则对象表达顺序（§6.2）。

## 19. 实现记录：L2.2-b 冲突解决与撤销（2026-11-04）

**目标**：补齐 §5.4（读时冲突）、§5.5（写写冲突）、§7（撤销）三块，且严格按官方命名与判据。

### 19.1 读时冲突：两策略，按对象类型配置

| 官方（`how-edits-applied.md:167-187`） | 我们 |
| --- | --- |
| `apply_user_edits`：用户编辑恒胜（默认） | `conflict_resolution` 默认值，覆盖值直接盖源值 |
| `apply_most_recent_value`：比较编辑生效时间与源行时间戳 | 覆盖 `applied_at` vs 源行 `conflict_timestamp_property` 值，仅覆盖更晚才生效 |
| edit-only 属性（无源列）恒生效 | 属性不在源行 → 直接采用；`action_created` 类型全部按 edit-only 处理 |

- 配置在对象类型上（`conflict_resolution` + `conflict_timestamp_property`），存 `chat_data_ontology_object_type`；
  选 `apply_most_recent_value` 必须给时间戳属性，保存时校验该属性存在且绑定物理列（`ontology_service._validate_conflict_resolution`）。
- 合并收口在 `object_projection._merge_conflict_values`，读路径只有 `_merge_object_edits` 一个出口，
  不允许旁路直查 `object_edit`（§16 风险对策）。
- 整体编辑未显式传该字段时保留原策略（`_preserve_conflict_resolution`），避免旧客户端保存静默重置。

### 19.2 提交时写写检测：基线指纹

- test run 结果新增 `baseline`：`{row_hash, row_hash_scope, effective_values, object_generation, read_at}`。
- 提交时把 `row_hash` 作为 `expected_baseline` 传回，服务端**重新读源行重算指纹**再比对；
  不一致 → 拒绝提交并提示「对象已被修改（写写冲突）」，**零写入**（不进事务、不落执行记录）。
- `conflict_check_level` 三档：`object`（整行，默认）/ `property`（只比本次要改的属性）/ `off`（不比，仍记基线供撤销用）。
  口径不一致（调用方传的是 property 指纹、服务端按 object 比）单独报「基线口径不一致」，不伪装成真冲突。
- 指纹归一（`_row_fingerprint`）：Decimal 去尾零、时间统一 UTC 秒级、字符串 trim、key 排序后哈希，
  避免浮点/时区格式差异造成假冲突（§16）。
- **比的是源数据不是合并值**：`read_object_projection(..., with_source_values=True)` 取未合并覆盖值的源行，
  否则自己刚写的覆盖值会让基线永远对不上。

### 19.3 撤销：追加反向编辑，不删原记录

官方 `action-reverts.md` 的判据逐条落地：

| 官方判据 | 实现 |
| --- | --- |
| 只有执行者本人能撤销 | `execution.create_by != operator` → 拒绝（不开管理员例外，见 §17-2） |
| 只能撤销对象级最近一次编辑 | `latest_edit_async` 取该对象当前代际最新 edit 的 `execution_id`，不等于本次 → 拒绝「又有新的编辑」 |
| 只能撤销成功且未被撤销的执行 | `status != succeeded`、`revert_of_execution_id != 0` 各自拒绝 |
| 撤销是新增一条执行 + 反向编辑 | 新建执行（`revert_of_execution_id=原 id`、`conflict_check_level=off`）并 append 反向 edit，原执行标 `reverted` + `reverted_by/at` |
| 反向值取自执行前快照 | `baseline.effective_values[属性]`（执行时被编辑属性的有效值），缺失则该属性无法撤销并显式报错 |

- `delete_object` 的撤销：实例状态 `deleted → active`，**代际不变**（撤销删除不是重建，旧编辑继续生效）；
  若对象已被重建（代际变了）→ 拒绝「对象状态已变化」。
- `create_object` 的撤销：对象标 `deleted`（等价官方「撤销创建」），并要求该对象此后没有新变更。

### 19.4 代码与验收

- 主要文件：`services/ontology/action_type_service.py`（`_row_fingerprint` / `_check_baseline` /
  `revert_execution_async`）、`object_projection.py`（`_merge_conflict_values` / `has_overrides`）、
  `daos/ontology_action_execution_dao.py`（`latest_object_edits_async` / `latest_edit_async` /
  `mark_reverted_async`）、`ontology_validator.py` + `ontology_service.py`（策略校验）、
  `schema.py` / `routers/ontology_admin.py`（`expected_baseline`、`conflict_check_level`、revert 端点）、
  `agent/tools/ontology_tool.py`（`expected_baseline`、`revert_action`）、`schema_check.py`（三列自检）；
  DDL 见 `next_version.md`「本体 Action L2.2-b」。
- 验收：新增 20 条用例——基线冲突零写入、基线匹配记录有效值、`off` 放行、口径不符、指纹归一、
  撤销成功/非执行者/有更晚编辑/已撤销/撤销记录本身/缺基线、删除撤销恢复可见且代际不变、
  删除撤销遇重建拒绝、创建撤销标删除；API 侧补 3 条（新参数透传、非法口径 422、revert 路由）。
- 回归：本体相关 5 个测试文件 203 passed（含 Action 单测 54）；`chat_data_app` 全量 5297 passed。
  5 条失败已在 HEAD 干净工作树复现，属既有环境问题（4 条 `test_filesystem_tools_branch_coverage`
  被本机 `sitecustomize` 拦 `mkdir`，1 条 `test_conversation_memory_service` 后台落盘 50ms 超时），
  与本次改动无关。

### 19.5 仍未做（边界，不是 bug）

- **源绑定类型的 Create**：对象只能存在于编辑层，需要「编辑层对象」的读投影与列表合并，属 L2.3。
- **列表/搜索不合并覆盖值**：只标 `has_overrides`，合并仍限详情；批量合并要按当前页 key 二次查询，留待性能评估。
- **聚合/分组不排除已删除对象**：需在 SQL 注入 `NOT IN` 子查询，与派生 JOIN 叠加有超时风险。
- **官方按用户/组配置冲突策略**：我们只有对象类型级单值配置，没有多策略按主体切换。
- **`apply_most_recent_value` 是近似语义**：官方比的是「对象存储 vs 输入数据源的一次 sync 写入」，
  我们没有对象存储、源数据是每次查询实时读 SQL，只能拿业务表某个 timestamp 属性当作「源侧最后
  写入时间」。若该列不是行版本时间（例如是业务创建时间）或不随更新变化，策略会退化。配置前必须
  确认该列语义，不能只看「有没有这个列」。

## 20. 闭环验证（2026-11-04，方向复盘后补做）

复盘发现 L2.2-b 的语义虽对，但**交付闭环断在调用方与验证层**：5297 条单测通过，
然而提示词没更新、`expected_baseline` 可选且空值直接跳过、新增 DAO 只有 fake 单测、
DDL 只写在文档没执行过。本节记录补做内容与抓出的真实缺陷。

### 20.1 抓出的四个真缺陷（都由新增测试首次发现）

| 缺陷 | 后果 | 修复 |
| --- | --- | --- |
| 部署 DDL 漏 `applied_at` / `isolation_level` / `reverted_by` / `reverted_at` | ORM 默认 SELECT 全字段，缺列在运行期才炸 | 补齐 `next_version.md` 的 CREATE 与 ALTER，并加「文档 DDL 列 = ORM 列」全量断言 |
| `conflict_timestamp_property` DDL `varchar(64)` vs 模型 `String(128)` | 长属性名被截断 | 统一为 128，并由测试锁死 |
| `object_generation` 同时出现在 CREATE 与 ALTER，CREATE TABLE 又缺 L2.1 全部新列 | 新库缺列、存量库重复执行报 Duplicate column | CREATE TABLE 改为最终完整形态；文档明确「新库只执行 CREATE，所有 ALTER 仅供存量库」 |
| `chat_data.py` 两处提示词仍写「`execute_action` 执行」 | LLM 不知道 dry_run / baseline / revert 存在；`_check_baseline` 空值直接 return，冲突检测在真实链路上默认不生效 | 提示词改为两步提交；服务端兜底自动补试运行；加提示词守卫测试 |

### 20.2 接通调用方：两步提交 + 服务端兜底

- `ONTOLOGY_WORKFLOW_BLOCK` / `ONTOLOGY_SUBAGENT_HINT` 明确写：`list_action_types` →
  `execute_action(dry_run=true)` 看 `proposed_changes`/`errors` →
  `execute_action(dry_run=false, expected_baseline=原样回传)`；冲突则重新试运行，不用旧基线重试；
  撤销用 `revert_action` 传 `execution.id`。
- **不把正确性押在 LLM 跨轮传字符串上**：`_run_execute_action` 在模型漏传 baseline 时，
  自动补一次只读 test run 取基线再提交，返回 `baseline_source=auto_preview`；模型正常传了就用
  模型的（`caller`），保留「预演 → 用户确认 → 提交」之间真实的冲突窗口。
  管理 API 的 `expected_baseline` 仍可选，兼容旧客户端。
- 补齐三类操作的一致性：`create_object` 的 test run 现在也返回预览基线（scope=create，
  提交侧不比对）；`delete_object` 提交改为**接受并校验** `expected_baseline`
  （此前只有 set_property 校验，删除是漏网之鱼）。

### 20.3 真实 SQL 与端到端

新增 `tests/unit/test_ontology_action_l2_sql.py`：内存 SQLite + `Base.metadata.create_all`，
把 `dao_session.get_session` 指过去（事务复用语义不变），所有 Action 侧读写跑真实 SQL。

- **DDL / 启动自检**：真实建表列集合通过 `missing_schema_issues(action_execute_enabled=True)`；
  `require_ontology_schema_async()` 在本体开关全开时通过；文档 DDL 声明列与 ORM 列全量比对。
- **DAO**：执行审计 JSON round-trip、代际过滤、`latest_edit_async` 取最后一条、
  `overridden_keys_async` 忽略旧代际、`mark_reverted_async` 保留行、实例唯一键、
  动作对象软删/恢复/重建。
- **端到端**：定义 → 试运行零写入 → 带基线提交 → 读回覆盖值（走生产 `_merge_conflict_values`
  + 真实覆盖层 SQL）→ 撤销 → 读回源值；以及「源行在两步间被改 → 整单取消且零写入」、
  「撤销不可重复」、删除的两步提交与撤销恢复可见性、删除的旧基线拒绝。
- **边界（如实说明）**：业务数据源用 SQLite `orders` 表模拟，`read_object_projection` 的
  Dataset/member binding 解析仍由轻量桩替代；真实 MySQL + 数据源绑定下的启动与查询未在本机跑过，
  上线前仍需在预发环境按 §20.1 的部署顺序执行一次完整冒烟。

### 20.4 验收

本体相关 6 个测试文件 **226 passed**（其中真实 SQL 闭环 18 条、Action 单测 54 条）；
`chat_data_app` 全量结果见 §20 更新时的回归记录。既有环境性失败（4 条 filesystem 被本机
`sitecustomize` 拦 `mkdir`、1 条 memory service 后台落盘超时）已在 HEAD 干净工作树复现，与本轮无关。
