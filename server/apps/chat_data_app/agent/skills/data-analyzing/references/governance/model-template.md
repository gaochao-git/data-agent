# 标准参考模板（骨架 + 可回导规则）

写或改模型 YAML、输出质量报告时，直接以本模板为骨架填空，不要自行发挥结构。
键顺序按本模板（与平台导出一致），保证「导出 → 修改 → 导入」往返 diff 最小。
只保留用到的对象与字段；`#` 注释是说明，落文件时删除。

**产出方式**：先 `dir_create` 统一治理目录 `/mnt/user-data/workspace/governance/`，把原模型 YAML `file_copy` 进该目录，在治理拷贝件上用 `file_edit` 局部修改；自检通过后再把文件复制到 `/mnt/user-data/outputs/governance/`（`file_copy` 只支持单文件需逐个复制）。不要从零重写整份文档，未改动的行必须与原文件逐字一致。新增指标/维度/关系只允许追加到对应数组末尾，既有元素顺序与 mapping 键位置不得调整——导入后平台的展示顺序与 YAML 数组顺序一致，顺序漂移会让用户无法核对 diff。

## 一、模型 YAML 骨架

```yaml
version: "0.2.0.dev0"            # 必须是带引号字符串；规范钉死此值（模型字段平铺在根级，一文档一模型）
name: 用户-订单模型
description: 模型一句话短描述
ai_context: 业务说明          # 字符串；内容多时用下方结构化写法
# ai_context:               # 结构化写法（Ossie 规范推荐字段，补 DOC_NO_BUSINESS_NOTES 用这个）
#   instructions: 这个模型用于什么分析、怎么用
#   synonyms: [别名1, 别名2]
#   examples: [示例问题1, 示例问题2]
datasets:                    # 维度来源：每个物理表一个 dataset
  - name: orders
    source: orders
    primary_key: [id]        # 可选，声明键；无则省略整行（官方示例与平台导出都不输出空键）
    unique_keys:
      - [order_no]           # 可选，每条一个唯一键；无则整个 unique_keys 也省略
    fields:
      - name: 订单编号
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: '`order_id`'
        dimension:
          is_time: false
        description: 订单唯一标识，一行代表一笔订单   # 可选；补 DOC_DIMENSION_NO_DESCRIPTION 写这里
        datatype: String
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"table": "orders", "column": "order_id", "column_dict": {}}'   # 物理归属必带；column_dict 每列留位，无信息写空对象
      - name: 下单时间        # 时间维度范例
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: '`order_time`'
        dimension:
          is_time: true
        datatype: DateTime
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"table": "orders", "column": "order_time", "column_dict": {}}'
      - name: 订单状态         # 枚举维度范例：枚举口径以结构化 column_dict 随 YAML 携带，不抄进 description
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: '`status`'
        description: 订单状态   # 只写业务概述；枚举清单不抄进描述，权威在 column_dict/字典表
        datatype: String
        custom_extensions:
          - vendor_name: CHAT_DATA
            # data 是 JSON 字符串：table.column 是字典绑定键；column_dict 内容结构化随 YAML 携带，
            # 导入时按「仅填空」回填字典表（已有值保留）。value 必须真实入库值，拿不准先抽样确认。
            data: '{"table": "orders", "column": "status", "logical_type": "enum", "column_dict": {"enum_values": [{"value": "0", "label": "待支付"}, {"value": "1", "label": "已支付"}, {"value": "2", "label": "已取消"}], "unit": "", "storage_format": ""}}'
relationships:               # 段序对齐官方/平台导出：datasets → relationships → metrics
  - name: orders__users__1   # 官方必填；平台导出格式 {from}__{to}__{序号}
    from: orders             # 官方字段名，=many 侧（一对多时指向一端）；写别名 from_dataset: 导入也接受
    to: users                # =一端
    from_columns: [user_id]  # 与 to_columns 一一对应、数量相等（导入闸门）
    to_columns: [id]         # 应覆盖 to 表声明的键（KEY_UNCOVERED 检查）
    custom_extensions:
      - vendor_name: CHAT_DATA
        # 自关联（from 与 to 同表）必须在 data 里加 "left_alias"/"right_alias" 区分两侧角色
        # （仅在与表名不同时写，如 '{"left_table": "employees", "right_table": "employees", "relationship_type": "many_to_one", "join_type": "left", "right_alias": "mgr"}'），
        # 否则查询层不会生成自连接，依赖它的维度会读到本表字段。
        data: '{"left_table": "orders", "right_table": "users", "relationship_type": "one_to_many", "join_type": "left"}'
metrics:
  - name: 销售额             # 原子指标范例
    description: 已支付订单金额合计   # 必写（DOC 类 finding）
    datatype: Decimal
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: SUM(amount)
    custom_extensions:
      - vendor_name: CHAT_DATA
        # 原子指标可带单位；来源列由表达式解析（SUM(amount) → amount），拿不准物理列就别带 column_dict
        data: '{"table": "orders", "column_dict": {"enum_values": [], "storage_format": "", "unit": "元"}}'
  - name: 近7日销售额         # 派生指标范例：expression 必须留空字符串
    description: 最近7天滚动销售额
    datatype: Decimal
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: ""
    custom_extensions:
      - vendor_name: CHAT_DATA
        data: '{"metric_kind": "derived", "base_metric": "销售额", "period": {"kind": "last_n_days", "n": 7, "time_dimension": "下单时间"}}'
  - name: 客单价             # 复合指标范例
    description: 平均单笔成交金额
    datatype: Decimal
    expression:              # 键必须存在（官方规范必填）；值留空，口径权威在下方扩展 formula
      dialects:
        - dialect: ANSI_SQL
          expression: ""
    custom_extensions:
      - vendor_name: CHAT_DATA
        data: '{"metric_kind": "composite", "formula": "({销售额} / {订单数})"}'
```

