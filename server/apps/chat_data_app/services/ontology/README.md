# ontology

本体服务层：智能问数的语义资产导航层，对齐 Palantir Foundry 本体（Object / Property / Link Type 三等实体）。

## 职责与边界

- **本体**（`chat_data_ontology`）：本体实体（对位 Palantir 一个 ontology），纯语义容器**不绑数据源组**（对象类型取数后端由各自映射指向的语义模型决定，可跨组，对齐 Palantir「本体不绑存储」）；删除为软删，挂有活跃对象类型时拒绝。2026-10-20 由「领域」更名而来（原 `chat_data_ontology_domain`），同日解耦组绑定（`group_id` 列废弃）。
- **对象类型**（`chat_data_ontology_object_type`）：业务实体类型，API 名全局唯一且创建后不可改；状态机 `draft → experimental → certified`，`deprecated` 为终态（附原因/期限/替代对象类型，不可逆）。软删同名重建视为新建：复活时状态重置为 `draft` 并清空废弃三元数据。
- **属性**（`chat_data_ontology_property`）：一等实体。主键 = `is_identifier` 属性组合（业务主键，禁自增行号），标题键 = `is_title` 属性；值类型为内建 6 种或 `enum`（`enum_ref` 引用全局枚举表）。不存枚举值/单位/聚合口径。
- **关系类型**（`chat_data_ontology_relation_type`）：基数仅 `many_to_one`/`one_to_one`（多对多走 junction 对象类型）；目标侧关联列必须引用对方标识属性（FK→PK 红线）；状态随对象类型级联（降级自动、升级手动）。

## 语义模型映射

对象类型 `mappings[]` 统一映射语义模型（层级 = 数据源组 → 语义模型 → 维度映射：
`semantic_config_id` + `column_mappings {属性API名: 模型维度名}`），不触碰物理表：
Palantir 本体物化物理表，我们的物化层是语义模型，本体根基随之对齐——认证对象类型
必然能在语义层拿到口径，问数链路保持「本体目录 → 语义模型 YAML → NL2SQL → sql_guard」闭环。
本体层只引用模型维度名，不重复定义物理列（Ossie 映射文档同构：映射文档只引用
语义模型，物理表解析是模型文档内部的事）；实体锚点由映射维度的所属表派生，
同一映射的全部维度必须同表（service 层校验）。

- 「一源一对象类型」唯一归属检查（Palantir backing 约束）：同一语义模型映射全局只能归属一个对象类型，唯一索引兜底 + service 层预检给友好报错。
- **跨组映射自由**（2026-10-20 解耦后）：对象类型的映射语义模型可属于任意数据源组，挂本体/换本体不再校验组锚点；数据源组删除只阻断仍被对象映射占用的组（40070）。跨源关系类型由一致性巡检 `ONT_REL_CROSS_SOURCE` 出 warning 提示，不阻断建模。
- **多源属性单源**：一个对象类型绑定多个语义模型时，非标识属性只能出现在一个映射里（标识属性可作跨源主键，且每条映射都要覆盖）；跨源重复的非标识属性在保存时拒绝。

## 治理门禁与不变量

- 写方法统一用 `transaction_scope` 编排事务（DAO 经 ContextVar 复用同一会话），删除/状态迁移等多步写整体回滚，不产生半成功数据。
- 破坏性变更（映射变更、主键/标题键变更、关系类型连接对象类型/关联列/基数变更）需 `confirm_destructive=true`。
- certified 冻结：主键组合、标题键、既有属性禁删；certified 对象类型禁止删除（只能 deprecated）。
- 升级 certified 前置：至少一个标识属性；**每一条映射都必须非空且覆盖全部标识属性**（保证认证对象类型在语义层可唯一定位实体）。
- 对象类型编辑守护：删除/改名属性、变更主键组合时，若会让既有关系类型的 `from_columns`/`to_columns` 悬空则直接拒绝。
- 关系类型状态：新建与软删复活一律按两侧对象类型状态取较低者；活跃编辑只降不升（`min(旧状态, 派生状态)`）。手动迁移端点 `POST .../relation-types/{name}/status` 的上限 = 两侧对象类型较低者，deprecated 仅随对象类型级联不可手动迁移。
- 同名活跃对象类型/关系类型/本体再次创建且未带 `allow_overwrite` → 40068；状态违规 → 40069。
- `deprecated_until` 服务层解析为 datetime（接受 `YYYY-MM-DD HH:MM:SS` / `YYYY-MM-DD`），非法值 40067；本体 API 名限 64 字符（落库列宽）。
- 关系类型 `from_columns` 必须引用 from 对象类型已定义属性；`to_columns` 必须引用 to 对象类型标识属性；`one_to_one` 两侧都要求引用标识属性（from 侧以标识属性保证唯一性），多对多走 junction 对象类型。
- 一致性巡检新增关系类型 join 可执行性：同模型两端存在映射但模型 `relationships` 图内无路径 → `ONT_REL_MODEL_PATH_MISSING` error；两端绑定不同模型且同属一个数据源组 → `ONT_REL_CROSS_MODEL` info（单模型 SQL 无法直接 join，待对象层能力补齐；跨组仍由 `ONT_REL_CROSS_SOURCE` warning 覆盖）。

## 消费链路（已实现）

认证/实验态对象类型随语义模型预物化写入 `workspace/semantic/_ontology_catalog.yaml`（只读）：

- **二选一**：本体目录物化成功时不再写 `_semantic_catalog.yaml`；但本体目录顶层 `models` 列出**全部启用模型**（id/name/file/description/business_notes），未建本体对象类型的模型不会丢失索引。本体目录不存在时回落到语义资产目录。
- **关系类型列翻译**：`from_columns`/`to_columns` 为排序后第一个可用映射的尽力翻译；`from_columns_by_model`/`to_columns_by_model` 给出每个绑定模型的物理列；任一级未解析时 `unresolved: true`，模型必须回读模型 YAML 确认后再 join。
- **规模上限**：`object_types` 按对象类型名升序装入 40k 字符预算，超出时 `truncated: true` + `omitted_objects: N`，其余对象类型由 `file_grep` 检索；`models` 不受截断影响。
- **状态过滤**：只注入 `certified`/`experimental` 对象类型与关系类型；`draft`/`deprecated` 不进目录；软删对象类型/关系类型不注入。
- **刷新与失效**：每次 run 启动预物化时重渲染本体目录；语义快照按内容哈希缓存。全部模型被停用/不可见时清空 `semantic/` 下旧目录与快照，避免读到上一轮陈旧目录。
- **开关与降级**：`CHAT_DATA_ONTOLOGY_INJECTION_ENABLED`（默认关闭，见 `server/.env.example`）控制注入；物化失败只告警，不阻塞会话启动。
- **入口授权**：建会话与每轮 completion 绑定数据源组前，复用数据源机构授权口径校验（403 拒绝），避免无权用户把该组语义快照/本体目录物化进自己的工作区。
- 已知限制（后续演进）：目录 40k 字符预算之外的对象类型依赖 `file_grep`；本体注入的授权校验落在会话绑定入口，内部调度调用需自行保证 datasource_id 来源可信。
