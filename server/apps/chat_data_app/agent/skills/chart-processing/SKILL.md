---
name: chart-processing
title: 图表处理技能
description: 基于一条或多条 SQL 查询结果生成可渲染 chart_spec，复杂拼接和派生计算必须通过 Python 脚本完成。
metadata:
  author: 高超
  version: "2.0"
  domain: 数据分析
  category: chart_processing
  output_type: chart_spec
---

# 图表处理技能

## 适用目标

把用户的“生成图表 / 可视化数据 / 展示趋势、对比、占比、同比、环比、目标达成”等需求，转换成前端可渲染的 `chart_spec`。

本技能不是让模型手写最终大段图表数据，也不是直接生成完整 ECharts option。模型负责规划查询和选择图表，确定性代码负责拼接、对齐、补齐和计算，最终产物是稳定的图表规格文件。

## 产物契约

图表预览文件使用业务语义文件名；单图可使用默认名，多图必须增加能表达图表含义的后缀：

```text
/mnt/user-data/outputs/sales.chart.json
/mnt/user-data/outputs/chart_spec_sales_rank.json
/mnt/user-data/outputs/chart_spec_sales_ratio.json
```

工作草稿：

```text
/mnt/user-data/workspace/chart_spec_draft.json
/mnt/user-data/workspace/chart_spec_sales_rank_draft.json
/mnt/user-data/workspace/chart_spec_sales_ratio_draft.json
```

复杂处理脚本：

```text
/mnt/user-data/workspace/build_chart_spec.py
```

写入 outputs 后必须调用 `present_artifacts(files=[...])` 显式呈现给用户；前端聊天框只渲染 `present_artifacts` 返回的 `chart_spec`，右侧文件面板仍可按 outputs 文件查看产物。

## 必读资料

进入本技能后执行：

1. 读取主指南：
   - `/mnt/skills/chart-processing/references/chart-spec-guide.md`
3. 需要写 Python 处理脚本时，优先参考：
   - 单 SQL 图：`/mnt/skills/chart-processing/scripts/single_result_chart.py`
   - 本期 / 上期、同比 / 环比：`/mnt/skills/chart-processing/scripts/compare_period_chart.py`
   - 多 SQL 多系列拼接：`/mnt/skills/chart-processing/scripts/join_results_chart.py`

## 场景判断

### 单 SQL 单图

适用于普通趋势、分类对比、占比分布。可以直接基于一条查询结果生成 `chart_spec`。

示例：
- 按月份销售额趋势
- 各部门工单数对比
- 各渠道订单占比
- 线索、注册、下单、支付等阶段转化漏斗

### 单 SQL 多图

适用于一条查询结果包含多个指标或多个维度视角。可以基于同一份查询结果生成多个 `chart_spec`，或生成一张多指标图。

示例：
- 月份 + 销售额 + 订单数 + 客单价
- 地区 + 销售额 + 利润 + 客户数

### 多 SQL 单图

适用于环比、同比、目标达成、多口径对比等场景。必须先执行多条 SQL，再通过 Python 脚本把结果对齐成最终图表数据。

示例：
- 本期 SQL + 上期 SQL → 环比趋势图
- 今年 SQL + 去年 SQL → 同比折线图
- 实际值 SQL + 目标值 SQL → 达成率图
- 多个独立口径 SQL → 多系列对比图

### 同比/环比图

同比/环比图必须按同期位置对齐，不是连续日期趋势图。

