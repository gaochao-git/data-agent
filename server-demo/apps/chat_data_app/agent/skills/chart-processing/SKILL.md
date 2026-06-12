---
name: chart-processing
description: 图表生成技能 - 根据 SQL 查询结果生成 ECharts 配置（柱状图、折线图、饼图）
metadata:
  author: 高超
  version: "1.1"
---

# 图表处理技能

将 SQL 查询结果转换为 ECharts 图表配置。

## 何时使用

**激活条件**：用户要求可视化数据、生成图表、展示趋势/对比/占比时

**前置条件**：必须先有 SQL 查询结果（result_id）

## 调用流程（重要）

```
用户说"生成图表"
    ↓
第一步：分析数据
generate_chart_tool(result_id="xxx")  ← 只传 result_id
    ↓
返回字段信息，你来判断：
    ↓
数据符合要求?
  ├─ 不符合 → 告诉用户原因，给出建议 SQL
  └─ 符合 → 第二步：生成图表
              generate_chart_tool(result_id, chart_type, x_field, y_field)
```

**禁止猜测字段名，必须先获取实际字段信息！**

## 数据是否符合要求的判断

根据样例数据判断：
- 是否有可作为 Y 轴的数值字段
- 是否有可作为 X 轴的分类字段

符合要求 → 直接生成图表
不符合 → 简洁告诉用户原因 + 建议 SQL

**不符合时的回复示例**：
> 当前数据是明细列表，没有数值字段。建议按日期统计数量：
> ```sql
> SELECT expiry_date, COUNT(*) as count FROM ... GROUP BY expiry_date
> ```

**禁止逐个字段分析，直接给结论。**

## 图表类型选择

根据数据特征选择：
- 类别对比 → bar
- 时间趋势 → line
- 占比分布 → pie
- 多维对比 → bar + series_field
- 年度同比 → line + series_field

## 数据要求

图表需要的数据特征：
- **有分类字段**：如部门、日期、类别（作为 X 轴）
- **有数值字段**：如数量、金额、占比（作为 Y 轴）
- **数据量适中**：每个分类对应一个数值点

数据来源可以是：
- SQL 聚合查询（GROUP BY + COUNT/SUM）
- 已聚合的数据表（直接 SELECT）
- 汇总报表数据

## 图表类型速查

| 场景 | 类型 | 示例 |
|------|------|------|
| 类别对比（各部门销售额） | bar | x=部门, y=销售额 |
| 时间趋势（每日订单量） | line | x=日期, y=数量 |
| 占比分布（各渠道占比） | pie | x=渠道, y=金额 |
| 多维对比（各部门各月销售） | bar + series_field | x=月份, y=销售额, series=部门 |
| 年度同比（今年vs去年） | line + series_field | x=月-日, y=数量, series=年份 |

---

## 柱状图 (bar)

**适用场景**
- 单柱状图：不同类别之间的数值对比
- 分组柱状图：多维度对比（如各部门各季度销售额）

**参数说明**
- result_id：查询结果 ID
- chart_type：`"bar"`
- x_field：X轴分类字段
- y_field：Y轴数值字段
- series_field（可选）：分组字段，传入则生成分组柱状图

**单柱状图调用**
```
generate_chart_tool(result_id="xxx", chart_type="bar", x_field="department", y_field="sales")
```

**分组柱状图调用**
```
generate_chart_tool(result_id="xxx", chart_type="bar", x_field="month", y_field="sales", series_field="department")
```

**数据示例**

单柱状图数据：
| department | total_sales |
|------------|-------------|
| 销售部 | 150000 |
| 技术部 | 120000 |
| 市场部 | 80000 |

分组柱状图数据：
| month | department | total_sales |
|-------|------------|-------------|
| 2024-01 | 销售部 | 50000 |
| 2024-01 | 技术部 | 40000 |
| 2024-02 | 销售部 | 55000 |
| 2024-02 | 技术部 | 45000 |

---

## 折线图 (line)

**适用场景**
- 单折线：时间序列数据的趋势展示
- 多折线：多类别对比、年度同比对比

**参数说明**
- result_id：查询结果 ID
- chart_type：`"line"`
- x_field：X轴时间/日期字段
- y_field：Y轴数值字段
- series_field（可选）：分组字段，传入则生成多折线

**单折线调用**
```
generate_chart_tool(result_id="xxx", chart_type="line", x_field="date", y_field="count")
```

**多折线调用**
```
generate_chart_tool(result_id="xxx", chart_type="line", x_field="day_month", y_field="cnt", series_field="year")
```

**数据示例**

单折线数据：
| date | cnt |
|------|-----|
| 2024-01-01 | 120 |
| 2024-01-02 | 135 |
| 2024-01-03 | 98 |

多折线数据（年度同比）：
| day_month | cnt | year |
|-----------|-----|------|
| 01-01 | 120 | 2024 |
| 01-01 | 100 | 2023 |
| 01-02 | 135 | 2024 |
| 01-02 | 110 | 2023 |

**重要：多折线同比时，X轴必须去掉年份**
- ❌ 错误：X轴用 `2024-01-01`，两条线错开无法对比
- ✅ 正确：X轴用 `01-01`（月-日），两条线对齐可对比

---

## 饼图 (pie)

**适用场景**
- 各部分占总体的比例展示（如各类别销售占比、各渠道用户占比）
- 强调"部分与整体"的关系

**参数说明**
- result_id：查询结果 ID
- chart_type：`"pie"`
- x_field：分类字段（类别名称）
- y_field：数值字段（必须是正数）

**限制条件**
- 分类数建议 2-10 个，超过 10 个难以区分
- 不支持负数值（负数无法表示占比）
- 至少需要 2 个分类

**调用方式**
```
generate_chart_tool(result_id="xxx", chart_type="pie", x_field="category", y_field="total")
```

**数据示例**
| category | total |
|----------|-------|
| 电子产品 | 45000 |
| 服装 | 32000 |
| 食品 | 18000 |
| 其他 | 5000 |

---

## 常见错误及解决

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| "没有数值类型字段" | 数据中没有数值列 | 确认数据有数值字段，或修改 SQL |
| "饼图分类数过多" | 超过 10 个分类 | 添加 LIMIT 10 或改用 bar |
| "数据是明细记录" | 每行都是独立记录 | 按分类聚合或选择汇总表 |
| "字段不存在" | 字段名写错 | 检查实际字段名或 AS 别名 |
| "Y轴不是数值类型" | 选错了 y_field | 选择数值字段作为 y_field |

## 生成前检查

- [ ] 有 result_id（已执行过查询）
- [ ] 有分类字段（可作为 X 轴）
- [ ] 有数值字段（可作为 Y 轴）
- [ ] 分类数量合理（饼图 ≤10，柱状图 ≤30）
