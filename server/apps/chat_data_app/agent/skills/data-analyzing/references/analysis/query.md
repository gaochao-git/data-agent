
# 查询与结果分析

## 适用范围

处理自然语言数据查询、明细查看、统计汇总、趋势、排行、对比和基于查询结果的计算分析。

本文档只负责语义模型选择、查询规划、DSL 模式/SQL 模式选择和结果分析；文件和产物任务遵循其各自的工具协议。

## 必读资料

- 当前数据库方言以 `list_execution_targets` 返回的 `dialect` 为准，调用该工具前不要假设方言；涉及原生 SQL 的日期、字符串、窗口等方言差异时，直接参考本文档下方「查询方言对照」。
- DSL 查询只使用 `time_grain`，不要把 SQL 日期格式串写入 DSL。

## 必须遵守的总原则

- 所有业务查询先确认语义模型和字段定义，不凭常识编造指标、维度、表、字段、枚举或查询结果。
- 查询支持两种模式：**DSL 模式**和**SQL 模式**。这是查询路径术语，不是 `mode` 参数。
- 常规查询使用 DSL 模式；只有 DSL 确实无法表达查询结构时，才使用 SQL 模式。
- 查询执行统一走 `data_analyzing_tool`：DSL 模式在 `action_payload` 里传 `dsl`，SQL 模式传 `sql`，两者只能二选一。SQL 模式不做 validate_dsl 校验，不要传 `mode`。
- 指标、维度或业务口径本身不明确时必须澄清，不能用 SQL 绕过；字段已经确认、只是枚举值或时间存储格式未知时，才允许少量抽样确认。
- 过滤值必须与真实入库值一致，不凭用户原词或常识猜测存储格式：带 `column_dict.enum_values` 的维度只能使用字典内的 value；开放维度（城市、渠道、名称等无法穷举）不确定真实存储格式时（如用户说“北京”，库里可能存“北京市”或任意格式），先定向探查真实值域，探查确认后再写过滤条件。探查可将用户原词的常见变体（全称、简称、英文、编码）用 OR 合并为一次查询，每个 `LIKE` 必须独立完整，模式取紧凑变体、不用单字宽模式（`%京%` 会误捞“南京”），`LIMIT` 保留：`SELECT DISTINCT 字段 FROM 原始表 WHERE 字段 LIKE '%北京%' OR 字段 LIKE '%beijing%' OR 字段 LIKE '%BJ%' LIMIT 20`。英文变体注意方言大小写敏感性（MySQL 默认不敏感，PostgreSQL/Oracle 敏感，必要时按方言资料用 `LOWER()`）。禁止全表摸底。
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

### 2. 读本体语义层（存在才导航，缺失不影响后续）

本体层按对象类型持续逐步建设，**任何会话都可能没有本体清单**：指标语义层必然存在，本体层是可选增强，缺失时完全按原有指标语义层路线继续，不得报错、重试或向用户提及缺失。

1. 先用 `file_read` 读取 `/mnt/user-data/workspace/semantic/_ontology_catalog.yaml`。文件不存在：当前会话未物化本体层（此时 semantic/ 下会有 `_semantic_catalog.yaml` 语义清单文件），直接进入「3. 检索指标语义层」，不要重试或猜测路径。两份清单二选一：本体清单存在时不发语义清单文件，**`_semantic_catalog.yaml` 缺失属正常**；本体清单的 `models` 段是全部启用模型索引（含未建本体对象类型、不在 `object_types` 中的模型），用它的 `file` 定位取数模型即可。
2. 清单存在时，识别与用户问题相关的业务对象类型。**优先调 `ontology_tool` 的 `list_object_types`（无参数）**：返回轻量对象类型清单（对象类型API名/显示名/描述 + 各自绑定模型及快照文件名），一次调用同时完成「列对象类型」与「对象类型→模型」两跳；工具未注册（未启用开关）时回落清单检索。锁定模型后再按需升级 `locate_object_type`（见下）。直接读清单时：
   - 清单可能带 `truncated: true`：此时 `object_types` 只包含按对象类型名排序的前一部分，不能据此判定「没有相关对象类型」；先用 `file_grep` 在 `/mnt/user-data/workspace/semantic/` 下按对象类型名/业务关键词检索，找不到再走「3. 检索指标语义层」。
   - 命中对象类型：按该对象类型 `mappings` 的 `file` 字段（取数模型快照文件名，semantic/ 下）定位模型并 `file_read`（`semantic_config_id` 为辅助键；`column_mappings` 是「属性 → 模型维度名」的概念映射，物理列以模型 YAML 为准）；涉及多个对象类型时，用 `relation_types` 的 `from`/`to` 对象类型与 `from_columns`/`to_columns` 推导 join 路径，锁定候选后直接进入「4. 精读完整语义层」。
   - 命中对象类型、但拿不准「对象类型在哪个模型/属性对应哪个维度」或需要对象类型间关系类型、失修状态时，调用 `ontology_tool` 的 `locate_object_type`（`object_types` 传对象类型 API 名列表，可多个）：返回绑定模型（含快照文件名）、属性→维度对照与对象类型间关系类型，据此直接进入「4. 精读完整语义层」或 `execute_query`，不要靠 grep 模型文件猜定位；工具报错（范围）或未注册（未启用开关）时，才回落本文件既有检索路径。
   - 清单中没有任何相关对象类型：同样进入「3. 检索指标语义层」——这只说明本体尚未覆盖该对象类型，不代表系统能力缺失。
