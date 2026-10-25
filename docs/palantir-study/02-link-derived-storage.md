# Palantir 关系类型 / 派生属性 / 编辑落库 / 存储索引研读

## 0. 依据

**官方文档**（本地副本根目录 `docs/palantir-ontology/`，行号均指该副本；抓取日 2026-10-17）：

| 主题 | 文件 |
|---|---|
| 关系类型 | `object-link-types/link-types-overview.md`；`object-link-types/create-link-type.md`；`object-link-types/edit-link-types.md`；`object-link-types/link-type-metadata.md`；`interfaces/interface-link-types-overview.md` |
| 派生属性 | `object-link-types/derived-properties.md`；`ontology/derived-properties.md`；`ontology/derived-properties-reference.md` |
| 编辑落库 | `object-edits/overview.md`；`object-edits/how-edits-applied.md`；`object-edits/materializations.md`；`object-edits/permission-checks.md`；`object-edits/user-edit-history.md`；`object-edits/schema-migrations.md`；`ontology/merge-scenario.md` |
| 存储索引 | `object-backend/overview.md`；`object-backend/osv1-osv2-migration.md`；`object-backend/object-storage-v2-breaking-changes.md`；`object-backend/aggregation-considerations.md`；`object-indexing/overview.md`；`object-indexing/direct-datasources.md`；`object-indexing/data-restrictions.md`；`object-indexing/funnel-batch-pipelines.md`；`object-indexing/faq.md`；`object-databases/object-storage-v1.md` |

**为补全证据额外阅读**（同在允许目录内）：`object-permissioning/multi-datasource-objects.md`（MDO）、`object-link-types/create-object-type.md`（主键唯一性/确定性）、`object-link-types/allow-editing.md`、`object-link-types/edit-only-properties.md`、`object-link-types/property-reducers.md`、`ontologies/oss-limitations.md`（Search Around）。

**项目侧简写**：`ontology/README.md` = `server/apps/chat_data_app/services/ontology/README.md`；`graph-design.md` = `docs/ontology-graph-layer-design.md`；`capability-audit.md` = `docs/ontology-capability-audit.md`。

**编码说明**：本地副本部分字符被抓取为 cp1252 乱码（`â`、`â`），引文按语义还原为 `→`、`↔`，未改动英文措辞。

## 1. 关系类型

### 1.1 定义与边界

- "A **link type** is the schema definition of a relationship between two object types. A **link** refers to a single instance of that relationship between two objects in the same Ontology."（`object-link-types/link-types-overview.md:10`）——link type 是模式，link 是实例，实例范围限定在**同一个 Ontology 内**。
- 自关联合法："Links can also exist between two objects of the same type."（同文件 `:16`）；跨本体不支持："Links between object types across different Ontologies are not supported."（`:18`）。
- 与数仓的类比："The definition of a link type ... is analogous to that of a join between two datasets, while the definition of a link is analogous to that of a row joined with the fields of the same row in another dataset."（`:28`）——link 没有独立身份，等价于"一行 join 上的配对"。

### 1.2 基数

- 四种基数："_One-to-one cardinality:_ ... The one-to-one cardinality serves as an indicator of the intended relationship, but the one-to-one cardinality is not enforced."（`object-link-types/create-link-type.md:62`）；one-to-many `:63`、many-to-one `:64`、many-to-many `:65`。
- **一对一是"意图声明"而非约束**（`:62`）：官方不在索引/写入层强制唯一性，只通过"key 必须映射对方主键"间接保证"one 侧唯一"（`object-link-types/edit-link-types.md:88`）。
- 基数决定实现形态（`object-link-types/create-link-type.md:54-56`）：FK → one-to-one/many-to-one；join table → many-to-many；backing object → many-to-one 的扩展。
- 元数据里的 cardinality 是**按侧**表达："Indicates to applications if each object type in the link type has one or many objects."（`object-link-types/link-type-metadata.md:16`）——不是单一枚举，而是两侧各一个 `one|many`。

### 1.3 方向与双向可见性

- "A link type is bidirectional: it always has two **sides**, one for each of the two object types it relates. Each side ... has its own display name and API name"（`object-link-types/link-types-overview.md:22`）。
- 不生成反向 link type："Creating a single link type between two object types does not implicitly create a second, reverse link type. Instead, that single link type already supports traversal in both directions through its two sides"（`:24`）。
- 同一对对象类型可有多条关系，各自独立："each one represents a separate real-world relationship rather than a reverse direction of an existing one ... Each link type requires unique API names on its sides"（`:26`）。
- API name 归属**侧**："The API name on a side of a link type can be used to return objects of that type."（`object-link-types/create-link-type.md:143`）；命名规则 `:144-149`（小写开头、同对象类型内唯一、1-100 字符、NFKC、非保留字）。
- 可见性："A `prominent` side ... will lead applications to show this side of the link type first ... A `hidden` side of a link type will not appear in user applications."（`object-link-types/link-type-metadata.md:23`）。

