# 行数展示 + 写入内容格式化预览 · Qwen3.6 真浏览器深度测试（2026-11-22 22:50–23:00）

## 被测改动（本轮"改了什么"）

三轮改动叠加，HEAD `c57aef7d`（feature/async-subagent）：

| 来源 | 内容 | 关键 code ref |
| --- | --- | --- |
| `c559dce2` | 修复工具行数展示与子智能体收口口径 | 见下 |
| `c57aef7d` | 工具参数预览改为 16ms 时间窗合并 | `tool_args_preview.py` |
| 工作区未提交 | 写入工具内容格式化预览（新组件） | `FileWritePreview.vue` 等 |

**c559dce2 六项修复**：
1. 主图完整 AIMessage 收口帧改为 `mergeToolCallFromMessage`，只补身份+参数，保留 `tool_progress` 写入的 +N/-M/intent（commit message 明确："本轮 file_write 的 +100 就是这样丢的"——正是用户报的"+N/-M 没了"根因）
2. 历史事件读取 `_with_history_tool_line_counts` 按 args 现算补齐行数（老会话回放恢复 +N/-M）
3. 子图 `tool_started` 加 `replace_args` 权威快照标记，前端仅对该事件整份替换参数
4. 子图 trail 终态守卫：重连重放 `tool_started` 不把 completed/error 打回 running
5. 子图过程快照先取 Redis 游标再读检查点，避免完成帧被续传跳过导致工具卡转圈
6. `getHistoryToolCall` 前端补 `contentLines/removedLines`

**c57aef7d**：32 字符阈值 → 16ms 时间窗合并（首片立即发、窗口内合并、收口前 flush 尾片），新增 `flush()` / `pending_tool_call_ids()`。

**工作区新组件**：`fileWritePreview.ts`（提取 file_write/file_edit 内容 + 按扩展名判格式）、`FileWritePreview.vue`（md/json/yaml/sql/code/text 分型渲染）、`FileWriteViewToggle.vue`（格式化/参数切换）、`ToolCallCard.vue` 与 `ToolCallDetailPanel.vue` 接入、`CodeFilePreview.vue` 加 sql 语言包。

## 自动化验证（先跑，全绿）
- 后端定向单测（`test_tool_args_preview` + `test_streaming_line_count` + `test_conversation_event_service_branch_coverage`）：**48 passed**
- 前端定向单测（fileWritePreview + subagentTrail + messageView + useConversationHistory + ToolCallCard + ToolCallDetailPanel）：**112 passed**
- 前端 `npm run type-check`（vue-tsc --build）：**exit 0**

## 环境
- 模型：`local/Qwen3.6-35B-A3B-4bit`（用户指定 qwen）
- URL：`http://localhost:8089/chatdata?parentComponentName=执法检查`（**必须带此参数**——`applyDatasourceFilters` 要求非空 parentName 才返回数据源组，否则 `noMatchedDatasource=true`，发送按钮 disabled、无法发消息）
- 后端 gunicorn 22:48:07 启动（晚于两 commit，已加载最新）；前端 vite HMR
- 抓帧法：hook `fetch` + `ReadableStream.tee()` 分流（复用既有方案，未造轮子）；错误探针 `window.__errs`（error/unhandledrejection/console.error）
- 全程 `window.__errs` = **0 条**

## 真浏览器 verification matrix