3. 本体层只负责「识别业务对象类型 + 选模型 + 定关联路径」；指标口径、维度枚举、时间格式等细节仍以对象类型映射指向的语义模型 YAML 为准，不能只凭清单描述直接写查询。
4. 跨对象类型的关联查询超出「一次调用一个 `semantic_config_id`」的约束，走 SQL 模式：join 条件优先参考本体 `relation_types` 的 `from_columns`/`to_columns`；**关系列是尽力翻译的物理列**：带 `unresolved: true` 时表示有列回退为原始属性/维度名，必须回读涉及模型的 YAML 确认表名与物理列后再写 join；一个对象类型绑定多个模型时，用 `from_columns_by_model`/`to_columns_by_model`（`{semantic_config_id: [物理列]}`）取对应模型的列。两个模型的口径都不明确时仍先澄清。

### 3. 检索指标语义层（清单优先，grep 过滤）

1. 当前用户可见（当前数据源组）的全部启用语义模型 Ossie YAML 已预物化到会话工作区 `/mnt/user-data/workspace/semantic/`，文件名为 `{模型名}__{id后缀}.yaml`；同目录的 `_semantic_catalog.yaml` 是系统生成的语义清单文件（每个启用模型一条 file/description/business_notes，file 即快照文件名）。**本体清单接管时 `_semantic_catalog.yaml` 不存在属正常**：此时用 `_ontology_catalog.yaml` 的 `models` 段取全部启用模型的 file/description/business_notes（`object_types` 只是已建本体对象类型的子集，可能覆盖不全）。
2. **第一步读清单**（本文档所称「清单」专指上文两个索引 YAML 文件本身，读取动作一律是 `file_read` 读取文件路径，**不是列目录文件名**）：`file_read` 整读 `_semantic_catalog.yaml`（本体清单接管时读其 `models` 段），得到全部启用模型的 file/description/business_notes 清单，按描述与用户问题的匹配度缩小候选模型。仅凭文件名判断模型相关性属于猜测——文件名不含 description/business_notes；也不要跳过清单直接 grep 全目录——先看全景清单才知道系统里有什么、避免关键词盲区。
3. 清单描述不足以确认字段与口径时，用 `file_grep(path="/mnt/user-data/workspace/semantic/", pattern="<指标名|维度名|业务关键词>")` 过滤关心内容：候选明确时用 `glob` 限定候选模型文件，候选不明时再扫全目录；英文关键词加 `ignore_case=true` 一次覆盖大小写变体；宽泛关键词命中过多（返回 `has_more=true`）时，先用 `output_mode="files_with_matches"` 看哪些文件命中、必要时再用 `output_mode="count"` 看热点分布，最后收敛 `pattern`/`glob` 后取默认 content 看具体行。
4. 同一关键词无命中时，先换词再判无：至少覆盖三类换法——业务同义词（如“入金/来账/收款”）、中英文对照、词组拆分；三类换法全部落空才判定为「确认无命中」。
5. 一旦确认无命中，就是当前语义清单不含该术语：立即停止检索，用业务语言说明当前缺少的指标或模型并向用户澄清，不得用相同或相近关键词重复检索。**禁止为找模型逐个读取清单里的 YAML**——逐个读取不是检索手段，清单覆盖的模型很多时会耗尽上下文，且读不到关键词一样无解。
6. 手动选择的模型只是优先参考，不代表锁定。

