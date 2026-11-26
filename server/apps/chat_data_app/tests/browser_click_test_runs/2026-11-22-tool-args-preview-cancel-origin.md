# 工具参数预览 + 子智能体取消来源 验证（2026-11-22）

## 0. 结论（先看）

被测对象：**工作区未提交改动**（34 个文件；HEAD=`22b1b581`），两大功能：
1. 工具参数生成过程实时预览（`tool_args_delta` SSE 帧 + 前端 Markdown/任务书预览 + done 收口）
2. 子智能体取消来源区分（用户停止 vs 主智能体取消，通知文案/来源落库）

验证方式：真浏览器点击（agent-browser）+ 服务端 SSE 抓帧 + 单测 + type-check。

**整体结论：功能主线成立，发现 1 个确定缺陷 + 2 个观察项，需他人修复/确认。**

| 维度 | 结果 |
| --- | --- |
| 后端单测（5 文件，最新代码） | 174 passed |
| 前端单测（6 spec） | 50 passed |
| 前端 type-check（`vue-tsc --build --force`） | exit 0 |
| SSE 抓帧 | `tool_args_delta`×2（含长 delta + done 收口）、`tool_started`×1、`tool`×2、`tool_progress`×1、`tool_completed`×1，顺序正确 |
| 页面错误探针 | `window.__errs=[]`，0 条 |
| 确定缺陷 | **DEF-1：完成通知里残留旧嵌套语法 `subagent(manage={action:"check"...})`** —— 见 §3 |
| 观察项 | OBS-1 执行节点失联路径的 report 文案；OBS-2 取消撞上任务自然完成的竞态 |

## 1. 测试环境
- 前端 `http://localhost:8089/chatdata?parentComponentName=执法检查`（Vite dev，Chromium via agent-browser）
- 后端 `http://127.0.0.1:5001`，重启两次以加载测试期间被他人更新的代码（最终加载 20:36 版本）
- 模型：`local/Qwen3.6-35B-A3B-4bit` / `deepseek/deepseek-v4-flash`（中途 Insufficient Balance）/ `local/qwen3.8-27b-3bit`
- 测试会话：`269e6966-8e68-40d5-ba32-12d25b294a1f`（"派发后台订单状态分析任务"）
- 任务：`29deeef7...`（completed）、`30f6022d...`（用户取消→cancelled）
- 注意：测试期间代码被另一智能体改动（`subagent_notification_text.py` 20:36 新建、`subagent_task_service.py` 20:36 改），故本轮结论以最终 20:36 版本为准。

## 2. 通过的验证

### 2.1 工具参数预览（真浏览器 + SSE 抓帧）
用 XHR/fetch 都不稳定，最终用 **fetch + `ReadableStream.tee()`** 分流抓到完整事件流（关键方法）。
file_write 长参数场景（"把这句话重复20遍写入 notes.txt"）抓到：
```
evs: { heartbeat:1, run_steps:6, token_usage:3, thinking:603,
       generation_speed:25, conversation_title_updated:1,
       tool_started:1, tool:2, tool_args_delta:2, tool_progress:1,
       tool_completed:1, message:1, message_end:1, done:1 }
```
`tool_args_delta` 第 1 帧 = `{"tool_call_id":"call_d0b2...","tool_name":"file_write","status":"generating","delta":"{\"content\":\"数据智能体季度复盘要点记录\n...\"}"}`；第 2 帧 = `delta:"", done:true`（收口）。顺序 `delta → tool_started → tool_progress → tool_completed` 正确。
- 后端白名单：`SSE_EVENT_TOOL_ARGS_DELTA` 在 `SSE_EVENT_NAMES` 与 `_SUBAGENT_TOOL_EVENT_TYPES` 两处均注册（单测 `test_tool_args_delta_frame_is_registered_on_both_streams` 通过）。
- 前端 `ToolCallInfo.status` 扩了 `generating/cancelled`、`argsText/argsStreaming/argsTruncated` 字段；`ToolCallCard` 参数预览框 `.tool-args-preview` / 任务书 `.tool-description-preview` 渲染逻辑正确（单测覆盖）。
- 32 字符 flush、8000 字符封顶、has_preview 收口补发快照——`ToolArgsPreviewBuffer` 单测通过（`test_tool_args_preview.py`）。

### 2.2 子智能体派发卡片任务书预览
真点派发 `subagent({"action":"dispatch",...})`，主对话框工具卡显示 description（"逐步分析 orders 表订单状态分布…"），未铺 subagent_type/label 等 JSON——`compactDescription` + `getSubagentDispatchArgs` 改动生效。

### 2.3 用户取消链路（真点 + API）
- 工作台子智能体管理列表点 `data-subagent-task-cancel` 取消按钮 → 后端 `POST .../subagent-tasks/{id}/cancel` 200，router 传 `origin=CANCEL_ORIGIN_USER`（单测 `test_subagent_task_routes_list_detail_and_cancel` 断言）。
- `cancel_task` 的 meta 落 `cancel_origin=user`；"用户优先"逻辑（`_resolve_cancel_origin`）经单测验证：先用户停止后主智能体 cancel，来源仍为 user。
- 终态通知三分支实跑渲染（直接调 `terminal_notification_text`）：
  - user → 标题"用户手动停止后台子智能体"+"取消来源=用户主动停止"+不续跑指引 ✅
  - agent → "后台子智能体任务状态通知"+"取消来源=主智能体"+可 send 续写 ✅
  - 无来源 → 通用取消通知 ✅
