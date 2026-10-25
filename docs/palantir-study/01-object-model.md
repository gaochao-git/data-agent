# Palantir 对象模型研读

> 范围：`docs/palantir-ontology/` 本地官方文档副本，仅做文档研读，未修改任何代码。行号以本地副本为准。

## 0. 依据

URL 映射取自 `docs/palantir-ontology/_index.md`（例如 `:639` 映射 core-concepts、`:647/:645` 映射 best-practices/anti-patterns、`:528` 映射 create-object-type、`:670` 映射 save-changes）。

| 本地路径（相对 `docs/palantir-ontology/`） | 官方 URL 路径 |
|---|---|
| `ontology/core-concepts.md`、`ontology/why-ontology.md` | `/docs/foundry/ontology/core-concepts`、`/docs/foundry/ontology/why-ontology` |
| `ontology/ontology-best-practices.md`、`ontology/ontology-anti-patterns.md` | `/docs/foundry/ontology/ontology-best-practices`、`/docs/foundry/ontology/ontology-anti-patterns` |
| `object-link-types/object-types-overview.md`、`create-object-type.md`、`edit-object-type.md`、`properties-overview.md` | `/docs/foundry/object-link-types/...`（同名） |
| `object-link-types/value-types-overview.md`、`create-value-type.md`、`value-formatting.md`、`value-type-constraints.md`、`structs-overview.md`、`create-struct-type.md`、`shared-property-overview.md`、`type-groups.md` | `/docs/foundry/object-link-types/...`（同名） |
| `interfaces/interface-overview.md`、`interfaces/create-interface.md` | `/docs/foundry/interfaces/interface-overview`、`/docs/foundry/interfaces/create-interface` |
| `ontology-manager/overview.md`、`save-changes.md`、`export-import.md` | `/docs/foundry/ontology-manager/...`（同名） |

对照用的项目文件：`server/apps/chat_data_app/services/ontology/README.md`（下称 README）、`docs/ontology-capability-audit.md`（下称 audit）。

## 1. 概念定义

### 1.1 Ontology（容器）

- 定义：“In Foundry, the Ontology is the digital twin of an organization, integrating the organization's digital assets (datasets and models) into a coherent whole.”（`ontology/core-concepts.md:14`）
- 组成：“mapping datasets and models to object types, properties, link types, and action types.”（`ontology/core-concepts.md:14`）；四条子定义见 `:16-19`。
- 定位：“The Ontology represents the _decisions_ in an enterprise, not simply the data.”（`ontology/why-ontology.md:12`）
- 作用域：“A space can hold a single ontology. Value types can only be used within the space in which they were defined.”（`object-link-types/value-types-overview.md:10`）
- 对照：README:7 把本体定义为「纯语义容器**不绑数据源组**」，属有意简化；官方以 space 为容器边界且一 space 一 ontology，我们的表结构允许多本体且不表达 space 约束（简化，且丢失了官方「同一 space 内类型/值类型可互相引用」的隐式前提）。

### 1.2 Object type

- 定义：“An **object type** is the schema definition of a real-world entity or event.”（`object-link-types/object-types-overview.md:10`，同文 `ontology/core-concepts.md:37`）
- 实例与集合：“An **object set** refers to a collection of multiple object instances”（`object-link-types/object-types-overview.md:10`）。
- 关键约束：“Every object type requires at least one property. This is because object types need a primary key to uniquely identify them.”（`create-object-type.md:76`）；“Every object type still requires a primary key, and each object created through an action must have a unique primary key value.”（`:58`）
- 数据源：“A single datasource can only be used to back one object type.”（`create-object-type.md:176`）；`:41` 说明可从已有 datasource 自动推断元数据与属性，但一个对象类型能否挂多个 datasource 在本副本未明确（见 §5.3）。
- 必填字段：ID、Display name、Plural display name、Backing datasource（无源时除外）、API name（`create-object-type.md:265-271`）。
- 生命周期：status 取值 `deprecated`/`experimental`/`active`（`edit-object-type.md:114`）；“Object types with an `active` status cannot be deleted.”（`:78`）；active 状态下 API name 不可改（`:118`），而 object ID 与状态无关、创建后终身不可改（`:124`）。
- 对照：README:8 状态机 `draft → experimental → certified`、`deprecated` 终态、API 名创建后不可改，语义与 `edit-object-type.md:78/:118` 同向，状态命名与官方不同（官方为 active，我们为 certified，audit:201 已记录）。**官方无 `draft` 态**（文档未出现），属我们新增。

