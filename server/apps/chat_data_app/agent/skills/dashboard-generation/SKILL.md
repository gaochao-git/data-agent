---
name: dashboard-generation
title: 看板生成技能
description: 基于用户需求、数据源、指标体系和语义工具，自动设计分析看板卡片，生成并校验 DSL，输出可预览的看板规格。
metadata:
  domain: 数据分析
  category: dashboard_generation
  output_type: dashboard
  input_schema:
    fields:
      - name: goal
        label: 看板目标
        type: text
        required: false
        placeholder: 请输入希望分析或监控的问题，例如经营风险、销售表现、客户结构
      - name: target
        label: 分析对象
        type: text
        required: false
        placeholder: 可选，例如机构、区域、产品、渠道、客户群
      - name: time_range
        label: 时间范围
        type: text
        required: false
        placeholder: 可选，例如近12个月、本季度、2025年
---

# 看板生成技能

## 适用目标

把用户的“生成看板 / 创建看板草案 / 预览看板 / 沉淀分析面板”需求转成可预览的 `dashboard_spec`。

`dashboard_spec` 是看板规格，不是 SQL 查询结果报表。它只描述卡片、布局、图表类型和语义 DSL；不保存 SQL 执行后的数值、明细行或图表数据。

系统看板写入只能由用户在前端预览区点击“写入看板”触发。模型只生成已校验的预览文件，不自动写入业务系统。

## 产物契约

看板预览文件必须写入 outputs，文件名使用 `.dashboard.json` 后缀。每次新建看板必须使用独立文件名，避免覆盖同一会话里的历史看板预览；推荐格式：

```text
outputs/<业务主题>-<短时间戳或短随机后缀>.dashboard.json
```

工作草稿：

```text
workspace/dashboard_draft.json
```

写入 outputs 后必须调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])` 显式呈现给用户；`present_artifacts` 只返回轻量文件元信息，前端会按文件路径加载对应看板规格并渲染预览。

## 必读资料

进入本技能后执行：

1. 确认已经看到 `skill_load("dashboard-generation")` 返回 `[skill_load]`。
2. 读取主指南：
   - `/mnt/user-data/runtime-skills/dashboard-generation/references/dashboard-generation-guide.md`
3. 用户需求宽泛或需要模板时，再读取一个示例：
   - 综合 / 默认分析：`/mnt/user-data/runtime-skills/dashboard-generation/assets/examples/comprehensive-dashboard.json`
   - 风险 / 异常 / 预警：`/mnt/user-data/runtime-skills/dashboard-generation/assets/examples/risk-monitoring-dashboard.json`
   - 机构 / 区域 / 产品 / 客户画像：`/mnt/user-data/runtime-skills/dashboard-generation/assets/examples/profile-dashboard.json`

`skill_load` 返回已包含本 `SKILL.md` 正文，不需要再读取 `SKILL.md`。

## 工具流程

按下面顺序完成，不把工具参数、JSON、DSL、SQL、patch 或工具返回原文写进用户正文。
每次调用工具前先输出一句短句说明当前要做什么；工具返回后用一句短句概括发现，再继续下一步。

1. `skill_load("dashboard-generation")`
2. `file_read("/mnt/user-data/runtime-skills/dashboard-generation/references/dashboard-generation-guide.md")`
3. 按需 `file_read` 一个示例 JSON
4. 如果系统提示词里的 `data_context.semantic_configs` 没有 `selected=true` 的指标集，先调用 `select_semantic_config_tool(query=用户原始看板目标)` 获取唯一可用指标集
5. 只使用已手动选择或 `select_semantic_config_tool` 返回的唯一指标集中的真实指标和维度
6. 如枚举、状态、类型、时间或金额等字段存储口径不明确，先构造明细抽样并查询 3 条原始数据确认结构
7. 内部构建完整 `dashboard_spec`，顶层必须包含 `semantic_config_id`
8. `dsl2sql_tool(dsl=dashboard_spec, semantic_config_id=dashboard_spec.semantic_config_id)` 校验整份规格
9. 校验失败时，按主指南修正后重新调用 `dsl2sql_tool`
10. 校验通过后 `file_edit(operation="write", file_path="workspace/dashboard_draft.json", content=dashboard_spec, mode="overwrite")`
11. 新建预览时写入 `/mnt/user-data/outputs/<业务主题>-<唯一后缀>.dashboard.json`
12. 只有用户明确要求修改某个已有看板预览文件时，才先 `file_read` 该 outputs 文件并用 `file_edit(operation="patch", patch=...)` 修改
13. 调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])` 展示最终看板预览
14. 简短总结预览结果

