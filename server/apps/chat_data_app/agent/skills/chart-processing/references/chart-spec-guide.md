# Chart Spec 指南

## 目标

`chart_spec` 是前端图表渲染的中间协议。它屏蔽 SQL 数量和数据处理复杂度，让前端只根据稳定字段渲染图表。

模型不能直接把复杂分析结果硬写成最终数据。需要拼接、对齐、补齐和计算时，必须用 Python 脚本生成 `chart_spec`。

## 最小结构

```json
{
  "version": "1.0",
  "title": "月度销售额趋势",
  "chart_type": "line",
  "data": [
    {"month": "2026-01", "sales": 1200},
    {"month": "2026-02", "sales": 1500}
  ],
  "encoding": {
    "x": "month",
    "y": ["sales"]
  },
  "series": [
    {"field": "sales", "label": "销售额", "format": "number"}
  ],
  "sources": [
    {"name": "main", "sql": "select ..."}
  ],
  "meta": {
    "missing_policy": "none",
    "generated_by": "chart-processing"
  }
}
```

## 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `version` | 是 | 当前固定为 `"1.0"` |
| `title` | 是 | 图表标题，简短描述口径 |
| `chart_type` | 是 | `table`、`kpi`、`bar`、`horizontal_bar`、`line`、`area`、`pie`、`map`、`combo`、`funnel`、`graph` |
| `data` | 是 | 已整理好的对象数组 |
| `encoding.x` | 否 | X 轴字段，表格和指标卡可省略 |
| `encoding.y` | 否 | Y 轴字段数组 |
| `encoding.series` | 否 | 系列字段，适合长表多系列 |
| `series` | 否 | 指标字段展示名、格式和轴配置 |
| `sources` | 否 | 查询来源，用于追溯 |
| `meta` | 否 | 缺失值策略、时间范围、计算说明等 |

## 数据形态

### 宽表多系列

适合本期 / 上期、两个指标并列：

```json
{
  "chart_type": "line",
  "data": [
    {"period": "2026-01", "current": 120, "previous": 100, "change_rate": 0.2}
  ],
  "encoding": {"x": "period", "y": ["current", "previous"]}
}
```

### 长表多系列

适合动态系列数量：

```json
{
  "chart_type": "line",
  "data": [
    {"period": "2026-01", "series": "华东", "value": 120},
    {"period": "2026-01", "series": "华南", "value": 90}
  ],
  "encoding": {"x": "period", "y": ["value"], "series": "series"}
}
```

优先使用宽表。只有系列数量动态、字段无法提前确定时，才使用长表。

### 柱线组合

`combo` 只用于同一维度下两个量纲指标组合展示。第一个指标画柱，第二个指标画线，不要塞入 3 个及以上指标。

```json
{
  "chart_type": "combo",
  "data": [
    {"period": "2026-01", "orders": 120, "sales": 1500}
  ],
  "encoding": {"x": "period", "y": ["orders", "sales"]}
}
```

### 同比/环比标准形态

同比/环比图必须按“同期位置”对齐，不能把两个年份或两个周期直接拼接成连续 X 轴。

月同比推荐使用宽表多系列：

```json
{
  "chart_type": "line",
  "data": [
    {
      "month_label": "1月",
      "month_no": "01",
      "period": "2024-01",
      "compare_period": "2025-01",
      "sales_2024": 71,
      "sales_2025": 87,
      "diff": 16,
      "change_rate": 0.225
    }
  ],
  "encoding": {"x": "month_label", "y": ["sales_2024", "sales_2025"]},
  "series": [
    {"field": "sales_2024", "label": "2024年销量", "format": "number"},
    {"field": "sales_2025", "label": "2025年销量", "format": "number"},
    {"field": "change_rate", "label": "同比增长率", "format": "percent"}
  ]
}
```

规则：