### 1.3 Property

- 定义：“A **property** of an object type is the schema definition of a characteristic of a real-world entity or event.”（`object-link-types/properties-overview.md:10`）
- 基类型支持矩阵（`properties-overview.md:22-38`）：`String/Integer/Short` 可作标题键与主键；`Date/Timestamp` 可作标题键、作主键「Discouraged」；`Float/Double/Decimal`、`Array`、`Geopoint`、`Cipher` 可作标题键但不可作主键（`:27`、`:29`、`:34`、`:38`）；`Vector`、`Struct`、`Media Reference`、`Time Series`、`Geotemporal Series`、`Attachment`、`Geoshape`、`Marking` 既不可标题键也不可主键（`:28`、`:32-33`、`:36-37`）。
- 类型陷阱：`Long` 有 JS 表示问题（`:26`）；`Array` 不能含 null 元素（`:29`）；`Geopoint` 以 `latitude,longitude` 逗号串存储（`:34`）。
- 对照：README:9 值类型只有内建 6 种 + `enum`，audit:197 承认缺 `Long`/`Float`/`Double`。官方把「能否作主键/标题键」编码进基类型矩阵，我们目前是服务端规则（README:88-94），**缺少类型矩阵这一层显式声明**。

### 1.4 Primary key

- 定义：“The property that acts as a unique identifier for each instance of an object type. Every object must have a unique value for this property. For a datasource-backed object type, each row in the backing datasource must have a different value.”（`create-object-type.md:86`，另见 `:218`）
- 重复后果：“a duplicate primary key will cause [Funnel batch pipeline] errors leading to a build failure”（OSv2）；“an update will appear as successful; however, the duplicate primary keys can cause unexpected changes”（OSv1）（`create-object-type.md:93`）
- 确定性：“primary keys should be deterministic. If the primary key is non-deterministic and changes on build, edits can be lost and links may disappear.” / “Avoid using numbered row or random key generation”（`create-object-type.md:95`）
- 与编辑绑定：“Edits are permanently attached to the primary key value you made them for. Any time you change the primary key of an object type, you will be prompted to delete all existing edits.”（`create-object-type.md:221`）；改主键属破坏性变更，需 unregister/reregister（`edit-object-type.md:25`）。
- 对照：README:9 主键 = `is_identifier` 属性组合且「禁自增行号」，与 `:95` 的确定性要求对齐；但官方本节通篇用单数 “the property”，**复合主键在本文档未明确**（`properties-overview.md` 亦按单属性判断），我们支持组合主键属扩展。

### 1.5 Title key

- 定义：“The property that acts as a display name for objects of this type.”（`create-object-type.md:84`、`:222`）
- 类型限制：见 1.3 矩阵；`Array` 作标题键时“If the inner type of the `Array` is not a valid title property, the `Array` property also cannot be used as the title property.”（`properties-overview.md:30`）
- 变更成本：“Changing the display name, title key, render hints, type classes, and visibility of a property that has received edits will _not_ require the object type to unregister.”（`edit-object-type.md:44`）——即官方视 title key 为可安全变更项。
- 对照：README:88 把「标题键」列入 certified 冻结项，比官方更严。官方允许改标题键且不触发重建，我们的冻结是产品选择，不是官方约束，**不应表述为「对齐官方」**。

### 1.6 Value type

