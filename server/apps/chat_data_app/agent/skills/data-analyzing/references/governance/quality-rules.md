# 语义质量检查规则清单

本清单与后端机器检查器（`semantic_quality_checker.py`）同一套编码与口径。
导入预览（ossie-import/preview）会对可导入模型执行同样的机器检查并在 `quality` 字段返回报告；
会话内按本清单分析时，结论应与机器检查方向一致。机器检查只输出触发的 findings，
不输出评分/等级（评分无法指导动作），报告以问题清单为准。

## 检查项

### 结构校验 structure

阻断级硬门槛：保存/导入链路的结构校验（`first_structure_error`）从本域提取第一条错误拦截保存；
质量报告里同样呈现。判定逻辑与错误文案单点在 `core/config_validator.py`，由 checker 复用。

| 编码 | 严重度 | 判定 |
|------|--------|------|
| STR_FIELD_NAME_INVALID | error | 字段显示名称为空或重复（message 为完整错误句子，如「字段显示名称重复: X」） |
| STR_DIMENSION_INVALID | error | 维度所属表/字段不存在、allowed_operators 非法（依赖物理列清单，缺清单时跳过） |
| STR_METRIC_INVALID | error | 指标所属表不存在、表达式未锚定物理字段、表达式引用不存在的列（表达式级全量校验） |
| STR_DERIVED_INVALID | error | 派生指标结构不合法（禁配 expression、base_metric 缺失/类型/非聚合） |
| STR_COMPOSITE_INVALID | error | 复合指标结构不合法（禁配 expression、缺 formula、公式语法、成员存在性/类型/同表约束） |
| STR_RELATIONSHIP_INVALID | error | 表关系配置不合法（表/列不存在、别名冲突、缺关联条件、字段缺少关联路径） |

### 来源完整性 provenance

| 编码 | 严重度 | 判定 |
|------|--------|------|
| PROV_METRIC_MISSING_TABLE | error | 原子指标（无 metric_kind）没有归属表 table |
| PROV_DIMENSION_MISSING_TABLE | error | 维度没有归属表 table |
| PROV_DIMENSION_MISSING_COLUMN | warning | 维度有 table 但缺少物理字段 column |

派生/复合指标不落物理表，table 为空是正常形态，不算缺失。

### 可执行性 executability

| 编码 | 严重度 | 判定 |
|------|--------|------|
| EXE_DERIVED_NO_CONSTRAINT | info | 派生指标未设置 period 时间窗或 filters 过滤，口径可能与基础指标重复 |
| EXE_COMPOSITE_UNRESOLVED | warning | formula 中存在无法对应到已知指标名的片段 |

### 覆盖度 coverage

| 编码 | 严重度 | 判定 |
|------|--------|------|
| COV_NO_METRICS | warning | 配置只有维度没有指标，聚合类问题只能退化 SQL 直查（无统一口径沉淀），建议补充常用聚合指标 |
| COV_NO_DIMENSIONS | warning | 配置只有指标没有维度，聚合只能看总量，分组/过滤/明细分析受限，建议补充常用分析维度 |
| COV_DUPLICATE_DIMENSION_COLUMN | warning | 同一物理列被建模为多个维度 |
| COV_UNMODELED_COLUMNS | info | 已建模表中存在未纳入任何维度/指标的物理列（会话内按 YAML 字段与表达式人工核对，机器检查依赖物理列清单） |

覆盖度分析还应口头补充：模型覆盖了哪些表、数据源里是否还有完全未建模的表（无法从单个 YAML 判断时如实说明）。

### 一致性 consistency

