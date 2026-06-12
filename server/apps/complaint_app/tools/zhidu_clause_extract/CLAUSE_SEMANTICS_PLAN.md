# 条款语义与本体落地方案

当前“制度原始输入 -> 条款语义理解 -> 本体查询 -> 冲突裁决”固定采用七层方案，后续实现与讨论都以此为准。

固定执行原则：
- 每一层产出后都要做轻量检查，检查本身属于方案设计的一部分，不是额外补充动作。
- 检查目标不是证明结果“完美”，而是判断“是否已经达到继续进入下一层的标准”。
- 如果某层结果已达到可继续推进标准，应进入下一层；只保留抽样复核，不在该层反复打磨。

补充说明：
- 术语增强是对第 `3/4` 层产物的补充加工，用于补 `definition / broader_terms / narrower_terms / related_terms`，不单独改写为第八层，也不改变固定七层口径。

## 七层总览

| 层级 | 输入 | 实现 | 输出 | 作用 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| 1. 制度抽取与主数据映射层 | `outline.xlsx`<br>`199个PDF` | 从 PDF 中抽取条款，再将制度目录元数据映射回制度文档 | `documents_mapped.jsonl` + `clauses.jsonl` | 从原始制度资料中产出后续条款语义和本体流程直接使用的制度元数据与条款数据 | 已实现 |
| 2. 原始提及层 | `clauses.jsonl` | 按条款调用大模型抽取主体、动作、对象、约束等原始语义提及 | `clause_semantics_mentions.jsonl` | 抽取每条条款中的原始语义提及，不做合并 | 已实现 |
| 3. 归并层 | `clause_semantics_mentions.jsonl` | 按 `doc_id` 和 `mention_category` 分批调用 LLM 先做制度内归一化，再基于制度内结果做跨制度归一化 | `clause_semantics_normalization.jsonl` | 对原始提及做同义/近义归并，形成规范名称与分组 | 已实现 |
| 4. 概念筛选层 | `clause_semantics_normalization.jsonl` | 按归并组调用 LLM 判断哪些概念适合进入后续规制表达与本体，并标记概念价值与本体角色 | `clause_semantics_concepts.jsonl` + `clause_semantics_concepts_review_noise.jsonl` | 将归并结果区分为核心概念、上下文概念和噪音概念，为后续规制表达和人工复核提供依据 | 已实现 |
| 5. 规制表达层 | `clause_semantics_mentions.jsonl`<br>`clause_semantics_normalization.jsonl`<br>`clause_semantics_concepts.jsonl` | 结合原始提及、归并结果与概念筛选结果生成稳定的规则表达结构 | `clause_rule_expressions.jsonl` | 基于提及、归并和概念筛选结果生成稳定规则表达；不得跳过归并层直接进入本层，除非需求方明确重新确认方案 | 已实现 |
| 6. 本体映射与查询层 | `clause_rule_expressions.jsonl`<br>`clause_semantics_normalization.jsonl`<br>`clause_semantics_concepts.jsonl`<br>`clause_semantics_terms.jsonl`<br>`documents_mapped.jsonl` | 按条款聚合相关信息并调用 LLM 生成受 schema 约束的 Turtle 片段，再由脚本稳定补入术语定义与上下位/相关概念关系；生成后用 SHACL 校验 `RuleExpression/Clause/Concept` 的必填关系和类型约束 | RDF/OWL 文件 + `SPARQL` 查询示例 + `SHACL` 校验报告 | 将规制表达映射为可查询本体，并在进入图数据库前发现脏连边和缺失关系 | 已实现到 `TTL/SPARQL/SHACL` 产出与校验 |
| 7. 图谱候选冲突裁决层 | `examples/conflict_candidates.sparql`<br>`Fuseki 查询端点`<br>`clause_semantics_ontology.ttl`（已导入图数据库） | 先用 SPARQL 召回冲突候选，再对每条候选调用 LLM 做 `conflict/non_conflict/uncertain` 裁决 | `ontology_conflict_candidates.jsonl` + `ontology_conflict_judgments.jsonl` + `ontology_conflict_judgments_summary.json` | 将“图谱召回”与“语义裁决”解耦，形成可复核、可回归的冲突判定结果 | 已实现 |

## 七层与 7+1 对标

说明：当前“七层”是工程流水线，`7+1` 是本体语义能力框架；二者不是逐层一一对应，而是“每层产物支撑哪些 7+1 能力”。