- 定义：“**Value types** are semantic wrappers around a [field type] that include metadata and constraints that can enhance type safety, improve expressiveness, and provide additional context.”（`object-link-types/value-types-overview.md:10`）
- 目的：“value types capture the context and semantic meaning of data and centralize data validation”（`:12`）；示例是带正则约束的 `email` 类型（`:14`）。
- 治理：“they are permissioned ... and versioned to handle both breaking and non-breaking edits.”（`:16`）
- 约束：可选一条 constraint，并需配置 “Failure validation message”（`create-value-type.md:20`）；Enum 合法基类型 String/Boolean/Decimal/Double/Float/Integer/Short，Range 合法基类型含 Date/Timestamp/String/Array（`value-type-constraints.md:12-18`）；String 另有 Regex/RID/UUID，Array 有 Uniqueness/Nested，Struct 有 Element constraints（`:24-32`）。
- 对照：README:9「值类型为内建 6 种或 `enum`（`enum_ref` 引用全局枚举表）。不存枚举值/单位/聚合口径」——只覆盖了官方 Enum constraint 的一个子集，**没有值类型一等实体、没有正则/范围/UUID、没有版本化**（简化）。`enum_ref` 全局枚举在语义上接近官方「可复用约束」，但不带基类型与校验消息。

### 1.7 Struct

- 定义：“A **struct** is an Ontology property base type that lets a single property hold several fields instead of one value.”（`object-link-types/structs-overview.md:10`）
- 存储：“A struct property is backed by a single datasource column whose type is itself a struct.”（`structs-overview.md:12`）
- 约束：“A struct property cannot contain another struct.” / “A struct field cannot be an array, although a struct property itself can hold an array of structs.” / “A struct property must have at least one field.”（`structs-overview.md:18-20`）；字段类型白名单 12 种（`:21-33`）。
- 语义陷阱：“a query evaluates each field condition independently”，数组场景下不同元素分别满足条件也可命中（`structs-overview.md:39-41`）。
- 可用性：“Structs will not be supported in Object Storage v1 (Phonograph).”（`structs-overview.md:58`）
- 对照：audit:196 记为未实现。**待确认**：`create-object-type.md:43` 写 “A backing datasource for an object type may not contain `MapType` or `StructType` columns.”，与 `structs-overview.md:12`（struct 属性必须由 struct 列支撑）字面冲突，疑为引导式 helper 路径的旧限制，见 §5。

### 1.8 Shared property

- 定义：“A **shared property** is a property that can be used on multiple object types in your ontology. Shared properties allow for consistent data modeling across object types and centralized management of property metadata. While property metadata is shared across objects, the underlying object data is not.”（`object-link-types/shared-property-overview.md:10`，同 `ontology/core-concepts.md:49`）
- 生命周期：可直接创建，也可把已有属性转成共享属性，之后像普通属性一样使用/编辑（`shared-property-overview.md:14`）；UI 上用地球图标标识（`:16`）。
- 对照：未实现（audit:196 称为占位）。我们的属性虽是一等实体表，但**没有「同一属性定义被多个对象类型引用」的多对多结构**，因此不具备官方「一处改元数据、多对象生效」的能力。

### 1.9 Interface

- 定义：“An **interface** is an Ontology type that describes the shape of an object type and its capabilities.”（`interfaces/interface-overview.md:10`）
- 组成：“An interface is composed of interface properties, [link type constraints], [action type constraints], and metadata about the interface.”（`:18`）；可被多对象类型实现（`:18`），可多继承（`:20`）。
- 抽象性：“interfaces are abstract; they have schemas defined by interface properties, are not backed by datasets, and cannot be instantiated directly but must be instantiated as a specific object type.”（`:26-28`）——与 object type 的本质区别。
- 强制力：“For **required** properties, any object type that implements the interface must provide a mapping from a local property to the interface property.”（`create-interface.md:24`）；required 的 link type / action type constraint 同样必须在实现类型上落地（`create-interface.md:42`、`:54`）。
- 权限：“Interfaces are permissioned through [Ontology roles].”（`interface-overview.md:34`）
- 对照：未实现（audit:196）。我们无「实现关系」与「required 属性强制映射」这套校验，README 中的关系类型只有 FK→PK 二元结构，无法表达多态。

### 1.10 Type group

- 定义：“Object type groups are a classification primitive that helps users better search and explore their ontology.”（`object-link-types/type-groups.md:10`）
- 创建/权限：由 ontology owners/editors 管理（`:10`）；“To view object type groups, users must have **viewer** permission on the project that the object type group is in.”（`:24`）
- 历史：“the _group_ primitive described on this page has replaced the tag-based system of legacy groups.”（`:28`）；旧做法是在主键属性上打 `oe_home_page_object_type_group` type class，“this previous method is no longer available.”（`create-object-type.md:166`）
- 对照：缺失。属纯组织/检索能力，优先级低；注意我们若用「属性/标签近似 group」，正对应官方已废弃的 type class 方案。

