# Palantir Action Type 与对象编辑研读

## 0. 依据

- 引用口径（两套基准，勿混）：① Palantir 副本内的引用一律写成 `action-types/xxx.md:行号` 或 `functions/xxx.md:行号`，基准是 `docs/palantir-ontology/`（抓取 2026-10-17，版权属 Palantir），行号是**本地文件行号**，每个副本前 6 行是抓取头、正文从第 8 行起；② 本项目引用写成 `services/ontology/README.md:行号` / `docs/ontology-capability-audit.md:行号`，基准是**仓库根**。行号连续引用时省略文件名（如 `（:18`）沿用上一条的同文件。
- 已精读（本体侧）：`action-types/` 下 overview、getting-started、explore-action-types、rules、submission-criteria、parameter-overview、parameters-default-value、parameters-filter、parameters-override、parameter-performance-considerations、use-actions、side-effects-overview、webhooks、notifications、trigger-schedule-build、action-log、action-reverts、consistency-guarantees、permissions、read-write-authorizations、branching-action-types、function-actions-overview/-getting-started/-batched-execution、actions-on-interfaces、actions-on-structs、inline-edits、upload-attachments、upload-media、scale-property-limits、test-run。
- 已精读（函数侧）：`functions/` 下 edits-overview、api-ontology-edits、python-ontology-edits、typescript-v2-ontology-edits、edits-generate-id、python-staged-writes。
- 对照侧只读：`server/apps/chat_data_app/services/ontology/README.md` 的 Action 段（11、86、98-113、191-202 行）、`docs/ontology-capability-audit.md` 的 Action 段（22、38、54、58、61、94-95、110 行）。
- 副本质量提示（不影响结论，但引用时须知）：`action-types/read-write-authorizations.md` 是把「Permissions」与「Read and write authorizations」两页拼接后又重复了一遍（1-114 行与 116-168 行同文）；`action-types/consistency-guarantees.md:8` 首行是上一页残留句；`action-types/action-reverts.md:34-36` 只剩 "Edits applied / Edits reverted" 图注。

## 1. Action Type 是什么（定义、组成、生命周期、与 object type/function 的关系）

- **一次 action = 一个事务**：「An action is a single transaction that changes the properties of one or more objects, based on a user-defined logic.」（`action-types/overview.md:10`）。中文含义：action 是面向「一个或多个对象」的单一事务，而不是逐属性编辑的界面动作。
- **action type = 编辑定义 + 副作用定义**：「An **action type** is the definition of a set of changes or edits to objects, property values, and links that a user can take at once. It also includes the side effect behaviors that occur with action submission.」（`action-types/overview.md:12`）。所以官方 action type 天然包含两块：Ontology 编辑 + 提交时副作用。
- **组成（至少四件）**：参数（输入契约，`action-types/parameter-overview.md:10`）、规则（把参数变换成编辑或其它效果，`action-types/rules.md:10`）、提交条件（能否提交，`action-types/submission-criteria.md:10`）、以及承载它的对象类型/表单/权限配置（`action-types/getting-started.md:40,54,58`）。
- **落地前置**：对象类型必须先允许编辑——「If running Object Storage v2, the user must enable edits with a toggle. If running Object Storage v1 (Phonograph), a writeback dataset must be created.」（`action-types/getting-started.md:34`）。默认仅允许通过 action 编辑（`action-types/permissions.md:42`）。
- **提交即全局生效**：「Any changes made to objects, property values, and links will be committed to the Ontology when the user takes the action and will be reflected in all user applications.」（`action-types/overview.md:29`）。同一套 action 逻辑与校验跨应用复用，最终落到对象类型的 writeback dataset。
- **与 function 的关系**：function 是「规则」的一种实现方式，且**互斥**——「**Function rule:** ... When this rule is present, no other rule may be configured since function code alone is capable of handling everything that other rules can do.」（`action-types/rules.md:24`，另见 `action-types/explore-action-types.md:29`）。函数本身不能改数据，必须挂成 action 才生效（`functions/edits-overview.md:136`：「The only way to update objects using a function is by configuring an action」）。
- **生命周期**：action type 每次更新自动升版本——「**Action type version:** Version number that auto-increments each time an action type is updated」（`action-types/action-log.md:65`）；函数规则默认**钉住**版本，「if the function logic is changed, the action does not automatically update to match it」（`action-types/function-actions-getting-started.md:62`），可选 auto upgrade 走版本区间（`action-types/function-actions-getting-started.md:66-68`）。
- **上线前的安全网**：test run 只模拟不落库——「The edits produced by a test run are **not** applied to your Ontology.」（`action-types/test-run.md:12`），并且 test run 会真实执行必要的外部调用（`action-types/test-run.md:69`）。

## 2. 参数系统（参数类型、默认值、覆盖、过滤、性能限制；用户输入 vs 绑定对象）