先判断粒度和图形边界：
- `全年同比` / `年度同比`：比较年度总量，通常用 `kpi` 或 `bar`，X 轴为年份，不显示月份。
- `月同比` / `按月同比` / `月度同比`：比较不同年份同月份，X 轴用 `1月...12月`，系列用年份。
- `季度同比`：比较不同年份同季度，X 轴用 `Q1...Q4`，系列用年份。
- `日同比`：比较不同年份同月同日或指定日期范围内的同期日，X 轴用日期位置，系列用年份。
- `月环比`：同一年或指定区间内相邻月份比较，X 轴用月份，派生 `previous_period`、`diff`、`change_rate`。
- `季度环比`：相邻季度比较，X 轴用季度。
- `年环比`：相邻年份比较，X 轴用年份。
- `2024年 vs 2025年` 这类不同年份对比不是环比；除非用户明确要求“逐月环比”，否则不要把年份对比处理成环比。

月同比的正确做法：
- 分别查询两个年份的月度数据，查询结果月份可以是 `2024-01`、`2025-01`。
- Python 脚本用月份序号对齐，例如 `2024-01` 与 `2025-01` 都归到同一组。
- 最终 `chart_spec.data` 使用宽表，一行表示一组同期月份，包含展示月份 `month_label`（如 `1月`）、排序月份 `month_no`（如 `01`）、原始年月 `period` / `compare_period`、两个年份数值、`diff`、`change_rate`。
- `encoding.x` 使用展示字段 `month_label`，不要用 `2024-01` / `2025-01` 做横坐标；`period` / `compare_period` 只用于保留原始年月关系。
- `encoding.y` 只放两条需要绘制的系列，例如 `["sales_2024", "sales_2025"]`。
- 当前前端支持这种宽表多系列：`encoding.y` 可以是多个数值字段；未放入 `encoding.y` 的 `diff`、`change_rate` 会保留在数据里，但不会作为主折线绘制。
- `encoding.x`、`encoding.y`、`series[].field` 必须与 `data` 中真实字段名完全一致；如果数据字段是 `sales_current` / `sales_previous`，不能写成 `current` / `previous`。
- 使用参考脚本时，月同比传 `--align-by month-of-year --current-field sales_2025 --previous-field sales_2024 --current-label 2025年销量 --previous-label 2024年销量`，脚本会输出 `month_label` 作为 X 轴、`month_no` 作为排序字段，避免 `2024-01` 和 `2025-01` 被当成两个 X 轴点。
- 如果数据源或语义配置 `source_type=hive`，月份 SQL 必须由 DSL2SQL 生成 Hive 方言，例如 `DATE_FORMAT(time_col, 'yyyy-MM')`。看到 `%2024-%0`、`%2025-%0` 这类结果时，原因是 Hive 执行了 MySQL 风格的 `%Y-%m` 格式串，不是 URL 编码；禁止据此手写 SQL 或说“URL 编码”，应修正 DSL/方言上下文后重新调用 `dsl2sql_tool`。
- DSL 时间粒度参数必须使用 `time_grain`，例如 `{"name":"下单时间","time_grain":"month"}`；如果 `dsl2sql_tool` 不接受错误参数名，修正 DSL 参数名后重试，禁止改为手写 SQL。

禁止做法：
- 禁止把 `2024-01, 2024-02, ..., 2025-01, 2025-02` 拼成连续 X 轴后给另一年补 0。
- 禁止把同比增长率作为主折线，除非用户明确要求画增长率。
- 禁止因为日期格式或 SQL 结果不理想而手写 SQL；只能调整 DSL 后重新调用 `dsl2sql_tool`。
- 禁止把 Hive 的 `%Y-%m` 错误结果解释成 URL 编码或前端编码问题。
- 禁止把所有同比/环比默认画成月份；必须根据用户问题中的“全年/年度/月度/季度/按日”等词确定粒度。

## 强制规则