## 二、可回导规则备注（写文件前逐条核对）

不满足的条目在导入时会变成 errors（阻断）或 warnings（降级推断）。

### 维度（dataset fields）

- 每个 field 必须带 CHAT_DATA 扩展声明物理归属；缺省时单 dataset 模型按唯一 dataset 推断（warning），多 dataset 模型直接阻断（error）。
- 表达式是单一物理列时可省略扩展的 `column`（按表达式推断，产生 warning）；建议始终显式声明。
- `description` 键位置保持与导出一致：`dimension` 之后、`datatype` 之前。

### 字段数据字典（column_dict）

- 每个字段的 CHAT_DATA 扩展 `data` JSON 都带 `column_dict` 键（与 `table`/`column` 同层，**这是 Ossie 规范里唯一能开的扩展槽**——扩展条目本身只允许 vendor_name + data 两个属性，平级新开键会挂官方 schema 校验）。字典内容**结构化随 YAML 携带**，大模型与代码都可直接解析；导入时按「仅填空」回填数据字典表（已有值一律保留），运行时展示由平台按 `表.列` 绑定键从字典注入。没有字典信息就写空对象 `{}`；编辑既有文件时只给新增/修改的字段写，未动字段保持逐字一致（最小 diff）。
- 三项口径：`enum_values` 的 value 必须是**真实入库值**（如 `0`、`1`、`RUNNING`）、label 是业务含义，拿不准就少量抽样确认禁止编造；`unit` 是数值单位（元、万元、秒），没有就留空串；`storage_format` 一律留空、不猜（字段类型 ≠ 真实存储格式）。样例见「订单状态」「销售额」范例。
- **枚举维度**必须带 `logical_type: enum` 扩展，且 `column_dict.enum_values` 不可缺——随 YAML 携带的枚举值会计入导入校验，首次导入即可自足通过；**不要**靠去掉 enum 标记绕过——那会让维度失去枚举过滤能力。
- 派生/复合指标无物理列，不携带 `column_dict`；原子指标不确定来源列时也不带。
- `description` 只写业务概述，**不抄**枚举清单或单位——字典是唯一权威，抄进描述必然漂移（字典改了 YAML 就是旧口径）。

