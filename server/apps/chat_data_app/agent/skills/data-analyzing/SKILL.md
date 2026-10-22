---
name: data-analyzing
title: 数据分析
description: 数据分析场景统一入口，按需加载三类能力——用自然语言查数与结果分析（检索语义模型、确认指标维度口径、DSL/SQL 执行、同比环比计算），生成可预览的分析看板草案，以及体检语义模型质量、对话补齐口径并产出可回导的 Ossie YAML。用户提出查数、看明细、统计汇总、趋势排行、结论对比、做看板或分析面板、检查或优化语义模型、补充指标维度口径时使用本技能。
metadata:
  domain: 数据分析
  category: data_analyzing
  output_type: mixed
---

# 数据分析

## 适用范围

本技能是数据分析场景的统一入口，涵盖三类任务。**先判断任务属于哪一类，再读取该类对应的能力文档；不要预读全部资料。**

| 任务类别 | 典型触发语 | 能力文档 | 产物 |
|---|---|---|---|
| 查询与分析 | 查个数、看明细、统计、汇总、趋势、排行、对比、同环比、占比、Top N | `references/analysis/query.md` | 系统落盘的查询结果，前端按 `result_id` 展示 |
| 看板生成 | 生成看板、做分析面板、看板草案、预览看板、沉淀分析视图 | `references/dashboard/workflow.md` + `references/dashboard/guide.md` | `outputs/*.dashboard.json` |
| 质量检查与治理 | 检查语义模型质量、覆盖度、口径一致性、补齐指标维度、优化模型、产出模型 YAML | `references/governance/quality-check.md` | 质量报告 / `outputs/governance/` 下交付的模型 YAML |

三类任务一次只走一条主路径。「先查数，满意后再沉淀成看板」是两个连续轮次，各自按对应能力文档执行，不要在一条路径里预读另一条路径的资料。

## 资料分层

```text
references/
├── analysis/     查询与分析：取数与结果处理、SQL 方言差异
├── dashboard/    看板生成：入口与产物契约、规格与图表规范
└── governance/   质量检查与治理：检查项清单、可回导 YAML 规范
assets/dashboard/examples/   看板示例（需求宽泛时按需取一个）
```

`references/` 按能力分目录：**每条路径只读自己目录下的资料**。三类能力的规则互不污染，新增能力域也只是新增一个能力目录，既有文档零改动。

## 共享前置：语义模型与字段口径

查询与看板依赖同一套语义模型检索与口径确认规则，**唯一依据是 `references/analysis/query.md`「标准流程」的「2. 读本体语义层」「3. 检索指标语义层」「4. 精读完整语义层」与「5. 通过查询门禁」**；本文件与 `references/dashboard/workflow.md` 都不重复定义。

以下硬约束不因走哪条路径而放宽：

