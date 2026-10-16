# ontology

本体服务层：智能问数的语义资产导航层，对齐 Palantir Foundry 本体（Object / Property / Link Type 三等实体）。

## 职责与边界

- **对象**（`chat_data_ontology_object`）：业务实体类型，API 名 certified 后冻结；状态机 `draft → experimental → certified`，`deprecated` 为终态（附原因/期限/替代对象，不可逆）。
- **属性**（`chat_data_ontology_property`）：一等实体。主键 = `is_identifier` 属性组合（业务主键，禁自增行号），标题键 = `is_title` 属性；值类型为内建 6 种或 `enum`（`enum_ref` 引用全局枚举表）。不存枚举值/单位/聚合口径。
- **关系**（`chat_data_ontology_relation`）：基数仅 `many_to_one`/`one_to_one`（多对多走 junction 对象）；目标侧关联列必须引用对方标识属性（FK→PK 红线）；状态随对象级联（降级自动、升级手动）。

## 语义模型映射

对象 `mappings[]` 统一映射语义模型（层级 = 数据源组 → 语义模型 → 维度映射：
`semantic_config_id` + `column_mappings {属性API名: 模型维度名}`），不触碰物理表：
Palantir 本体物化物理表，我们的物化层是语义模型，本体根基随之对齐——认证对象
必然能在语义层拿到口径，问数链路保持「本体目录 → 语义模型 YAML → NL2SQL → sql_guard」闭环。
本体层只引用模型维度名，不重复定义物理列（Ossie 映射文档同构：映射文档只引用
语义模型，物理表解析是模型文档内部的事）；实体锚点由映射维度的所属表派生，
同一映射的全部维度必须同表（service 层校验）。

「一源一对象」唯一归属检查（Palantir backing 约束）：同一语义模型映射全局只能归属一个对象，
唯一索引兜底 + service 层预检给友好报错。

## 治理门禁

- 破坏性变更（映射变更、主键/标题键变更、关系连接对象/关联列/基数变更）需 `confirm_destructive=true`；
- certified 冻结：主键组合、标题键、既有属性禁删；
- deprecated 对象只读、禁止挂关系；certified 对象禁止删除（只能 deprecated）；
- 升级 certified 前置：至少一个标识属性 + 至少一个映射（映射统一为语义模型轨，
  只指向 semantic_config_id + 维度映射，不触碰物理表）；
- 关系状态手动迁移：`POST .../relations/{name}/status`，目标状态上限 = 两侧对象
  状态的较低者（先升对象再升关系），deprecated 仅随对象级联不可手动迁移。

## 消费链路（后续阶段）

认证对象物化为 `_ontology_catalog.yaml` 供 agent 检索：本体命中 → 语义模型 YAML → NL2SQL → sql_guard；未命中兜底走现有语义目录直查，不新增工具。
