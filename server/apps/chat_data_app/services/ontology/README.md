# ontology

本体服务层：智能问数的语义资产导航层，对齐 Palantir Foundry 本体（Object / Property / Link Type 三等实体）。

## 职责与边界

- **本体**（`chat_data_ontology`）：本体实体（对位 Palantir 一个 ontology），纯语义容器**不绑数据源组**，对齐 Palantir「本体不绑存储」；删除为软删，挂有活跃对象类型时拒绝。2026-10-20 由「领域」更名而来（原 `chat_data_ontology_domain`），同日解耦组绑定（`group_id` 列废弃）。
- **对象类型**（`chat_data_ontology_object_type`）：业务实体类型，API 名全局唯一且创建后不可改；状态机 `draft → experimental → certified`，`deprecated` 为终态（附原因/期限/替代对象类型，不可逆）。软删同名重建视为新建：复活时状态重置为 `draft` 并清空废弃三元数据。
- **属性**（`chat_data_ontology_property`）：一等实体。主键 = `is_identifier` 属性组合（业务主键，禁自增行号），标题键 = `is_title` 属性；值类型为内建 6 种或 `enum`（`enum_ref` 引用全局枚举表）。不存枚举值/单位/聚合口径。
- **关系类型**（`chat_data_ontology_relation_type`）：基数仅 `many_to_one`/`one_to_one`（多对多走 junction 对象类型）；目标侧关联列必须引用对方标识属性（FK→PK 红线）；状态随对象类型级联（降级自动、升级手动）。
- **Action Type**（`chat_data_ontology_action_type`）：参数 + `set_property` 编辑契约，experimental/certified 可执行（写属性覆盖层 + 审计，不改源库）。

## 数据源绑定（Logical Dataset source binding，2026-10-23）

对象类型取数统一走 `chat_data_ontology_source_binding` → **Logical Dataset**，不再持有语义模型映射
（`chat_data_ontology_mapping` 已下线）：

- 绑定 = `dataset_id` + `column_mappings {属性API名: Dataset 逻辑列名}`，物理表/物理列由
  `chat_data_dataset_binding.physical_table` 与 Dataset `schema_json` 的
  `physical_column` 决定；
- 一个对象类型绑定一个 Dataset；Dataset 的 member binding 按 `datasource_group_id` 区分
  业务域，同一 Dataset 可在多个数据源组各有一条 member binding；
- 对象身份 = `member_datasource_id:object_type:local_key`（不同 member 同主键不是同一对象）；
- certified 前置：至少一个标识属性，且 source binding 的 `column_mappings` 必须覆盖全部
  标识属性（保证认证对象类型可唯一定位实体）；列名必须存在于 Dataset schema。
- **一源一对象类型**：同一 Dataset 只能被一个活跃对象类型绑定（对齐 Palantir
  「A single datasource can only be used to back one object type」）；`set_source_binding`
  先查占用给友好报错，避免同一张表被解释成两种对象、对象身份语义发散。
- 本体层只引用 Dataset 逻辑列，不重复定义物理列；语义层与本体层共用 Dataset，但二者之间
  不互相依赖。

## 查询能力路由（语义 / 本体二选一，2026-10-23）

**唯一选择入口**：`query_mode.py`（`resolve_query_mode_async` / `current_query_mode`）。
提示词、工具注册、目录物化、上下文预算全部读取它的结论，不允许各消费方自行探测目录或
写第二份选择逻辑。

- **判断范围 = 当前数据源组**（会话绑定的 `datasource_id`）。
- **有本体**：该数据源组内至少存在一个 `certified`/`experimental` 活跃对象类型，且对象类型
  已配置 source binding、其 Dataset 在该组下有 member binding（对象查询可用）。
- **无本体**：对象类型为空、没有 source binding、只绑其它数据源组、或探测失败 → 保持原语义链路。
- 部署开关 `CHAT_DATA_ONTOLOGY_INJECTION_ENABLED`（默认关闭）关闭时一律语义模式；开启后按
  数据源组二选一。判定结果按数据源组做短 TTL 缓存（5s），管理端改动最迟 TTL 后生效。