### 指标

- 原子指标表达式支持任意 SQL（配置页指标管理区展示全部指标，复杂表达式如 CASE WHEN 也可正常回导）；CHAT_DATA 扩展必须带 `table`，多 dataset 模型缺 table 直接阻断。
- 派生指标（对基础指标加时间窗/过滤）**不能写表达式内容**，口径完全由基础指标 + 周期 + 过滤决定：expression 键必须存在（官方规范必填）但内层 expression 留空字符串，写了内容会被拒绝：
  - `base_metric` 必填，同模型内已存在的指标名；
  - `period`（可选，固定统计周期）：`kind` 仅限 `today` / `yesterday` / `last_n_days` / `this_week` / `this_month` / `between`；`last_n_days` 需 `n`（≥1 整数）；`between` 需 `start`/`end`（YYYY-MM-DD）；`time_dimension` 可选，指定周期过滤用的时间维度名；
  - `filters`（可选，修饰词过滤，数组）：每项 `{"dimension": "维度名", "operator": "=", "value": "值"}`；`operator` 合法集合：`=` `!=` `<>` `>` `<` `>=` `<=` `IN` `NOT IN` `LIKE` `NOT LIKE` `STARTS WITH` `ENDS WITH` `IS NULL` `IS NOT NULL` `BETWEEN` `EMPTY STRING`（IN/NOT IN/BETWEEN 的 value 为数组）；`dimension` 必须引用同模型内已定义的维度名；
  - period 与 filters 都缺省时口径与基础指标重复（warning，对应 EXE_DERIVED_NO_CONSTRAINT）。
- 复合指标 formula 成员用**花括号**引用同模型内已定义的指标名（如 `{销售额}`），数字与 `+ - * /` 运算符可自由组合；expression 键必须存在（官方规范必填）但内层 expression 留空字符串；**禁止**用代入后的 expression 反推复合指标——导入器只认 metric_kind 扩展里的 formula。

### 表关系

- 扩展里的 `left_table`/`right_table` 声明多端/一端方向（权威方向）；缺省按 from→to 导入并产生 warning。
- `relationship_type` 只支持 `one_to_one` / `one_to_many` / `many_to_many`；`join_type` 支持 `inner` / `left` / `right`（缺省 left）。
- 导出时 one_to_many 会反转 from/to 方向（from=右表），编写新文件时保持 left=多端。

## 三、自检清单（写文件前逐条确认）

1. `version` 是带引号的字符串。
2. 所有 field/metric 的表达式都是 ANSI_SQL 方言。
3. 多 dataset 模型中每个 field、每个原子指标都带 CHAT_DATA 扩展的 table。
4. 派生指标 base_metric、复合指标 formula 引用的名字都在模型内存在。
5. 关系两端表都在 datasets 中定义，且扩展方向与业务基数一致。
6. 未改动的部分与原文件逐字一致（最小 diff）。
7. 新增指标/维度/关系都在对应数组末尾追加，既有元素顺序与 mapping 键位置未变动。
8. 枚举维度：字段带 `logical_type: enum` 扩展与含 `enum_values` 的 `column_dict`，description 只写业务概述未复制枚举清单。

## 四、质量报告输出模板

```text
结论：<可导入 / 存在阻断问题>，最要紧的问题是 <1~3 条>。

问题清单（error → warning → info）：
  <编码> <严重度> <涉及对象>：<一句话修复建议（按 quality-rules.md 修复指引分档）>
  …（同类多条聚合为一行「等 N 处」）

统计：原子指标 N / 派生 N / 复合 N / 维度 N / 关系 N；描述覆盖 M/N。

口头提示（不列入清单）：<业务口径疑点>

自检结果：<file_diff 增删行数与实际改动一致 / check_yaml 是否通过、遗留 warning>
```
