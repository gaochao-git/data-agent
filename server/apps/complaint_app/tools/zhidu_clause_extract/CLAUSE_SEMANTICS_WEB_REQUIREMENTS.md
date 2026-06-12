# 条款语义分析工作台需求

## 1. 目标

当前条款语义相关产物已经包含多层 JSONL：
- `clauses.jsonl`
- `clause_semantics_mentions.jsonl`
- `clause_semantics_normalization.jsonl`
- `clause_semantics_concepts.jsonl`
- `clause_rule_expressions.jsonl`

继续通过目录遍历和直接查看 JSONL 文件进行分析，效率较低，也不便于人工抽检和跨层联查。

因此，需要新增一个 Web 页面，作为“条款语义分析工作台”，用于：
- 目录级浏览条款语义相关产物
- 按制度、条款、概念组、规制表达进行联动查看
- 支持人工抽检、问题定位和后续经验沉淀

## 2. 定位

该页面不是面向普通业务用户的展示页，而是面向研发/分析人员的内部工作台。

核心用途：
- 辅助分析条款语义中间产物
- 辅助定位抽取、归并、概念筛选、规制表达的问题
- 为后续本体映射与查询提供人工检查入口

## 3. 最小范围

第一版只做“查看与分析”，不做在线编辑，不做人工回写。

第一版聚焦以下能力：
- 浏览制度列表
- 浏览条款列表
- 查看条款在各层产物中的对应结果
- 查看概念组
- 查看规制表达
- 支持基础筛选与定位

## 4. 数据范围

页面首期使用以下文件作为数据源：

- `documents_mapped.jsonl`
- `clauses.jsonl`
- `clause_semantics_mentions.jsonl`
- `clause_semantics_normalization.jsonl`
- `clause_semantics_concepts.jsonl`
- `clause_rule_expressions.jsonl`

默认按文件落盘结果读取，不依赖数据库。

## 5. 信息架构

第一版建议包含 4 个核心视图。

### 5.1 制度列表视图

用于按制度浏览整体结果。

建议展示字段：
- `doc_id`
- `title`
- `版块`
- `领域`
- `制度文号`
- `主责部门`
- 条款数
- 原始提及数
- 归并组数
- 概念组数
- 规制表达数

建议支持：
- 按 `doc_id`
- 按 `title`
- 按 `版块`
- 按 `领域`
进行筛选

### 5.2 条款详情视图

这是第一版最核心的视图。

对单条条款联动展示：
- 条款原文
- `subject_mentions`
- `action_mentions`
- `constraint_mentions`
- 该条款相关的归并结果
- 该条款相关的概念筛选结果
- 该条款拆出的规制表达

目标：
同一屏内能看清“原文 -> 原始提及 -> 归并 -> 概念 -> 规制表达”的链路。

### 5.3 概念组视图

用于按概念组查看筛选结果。

建议展示字段：
- `normalization_group_id`
- `mention_category`
- `canonical_name`
- `concept_status`
- `ontology_role`
- `screen_reason`
- `member_mentions`

建议支持：
- 按 `concept_status`
- 按 `ontology_role`
- 按 `mention_category`
- 按 `canonical_name`
筛选

重点用途：
- 查看 `noise_concept`
- 查看 `single_member_group`
- 查看 `context_concept`
- 查看疑似误判概念

### 5.4 规制表达视图

用于按条款或制度查看拆出的规则表达。

建议展示字段：
- `doc_id`
- `title`
- `article_no`
- `subject`
- `actions`
- `object`
- `business_context`
- `condition`
- `constraint`
- `effect`
- `evidence_text`

重点用途：
- 检查是否漏拆
- 检查是否过拆
- 检查 `condition / constraint / object` 是否挂对

## 6. 第一版筛选能力

第一版至少支持以下筛选：

- 制度维度：
  - `doc_id`
  - `title`
  - `版块`
  - `领域`

- 条款维度：
  - `article_no`

- 概念维度：
  - `mention_category`
  - `concept_status`
  - `ontology_role`
  - `canonical_name`

## 7. 联动关系

页面应支持以下联动：

- 从制度进入条款
- 从条款进入该条款的原始提及
- 从条款进入该条款相关概念组
- 从条款进入该条款对应规制表达
- 从概念组反查对应条款和原文

## 8. 第一版不做的内容

以下内容明确不纳入第一版：

- 在线编辑 JSONL
- 在线修改概念筛选结果
- 在线回写人工标注
- 批量纠错
- 图数据库查询页面
- 本体可视化图谱

## 9. 后续扩展方向

如果第一版验证有效，后面可以考虑扩展：

- 人工标注与纠错
- 标注结果回写
- 规制表达人工修订
- 本体查询视图
- 图谱化展示

## 10. 当前结论

该需求已经确认立项为“后续实现需求”，当前先落需求文档，不立即进入开发。

后续如果开始实现，应先基于本文件补充：
- 页面路由设计
- 前后端接口设计
- 最小交互稿
