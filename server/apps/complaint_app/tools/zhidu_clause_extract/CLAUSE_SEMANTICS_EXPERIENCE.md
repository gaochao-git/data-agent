# 条款语义实验经验记录

本文档用于沉淀“原始提及 -> 归并 -> 概念筛选”阶段的阶段性观察、判断口径与人工复核经验，避免分析结果只停留在对话里。

## 2026-04-15 两个制度样本

### 1. 归并层结果

输入摘要：
- `doc_count=2`
- `mention_count=2114`
- `mention_category_counts.subject_mentions=671`
- `mention_category_counts.action_mentions=772`
- `mention_category_counts.constraint_mentions=671`

输出摘要：
- `doc_level_group_count=786`
- `normalization_group_count=634`
- `doc_level_group_category_counts.subject_mentions=122`
- `doc_level_group_category_counts.action_mentions=210`
- `doc_level_group_category_counts.constraint_mentions=454`
- `normalization_group_category_counts.subject_mentions=90`
- `normalization_group_category_counts.action_mentions=157`
- `normalization_group_category_counts.constraint_mentions=387`

经验判断：
- 制度内归一化压缩明显，说明同一制度内部确实存在较多别名、近义动作和重复约束表达。
- 跨制度归一化仍然有效，但压缩幅度低于制度内归一化，符合预期。
- `subject_mentions` 和 `action_mentions` 更适合做归并，收益明显。
- `constraint_mentions` 归并幅度最小，说明约束表达更依赖上下文，天然更分散。

### 2. 概念筛选层结果

输入摘要：
- `concept_group_count=634`
- `single_member_group_count=441`
- `concept_status_counts.core_concept=338`
- `concept_status_counts.context_concept=275`
- `concept_status_counts.noise_concept=21`
- `ontology_role_counts.subject=75`
- `ontology_role_counts.action=189`
- `ontology_role_counts.object=61`
- `ontology_role_counts.context=288`
- `ontology_role_counts.discard=21`
- `mention_category_counts.subject_mentions=90`
- `mention_category_counts.action_mentions=157`
- `mention_category_counts.constraint_mentions=387`

经验判断：
- `634` 个概念组中只有 `21` 个被判为 `noise_concept`，说明这一层没有过度删数据，主要是在清理明显不适合作为概念的残片。
- `single_member_group_count=441` 并不自动代表结果不合理。很多稳定角色或对象即使只出现一次，仍然应该保留。
- `context_concept=275` 且 `ontology_role=context=288`，说明大量约束表达更适合作为上下文或条件侧概念，而不是主体或动作。
- `action=189` 明显高于 `subject=75` 和 `object=61`，说明当前样本中动作类概念更集中、更容易进入后续规制表达。
- `constraint_mentions=387` 在最终概念中仍占很高比例，提示后续规制表达层要重点处理“条件/约束/上下文”而不是只盯主体和动作。

### 3. 噪音概念的当前判断口径

本轮筛出的 `21` 个 `noise_concept` 整体合理，主要集中在以下几类：

明显残片或虚词：
- `为`
- `执`
- `后`
- `切实`
- `及时`
- `相应的`

纯规范连接语或约束语气，不适合作为概念：
- `必须`
- `应当/应（义务要求）`
- `应当如实`

句式壳子，不是稳定概念：
- `以下事项`
- `有一下情形之一的`
- `至少包括一下内容`
- `记载下列事项`
- `通知中应列明`

过长的整句片段，不适合作为概念：
- `根据《xxx发》等xxxxx的规定`
- `用于上报备案与公司存档15份`
- `本章程第二十条第（一）项规定的`

需要继续人工留意的边界项：
- `解释`
- `包括`
- `本公司的`

经验判断：
- 这类概念更像语法片段、格式残片、句式壳子或约束语气，不适合直接进入后续本体或规制表达。
- 但边界项不能一刀切，应继续通过人工抽查建立更稳定的筛选白名单和黑名单。

### 4. 单元素组的当前判断口径

当前口径：
- 不能因为 `member_mentions` 只有 `1` 个，就自动判成噪音。
- 判断重点不是“组内成员数”，而是“这个概念是否稳定、是否有复用价值”。

应保留的典型单元素组：
- `债权人`
- `党务工作人员`