### 1.11 Object set

- 定义：“An **object set** refers to a collection of multiple object instances; that is, an object set represents a group of real-world entities or events.”（`ontology/core-concepts.md:37`）
- 数据集类比：“The definition of an object set is analogous to a filtered set of rows in a dataset.”（`object-link-types/object-types-overview.md:16`）
- 接口维度：Object Set Service 支持 “Search and sort objects by interfaces”（`interface-overview.md:51`），暗示 object set 可跨实现类型。
- 对照：audit:27/42/60 标为缺失，“查询结果只活在当轮对话”。这是本项目 P0 缺口（audit:94）。

### 1.12 Ontology 变更管理（容器级规范）

- 工作副本：“Any changes you make in the Ontology Manager are stored locally in a work-in-progress state.”（`ontology-manager/save-changes.md:12`）；对象类型创建同理：“Selecting **Create** will only stage your changes and **will not save** them.”（`create-object-type.md:107`）
- 错误/警告分级：“While errors need to be handled in order to save, warnings will not prevent you from saving.”（`save-changes.md:32`）
- 并发：他人已保存时需先 Update 再合并，冲突需逐项选择保留哪边（`save-changes.md:40-42`）
- 破坏性确认：“you can type in the name of the entity you edited to proceed with saving.”（`save-changes.md:58`）
- 导入导出：“You should not depend on the exported JSON schema as it may change over time.”（`ontology-manager/export-import.md:10`）；带 conditional formatting 的工作状态不能导入到别的 ontology（`:29`）。
- 对照：README:86-87 用 `transaction_scope` + `confirm_destructive=true` 表达同类语义，方向一致；差异是我们**没有 working state / 批量 review / 并发合并**（每次写即生效），属显著简化。

## 2. 规范约束清单

| 约束 | 原文依据 | 含义 |
|---|---|---|
| 对象类型至少一个属性 | `create-object-type.md:76` | 因必须有主键，属性数 ≥1 |
| 必须有主键 | `create-object-type.md:58` | 无源（action 创建）的对象类型也不能省 |
| 主键值唯一 | `create-object-type.md:86` | 数据源每行值必须互异 |
| 主键应确定性 | `create-object-type.md:95` | 禁用行号/随机生成，否则丢编辑、断链 |
| 改主键需清编辑 | `create-object-type.md:221` | 编辑永久绑定主键值 |
| 一个 datasource 只支撑一个对象类型 | `create-object-type.md:176` | 复用会报 `Phonograph2:DatasetAndBranchAlreadyRegistered`（`:332`） |
| 对象类型 ID 规则 | `create-object-type.md:144-146`、`:292-294` | 小写字母/数字/连字符，首字符 must 为小写字母，全局唯一 |
| 属性 ID 规则 | `create-object-type.md:302-304` | 大小写字母/数字/连字符/下划线，同对象类型内唯一 |
| 改 ID 会破坏应用 | `create-object-type.md:146`、`:210` | 保存后任何 ID 变更都可能 break 应用 |
| object type API name | `create-object-type.md:245-248` | 大写开头、PascalCase、全局唯一、1-100 字符 |
| property API name | `create-object-type.md:254-257` | 小写开头、camelCase、同类型内唯一、1-100 字符 |
| API name 保留字 | `create-object-type.md:326` | `ontology`/`object`/`property`/`link`/`relation`/`rid`/`primaryKey`/`typeId`/`ontologyObject` 禁用 |
| 保存前必填字段 | `create-object-type.md:265-282` | 对象类型 5 项、属性 6 项（含 title/primary key） |
| object ID 不可改 | `edit-object-type.md:124` | 创建后终身固定 |
| active 不可删 | `edit-object-type.md:78` | 只能先降级 |
| active 不可改 API name | `edit-object-type.md:118` | 防止断引用 |
| 破坏性变更需重建索引 | `edit-object-type.md:22-26` | 换源、改主键、删类型 |
| 曾接收编辑的属性改 schema 需重建 | `edit-object-type.md:40` | 含 ID 与 base type 变更 |
| Long 的 JS 精度风险 | `properties-overview.md:26` | >1e15 前端库不可靠 |
| Array 元素不得为 null | `properties-overview.md:29` | 索引期约束 |
| Struct 不得嵌套、字段不得为数组 | `structs-overview.md:18-19` | 且字段类型限于 12 种白名单（`:21-33`） |
| Struct 需要 struct 列支撑 | `structs-overview.md:12` | 与 `create-object-type.md:43` 字面冲突 |
| 值类型只在所属 space 可用 | `value-types-overview.md:10` | 且不适用于 Default ontology |
| 值类型约束需失败消息 | `create-value-type.md:20` | 校验不通过时的提示必配 |
| 接口 required 属性必须映射 | `create-interface.md:24` | 实现类型不能跳过 |
| 接口不可实例化 | `interface-overview.md:26-28` | 无 dataset 支撑 |
| 组可见性最低 project viewer | `type-groups.md:24` | 与「所有组对可查看 ontology 的用户可见」并读（`:34`） |
| 导出 JSON 不是稳定契约 | `export-import.md:10` | 不得依赖其 schema |

