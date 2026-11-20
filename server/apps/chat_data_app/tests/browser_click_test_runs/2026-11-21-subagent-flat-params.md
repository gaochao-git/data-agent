# 子智能体扁平参数优化整体验证（2026-11-21）

## 0. 结论（先看）

被测提交：`b493d30f fix(chat-data): 扁平化子智能体参数，修复小模型嵌套调用不稳定`（用户 07:00:56 提交）。
改动本质：`subagent` 工具参数从三层嵌套 `{"dispatch":{...}}` / `{"send":{...}}` / `{"manage":{"action":...}}` 改为**顶层扁平** `{"action": ..., 字段}`；执行层仍归一到 DispatchArgs/SendArgs/ManageArgs，逻辑不变。

**整体结论：优化有效，六动作全部真点通过；但发现一个明确的使用边界，需如实记录。**

| 维度 | 结果 |
| --- | --- |
| 自动化回归 | 后端 schema 单测 34 passed；前端 3 spec 49 passed；type-check exit 0 |
| 六动作真点（9b + 显式扁平示例） | **6/6 PASS**：dispatch / list / check / send / wait / cancel |
| 服务端是否已加载新代码 | 是（timeline 抓到模型实际生成 `{"action":"dispatch",...}` 扁平参数） |
| 前端展示 / 回执文案 | 扁平格式在页面 `<code>` 回执中生效 |
| **边界问题** | **纯自然语言（不给示例）时 9b 幻觉出工具名 `subagent_type`，触发错误审批卡**——见 §4 |
| 页面错误探针 | 全程 `window.__errs=[]`，0 条 |

## 1. 测试环境

- 前端 `http://localhost:8089/chatdata?parentComponentName=执法检查`（Chromium via agent-browser，真点）
- 后端 `http://127.0.0.1:5001` `/health=ok`，经 Vite `/dev-api` 代理读真实 dev MySQL
- 模型：`local/商汤-9b`（`sensenova-loop-9b-mlx-6bit-713bf770`）——**本次优化的目标小模型**，全程用它测
- 主会话 `34e63285-ae43-4eaf-a37f-8b5bbaf5fb27`（六动作主测试会话）；边界会话 `220f50bc-40e4-45c2-9c64-3f45c59cb0a4`
- 截图：`.workbuddy/screenshots/subagent-flat-params-20261121/`
- 参数真值来源：会话 timeline `history_events[].payload.tool_calls[].args`（模型实际生成的原始参数，非回放映射）

## 2. 自动化回归（独立重跑，不照抄提交说明）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 后端 schema 单测 | `pytest apps/chat_data_app/tests/unit/test_subagent_tool_schema.py -q` | **34 passed** |
| 前端 spec | `vitest run toolDisplay.spec.ts SubagentTaskProcessView.spec.ts ConversationWorkspacePanel.spec.ts` | **49 passed**（3 文件） |
| 前端类型检查 | `npm run type-check` | exit 0 |
| 探针 | 全程 console 探针 | 0 条 |

单测覆盖这次新增契约：legacy 嵌套写法拒绝并提示「扁平 action」（`test_rejects_legacy_nested_arguments`）、字段白名单、必填校验、`decide` 旧字段拒绝、工具描述含可照抄扁平 JSON、系统提示扁平示例。

## 3. 六动作真点（9b + 显式扁平 JSON 示例）

统一方法：每条指令里给出**可照抄的扁平 JSON 示例**，让 9b 照抄，从 timeline 读它实际生成的参数。

| 动作 | 指令给的示例 | 9b 实际生成参数 | 页面/后端效果 | 结论 |
| --- | --- | --- | --- | --- |
| dispatch | `{"action":"dispatch","subagent_type":"explore","description":"盘点 outputs...","background":true}` | `{"action":"dispatch","subagent_type":"explore","description":"...","background":true}` | 派发成功，task `2dd1b2a9`，子任务 `completed`（`last_tool=dir_list`） | PASS |
| list | `{"action":"list"}` | `{"action":"list"}` | 正确列出 1 个智能体/1 条任务，带 agent_id/类型/task_id/状态表格 | PASS |
| check | `{"action":"check","task_id":"2dd1b2a9..."}` | 同左 | 返回完整详情+报告，且回执追问示例是扁平 `subagent({"action":"send",...})` | PASS |
| send | `{"action":"send","task_id":"2dd1b2a9...","message":"..."}` | 同左（字段顺序不同） | 追问生效，生成第 2 轮任务 `f351072f`（`turn_index=2`，completed），沿用同一子会话 | PASS |
| wait | `{"action":"wait","task_ids":["2dd1b2a9..."],"wait_seconds":5}` | 同左 | 对已完成任务立即返回终态+结论 | PASS |
| cancel | `{"action":"cancel","task_id":"a435c304..."}` | 同左（并自动追加一次 check 确认） | 运行中任务状态变 `cancelled`，页面显示「任务已取消」+详情 | PASS |

关键证据：
- timeline 原始参数 `name="subagent"`、`args={"action":"dispatch",...}`——**证明服务端已加载新代码**（旧 schema 无顶层 action 字段），且 9b 照抄扁平示例成功。
- check/send 回执里 `<code>subagent({"action":"send",...})</code>` 在页面真实呈现——证明 `toolDisplay.ts` / `SubagentTaskProcessView.vue` 的扁平展示改动生效。
- cancel 前先派发一个长任务（`a435c304`，general，running），cancel 后 `status=cancelled`，闭环成立。
- 截图：`s2-six-actions-all-pass.png`。

## 4. 发现的边界问题（如实记录，非本次改动直接缺陷）

**纯自然语言指令（不带显式扁平示例）时，9b 会把字段 `subagent_type` 幻觉成工具名。**

- 会话 `220f50bc`，指令「请派发一个后台 explore 子智能体盘点 outputs...用派发子智能体的方式做」（无 JSON 示例）。
- 9b 实际生成：`tool_calls=[{"name":"subagent_type","args":{}}]`——把扁平 schema 的字段名 `subagent_type` 当成工具调用目标。
- 后果：未知工具名按 fail-closed 走 `ask` 策略，主对话弹出审批卡「是否允许执行「subagent_type」？」（截图 `s1-9b-hallucinated-tool-approval.png`，弹窗时间 07:04:18）。用户若误点「允许」会去执行一个不存在的工具。测试中已点「取消」清理，未放行。
- 对照：同一 9b，指令里给出**显式扁平 JSON 示例**后（§3），六动作全部正确生成 `name="subagent"` + 扁平 args。说明这是「小模型无示例时的工具选择幻觉」，不是扁平 schema 解析错误——扁平化方向本身是对的（示例更短更易照抄），但**小模型仍需示例兜底**。
- 定性：这是模型行为边界，不是这次参数改动的回归 bug；但值得关注——系统提示/工具描述里已经给了扁平示例（单测 `test_tool_description_has_flat_example` 验证），说明设计上已考虑；9b 在长上下文/多轮后仍可能忽略示例。不做代码改动，记录供后续提示词优化参考。

## 5. 说明 / 边界

- 本次全程用 9b（优化目标模型）；35B/deepseek 未单独测——它们生成嵌套 JSON 本就稳定，扁平化对它们是简化而非修复。
- 审批冒泡相关（上一轮 `2026-11-20-subagent-approval-bubble.md`）结论不变：子智能体操作仍不提供用户审批入口；本轮 §4 的审批卡是**主对话层面的未知工具审批**，与子智能体审批是两回事。
- 测试结束已关闭 3 个子会话（2dd1b2a9 / a435c304 / 220f50bc 下的残留）释放额度；未改任何产品代码。