| 当前七层 | 对标 `7+1` | 对标说明 | 当前覆盖度 |
| --- | --- | --- | --- |
| 1. 制度抽取与主数据映射层 | 1 业务资源描述语义 | 产出制度/条款基础对象与元数据 | 高 |
| 2. 原始提及层 | 前处理层（非 `7+1` 直接层） | 提供术语、规则、权限语义的原始证据 | 中 |
| 3. 归并层 | 4 业务术语语义 | 形成 `canonical_name/alias` 统一术语入口 | 高（同义归并）/低（上下位） |
| 4. 概念筛选层 | 4 业务术语语义 + 3 业务分层约束语义 | 概念角色化，为本体类/属性约束做准备 | 中 |
| 5. 规制表达层 | 5 业务控制规则语义 + 7 权限管控策略语义（部分） | 抽取 `condition/constraint/effect`，沉淀规则和权限语义基础 | 中 |
| 6. 本体映射与查询层 | 1 业务资源描述语义 + 2 CRUD 数据操作语义 + 3 业务分层约束语义 + 4 业务术语语义 | RDF/OWL 落库与 SPARQL 查询能力落地 | 高 |
| 7. 图谱候选冲突裁决层 | +1 目标评估语义（部分） | 形成冲突判定与统计，支持评估闭环 | 中 |

按 `7+1` 能力视角的当前结论：
- `1` 对象描述语义：已覆盖
- `2` CRUD/SPARQL 语义：已覆盖
- `3` 分层约束语义：部分覆盖（有 `domain/range`，已补 SHACL 校验用于检查必填关系和类型约束）
- `4` 术语语义：部分覆盖（有规范名/别名，缺上下位/相关词/定义）
- `5` 控制规则语义：部分覆盖（有规制表达，缺更形式化规则层）
- `6` 流程活动与业务操作语义：基本未覆盖
- `7` 权限管控策略语义：部分覆盖（文本级有，策略级未形式化）
- `+1` 目标评估语义：部分覆盖（有 summary，缺标准评测集与回归门槛）

## 术语增强补充产物

输入：
- `clause_semantics_normalization.jsonl`
- `clause_semantics_concepts.jsonl`

实现：
- 对已归并且非 `noise_concept` 的概念组调用 LLM，补充术语定义、上位词、下位词、相关词；其中 `aliases` 直接沿用归并组成员提及，不重复再做同义归并。

输出：
- `clause_semantics_terms.jsonl`

作用：
- 增强 `7+1` 中第 `4` 类术语语义能力，为后续 `TTL/OWL` 中的术语关系挂载、别名查询、概念解释和上下位查询做准备。

## JSONL 结构定义

### `documents_mapped.jsonl`

每条记录至少包含：
- `doc_id`：制度文档唯一标识，用于把制度元数据、条款、规制表达和本体实例串起来
- `title`：制度标题，作为制度主显示名称
- `file_name`：原始 PDF 文件名，便于回溯原文件
- `file_path`：原始 PDF 文件路径，便于回溯原文件位置
- `category`：当前制度分类，用于分组、筛选和后续分析
- `版块`：制度所属版块，是制度目录中的上层分类
- `领域`：制度所属领域，是制度目录中的细分分类
- `制度文号`：制度正式文号，用于区分制度版本和来源
- `制度级别`：制度层级或文种，如章程、办法、细则等
- `发布时间`：制度发布时间，用于时序分析和版本判断
- `主责部门`：制度主责部门，用于职责归属和组织视角分析
- `outline_match_status`：该制度与 `merged_outline.json` 的映射状态，如 `matched`
- `outline_match_confidence`：映射置信度，表示映射结果可信程度
- `outline_match_reason`：映射理由，说明为什么匹配到该条制度主数据
- `outline_title`：映射到的制度目录标题，便于人工核对

### `clauses.jsonl`

