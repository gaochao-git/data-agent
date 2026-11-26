# 增量消息流功能 · Qwen3.6 真浏览器点击验证（2026-11-22 21:24–21:30）

## 被测范围
增量消息流 = 工具参数逐增量预览（`tool_args_delta`）+ 子智能体 token 级增量转发
（`subagent_message_delta` / `subagent_thinking_delta`）+ 前端「生成参数中 → 结构化」收口。
改动文件：`tool_args_preview.py`(新)、`agent_stream_events.py`、`sse_protocol.py`、
`agent/middleware/subagent_stream.py`、`web-vue .../ToolCallCard.vue`、`partialJson.ts`(新) 等。
模型：`local/Qwen3.6-35B-A3B-4bit`（本地，符合用户指定）。服务 worker 21:21 启动，
后端改动时间 20:04–20:36 均早于启动，已加载最新代码；前端 Vite HMR 自动更新。

## 自动化验证
- 后端定向单测（test_tool_args_preview + test_subagents_middleware + test_cov_agent_stream_branches）：**107 passed**
- 前端定向单测（ToolCallCard + partialJson + subagentTrail + useSubagentTaskStream）：**41 passed**
- 页面错误探针 `window.__errs`（error/unhandledrejection/console.error）：全程 **0 条**

## 真浏览器验证（SSE 抓取法：fetch + ReadableStream.tee 分流）
### 1. 主对话工具参数增量（file_write 长 content）
抓到事件链：`tool_args_delta`×2（第1帧带长 content delta，第2帧 `done:true` 收口）
→ `tool_started` → `tool_progress` → `tool_completed` → `done`。顺序正确，落盘成功，
工具卡正常收口（无卡在「生成参数中」）。

### 2. 主对话派发子智能体（subagent dispatch）
`tool_args_delta` 带扁平参数 `{"action":"dispatch","intent":...,"description":...}` + `done:true`
收口；`subagent_task_updated`×4 后台状态推进。派发卡片正确展示任务书 description。

### 3. 子智能体过程增量流（本次改动核心，进入「查看过程」面板抓取）
- `subagent_message_delta`：累计 **1400** 帧，正文逐 token（"先"→"获取"→"当前"→"可用的"…），
  同一 message_id，均带 parent_tool_call_id。
- `subagent_thinking_delta`：累计 **1686** 帧，思考逐 token（"用户"→"要求"→"分析"→"orders"…）。
- `tool_args_delta`：累计 **21** 帧，内层工具（load_skill/data_analyzing_tool/file_read）参数增量，
  带真实参数片段与工具名（验证 subagent_stream.py 的 index→id/name 反查逻辑生效）。
- 生命周期帧齐全：`tool_started`×11 / `tool_completed`×9 / `subagent_message`(完整收敛帧)×7。
- **收敛正确性**：任务完成后报告正文完整连贯，无 token 重复堆叠、无「工作中」卡死。
  核查「84.44%」5 次出现均为报告正文正常引用（概览/表格/详解/总结），非增量残留。

### 4. 过程面板取消按钮
「取消正在运行的子智能体任务」（title 匹配 StreamingStatus 新增入口）在任务运行中正确渲染、
可见可点、非 disabled。设计上取消统一由详情页顶部承载、消息流 trail 不再重复（单测
SubagentTaskProcessView.spec.ts:180 断言 cancellable=false），与实现一致。
真点取消受任务快速完成竞态影响未抓到「取消中」瞬时态（非缺陷，同 11-22 前次记录竞态）。

## 结论
增量消息流功能在 Qwen3.6 下**通过**：双链路（主对话 + 子智能体）增量事件完整下发、
参数预览正确收口、token 级正文/思考增量与完整消息收敛正确、0 页面报错。本轮**未发现新缺陷**。
DEF-1（完成通知旧嵌套语法，见 2026-11-22-tool-args-preview-cancel-origin.md）为本次改动伴生问题，
本轮未重复复测（已于 20:59 独立复测确认）。

## 收尾
未改任何产品代码；仅新增本记录文件。浏览器已关闭；服务 5001/8089 正常。