### 4. 精读完整语义层

1. 只有「清单描述筛出的候选」「`file_grep` 命中的候选」或「本体层 `mappings`/`locate_object_type` 指向的模型」才用 `file_read` 分页精读 YAML 全文，比较各自的指标、维度、关系与口径后确定本轮使用的模型。不能根据模型名称、文件名或字段相似性猜测。
2. 本体命中的跨对象关联：把涉及的全部模型 YAML 都读全，确认各自的原始表名、原始列名与已维护关系，join 路径以本体 `relation_types` 为先、以模型 YAML 的 `relationships` 为校验。
3. 精读后确认当前配置不存在所需指标、维度或模型时，停止查询并说明当前缺少的能力；候选不唯一或读取后业务口径仍不清楚时，停止查询并只向用户提出一个最关键的业务澄清问题。

### 5. 通过查询门禁

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
- 需要精确计算的数值（求和、差值、比例、单位换算等）一律调用 `calculator_tool` 并以工具返回结果为准，禁止心算或口算。
- 相对时间（今天、昨天、本周、上周、本月、最近等）需要确定实际日期或时间范围时，必须在生成 DSL、SQL 或调用数据查询工具前调用 `get_current_time(detail=false)`；需要时区、星期或 Unix 时间戳时改为 `detail=true`。不能根据模型自身时间、历史消息时间或猜测确定日期。

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
- `filters` 用于行级维度过滤。固定值过滤字段为 `dimension`、`operator`、`value`、`value_type`；`value_type` 只允许 `literal` 或 `sql_function`。单个值类型适用于全部值；多值过滤可传与 `value` 等长的类型列表，按位置逐项对应。`having` 只用于聚合结果过滤。**数组内多个平铺过滤条件之间固定按 `and` 连接**；同一字段的多个候选值（同义词、多关键词、枚举多选）命中任一即可时，必须写成 `or` 树，不要平铺多个条件（平铺即全部同时命中，等于必然查不到）。
- `order_by.field` 只能是当前 DSL 已输出的指标或维度；普通查询不要设置 `offset`。

固定值过滤的操作符使用 SQL 风格：`=`、`!=`、`<>`、`>`、`<`、`>=`、`<=`、`IN`、`NOT IN`、`LIKE`、`NOT LIKE`、`STARTS WITH`、`ENDS WITH`、`BETWEEN`、`IS NULL`、`IS NOT NULL`、`EMPTY STRING`。不要使用 `equals`、`notEquals`。

复杂条件（包括同一字段多个候选值任一命中的场景）使用 `and` / `or` 树表达组合逻辑，不要依赖平铺数组；如果 `or` 条件还要叠加其他条件，必须用顶层 `and` 包住整个组合。字段间比较优先使用结构化 `left` / `right`：外层 `operator` 只能是 `=`、`!=`、`<>`、`>`、`<`、`>=`、`<=`，`left` 只允许 `{"type":"dimension","name":"维度名"}`，`right` 可以是维度、字面量或 `binary` 表达式，`binary.operator` 只能是 `+`、`-`、`*`、`/`。不要把“维度B * 10”作为固定值字符串。

日期函数、计算式或只读子查询等无法用结构化 `right` 表达的右侧内容，可在普通维度过滤或 HAVING 中同时使用 `valueType: "expression"` 和非空 `rightExpression`。普通比较会直接使用该表达式；`IN` / `NOT IN` 会将表达式作为子查询或集合表达式并自动包裹括号。表达式叶子可以放入 `and` / `or` 树。禁止分号、SQL 注释和写操作；不能借此引用未确认的表列、绕过语义模型或弥补不明确的业务口径。涉及方言时见下方「查询方言对照」。