应谨慎复核或倾向丢弃的单元素组：
- `（1）`
- 纯编号项
- 明显句式残片
- 无独立语义价值的修饰性短语

### 5. 泛化对象的当前判断口径

例如：
- `他人`

经验判断：
- `他人` 不是噪音，它表达的是一种泛化外部对象或受益方。
- 这类概念通常不适合作为强主体实体，但适合作为 `object` 或 `context` 侧概念保留。
- 与 `他人` 相似的，还可能包括：
  - `本人`
  - `第三方`
  - `其他单位`
  - `其他组织`
  - `其他人员`
  - `有关人员`
  - `债权人`
  - `债务人`
  - `客户`
  - `供应商`

注意：
- 这些词不能一概视为噪音。
- 也不能一概当成强本体实体。
- 更合理的方式是按“稳定角色 / 泛化对象 / 上下文指代”继续区分。

### 6. 对后续规制表达层的启示

基于目前结果，后续规制表达层应重点注意：

- 不要只围绕 `subject` 和 `action` 设计，`context` 和约束表达占比很高。
- `constraint_mentions` 在概念层仍然很多，说明后续 `condition[]`、`constraint[]`、`business_context` 必须认真设计。
- 噪音概念比例不高，说明可以先以 `core_concept + context_concept` 为主要输入推进规制表达，再持续人工抽查噪音边界。
- 单元素组不能简单删除，应该继续依赖“概念价值”而不是“组内成员数”做判断。

### 7. 对 summary 指标的补充解读

本轮 `summary` 的进一步解读如下：

- `core_concept=338`、`context_concept=275`、`noise_concept=21`
  说明概念筛选层的主要作用不是大规模删数据，而是做“概念分层”。当前主流结果仍然是可继续进入后续流程的概念，只是其中相当一部分更适合作为上下文或约束侧概念使用。

- `single_member_group_count=441`
  说明当前样本中存在大量只出现一次的概念组。这个数字本身不能直接作为负面指标，更合理的理解是：很多稳定角色、对象或约束表达在样本中只出现一次，但仍然有保留价值。

- `ontology_role_counts.context=288`
  说明当前两份制度里，最主要的不是“治理主体”或“业务对象”，而是大量条件、程序、上下文和约束性概念。这直接提示后续规制表达层不能只设计成 `subject + action + object`，必须认真承载 `business_context`、`condition[]`、`constraint[]` 这类信息。

- `mention_category_counts.constraint_mentions=387`
  进一步验证约束表达在当前样本中占比很高，也说明约束表达天然更丰富、更分散、更依赖上下文。后面如果规制表达层做得太扁，会丢掉大量制度语义。

## 2026-04-16 两个制度样本的规制表达层

### 1. 规制表达层结果

输入摘要：
- `doc_count=2`
- `article_count=141`

输出摘要：
- `rule_expression_count=504`

经验判断：
- 平均每条条款产出约 `3.57` 条规制表达，说明模型已经在主动把一条条款拆成多个相对独立的规则单元，而不是机械地“一条条款只出一条规则”。
- 这个数量本身不算异常，因为制度条款里经常同时包含主体、程序、条件、义务、结果等多个规制点。
- 后续检查重点不应该只盯“数量多不多”，而应看是否存在明显的“漏拆”或“过拆”。

### 2. 当前示例的初步判断

示例：
- `subject=["公司"]`
- `actions=["登记成立"]`
- `object=[]`
- `business_context="公司设立登记"`
- `condition=[]`
- `constraint=["依法"]`
- `effect="确立公司依法完成注册登记"`
- `evidence_text="公司经xxxx审批，由xxxxx组织筹建，并依法登记成立。"`

经验判断：
- 这条规制表达整体方向是对的，至少已经抓住了“公司依法登记成立”这一核心规制语义。
- `object=[]` 本身不一定是问题，因为“登记成立”在这里更像不需要显式对象的动作表达。
- `constraint=["依法"]` 基本合理，说明模型把“依法”识别成规范性约束，而不是当成普通修饰词。
- `business_context="公司设立登记"` 也基本成立，至少没有明显跑偏。

### 3. 当前示例暴露的检查点

这个示例同时也提示了后续需要重点检查的几个问题：

- 是否存在“漏拆”：
  原文里除了“依法登记成立”，还包含“经……审批”“由……组织筹建”等语义。如果这些没有被拆成其他规制表达，说明模型可能只抓到了条款后半句，漏掉了前半句的程序或主体信息。