每条记录至少包含：
- `doc_id`：制度文档唯一标识，用于把条款回挂到具体制度
- `category`：制度分类，用于条款级分组和筛选
- `title`：制度标题，便于人工识别条款来源
- `file_name`：原始 PDF 文件名，便于回溯原文件
- `file_path`：原始 PDF 文件路径，便于回溯原文件位置
- `chapter_no`：章编号，用于定位条款所在章节结构
- `chapter_title`：章标题，用于补充条款所属主题语境
- `section_no`：节编号，用于定位更细的章节结构
- `section_title`：节标题，用于补充条款所属主题语境
- `article_no`：条款编号，是条款级分析和规制表达的主索引
- `article_text`：条款正文，是原始提及抽取和规制表达生成的直接证据文本
- `items`：条款内进一步拆出的分项列表，用于辅助理解条款内部枚举结构
- `source_pages`：条款来源页码列表，用于回溯 PDF 原页位置

### `clause_semantics_mentions.jsonl`

每条记录至少包含：
- `doc_id`：制度文档唯一标识，用于把条款提及回挂到具体制度
- `title`：制度标题，便于人工识别和后续展示
- `article_no`：条款编号，用于定位提及来源条款
- `article_text`：条款原文，是原始提及抽取的直接证据文本
- `subject_mentions`：条款中的主体原始提及，即承担义务、享有权利、作出动作、参与程序的主体表达；这一层保留原始表达，不做归一化。例子：`董事会`、`公司`、`会计师事务所`、`股东`
- `action_mentions`：条款中的动作原始提及，即行为、程序行为、权力行为、义务行为等；并列动作应分别抽出，不要提前合并。例子：`决定`、`聘用`、`解聘`、`进行表决`、`陈述意见`
- `object_mentions`：条款中的对象原始提及，即动作作用对象、受影响对象、被处理对象、权利义务指向对象或事项对象。例子：`会计师事务所`、`法人财产`、`股东会特别决议事项`
- `constraint_mentions`：条款中的约束性原始提及，即条件、适用范围、触发情形、例外、程序要求、门槛、规范力度、标准要求等；对象类内容不应放入本字段。例子：`应当`、`经批准后`、`过半数通过`、`在5个工作日内`

### `clause_semantics_normalization.jsonl`

每条记录至少包含：
- `normalization_group_id`：归并分组唯一标识，用于稳定引用同一组同义/近义提及
- `mention_category`：归并类别，标识该组属于 `subject_mentions`、`action_mentions`、`object_mentions` 还是 `constraint_mentions`
- `canonical_name`：该归并组采用的规范名称，作为后续查询和规制表达的统一入口
- `member_mentions`：归并进该组的原始提及列表，保留原词和来源，便于追溯
- `reason`：归并理由，说明为什么这些提及可以归成一组

归并层检查口径：
- 允许在结果达到“可继续推进”标准后进入下一层，不要求在本层追求完全收敛
- 重点抽检 `subject_mentions`、`action_mentions`、`object_mentions`、`constraint_mentions` 四类归并组是否存在明显误合并
- 重点抽检 `single_member_group`、`context_concept`、`noise_concept` 等边界项是否符合当前经验口径
- 检查目标是识别会直接污染下一层的明显问题，而不是在本层做无限迭代

### `clause_semantics_concepts.jsonl`

每条记录至少包含：
- `normalization_group_id`：继承归并层分组标识，用于把概念筛选结果回挂到具体归并组
- `mention_category`：概念所属类别，标识该组来自 `subject_mentions`、`action_mentions`、`object_mentions` 还是 `constraint_mentions`
- `canonical_name`：归并后的规范名称，是概念筛选判断的核心对象
- `member_mentions`：该概念组包含的原始提及列表，供人工复核与后续解释使用
- `reason`：归并层给出的归并理由，作为概念筛选的输入背景
- `concept_status`：概念筛选状态，取值为 `core_concept`、`context_concept`、`noise_concept`
- `ontology_role`：该概念更适合作为哪类本体角色，取值为 `subject`、`action`、`object`、`context`、`discard`
- `screen_reason`：概念筛选理由，说明为什么这样判定

概念筛选层检查口径：
- 重点检查 `noise_concept` 是否主要清理掉编号残片、句式壳子、格式碎片和明显无独立语义价值的内容
- 重点检查 `core_concept` 和 `context_concept` 的边界是否基本稳定，尤其关注泛化对象和单元素组
- 只要大多数概念已经能稳定区分“核心概念 / 上下文概念 / 噪音概念”，即可进入规制表达层

### `clause_rule_expressions.jsonl`