- **定义**：「**Parameters** are the inputs of an action type. They are the interface between the **Rules** and other Foundry applications」（`action-types/parameter-overview.md:10`）；每个参数由 type 决定取值域，且可单独配置「是否在表单暴露、是否允许用户改」。
- **参数的四类引用点**：「...can be referenced in rules to pass the value back on an object, link, or side effect, in submission criteria, to check if an action can be submitted, to access the current value of an object property before it is changed by the action or in overrides to change the configuration of a following parameter.」（`action-types/parameter-overview.md:14`）——即 rules / submission criteria / 读旧值 / overrides 四个用途。
- **用户输入 vs 绑定对象**：同一个 action 里既有「字符串/数值等用户输入参数」，也有「对象引用参数」；官方用参数基数区分单条与批量——「**single action type** to refer to an action type using an object reference parameter, and **bulk action type** to refer to an action using an object reference list parameter.」（`action-types/use-actions.md:12`）。批量上下文只列匹配当前选中对象类型的 bulk action（`action-types/use-actions.md:14`）。
- **宿主应用可注入旧值**：「As a variable in Workshop, `previous_status` can take the current value of the `Status` property ... This can be passed to a hidden parameter in the action, `Previous Status`」（`action-types/parameter-overview.md:18`）。
- **参数类型覆盖面（官方）**：内建标量 + 对象引用/对象引用列表 + struct（`action-types/actions-on-structs.md:14` 列 base type `BOOLEAN/DATE/DOUBLE/GEOPOINT/INTEGER/LONG/STRING/TIMESTAMP`）+ attachment（`action-types/upload-attachments.md:16`）+ media reference（`action-types/upload-media.md:10`）+ 多重选择（`action-types/parameters-filter.md:14`）。可用 value type 复用校验规则（`action-types/parameter-overview.md:12`）。
- **基数上限**：原始类型 list ≤ 10,000；对象引用 list ≤ 1,000；用于提交条件的 list ≤ 1,000（`action-types/scale-property-limits.md:18-20`）。
- **默认值**：用于预填表单，「Default values are configured at the parameter level and are supported in Workshop, Object Explorer, Object Views, Quiver, and Slate.」（`action-types/parameters-default-value.md:10`），一次定义多端复用；两种来源——静态值（`:24`）与对象属性值（`:30`，且「Only object reference parameters that are placed above the parameter in the input list are available」，即只能引用**排在自己前面**的对象参数）。
- **默认值优先级**：「Local default values (for example, Workshop variables) always take precedence over global default values.」（`action-types/parameters-default-value.md:16`）——实例级覆盖 > 全局默认；反过来，迁移到全局默认必须删掉本地覆盖。
- **类型类预填**：可用 type class 自动填 UUID、当前用户 ID 等特殊值，官方建议「set the parameter visibility to `hidden`」（`action-types/parameters-default-value.md:36-38`）。
- **Overrides（按条件改参数配置）**：override block = if（条件）+ then（覆盖），可改「constraints, visibility, requiredness, and default values」（`action-types/parameters-override.md:30`）；多个 block 只有第一个命中生效（`:22,34`）；条件只能引用**排在本参数前面**的参数（`:26`）。典型用法：经理必须填理由、处理人不必填（`:12`）。
- **过滤（下拉候选限制）**：非对象引用的 multiple choice / 单对象引用参数可加 filter（`action-types/parameters-filter.md:10`）；multiple choice 可「Get options from an object set」从对象集取候选，且选项来自用户有权限查看的对象（`:14`）；对象下拉支持 filter + Search Around，且「The value selected is also validated before the action is executed.」（`:18`，即候选过滤同时是提交前校验）。过滤值为多值时是 **OR** 语义（`:40`），起始集合可换成别的类型或 `ObjectReference` 列表（`:44`）。
- **隐私坑**：对象参数过滤可能把敏感静态值暴露给所有能看该 action type 的人（`action-types/parameters-filter.md:32`）。
- **性能约束**：参数间依赖（默认值依赖、候选依赖）会串行拉长表单打开时间（`action-types/parameter-performance-considerations.md:10`）；官方建议「keep the dependency hierarchy as flat as possible」（`:26`）。
- **提交条件不支持的类型**：「Submission criteria do not support attachment and object set parameters.」（`action-types/submission-criteria.md:57`）。

## 3. 编辑类型（创建对象/修改属性/删除对象/加关系/删关系；是否支持批量；一次 action 能否编辑多对象/多类型；编辑的提交语义（原子性））