- 禁止模型凭推理手写复杂最终数据 JSON。
- 需要多结果拼接、排序、补齐、同比、环比、差值、占比、TopN 合并时，必须写 Python 处理脚本生成最终 `chart_spec`。
- Python 脚本必须读取 `semantic_query_tool(save_as=...)` 生成的真实查询结果文件，不能把数据硬编码在脚本里。
- 查询数据必须先通过 `dsl2sql_tool` 校验同一份 DSL，再把同一份 DSL 传给 `semantic_query_tool` 执行；禁止模型手写 SQL 或向 `semantic_query_tool` 传 SQL。
- 如果 `dsl2sql_tool` 生成的 SQL 不符合预期，只能修正 DSL 参数后重新调用 `dsl2sql_tool`，最多重试 2 次；仍失败时停止该查询分支并说明失败原因，禁止自行改写、拼接或替换成手写 SQL。
- 派生字段必须由脚本计算，例如 `diff`、`change_rate`、`ratio`、`rank`。
- 缺失值处理必须显式，例如补 0、保留 null 或丢弃，并写入 `meta.missing_policy`。
- 当指标或维度没有说明枚举值含义、状态码 / 类型码映射、时间字段存储格式 / 粒度 / 时区、金额单位等关键存储口径时，先查询 3 条原始明细确认字段取值和时间格式，再执行正式图表查询和处理。
- 生成或整体覆盖 `chart_spec` 时必须使用多行缩进 JSON，并使用 `file_write`；不要压缩成一行。
- 修改已有 `chart_spec` 的局部内容前必须先用 `file_read` 读取目标文件，再使用 `file_edit(file_path, old_content, new_content)` 精确替换一段完整且唯一的原文。找不到或匹配多处时重新读取，不要凭记忆重试；需要整体替换时改用 `file_write`。
- 最终输出写入 `outputs/<业务名>.chart.json`。多图必须使用业务语义命名区分，例如 `sales_rank.chart.json`、`sales_ratio.chart.json`。不要用版本号区分图表含义。
- 写入或更新 outputs 图表文件后，必须调用 `present_artifacts(files=["/mnt/user-data/outputs/<业务名>.chart.json"])`，否则聊天框不会展示图表预览。
- 不要在用户正文展示完整 JSON、SQL、脚本或原始查询行。

## 工具流程

按下面顺序完成，不把工具参数、JSON、SQL、脚本或工具返回原文写进用户正文。

1. `file_read("/mnt/skills/chart-processing/references/chart-spec-guide.md")`
2. 判断属于单 SQL 还是多 SQL 场景
4. 如枚举、状态、类型、时间或金额等字段存储口径不明确，先构造明细抽样并查询 3 条原始数据确认结构
5. 使用 `dsl2sql_tool` 构造真实查询；禁止手写 SQL
6. 使用 `semantic_query_tool(dsl=同一份DSL, save_as="/mnt/user-data/workspace/current.json")` 获取真实数据并保存中间文件；多 SQL 场景分别保存为 `/mnt/user-data/workspace/current.json`、`/mnt/user-data/workspace/previous.json` 等
7. 简单单 SQL 场景：用 `file_write` 写入 `/mnt/user-data/workspace/chart_spec_draft.json`；多图使用带业务后缀的草稿名
8. 复杂场景：用 `file_write` 写入 `/mnt/user-data/workspace/build_chart_spec.py`，并让脚本读取第 6 步物化的查询结果文件
9. 仅当本轮可用 `bash_run` 时，执行 `bash_run(command="python3 /mnt/user-data/workspace/build_chart_spec.py ...")` 生成草稿文件；未提供 `bash_run` 时不要假装脚本已执行，复杂处理应说明当前无法执行脚本
10. 用 `file_write` 将草稿整体写入 `/mnt/user-data/outputs/<业务名>.chart.json`；仅需改动已有产物的一处唯一文本时，先 `file_read` 再用 `file_edit`。多图用业务命名区分，不使用版本号
11. 调用 `present_artifacts(files=["/mnt/user-data/outputs/<业务名>.chart.json"])` 展示最终图表预览；多图一次传入多个 outputs 文件
12. 简短总结图表预览状态

## 支持图表类型

生成 `chart_spec` 或 `dashboard_spec` 时，`chart_type` 只能使用下表注册的 key；未注册类型会被工具层 `is_supported_chart_type` 拒绝，不要用通用 ECharts 知识扩展。