- `effect` 是否过于抽象：
  例如“确立公司依法完成注册登记”虽然不算错，但更像概括句，而不是最稳定、最利于后续比较的规制效果表达。后续要留意 `effect` 是否经常写得太虚。

- `business_context` 是否稳定：
  当前例子里的“公司设立登记”基本可接受，但后续要观察是否会出现“业务语境”和“动作本身”重复、或者概括得过长的问题。

- `constraint` 与 `condition` 的边界：
  当前这条里 `依法` 放在 `constraint` 是合理的；但如果后续出现“经批准后”“在……时”“符合……条件的”，需要继续观察模型是否能稳定区分 `condition[]` 和 `constraint[]`。

### 4. 对下一轮抽检的建议

规制表达层下一轮抽检建议重点看：

- 一条条款是否被拆成了足够的规则单元，尤其关注是否漏掉前半句或并列句
- `subject[]` 是否抓到了真正承担规范角色的主体
- `actions[]` 是否保留了核心动作，且没有把不同动作错误混成一个
- `object[]` 为空时是否真的合理，还是因为对象漏抽
- `condition[]` 和 `constraint[]` 是否区分基本稳定
- `effect` 是否写得过于抽象、空泛或难以比较

## 2026-05-09 zhidu_0430 全量批次

### 1. 归并层结果

输入摘要：
- `doc_count=182`
- `mention_count=94551`
- `mention_category_counts.subject=12676`
- `mention_category_counts.action=27150`
- `mention_category_counts.object=35330`
- `mention_category_counts.constraint=19395`

输出摘要：
- `doc_level_group_count=53983`
- `normalization_group_count=5513`
- `doc_level_group_category_counts.subject=6168`
- `doc_level_group_category_counts.action=14630`
- `doc_level_group_category_counts.object=19675`
- `doc_level_group_category_counts.constraint=13510`
- `normalization_group_category_counts.subject=900`
- `normalization_group_category_counts.action=566`
- `normalization_group_category_counts.object=2992`
- `normalization_group_category_counts.constraint=1055`

经验判断：
- `doc_count=182` 与本批制度规模基本一致，说明本轮数据进入归并层的覆盖范围正常。
- `mention_count=94551` 表明原始提及量很大，后续 `05/06/06b` 执行时间较长属于正常现象。
- `doc_level_group_count=53983` 压缩到 `normalization_group_count=5513`，说明跨制度归并有效，已经把大量制度内表达压缩成全局概念组。
- `object` 类从 `19675` 个制度内组压缩到 `2992` 个全局组，仍然是数量最多的类别，说明本批制度的对象、事项、材料、范围类表达非常丰富。
- `constraint=1055` 不算异常，说明约束类表达虽然多，但经过归并后仍处在可进入概念筛选层的规模。
- 当前 `05` 结果可以进入 `06`；后续性能压力主要来自 `object` 类概念，而不是 `constraint` 类概念。

可用于报告的结论：
- 本批 `182` 份制度共抽取 `94551` 条语义提及，经制度内归并与跨制度归并后，形成 `5513` 个全局归并组，压缩效果明显。
- 全局归并结果中，`object` 类概念占比最高，说明制度文本中大量冲突线索可能集中在事项、对象、材料、适用范围等对象侧表达上。

### 2. 概念筛选层结果

输入摘要：
- `concept_group_count=5513`
- `single_member_group_count=3320`
- `mention_category_counts.subject=900`
- `mention_category_counts.action=566`
- `mention_category_counts.object=2992`
- `mention_category_counts.constraint=1055`

输出摘要：
- `concept_status_counts.core_concept=2551`
- `concept_status_counts.context_concept=2714`
- `concept_status_counts.noise_concept=248`
- `ontology_role_counts.subject=713`
- `ontology_role_counts.action=568`
- `ontology_role_counts.object=2215`
- `ontology_role_counts.context=1769`
- `ontology_role_counts.discard=248`