- **12 种规则清单**（`action-types/rules.md:18-29`）：Create object / Modify object(s) / Create or modify object(s) / Delete object(s) / Create link(s) / Delete link / Function / 以及 5 种 interface 版（create、modify、delete、create link、delete link）。
- **创建**：主键必填——「The primary key of the object type is a required property which has to be filled.」（`action-types/rules.md:18`）。
- **修改/删除的边界**：只能改由对象引用参数定位的既有对象，且「This cannot reference an object created as a part of the current action.」（`action-types/rules.md:19,21`）。
- **Create or modify**：未选对象时新建，「with either an automatically generated unique ID, or with a user submitted primary key」（`action-types/rules.md:20`）。
- **关系的两条路**：多对多直接用 Create link / Delete link；一对多/一对一必须改外键属性——「For foreign key links, one has to use the **Modify object** rule to explicitly modify the foreign key property.」（`action-types/rules.md:22-23`，另见 `action-types/explore-action-types.md:70`）。
- **批量**：对象引用列表参数即批量入口（`action-types/use-actions.md:12`）；批量调用上限「An action can be called a maximum of 10,000 times in a batch. This limit is reduced to 20 when the action is function-backed and the function is not configured to use batched execution.」（`action-types/scale-property-limits.md:32`）；function 可开启 batched execution，用「a single input parameter containing a list of structs」一次吃掉整批（`action-types/function-actions-batched-execution.md:14-19`）。
- **一次 action 可编辑多对象/多类型**：上限「Number of **object types** you can edit in a single action submission | 50」「Number of **objects** ... | 10,000」（`action-types/scale-property-limits.md:26-27`）；单条对象编辑体积 32KB(OSv1)/3MB(OSv2)（`:28`）。旁证：action log 举例「closing 10 `Alert` objects at once will yield a single `action log` object with foreign key links to all 10 `Alert` objects.」（`action-types/action-log.md:53`）。
- **函数式可跨类型**：function 能「Create several different types of objects and set up links between them.」（`action-types/function-actions-overview.md:16,20`）。
- **原子性**：「An action's edits to the Ontology are applied as a single, all-or-nothing batch. This does not extend to other side effects of the action, such as notifications or webhooks」（`action-types/consistency-guarantees.md:18`）；两种写模式都在结束时一次性原子提交，「other actions never observe a partially applied action」（`:46`）。
- **规则合成语义**：多条规则会被编译成「每个对象一条编辑」，「if the result of one rule updates a property to "A", but another rule ... updates the same object's property to "B", the resulting edit would just update the property to "B". The order of rules affects the final object edit.」（`action-types/rules.md:52`）。
- **非法组合**：对象不能在新增/修改前被删除；不能先改后建；同一对象不能在一次提交里创建两次（`action-types/rules.md:54-56`）。
- **主键不可改**：「Currently, actions cannot be used to edit the **primary key** of an object.」（`action-types/scale-property-limits.md:96`；另见 `action-types/actions-on-interfaces.md:54`、`functions/api-ontology-edits.md:58`）。
- **函数侧编辑会被折叠**：「Edits are collapsed intelligently so that the minimal set of edits are applied ... Deleting an object will erase any other property edits that were done before the deletion.」（`functions/api-ontology-edits.md:148`）。
- **行内编辑是另一套语义**：inline edit 里「every parameter is optional and defaults to the existing value of the object」，且要求「May only modify a single object of a single object type」「Default values must be enabled」「Side effect webhooks ... or side effect notifications ... cannot be enabled」（`action-types/inline-edits.md:10,26-32`）。
- **附件是「先传后提交」**：附件在加入表单时即上传，「If form submission fails or is canceled, the outstanding attachment ... will automatically be permanently deleted after some time.」（`action-types/upload-attachments.md:28`）；单文件上限 200MB、单个附件一生最多链 10 个对象（`:30-32`）。media 上传则在提交成功后才持久化，且权限完全由提交条件代管（`action-types/upload-media.md:21,31`）。

## 4. 规则与提交条件（区别、时机、失败行为）

- **职责切分**：rules 定义「怎么变」——「**Rules** define the logic of the action type that transforms the parameters into Ontology edits or other effects.」（`action-types/rules.md:10`）；submission criteria 定义「能不能交」——「the conditions that determine whether an action can be submitted.」（`action-types/submission-criteria.md:10`，原文注明「formerly known as validations」）。
- **提交条件是权限的一部分**：「Submission criteria support encoding business logic into data editing permissions」（`action-types/submission-criteria.md:10`）；但它与「能否编辑 action type 本身」的权限独立（`:18`），且每个 action type 各自一套（`:18`）。
- **结构**：条件 + 逻辑算子；条件模板三选一「Current user」「Parameter」「Execution context」（`action-types/submission-criteria.md:22`）；Current user 可比 user ID、group ID、multipass 属性（`:28-32`）；Execution context 目前只表达「是否在 Ontology Scenario 内提交」（`:44`）。
- **算子集**：单值 `is / is not / matches / is less than / is greater than or equals`；多值 `includes / includes any / is included in / each is / each is not`（`action-types/submission-criteria.md:73-87`）；算子按参数类型预过滤，改参数后所有相关条件需重配（`:67`）。
- **逻辑算子**可嵌套，语义为 all / any / none（`:99`）。
- **执行时机**：提交时逐条评估，全部满足才允许提交——「Actions can only be submitted if all the submission criteria are met.」（`action-types/submission-criteria.md:18`）。
- **失败行为**：每个根级条件/算子都带 failure message，下层不满足时展示对应根级文案（`:101-103`），并且「The failure message will be displayed to the end user across Foundry (Object Explorer, Workshop, or Quiver)」（`:103`）。条件不通过则副作用也不会触发——「if the action submission criteria fail, then side effects will not be triggered.」（`action-types/permissions.md:64`）。
- **反模式警告**：不要对 group/marking/organization 用 `NOT`，因为 scoped token 可能缺该属性导致条件误通过（`action-types/submission-criteria.md:34`）。
- **两者对照**（据 `rules.md`、`submission-criteria.md` 整理）：