| chart_type | 中文名 | 适用场景 | 数据形态 |
|---|---|---|---|
| `table` | 表格 | 明细数据、多字段精确查看 | 多列明细或聚合结果 |
| `kpi` | 指标卡 | 单个或少量核心指标 | 单行单值或少量指标值 |
| `bar` | 柱状图 | 分类对比、排行 | 分类字段 + 数值字段 |
| `horizontal_bar` | 条形图 | 名称较长的分类对比或 TopN 排行 | 分类字段 + 数值字段 |
| `line` | 折线图 | 时间趋势、连续变化 | 时间字段 + 一个或多个数值字段 |
| `area` | 面积图 | 趋势规模和累计感 | 时间字段 + 数值字段 |
| `pie` | 饼图 | 占比分布，分类不宜过多 | 分类字段 + 正数值字段 |
| `map` | 地图 | 地区、城市、省份等空间分布 | 地区字段 + 数值字段 |
| `combo` | 柱线组合图 | 同一维度下两个量纲指标柱线组合展示 | 维度字段 + 2个数值字段 |
| `funnel` | 漏斗图 | 阶段转化、阶段递减、流程流失分析 | 阶段字段 + 数值字段，通常按阶段顺序递减 |
| `graph` | 关系图 | 实体关系、依赖网络、起点终点连接 | 起点字段 + 终点字段，可选关系强度数值字段 |

## 图表选择

| 场景 | 图表类型 | 数据形态 |
|---|---|---|
| 分类对比 | `bar` | 分类字段 + 数值字段 |
| 时间趋势 | `line` | 时间字段 + 数值字段 |
| 占比分布 | `pie` | 分类字段 + 正数值字段，分类不宜过多 |
| 阶段转化 / 流失分析 | `funnel` | 阶段字段 + 数值字段，按阶段业务顺序展示，通常逐步递减 |
| 实体关系 | `graph` | 起点字段 + 终点字段，可选关系强度数值字段 |
| 多指标趋势 | `line` | 时间字段 + 多个数值字段 |
| 本期/上期对比 | `line` 或 `bar` | key + current + previous + change_rate |
| 单值指标 | `kpi` | 一个或多个指标值 |
| 明细结果 | `table` | 多字段明细行 |

漏斗图只用于阶段转化、阶段递减或流失分析，例如访问 → 注册 → 下单 → 支付。它不是泛流程图；如果用户要表达节点连接、上下游依赖、服务调用或普通流程结构，优先使用 `graph`，不要误选 `funnel`。

漏斗图查询是聚合查询，排序只能使用阶段维度或聚合指标。不要按订单号、用户ID、明细时间等未分组明细字段排序，否则 Hive 会拒绝执行。

所有图表 DSL 的 `order_by.field` 都只能来自当前查询输出字段，也就是 `metrics` 中的指标或 `dimensions` 中的维度。聚合指标排序只写指标名，由 DSL2SQL 生成 `ORDER BY COUNT(...)` / `ORDER BY SUM(...)`，不要手写聚合函数。

## 图表示例（few-shot）

以下示例只用于学习图表内部语义参数选择和工具调用顺序。语义参数、DSL、SQL、脚本和工具参数不得输出到用户消息正文；需要使用时直接作为工具参数传入。进入本技能后按上方「工具流程」执行，下面示例省略 `file_read` 指南步骤。