新建看板预览时不要复用或覆盖已有 outputs 看板文件；同一会话中多次生成看板，应保留多个独立的 `.dashboard.json` 产物。修改已有预览文件前必须 `file_read` 当前文件内容，修改后必要时重新调用 `dsl2sql_tool` 校验。

`semantic_query_tool` 只用于抽样确认 SQL 是否有数据或确认枚举、状态、类型、时间、金额等字段存储口径；执行结果不能写入 `dashboard_spec`。

## 设计规则

具体规格、布局、图表选择、DSL 规则和失败处理以 `references/dashboard-generation-guide.md` 为准。这里保留必须遵守的底线：

- 指标、维度、过滤、排序字段必须来自已确定的唯一指标集。
- 没有已手动选择或工具自动选择出的唯一指标集时，不得生成看板 DSL，不得调用 `dsl2sql_tool`。
- `dashboard_spec.semantic_config_id` 必填，取值必须是已手动选择的指标集 ID，或 `select_semantic_config_tool` 返回的 `selected_config_id`。
- outputs 看板文件中的 `semantic_config_id` 必须与 `dsl2sql_tool` 校验时传入的指标集 ID 一致。
- 默认生成最小可用看板，优先覆盖总览、趋势、结构 / 对比、排行 / 诊断等核心视角。
- 每张卡片必须包含 `title`、`chart_type`、`layout`、`dsl`。
- 卡片中不包含 `sql`、`data`、`rows`、`result`、`x_field`、`y_field`，不使用 `type` 代替 `chart_type`。
- 生成或修改 `dashboard_spec` 时必须使用多行缩进 JSON，不要压缩成一行；后续 patch 必须基于文件当前真实内容。
- 修改已有 `dashboard_spec` 前必须先用 `file_read` 读取目标文件；`file_edit` patch 失败后必须重新 `file_read` 再生成新 patch，不能凭记忆重试。
- 最终文件必须是看板规格文件，不是查询结果文件。
- 写入或更新 outputs 看板文件后，必须调用 `present_artifacts(files=["/mnt/user-data/outputs/<实际文件名>.dashboard.json"])`，否则聊天框不会展示看板预览。

## 完成判定

同时满足以下条件，才算看板预览已生成：

1. 已读取主指南。
2. 已构建包含 `dashboard_name` 和非空 `cards` 的 `dashboard_spec`。
3. 已调用 `dsl2sql_tool(dsl=dashboard_spec, semantic_config_id=dashboard_spec.semantic_config_id)`，且至少一个卡片通过校验。
4. 已生成或更新一个 `outputs/*.dashboard.json` 文件，且可被识别为 dashboard_spec。
5. 已调用 `present_artifacts` 显式展示最终 outputs 看板。
6. 最终文件符合“设计规则”的字段底线。

只写入 `workspace/dashboard_draft.json` 不算完成；只执行 SQL 或总结数据也不算完成。

## 用户输出

过程说明保持一句短句，只说明当前动作或发现，不展开指标、维度、SQL、卡片清单或工具结果明细。最终回答只包含：

- 看板预览已生成或失败原因
- 实际生成或更新的 `outputs/<实际文件名>.dashboard.json`
- 卡片数量和默认口径
- 提醒用户在右侧预览区点击“写入看板”

不要在正文展示完整看板 JSON、DSL、SQL、patch、工具参数或原始查询结果。