| 维度 | Rules | Submission criteria |
|---|---|---|
| 回答的问题 | 提交后**改什么**（`action-types/rules.md:10`） | 这次提交**允不允许**（`action-types/submission-criteria.md:10`） |
| 组成 | 规则类型 + 属性→值映射（`action-types/rules.md:16-40`） | 条件模板 + 算子 + 值 + 失败文案（`action-types/submission-criteria.md:22-103`） |
| 作用域 | 一个 action type 内可多条、按序合成（`action-types/rules.md:52`） | 每个 action type 独立一套（`action-types/submission-criteria.md:18`） |
| 失败后果 | 事务整体回滚（`action-types/consistency-guarantees.md:18`） | 直接不可提交 + 展示失败文案（`action-types/submission-criteria.md:103`）、副作用不触发（`action-types/permissions.md:64`） |

- **与 override 的关系**：override 的条件与提交条件同构，唯一差别是可引用范围更窄（只能引用前面的参数）（`action-types/parameters-override.md:26`）。
- **可调试性**：test run 的 Details 页逐段展示 metadata loading / dependency validation / submission criteria / parameter validation / edits calculation（`action-types/test-run.md:44`），失败时区分 admin-facing 与 end-user-facing 错误（`:50-53`）。

## 5. 副作用与集成（side effects、webhook、notification、trigger build 的官方约定）

- **总定位**：副作用是「send data out of Foundry」，只有两类：Notifications 与 Webhooks（`action-types/side-effects-overview.md:17-20`）。
- **Notification 时机**：编辑全部落地后才发，但「the content of the notification will be generated based on the state of the Ontology before edits are applied.」（`action-types/rules.md:64`，同义见 `action-types/notifications.md:74`、`action-types/explore-action-types.md:99`）。
- **Notification 限额**：单次 action 最多 500 收件人，内容「From a Function」时降到 50（`action-types/notifications.md:99-100`，另见 `action-types/scale-property-limits.md:100`）；subject ≤250 字符、body ≤1,000、自定义 HTML ≤51,200（`action-types/notifications.md:106-107`）。
- **Notification 权限两档**：「**Require all users to have permissions (default)**」时任一收件人无权限即整单失败、不编辑不发通知；「**Require any user to have permissions**」时有权限者才收到（`action-types/notifications.md:142-144`）。发信失败不阻塞编辑——「If notifications fail to send for whatever reason, edits may still succeed.」（`action-types/permissions.md:70`）。
- **Webhook 分两种，时机完全相反**（`action-types/webhooks.md:22-25` 表）：Writeback = 对象变更**前**、失败对用户可见、错误阻断；Side effect = 变更**后**、失败不可见、可能晚于成功提示。
- **Writeback webhook**：「executed _before_ any other rules are evaluated; if the webhook execution fails, no other changes will be made.」，只允许配一个，其输出参数可供后续规则使用（`action-types/webhooks.md:31-37`）。官方也明确它只是「some degree of transactionality」——外部成功仍可能 Ontology 失败（`:33`）。
- **Side effect webhook**：可配多个、**无顺序保证**、失败不提示用户；需要多次调用时用「a list of payloads」触发多次（`action-types/webhooks.md:41-43`）。
- **Webhook 输入参数**：映射 action 参数/静态值/对象参数属性，或用函数返回强类型结构（`action-types/webhooks.md:53-55`，类型不符报 `OntologyMetadata:ActionWebhookInputsDoNotHaveExpectedType`）。
- **Webhook 鉴权**：REST API source 配 outbound application 时由 Foundry 代管 OAuth 2.0 token（`action-types/webhooks.md:189`）。
- **Schedule（trigger build）**：编辑**不等待**构建——「the action's Ontology edits are applied _after_ the build begins. Edits do not wait for the build to finish.」（`action-types/trigger-schedule-build.md:12`）；schedule 必须 project-scoped（`:16`）；可把 schedule run RID 写进对象字符串属性做追踪（`:30-32`）；权限由 action 提交条件代理（`:24-26`）。
- **分支环境默认关闭副作用**：webhook 默认不执行（`action-types/branching-action-types.md:38`）、带外部调用的函数默认整体失败（`:50`）、通知默认不发（`:60`），需在 Security & submission criteria 页显式打开。
- **重试与副作用冲突**：默认「actions without external system calls ... are retried, while actions with external system calls are not — because re-executing the action would repeat its side effects」（`action-types/consistency-guarantees.md:172`）；三档可配（`:174-178`）。
- **inline edit 禁用副作用**：inline edit action 不可开 side effect webhook / notification（`action-types/inline-edits.md:32`）。

## 6. Action Log / 回滚（action log 记录什么、revert 语义与限制、一致性保证）

