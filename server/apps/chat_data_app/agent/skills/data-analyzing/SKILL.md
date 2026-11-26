---
name: data-analyzing
title: 数据分析
description: 数据分析场景统一入口，按需加载查询分析、对象查询、关系导航、对象操作、看板生成与语义模型治理能力。用户提出查数、看明细、统计汇总、趋势排行、结论对比、查具体对象/详情/关系、执行对象操作、做看板或分析面板、检查或优化语义模型、补充指标维度口径时使用本技能。
metadata:
  domain: 数据分析
  category: data_analyzing
  output_type: mixed
---

# 数据分析

## 路由

先判断任务属于哪一类，只读对应能力文档；不要预读全部资料。

| 任务 | 典型触发 | 能力文档 |
|---|---|---|
| 查询与分析 | 查数、明细、统计、汇总、趋势、排行、对比、同环比、占比、Top N | `references/analysis/query.md` |
| 对象查询与关系 | 具体对象、详情、关系、对象聚合、派生属性 | `references/ontology/query.md` + `references/ontology/relations.md` |
| 对象操作 | 修改、标记、状态变更、执行 Action | `references/ontology/actions.md` |
| 看板生成 | 做看板、分析面板、看板草案、预览 | `references/dashboard/workflow.md` + `references/dashboard/guide.md` |
| 质量检查与治理 | 检查/优化语义模型、补齐口径、产出模型 YAML | `references/governance/quality-check.md` + `references/governance/quality-rules.md` + `references/governance/model-template.md` |

## 路由规则

```text
全局指标、趋势、排名、同比环比、复合指标
  → references/analysis/query.md

具体对象、对象详情、关系、对象级派生属性
  → references/ontology/query.md / relations.md

对象修改、状态变更、Action
  → references/ontology/actions.md

先全局再定位对象
  → references/analysis/query.md + references/ontology/query.md

看板 / 治理
  → references/dashboard / references/governance
```

同一轮只走一条主路径；跨层按“先聚合、后下钻”执行。

本体模式调用前先 `file_read` 读取 `/mnt/user-data/workspace/.system/semantic/_ontology_catalog.yaml`
确认对象类型 / 关系 / Action（文件不存在说明当前会话未物化本体层，走语义层路径）。

本体工具 `ontology_tool` 的参数契约（字段/必填/默认/上限）用
`ontology_tool(action="help", action_payload={"action": "<动作名>"})` 查，由工具入参模型现算；
`references/ontology/` 三份文档（query / relations / actions）只写用哪个动作、顺序、口径与边界，
不重复参数模板。技能负责“怎么做业务”，工具负责“参数怎么填”。

## 共享边界

- 当前用户可见（当前数据源组）的启用语义模型 Ossie YAML 已预物化到 `/mnt/user-data/workspace/.system/semantic/`，文件名为 `{模型名}__{id后缀}.yaml`；同目录的 `_semantic_catalog.yaml` 是系统生成的语义资产目录（每个启用模型一条 file/description/business_notes，file 即快照文件名）。先用 `file_read` 整读目录定位候选模型，再用 `file_grep` 按业务关键词在语义目录内定位细节（宽泛词命中过多时先 `output_mode="files_with_matches"` 收敛到文件），只有命中候选才 `file_read` 分页精读。**禁止为找模型逐个读取目录里的 YAML。**
- 指标、维度、对象属性、关系或业务口径不明确时必须澄清，不能凭常识编造，也不能用 SQL 绕过。
- 写操作必须由用户明确要求；分析问题不能自动升级为对象操作。
- 当前用户的可用执行对象清单不进系统提示词：涉及查询时先调 `data_analyzing_tool` 的 `list_execution_targets` 实时获取，`member_datasource_ids` 只能从返回清单中选（细则见 `references/analysis/query.md`「1. 获取执行对象清单」）。
- 一次 `data_analyzing_tool` 调用只使用一个 `semantic_config_id`，不混合多个模型的字段、分组或 JOIN。
- 产物只写入当前会话 `outputs/`；工具返回大结果时按 `result_path` 读取实际数据，不复述目录或参数。

## 能力路由细则

### 查询与分析

读取 `references/analysis/query.md`，按其「标准流程」执行。

补充必读：涉及 Hive 方言，或原生 SQL 的日期、字符串、窗口等方言差异时，读取 `references/analysis/dialect.md`。

### 看板生成

读取 `references/dashboard/workflow.md`（入口与产物契约）与 `references/dashboard/guide.md`（规格、布局、图表选择、DSL 规则、失败处理）。用户需求宽泛或需要模板时，再从 `assets/dashboard/examples/` 里选一个示例。

每张卡片使用的指标和维度必须来自该卡片 `semantic_config_id` 对应的语义模型，检索与口径确认全部按 `references/analysis/query.md` 执行。

### 质量检查与治理

读取 `references/governance/quality-check.md`。

- 机器检查项清单与严重度：`references/governance/quality-rules.md`，与平台导入预览的机器检查同源，**不自创检查规则**。
- 产出可回导 YAML 之前：`references/governance/model-template.md`（骨架照抄填空 + 可回导规则备注与自检清单）。

## 通用边界

- 不编造指标、维度、表、字段、枚举或查询结果；确认不了的口径就问，不猜。
- 治理修改在统一治理目录 `/mnt/user-data/workspace/governance/` 下的拷贝件上进行，不改写 `/mnt/user-data/workspace/.system/semantic/` 下已物化的原模型文件；自检通过后把文件复制到 `outputs/governance/` 交付，治理中间态文件不进 `outputs/`。
- 每次调用工具前先输出一句短句说明当前动作，工具返回后用一句短句概括发现；不把参数、JSON、DSL、SQL 或原始工具返回原文写进用户正文，除非用户明确要求解释技术原因。

## 完成判定

- 查询与分析：按 `references/analysis/query.md`「完成判定」。
- 对象查询与关系：按 `references/ontology/query.md` / `relations.md`，必须返回对象主键、属性或关系结果。
- 对象操作：按 `references/ontology/actions.md`，必须有执行结果与审计。
- 看板：按 `references/dashboard/workflow.md`「完成判定」。
- 治理：按 `references/governance/quality-check.md`「产出新 YAML」。