经验判断：
- `noise_concept=248 / 5513`，约 `4.5%`，占比不高，说明概念筛选层没有大规模误删概念，主要是在清理明显噪音。
- `core_concept=2551`、`context_concept=2714`，两者规模接近，说明这批制度不只是主体、动作、对象，还包含大量上下文、条件、范围、约束侧语义。
- `ontology_role_counts.object=2215` 是最大类别，与归并层中 `object` 数量最多一致，说明对象侧概念是本批制度语义结构中的主要部分。
- `ontology_role_counts.context=1769` 数量较高，说明后续规制表达和本体映射不能只依赖 `subject/action/object`，必须保留 `business_context/condition/constraint` 等上下文表达。
- `single_member_group_count=3320` 较高，但不能直接视为质量问题；制度中的部门、事项、对象、材料、适用范围等概念可能只出现一次，但仍然具有业务含义。
- 当前 `06` 结果整体可用，可以进入 `06b` 术语增强层。

可用于报告的结论：
- `06` 概念筛选后，`5513` 个全局归并组中保留 `5265` 个有效概念，其中 `2551` 个为核心概念、`2714` 个为上下文概念，仅 `248` 个被识别为噪音概念。
- 本批制度的概念结构呈现“对象侧概念多、上下文概念重”的特点，这提示后续冲突识别不能只比较主体和动作，还需要关注适用对象、业务语境、条件和约束差异。

## 2026-04-24 八个制度样本的 06b/07 结果

### 1. 06b 术语补全层结果

输入摘要：
- `term_count=992`
- `definition_filled_count=992`
- `alias_count=1172`
- `broader_term_count=895`
- `narrower_term_count=93`
- `related_term_count=1441`
- `mention_category_counts.subject_mentions=112`
- `mention_category_counts.action_mentions=232`
- `mention_category_counts.constraint_mentions=648`
- `concept_status_counts.core_concept=602`
- `concept_status_counts.context_concept=390`
- `ontology_role_counts.context=496`
- `ontology_role_counts.object=210`
- `ontology_role_counts.subject=45`
- `ontology_role_counts.action=241`

经验判断：
- `term_count=992` 的规模正常，说明术语补全层没有明显失控。
- `concept_status_counts` 里只有 `core_concept=602`、`context_concept=390`，没有 `noise_concept`，这是合理现象，因为 06b 属于 06 之后的术语补全层，不应再混入噪音概念。
- `mention_category_counts.constraint_mentions=648` 明显高于 `subject_mentions=112` 和 `action_mentions=232`，说明当前制度语料仍然以约束类表达为主，这与制度文本的语言特征一致。
- `ontology_role_counts.context=496` 最高，也符合当前数据特征：大量约束类术语更适合作为上下文、条件、程序或限制语义，而不是强行映射成主体或动作。
- `broader_term_count=895` 明显高于 `narrower_term_count=93`，说明当前术语关系更偏“向上归类”，不偏细粒度展开；这不是错误，但意味着术语层整体更适合检索、归类和主题组织，不适合直接理解为细粒度层级已经充分展开。
- `related_term_count=1441` 偏高，说明术语间弱关联较丰富；后续如果用于召回和浏览通常是利好，但如果直接用于严格推理，需要继续关注是否存在“相关关系过宽”的问题。

## 2026-05-09 全量制度批次的 06b 结果

### 1. 06b 术语补全层结果

输出文件：
- `06b_clause_semantics_terms/clause_semantics_terms.jsonl`
- `06b_clause_semantics_terms/clause_semantics_terms_summary.json`

输入摘要：
- `term_count=5265`
- `definition_filled_count=5265`
- `alias_count=15172`
- `broader_term_count=1390`
- `narrower_term_count=197`
- `related_term_count=4231`
- `mention_category_counts.subject=862`
- `mention_category_counts.action=522`
- `mention_category_counts.object=2896`
- `mention_category_counts.constraint=985`
- `concept_status_counts.context_concept=2714`
- `concept_status_counts.core_concept=2551`
- `ontology_role_counts.subject=713`
- `ontology_role_counts.object=2215`
- `ontology_role_counts.context=1769`
- `ontology_role_counts.action=568`

