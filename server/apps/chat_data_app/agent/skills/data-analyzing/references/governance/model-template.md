# 标准参考模板

写或改模型 YAML、输出质量报告时，直接以本模板为骨架填空，不要自行发挥结构。
键顺序按本模板（与平台导出一致），保证「导出 → 修改 → 导入」往返 diff 最小。
只保留用到的对象与字段；`#←` 注释是说明，落文件时删除。

## 一、模型 YAML 骨架

```yaml
version: "0.2.0.dev0"            # 必须是带引号字符串
semantic_model:
  - name: 用户-订单模型
    description: 模型一句话短描述
    ai_context: 业务说明          # 字符串；内容多时用下方结构化写法
    # ai_context:               # 结构化写法（Ossie 规范推荐字段，补 DOC_NO_BUSINESS_NOTES 用这个）
    #   instructions: 这个模型用于什么分析、怎么用
    #   synonyms: [别名1, 别名2]
    #   examples: [示例问题1, 示例问题2]
    datasets:                    # 维度来源：每个物理表一个 dataset
      - name: orders
        source: orders
        primary_key: [id]        # 可选，声明键；无则留 []
        unique_keys:
          - [order_no]           # 可选，每条一个唯一键
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
                data: '{"table": "orders", "column": "order_id"}'   # 物理归属必带
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
                data: '{"table": "orders", "column": "order_time"}'
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
            data: '{"table": "orders"}'
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
      - name: 客单价             # 复合指标范例：无 expression 键
        description: 平均单笔成交金额
        datatype: Decimal
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"metric_kind": "composite", "formula": "({销售额} / {订单数})"}'
    relationships:
      - from_dataset: orders
        to: users
        from_columns: [user_id]  # 与 to_columns 一一对应、数量相等（导入闸门）
        to_columns: [id]         # 应覆盖 to 表声明的键（KEY_UNCOVERED 检查）
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"left_table": "orders", "right_table": "users", "relationship_type": "one_to_many", "join_type": "left"}'
```

## 二、质量报告输出模板

```text
结论：<可导入 / 存在阻断问题>，最要紧的问题是 <1~3 条>。

问题清单（error → warning → info）：
  <编码> <严重度> <涉及对象>：<一句话修复建议（按 quality-rules.md 修复指引分档）>
  …（同类多条聚合为一行「等 N 处」）

统计：原子指标 N / 派生 N / 复合 N / 维度 N / 关系 N；描述覆盖 M/N。

口头提示（不列入清单）：<业务口径疑点>

自检结果：<file_diff 增删行数与实际改动一致 / check_yaml 是否通过、遗留 warning>
```