`having` 固定值过滤使用 `metric`、`operator`、`value`；指标间比较使用结构化 `left` / `right`，`left` 只允许 `{"type":"metric","name":"指标名"}`，`right` 只能是指标、字面量或 `binary` 表达式，`binary.operator` 只能是 `+`、`-`、`*`、`/`。不要把“指标B * 10”作为固定值字符串，也不要把指标结果条件放进 `filters`。

### DSL 示例

下面示例中的“指标A”“维度A”“字段A”只是结构占位，不能直接作为真实查询参数。实际调用前必须替换为本轮清单检索和 YAML 确认过的名称。

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

同一字段多个候选关键词（模糊匹配，命中任一即可）：

```json
{"metrics":[],"dimensions":["字段A"],"filters":[{"or":[{"dimension":"字段A","operator":"LIKE","value":"%关键词1%"},{"dimension":"字段A","operator":"LIKE","value":"%关键词2%"}]}]}
```

候选关键词检索且需叠加其他条件时，必须用顶层 `and` 包住整个组合：

```json
{"metrics":[],"dimensions":["字段A"],"filters":[{"and":[{"or":[{"dimension":"字段A","operator":"LIKE","value":"%关键词1%"},{"dimension":"字段A","operator":"LIKE","value":"%关键词2%"}]},{"dimension":"字段B","operator":">","value":10}]}]}
```

错误示例：

```json
{"metrics":["指标A"],"dimensions":[{"dimension":"时间维度","time_grain":"month"}]}
```

时间维度对象必须使用 `name`，不能使用 `dimension`；否则维度名可能被解析为空。

### DSL 模式

1. 生成完整 DSL。
2. 调用 `data_analyzing_tool(action="validate_dsl", action_payload={"dsl": <同一份 DSL>, "semantic_config_id": "<同一模型>"})` 做校验/生成 SQL。
3. 校验成功后调用 `data_analyzing_tool(action="execute_query", action_payload={"dsl": <同一份 DSL>, "semantic_config_id": "<同一模型>", "member_datasource_ids": ["<清单里的 member_datasource_id>"]})`。
   - `member_datasource_ids` 必须是 ID 字符串**数组**：单个机构传 `["ORG_CODE1"]`，多个机构传 `["ORG_CODE1", "ORG_CODE2"]`。
4. 不要把 DSL 字段提升到 `action_payload` 之外，也不要修改校验返回的 SQL。

## SQL 模式

SQL 模式是 DSL 模式无法表达查询结构时的回退模式。只有满足该条件，才可以使用 `data_analyzing_tool(action="execute_query", action_payload={"sql": "..."})` 的 `sql` 参数。典型结构包括 CTE、子查询、窗口函数、多阶段聚合、自连接、`UNION`、`EXISTS`、复杂 `CASE WHEN` 或 DSL 无法表达的中间结果计算。

SQL 模式的 `action_payload` 必须同时传入 `semantic_config_id` 和 `member_datasource_ids`，不能同时传 `dsl`，也不做 validate_dsl 校验。

SQL 只能是只读查询。规划 SQL 时只能使用当前语义模型维度中维护的原始表名、原始列名和 `relationships` 中确认的关联关系；关联字段只用于连接路径，不能当作未暴露业务字段随意查询或过滤。已维护指标的表达式代表业务口径，不能擅自改写。不能因为指标、维度或口径不清就回退 SQL。

涉及时间格式或原生 SQL 方言时，直接参考下方「查询方言对照」；DSL 中仍然只写 `time_grain`，不要写 SQL 日期格式串。

## 查询方言对照

本节只处理 Hive 和 MySQL 的方言差异。当前使用的方言以本轮系统数据上下文中的 `source_type` 为准；上下文缺失时遵循系统默认方言。不要根据历史消息或模型常识猜测方言。

### DSL 时间分组

DSL 时间分组始终使用：

```json
{"name":"已确认的时间维度","time_grain":"month"}
```

不要在 DSL 中写 `DATE_FORMAT`、`%Y-%m`、`yyyy-MM` 或其他 SQL 格式串。具体格式由 DSL 编译器根据数据源方言生成。

### Hive