经验判断：
- `term_count=5265`，规模不小，说明当前全量批次已形成足够的术语实体基础，可以继续推进后续规制表达与本体层。
- `definition_filled_count=5265`，说明每个术语都补到了定义，这是 06b 结果完整性的正向信号。
- `alias_count=15172`，平均每个术语约 `2.9` 个别名，有利于后续检索、召回和同义表达归一。
- `mention_category_counts.object=2896` 占最大，与制度条款中对象、事项、材料、流程对象等概念偏多的文本特征一致。
- `core_concept=2551`、`context_concept=2714` 接近对半，说明核心概念和上下文概念都很重要，后续冲突识别不能只看主体和动作，还要保留适用范围、条件、约束、业务语境等上下文语义。
- `broader_term_count=1390`、`related_term_count=4231` 说明术语关系已经铺开；`narrower_term_count=197` 相对偏少，若后续要做层级导航或上下位推理，需要关注细粒度下位关系是否不足。

可用于报告的结论：
- 当前 06b 结果整体可用，可以进入下一步 `07_clause_rule_expressions`。
- 这批制度呈现“对象侧概念多、上下文概念重、相关关系丰富、下位关系偏少”的结构特征。
- 后续 GraphDB 导入仍应使用 `08_clause_semantics_ontology/clause_semantics_ontology.graphdb_ready.ttl`，不要直接导入原始 `clause_semantics_ontology.ttl`。

### 2. 06 噪音概念样本的当前口径

本轮人工抽看的 `clause_semantics_concepts_review_noise.jsonl` 中，典型 `canonical_name` 包括：
- `不含`
- `严明`
- `为`
- `交`
- `做到`
- `做成`
- `包括`

经验判断：
- 这批样本整体支持当前 `noise_concept` 的筛选方向。
- 它们更像功能词、残片词、句式连接片段或不稳定短语，不适合作为后续规制表达和本体映射的稳定概念。
- 因此，本轮 `noise_concept` 不是“噪音过多”，而是“拦截对象基本正确”。

### 3. 07 规制表达层结果

输入摘要：
- `doc_count=8`
- `article_count=298`
- `rule_expression_count=1013`

质量检查结果：
- `总规则数=1013`
- `空规则数=0`
- `缺subject规则数=4`（`0.39%`）
- `缺actions规则数=1`（`0.10%`）
- `subject/actions都缺规则数=0`
- `整条款都无subject/actions的条款数=0`

经验判断：
- 平均每条条款产出约 `3.4` 条规制表达，数量处于正常区间，没有明显“过低塌陷”或“异常爆炸”。
- `空规则数=0`、`subject/actions都缺规则数=0` 是非常强的正向信号，说明 07 没有生成结构性无效结果。
- `缺subject=0.39%`、`缺actions=0.10%` 的比例都很低，说明 06 的概念筛选和去噪没有把 07 明显打坏。
- 因此，这一轮更合理的结论不是“06 噪音偏多导致 07 质量风险大”，而是“06 的噪音过滤方向基本正确，07 的规制表达质量健康，可继续推进到 08 本体层”。

### 4. object_mentions 改造口径

背景判断：
- 当前 `constraint_mentions` 容易混入动作对象、属性对象、事项清单等内容，导致后续 `canonical_name` 出现长枚举、规则短句、字段项或修饰词。
- 根因不是 `06b` 术语增强，而是 `04` 原始提及阶段缺少独立的 `object_mentions`，使 `constraint_mentions` 承担了兜底桶角色。

当前改造：
- `04_clause_semantics_mentions` 增加 `object_mentions`，用于抽取动作作用对象、受影响对象、被处理对象、权利义务指向对象和事项对象。
- `constraint_mentions` 收窄为条件、适用范围、触发情形、例外、程序要求、门槛、规范力度、标准要求等。
- `05_clause_semantics_normalization` 将 `object_mentions` 纳入制度内和跨制度归一化，并收紧 `canonical_name` 命名口径，避免把长枚举、整句规则、字段项和临时修饰词当成规范名称。
- `06_clause_semantics_concepts` 将 `object_mentions` 纳入概念筛选，稳定对象、事项、财产、权利义务指向对象优先判为 `object`；字段项、长枚举原文和临时修饰片段应降级为 `context_concept` 或 `noise_concept`。
- `07_clause_rule_expressions` 读取 `object_mentions` 及其归一化/概念筛选结果，生成 `object[]` 时优先参考该字段。

后续判断：
- 如果 `constraint_mentions` 中仍大量出现对象类内容，应继续收紧 `04` 的提示词，而不是优先调整 `06b`。
- 如果 `object_mentions` 的 `canonical_name` 仍出现长枚举或整句规则，应优先继续收紧 `05` 的命名口径。