### 1.4 外键/主键语义与三种存储形态

- FK 定义："A **foreign key** is a property on one object type that stores the value of another object type's primary key ... The **foreign key** property of one object type must refer to the **primary key** property of the other object type."（`object-link-types/create-link-type.md:80`）——存的是**值**，不是对象引用。
- 匹配即建链："Links will be created between `Aircraft` and `Flight` object types when the `Tail Number` of the `Aircraft` matches a `Flight Tail Number` of a `Flight`."（`:82`）。
- 主键侧自动选定："The primary key of the object type is auto-selected since there is only one primary key for each object type"（`:95`）——"one primary key"指主键**定义**唯一，本页未说明是否允许复合主键列（§6）。
- join table 形态："select a datasource that includes all combinations of links between the primary key of the first object type ... and the second object type"（`:107`）；列与主键映射 `:116`；"A column can only be mapped to one primary key."（`:114`）。
- m2m 是唯一可写回形态："A many-to-many cardinality, which requires a backing datasource, is required to enable users to edit or write back to the link type."（`:109`）。
- backing object 形态："The object in the middle serves as the intermediary and provides additional metadata about the connection between the two entities, and backs the link."（`:129`）；价值是"allow for the inclusion of additional metadata on the link and support restricted views"（`:169`），前置为两条 many-to-one link（`:190-192`）。
- 一个 datasource 只能支撑一个 link type："the datasource backing the link type you are trying to save is already backing a different link type in the Ontology and cannot be used again."（`:220`）。

### 1.5 link type metadata

- 全量字段（`object-link-types/link-type-metadata.md:12-24`）：ID、RID、Status（`active|experimental|deprecated`，默认 experimental）、Object types、Cardinality（按侧）、Key、Display name（按侧，"link _to_ that object type"）、Plural display name、API name（按侧）、Visibility、Type classes。
- Key 两套语义（`:18-19`）：FK 型 "a property of one object type (the foreign key) refers to the primary key property of the other object type"；m2m 型 "a table containing pairs of primary keys defines the links ... require a join table to be specified, along with mapping these keys"。
- 编辑后果（`object-link-types/edit-link-types.md:18-23`）：改 m2m backing datasource / 改基数 / 改外键 / 删除 link type 都会 unregister+reregister，重索引期间"make the links of that type **unavailable** in user applications"（`:16`）。
- 换 datasource 只在 schema 相同时自动重映射："Keys will be automatically remapped for you **only if** you change to a new datasource with the **same schema**"（`:80`）。
- 状态是编辑闸门："Link types with an `active` status cannot be deleted."（`:65`）、"You cannot change the API name for link types with an `active` status."（`:90`）。

### 1.6 是否存在 derived link

- **官方没有 derived link 概念**：创建入口穷举三种形态（FK / join table / backing object，`object-link-types/create-link-type.md:54-56`），编辑器只允许改 backing datasource、key、metadata（`object-link-types/edit-link-types.md:69-95`），无"表达式/派生关系"入口。
- 反向证据："Use as a link type foreign key | No"（`ontology/derived-properties-reference.md:122`）——派生值不能当外键，因此也无法成为关系端点。
- 结论：要表达"算出来的关系"，官方路径是物化成对象/join table。文档未直接写"derived link 不支持"，但由入口穷举 + FK 禁令可判定其不存在。

### 1.7 interface 上的 link

- 机制是**约束 + 具体实现**："An interface link type constraint defines an object-to-object relationship common across all object types implementing an interface ... concrete link types on the object type are used to fulfill interface link type constraints."（`interfaces/interface-link-types-overview.md:10`）。
- 约束参数（`:16-21`）：link target type（interface 或 object type）、具体 Target、Cardinality（**只允许 ONE / MANY**）、是否 required。
- 基数语义（`:39`）："A `ONE` cardinality indicates that each object implementing the interface should link to one object of the target type. A `MANY` cardinality indicates ... any number of objects"。
- 因此接口层只抽象 one-to-one / one-to-many，**没有 many-to-many 接口约束**，也不引入新的多跳语义。

### 1.8 一个关系两端如何定位对象 / 多跳与路径