| 编码 | 严重度 | 判定 |
|------|--------|------|
| CON_DUPLICATE_METRIC_EXPRESSION | info | 多个原子指标使用相同表达式（重复口径） |
| CON_NAME_DUPLICATE | warning | 指标内 / 维度内存在重名（指标与维度跨命名空间同名不判；对齐 Ossie 唯一名检查） |
| CON_NAME_CHARS | warning | 指标/维度名称含白名单外字符（仅允许中文、英文、数字、下划线）。名称是复合公式 {引用}、base_metric、filters 维度引用的精确查找键，空格/花括号等字符不破坏匹配但让引用难写、显示混乱、YAML 导入类型漂移 |
| CON_RELATIONSHIP_DUPLICATE | warning | 同一对表、同一组关联字段重复定义关系 |
| CON_RELATIONSHIP_SELF | info | 关系左右为同一张表 |
| CON_RELATIONSHIP_TABLE_UNREFERENCED | warning | 关系引用的表没有任何维度或指标建模 |
| CON_RELATIONSHIP_CYCLE | warning | 表关系图存在环（A→B→A 这类循环依赖） |
| CON_RELATIONSHIP_KEY_UNCOVERED | warning | one 端（right_table）的 join 列未覆盖该表在 tables 元数据中声明的 primary/unique key（对齐 Ossie 的 to_columns/声明键覆盖检查；覆盖任一声明键即可，表无声明键或 many_to_many 跳过） |

### 跨模型口径 cross-model（第二层校验）

以下检查的比较域是**数据源组内多个模型之间**（单模型层只保证模型内部自洽；
多模型 @ 引用会把多份模型一起注入会话，同名异式的指标只能靠模型自行裁决，
必须机器提示）。只比较原子指标——派生/复合口径由 base_metric/formula 决定，不可直接比。
check_yaml / 导入预览按模型归因（只报告与当前模型相关的冲突）；数据源组质量
报告输出全量两两冲突。这两条不参与单模型评分。

只判同名指标：异名同表达式不判——COUNT(id)、SUM(xxx) 这类通用表达式跨表
撞车太常见（不同表各自的 id 计数是两个正当指标），判了全是噪音。

| 编码 | 严重度 | 判定 |
|------|--------|------|
| CON_CROSS_MODEL_NAME_CONFLICT | error | 同名指标在多个模型中表达式不同（真口径冲突，同名异义） |
| CON_CROSS_MODEL_METRIC_DUPLICATE | warning | 同名指标在多个模型中表达式完全相同（重复定义，宜合并或引用同一模型） |

### 文档完备性 documentation

| 编码 | 严重度 | 判定 |
|------|--------|------|
| DOC_METRIC_NO_DESCRIPTION | info | 指标缺少 description |
| DOC_DIMENSION_NO_DESCRIPTION | info | 维度缺少 description |
| DOC_NO_BUSINESS_NOTES | info | 模型缺少 ai_context（业务说明） |

## 修复指引

findings 只回答「哪里有问题」。修复前先按三档判断「能不能自己改」：可直接修的按
references/governance/model-template.md 的规则备注在治理目录拷贝件（/mnt/user-data/workspace/governance/）上 file_edit 局部修改（最小 diff）；
标准写法直接照抄同一文件的骨架；
标「问用户」的，口径确认后才落字；「只提示」的止步于报告与建议。

注意：关联字段为空或左右数量不一致、缺 from/to 数据集等属**导入闸门错误**，不会进入质量
报告，出现时按 model-template.md 的规则备注修正后重跑 check_yaml 即可。

### 可直接修（确定性修复）