### 示例1：阶段转化漏斗图
用户：用漏斗图看线索到成交的转化
内部语义判断：
- chart_type: funnel
- dimensions: 阶段字段，例如“线索阶段”或“订单阶段”
- metrics: 阶段人数、订单数、用户数等数值指标
- order_by: 按阶段业务顺序、阶段维度或指标排序；不要按订单号、用户ID、明细时间等未分组明细字段排序
工具顺序：
→ `dsl2sql_tool` 构造阶段聚合 DSL（维度=阶段字段、指标=数值字段、order_by 按阶段顺序或指标，不要手写聚合函数）。
→ `semantic_query_tool(dsl=同一份DSL, save_as="/mnt/user-data/workspace/funnel_stage.json")` 获取阶段数据。
→ 单 SQL 用 `file_write` 写 `/mnt/user-data/workspace/chart_spec_draft.json`，`chart_type=funnel`、`encoding.x=阶段字段`、`encoding.y=[数值字段]`；阶段多或需补零/排序复杂时写 `build_chart_spec.py` 并在可用 `bash_run` 时执行。
→ `file_write` 将草稿整体写入 `/mnt/user-data/outputs/<业务名>.chart.json`。
→ `present_artifacts(files=["/mnt/user-data/outputs/<业务名>.chart.json"])`。
回复示例：阶段转化漏斗图已生成。

### 示例2：关系图
用户：画出服务之间的调用关系
内部语义判断：
- chart_type: graph
- dimensions: 起点实体、终点实体
- metrics: 可选调用次数、流量、权重等关系强度
工具顺序：
→ `dsl2sql_tool` 构造关系边查询 DSL（维度=起点字段、终点字段，指标=可选关系强度）。
→ `semantic_query_tool(dsl=同一份DSL, save_as="/mnt/user-data/workspace/graph_edges.json")` 获取关系边数据。
→ `file_write` 写 `/mnt/user-data/workspace/chart_spec_draft.json`，`chart_type=graph`、`encoding.x=起点字段`、`encoding.y=[终点字段]`，保留关系强度字段。
→ `file_write` 写入 `/mnt/user-data/outputs/<业务名>.chart.json`。
→ `present_artifacts(files=["/mnt/user-data/outputs/<业务名>.chart.json"])`。
回复示例：服务调用关系图已生成。

### 示例3：柱线组合图
用户：查看订单数量和销售额趋势
内部语义判断：
- chart_type: combo
- dimensions: 时间字段
- metrics: ["订单数量", "销售额"]
- encoding.y 只放两个指标，第一个画柱、第二个画线
工具顺序：
→ `dsl2sql_tool` 构造时间维度双指标 DSL（dimensions=时间字段、metrics=[订单数量, 销售额]）。
→ `semantic_query_tool(dsl=同一份DSL, save_as="/mnt/user-data/workspace/combo_data.json")` 获取查询结果。
→ `file_write` 写 `/mnt/user-data/workspace/chart_spec_draft.json`，`chart_type=combo`、`encoding.x=时间字段`、`encoding.y=[订单数量, 销售额]`（第一个画柱、第二个画线）。
→ `file_write` 写入 `/mnt/user-data/outputs/<业务名>.chart.json`。
→ `present_artifacts(files=["/mnt/user-data/outputs/<业务名>.chart.json"])`。
回复示例：柱线组合图已生成。

## 完成判定

同时满足以下条件，才算图表预览已生成：

1. 已读取主指南。
2. 已执行必要 SQL 并获得真实数据。
3. 复杂拼接场景已生成并执行 Python 处理脚本，且最终数据来自脚本结果。
4. 已生成 `outputs/<业务名>.chart.json`，且可被识别为 chart_spec。
5. 已调用 `present_artifacts` 显式展示最终 outputs 图表。
6. `chart_spec` 包含 `version`、`title`、`chart_type`、`data`、`encoding`。

只执行 SQL 不算完成；只写处理脚本但没有执行并生成 `outputs/<业务名>.chart.json` 不算完成；只在正文描述图表也不算完成。

## 用户输出

最终回答只包含：

- 图表预览已生成或失败原因
- 实际生成的 `outputs/<业务名>.chart.json`
- 图表类型和关键口径

不要展示完整 `chart_spec`、SQL、Python 脚本、工具参数或原始查询结果。
