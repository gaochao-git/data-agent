# 看板生成指南

本指南是看板生成技能的唯一参考文档，覆盖看板规格、布局、图表选择、语义 DSL 规则和失败处理。生成看板时读取本文件即可，不要再读取其他 reference 文档。

## 交付契约

看板生成的交付物是 `dashboard_spec`，不是 SQL 查询结果报表。它描述“看什么、怎么查、怎么布局”，只保存卡片定义、图表类型、布局和语义 DSL，不保存 SQL 执行后的结果数据。

看板预览文件写入 outputs 后，必须调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])` 显式展示给用户；`file_write` 或 `file_edit` 只代表文件已写入或更新，不代表聊天框已展示预览。

```text
outputs/<业务主题>-<短时间戳或短随机后缀>.dashboard.json
```

每次新建看板必须使用独立文件名，不要复用或覆盖已有 outputs 看板预览文件；同一会话中多次生成看板，应保留多个独立的 `.dashboard.json` 产物。只有用户明确要求修改某个已有看板预览文件时，才读取并更新该文件。

看板生成时先维护草稿，最终再移动为预览文件：

```text
workspace/dashboard_draft.json
  -> outputs/<业务主题>-<唯一后缀>.dashboard.json
```

新建看板时先用 `file_write(file_path="workspace/dashboard_draft.json", content=dashboard_spec)` 写入草稿，校验通过后用 `file_write(file_path="outputs/<业务主题>-<唯一后缀>.dashboard.json", content=dashboard_spec)` 写入最终预览文件。修改已有看板预览时，先 `file_read` 指定 outputs 文件；局部修改使用 `file_edit(file_path, old_content, new_content)`，其中 `old_content` 必须是最新文件中完整、非空且唯一的一段原文。需要整体替换时使用 `file_write`。

草稿阶段可以整体覆盖同一个 `workspace/dashboard_draft.json`；不要用 append 拼接 JSON。写入或整体覆盖 `dashboard_spec` 时必须使用多行缩进 JSON 和 `file_write`。局部修改前必须先用 `file_read` 读取当前内容；`file_edit` 找不到原文或匹配多处时，重新读取并提供更长且唯一的 `old_content`，不能凭记忆重试。

## 规格结构

顶层字段：

- `dashboard_name`：看板名称，必填，体现主题和对象。
- `analysis_target`：分析对象，用户未指定时写当前数据源或当前业务整体。
- `topic`：可选，看板主题。
- `need_clarify`：通常不要设置。只有完全无法推断目标、指标和维度时才设置为 `true`。
- `cards`：卡片列表，必填，建议 `4-8` 张，最多 `12` 张。

最小结构：

```json
{
  "dashboard_name": "综合分析看板",
  "analysis_target": "当前数据源",
  "topic": "综合业务分析",
  "cards": []
}
```

每张卡片建议包含：

- `title`：卡片标题，清楚表达看什么。
- `chart_type`：图表类型，只能使用 `table`、`kpi`、`bar`、`horizontal_bar`、`line`、`area`、`pie`、`map`、`combo`、`funnel`、`graph`。
- `purpose`：卡片用途，说明解决的问题。
- `diagnosis`：诊断口径，说明如何判断表现、异常或改进方向。
- `layout`：12 列网格布局，字段为 `x`、`y`、`w`、`h`。
- `dsl`：语义查询 DSL，只使用当前指标体系中可用的指标和维度。
- `chart_config`：可选，图标、颜色和样式配置。只有能提升看板辨识度时才设置，不确定时省略。

禁止事项：

- 不要在卡片中设置 `sql` 字段。
- 不要在卡片中设置 `data`、`rows`、`result`、`x_field`、`y_field` 字段。
- 不要用 `type` 代替 `chart_type`。
- 不要把已查询出的指标数值、排行数据或明细列表写入规格文件。

最小卡片示例：

```json
{
  "title": "核心指标总览",
  "chart_type": "kpi",
  "purpose": "展示当前核心指标水平",
  "diagnosis": "结合趋势或对比判断变化方向",
  "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
  "chart_config": {"kpiIcon": "fa-chart-line", "kpiColor": "#00e5ff"},
  "dsl": {
    "metrics": ["指标名称"],
    "dimensions": [],
    "filters": [],
    "order_by": [],
    "limit": 100
  }
}
```

不要把示例中的 `指标名称`、`时间维度`、`对象维度` 原样用于真实看板。必须替换成当前指标体系真实存在的名称。

## 样式配置

`chart_config` 是可选视觉配置，平台会按看板主题提供默认样式。需要强调业务语义时，可以让模型自行选择以下字段：

- `kpiIcon`：仅用于 `kpi`，可选值为 `fa-users`、`fa-user`、`fa-yen-sign`、`fa-dollar-sign`、`fa-coins`、`fa-chart-line`、`fa-percent`、`fa-file-invoice`、`fa-shopping-cart`、`fa-heart`、`fa-eye`、`fa-clock`、`fa-database`、`fa-server`、`fa-bolt`。
- `kpiColor`：仅用于 `kpi`，可选值为 `#00e5ff`、`#7c4dff`、`#00dc75`、`#ffdc3d`、`#ff3d00`、`#1677FF`、`#36CFC9`、`#52C41A`、`#FAAD14`、`#F5222D`、`#722ED1`、`#EB2F96`、`#8c8c8c`。
- `chartColor`：用于 `bar`、`horizontal_bar`、`line`、`area`、`pie`、`combo`、`funnel`、`graph`，可选值同 `kpiColor`。
- `lineStyle`：仅用于 `line`，可选值为 `smooth`、`straight`、`smooth-no-fill`、`straight-no-fill`。

