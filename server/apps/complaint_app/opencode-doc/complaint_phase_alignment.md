# complaint 阶段对齐状态（`complaint_v1.mmd` 对照当前实现）

更新时间：2026-02-10  
对照基线：`server/apps/complaint_app/opencode-doc/complaint_v1.mmd`

## VerifyAgent 在 v1 中的职责

在 `v1` 设计里，`VerifyAgent` 不是“再总结一次”，而是“核实与证据校验”环节，核心动作是：

1. 接收“核查材料 + 机构报告”作为输入。  
2. 在高上下文占用时先做深度压缩，再进入核实推理。  
3. 加载 `verify skill`，并结合附件/工具结果做逐项核实。  
4. 输出“核实结论”回传给 `MainAgent`。  

对应设计位置：`server/apps/complaint_app/opencode-doc/complaint_v1.mmd:206`、`server/apps/complaint_app/opencode-doc/complaint_v1.mmd:218`、`server/apps/complaint_app/opencode-doc/complaint_v1.mmd:236`、`server/apps/complaint_app/opencode-doc/complaint_v1.mmd:248`。

## 阶段对齐总览

| 阶段 | 对齐结论 | 说明 |
|---|---|---|
| 阶段1 初始化与任务接收 | 🟡 部分实现 | 已有启动与工具装载；MCP 为可配置远端发现，但与 v1 的“固定连接握手”表述不完全一致。 |
| 阶段2 任务分析与规划 | ✅ 已实现 | 已有 Planner + 依赖修正 + 执行前上下文检测/压缩。 |
| 阶段3 子智能体1（举报摘要） | 🟡 部分实现 | 通过 `task -> complaint_summary` 实现，不是独立 SummaryAgent 运行时模块。 |
| 阶段4 子智能体2（事项分析） | 🟡 部分实现 | 通过 `complaint_analysis`/`skill` 路径实现，尚非 v1 中独立 IssueAgent 编排形态。 |
| 阶段5 子智能体3（核实验证） | ❌ 未实现 | 当前没有 `verify` 子智能体及其执行链路。 |
| 阶段6 结果合并与验证 | 🟡 部分实现 | 有结果合并与后置压缩；无独立“质量校验/验证报告产出”步骤。 |
| 阶段7 展示结果 | ✅ 已实现 | API 可返回 `answer + plan + step_outputs`。 |
| 阶段8 清理与保存 | 🟡 部分实现 | API 路径已落历史持久化与临时文件清理；本地脚本路径主要是内存会话。 |

## 分阶段证据（代码）

### 阶段2：任务分析与规划（✅）

- 主流程先规划再执行：`server/apps/complaint_app/opencode_runtime/main_agent.py:242`。  
- 执行前上下文压缩：`server/apps/complaint_app/opencode_runtime/main_agent.py:255`。  
- Planner 生成计划并做依赖补全（提取->摘要->分析）：`server/apps/complaint_app/opencode_runtime/planner.py:285`、`server/apps/complaint_app/opencode_runtime/planner.py:230`、`server/apps/complaint_app/opencode_runtime/planner.py:258`。

### 阶段3：举报摘要（🟡）

- 摘要通过 `task` 工具拉起子智能体：`server/apps/complaint_app/opencode_runtime/tools/builtin/task_tool.py:45`、`server/apps/complaint_app/opencode_runtime/tools/builtin/task_tool.py:49`。  
- 当前注册的摘要子智能体为 `complaint_summary`：`server/apps/complaint_app/opencode_runtime/agents/agents.py:94`。

### 阶段4：事项分析（🟡）

- 事项分析工具主入口：`server/apps/complaint_app/opencode_runtime/tools/builtin/complain_analysis_tool.py:20`。  
- 强依赖 `query + source_text` 输入：`server/apps/complaint_app/opencode_runtime/tools/builtin/complain_analysis_tool.py:33`。  
- 仅本地法规路径通过 `skill` 装载：`server/apps/complaint_app/opencode_runtime/tools/builtin/skill_tool.py:15`。

### 阶段5：核实验证（❌）

- 目前无 `verify` 子智能体定义；子智能体列表仅见 `complaint_summary`：`server/apps/complaint_app/opencode_runtime/agents/agents.py:94`、`server/apps/complaint_app/opencode_runtime/agents/agents.py:167`。

### 阶段6：结果合并与验证（🟡）

- `plan` 模式下最终输出取最后一步 `output`：`server/apps/complaint_app/opencode_runtime/main_agent.py:267`。  
- 执行后再次压缩上下文：`server/apps/complaint_app/opencode_runtime/main_agent.py:282`。  
- 当前压缩为窗口裁剪策略（阈值触发），未单独实现“核实质量判定器”：`server/apps/complaint_app/opencode_runtime/compaction.py:81`。