- `month_label` 是图表横坐标展示字段，如 `1月`；`month_no` 是排序字段，如 `01`。
- `period` / `compare_period` 保存原始年月关系，如 `2024-01` / `2025-01`，不要作为月同比图横坐标展示。
- `encoding.x` 使用 `month_label`，不能同时把 `2024-01` 和 `2025-01` 作为两个 X 轴点。
- `encoding.y` 只放需要画线/柱的两个数值字段；`diff`、`change_rate` 保留在 `data` 和 `series` 说明里，默认不放进 `encoding.y`。
- `encoding.x`、`encoding.y`、`series[].field` 必须与 `data` 中真实字段名完全一致；例如数据字段是 `sales_current` / `sales_previous` 时，不能写成 `current` / `previous`。
- 如果用户明确要求“2025年相对2024年同比”，也可以让 `period` 保存 `2025-01`、`compare_period` 保存 `2024-01`，但横坐标仍用 `month_label`，且必须一行对齐一组同期数据。
- 禁止输出这种错位数据：`{"period":"2024-01","sales_2024":71,"sales_2025":0}` 和 `{"period":"2025-01","sales_2024":0,"sales_2025":87}`。

## 图表选择

- 时间趋势：`line`
- 分类对比：`bar`
- 占比：`pie`，分类建议不超过 10 个
- 阶段转化 / 流失分析：`funnel`，`encoding.x` 为阶段字段，`encoding.y[0]` 为数值字段，按阶段业务顺序展示
- 实体关系：`graph`，`encoding.x` 为起点字段，`encoding.y[0]` 为终点字段，可额外保留关系强度数值字段
- 单个或少量关键指标：`kpi`
- 明细列表：`table`
- 多指标同趋势：`line`，`encoding.y` 放多个字段

漏斗图表达的是阶段转化和阶段流失，例如访问 → 注册 → 加购 → 下单 → 支付；它不是普通流程图。用户要看节点连接、调用链、上下游关系或依赖网络时，用 `graph`。

漏斗图查询是阶段聚合，排序只能使用阶段字段或聚合后的数值字段。不要在 `GROUP BY 阶段` 后按订单号、用户ID、明细时间等未分组明细字段排序。

生成任何图表 DSL 时，`order_by.field` 只能使用当前查询输出字段：已选择的维度或指标。聚合指标排序写指标名，不写 `COUNT(...)`、`SUM(...)` 这类 SQL 表达式。

## 多 SQL 处理规则

多 SQL 结果生成一张图时，不能让模型手写最终 `data`。必须写 Python 脚本完成：

1. 读取多份查询结果 JSON。
2. 选择 join key，例如日期、月份、类别。
3. 标准化 key 类型和值。
4. 对齐 key 集合。
5. 处理缺失值。
6. 计算派生字段。
7. 输出 `chart_spec`。

常见派生字段：

| 字段 | 公式 |
|---|---|
| `diff` | `current - previous` |
| `change_rate` | `(current - previous) / previous`，previous 为 0 时为 null |
| `ratio` | `value / total`，total 为 0 时为 null |
| `achievement_rate` | `actual / target`，target 为 0 时为 null |

## 缺失值策略

必须写入 `meta.missing_policy`：

- `fill_zero`：缺失补 0，适合计数、金额汇总。
- `keep_null`：缺失保留 null，适合趋势断点。
- `drop_missing`：丢弃任一口径缺失的 key。

除非业务明确要求，金额、数量聚合类默认 `fill_zero`；比率、均值类默认 `keep_null`。

## 输出要求

最终文件使用业务语义命名。单图可使用默认名；多图必须增加能表达图表含义的后缀，避免互相覆盖：

```text
outputs/sales.chart.json
outputs/chart_spec_sales_rank.json
outputs/chart_spec_sales_ratio.json
```

不要用版本号区分多个图表；后缀必须表达图表业务含义。

写入或整体覆盖 `chart_spec` 时必须使用多行缩进 JSON 和 `file_write`，不要压缩成一行。局部修改已有文件时，先用 `file_read` 读取目标文件当前内容，再用 `file_edit(file_path, old_content, new_content)` 替换一段完整且唯一的原文；找不到或匹配多处时重新读取，不能凭记忆重试。

最终图表文件写入 outputs 后，必须调用 `present_artifacts(files=[...])` 显式展示给用户；`file_write` 或 `file_edit` 只代表文件已写入或更新，不代表聊天框已展示预览。

不要把完整 JSON 展示给用户。用户只需要知道预览已生成、图表类型、口径和文件路径。
