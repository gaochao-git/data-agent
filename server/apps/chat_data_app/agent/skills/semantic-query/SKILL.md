---
name: semantic-query
description: 用当前授权的语义模型完成自然语言查数，先检索并确认指标维度口径，再选择 DSL 或只读 SQL 执行查询和分析。
metadata:
  title: 语义查询技能
  domain: 数据分析
  category: semantic_query
  output_type: query
---

# 语义查询技能

## 适用范围

处理自然语言数据查询、明细查看、统计汇总、趋势、排行、对比和基于查询结果的计算分析。

本技能只负责语义模型选择、查询规划、DSL/SQL 选择和结果分析；文件和产物任务遵循其各自的工具协议。

## 必读资料

- 当前数据库方言为 Hive 或涉及原生 SQL 的日期、字符串、窗口等方言差异时，读取：
  `/mnt/skills/semantic-query/references/dialect.md`
- DSL 查询只使用 `time_grain`，不要把 SQL 日期格式串写入 DSL。

## 必须遵守的总原则

- 所有业务查询先确认语义模型和字段定义，不凭常识编造指标、维度、表、字段、枚举或查询结果。
- 常规查询优先使用 DSL；只有 DSL 确实无法表达查询结构时，才使用顶层 `sql` 参数传入只读 SQL。
- `semantic_query_tool` 的 `dsl` 和 `sql` 只能二选一。SQL 路径不调用 `dsl2sql_tool`。
- 指标、维度或业务口径本身不明确时必须澄清，不能用 SQL 绕过；字段已经确认、只是枚举值或时间存储格式未知时，才允许少量抽样确认。
- 一次 `dsl2sql_tool` 或 `semantic_query_tool` 只能使用一个 `semantic_config_id`，不能混合多个模型的字段、分组或 JOIN。
- 机构范围由 `member_datasource_ids` 控制，不在 DSL 过滤机构名称、编码或 ID。

## 标准流程

### 1. 检索语义模型

1. 每个业务问题原则上只调用一次 `semantic_tool(operation="search_models", query="...")`。工具会在当前用户可见的全部启用语义模型中检索，并可一次返回多个候选；`query` 保留完整业务意图，但不带机构名称、机构编码或 `member_datasource_id`。不要按指标、维度或分析子问题拆成多个重复或并行的 `search_models`。
2. 根据返回候选模型、描述、字段数量和相关性选择候选；手动选择的模型只是优先参考，不代表锁定。
3. 对准备使用的候选调用 `semantic_tool(operation="list_fields", semantic_config_id="...", field_type="all")`，必要时用 `field_type="metric"` 或 `"dimension"` 分页。
4. `list_fields` 只是轻量字段索引，单页 `limit` 为 1 至 200。需要确认指标表达式、维度表达式、表列、关系、枚举或完整口径时，必须按工具返回的实际 `path` 使用 `file_grep` 定位，再用 `file_read` 分页读取 YAML。不能根据模型名称、文件名或字段相似性猜测。
5. 搜索或读取确认当前配置不存在所需指标、维度或模型时，停止查询并说明当前缺少的能力；候选不唯一或读取后业务口径仍不清楚时，停止查询并只向用户提出一个最关键的业务澄清问题。

### 2. 通过查询门禁

调用 `dsl2sql_tool`、`semantic_query_tool` 或 `calculator_tool` 前，必须确认：

- 当前使用的语义模型明确；
- 用户问题中的指标、维度、筛选对象和时间范围能与模型高置信匹配；
- 枚举含义、单位、时间格式、统计粒度和业务限定明确，或已按规则抽样确认；
- 没有多个合理解释，也没有依赖未维护的元数据。

“看起来像”“可能是”“通常指”“与某字段相近”都不算确认。

复购率、转化率、留存率、占比、客单价、有效率等比例或比率问题，必须使用模型中已维护且口径一致的指标；模型没有明确指标时，用户必须补充公式和统计周期，不能根据相近字段自行推导。

只有以下情况允许抽样：指标、维度和业务口径已经确认，只是状态值、枚举含义、时间存储格式或实际粒度未知。抽样应使用少量明细字段和明确的 `limit`，确认后再执行正式查询。

### 3. 选择执行对象

- 用户明确指定一个机构：只传该机构对应的 `member_datasource_ids`。
- 用户明确指定多个机构：只传这些机构对应的多个 ID。
- 只有用户明确说全部机构、各机构或所有执行对象时，才传全部可用 ID。
- 未指定机构且有多个可用机构时，默认传系统列表第 1 个机构，并在最终回答说明默认机构。
- 机构名称或编码无法唯一匹配时，先澄清范围。