## Harness 反馈层待办

### 1. `complaint-analysis` 输出校验

目标是在 `complaint-analysis` 结果写入 `issue_analysis/<version>/final.json` 之前增加确定性校验，避免把明显不合格的模型结果固化为可复用产物。

建议落点：

1. `assemble_issue_analysis_result(...)` 组装最终结构。  
2. `validate_issue_analysis_result(...)` 校验结构、事项一致性、客观表述、引用可追溯性和派生结果一致性。  
3. 校验通过后调用 `save_issue_analysis_result(...)`。  
4. 校验失败时输出结构化 `validation_errors`，通过 SSE 返回错误或低质量状态，不写入 `issue_analysis/final.json`。

第一版校验范围：

- 结构完整性：`api_kind/base_id/items_and_appeals/law_basis/document_checklist/verification_points/conclusion_text/versions` 类型正确，`complaint_items` 非空。
- 事项一致性：`complaint_items` 与摘要阶段 `structured_payload.complaints` 的分项保持一致，避免把多个原始举报事项合并成一个。
- 客观表述：事项文本保持“举报人反映/称/涉嫌/可能存在”等表述，避免改写为“已违法、构成违法、确认违规”等已证实结论。
- 引用可追溯：`original_quotes`、`user_cited_laws` 不应脱离 OCR 原文或摘要结构；`normalized_law` 不应凭空补全法规名。
- 派生结果一致性：`law_basis/document_checklist/verification_points` 应来自经验库/本地法规分析结果聚合，不由最终结论文本单独编造。

### 2. 基于 Hook 的举报事项分析自我进化

目标是在人工处理完成后，把“系统分析结果”和“人工填写的调阅资料清单、核实思路”进行偏差分析，形成可复用反馈，而不是每次依赖人工重新纠正。

建议采用 Hook 方式接入，避免侵入主链路：

1. `post_issue_analysis_saved`：系统完成 `complaint-analysis` 并保存结果后触发，记录本次 `base_id/issue_version/complaint_items/document_checklist/verification_points`。
2. `post_human_review_submitted`：外部系统或人工表单提交最终调阅资料清单、核实思路后触发。
3. Hook 调用外部接口按举报事项查询历史处理结果，拿到历史人工清单与核实思路。
4. 偏差分析器按事项粒度比较三类内容：系统建议、当前人工填写、历史同类处理。
5. 输出 `feedback_delta`，记录缺失项、冗余项、表述差异、分类偏差和可沉淀经验。
6. 反馈只先落盘为待审核经验，不自动改 prompt 或覆盖结果；经人工确认后再进入经验库或 skill 文档。

建议反馈产物：

```json
{
  "base_id": "",
  "issue_version": 0,
  "complaint_id": "complaint_1",
  "system_document_checklist": [],
  "human_document_checklist": [],
  "historical_document_checklist": [],
  "missing_documents": [],
  "extra_documents": [],
  "system_verification_points": [],
  "human_verification_points": [],
  "historical_verification_points": [],
  "missing_verification_points": [],
  "classification_delta": "",
  "suggested_experience_patch": "",
  "review_status": "pending"
}
```

边界约束：

- Hook 只能追加反馈记录，不直接修改已保存的 `issue_analysis/final.json`。
- 自我进化默认进入人工审核队列，不能自动把一次人工处理差异升级为全局规则。
- 外部历史接口失败不能影响主链路，只记录 Hook 失败原因与重试状态。
- 偏差分析按举报事项粒度执行，避免把整案级清单误写回单个事项。

### 阶段7：展示结果（✅）

- API 返回 `answer/metadata`：`server/routes/chat_routes.py:760`。  
- 可选返回 `plan` 与 `step_outputs`：`server/routes/chat_routes.py:767`、`server/routes/chat_routes.py:770`。

### 阶段8：清理与保存（🟡）

- API 路径会从历史构建 session：`server/routes/chat_routes.py:518`、`server/routes/chat_routes.py:695`。  
- API 路径会持久化用户与助手消息：`server/routes/chat_routes.py:722`、`server/routes/chat_routes.py:748`。  
- API 路径会清理临时上传文件：`server/routes/chat_routes.py:773`。  
- 本地脚本 `run_opencode_runtime.py` 使用内存 `Session`，无默认落盘：`scripts/run_opencode_runtime.py:47`、`scripts/run_opencode_runtime.py:65`。