- 按月：`DATE_FORMAT(time_col, 'yyyy-MM')`
- 按日：`DATE_FORMAT(time_col, 'yyyy-MM-dd')`
- 按小时：`DATE_FORMAT(time_col, 'yyyy-MM-dd HH:00')`
- 不要使用 MySQL 风格的 `%Y-%m`、`%Y-%m-%d`、`%H`。
- 如果结果出现 `%2024-%0`、`%2025-%0` 等形式，通常是方言格式串错误。修正 DSL 或方言上下文后重新走 DSL 编译，不要手工改写生成 SQL。

### MySQL

- 按月：`DATE_FORMAT(time_col, '%Y-%m')`
- 按日：`DATE_FORMAT(time_col, '%Y-%m-%d')`
- 按小时：`DATE_FORMAT(time_col, '%Y-%m-%d %H:00')`
- 不要使用 Hive 风格的 `yyyy-MM`、`yyyy-MM-dd`、`HH`。

### 原生 SQL 回退

- 原生 SQL 必须符合当前 `source_type` 的方言。
- 如果工具或服务端已经生成 SQL，不要手工替换其中的日期格式或函数。
- 看到方言上下文与生成结果明显不一致时，停止当前分支并说明方言上下文异常，不能把 Hive SQL 和 MySQL SQL 互相手工改写。

## 多步分析与结果处理

- 查询成功后系统始终将完整结果保存到当前会话 `outputs/query_result/<result_id>.json`，工具只返回状态、行数/执行耗时、`result_id` 和 `result_path`，不把明细放入模型上下文。前端按 `result_id` 展示，后续分析使用 `file_read` 读取工具返回的 `result_path`。
- 同比和环比分别查询当前周期与对比周期的基础数据，按周期对齐后调用 `calculator_tool` 计算差值、增长率或比例；对比值为 0 或为空时不能输出普通百分比。
- 多个独立查询分别执行，最后统一总结；中间过程不必输出完整原始数据。
- 用户只要求查询、查看或列出结果时，不读取结果文件；前端会按 `result_id` 展示完整结果，直接说明查询已完成，不要再次粘贴表格、逐行明细、完整 JSON 或原始 SQL。
- 回答具体数值、形成业务结论，或进行比较、计算、排名、异常判断、空结果及执行对象状态判断时，必须先读取 `result_path` 中的实际数据；不得依据状态、SQL、路径、行数或 preview 推断。
- 结果较大时，先读取最小范围确认结构和规模；聚合、筛选、排序或计算优先重新执行更小的 DSL/SQL。只有必须检查原始明细时才按 `offset`/`limit` 分页读取，禁止为了分析把大型结果逐页全部加载进模型上下文。
- 读取实际结果后，空结果、失败结果和部分执行对象失败必须如实说明，不编造原因或数字。
- 带过滤条件的查询返回 0 行时，先怀疑过滤值与入库值不匹配，不直接下“无数据”结论：对过滤字段按总原则定向探查真实值域（多变体 `OR` 合并），用探到的真实值重查；值域确认无匹配才可说明无数据。带过滤的聚合结果相对预期异常偏少时，同样先核查过滤值再下结论。
- 原始结果中超过 5 位的数字，除非用户明确要求或已经通过计算器校验，否则不要在回复中反复复述。

## 结果回答规范

- 单指标单值查询只输出一句结果，第一句直接回答问题。
- 多指标或多执行对象查询，提炼最有价值的 2-3 个关键数据点；部分执行对象失败或无数据时，必须说明对应执行对象状态。
- 禁止在最终回复中使用 markdown 表格、逐行明细列表、完整结果清单或大段复述 SQL 返回数据；只提炼关键结论。
- 洞察只能基于查询结果中已有字段和值；样本量少时只描述当前结果，不强行总结趋势或规律。
- 不擅自改变查询结果的单位；结果为空时明确说明本次查询没有返回数据，不编造原因。

## 失败处理

- 已确认当前模型、指标或维度不存在：说明当前配置缺少什么；候选不唯一或口径不清：向用户提出一个最关键的业务澄清问题。
- 工具提示当前配置不支持、模型不存在或缺少必要配置：停止当前查询分支并用业务语言说明缺口。
- 同类工具错误连续出现时停止重复尝试，改为修正输入、换合法路径或说明能力边界。
- 不向用户展示工具参数、语义模型配置、DSL、SQL 或原始工具返回，除非用户明确要求解释技术原因。
