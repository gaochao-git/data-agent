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

## 共享边界

- 指标、维度、对象属性、关系口径不明确时先澄清，不编造。
- 不直接拼 SQL 绕过技能文档；工具失败按原文说明。
- 写操作必须用户明确要求；分析问题不能自动升级为操作。
- 产物只写入当前会话 `outputs/`，不改写 `/mnt/user-data/workspace/semantic/`。
- 工具返回大结果时按 `result_path` 读取实际数据，不复述目录或参数。

## 完成判定

- 查询与分析：按 `references/analysis/query.md`「完成判定」。
- 对象查询与关系：按 `references/ontology/query.md` / `relations.md`，必须返回对象主键、属性或关系结果。
- 对象操作：按 `references/ontology/actions.md`，必须有执行结果与审计。
- 看板：按 `references/dashboard/workflow.md`「完成判定」。
- 治理：按 `references/governance/quality-check.md`「产出新 YAML」。