机构 ID 只放 `member_datasource_ids`，不要添加机构维度或机构过滤条件。

## 查询规划

- 查看明细、详情、列表或具体记录：`metrics` 必须为空，展示字段全部放入 `dimensions`。
- 统计、汇总、平均、总计、趋势、分组、排行等聚合意图明确时，才使用口径明确一致的 `metrics`。
- 用户原词精确命中指标或维度时优先使用精确项，不自动增加“平均、汇总、总额、数量”等限定词。
- 未指定时间范围时，总量和明细查询可以继续；趋势、排行、对比、Top N、分布分析必须先补充时间范围。
- 派生字段、截取、拼接、正则或复杂函数分组如果没有已维护维度，DSL 不可表达，不要退化为原字段分组，应评估 SQL 回退或澄清。
- 同比、环比、增长率必须拆为基础查询，结果对齐后使用 `calculator_tool` 计算差值和比例，禁止心算。全年或年度同比是年度总量对比；月度、季度或日同比按相同粒度对齐；环比只能比较相邻周期，不能把两个不同年度直接称为环比。
- 用户使用今天、昨天、本周、本月、当前或最近等相对时间，且需要确定真实范围时，先调用 `get_current_time(detail=false)`；需要时区、星期或时间戳时使用 `detail=true`。

## DSL 查询

### 结构

DSL 顶层只使用以下字段：

```json
{
  "metrics": [],
  "dimensions": [],
  "filters": [],
  "having": [],
  "order_by": [],
  "limit": 100,
  "offset": 0
}
```

- `dimensions` 普通维度写字符串；时间分组写 `{"name":"已确认的时间维度","time_grain":"month"}`。
- 时间粒度可用 `year`、`quarter`、`month`、`week`、`day`、`hour`。
- 时间过滤值必须与已确认的维度表达式和原始存储格式一致；`time_grain` 只控制分组展示，不改变过滤口径。显式时间范围优先使用半开区间，避免结束日漏数。
- `filters` 用于行级维度过滤，固定值过滤字段为 `dimension`、`operator`、`value`、`value_type`；`value_type` 只允许 `literal` 或 `sql_function`。单个值类型适用于全部值；多值过滤可传与 `value` 等长的类型列表，按位置逐项对应。`having` 只用于聚合结果过滤。
- `order_by.field` 只能是当前 DSL 已输出的指标或维度；普通查询不要设置 `offset`。

固定值过滤的操作符使用 SQL 风格：`=`、`!=`、`<>`、`>`、`<`、`>=`、`<=`、`IN`、`NOT IN`、`LIKE`、`NOT LIKE`、`STARTS WITH`、`ENDS WITH`、`BETWEEN`、`IS NULL`、`IS NOT NULL`、`EMPTY STRING`。不要使用 `equals`、`notEquals`。

复杂条件使用 `and` / `or` 树；如果 `or` 条件还要叠加其他条件，必须用顶层 `and` 包住整个组合。字段间比较使用结构化 `left` / `right`：外层 `operator` 只能是 `=`、`!=`、`<>`、`>`、`<`、`>=`、`<=`，`IN`、`LIKE`、`BETWEEN` 等只用于固定值过滤；`left` 只允许 `{"type":"dimension","name":"维度名"}`，`right` 可以是维度、字面量或 `binary` 表达式，`binary.operator` 只能是 `+`、`-`、`*`、`/`。不要把“维度B * 10”作为固定值字符串。

`having` 固定值过滤使用 `metric`、`operator`、`value`；指标间比较使用结构化 `left` / `right`，`left` 只允许 `{"type":"metric","name":"指标名"}`，`right` 只能是指标、字面量或 `binary` 表达式，`binary.operator` 只能是 `+`、`-`、`*`、`/`。不要把“指标B * 10”作为固定值字符串，也不要把指标结果条件放进 `filters`。

### DSL 示例

下面示例中的“指标A”“维度A”“字段A”只是结构占位，不能直接作为真实查询参数。实际调用前必须替换为本轮 `semantic_tool` 和 YAML 确认过的名称。

明细查询：

```json
{"metrics":[],"dimensions":["字段A","字段B"],"filters":[{"dimension":"字段A","operator":"BETWEEN","value":["值1","值2"]}]}
```

分组统计：