每条记录至少包含：
- `doc_id`：制度文档唯一标识，用于把规则表达回挂到具体制度
- `title`：制度标题，便于人工识别和后续展示
- `article_no`：条款编号，用于定位该规则表达对应的原始条款
- `subject[]`：经过归并后的规则主体列表，即该规则中承担规范角色的主体；使用数组是因为一条规则可能存在多个主体
- `actions[]`：经过归并后的规则动作列表，即该规则要求、允许、禁止或程序化处理的动作；使用数组是因为一条规则可能包含多个并列动作
- `object[]`：规则动作指向的对象列表，即被处理、被决定、被约束、被作用的对象；使用数组是因为一条规则可能指向多个对象。例子：`会计师事务所`、`公司审计业务`、`董事候选人`
- `business_context`：该规则所处的业务语境或事项场景，是对整条规则主题的简要概括，不等于原文摘抄。例子：`解聘会计师事务所表决程序`、`公司审计业务聘任决策`
- `condition[]`：规则成立、触发、适用的条件列表；使用数组是为了兼容多条件条款，即使当前只有一个条件，也保持数组结构。例子：`就解聘会计师事务所进行表决时`、`发生重大风险时`、`金额超过1000万元时`
- `constraint[]`：规则中的约束性表达列表，体现规范力度、程序要求、约束方式；使用数组是因为一条规则可能同时存在多个约束表达。例子：`应当允许`、`由董事会决定`、`不得擅自变更`
- `effect`：对该规则规制结果的简要概括，即这条规则最终要求达到什么规范效果，不是原文逐字摘抄。例子：`董事会是该事项的决策主体`、`会计师事务所在该程序中享有陈述意见机会`
- `evidence_text`：支持该规制表达的原始条款证据文本，用于追溯、复核和查询结果解释，是规制表达与原文之间的证据连接

### `clause_semantics_terms.jsonl`

每条记录至少包含：
- `normalization_group_id`：术语增强结果对应的归并组 ID，用于与概念组、本体概念稳定挂接
- `mention_category`：该术语来自 `subject_mentions`、`action_mentions`、`object_mentions` 或 `constraint_mentions`
- `canonical_name`：归并后的规范名称，是该术语对象的主名称
- `aliases`：同义或近义别名列表，直接来自归并组成员提及的去重结果，不重新做一轮同义归并。例子：`平台部`、`基础平台`、`平台部门`
- `concept_status`：继承概念筛选层结果，表示该术语对象当前是 `core_concept` 还是 `context_concept`
- `ontology_role`：继承概念筛选层的本体角色判断，如 `subject`、`action`、`object`、`context`
- `definition`：该术语在制度语境中的一句简短定义，用于解释概念含义。例子：`公司治理中负责重大事项决策的机构`
- `broader_terms`：该术语的上位词列表，即更一般的概念。例子：`治理机构`、`内部管理部门`
- `narrower_terms`：该术语的下位词列表，即更具体的概念；若当前输入不足以支持，应为空数组
- `related_terms`：与该术语强相关但不同义的概念列表，用于补充概念网络。例子：`监事会`、`股东会`
- `normalization_reason`：归并层给出的归并理由，用于追溯为什么形成该术语对象
- `screen_reason`：概念筛选层给出的筛选理由，用于解释为什么该术语被保留

### `ontology_conflict_candidates.jsonl`

每条记录至少包含：
- `candidate_id`：冲突候选唯一标识，用于追踪与去重
- `query_label`：候选来源的 SPARQL 查询标签（如 `Query 1`）
- `query_index`：候选来源查询序号
- `row`：SPARQL 原始返回字段集合（候选证据）

### `ontology_conflict_judgments.jsonl`

每条记录至少包含：
- `candidate_id`：对应候选 ID
- `query_label`：候选来源查询标签
- `row`：原始候选字段
- `verdict`：裁决结果，取值为 `conflict`、`non_conflict`、`uncertain`
- `confidence`：裁决置信度，取值为 `high`、`medium`、`low`
- `conflict_type`：冲突类型（若可判定）
- `reason`：裁决理由
- `key_differences`：关键差异点列表
- `error`：模型解析失败或其他异常信息（为空表示正常）

### `ontology_conflict_judgments_summary.json`

汇总字段至少包含：
- `candidate_count`：候选总数
- `judgment_count`：裁决总数
- `error_count`：失败数
- `verdict_counts`：按 `verdict` 的分布
- `confidence_counts`：按 `confidence` 的分布
- `query_candidate_counts`：按查询标签聚合的候选数量
