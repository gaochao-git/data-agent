# 可回导的 Ossie YAML 编写要求

产出供「语义配置 → 导入」回流的模型 YAML 时，必须满足导入器（`chat_data_ossie_importer`）的解析要求。
逐条核对后再写文件；不满足的条目在导入时会变成 errors（阻断）或 warnings（降级推断）。

**产出方式**：把原模型 YAML `file_copy` 到当前会话 `outputs/` 后用 `file_edit` 局部修改；不要从零重写整份文档，未改动的行必须与原文件逐字一致。新增指标/维度/关系只允许追加到对应数组末尾，既有元素顺序与 mapping 键位置不得调整——导入后平台的展示顺序与 YAML 数组顺序一致，顺序漂移会让用户无法核对 diff。

## 文档结构

```yaml
version: "1.0"          # 必须是字符串，不要写成 1.0（会被 YAML 解析成数字导致校验失败）
semantic_model:
  - name: 模型名
    description: 模型短描述
    ai_context: 业务说明/业务知识（对应导入后的 business_notes）
    datasets: [...]      # 维度来源：每个物理表一个 dataset
    metrics: [...]
    relationships: [...]
```

## datasets 与 fields（维度）

- 每个物理表一个 dataset，`name` 为表名；维度写在 dataset 的 `fields` 数组。
- 每个 field 必须有：`name`（维度名）、`expression`（ANSI_SQL 方言表达式）、`datatype`、`dimension.is_time`（时间维度标记）。
- `description`（可选，规范字段）：维度的业务口径描述，导入后写入维度 description（修复 DOC_DIMENSION_NO_DESCRIPTION 就写这里）。键位置保持与导出一致：`dimension` 之后、`datatype` 之前。
- **每个 field 必须带 CHAT_DATA 扩展声明物理归属**，缺省时：单 dataset 模型按唯一 dataset 推断（warning），多 dataset 模型直接阻断（error）：

```yaml
custom_extensions:
  - vendor_name: CHAT_DATA
    data: '{"table": "表名", "column": "物理列名"}'
```

- 表达式是单一物理列时可省略 column（按表达式推断，产生 warning）；建议始终显式声明。

## metrics（指标）

原子指标（无 metric_kind）：

```yaml
- name: 销售额
  description: 口径说明（必写，缺失会触发 DOC 类 finding）
  datatype: DECIMAL
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: SUM(amount)
  custom_extensions:
    - vendor_name: CHAT_DATA
      data: '{"table": "事实表名"}'
```

- 表达式支持任意 SQL（配置页指标管理区展示全部指标，复杂表达式如 CASE WHEN 也可正常回导）。
- CHAT_DATA 扩展必须带 `table`；多 dataset 模型缺 table 直接阻断。

派生指标（对基础指标加时间窗/过滤；**不能带 expression**，口径完全由基础指标 + 周期 + 过滤决定）：

```yaml
- name: 近7日销售额
  description: 最近7天滚动销售额
  datatype: DECIMAL
  expression:
    dialects:
      - dialect: ANSI_SQL
        expression: ""        # 派生指标必须留空，写表达式会被拒绝
  custom_extensions:
    - vendor_name: CHAT_DATA
      data: '{"metric_kind": "derived", "base_metric": "销售额", "period": {"kind": "last_n_days", "n": 7, "time_dimension": "下单时间"}}'
```

扩展字段结构：

- `base_metric`：必填，同模型内已存在的指标名。
- `period`（可选，固定统计周期）：`kind` 取值仅限 `today` / `yesterday` / `last_n_days` / `this_week` / `this_month` / `between`；
  - `last_n_days` 需 `n`（≥1 整数）；`between` 需 `start`/`end`（YYYY-MM-DD）；
  - `time_dimension` 可选，指定周期过滤用的时间维度名。
- `filters`（可选，修饰词过滤，数组）：每项 `{"dimension": "维度名", "operator": "=", "value": "值"}`；
  - `operator` 合法集合：`=` `!=` `<>` `>` `<` `>=` `<=` `IN` `NOT IN` `LIKE` `NOT LIKE` `STARTS WITH` `ENDS WITH` `IS NULL` `IS NOT NULL` `BETWEEN` `EMPTY STRING`（IN/NOT IN/BETWEEN 的 value 为数组）；
  - `dimension` 必须引用同模型内已定义的维度名。
- period 与 filters 都缺省时口径与基础指标重复（warning）。

复合指标（指标间四则运算）：

```yaml
- name: 毛利率
  description: 销售额减成本后与销售额的比值
  datatype: DECIMAL
  custom_extensions:
    - vendor_name: CHAT_DATA
      data: '{"metric_kind": "composite", "formula": "({销售额} - {成本}) / {销售额}"}'
```

- formula 成员用**花括号**引用同模型内已定义的指标名（如 `{销售额}`），数字与 `+ - * /` 运算符可自由组合。
- **禁止**用代入后的 expression 反推复合指标——导入器只认 metric_kind 扩展里的 formula。

## relationships（表关系）

```yaml
- from_dataset: 表A
  to: 表B
  from_columns: [a_id]
  to_columns: [b_id]
  custom_extensions:
    - vendor_name: CHAT_DATA
      data: '{"left_table": "表A", "right_table": "表B", "relationship_type": "one_to_many", "join_type": "left"}'
```

- 扩展里的 `left_table`/`right_table` 声明多端/一端方向；缺省按 from→to 导入并产生 warning。
- `relationship_type` 只支持 `one_to_one` / `one_to_many` / `many_to_many`；`join_type` 支持 `inner` / `left` / `right`（缺省 left）。
- 导出时 one_to_many 会反转 from/to 方向（from=右表），扩展中的左右表才是权威方向，编写新文件时保持 left=多端。

## 自检清单（写文件前逐条确认）

1. `version` 是带引号的字符串。
2. 所有 field/metric 的表达式都是 ANSI_SQL 方言。
3. 多 dataset 模型中每个 field、每个原子指标都带 CHAT_DATA 扩展的 table。
4. 派生指标 base_metric、复合指标 formula 引用的名字都在模型内存在。
5. 关系两端表都在 datasets 中定义，且扩展方向与业务基数一致。
6. 未改动的部分与原文件逐字一致（最小 diff）。
7. 新增指标/维度/关系都在对应数组末尾追加，既有元素顺序与 mapping 键位置未变动。