- 定位模型：两端各由 **(对象类型, 主键值)** 定位；FK 型把对方 PK 值存成本侧属性（`object-link-types/create-link-type.md:80`），m2m 型用 join table 两列 PK 配对（`:114-116`），backing object 型用中间对象 PK 加两条 m2o link（`:190-192`）。
- 只有 object-backed link 有"关系自身的身份与属性"（中间对象有 PK 与业务属性，`:171`、`:214`）；FK/m2m 型的 link 无独立 ID，与"link = joined row"一致（`object-link-types/link-types-overview.md:28`）。
- **多跳**：官方显式定义只有派生属性链——"Derived properties support traversing up to **three levels** of linked objects"（`object-link-types/derived-properties.md:85`），示例 `Department → Employee → Project`（`:87`）。
- **路径**：查询侧多跳操作叫 Search Around——"When you perform a Search Around operation to traverse a link relationship between objects, OSS uses a specialized type of join operation"（`ontologies/oss-limitations.md:97`），实现为 left-semi join（`:106`），限制见 `:82` 与 `object-backend/overview.md:115`。
- 但**没有"link chain / 路径类型"的建模概念**：link type 始终只连两个对象类型（`object-link-types/link-types-overview.md:10`），多跳是查询期组合；任意长度路径查询 API 在上述页面中**文档未明确**。

## 2. 派生属性

### 2.1 定义

- "Derived properties are properties whose values are calculated at query time from other properties or from linked objects. Instead of storing a value, a derived property describes how to compute one"（`ontology/derived-properties.md:10`）。
- 三种算法（`:14-18`）：跨链接聚合、从单个链接对象取值、算术表达式。
- 只读且不落存储："Derived properties are **read-only**. They cannot be edited by actions or functions, and they are not stored in object storage."（`:22`）。
- 两种形态（`:28-35` 表）：ontology-defined（Ontology Manager 定义、全员可见、最多 3 跳、无表达式、Beta）vs runtime-defined（object set/请求内定义、可带表达式、Workshop/Insight 限 1 跳、OSDK 无固定上限、GA）。

### 2.2 支持的来源与算子

- 算子清单（`object-link-types/derived-properties.md:48-56`）：Count / Average / Sum / Minimum / Maximum / Approximate cardinality / Exact cardinality / Collect list / Collect set；`ontology/derived-properties-reference.md:20-31` 给出 SDK 方法名与输入/输出类型。
- 类型契约：Count→`Long`、Average→`Double`、Sum **加宽**（short/int→long、float→double、decimal 提精度）、cardinality→`Long`、Collect→`Array<源类型>`（`ontology/derived-properties-reference.md:22-30`、`:39-47`）；`approximatePercentile` 仅 runtime-defined（`:31`、`:139`）。
- 何时必须聚合："If any link in your chain can reach more than one object, you must select an **Aggregation**"（`object-link-types/derived-properties.md:46`）；判定表 `ontology/derived-properties-reference.md:99-106`：任一跳可达多对象、链含 m2m、FK 为数组时必须聚合，m2m "selecting is never permitted"（`:105`）；Count 无需选属性（`object-link-types/derived-properties.md:69`）。
- collect 细节：默认 limit 10、上限 100（`:75`）；Collect set 先去重再截断（`:77`）；无 reducer 时**顺序不保证**、跳过 null、不产生 null 元素（`ontology/derived-properties-reference.md:53-55`）；数组输入先展平（`:35`），vector 一律不可聚合/不可输出（`:66`）。

### 2.3 可用场景

- 结果/过滤/排序/聚合/分组全部支持（`ontology/derived-properties-reference.md:114-118`），但"Derived properties must be selected explicitly"（`:114`）。
- 排序护栏："In Workshop, sorting an object set that uses derived properties limits the set to 200 rows."（`:116`）。
- Automate 只有 scheduled monitoring 支持派生过滤，live 不支持（`ontology/derived-properties.md:98`）；OSDK 最低 `2.2.0-beta.x`，取值/表达式需 `2.4.0+`（`:60`、`ontology/derived-properties-reference.md:95`）。

### 2.4 限制