- 模式发布经 ContextVar（一次 run 有效），由 `prematerialize_enabled_semantic_models` 在
  agent 装配前发布；未发布（历史回放/直连装配）按语义模式处理。

消费口径：

| 模块 | 有本体 | 无本体 |
|---|---|---|
| 目录物化 | 只写 `workspace/semantic/_ontology_catalog.yaml`，清空语义模型快照 | 只写 `_semantic_catalog.yaml` + 模型 YAML，清空本体目录 |
| 工具注册 | 只暴露 `ontology_tool`（对象/关系/Action） | 只暴露 `data_analyzing_tool`（语义查询） |
| 系统提示词 | `ONTOLOGY_WORKFLOW_BLOCK` + 本体环境/技能/错误处理段 | 原语义提示词与 `data-analyzing` 技能链路 |
| 回退 | **禁止**回退语义查询/DSL/SQL | 不出现本体目录与本体工具 |

## 指标关联（对象类型 ↔ 语义层）

对象类型不承载指标口径；指标定义唯一权威在语义层。二者的关联走 Logical Dataset 的
`chat_data_dataset_semantic_link`（引用 + 列对照，见 `services/dataset/README.md`）：

- 解析链路：对象类型 source_binding（属性→Dataset 逻辑列）→ Dataset 关联（逻辑列→语义维度）
  → `SemanticConfig.metrics`；服务实现 `services/ontology/object_metrics.py`；
- 目录：`_ontology_catalog.yaml` 的 `object_types[].metrics` 输出只读指标索引
  （名称/kind/描述/executable_in_scope/unavailable_reason），不复制表达式；
- 工具：`ontology_tool` 新增 `list_object_metrics`（只读）与
  `aggregate_objects.metric_names`（按命名指标聚合，query_mode=`ontology_metric_aggregate`）；
- 约束：指标与对象 Dataset 必须同表（MVP）；关联未 active、指标表不一致、维度未映射时
  `executable_in_scope=false`，执行时明确报错，**不降级为属性 ad-hoc 聚合**。

## 治理门禁与不变量

- 写方法统一用 `transaction_scope` 编排事务（DAO 经 ContextVar 复用同一会话），删除/状态迁移等多步写整体回滚，不产生半成功数据。
- 破坏性变更（source binding 变更、主键/标题键变更、关系类型连接对象类型/关联列/基数变更）需 `confirm_destructive=true`。
- certified 冻结：主键组合、标题键、既有属性禁删；certified 对象类型禁止删除（只能 deprecated）。
- 升级 certified 前置：至少一个标识属性；**source binding 必须非空且覆盖全部标识属性**。
- 对象类型编辑守护：删除/改名属性、变更主键组合时，若会让既有关系类型的 `from_columns`/`to_columns` 悬空则直接拒绝。
- 关系类型状态：新建与软删复活一律按两侧对象类型状态取较低者；活跃编辑只降不升（`min(旧状态, 派生状态)`）。手动迁移端点 `POST .../relation-types/{name}/status` 的上限 = 两侧对象类型较低者，deprecated 仅随对象类型级联不可手动迁移。
- 同名活跃对象类型/关系类型/本体再次创建且未带 `allow_overwrite` → 40073；状态违规 → 40074。
- `deprecated_until` 服务层解析为 datetime（接受 `YYYY-MM-DD HH:MM:SS` / `YYYY-MM-DD`），非法值 40072；本体 API 名限 64 字符（落库列宽）。
- 关系类型 `from_columns` 必须引用 from 对象类型已定义属性；`to_columns` 必须引用 to 对象类型标识属性；`one_to_one` 两侧都要求引用标识属性（from 侧以标识属性保证唯一性），多对多走 junction 对象类型。
- 一致性巡检覆盖关系类型 join 可执行性（`ONT_REL_MODEL_PATH_MISSING` / `ONT_REL_CROSS_MODEL` / `ONT_REL_CROSS_SOURCE`），详见 `ontology_consistency.py`。

## Action Type 定义层（2026-10-22）

对齐 Palantir Action Type 的「参数 + 编辑契约」，当前已支持定义、参数试运行与最小执行：