- `finalizeSubagentTrailOnStop` 收口未完成内层工具为 cancelled（单测验证，避免停止后一直转圈）。

## 3. 缺陷清单（给他人修，本轮未改代码）

### DEF-1（确定，MED）：完成通知残留旧嵌套工具语法，与同段扁平语法自相矛盾
- 位置：`server/apps/chat_data_app/agent/run/subagent_notification_text.py:134`（本次新增 untracked 文件，`terminal_notification_text` 非取消分支）
- 现状实跑输出：
  ```
  请根据报告向用户简要汇报结论…；需要完整报告或最新状态时调用
  subagent(manage={action:"check", task_id:"tid"})；
  subagent({"action":"check","task_id":"..."}) 会返回完整正文…
  ```
  同一段里 `manage={action:...}`（旧嵌套、非法 JSON）与 `{"action":"check",...}`（新扁平、正确）并存。
- 影响：通知是主智能体感知后台任务的唯一业务入口；模型照抄前者会生成扁平 schema 拒绝的参数（历史 commit `b493d30f` 已把 subagent 改扁平）。且该分支是**完成通知高频路径**。
- 建议：第 134 行改为 `subagent({"action":"check","task_id":"{task_id}"})`，与第 25/70/138/142 行口径统一；建议补一条断言"完成通知不含 subagent(manage=" 的单测（现有 `test_subagents_background` 只断言取消分支文案）。

### OBS-1（观察项，LOW）：执行节点失联回收路径的 report 文案不区分取消来源
- 位置：`subagent_task_service.py:1907-1908` `_expired_status_and_report`：`cancelling → "取消请求已生效（执行节点失联）。"`
- 现象：用户取消后若任务靠租约过期回收（未赶上下一次心跳），task.report 显示"执行节点失联"而非"用户主动停止"。通知 header 仍按 meta.cancel_origin 正确显示"用户手动停止"，但 task 详情文案对用户/模型有误导。
- 本次实测遇到此路径，但**起因是测试期间重启 gunicorn 导致执行节点失联**，属环境噪音，不能单凭此判定为产品 bug。请他人确认是否需要让该分支也读 cancel_origin 输出差异化文案。

### OBS-2（观察项，LOW）：取消请求撞上任务自然完成的竞态
- 第一次真点取消时，任务恰好在取消请求到达前完成（finished_at=20:42:34），UI 提示"已请求取消"，但任务终态为 completed。
- 属预期竞态（用户点慢了），非缺陷；记录以免误判。

## 3.1 DEF-1 独立复测（2026-11-22 20:59，最新代码）
- 复测动机：测试期间代码被另一智能体改动过，需在**当前最新代码**上重新确认缺陷是否仍成立。
- 文件未再变动：`subagent_notification_text.py` 仍为 20:36:11、untracked，第 134 行旧语法仍在。
- **闭环证据（实跑）**：
  - 渲染完成通知（`status=completed`）输出中，A1「含 `subagent(manage=`」= True、A2「含扁平 `subagent({"action":"check"`」= True、A3「两者并存自相矛盾」= True。
  - 工具契约 `SubagentToolArgs.model_validate` 实测：`{"manage":{"action":"check","task_id":"tid"}}` → **拒绝**（`ValueError: subagent 参数已改为扁平 action 写法，不要再嵌套 dispatch/send/manage`）；`{"action":"check","task_id":"tid"}` → **接受**。契约源码 `subagent_tool_schema.py:137-148` `_explain_legacy_nested_shape` 对 `legacy in ("dispatch","send","manage")` 直接抛错。
- **定性升级**：DEF-1 由"确定"进一步确认是**会实际触发工具校验失败**的缺陷——模型照抄通知里第一处提示（`subagent(manage=...)`）必然被 schema 拒绝。修复建议不变（第 134 行改扁平 + 补完成通知断言单测）。复测未改任何产品代码。

## 4. 未完全覆盖（诚实说明）
- **"回到最新"按钮（ConversationSidePanel 新增）未做真点**：该按钮 v-if 要求 `autoFollow=true`，仅"过程 Tab + agent 模式 + 子智能体任务"满足；当前会话数据不足以自然触发滚动溢出。其逻辑由 `ConversationSidePanel.spec.ts`（+22 行）单测覆盖。如需端到端确认，请在有长过程输出的子任务详情面板上翻后点底部"回到最新"。
- **参数预览的中间 UI 态未截到图**：Qwen/deepseek 的 `tool_call_chunks` 把 args 一次性给出（抓帧显示单帧即含完整 delta），预览态在 UI 上停留极短；功能链路（SSE 帧 + 收口）已证实，UI 渲染由单测覆盖。
- 后台子任务"用户取消→通知 Run 唤醒主智能体并明确不续跑"的完整端到端（通知文本注入主模型上下文后主模型的实际行为）未验证——余额/模型限制下派发不稳定；通知文本本身已实跑渲染确认正确。