- 通用限制（`ontology/derived-properties.md:92-98`）：含 OSv1 索引对象类型的查询不能带派生属性；**不能做主键**；不能 mark required；不能直接套 value type；不能配条件格式/value formatting；不能配 inline action。
- ontology-defined 额外限制：最多 3 跳（`:104`）、不支持算术与日期表达式（`:105`，只能用 runtime-defined）。
- 不能引用另一个派生属性："A derived property cannot take its value from another derived property."（`object-link-types/derived-properties.md:100`）；声明顺序规则见 `ontology/derived-properties-reference.md:72`。
- 不能当外键（`ontology/derived-properties-reference.md:122`）、不能用 action/function 编辑（`:120`）。
- **collect 的 limit 不限制求值**："Filters, sorts, and aggregations over a collected property run against the **complete** set of values, not the truncated list."（`ontology/derived-properties.md:82`）；"命中但不在返回列表里"是合法的（`:84`）；reducer 等价于 limit=1 但仍按全量过滤（`:84`、`ontology/derived-properties-reference.md:62`）。
- 安全：逐跳鉴权、**不同用户看同一派生属性可得到不同值**（`ontology/derived-properties.md:64-66`）；collect 结果保留每个值的 markings（`:68`）。
- 性能：查询期计算且**不建索引**（`ontology/derived-properties-reference.md:126`）；平台可把过滤下推到对象索引（`ontology/derived-properties.md:72`）；load 便宜、search 贵，唯 Collect list/set 例外（`ontology/derived-properties-reference.md:128-140`）；建议优先近似基数（`:144`）、先过滤再聚合（`:146`）、超大规模改预计算（`:147`）。

### 2.5 与关系类型的关系

- 派生属性完全寄生在 link type 上："select a link type from the dropdown menu. This determines which objects the property derives values from."（`object-link-types/derived-properties.md:36`）。
- 关系方向决定可用形态：聚合要求起点能到达多行，select 适用于一对一链路（`ontology/derived-properties-reference.md:99-106`）。
- 派生属性**不可反向成为关系端点**（`:122`），"派生 → 关系"的闭环在官方被切断。
- link type 变更后派生属性是否被阻断/重算，官方只说会 reindex 与暂不可用（`object-link-types/edit-link-types.md:16-23`），**未明确** → §6。

## 3. 对象编辑写入模型

### 3.1 edits 如何应用

- 唯一入口是 Action："users can edit property values, add and remove links, and create and delete objects by applying Actions"（`object-edits/overview.md:10`）；"An Action in Foundry is a single transaction that changes the properties of one or more objects"（`:12`）。
- 写路径："the data-modification logic is immediately applied to the index in the object databases and periodically flushed into a persistent store in the form of Foundry datasets owned and managed by Funnel."（`object-edits/how-edits-applied.md:16`）。
- 队列与读己之写："This instruction is stored in a Funnel-managed queue that has offset tracking ... if an object read ... happens after a user modification is sent, the object read is guaranteed to contain the user edits."（`:20`）。
- 无单条撤销："There is no mechanism to directly undo a single user edit or deletion other than to make additional user edits"（`:24`），只能整体丢弃（`:28`）。
- scenario 内编辑"exist only within that scenario's isolated sandbox ... Applying the scenario commits all those staged edits to the Ontology as a single transaction via the merge action."（`ontology/merge-scenario.md:14`）。

### 3.2 materialization / edit history / schema migration

- materialization 定义："materializations of indexed data from the Ontology that contains the latest state of each object by combining data from both input datasources and user edits."（`object-edits/materializations.md:10`）；用途是下游管道与下载（`:16-17`）；自动更新、不可手工 build（`:25`）。
- OSv1 必须有 writeback dataset 才能编辑；OSv2 不必，且允许多个 materialized dataset（`:29-31`）。调度：automatic（分钟级传播）vs periodic（新数据或每 6 小时）（`:41-42`）。OSv2 retention 不可定制、只保证最新快照（`:52`）；schema 取自本体属性 API name（`:62`）；`__is_deleted`/`__patch_offset` 为内部列不可用（`:66`）。
- edit history 只在 OSv2："Tracking the history of user edits to objects indexed into Object Storage v2."（`object-edits/user-edit-history.md:10`）；依赖 Edits 开关（`:12`）；记录每次编辑，决策上下文靠 action log（`:14`）；时间戳为 UTC 提交时间（`:18`）；关掉即永久删除历史（`:35`）；能看当前对象即可看全部历史（`:27`）。
- schema migration 是 OSv2 专属："such user edits cannot be migrated in OSv1 ... Object Storage v2 ... provides a schema migration framework with a list of predefined migrations"（`object-edits/schema-migrations.md:14-16`）。
- 破坏性变更（`:30-38`）：换 input datasource、换主键、改属性类型、改已编辑属性的 ID、删已编辑属性/struct field、改 struct field 类型；非破坏：display name/title key/render hints/type classes/visibility（`:44-48`）。
- 迁移指令（`:56-118`）：Drop all property edits / Drop all struct field edits / Drop all edits / Move edits / Move struct field edits / Cast property to new type / Cast struct field / Revert migration；单次上限 500 条、**不支持主键属性**（`:122-124`）；失败可 Fix schema migrations（`:126`）。
- 生效链路：新 schema version + 替换 Funnel pipeline，"as soon as the replacement pipeline is completed and the new version is declared to be fully hydrated"（`:26`）。

