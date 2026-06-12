# 对外投资管理专题本体

本文件只按 `sources/` 目录下的 4 份样例制度设计，不扩展到其他制度类型。

## 1. 目标

本体不是为了在图数据库里做规则推理，而是为了：

- 统一制度语义
- 组织查询上下文
- 给大模型提供稳定的冲突分析输入

图数据库只做存储和查询，推理在大模型完成。

## 2. 实体

### `InvestmentPolicyDocument`

制度文档实体。

关键属性：

- `document_id`
- `title`
- `company_name`
- `publish_date`
- `doc_level`
- `doc_no`
- `owner_department`
- `effective_status`

### `Clause`

制度中的原始条款。

关键属性：

- `clause_id`
- `document_id`
- `chapter_no`
- `chapter_title`
- `article_no`
- `article_index_mode`
- `item_label`
- `source_text`

`article_index_mode` 当前至少支持：

- `cn_article`
  - 例如 `第一条`
- `decimal_article`
  - 例如 `第2.3条`

### `NormUnit`

用于冲突分析的最小规范单元。

一条 `Clause` 可以拆成多个 `NormUnit`。

典型字段：

- `topic`
- `subject_role`
- `action`
- `object`
- `modality`
- `approval_body`
- `threshold`
- `time_condition`
- `precondition`
- `exception`
- `applies_to_scope`

### `ApprovalBody`

审批主体。

样例制度中已出现：

- `股东会`
- `董事会`
- `董事长`
- `总经理`
- `总经理办公会`
- `党委会`
- `董事会战略委员会`
- `审计委员会`

### `Department`

主责或协同部门。

样例制度中已出现：

- `资本运营部`
- `需求部门`
- `证券部`
- `法务部`
- `财务部`
- `人力中心`

### `Topic`

按样例制度先收敛到以下主题：

- `适用范围`
- `投资分类`
- `审批权限`
- `项目提出`
- `可行性研究`
- `风险评估`
- `项目审核`
- `实施控制`
- `投后管理`
- `处置退出`
- `信息披露`
- `监督检查`
- `责任追究`

## 3. 关系

### 文档结构关系

- `InvestmentPolicyDocument -> HAS_CLAUSE -> Clause`
- `Clause -> EXPRESSS -> NormUnit`

### 语义关系

- `NormUnit -> ABOUT_TOPIC -> Topic`
- `NormUnit -> HAS_SUBJECT_ROLE -> Department|Role`
- `NormUnit -> REQUIRES_APPROVAL_BY -> ApprovalBody`

### 分析关系

- `NormUnit -> MAY_CONFLICT_WITH -> NormUnit`
- `NormUnit -> SUPPLEMENTS -> NormUnit`
- `NormUnit -> SAME_STAGE_AS -> NormUnit`

说明：

- `MAY_CONFLICT_WITH` 是分析结果关系，不是抽取阶段原生关系。
- 正式实现时，这些关系可由程序或大模型回写。

## 4. 当前必须支持的条款模式

基于样例制度，归一化至少要支持下列模式：

1. 定义类条款
- 例如：什么算对外投资、短期投资、长期投资

2. 审批阈值类条款
- 例如：`3000万元以下由总经理审批`
- 例如：`净资产10%以上且绝对金额超过1000万元提交董事会`

3. 职责分工类条款
- 例如：`需求部门牵头`
- 例如：`法务部负责法律尽调`

4. 流程前置类条款
- 例如：可研、评审、党委会前置研究、总经理办公会预审

5. 实施控制类条款
- 例如：`签约前不得支付投资款`
- 例如：`实施方案变更需重新审查批准`

6. 投后与处置类条款
- 例如：派驻代表、异常报告、暂停调整、转让收回

7. 披露与监督类条款
- 例如：信息披露、审计、责任追究

## 5. 设计结论

如果只按当前 4 份样例制度设计，本体的最小可用组合就是：

- `InvestmentPolicyDocument`
- `Clause`
- `NormUnit`
- `ApprovalBody`
- `Department`
- `Topic`

其中真正用于冲突分析的是 `NormUnit`，不是整份文档，也不是图库推理规则。