- **本体层可选增强，缺失不破坏原有路线**：标准链路是「读本体语义层 → 读指标语义层清单 → 精读完整语义层 → DSL/SQL」。先探测 `/mnt/user-data/workspace/semantic/_ontology_catalog.yaml`（本体清单与语义清单文件同放 semantic/ 下、二选一：本体清单存在时不发 _semantic_catalog.yaml）——存在则识别业务对象类型（优先调 `ontology_tool` 的 `list_object_types` 列对象类型并取对象类型→模型绑定，工具不可用时读本清单），按对象类型绑定定位取数模型、按对象类型关系推导跨模型关联，再精读对应语义模型 YAML 确认口径；**指标语义层必然存在，本体层按对象类型逐步建设、可能没有**——清单不存在或无相关对象类型时，直接走原有指标语义层检索路线，不得报错、重试或向用户提及缺失。清单 `models` 段覆盖全部启用模型（含未建本体对象类型、不在 `object_types` 中的模型），是二选一后完整的模型索引；清单可能带 `truncated: true`，`object_types` 只含前一部分，其余对象类型用 `file_grep` 在 `semantic/` 下检索。关系 `from_columns`/`to_columns` 是尽力翻译的物理列，带 `unresolved: true` 时表示有列回退为原始属性/维度名，必须回读对应模型 YAML 确认，不能直接写 join。跨对象关联调 `ontology_tool` 的 `search_around` 直接取物理连接列与对端定位条目，不靠 grep YAML 猜 join；细则见 `references/analysis/query.md`「2. 读本体语义层」。
- 当前用户可见（当前数据源组）的启用语义模型 Ossie YAML 已预物化到 `/mnt/user-data/workspace/semantic/`，文件名为 `{模型名}__{id后缀}.yaml`；同目录的 `_semantic_catalog.yaml` 是系统生成的语义清单文件（每个启用模型一条 file/description/business_notes，file 即快照文件名），**本体清单接管时该文件不存在属正常**，此时用 `_ontology_catalog.yaml` 的 `models` 段取全部启用模型与快照文件名。检索清单优先：第一步 `file_read` 整读语义清单（或本体清单 `models` 段）得到全部启用模型描述、缩小候选（「清单」专指这两个索引 YAML 文件，读取动作是 `file_read` 读取文件路径，不是列目录文件名）；清单描述不足以确认字段口径时，再用 `file_grep` 按业务关键词过滤定位（宽泛词命中过多时先 `output_mode="files_with_matches"` 收敛到文件）；只有清单描述筛出、grep 命中或本体指向的候选才 `file_read` 分页精读。**禁止为找模型逐个读取清单里的 YAML。**
- 指标、维度、业务口径不明确时必须澄清，不能凭常识编造，也不能用 SQL 绕过。
- 当前用户的可用执行对象清单不进系统提示词：涉及查询时先调 `data_analyzing_tool` 的 `list_execution_targets` 实时获取，`member_datasource_ids` 只能从返回清单中选（细则见 `references/analysis/query.md`「1. 获取执行对象清单」）。
- 一次 `data_analyzing_tool` 调用只使用一个 `semantic_config_id`，不混合多个模型的字段、分组或 JOIN。
- **已知具体对象主键时优先读对象**：调 `ontology_tool` 的 `get_object`（`object_type` + `object_key`）读取对象属性与一跳 `links`；`links` 只是对端对象引用，需要属性时再用对端 key 读取。对象读取只解决「这个对象是什么、和谁有关」，聚合/统计仍走 `data_analyzing_tool` 的 `execute_query`，不要用对象结果自己拼 SQL。

## 能力路由细则

### 查询与分析

读取 `references/analysis/query.md`，按其「标准流程」执行（Hive/MySQL 方言对照已并入该文档「查询方言对照」章节）。

### 看板生成

读取 `references/dashboard/workflow.md`（入口与产物契约）与 `references/dashboard/guide.md`（规格、布局、图表选择、DSL 规则、失败处理）。用户需求宽泛或需要模板时，再从 `assets/dashboard/examples/` 里选一个示例。

每张卡片使用的指标和维度必须来自该卡片 `semantic_config_id` 对应的语义模型，检索与口径确认全部按 `references/analysis/query.md` 执行。

### 质量检查与治理

读取 `references/governance/quality-check.md`。

- 机器检查项清单与严重度：`references/governance/quality-rules.md`，与平台导入预览的机器检查同源，**不自创检查规则**。
- 产出可回导 YAML 之前：`references/governance/model-template.md`（骨架照抄填空 + 可回导规则备注与自检清单）。

## 通用边界

- 不编造指标、维度、表、字段、枚举或查询结果；确认不了的口径就问，不猜。
- 治理修改在统一治理目录 `/mnt/user-data/workspace/governance/` 下的拷贝件上进行，不改写 `/mnt/user-data/workspace/semantic/` 下已物化的原模型文件；自检通过后把文件复制到 `outputs/governance/` 交付，治理中间态文件不进 `outputs/`。
- 每次调用工具前先输出一句短句说明当前动作，工具返回后用一句短句概括发现；不把参数、JSON、DSL、SQL 或原始工具返回原文写进用户正文，除非用户明确要求解释技术原因。

## 完成判定

按任务类别分别判定，条件写在对应能力文档中，不在此处重复：

- 查询与分析：`references/analysis/query.md`「多步分析与结果处理」——回答具体数值或形成业务结论前，必须已读取 `result_path` 中的实际数据。
- 看板生成：`references/dashboard/workflow.md`「完成判定」——只写入草稿不算完成，必须已 `present_artifacts` 展示最终 outputs 看板。
- 质量检查与治理：`references/governance/quality-check.md`「产出新 YAML」——交付前必须用 `data_analyzing_tool` 的 `check_yaml` 自检通过。