### 3.3 权限检查

- 单 datasource：能 view/load 对象 + 通过 submission criteria（`object-edits/permission-checks.md:14-17`），新建对象还需能 view input datasource（`:21`）；MDO 按操作分级：create 只校验被写 datasource、其余默认 null（`:39-43`），modify 只校验被改属性所在 datasource（`:47-51`），**delete 必须能看全对象**（`:57`），create/delete link 只要求两侧都能 load（`:63`、`:69`）。

### 3.4 冲突与一致性

- 两条策略："Strategy 1: Apply user edits (default)" / "Strategy 2: Apply most recent value"（`object-edits/how-edits-applied.md:90-91`）。
- 删除不是编辑："Deletions are not considered an edit. Once a deletion is applied, the object is no longer visible regardless of datasource state. If the object is later recreated, it will not inherit the previous edits."（`:86`）。
- 默认策略行为表（`:103-119`）关键行：源行消失 → 对象消失（T1 `:106`）；源行重现 → 旧编辑仍生效（T5 `:110`）；**未被编辑的属性接受源更新**（T6 `:111`）；Create 后其它列置 null 且不再被源更新覆盖（T9/T10 `:114-115`）；源行消失但对象被 Create 过则对象保留（T12 `:117`）；对已删除对象 Modify 失败（T14 `:119`）。
- 时间戳策略：按属性所在 datasource 判定、仅 OSv2、要求 datasource 含 UTC timestamp 属性（`date` 不行）（`:127-129`）；既有编辑以策略切换时刻为时间戳（`:135`）；edit-only 属性无视时间戳始终生效（`:171`）；只与 input datasource 时间戳比较（`:184-187`）。
- 版本一致性：前端与 Actions server 加载版本可能不同（`:40`）；OSv1 用缓存版本 + `StaleObject`，且**不检查属性级变更**故无关属性也会冲突（`:50-52`）；OSv2 只校验"直接参与生成编辑"的对象版本，"reduces the frequency of `StaleObject` conflicts, with a consequence of weaker guarantees"（`:56-60`）。
- 持久化节拍：merged dataset 在有新数据事务时构建，否则"every 6 hours, if edits had been detected"（`:75-78`）；live pipeline 同理（`object-indexing/funnel-batch-pipelines.md:51`），"User edits are applied to indexes ... immediately"（`:53`）。

### 3.5 OSv1 vs OSv2 差异要点

- "OSv2 only supports user edits via Actions. All existing direct queries on OSv1 edit APIs must be refactored"（`object-backend/object-storage-v2-breaking-changes.md:25`）；writeback 更名 materializations 且由必需变可选（`:26`）。
- 历史与迁移：迁移时可选保留 edit history（`:32-35`、`object-backend/osv1-osv2-migration.md:24-26`）；迁移期间编辑全禁（`object-backend/osv1-osv2-migration.md:23`）；有 soak period，最长 14 天（`:78`）。
- 索引行为：OSv1 仅对 APPEND/UPDATE 增量，SNAPSHOT 必全量（`object-databases/object-storage-v1.md:38`）；OSv1 无 Spark 回退（`ontologies/oss-limitations.md:74`）。
- 数据质量：OSv2 更严并新增主键/类型/尺寸限制（`object-backend/object-storage-v2-breaking-changes.md:42`、`object-indexing/data-restrictions.md:10`）。
- 生命周期：OSv1 "will be unavailable after June 30, 2026"（`object-backend/overview.md:90`）。

## 4. 对象存储与索引

### 4.1 存储 vs 索引：官方不把它们当两个并列概念

- indexing 是**动作**："**indexing** is the process of making tabular or other forms of data in Foundry datasources available for faster data retrieval operations through specialized databases."（`object-indexing/overview.md:10`）。
- object database 是**载体**："Object databases are the services responsible for storing the indexed object data ... also responsible for indexing, querying, and orchestrating user edits."（`object-backend/overview.md:43`）。
- 分工：Funnel 负责写（`:74`），OSS 只负责读（`:49`）。
- **索引数据是易失的**："All indexed data in object databases are considered ephemeral, requiring persistent storing of all Ontology data in other ways."（`object-edits/how-edits-applied.md:73`）——真实持久层是 Foundry datasets，索引可重建。
- 因此不要类比成"存储一套、索引一套"：官方是"持久 dataset → Funnel 索引 → object database 查询"。

