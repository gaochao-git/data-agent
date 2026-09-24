
# 查询与结果分析

## 适用范围

处理自然语言数据查询、明细查看、统计汇总、趋势、排行、对比和基于查询结果的计算分析。

本文档只负责语义模型选择、查询规划、DSL 模式/SQL 模式选择和结果分析；文件和产物任务遵循其各自的工具协议。

## 必读资料

- 当前数据库方言以 `list_execution_targets` 返回的 `dialect` 为准，调用该工具前不要假设方言；涉及原生 SQL 的日期、字符串、窗口等方言差异时，读取：
  `/mnt/skills/data-analyzing/references/analysis/dialect.md`
- DSL 查询只使用 `time_grain`，不要把 SQL 日期格式串写入 DSL。

## 必须遵守的总原则

- 所有业务查询先确认语义模型和字段定义，不凭常识编造指标、维度、表、字段、枚举或查询结果。
- 查询支持两种模式：**DSL 模式**和**SQL 模式**。这是查询路径术语，不是 `mode` 参数。
- 常规查询使用 DSL 模式；只有 DSL 确实无法表达查询结构时，才使用 SQL 模式。
- 查询执行统一走 `data_analyzing_tool`：DSL 模式在 `action_payload` 里传 `dsl`，SQL 模式传 `sql`，两者只能二选一。SQL 模式不做 validate_dsl 校验，不要传 `mode`。
- 指标、维度或业务口径本身不明确时必须澄清，不能用 SQL 绕过；字段已经确认、只是枚举值或时间存储格式未知时，才允许少量抽样确认。
- 一次 `data_analyzing_tool` 调用只能使用一个 `semantic_config_id`，不能混合多个模型的字段、分组或 JOIN。
- 机构范围由 `member_datasource_ids` 控制，不在 DSL 过滤机构名称、编码或 ID。
- 可用执行对象清单不进系统提示词：查询前先用 `data_analyzing_tool(action="list_execution_targets", action_payload={})` 实时获取当前用户可用清单，`member_datasource_ids` 只能从返回清单中选，不要凭记忆或历史轮次假设。

## 标准流程

### 1. 获取执行对象清单

1. 标准流程第一步：调用 `data_analyzing_tool(action="list_execution_targets", action_payload={})` 实时获取当前用户可用执行对象清单（机构名称、编码与 `member_datasource_id`）；返回同时携带当前方言 `dialect` 与智能问数说明 `query_context`，供后续语义模型检索与查询执行使用，清单与方言均以工具当次返回为准。清单为空时直接向用户说明无可用执行对象，不继续查询。
2. 用户明确指定一个机构：只传该机构对应的 `member_datasource_ids`。
3. 用户明确指定多个机构：只传这些机构对应的多个 ID。
4. 只有用户明确说全部机构、各机构或所有执行对象时，才传全部可用 ID。
5. 未指定机构且有多个可用机构时，默认传系统列表第 1 个机构，并在最终回答说明默认机构。
6. 机构名称或编码无法唯一匹配时，先澄清范围。

机构 ID 只放 `member_datasource_ids`，不要添加机构维度或机构过滤条件。

### 2. 检索语义模型

1. 当前用户可见（当前数据源组）的全部启用语义模型 Ossie YAML 已预物化到会话工作区 `/mnt/user-data/workspace/semantic/`，文件名为 `{模型名}__{id后缀}.yaml`。先用 `dir_list` 浏览该目录得到模型清单，结合用户业务问题圈定候选范围。
2. 多个模型可能包含相关内容、或模型名与业务术语对不上时，用 `file_grep(path="/mnt/user-data/workspace/semantic/", pattern="<指标名|维度名|业务关键词>")` 按内容定位包含关键词的模型，缩小候选范围。同一关键词无命中时，先换词再判无：至少覆盖三类换法——业务同义词（如“入金/来账/收款”）、中英文对照、词组拆分；三类换法全部落空才判定为「确认无命中」。
3. 一旦确认无命中，就是当前语义目录不含该术语：立即停止检索，用业务语言说明当前缺少的指标或模型并向用户澄清，不得用相同或相近关键词重复检索。**禁止为找模型逐个读取目录里的 YAML**——逐个读取不是检索手段，目录里文件很多时会耗尽上下文，且读不到关键词一样无解。
4. 只有 `file_grep` 命中后的候选模型才用 `file_read` 分页精读 YAML，比较各自的指标、维度、关系与口径后确定本轮使用的模型。不能根据模型名称、文件名或字段相似性猜测。
5. 手动选择的模型只是优先参考，不代表锁定。
6. 读取候选后确认当前配置不存在所需指标、维度或模型时，停止查询并说明当前缺少的能力；候选不唯一或读取后业务口径仍不清楚时，停止查询并只向用户提出一个最关键的业务澄清问题。