- **Action log 建模方式**：「Action log object types map one-to-one with action types. Submitting an action generates a single new object of the corresponding action log object type. This newly-created object is automatically linked to all objects edited by the submitted action.」（`action-types/action-log.md:51`）——一次提交一条日志、链到所有被编辑对象。
- **只记成功**：「The action log records **successful** action submissions only. It does not record failures.」（`action-types/action-log.md:32`）；且只记**经由 action type** 的编辑，直写 datasource 不产生日志（`:33`）。
- **必记字段**（`action-types/action-log.md:63-68`）：Action RID、Action type RID、Action type version、Current timestamp（OSv2 为 commit timestamp）、Current user（Multipass user ID）、Edited objects（**仅主键值**，「Storing properties of edited objects other than the primary key is not supported.」）。
- **溯源字段**（`action-types/action-log.md:72-77`）：调用的 webhook 标识（同步/异步）、通知标识与收件人、scenario RID、是否被 revert 及 revert 的 user/timestamp。
- **可选字段**：参数值；对象引用参数的属性值（但 `allow multiple values` 的对象引用参数不支持）（`action-types/action-log.md:83-84`）。可额外记录**未被编辑**的对象属性以支撑分析（`:88-90`）。
- **时间戳语义**：action log 用 commit timestamp，而规则里 `Current User/Time` 是执行开始时间且「stays fixed throughout execution」，因此可能早于日志时间（`action-types/action-log.md:98-102`，例子 12:00:00 vs 12:00:05）。
- **约束**：属性类型建后不可改（改则 action 校验失败）；summary ≤20 段、每段字面量 ≤50 字符；summary 不能引用对象类型参数；媒体引用参数被排除；**不回填**（只从配置保存那刻开始记）；日志对象同样受编辑限额约束（`action-types/action-log.md:112-118`）。
- **函数式 action 需要 Edits 溯源**：「the backing Ontology edit function must have `Edits` provenance configured.」（`action-types/action-log.md:122`；报错文案 `Function has no Edits Decorator` 见 `:144`）。
- **Revert 语义**：仅 OSv2——「Action reverts are only available for Object Storage v2」（`action-types/action-reverts.md:14`）；只能由**执行者本人**回滚（`:18`）；新 action 默认可回滚（`:12`），2024-05 后新建且只改 OSv2 的 action 默认开启（`:22`）。
- **Revert 限制**：对象上只要发生任何后续编辑（哪怕是别的属性）就不能回滚——「an action on an object can only be reverted if the action is the most recent edit to an object.」（`:42`）；提交后关掉开关再打开也不可回滚（`:43`）。
- **Revert 不撤销副作用**：「An action revert only reverts the edits to the object instance, but it will not revert side effects, such as notifications or webhooks」（`:47`）。删对象后 toast 消失则只剩「迁移对象类型复制数据」或「drop all edits」两条路（`:51-54`）。
- **一致性保证**：ACID 四性（`action-types/consistency-guarantees.md:16-21`），其中 Atomicity 明确「does not extend to other side effects ... or to side effects of a backing function, such as external calls it makes.」（`:18`）。
- **写模式**：batched（默认，自身读看不到自己的编辑）vs staged（自身读能看到自己的编辑，仅 TS v2 / Python / AIP Logic）（`:48-49`）。
- **冲突检测粒度是对象级**：「the Ontology detects write-write conflicts at the object level. ... This applies even if the actions write to different properties of that object.」（`:55`）；底层 dataset rebuild 也会造成（通常可重试）的瞬时冲突（`:57`）；冲突时「the failing action's entire edit is discarded」（`:59`）。
- **隔离级别**：Legacy / Snapshot isolation（默认）/ Read committed（未来）（`:73-75`）；新 batched action 默认 snapshot（`:67`）。

| 级别 | 读 | 冲突检测 |
|---|---|---|
| Legacy | 不保证单一时间点（`action-types/consistency-guarantees.md:73`） | 只对**写**的对象做冲突失败（`action-types/consistency-guarantees.md:73`） |
| Snapshot（默认） | 开始时取一个快照（`action-types/consistency-guarantees.md:74`） | 同上，但不检测读写冲突（`action-types/consistency-guarantees.md:107`） |
| Read committed（未上线） | 每次读最新已提交值（`action-types/consistency-guarantees.md:75`） | 允许并发写，后提交者覆盖同属性（`action-types/consistency-guarantees.md:75`） |

- **Snapshot 的盲区**：不做读写冲突检测，会写偏斜（write skew）——两个 action 各读同一集合、各写不同对象、都能提交（`:107-123`），官方建议「express the invariant as a write to a single shared object」（`:123`）。
- **重试**：瞬时错误最多重试 5 次、每次从头执行并用新数据重算（`:170`）；staged writes 目前只支持 legacy 隔离（`:156`）。

## 7. 权限与授权（谁能定义/执行、read-write authorizations、branching）