### 4.2 索引管线

- 四段：Changelog → Merge changes → Indexing → Hydration（`object-indexing/funnel-batch-pipelines.md:14-19`）；hydration = 把索引文件下载到搜索节点磁盘（`:39`）。中间 dataset 全归 Funnel、用户不可访问（`:27`、`:31`、`:35`）。
- live pipeline 跟随 datasource 更新，有编辑时每 6 小时也跑（`:51`）；schema 变更另起 replacement pipeline，首次成功后替换 live（`:57-59`）。
- 默认增量："Funnel pipelines use incremental indexing by default for all object types."（`:69`）；单事务改动超 80% 退化为全量（`:119`）。
- 增量 dataset 语义："uses a 'most recent transaction wins' strategy ... You may not have duplicate primary keys within a single transaction."（`:75`）；`is_deleted` 默认**不**当删除列，需 legacy OSv1 changelog metadata（`:77`、`:83`）。

### 4.3 支持的 datasource 类型

- 批量：datasets、restricted views（`object-backend/overview.md:74`、`object-permissioning/multi-datasource-objects.md:10`）；流式：streaming datasources（`object-indexing/overview.md:14`）；低延迟写入：direct datasources [Beta]，"Currently, only Pipeline Builder is a supported writer application."（`object-indexing/direct-datasources.md:14`）。
- direct datasource 取舍："trade throughput for more robust Ontology writing capabilities, including user edits on streams and time-based ordering."（`:16`）；source timestamp 丢弃乱序写（`:26`）；replay 清空对象视图（`:32`、`:94`）；非法写被丢弃（`:73`）。
- 流式不支持 materialization（`object-indexing/faq.md:68`）与 Action（`object-edits/overview.md:16`）；m2m link type 支持流式（`object-indexing/faq.md:60`）；吞吐上限 2 MB/s/对象类型（`:44`）。

### 4.4 MDO（multi-datasource object）

- "A multi-datasource object type (MDO) ... is only available in Object Storage v2. At this time, only Foundry datasets or restricted views can be used for MDOs. Streaming sources are not supported."（`object-permissioning/multi-datasource-objects.md:10`）。
- 只有列式："Foundry only supports column-wise MDOs and does not support row-wise MDOs."（`:21`），row-wise 由 restricted views 替代（`:17`）。
- 属性多重性不支持："a specific property of an object type must come from one—and only one—of the input datasources (except for the primary key property, which must exist in every input datasource to join all datasources)."（`:53`）。
- 与"两个对象类型 + link"的取舍："traversing links between multiple object types is a more expensive operation than filtering on a property on the same object type"（`:61`）。
- 上限 70 个 datasource（`:69`）；无权访问的 datasource 对应属性显示 null（`:47`）；编辑与 materialization 均支持（`:43`）。

### 4.5 限制

- 索引期强校验：单事务内重复主键直接失败、跨事务后者胜（`object-indexing/data-restrictions.md:14`）；主键禁用 Geopoint/Geoshape/Array/Time series/decimal,double,float（`:16-22`）；类型不匹配、NaN/±infinity、空字符串、嵌套数组、数组含 null 全部拒绝（`:28-34`）；字符串 12MB、数组 10 万元素（`:44-47`），超限任务失败（`:49`）；大关系建议用 link type 而非大数组（`:54`）。
- 主键必须唯一且**确定**："The primary key you select must be unique for every record in the datasource. If your ontology is using Object Storage v2, a duplicate primary key will cause Funnel batch pipeline errors"（`object-link-types/create-object-type.md:93`）；"If the primary key is non-deterministic and changes on build, edits can be lost and links may disappear."（`:95`）。
- 规模：单对象类型 2000 属性、单 Action 最多 10,000 对象、Search Around 默认 10 万（`object-backend/overview.md:113`、`:109`、`:115`）；Search Around 叶子集 1000 万、单查询总量 3000 万（`ontologies/oss-limitations.md:82`）。
- 聚合不保证精确：OSS 默认 `PREFER_SPEED`，响应带 `AggregateResultAccuracy`（`object-backend/aggregation-considerations.md:14-15`、`:31`），只有 OSDK 恒为 `ACCURATE`（`:49`）。

## 5. 与本项目对照

> 我方形态：**直连源表投影（无对象存储/索引）+ 属性覆盖层（Action 改覆盖表，不改源库）**。依据：`ontology/README.md:132-140`（"在对象存储之前先提供'数据库查询 → 代码组装对象'的只读投影"、"不落对象存储、不做写入"）、`:107-111`（Action 写 `chat_data_ontology_object_edit` 覆盖层 + 审计，"不直接改源库"）。