| # | 场景 | 预期 | 实测（真浏览器 + SSE 帧） | 结论 |
| --- | --- | --- | --- | --- |
| 1 | 历史回放行数（改动前 21:24 老会话） | 按 args 现算补 +N | 工具卡 `写入文件 +20`（内容重复20遍=20行），`text-emerald-500` 绿色 | ✅ 通过 |
| 2 | 主图 file_write 直播行数（写 report.md） | 工具_progress 发行数，收口帧不冲掉 | `tool_progress{content_lines:44,removed_lines:0,intent:创建季度复盘报告 Markdown 文件}`；展开工作过程见 `写入文件 +44` | ✅ 通过（用户报的问题已修复） |
| 3 | 主图 file_write 格式化预览（report.md） | 侧栏详情显示格式化/参数切换 + Markdown 渲染 | 工具详情面板 `格式化｜参数` 切换（默认格式化高亮）；`report.md｜Markdown｜45 行` 头 + Markdown 正文渲染；结果区原始 file_write args；点"参数"后预览消失、回原始 JSON | ✅ 通过 |
| 4 | JSON 文件格式化（config.json） | json 分支美化缩进 + 行号 | `tool_progress{content_lines:5}`；工具详情 `config.json｜JSON｜5 行` + CodeMirror 行号 + 2 空格美化；工作台文件预览同样美化 | ✅ 通过 |
| 5 | file_edit 修改工具（replace notes.txt） | +N/-M 双徽标 + "修改后内容"标签 + new_content | `tool_progress{content_lines:1,removed_lines:1}`；工具卡 `修改文件 +1 -1`（+绿/-红）；详情 `notes.txt｜文本｜修改后内容｜1 行` + 修改后文本；结果区 `{operation:edit,...,replaced_count:1}` | ✅ 通过 |
| 6 | 16ms 参数预览合并 | 首片立即发，窗口内合并，收口 flush 尾片 | Qwen 实测每条 file_write 均 `tool_args_delta`×2（第1帧带完整 delta、第2帧空 delta+done:true 收口），无逐 token 洪峰；多片合并由单测覆盖（48 passed） | ✅ 通过（真链路无回归 + 单测覆盖窗口逻辑） |
| 7 | 子智能体写文件（subagent→file_write） | 子图内层工具下行、主子收口正确 | 主图 `派发子智能体` tool_started(args 含完整任务书)；内层 `file_write call_7f5f…` tool_args_delta 带完整 content（sub_report.md）；`subagent_thinking_delta`×144 / `subagent_message_delta` / `subagent_message`×1 收敛；`subagent_steps`×5 / `subagent_task_updated`×5；回复正文完整、无 token 堆叠、无卡死 | ✅ 通过 |
| 8 | 生命周期完整性 + 页面错误 | 帧序正确、0 页面错误 | 每轮 `tool_started→tool→tool_progress→tool_completed→done`；全会话 18 类事件、375 帧；`window.__errs`=0 | ✅ 通过 |

## 结论
本轮三项改动在 Qwen3.6 真浏览器下**全部通过，未发现新缺陷**。用户报的"+N/-M 没了"根因（完整 AIMessage 收口帧冲掉行数字段）已由 `c559dce2` 的 `mergeToolCallFromMessage` 修复，直播（+44/+5/+1-1）与历史回放（+20）均恢复。新增的写入内容格式化预览组件在 Markdown/JSON/纯文本/修改后内容四类场景渲染正确，格式化↔参数切换双向可用。16ms 合并改动未引入回归。

## 观察项（非缺陷）
- OBS-1：`parentComponentName` 缺失会导致数据源组为空、发送禁用——测试方法学要求，测试 URL 必须带该参数；非产品缺陷（生产由宿主传入）。
- OBS-2：Qwen 的 tool_call args 基本单帧给全，`tool_args_delta` 多为"完整 delta + done 空帧"，16ms 窗口的"多片合并"收益在本模型上不可见，需 token 分片细的模型才能实测到；已由单测覆盖窗口逻辑。
- OBS-3：子智能体任务完成后，过程面板入口收敛（工作过程里只剩"派发子智能体"一行），内层 file_write 工具卡不在主对话展开——符合既有设计（过程详情在子任务面板），本轮通过 SSE 帧验证了内层工具下行与收敛。

## 收尾
未改任何产品代码；仅新增本记录文件。服务 5001/8089 正常，浏览器保留最终页面状态（未清理用户页面）。