选择建议：

- 用户、客户、人数：`fa-users` 或 `fa-user`。
- 金额、收入、成本、余额：`fa-yen-sign`、`fa-dollar-sign` 或 `fa-coins`。
- 趋势、增长、变化：`fa-chart-line`，颜色优先青色或绿色。
- 占比、率、完成率：`fa-percent`。
- 订单、工单、单据：`fa-file-invoice`。
- 访问、浏览、曝光：`fa-eye`。
- 时长、延迟、周期：`fa-clock`。
- 数据量、存储、库表：`fa-database`。
- 服务、系统、节点：`fa-server`。
- 性能、告警、风险：`fa-bolt`，风险类可用黄色、橙色或红色。

避免事项：

- 不要使用上面列表之外的图标、颜色或线型。
- 不要为了装饰给所有卡片强行设置同一种颜色；同一看板颜色应有主次。
- `kpiIcon`、`kpiColor` 不用于非 KPI 图表。

## 图表选择

图表必须服务分析目标，不要为了丰富形式而选择不合适的图表。

- `kpi`：总量、均值、比例、当前值等单指标总览。
- `line`：时间趋势，必须有时间维度。
- `area`：强调趋势规模或累计感，必须有时间维度。
- `bar`：类别对比、结构拆解，适合类别数量较少的维度。
- `horizontal_bar`：排行，适合对象名称较长或 Top N。
- `combo`：同一维度下柱线组合展示两个量纲指标，第一个画柱、第二个画线。
- `pie`：占比结构，只在类别较少且需要强调比例时使用。
- `table`：明细诊断、多指标交叉、可追溯清单。
- `map`：地理分布，只有存在地区维度且业务需要地图时使用。
- `funnel`：阶段转化、流程流失、递进步骤分析。
- `graph`：实体关联、来源去向、依赖关系、网络关系。

选择优先级：

1. 总览指标用 `kpi`。
2. 时间维度加核心指标用 `line`。
3. 趋势规模或累计感用 `area`。
4. 业务分类、渠道、类型、区域结构用 `bar`。
5. 机构、客户、产品等对象 Top N 用 `horizontal_bar`。
6. 同一维度下两个量纲指标组合用 `combo`。
7. 明细、多维交叉和后续核查用 `table`。
8. 地区维度且强调空间分布时才用 `map`。
9. 阶段转化链路用 `funnel`。
10. 起点到终点的实体关系用 `graph`。

避免事项：

- 没有时间维度不要用 `line`。
- 类别超过 `8` 个时慎用 `pie`。
- 只有一个指标且没有维度时不要用 `bar`。
- 表格不要承担总览职责，除非用户明确要求明细清单。
- 不要使用系统不支持的图表类型。

## 布局规则

平台看板采用 12 列网格布局。所有卡片必须包含：

```json
{"x": 0, "y": 0, "w": 3, "h": 2}
```

- `x`：横向起点，范围 `0-11`。
- `y`：纵向起点，从 `0` 开始。
- `w`：宽度，范围 `1-12`。
- `h`：高度，建议 `2-6`。

生成规格前检查：