| 编码 | 修法 |
|------|------|
| DOC_METRIC_NO_DESCRIPTION / DOC_DIMENSION_NO_DESCRIPTION | 补 description：写清统计对象、口径、周期、是否去重/过滤，不写空话 |
| DOC_NO_BUSINESS_NOTES | 补模型 ai_context；内容结构参考 Ossie 规范推荐字段（instructions 使用说明 / synonyms 同义词 / examples 示例问题） |
| PROV_METRIC_MISSING_TABLE / PROV_DIMENSION_MISSING_TABLE | 补 CHAT_DATA 扩展声明归属表；多 dataset 模型禁止省略 |
| PROV_DIMENSION_MISSING_COLUMN | 维度补 column（推荐显式声明，不要依赖单 dataset 推断） |
| STR_FIELD_NAME_INVALID | 空名补名称；重名对象哪个是真口径、如何改名，改名必须同步所有引用方（base_metric、formula 成员、filters 的 dimension） |
| STR_DIMENSION_INVALID / STR_METRIC_INVALID | 物理表/列名是写错还是表结构变了：先查数据源 schema，确认不了再问用户，不猜列名 |
| STR_DERIVED_INVALID | base_metric 改成同模型内已存在的指标名；基础指标本身缺失时先走差距对话，不要硬指 |
| STR_COMPOSITE_INVALID | 补 formula（成员用 {指标名} 花括号引用，禁止用代入后的 SQL 表达式反推）；公式成员改为同表聚合原子指标 |
| STR_RELATIONSHIP_INVALID | 补/修关系行：表名、关联列、别名冲突按数据源 schema 修正；缺关联路径时补一条桥接关系 |
| EXE_COMPOSITE_UNRESOLVED | formula 中未知片段改成已存在的指标名或数字/运算符 |
| CON_RELATIONSHIP_DUPLICATE / CON_RELATIONSHIP_SELF | 删除重复或自环的关系行，保留声明方向完整的一条 |

### 需问用户（业务口径，确认后再改）

| 编码 | 要问什么 |
|------|----------|
| COV_NO_METRICS / COV_NO_DIMENSIONS | 该主题需要哪些聚合口径 / 按什么看数（公式、周期、过滤）；一次只问最关键的一个 |
| EXE_DERIVED_NO_CONSTRAINT | 时间窗 / 过滤的具体口径（确认后补 period 或 filters，改法本身是机械的） |
| CON_NAME_DUPLICATE | 重名对象哪个是真口径、如何改名；改名必须同步所有引用方（base_metric、formula 成员、filters 的 dimension） |
| CON_NAME_CHARS | 改名为仅含中文/英文/数字/下划线的名称；改名必须同步所有引用方（base_metric、formula 成员、filters 的 dimension） |
| CON_DUPLICATE_METRIC_EXPRESSION | 两个同表达式指标保留哪个，保留者的口径描述 |
| COV_DUPLICATE_DIMENSION_COLUMN | 同一物理列保留哪个维度建模（另一个删除或换列） |
| CON_RELATIONSHIP_KEY_UNCOVERED | join 列与声明键不一致：是 join 配错还是键元数据过时；确认后改 join 列，或提示用户在数据源侧更新键信息 |
| CON_CROSS_MODEL_NAME_CONFLICT | 同名指标两个口径哪个是真口径：确认后改表达式，或给其中之一改名（改名必须同步该模型内所有引用方）；禁止两模型口径 silently 漂移 |
| CON_CROSS_MODEL_METRIC_DUPLICATE | 同一指标两个模型都定义：保留哪个模型作为权威来源，另一个删除或改名；确认后落字 |

### 只提示不修（信息项 / 需更大动作）

| 编码 | 说明 |
|------|------|
| COV_UNMODELED_COLUMNS | 未建模列是扩展机会，走差距对话决定是否补建模，不当缺陷硬修 |
| CON_RELATIONSHIP_TABLE_UNREFERENCED | 可能是关系多余，也可能是建模不全；结合业务判断，不擅自删 |
| CON_RELATIONSHIP_CYCLE | 拆环涉及业务关联路径，先在报告里给方案，用户确认后再动手 |

## 报告格式

1. 结论行：`可导入/存在阻断问题，最要紧的问题是 …`
2. 问题清单：按 error → warning → info 排序，每条含编码、严重度、涉及对象、一句话修复建议。
3. 统计：原子/派生/复合指标数、维度数、关系数、有描述的占比。
4. 口头提示：机器检查之外的业务口径疑点（不列入问题清单）。