### 3. 通过查询门禁

调用 `data_analyzing_tool`（校验或执行查询）或 `calculator_tool` 前，必须确认：

- 当前使用的语义模型明确；
- 用户问题中的指标、维度、筛选对象和时间范围能与模型高置信匹配；
- 枚举含义、单位、时间格式、统计粒度和业务限定明确，或已按规则抽样确认；
- 没有多个合理解释，也没有依赖未维护的元数据。

“看起来像”“可能是”“通常指”“与某字段相近”都不算确认。

复购率、转化率、留存率、占比、客单价、有效率等比例或比率问题，必须使用模型中已维护且口径一致的指标；模型没有明确指标时，用户必须补充公式和统计周期，不能根据相近字段自行推导。

只有以下情况允许抽样：指标、维度和业务口径已经确认，只是状态值、枚举含义、时间存储格式或实际粒度未知。抽样应使用少量明细字段和明确的 `limit`，确认后再执行正式查询。

## 查询规划

- 查看明细、详情、列表或具体记录：`metrics` 必须为空，展示字段全部放入 `dimensions`。
- 统计、汇总、平均、总计、趋势、分组、排行等聚合意图明确时，才使用口径明确一致的 `metrics`。
- 用户原词精确命中指标或维度时优先使用精确项，不自动增加“平均、汇总、总额、数量”等限定词。
- 未指定时间范围时，总量和明细查询可以继续；趋势、排行、对比、Top N、分布分析必须先补充时间范围。
- 派生字段、截取、拼接、正则或复杂函数分组如果没有已维护维度，DSL 模式不可表达，不要退化为原字段分组，应评估 SQL 模式或澄清。
- 同比、环比、增长率必须拆为基础查询，结果对齐后使用 `calculator_tool` 计算差值和比例，禁止心算。全年或年度同比是年度总量对比；月度、季度或日同比按相同粒度对齐；环比只能比较相邻周期，不能把两个不同年度直接称为环比。
- 相对时间（今天、昨天、本周、本月、最近等）的真实日期范围确定，按系统提示词的相对时间规则先调用 `get_current_time`。

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
- `filters` 用于行级维度过滤。固定值过滤字段为 `dimension`、`operator`、`value`、`value_type`；`value_type` 只允许 `literal` 或 `sql_function`。单个值类型适用于全部值；多值过滤可传与 `value` 等长的类型列表，按位置逐项对应。`having` 只用于聚合结果过滤。
- `order_by.field` 只能是当前 DSL 已输出的指标或维度；普通查询不要设置 `offset`。

固定值过滤的操作符使用 SQL 风格：`=`、`!=`、`<>`、`>`、`<`、`>=`、`<=`、`IN`、`NOT IN`、`LIKE`、`NOT LIKE`、`STARTS WITH`、`ENDS WITH`、`BETWEEN`、`IS NULL`、`IS NOT NULL`、`EMPTY STRING`。不要使用 `equals`、`notEquals`。

复杂条件使用 `and` / `or` 树；如果 `or` 条件还要叠加其他条件，必须用顶层 `and` 包住整个组合。字段间比较优先使用结构化 `left` / `right`：外层 `operator` 只能是 `=`、`!=`、`<>`、`>`、`<`、`>=`、`<=`，`left` 只允许 `{"type":"dimension","name":"维度名"}`，`right` 可以是维度、字面量或 `binary` 表达式，`binary.operator` 只能是 `+`、`-`、`*`、`/`。不要把“维度B * 10”作为固定值字符串。

日期函数、计算式或只读子查询等无法用结构化 `right` 表达的右侧内容，可在普通维度过滤或 HAVING 中同时使用 `valueType: "expression"` 和非空 `rightExpression`。普通比较会直接使用该表达式；`IN` / `NOT IN` 会将表达式作为子查询或集合表达式并自动包裹括号。表达式叶子可以放入 `and` / `or` 树。禁止分号、SQL 注释和写操作；不能借此引用未确认的表列、绕过语义模型或弥补不明确的业务口径。涉及方言时先读取方言资料。

`having` 固定值过滤使用 `metric`、`operator`、`value`；指标间比较使用结构化 `left` / `right`，`left` 只允许 `{"type":"metric","name":"指标名"}`，`right` 只能是指标、字面量或 `binary` 表达式，`binary.operator` 只能是 `+`、`-`、`*`、`/`。不要把“指标B * 10”作为固定值字符串，也不要把指标结果条件放进 `filters`。