1. 所有卡片都有 `layout`。
2. `x + w <= 12`。
3. `w` 不小于 `3`，除非是小 KPI。
4. `table` 卡片优先 `w=12`。
5. `kpi` 卡片高度优先 `h=2`。
6. 同一层卡片的 `y` 和 `h` 尽量一致，避免错层和空洞。

通用布局适合用户只说“生成一个综合看板”“分析业务表现”等宽泛需求：

- 顶部：`3-4` 张 KPI，总宽不超过 12。
- 第二层：左趋势、右结构，各 `w=6,h=4`。
- 第三层：左排行、右异常或对比，各 `w=6,h=4`。
- 底部：明细诊断表格，`w=12,h=5` 或 `w=12,h=6`。

推荐坐标：

```json
[
  {"x": 0, "y": 0, "w": 3, "h": 2},
  {"x": 3, "y": 0, "w": 3, "h": 2},
  {"x": 6, "y": 0, "w": 3, "h": 2},
  {"x": 9, "y": 0, "w": 3, "h": 2},
  {"x": 0, "y": 2, "w": 6, "h": 4},
  {"x": 6, "y": 2, "w": 6, "h": 4},
  {"x": 0, "y": 6, "w": 6, "h": 4},
  {"x": 6, "y": 6, "w": 6, "h": 4},
  {"x": 0, "y": 10, "w": 12, "h": 5}
]
```

KPI 数量自适应布局：

- 1 个 KPI：`x=0,y=0,w=12,h=2`。
- 2 个 KPI：两个卡片各 `w=6,h=2`，占满第一行。
- 3 个 KPI：三个卡片各 `w=4,h=2`，占满第一行。
- 4 个 KPI：四个卡片各 `w=3,h=2`，占满第一行。

KPI 行之后再放趋势、结构、排行和明细。不要让柱状图、折线图等大图从 `y=0` 开始占据 KPI 行，除非用户明确要求非对称首屏。

5 张卡片推荐布局：

```json
[
  {"x": 0, "y": 0, "w": 6, "h": 2},
  {"x": 6, "y": 0, "w": 6, "h": 2},
  {"x": 0, "y": 2, "w": 6, "h": 4},
  {"x": 6, "y": 2, "w": 6, "h": 4},
  {"x": 0, "y": 6, "w": 12, "h": 5}
]
```

用户指定布局时优先满足：

- “左边趋势，右边结构/排行”：趋势 `x=0,w=6`，结构或排行 `x=6,w=6`。
- “顶部核心指标”：KPI 从 `y=0` 开始铺满第一行。
- “明细放底部”：表格 `x=0,w=12`，`y` 放在所有图表之后。
- “重点看趋势”：趋势图可以 `w=12,h=4` 独占一行。
- “对比多个对象”：柱状图或横向柱状图可以 `w=12,h=4` 独占一行，或与结构图组成两列。

风险监控布局：

- 顶部 KPI 展示风险规模、风险占比、重点对象数量、最新变化。
- 中间用趋势和结构定位风险变化和来源。
- 下方用排行和明细承接治理动作。
- 如果指标体系没有风险分、风险等级、预警阈值，不要编造这些概念；使用趋势、排名、波动、占比、集中度替代。

对象画像布局：

- 顶部放指定对象核心表现。
- 中间放对象自身趋势和同类对象对比。
- 底部放对象下钻明细。
- 对象过滤必须使用当前指标体系中存在的对象维度。用户没有明确对象时，可以生成“对象画像模板看板”，不要强行填具体对象值。

## 语义 DSL 规则

看板卡片的 `dsl` 使用与智能问数一致的语义层规则，由平台工具转换为 SQL。不要手写 SQL，除非用户明确要求并且工具支持。

系统提示词只提供可见指标集目录和手动选择的轻量引用，不包含完整指标、维度、表结构或关联关系。生成 DSL 前必须调用 `search_dataset` 获取实际语义配置；手动选择只作为优先参考，不锁定指标集。再从工具返回的 `semantic_config_ids` 中根据用户的业务目标确定唯一指标集。

看板只能使用一个指标集。多个返回项都可能符合、但无法判断用户要哪个业务口径时，必须先向用户澄清；不得混用多个 `semantic_configs` 的指标或维度，不得生成看板 DSL，不得调用 `dsl2sql_tool`。

看板生成场景禁止在卡片中直接写 `sql` 字段。即使模型能够推断物理表和字段，也必须使用当前指标体系中的 `metrics`、`dimensions`、`filters` 生成 DSL，再交给平台工具转换 SQL。

