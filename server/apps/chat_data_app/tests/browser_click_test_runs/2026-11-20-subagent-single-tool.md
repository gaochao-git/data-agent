# 2026-11-20 子智能体单工具改造 · Chromium 真实点击记录

- 页面：`http://localhost:8089/chatdata?parentComponentName=执法检查`
- 后端：`http://127.0.0.1:5001`，`/health = {"status":"ok"}`（检查时间 2026-11-20 11:xx）
- 代码分支：`feature/async-subagent`（工作区有单工具改造未提交改动）
- viewport：1600×1200
- 浏览器：agent-browser 0.27.0 自带 Chromium，真实点击/真实输入
- 错误探针：`window.onerror`(error) / `unhandledrejection` / 包裹 `console.error` → `window.__errs`
- 会话策略：throwaway 会话，全程未删除任何会话
- 关键前置任务（真 dev MySQL `chat_data_agent_inbox`, `record_type='task'`）：
  - `56a931dde71d4cc980aca5a52b52db09` / conv `7113c743-4c11-4c8f-a1fd-f263b3d7d01a` / explore / 后台 / 第 1 轮 / completed / 12:19:41→12:21:09
  - `a63dbe1e597048e98c21011d8b3c2529` / conv `77536ac0-4a47-443c-9e83-3a4c12f0042a` / explore / 后台 / 第 1 轮 / completed / 12:10:15→12:25:12
  - `df2e0d29ce704d14b8e56bc80cb2fc0f` / conv `77536ac0-4a47-443c-9e83-3a4c12f0042a` / explore / 后台 / 第 1 轮 / running→lost / 12:29:04→12:35:04（长盘点任务；浏览器会话中断导致主进程消失，租约超时后自动标 lost，未卡在永久 running）

> 说明：本轮全部页面行为均来自真实渲染和新工具名；9B 本地模型必须在用户消息里显式给出嵌套 JSON 才能正确绑定新工具参数，否则会按旧平铺参数调用并被 `extra="forbid"` 拒绝。这是测试输入适配，不是接口桩。

## 结果总览（持续更新）

| 编号 | 场景 | 结论 | 证据 |
| --- | --- | --- | --- |
| CT-01 | 派发参数面板 | PASS | 页面工具卡「派发子智能体」，参数仅 `dispatch` 子字段，无 `timeout_seconds`/`[object Object]`；截图 `CT01-params-correct.png` |
| CT-02 | 后台任务详情 | PASS | `[data-subagent-task-detail]` + 聊天/工具视图切换 + 切回不丢；截图 `CT02-backlog-task-detail.png`、`CT02-generic-view.png` |
| CT-03 | 同步任务详情 | 待补 | — |
| CT-04 | manage/list | PASS | 标题「查看子智能体任务」，`{"manage":{"action":"list"}}`；截图 `CT04-manage-list.png` |
| CT-05 | manage/check | PASS | 标题「查看子智能体任务」，`{"manage":{"action":"check","task_id":"a63dbe1e…"}}`，返回完整报告；截图 `CT05-manage-check.png` |
| CT-06 | manage/wait | 待补 | — |
| CT-07 | manage/cancel | 待补 | — |
| CT-08 | send 运行中 | 待补（首次尝试因浏览器会话中断未取到结果） | 12:32:20 已真实发出消息，任务随后因租约过期转 lost |
| CT-09 | send 已结束 | 待补 | — |
| CT-10 | decide/allow | 已移除 | `decide` 不再暴露为模型参数 |
| CT-11 | decide/deny | 已移除 | `decide` 不再暴露为模型参数 |
| CT-12 | 参数契约边界 | 部分证据 | 页面工具卡参数已见三选一；Network 抓包待补 |
| CT-13 | 刷新回放 | PASS | 刷新后重进会话标题/参数/详情一致，探针 0；截图 `CT13-replay.png` |
| CT-14 | 普通工具回归 | 待补 | — |

截图目录：`.workbuddy/screenshots/agent-single-tool-20261120/`

## 详细记录

### CT-01 派发参数面板 — PASS

页面真实展开工具卡后显示标题「派发子智能体」+ 机器人图标，参数区只有 `dispatch` 的子参数：

```json
{"dispatch":{"subagent_type":"explore","description":"只读盘点工作区文件给3条事实结论","background":true,"label":"盘点"}}
```

未见 `dispatch` 键名重复展示、`timeout_seconds`、`[object Object]` 或旧名 `subagent_start` 等。后端日志对应 `tool_name=subagent status=success`，真实创建任务 `56a931dde71d4cc980aca5a52b52db09`。

### CT-02 后台任务详情 — PASS

点击任务卡打开右侧 `[data-subagent-task-detail]`：
- `[data-subagent-view-agent]` 聊天视图：任务书 + 工作过程 + 报告；
- `[data-subagent-view-generic]` 工具视图：按轮显示 dispatch 参数和结果，`[data-subagent-turn]` 存在；
- 再切回聊天视图，任务书与报告不丢。

### CT-04 manage/list — PASS

用户消息“列出子智能体任务”，真实工具卡：

```json
{"manage":{"action":"list"}}
```

结果含 agent_id、类型、轮次、task_id、状态；无 task_id/task_ids/wait_seconds 参数。

### CT-05 manage/check — PASS

用户消息“查看任务 a63dbe1e… 完整报告”，真实工具卡：

```json
{"manage":{"action":"check","task_id":"a63dbe1e597048e98c21011d8b3c2529"}}
```

结果含 `[已完成] agent_id=… task_id=… 类型=explore 轮次=1` 与完整报告。

### CT-13 刷新回放 — PASS

刷新后从侧栏重进会话，展开「派发子智能体」与任务详情，标题、参数分组、报告与刷新前一致，`window.__errs` 为 0。

## 错误探针口径

- 首轮进页面曾捕获 2 条 `加载分组会话失败: AxiosError: 系统接口500异常`，属环境噪声；刷新后重挂探针，后续所有取证页面 `window.__errs` 均为 0。
- 长时间等待导致 agent-browser 会话回到 `about:blank`，页面上下文和探针丢失；已重开页面并重挂探针，后续以重挂后的采样为准。