- `chat_data_ontology_action_type`：动作名、目标对象类型、参数定义 JSON、编辑定义 JSON、生命周期；
- 参数：`{name, type, required, description}`，类型限内建值类型；
- 编辑：当前仅 `set_property`，值可引用参数或静态值；标识属性禁改；目标属性必须存在；
- 接口：`GET/PUT/DELETE /admin/meta/ontology/action-types...`、`POST .../test-run`；
- 试运行只校验参数并解析编辑，`executed=false`；
- `POST .../action-types/{name}/execute`：experimental/certified Action 执行，写对象属性覆盖层
  `chat_data_ontology_object_edit` + 执行审计 `chat_data_ontology_action_execution`；
- 对象读取在 `CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED=true` 时合并最新覆盖值；
- agent 侧对应 `ontology_tool` 的 `list_action_types`（只读清单）与 `execute_action`（受同一开关门控）；
- **不直接改源库/语义模型数据**；回滚、撤销、副作用/Webhook、Action UI 尚未实现。

DDL 见 `next_version.md`「本体 Action Type 定义表」。

## 对象实例只读投影（MVP，2026-10-22）

在对象存储之前先提供“数据库查询 → 代码组装对象”的只读投影，验证对象契约：

- 接口：`GET /admin/meta/ontology/object-types/{object_type_name}/objects/read?object_key=...`；
- 数据来源：对象类型 source binding 的 Dataset 逻辑列 → 物理表/列（`member_datasource_ids` 定位 member）；
- 约束：单对象类型、单 source binding、单标识属性，属性必须同表；对象一跳 links（外键型，单关系最多 20 条）；
- 返回复合 `object_key = member_datasource_id:object_type:local_key`；
- Link 对齐 Palantir：关系类型双向，当前对象既可作 from 也可作 to；只支持 foreign key → primary key 型，many-to-many/join table/backing object 暂不支持；
- 安全：表/列只来自 source binding，值参数化转义，必须复用数据源组成员授权；不执行任意 SQL；
- 不落对象存储、不做写入；后续复合主键、多列关联、对象存储/图数据库只替换 `object_projection.py` 的实现。

## 消费链路（已实现）

认证/实验态对象类型随 run 启动预物化写入 `workspace/semantic/_ontology_catalog.yaml`（只读），
与语义资产目录严格二选一（选择逻辑见 `query_mode.py`）：

- **目录内容**：`object_types`（属性 + `source_binding`：Dataset/物理表/member/属性→逻辑列→物理列）、
  `relation_types`（物理连接列，未解析标 `unresolved`）、`action_types`（experimental/certified 定义清单）。
- **关系类型列翻译**：`from_columns`/`to_columns` 经两端对象类型 source binding 翻译成物理列；
  任一级未解析时 `unresolved: true`，模型必须回读对应 `source_binding` 确认后再 join。
- **规模上限**：`object_types` 按对象类型名升序装入 40k 字符预算，超出时 `truncated: true` +
  `omitted_objects: N`，其余对象类型由 `file_grep` 检索。
- **状态过滤**：只注入 `certified`/`experimental` 对象类型、关系类型与 Action；`draft`/`deprecated`
  不进目录；软删对象类型/关系类型不注入；域过滤按数据源组（Dataset member binding）。
- **刷新与失效**：每次 run 启动预物化时重渲染目录；领域 bundle 短 TTL 缓存；模式切换时清理
  另一模式的目录与快照，避免读到上一轮陈旧文件。
- **开关与降级**：`CHAT_DATA_ONTOLOGY_INJECTION_ENABLED`（默认关闭）控制本体链路；目录物化失败
  只告警，不阻塞会话启动；本体模式下语义模型目录不注入。
- **工具入口**：`agent/tools/ontology_tool.py` 的 `ontology_tool`（对象查询/关系导航/Action），
  受 `CHAT_DATA_ONTOLOGY_LOCATE_ENABLED` / `CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED` /
  `CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED` 分别门控导航/对象读取/Action 执行。
- **入口授权**：建会话与每轮 completion 绑定数据源组前，复用数据源机构授权口径校验（403 拒绝），
  避免无权用户把该组本体目录物化进自己的工作区。
- 已知限制（后续演进）：目录 40k 字符预算之外的对象类型依赖 `file_grep`；对象查询 MVP 一次
  只支持单 member；对象存储/图查询尚未落地。