## 3. 官方反模式 / 最佳实践

### 3.1 四条设计原则（按优先级，冲突时高优先级胜出）

| 优先级 | 原则 | 原文（`ontology/ontology-best-practices.md`） |
|---|---|---|
| 1 | Domain-driven design | “Model the real world, not the source data.”（`:40`） |
| 2 | Do not repeat yourself | “If you built the same thing three times, refactor.”（`:41`） |
| 3 | Open for extension, closed for modification | “Protect core models. Enable builders to extend them.”（`:42`） |
| 4 | Composition over deep hierarchies | “Favor multiple inheritance via interfaces. Keep things pluggable.”（`:43`） |

补充要点：
- “Links should represent real relationships ("this patient visited this facility"), not join keys or foreign key artifacts.”（`ontology-best-practices.md:51`）
- “When asked to 'ontologize a dataset', resist the urge to map columns 1:1 to properties and consider the work complete.”（`:53`）
- 命名：“Prefer `equipment.lastInspectionDate` over `equipment.dtLastInspMod`.”（`:100`）
- 三条重复即重构（`:110`、`:161`）
- 抽象优先于宽表：“When entities share common characteristics, model the abstraction with interfaces rather than creating wide, sparse object types.”（`:26`）
- 非语义类型应隐藏：“Mark non-semantic types ... as hidden”（`:102`）
- 务实条款：“These principles are guides, not laws.”（`:288`）；“Defend the critical invariants ... Naming quality, semantic clarity, and security design are hard to fix later.”（`:297`）

### 3.2 八条反模式（总表 `ontology-anti-patterns.md:16-23`）

| 反模式 | 原文要点 | 对我们的映射 |
|---|---|---|
| System Silos | 同一实体按来源系统拆多个对象类型（`:27`），应合并为一个并做 pipeline 合并（`:58`） | 多数据源组各自建同义对象类型即命中 |
| The Kitchen Sink | 把 ETL 元数据列做成属性（`:81`），解决：只保留有业务含义的列（`:119`） | 一键从 Dataset 全列生成属性的产品设计有风险 |
| Department Silos | 各部门自建客户类型（`:143`），应建共享类型 + 属性/链接承载差异（`:178`） | 同上，需治理而非技术约束 |
| The God Object | 一个类型塞多实体、大量 null 属性（`:208`、`:221-224`），应拆类型 + 用 interface 表达共性（`:254`） | 「万能对象类型」需在建模引导里阻止 |
| The Golden Hammer | 用错层（`:285-287`）：action 做本该 pipeline 预算的聚合（`:302`）、pipeline 做事件驱动（`:306`）、function 做简单拼接（`:310`） | 我们的 Action 只做 `set_property`，暂无此风险，但派生属性算子在查询期计算需注意 `:310` 同类问题 |
| Action Sprawl | 大量单属性 action（`:382`），“Action names that read like `Set [Property]` or `Update [Property]`”（`:397`） | 我们是参数 + `set_property` 的通用动作模型，**天然容易产生 `Set X` 类动作** |
| The Time Machine | 把历史版本建模成多个对象/对象类型（`:464`），应单对象 + 链接历史对象或时间序列（`:506`） | 若用快照表生成历史对象类型即命中 |
| The Misnomer | 泛化命名（`:536`），“Property names are single generic words like `value`, `type`, `status`, `date`, or `name` without qualification”（`:552`） | 命名治理缺失时高发 |

