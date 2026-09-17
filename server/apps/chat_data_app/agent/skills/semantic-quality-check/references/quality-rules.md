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
| CON_RELATIONSHIP_DUPLICATE | warning | 同一对表、同一组关联字段重复定义关系 |
| CON_RELATIONSHIP_SELF | info | 关系左右为同一张表 |
| CON_RELATIONSHIP_TABLE_UNREFERENCED | warning | 关系引用的表没有任何维度或指标建模 |
| CON_RELATIONSHIP_CYCLE | warning | 表关系图存在环（A→B→A 这类循环依赖） |

### 文档完备性 documentation

| 编码 | 严重度 | 判定 |
|------|--------|------|
| DOC_METRIC_NO_DESCRIPTION | info | 指标缺少 description |
| DOC_DIMENSION_NO_DESCRIPTION | info | 维度缺少 description |
| DOC_NO_BUSINESS_NOTES | info | 模型缺少 ai_context（业务说明） |

## 报告格式

1. 结论行：`可导入/存在阻断问题，最要紧的问题是 …`
2. 问题清单：按 error → warning → info 排序，每条含编码、严重度、涉及对象、一句话修复建议。
3. 统计：原子/派生/复合指标数、维度数、关系数、有描述的占比。
4. 口头提示：机器检查之外的业务口径疑点（不列入问题清单）。