```json
{"metrics":["指标A"],"dimensions":["维度A"],"filters":[],"having":[],"order_by":[]}
```

按月趋势：

```json
{"metrics":["指标A"],"dimensions":[{"name":"时间维度","time_grain":"month"}],"filters":[],"having":[],"order_by":[{"field":"时间维度","direction":"ASC"}]}
```

分组后过滤：

```json
{"metrics":["指标A"],"dimensions":["维度A"],"filters":[],"having":[{"metric":"指标A","operator":">","value":10}],"order_by":[]}
```

字段间过滤：

```json
{"metrics":[],"dimensions":["字段A","字段B"],"filters":[{"left":{"type":"dimension","name":"字段A"},"operator":">","right":{"type":"dimension","name":"字段B"}}]}
```

条件组合：

```json
{"metrics":[],"dimensions":["字段A","字段B"],"filters":[{"and":[{"or":[{"dimension":"字段A","operator":"=","value":"值1"},{"dimension":"字段A","operator":"=","value":"值2"}]},{"dimension":"字段B","operator":">","value":10}]}]}
```

错误示例：

```json
{"metrics":["指标A"],"dimensions":[{"dimension":"时间维度","time_grain":"month"}]}
```

时间维度对象必须使用 `name`，不能使用 `dimension`；否则维度名可能被解析为空。

### DSL 工具顺序

1. 生成完整 DSL。
2. 调用 `dsl2sql_tool`，显式传入同一个 `semantic_config_id` 和同一份 DSL 做校验/生成 SQL。
3. 校验成功后调用 `semantic_query_tool`，显式传入同一个 `semantic_config_id`、本次范围的 `member_datasource_ids` 和同一份顶层 `dsl`。
4. 不要把 DSL 字段提升到工具顶层，也不要修改校验工具返回的 SQL。

## SQL 回退

只有 DSL 明确无法表达查询结构时，才可以调用 `semantic_query_tool` 的顶层 `sql` 参数。典型结构包括 CTE、子查询、窗口函数、多阶段聚合、自连接、`UNION`、`EXISTS`、复杂 `CASE WHEN` 或 DSL 无法表达的中间结果计算。

SQL 路径必须同时传入 `semantic_config_id` 和 `member_datasource_ids`，不能同时传 `dsl`，也不调用 `dsl2sql_tool`。

SQL 只能是只读查询。规划 SQL 时只能使用当前语义模型维度中维护的原始表名、原始列名和 `relationships` 中确认的关联关系；关联字段只用于连接路径，不能当作未暴露业务字段随意查询或过滤。已维护指标的表达式代表业务口径，不能擅自改写。不能因为指标、维度或口径不清就回退 SQL。

涉及时间格式或原生 SQL 方言时，按“必读资料”读取 `/mnt/skills/semantic-query/references/dialect.md`；DSL 中仍然只写 `time_grain`，不要写 SQL 日期格式串。

## 多步分析与结果处理

- `save_as` 默认不传。只有用户明确要求保存查询结果，或后续二次分析、脚本处理、产物生成流程明确要求从磁盘读取完整结果时，才将其设置为当前会话 `workspace/` 下的 `.json` 路径；不要为普通查询、前端表格展示或简单结论查询主动使用 `save_as`。
- 同比和环比分别查询当前周期与对比周期的基础数据，按周期对齐后调用 `calculator_tool` 计算差值、增长率或比例；对比值为 0 或为空时不能输出普通百分比。
- 多个独立查询分别执行，最后统一总结；中间过程不必输出完整原始数据。
- 工具返回结果被卸载时，不能依据路径、行数、SQL 或 preview 推断业务结论。仅当后续分析确实需要具体值时，才按 `result_path` 分页读取最小必要内容。
- 查询结果只要求前端展示时，不要再次粘贴表格、逐行明细、完整 JSON 或原始 SQL。
- 空结果、失败结果和部分执行对象失败必须如实说明，不编造原因或数字。

## 失败处理

- 已确认当前模型、指标或维度不存在：说明当前配置缺少什么；候选不唯一或口径不清：向用户提出一个最关键的业务澄清问题。
- 工具提示当前配置不支持、模型不存在或缺少必要配置：停止当前查询分支并用业务语言说明缺口。
- 同类工具错误连续出现时停止重复尝试，改为修正输入、换合法路径或说明能力边界。
- 不向用户展示工具参数、语义模型配置、DSL、SQL 或原始工具返回，除非用户明确要求解释技术原因。