- **三类问题**：谁能看、谁能改 action type、谁能用某组参数执行（`action-types/permissions.md:10-14`）。
- **执行权限取决于被编辑的对象/关系类型**：「the user submitting the action must be able to view the edited object types and link types and their datasources, and pass the submission criteria.」（`action-types/permissions.md:20`）。
- **只允许 action 编辑时的最小权限**：「the user submitting the action will only need `Read` access on the objects that are being edited. This means that it is possible for users to create objects that they cannot view.」（`action-types/permissions.md:44`）；放开其它写路径则要求 writeback dataset 的 `Edit`（`:46`），官方明确不推荐（`:48`）。
- **配置端不校验**：「an action type's configuration does not display permission settings on affected underlying object types; the person configuring the action type must ensure that these permissions are correct.」（`action-types/permissions.md:50`）。
- **只读时过滤 ≠ 写入保护**：「Row and column access controls ... filter what a user can read when invoking an action. These controls do not extend to the action's write.」（`action-types/permissions.md:26`）。
- **Action log 权限**：提交者需要对日志对象类型有权限，否则提交即失败（`action-types/permissions.md:38`）。
- **副作用权限**：「Any user who can set up an action may configure side effects.」（`action-types/permissions.md:58`）；webhook 需先在 Data Connection 配好插件（`:60`）；收件人必须对通知内容涉及的数据有权限（`:66-70`），且执行者需能看到收件用户/组（`:74`）。
- **Read/Write authorizations（Beta）**：read authorization 给「能读什么/能接收什么输入」加上界，write authorization 给「写出的数据的最低安全等级」加下界，二者「do not grant access or replace existing permissions or submission criteria」（`action-types/read-write-authorizations.md:12,14,18,24`）。
- **脱敏例外**：write authorization 不管平台自管的写入——action log 与 edit history 可能低于该下界（`action-types/read-write-authorizations.md:28`）。
- **降密检查时机**：读写边界不一致时，在**保存/发布**时校验「是否有降密权限」，运行时不重复校验（`:36`）；未配 read authorization 则不检查、可能造成 data spill（`:38`）。
- **表单先给反馈、提交时权威校验**：`Minimum required security` 五态（Security passed / not passed / Can't be determined / Not set / Redacted），「Foundry performs the authoritative validation when the action is submitted」（`:100-110`）。
- **Branching**：所有被编辑对象类型必须在分支上建好索引，否则「the action does not apply its edits」（`action-types/branching-action-types.md:20-24`）；分支上的编辑**不会**合回 main（`:26,77`）；branch-aware 函数才读分支 schema，但无论是否 branch-aware 都只在分支执行（`:30`）。

## 8. 官方示例/最小闭环（按文档整理一个可实现的 action 定义结构；字段级别）

以 getting-started 的「改工单优先级」为例，官方最小闭环 = 对象类型（`Ticket ID/Title/Priority/Status`）+ action type（1 条 `Modify object(s)` 规则 + 2 个参数 + 1 条提交条件 + 表单默认值绑定当前对象）（`action-types/getting-started.md:16-21,40,46-48,52-54,60`）。

```jsonc
// 按文档整理的结构（非官方 schema，字段语义逐条有据）
{
  "actionType": {
    "name": "Change Ticket Priority",          // action-types/getting-started.md:58
    "objectType": "Demo Ticket",               // action-types/getting-started.md:40
    "rules": [                                 // action-types/rules.md:16-29
      { "kind": "modify_object",               // action-types/rules.md:19
        "target": { "param": "ticket" },       // 由对象引用参数定位（action-types/rules.md:19）
        "properties": {
          "Priority": { "fromParam": "priority" } // 每个属性→参数，action-types/rules.md:37
        }
      }
    ],
    "parameters": [                            // action-types/parameter-overview.md:10
      { "id": "ticket", "type": "object_reference", "objectType": "Demo Ticket",
        "required": true, "visibility": "hidden",     // action-types/getting-started.md:60
        "default": { "kind": "environment_variable", "value": "current_object" } },
      { "id": "priority", "type": "string", "required": true,
        "constraints": { "mode": "multiple_choice", "options": ["P0","P1","P2"] } } // action-types/getting-started.md:48
    ],
    "submissionCriteria": [                    // action-types/submission-criteria.md:10,22
      { "template": "parameter", "subject": "ticket.Status",
        "operator": "is", "value": "Open",      // action-types/getting-started.md:52
        "failureMessage": "Only open tickets can be reprioritized" } // action-types/getting-started.md:54 + action-types/submission-criteria.md:103
    ],
    "capabilities": {
      "actionLog": { "enabled": true },         // action-types/action-log.md:41
      "isolationLevel": "snapshot_isolation",   // action-types/consistency-guarantees.md:67
      "retryMode": "only_without_external_calls", // action-types/consistency-guarantees.md:176
      "allowRevert": true                       // action-types/action-reverts.md:20
    }
  }
}
```