### 5.1 已对齐

- 三实体与 API 名冻结：`ontology/README.md:3`、`:8`；对应官方 ID/API name/type classes 元数据（`object-link-types/link-type-metadata.md:12-24`），`active` 冻结类比官方 active 不可删、不可改 API name（`object-link-types/edit-link-types.md:65`、`:90`）。
- FK→PK 红线：`ontology/README.md:94`（"`to_columns` 必须引用 to 对象类型标识属性"）≈ `object-link-types/create-link-type.md:80` + "the key of one of the object types must map to the Primary key ... ensuring that the 'one' side of the Cardinality is unique"（`object-link-types/edit-link-types.md:88`）。
- 双向导航：`ontology/README.md:138`（"关系类型双向，当前对象既可作 from 也可作 to"）≈ `object-link-types/link-types-overview.md:22-24`（两个 side、不生成反向 link type）。
- 主键业务化且确定：`ontology/README.md:9`（"禁自增行号"）≈ `object-link-types/create-object-type.md:95`（非确定主键会导致编辑丢失、关系消失）。
- 派生属性语义：查询时计算/只读/无物理列（`ontology/README.md:58`、`:172`）≈ `ontology/derived-properties.md:10`、`:22`；禁作主键（`ontology/README.md:69`）≈ `ontology/derived-properties-reference.md:121`；禁入物理映射与"不可被 action 编辑"（`ontology/README.md:69`、`ontology/derived-properties-reference.md:120`）同向；无 OSv1 故天然满足 `ontology/derived-properties.md:92`。
- "一源一对象类型"引用正确，官方原文见 `object-link-types/create-object-type.md:176`（"A single datasource can only be used to back one object type."），见 `ontology/README.md:26-28`。
- 缺口标注诚实：Interfaces/Shared Properties/Structs/Object Set 未实现（`ontology/README.md:196`、`capability-audit.md:26-27`）。

### 5.2 简化

- 基数只做 `many_to_one`/`one_to_one`，m2m 走 junction 对象类型（`ontology/README.md:10`）≈ 官方 backing object 形态（`object-link-types/create-link-type.md:169-171`），但**缺 join table 形态**（`:107-116`），连带缺"可写回的 m2m"（官方明确 m2m 才可 writeback，`:109`）。
- 派生属性单跳、非自关联、单列（`ontology/README.md:173`）vs 官方 3 跳（`ontology/derived-properties.md:104`）；算子 count/sum/avg/min/max（`ontology/README.md:173`）vs 官方 9 种含两种 cardinality 与两种 collect（`object-link-types/derived-properties.md:48-56`）；无 collect limit 语义（`ontology/derived-properties.md:80-84`）。
- 类型收窄：`ontology/README.md:68`（count→Integer，sum/avg→Decimal）vs 官方 Count→`Long`、Average→`Double`、Sum 加宽（`ontology/derived-properties-reference.md:22-24`、`:39-47`）；`ontology/README.md:199` 已自认超大整数有 JS 精度风险。
- filter/sort 已 JOIN 下推，但 aggregate/group by 显式拒绝（`ontology/README.md:75`）vs 官方 aggregate/group by 均支持（`ontology/derived-properties-reference.md:117-118`）。
- Action 只有 `set_property`，无 create/delete/link、无回滚/副作用（`ontology/README.md:104`、`:111`）vs 官方覆盖对象/链接/对象集三类编辑（`object-edits/overview.md:10`、`object-edits/permission-checks.md:59-69`）。
- 对象身份带 member 维度：`ontology/README.md:23`（`member_datasource_id:object_type:local_key`）vs 官方"同一 Ontology 内两个对象"（`object-link-types/link-types-overview.md:10`）+ 编辑锚定主键（`object-link-types/create-object-type.md:95`）。
- 状态机 `draft/experimental/certified/deprecated`（`ontology/README.md:201`）vs 官方 `active/experimental/deprecated`（`object-link-types/link-type-metadata.md:14`）。

### 5.3 缺失