时间序列相关落地建议：“Leverage time series properties for values that change frequently and need temporal tracking.”（`ontology-anti-patterns.md:527`）

## 4. 与本项目对照

### 4.1 已对齐

- **本体不绑存储**：README:7 明确本体是纯语义容器，与 `core-concepts.md:14` 把 ontology 描述为数据集/模型的映射层同向。
- **一源一对象类型**：README:26-28 与 `create-object-type.md:176` 字面一致；audit:200 已澄清这不是错误归因。
- **主键确定性 / 禁自增行号**：README:9 与 `create-object-type.md:95` 一致。
- **破坏性变更需确认**：README:87 的 `confirm_destructive` 对应 `save-changes.md:58` + `edit-object-type.md:22-26`。
- **状态门控可见性**：README:153 只把 certified/experimental 注入目录、draft/deprecated 不外泄，对应官方“Object types with an `active` status cannot be deleted.”（`edit-object-type.md:78`）这类「已发布态受保护」的分级思路。
- **API 名不可变**：README:8 与 `edit-object-type.md:118`（active 状态冻结 API name）方向一致。
- **必须有标识属性**：README:24/89 的 certified 前置（至少一个标识属性且绑定覆盖）与 `create-object-type.md:58/76`（任何对象类型都需主键）同向。

### 4.2 简化（有意识收窄，非 bug）

- **单 datasource / 单标识属性 MVP**（README:136、audit:24）↔ 本地副本只正面写了「一个 datasource 只支撑一个对象类型」（`create-object-type.md:176`），多源能力未展开（见 §5.3）。
- **值类型仅 6 种内建 + enum**（README:9）↔ `value-types-overview.md:10-16` 的一等实体 + 版本化 + 多约束。
- **无 working state / review edits / 并发合并**（README:86 直接事务写）↔ `save-changes.md:12/40-42`。
- **标题键冻结**（README:88）↔ `edit-object-type.md:44` 官方允许改 title key 且不需重建。
- **对象身份 `member_datasource_id:object_type:local_key`**（README:23）：官方文档未定义此三段式 key 形态（`ontology-manager/*` 未提），属自研口径。

### 4.3 缺失

| 缺口 | 官方依据 | 影响 |
|---|---|---|
| Object Set | `core-concepts.md:37`、`object-types-overview.md:16` | 查询结果不可传递，Action 无法批量作用（audit:94） |
| Interface（含 required 属性强制映射） | `interface-overview.md:10-28`、`create-interface.md:24` | 无多态抽象，共性靠复制类型（命中 System/Department Silos） |
| Shared property | `shared-property-overview.md:10` | 元数据无法集中治理，多类型口径漂移 |
| Struct | `structs-overview.md:10-20` | 复合字段只能拍平成一堆属性（命中 Kitchen Sink） |
| Value type 一等实体 | `value-types-overview.md:10`、`value-type-constraints.md:12-32` | 无正则/范围/UUID 校验，无失败消息 |
| Type group | `type-groups.md:10` | 无分组检索（低优先级） |
| 类型可作主键/标题键的显式矩阵 | `properties-overview.md:22-38` | 我们只有零散服务端规则 |
| 对象类型 status 与 visibility 分离 | `edit-object-type.md:114-116` | 我们只有 status，无 `prominent`/`hidden` |

### 4.4 可能违背官方约束的点