### DSL 示例

下面示例中的“指标A”“维度A”“字段A”只是结构占位，不能直接作为真实查询参数。实际调用前必须替换为本轮目录检索和 YAML 确认过的名称。

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

日期函数右侧表达式（复杂日期运算先按当前方言替换表达式）：

```json
{"metrics":["指标A"],"dimensions":[],"filters":[{"dimension":"时间维度","operator":"<","valueType":"expression","rightExpression":"CURRENT_DATE"}]}
```

只读子查询右侧表达式：

```json
{"metrics":[],"dimensions":["字段A"],"filters":[{"dimension":"字段A","operator":"IN","valueType":"expression","rightExpression":"SELECT 字段A FROM 已确认的表 WHERE 条件字段 = '值1'"}]}
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

### DSL 模式

1. 生成完整 DSL。
2. 调用 `data_analyzing_tool(action="validate_dsl", action_payload={"dsl": <同一份 DSL>, "semantic_config_id": "<同一模型>"})` 做校验/生成 SQL。
3. 校验成功后调用 `data_analyzing_tool(action="execute_query", action_payload={"dsl": <同一份 DSL>, "semantic_config_id": "<同一模型>", "member_datasource_ids": [...]})`。
4. 不要把 DSL 字段提升到 `action_payload` 之外，也不要修改校验返回的 SQL。

## SQL 模式

SQL 模式是 DSL 模式无法表达查询结构时的回退模式。只有满足该条件，才可以使用 `data_analyzing_tool(action="execute_query", action_payload={"sql": "..."})` 的 `sql` 参数。典型结构包括 CTE、子查询、窗口函数、多阶段聚合、自连接、`UNION`、`EXISTS`、复杂 `CASE WHEN` 或 DSL 无法表达的中间结果计算。

SQL 模式的 `action_payload` 必须同时传入 `semantic_config_id` 和 `member_datasource_ids`，不能同时传 `dsl`，也不做 validate_dsl 校验。

SQL 只能是只读查询。规划 SQL 时只能使用当前语义模型维度中维护的原始表名、原始列名和 `relationships` 中确认的关联关系；关联字段只用于连接路径，不能当作未暴露业务字段随意查询或过滤。已维护指标的表达式代表业务口径，不能擅自改写。不能因为指标、维度或口径不清就回退 SQL。

涉及时间格式或原生 SQL 方言时，按“必读资料”读取 `/mnt/skills/data-analyzing/references/analysis/dialect.md`；DSL 中仍然只写 `time_grain`，不要写 SQL 日期格式串。

## 多步分析与结果处理

- 查询成功后系统始终将完整结果保存到当前会话 `outputs/query_result/<result_id>.json`，工具只返回状态、行数/执行耗时、`result_id` 和 `result_path`，不把明细放入模型上下文。前端按 `result_id` 展示，后续分析使用 `file_read` 读取工具返回的 `result_path`。
- 同比和环比分别查询当前周期与对比周期的基础数据，按周期对齐后调用 `calculator_tool` 计算差值、增长率或比例；对比值为 0 或为空时不能输出普通百分比。
- 多个独立查询分别执行，最后统一总结；中间过程不必输出完整原始数据。
- 用户只要求查询、查看或列出结果时，不读取结果文件；前端会按 `result_id` 展示完整结果，直接说明查询已完成，不要再次粘贴表格、逐行明细、完整 JSON 或原始 SQL。
- 回答具体数值、形成业务结论，或进行比较、计算、排名、异常判断、空结果及执行对象状态判断时，必须先读取 `result_path` 中的实际数据；不得依据状态、SQL、路径、行数或 preview 推断。
- 结果较大时，先读取最小范围确认结构和规模；聚合、筛选、排序或计算优先重新执行更小的 DSL/SQL。只有必须检查原始明细时才按 `offset`/`limit` 分页读取，禁止为了分析把大型结果逐页全部加载进模型上下文。
- 读取实际结果后，空结果、失败结果和部分执行对象失败必须如实说明，不编造原因或数字。

## 失败处理

- 已确认当前模型、指标或维度不存在：说明当前配置缺少什么；候选不唯一或口径不清：向用户提出一个最关键的业务澄清问题。
- 工具提示当前配置不支持、模型不存在或缺少必要配置：停止当前查询分支并用业务语言说明缺口。
- 同类工具错误连续出现时停止重复尝试，改为修正输入、换合法路径或说明能力边界。
- 不向用户展示工具参数、语义模型配置、DSL、SQL 或原始工具返回，除非用户明确要求解释技术原因。