看板生成场景也禁止把 `semantic_query_tool` 返回的数据写入卡片。抽样验证必须向 `semantic_query_tool` 传 DSL，由工具内部生成 SQL 并执行，不能替代看板规格。

DSL 字段：

```json
{
  "metrics": ["指标名称"],
  "dimensions": ["维度名称"],
  "filters": [],
  "having": [],
  "order_by": [{"field": "指标名称", "direction": "DESC"}],
  "limit": 100
}
```

指标规则：

- `metrics` 必须来自 `search_dataset` 返回的实际语义指标。
- `metrics` 必须是字符串数组，例如 `["订单金额"]`；不要写成对象数组，例如 `[{"name":"订单金额","aggregate":"SUM"}]`。
- 聚合卡片优先使用聚合指标，例如数量、金额、均值、占比。
- 不要在同一聚合卡片中混用原始值指标和聚合指标。
- KPI 卡片通常只放 `1` 个核心指标。
- 表格卡片可以放多个指标，但要保持可读。

维度规则：

- `dimensions` 必须来自 `search_dataset` 返回的实际语义维度。
- 趋势卡片使用时间维度，建议写成 `{"name": "时间维度", "time_grain": "month"}` 这类对象。
- 排行卡片使用对象维度、机构维度、区域维度、产品维度等。
- 没有维度时返回总体聚合结果。

过滤规则必须使用当前存在的维度：

```json
{"dimension": "维度名称", "operator": "=", "value": "过滤值"}
```

可使用 `and` / `or` 组合：

```json
{"and": [{"dimension": "区域", "operator": "=", "value": "华东"}]}
```

不要使用不存在的字段名、表字段名或自由文本字段作为过滤维度。

排序和限制：

- 排行卡片必须设置 `order_by` 和 `limit`。
- `order_by.field` 只能使用当前卡片 DSL 输出字段，也就是 `metrics` 中的指标或 `dimensions` 中的维度。
- 聚合排行优先按指标名排序，例如 `{"field":"订单数量","direction":"DESC"}`；不要写物理字段、订单号、用户ID、明细时间或 `COUNT(...)`。
- 趋势卡片按时间维度 `ASC` 排序。
- 明细表格通常 `limit=50`。

## 校验流程

1. 先确定唯一指标集，再生成完整 `dashboard_spec`，顶层必须包含 `semantic_config_id`。
2. `dashboard_spec.semantic_config_id` 必须取 `search_dataset` 返回的 `semantic_config_ids` 之一。
3. 调用 `dsl2sql_tool(dsl=dashboard_spec, semantic_config_id=dashboard_spec.semantic_config_id)`。
4. 根据返回 warnings 和错误修正 DSL。
5. 有效卡片数量满足用户目标后，写入 `workspace/dashboard_draft.json` 草稿。
6. 新建预览时用 `file_write` 写入一个新的 `outputs/<业务主题>-<唯一后缀>.dashboard.json`；只有用户明确要求修改已有预览时，才先 `file_read` 再用 `file_edit` 精确替换唯一内容，或用 `file_write` 整体覆盖。
7. 调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])` 显式展示最终看板预览。

写入或更新 `workspace/dashboard_draft.json` 后必须继续校验，并写入 outputs 最终预览文件；不能把草稿写入当成看板生成完成。更新已有 outputs 看板文件后必要时重新校验，确认看板规格仍可执行。

## 失败处理

- `dsl2sql_tool` 返回指标不存在：回到 `search_dataset` 返回的指标体系，替换为真实存在的指标，不要编造指标。
- `dsl2sql_tool` 返回维度不可用：检查工具返回的该指标可用维度，移除或替换不可用维度。
- 趋势卡片校验失败：优先改用工具返回指标体系中真实存在的时间维度，并设置合适的 `time_grain`。
- 排行卡片校验失败：确认 `order_by.field` 使用指标名，`dimensions` 使用对象、商品、机构、区域等真实维度。
- 过滤条件失败：只使用当前存在的维度作为 `filters.dimension`，不要使用物理字段名或自由文本字段。
- 有卡片长期无法通过校验：删除该卡片或改成可通过校验的替代视角，不要反复使用同一错误 DSL。
- `file_edit` 失败：重新读取当前目标文件内容，提供更长且唯一的 `old_content`；不要使用 append 拼接 JSON。
- outputs 目标已存在：如果用户是在新建看板，改用新的唯一 `.dashboard.json` 文件名；如果用户明确要求修改该文件，先 `file_read` 再精确替换或整体覆盖。