- **属性值映射只有 4 种合法来源**：From parameter / Object parameter property / Static value / Current User or Time（`action-types/rules.md:37-40`，其中 `:40` 强调 Current User/Time 固定于执行开始、且不能用于 action 其它部分）。
- **struct 属性映射必须"完整"**：每个 struct 字段都要映射，且 base type 必须一致；struct 属性只能由一个 struct 参数写入（`action-types/actions-on-structs.md:22,45`）。
- **interface 版的额外约束**：create 时必须由用户选具体对象类型，且该类型必须能拿到主键（`action-types/actions-on-interfaces.md:46-48`）；modify 不能碰主键（`:54`）；action log **尚不支持**、interface 上不能用函数（`:117-118`）。
- **函数版最小签名**：TypeScript v1 `@Edits(...) @OntologyEditFunction() ... : void`（`functions/api-ontology-edits.md:20-24`）；Python `@function(edits=[...]) ... -> list[OntologyEdit]`（`functions/python-ontology-edits.md:20-24`）；TS v2 `createEditBatch<OntologyEdit>(client)` + `batch.getEdits()`（`functions/typescript-v2-ontology-edits.md:54,261`）。
- **新建对象的主键生成**：TS v1 用 `Uuid.random()`（`functions/edits-generate-id.md:64`）；TS v2 无内置 UUID，用 npm `uuid`（`:14`）；Python 用 `str(uuid4())`（`functions/python-staged-writes.md:159-174`）。
- **staged writes 的最小闭环**：`WriteableClient` 注入 + `client.ontology.staged_writes()`，不返回 edits，函数成功即提交、抛错即整体丢弃（`functions/python-staged-writes.md:43,50-52,346-348`）。

## 9. 与本项目对照

本项目现状（`server/apps/chat_data_app/services/ontology/README.md:11`）：「**Action Type**：参数 + `set_property` 编辑契约，experimental/certified 可执行（写属性覆盖层 + 审计，不改源库）」；细节见 `services/ontology/README.md:100-111`：参数 `{name, type, required, description}`、编辑仅 `set_property`、标识属性禁改、`POST .../test-run` 只校验参数且 `executed=false`、`execute` 写 `chat_data_ontology_object_edit` + `chat_data_ontology_action_execution`、agent 侧只有 `list_action_types`/`execute_action`，并自认「回滚、撤销、副作用/Webhook、Action UI 尚未实现」。审计口径为 **L1**（`docs/ontology-capability-audit.md:22`：「无 create/link/edit、审批、副作用、回滚」），P0 要求补「Action L2：多属性编辑、状态机、执行前预览、审批占位」（`docs/ontology-capability-audit.md:94-95`）。

差距与官方明确要求（逐条对位）：

1. **编辑类型只有 1/12**：官方规则含 create/modify/create-or-modify/delete/create-link/delete-link/function/5 种 interface 版（`action-types/rules.md:18-29`）。我们只有 set_property，连 create/delete/link 都没有；官方还明确「主键必填」（`action-types/rules.md:18`）、「删除/修改不能引用本次新建对象」（`action-types/rules.md:19,21`）、「FK 关系必须用 modify 改外键」（`action-types/rules.md:22-23`）——这三条是 L2 必须写进契约的硬约束。
2. **无提交条件模型**：官方要求条件+算子+失败文案（`action-types/submission-criteria.md:16-22,101-103`），并且这是权限代管机制（`action-types/permissions.md:20`、`action-types/upload-media.md:31`、`action-types/trigger-schedule-build.md:24`）。我们 test-run「只校验参数并解析编辑」（`services/ontology/README.md:106`），等于把业务前置条件留给调用方，与官方「运维化权限」思路不一致。
3. **参数能力缺口**：缺默认值（`action-types/parameters-default-value.md:10`）、override（`action-types/parameters-override.md:30`）、下拉过滤（`action-types/parameters-filter.md:10`）、type class 预填（`action-types/parameters-default-value.md:36-38`）、对象引用列表（批量入口，`action-types/use-actions.md:12`）。仅 `{name,type,required,description}` 无法支撑官方表单预填/多端复用。
4. **原子性与冲突策略未定义**：官方单事务全有或全无（`action-types/consistency-guarantees.md:18,46`），对象级冲突检测（`:55`），snapshot 默认（`:67`）。好消息是写路径已有统一事务编排——「写方法统一用 `transaction_scope` 编排事务（DAO 经 ContextVar 复用同一会话），删除/状态迁移等多步写整体回滚，不产生半成功数据。」（`services/ontology/README.md:86`），L2 的单事务语义可直接复用它；但覆盖层写在源行消失/Pipeline 重建时的行为「未定义」（`services/ontology/README.md:202` 自认），这是开放 create/delete 前必须先定的事。
5. **Action log 语义不足**：官方是「一个 action type 对应一个日志对象类型、一次提交一条、自动链到所有被编辑对象」（`action-types/action-log.md:51`），必记 Action RID / type RID / version / commit timestamp / user / **被编辑对象主键**（`:63-68`）。我们只有 `chat_data_ontology_action_execution` 审计表（`services/ontology/README.md:108`），缺「一次提交可链多对象」「版本自增」「被编辑对象主键集合」三件事；且日志权限是提交前置（`action-types/permissions.md:38`）。
6. **无回滚**：官方 revert 有硬前提——仅 OSv2、仅执行者本人、且必须是该对象**最近一次**编辑（`action-types/action-reverts.md:14,18,42`），且不撤销副作用（`:47`）。我们「回滚、撤销」未实现（`services/ontology/README.md:111`），且覆盖层模型下"最近一次编辑"需要额外版本链才能判定。
7. **无副作用与函数 action**：官方两侧副作用（notification / webhook，时机相反）（`action-types/webhooks.md:22-25`）、schedule build 异步触发（`action-types/trigger-schedule-build.md:12`）、function rule 互斥且需 `Edits` 溯源（`action-types/rules.md:24`、`action-types/action-log.md:122`）。我们全部缺失，且函数能力本身是占位（`docs/ontology-capability-audit.md:26`）。
8. **批量边界没口子**：官方 50 对象类型 / 10,000 对象 / 单批调用 10,000（函数未批处理则 20）（`action-types/scale-property-limits.md:26-28,32`）。我们对象查询 limit 上限 100（`services/ontology/README.md:200`），action 执行尚无批量上限与批量事务定义，L2 需要显式设上限并写进契约。
9. **权限只有数据源组/member 级**：官方三层权限 + 提交条件 + read/write authorizations（`action-types/permissions.md:10-14`、`action-types/read-write-authorizations.md:18,24`）。我们「只有数据源组/member 授权」（`docs/ontology-capability-audit.md:28,61`），Action 的执行授权只能粗粒度门控开关。
10. **inline edit / struct / interface 均无**：官方 inline edit 要求单对象单类型 + 默认值来自对象参数 + 禁副作用（`action-types/inline-edits.md:26-32`）；struct 与 interface 各有硬限制（`action-types/actions-on-structs.md:44-47`、`action-types/actions-on-interfaces.md:116-118`）。我们概念级构件未实现（`services/ontology/README.md:196`）。

