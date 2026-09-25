# 语义质量检查规则清单

本清单与后端机器检查器（`semantic_quality_checker.py`）同一套编码与口径。
导入预览（ossie-import/preview）会对可导入模型执行同样的机器检查并在 `quality` 字段返回报告；
会话内按本清单分析时，结论应与机器检查方向一致。机器检查只输出触发的 findings，
不输出评分/等级（评分无法指导动作），报告以问题清单为准。

## 检查项

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
| EXE_UNKNOWN_COLUMN | error | 维度/指标引用的物理列在数据源中不存在（会话内无法核对物理清单时不判此项） |
| EXE_DERIVED_BASE_MISSING | error | 派生指标的 base_metric 在当前配置中不存在 |
| EXE_DERIVED_NO_CONSTRAINT | info | 派生指标未设置 period 时间窗或 filters 过滤，口径可能与基础指标重复 |
| EXE_COMPOSITE_FORMULA_MISSING | error | 复合指标缺少 formula |
| EXE_COMPOSITE_UNRESOLVED | warning | formula 中存在无法对应到已知指标名的片段 |

### 覆盖度 coverage

| 编码 | 严重度 | 判定 |
|------|--------|------|
| COV_NO_METRICS | warning | 配置只有维度没有指标，无法回答聚合类问题 |
| COV_NO_DIMENSIONS | warning | 配置只有指标没有维度，无法分组或过滤 |
| COV_DUPLICATE_DIMENSION_COLUMN | warning | 同一物理列被建模为多个维度 |
| COV_UNMODELED_COLUMNS | info | 已建模表中存在未纳入任何维度/指标的物理列（会话内按 YAML 字段与表达式人工核对，机器检查依赖物理列清单） |

覆盖度分析还应口头补充：模型覆盖了哪些表、数据源里是否还有完全未建模的表（无法从单个 YAML 判断时如实说明）。

### 一致性 consistency

| 编码 | 严重度 | 判定 |
|------|--------|------|
| CON_DUPLICATE_METRIC_EXPRESSION | info | 多个原子指标使用相同表达式（重复口径） |
| CON_NAME_DUPLICATE | warning | 指标内 / 维度内存在重名（指标与维度跨命名空间同名不判；对齐 Ossie 唯一名检查） |
| CON_RELATIONSHIP_DUPLICATE | warning | 同一对表、同一组关联字段重复定义关系 |
| CON_RELATIONSHIP_SELF | info | 关系左右为同一张表 |
| CON_RELATIONSHIP_TABLE_UNREFERENCED | warning | 关系引用的表没有任何维度或指标建模 |
| CON_RELATIONSHIP_CYCLE | warning | 表关系图存在环（A→B→A 这类循环依赖） |
| CON_RELATIONSHIP_KEY_UNCOVERED | warning | one 端（right_table）的 join 列未覆盖该表在 tables 元数据中声明的 primary/unique key（对齐 Ossie 的 to_columns/声明键覆盖检查；覆盖任一声明键即可，表无声明键或 many_to_many 跳过） |

### 文档完备性 documentation

| 编码 | 严重度 | 判定 |
|------|--------|------|
| DOC_METRIC_NO_DESCRIPTION | info | 指标缺少 description |
| DOC_DIMENSION_NO_DESCRIPTION | info | 维度缺少 description |
| DOC_NO_BUSINESS_NOTES | info | 模型缺少 ai_context（业务说明） |

## 修复指引

findings 只回答「哪里有问题」。修复前先按三档判断「能不能自己改」：可直接修的按
references/governance/model-template.md 的规则备注在拷贝件上 file_edit 局部修改（最小 diff）；
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
| EXE_DERIVED_BASE_MISSING | base_metric 改成同模型内已存在的指标名；基础指标本身缺失时先走差距对话，不要硬指 |
| EXE_COMPOSITE_FORMULA_MISSING | 补 formula，成员用 {指标名} 花括号引用；禁止用代入后的 SQL 表达式反推 |
| EXE_COMPOSITE_UNRESOLVED | formula 中未知片段改成已存在的指标名或数字/运算符 |
| CON_RELATIONSHIP_DUPLICATE / CON_RELATIONSHIP_SELF | 删除重复或自环的关系行，保留声明方向完整的一条 |

### 需问用户（业务口径，确认后再改）

| 编码 | 要问什么 |
|------|----------|
| COV_NO_METRICS / COV_NO_DIMENSIONS | 该主题需要哪些聚合口径 / 按什么看数（公式、周期、过滤）；一次只问最关键的一个 |
| EXE_UNKNOWN_COLUMN | 物理列名是写错还是表结构变了：先查数据源 schema，确认不了再问用户，不猜列名 |
| EXE_DERIVED_NO_CONSTRAINT | 时间窗 / 过滤的具体口径（确认后补 period 或 filters，改法本身是机械的） |
| CON_NAME_DUPLICATE | 重名对象哪个是真口径、如何改名；改名必须同步所有引用方（base_metric、formula 成员、filters 的 dimension） |
| CON_DUPLICATE_METRIC_EXPRESSION | 两个同表达式指标保留哪个，保留者的口径描述 |
| COV_DUPLICATE_DIMENSION_COLUMN | 同一物理列保留哪个维度建模（另一个删除或换列） |
| CON_RELATIONSHIP_KEY_UNCOVERED | join 列与声明键不一致：是 join 配错还是键元数据过时；确认后改 join 列，或提示用户在数据源侧更新键信息 |

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