- **无索引层**：查询直接落到物理表（`ontology/README.md:135-136`），没有"持久 dataset → 索引 → 查询"分层（`object-backend/overview.md:43`、`:74`、`object-edits/how-edits-applied.md:73`）。代价：无 hydration/替换管线（`object-indexing/funnel-batch-pipelines.md:39`、`:57`），也没有"索引可丢弃重建"的容错。
- **无 data restrictions 门禁**：重复主键、空串、NaN、嵌套数组、12MB/10 万上限（`object-indexing/data-restrictions.md:14`、`:30-34`、`:44-47`）在投影路径上无对应校验。
- **无 edit history / schema migration / materialization**：`ontology/README.md:111` 只声明"回滚、撤销、副作用/Webhook、Action UI 尚未实现"；官方见 `object-edits/user-edit-history.md:10`、`object-edits/schema-migrations.md:16`、`object-edits/materializations.md:10`。
- **无 Object Set**（`capability-audit.md:27`），官方对象集是 static/dynamic/temporary/permanent 四类一等资源（`object-backend/overview.md:57-63`）。
- **无 MDO**（`ontology/README.md:195`），官方支持列式 MDO 与 70 datasource 上限（`object-permissioning/multi-datasource-objects.md:10`、`:69`）。

### 5.4 可能违背官方约束的点（按风险排序）

1. **源行消失的对象可见性**：官方下"源行消失"会让对象消失，但用户 Create/编辑过之后源行再次消失，**对象仍保留**（T12，`object-edits/how-edits-applied.md:117`），且删除不算编辑、重建不继承旧编辑（`:86`）。我方投影是 `SELECT` 源表，源行消失对象即消失、覆盖层变孤儿行——直接偏离官方可见性契约。
2. **无冲突解决策略**：官方有 user-edits-win / most-recent-value 两策略并可按 datasource 配置（`:90-91`、`:127-129`）；`ontology/README.md:202` 自认"源行消失/源列被 Pipeline 重建时的行为未定义"。开放 create/delete/link 前必须先定策略（官方 T0-T14 即契约）。
3. **主键唯一性未强制**：官方重复主键在 OSv2 直接索引失败（`object-indexing/data-restrictions.md:14`、`object-link-types/create-object-type.md:93`），因为"edits are associated with the primary key of the object"（`:95`）。我方无唯一性校验，重复 `local_key` 会产生重复对象，并让覆盖层按 key 关联时命中多行。
4. **派生属性类型收窄的溢出面**：Count→`Long`（`ontology/derived-properties-reference.md:22`）vs 我方 Integer（`ontology/README.md:68`）；Sum 官方还要求加宽（`:39-47`）。超 2^31 的计数会**溢出**而非精度损失，属实现缺陷而非"子集"。
5. **member 编入对象身份导致跨组关系不可表达**：官方关系限于同一 Ontology，MDO 用同一主键 join 各 datasource（`object-link-types/link-types-overview.md:10`、`object-permissioning/multi-datasource-objects.md:53`）；我方 `object_key` 带 member（`ontology/README.md:23`、`:137`），等价于"ontology 内部隐式分片"，与"对象类型内身份唯一"不同构。
6. **派生属性 filter 与 collect limit 语义**：官方过滤跑**全集**、与返回列表截断无关（`ontology/derived-properties.md:82-84`）。将来实现 collect + limit 时必须复刻该条，否则出现"过滤命中但值不在列表"的不可解释结果。

## 6. 待确认问题

1. 官方"only one primary key for each object type"（`object-link-types/create-link-type.md:95`）是否允许**多列复合**主键？本页未明确（MDO 只说 PK 必须存在于每个 datasource，`object-permissioning/multi-datasource-objects.md:53`）。
2. 外键型 link 两侧键类型是否允许隐式 cast？官方只对 m2m 明说类型不一致会阻止保存（`object-link-types/edit-link-types.md:87`），FK 型未明确。
3. link type 基数/外键变更后，引用它的**派生属性**是否被自动阻断或重算？`object-link-types/edit-link-types.md:16-23` 只说 reindex/暂不可用。
4. 官方是否存在**任意长度路径**查询（区别于固定 3 跳派生与单跳 Search Around）？`ontologies/oss-limitations.md:97-106` 只描述一跳，多跳路径 API 文档未明确。
5. `AggregateResultAccuracy` 在派生属性聚合上如何表现？`object-backend/aggregation-considerations.md` 只讨论对象集聚合。
6. 我方 `member_datasource_id:object_type:local_key` 与官方"同 Ontology 内对象唯一"如何等价映射，是否需要"跨 member 同实体"的合并视图（官方对应 MDO，`object-permissioning/multi-datasource-objects.md:53`）。
7. Action 执行（覆盖层 + 审计）是否等价官方"single transaction"（`object-edits/overview.md:12`）：`ontology/README.md:86` 只说 `transaction_scope` 编排，未说明 execute 是否单事务、失败是否整体回滚。
8. 官方 edit-only 属性必须 permission 到某个 backing dataset（`object-link-types/edit-only-properties.md:19`），我方覆盖层属性（`ontology/README.md:107-109`）对应哪一级权限主体，文档未定义。