结论：我们的 L1 与官方最小闭环的**形状一致、内容偏窄**——都是「对象类型 + 参数 + 编辑 + 执行记录」；缺口集中在「编辑类型（3 类核心规则）」「提交条件」「参数默认/覆盖」「提交即单事务 + 冲突策略」「action log 的对象化建模」五处。

## 10. 待确认问题

1. **冲突解决策略选哪种**：官方给「Apply user edits (default)」与「Apply most recent value」两策略（`action-types/getting-started.md:78-81`）。我们的覆盖层是"永远用户值胜"，源库重建后是否需要 TTL/失效规则？文档未明确非 OSv2/覆盖层场景的等价方案。
2. **revert 在无对象存储时怎么定义**：官方 revert 仅 OSv2（`action-types/action-reverts.md:14`），我们无 OSv2。是否用「编辑版本链 + 最近一次判定」自建？官方未给替代路径。
3. **action log 是否复用现有审计表**：官方要求「one action type ↔ one log object type」、「一次提交一条、链到全部被编辑对象」（`action-types/action-log.md:51`）、版本自增（`:65`）。若复用 `chat_data_ontology_action_execution`，需要补 version、edited_object_keys、以及"一 action type 一对象语义"的映射，是否值得新建表？
4. **批量上限取值**：官方 50 类型 / 10,000 对象 / 3MB 单编辑（`action-types/scale-property-limits.md:26-28`）。MVP 是否需要一次性做到 10,000，还是先按查询 limit 100 对齐并显式声明降级？
5. **提交条件的最小内核**：官方条件模板依赖 multipass（user ID / group / org 属性，`action-types/submission-criteria.md:28-32`），我们没有该身份体系。L2 先做「参数条件 + 静态值 + 比较算子 + 失败文案」是否足够？用户/组维度要不要接现有机构授权？
6. **多属性编辑的范围**：官方一条 modify 规则可映射任意多属性（`action-types/rules.md:35`），但同对象多规则会按顺序覆盖（`action-types/rules.md:52`）。我们 L2 是「一次 action 改一个对象的多个属性」还是「也允许改多个对象」？后者立刻牵出冲突检测与事务边界（`action-types/consistency-guarantees.md:55`）。
7. **执行前预览（test run）要做到哪一层**：官方 test run 会真实跑函数与外部调用（`action-types/test-run.md:69`），并输出 Proposed changes + Execution log（`:38,44`）。我们 `executed=false`（`services/ontology/README.md:106`）——是否要补 "proposed edits diff"（只算不写）？
8. **审批占位与幂等**：审计 P0 提「审批占位」（`docs/ontology-capability-audit.md:95`），但官方 action type 文档没有审批原语（副作用只有 notification/webhook/schedule），审批要不要自建（状态机 + 待提交队列）？另外官方重试语义会重放编辑（`action-types/consistency-guarantees.md:170`），我们的覆盖层写是否天然幂等，需要验证。
9. **函数 action 是否进 L2**：官方 function rule 与其它规则互斥（`action-types/rules.md:24`），且日志需 `Edits` 溯源（`action-types/action-log.md:122`）。我们函数能力仍是占位（`docs/ontology-capability-audit.md:26`），L2 是否明确排除？
10. **副作用优先级**：notification（低风险、易演示）vs webhook（需外部系统与凭据）。官方都是「rule」，但 webhook 需 Data Connection 前置（`action-types/permissions.md:60`）。我们第一版做哪个？文档未给取舍依据。
