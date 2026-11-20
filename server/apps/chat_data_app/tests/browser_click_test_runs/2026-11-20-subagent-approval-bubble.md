# 子智能体审批冒泡验证（2026-11-20）

## 0. 问题与结论（先看结论）

**问题：子智能体执行需要审批的工具时，审批能否冒泡出来让用户点「允许/拒绝」？**

**结论：不能。** 分两层，两层实测+代码都指向同一结果：

1. **没有审批可冒**：标准装配下，子智能体调用 `ask` 档工具（含 MCP 动态工具）不会触发审批中断，任务直接跑完；本次实测 `ask` 档 MCP 工具 `mcp_fault-db_db_query` 被子智能体直接执行成功，全程 `pending_interrupt=null`、事件流 `approval/waiting_approval=0`。
2. **即使冒出来也不是给用户点的**：假设子任务真进入 `waiting_approval`（历史/非标准装配），冒泡到主会话的是一条**给主智能体的内部文本消息**，原文写死「子智能体不支持审批参数……请用 manage cancel 取消后重新派发」，不是可点审批卡；工具 schema `ManageArgs.action` 只有 `list/check/wait/cancel`，无 `approve/reject`；前端 `SubagentTaskRow.vue` 只有取消/关闭按钮。后端虽残留 `_control_resume(approve/reject)` 恢复逻辑，但无任何调用方，是死路径。

即：**产品口径已明确不做「用户审批子智能体操作」，只允许取消重派。** 本轮验证该口径在真实浏览器 + 真实后端 + 真实 LLM 下确实成立。

## 1. 测试环境与方法

- 前端 `http://localhost:8089/chatdata?parentComponentName=执法检查`（Chromium via agent-browser，真点）
- 后端 `http://127.0.0.1:5001` `/health={"status":"ok"}`，经 Vite `/dev-api` 代理读真实 dev MySQL
- 模型 `local/Qwen3.6-35B-A3B-4bit`（主对话与子智能体同源）
- 全程页面挂 `window.onerror`/`unhandledrejection`/`console.error` 探针到 `window.__errs`，结束为 **0 条**
- 会话：`98493d91-7c75-42ba-beef-f57af1a10d3b`（新建 throwaway「派发子智能体执行故障分析查询」）
- 截图：`.workbuddy/screenshots/subagent-approval-20261120/task-completed-no-approval.png`

## 2. 触发场景构造

用截图对应的 `fault-analysis` 插件做触发源（该插件已安装启用）：

- `chat_data/plugins/fault-analysis/plugin.json`：`fault-db` MCP server 的 `db_query` 明确 `approval: "ask"`（另一个 `fault-ssh.ssh_exec` 是 `allow`）。
- 主对话输入：「请派发一个后台子智能体（general 类型），调用故障分析插件的 fault-sql 工具执行 SELECT 1……必须以派发子智能体方式执行」。
- 模型正确识别 `mcp_fault-db_db_query`（catalog 解析策略=`ask`，`ask_names=['mcp_fault-db_db_query']`），并派发后台子智能体。

## 3. 执行与观测

| 项 | 结果 |
| --- | --- |
| 派发 | 成功，task_id `32a55034d78a450fa6e8a1b3a0d078f2`，general 后台任务，`background` 模式 |
| 子智能体调用工具 | `progress.last_tool=mcp_fault-db_db_query`，`tool_calls=1`，工具真实被调用并执行 |
| 任务终态 | `completed`（约 46s），`pending_interrupt=null`，无 `waiting_approval` |
| 事件流 | `tool_started=1`、`tool_completed=1`、`subagent_message=1`，`approval=0`、`waiting_approval=0`（SSE 全量统计） |
| 页面审批卡 | 全程无「需要你的确认」卡、无「允许/拒绝」按钮（`needConfirm=false`、按钮数=0） |
| 页面等待审批态 | 子任务状态文案从未出现「等待审批」 |
| 页面错误探针 | `window.__errs=[]`，0 条 |

截图证据：主对话气泡显示子智能体完成报告（表格形式，如 `replication/transactions/slowlog` 诊断项），无审批卡；页面底部输入框可直接继续输入，未被任何 pending 审批阻塞。

## 4. 代码依据（为什么会这样）

| 位置 | 事实 |
| --- | --- |
| `data_agent.py:354-356` `_subagent_approval_exclusions` | 子智能体装配时对所有 ask 工具做 fail-closed 剔除，避免委派绕过审批；`:383` 传给两个 spec |
| `subagents.py:420-428` | 审批中间件仅在 `control_enabled`（后台任务）且 `ask_tool_names(approval_policies)` 非空时装配 |
| `subagents.py:844-852` | 若真发生中断：`suspend_task(status=TASK_STATUS_WAITING_APPROVAL, interrupt_payload=...)` |
| `subagent_task_service.py:462-482` `_suspension_notification_text` | 冒泡文本是内部消息，明确让用户 cancel 重派，不是审批卡 |
| `subagent_tool_schema.py:64-108` `ManageArgs` | `action ∈ {list, check, wait, cancel}`，无 approve/reject |
| `subagent_capabilities.py:63-67` | 等待审批的任务只能取消，审批入口不暴露给模型 |
| `SubagentTaskRow.vue` / `useSubagentTasks.ts` | 前端无审批按钮、无 approve/reject API |
| `subagents.py:1592-1663` `_control_resume(approve/reject)` | 残留恢复逻辑，唯一调用方是 `action="steer"`（`:1721`），无路径触发 approve/reject，死代码 |

## 5. 说明 / 边界

- 本轮同时覆盖了一个更深的点：**ask 档 MCP 工具在子智能体里连中断都没触发就执行了**。按 `data_agent.py` 的 fail-closed 设计，ask 工具本应被剔除、子智能体根本看不到它；但实测它出现在 `last_tool` 并成功执行。可能原因是子图 `resolve_approval_policies(tools)` 对 MCP 实体工具名（如 `mcp_fault-db_db_query`）未匹配到插件 `approval: ask` 配置，导致审批中间件未把它识别为 ask。这是「审批未生效」而非「审批已冒泡」，**不构成越权**（该工具本身是只读诊断、`approval: ask` 只是策略配置），但值得后端侧确认 MCP 工具审批档位是否完整透传到子图。
- 未测：approve/reject 交互本身（产品未提供入口，无法真点）；`EVENT_WAITING_APPROVAL` 冒泡卡（本轮未触发该状态，无真实样本）。
- 不改任何产品代码；本记录为只读验证。