1. **用 title key 作为外部可见身份的稳定性假设**：我们把标题键冻结进 certified 契约（README:88），官方则把 title key 列为可安全变更项（`edit-object-type.md:44`）。若前端/工具把 title 当稳定标识使用，会与官方「title 是展示名」的定位（`create-object-type.md:84`）冲突。
2. **属性 ID 语义漂移风险**：官方强调 “any change to the property ID will break the application”（`create-object-type.md:146`），我们允许删除/改名属性（README:90 只在关系悬空时拒绝），**未把「属性 ID 被引用即不可改」纳入门禁**；`edit-object-type.md:40` 更进一步：曾接收过编辑的属性改 ID 会丢编辑历史。
3. **`draft` 态破坏了「外部可见 = 已发布」的边界**：官方只有 active/experimental/deprecated（`edit-object-type.md:114`），我们额外引入 `draft`；只要 `draft` 不进目录（README:153）就安全，但状态迁移语义需与官方 `promoted`/`hidden` 对齐说明。
4. **Action 命名与粒度**：我们的通用 `set_property` 动作模型（README:11）若不约束命名，会直接落进 Action Sprawl（`ontology-anti-patterns.md:382/397`）。
5. **派生属性在查询期计算**：聚合类派生在只读投影里逐对象计算（README:73-75），与官方 “matching the tool to the job”（`ontology-anti-patterns.md:20`）存在张力；官方把预计算归 pipeline（`:302` 的 Golden Hammer 案例正好是「用 action 现场算区域销售汇总」）。
6. **属性生成策略**：从 Dataset 全列一键生成属性会命中 Kitchen Sink（`ontology-anti-patterns.md:119/134`），建议默认排除 `_etl`/`_extracted_at` 类列或至少给出提示。

## 5. 待确认问题

1. **`create-object-type.md:43` vs `structs-overview.md:12` 的 struct 列冲突**：前者禁止 backing datasource 含 `StructType`，后者要求 struct 属性必须由 struct 列支撑。推测 `:43` 描述的是引导式 helper / 旧 OSv1 路径，需查在线文档或实验确认现行约束。
2. **复合主键是否被官方支持**：`create-object-type.md:86/:218` 与 `properties-overview.md:22-38` 都按单属性表述，本地副本未出现 composite primary key 说明。我们的组合主键属扩展，需确认官方语义（含 edits 绑定、去重、索引）。
3. **多数据源对象类型（MDO）规则**：`create-object-type.md:41` 只暗示可选 datasource，本地副本无 MDO 专页；多源时主键唯一性、权限继承、对象身份如何定义均「文档未明确」。
4. **对象类型 ID 首字符规则冲突**：`:145` 写 “**must** be a lowercase letter”，`:293` 写 “Should start with a letter”，`:144` 又允许数字。规范强度不一致，需在线核对。
5. **API name 字符集冲突**：`:245-246` 要求「仅字母数字 + PascalCase」，`:312` 又允许下划线，`:320` 对 property 只说「valid Unicode」。三处口径不统一。
6. **官方 status 全集**：`edit-object-type.md:114` 只列 3 个（deprecated/experimental/active），audit:201 提到 `example` 与 `promoted`，但本地副本无对应页面（`object-link-types/metadata-statuses.md` 未在必读清单，可补读）。
7. **title key 是否必填**：`create-object-type.md:281-282` 把 Title key 列为保存必填，但 `properties-overview.md` 的 “Valid as title key?” 列暗示存在不可作标题键的类型；若对象类型所有属性都不可作标题键，官方如何处置 → 「文档未明确」。
8. **Object Set 的持久化与跨类型语义**：`core-concepts.md:37` 只给集合定义，`interface-overview.md:51` 提到可按 interface 搜索；持久化形态（static/dynamic/temporary）在本地副本未展开，需查 `object-sets/` 或 Object Set Service 在线文档。
9. **Value type 与 ontology 的绑定边界**：`value-types-overview.md:10` 说值类型属于 space、一个 space 一个 ontology；我们无 space 概念，若引入值类型需先定「值类型作用域 = 全局 or 本体」。
10. **删除/降级的官方等价物**：官方 active 不可删（`edit-object-type.md:78`）但仍可 deprecated；我们的 `deprecated` 终态是否需要「删除期限 + 替代资源」字段（audit:201 说官方要求，本地副本未给原文），需补读 `metadata-statuses.md`。
