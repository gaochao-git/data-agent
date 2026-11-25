# 2026-11-25 run context 无 state 兜底 — 浏览器验证

## 环境

- 分支 / commit：`feature/async-subagent` @ `4a408590` + 工作区未提交改动（36 文件，+419/−207）
- 后端：gunicorn `main:app -c server/gunicorn.conf.py`
  - 首启：原主进程 62640 于 10:06:41 SIGTERM 优雅退出（2s 释放端口），10:06:54 新进程就绪 → 工作区改动（源码 mtime 09:41 / 09:49）已加载
  - TC-06 中途重启：10:36:03 停（EXIT 143 即 SIGTERM，符合预期），10:36:14 就绪
- 前端：`http://localhost:8089`，入口 `?parentComponentName=执法检查`
- 模型：`local/Qwen3.6-35B-A3B-4bit`（开启）
- 角色：`管理员` / `10001-测试用户`
- 数据源：`hive-localhost`，`ds_20260116115851_95505`，执行对象 6/6（财付通 ORG_CODE1 … 货拉拉 ORG_CODE6）
- 证据目录：`.workbuddy/evidence/2026-11-25-runctx/`

## 用例结果

| 用例 | 结果 | 证据 | 备注 |
|---|---|---|---|
| TC-01 主查询落盘 | **PASS** | `tool_result.py:138 查询工具结果已保存: result_id=53349f99-… size_chars=1028 rows=1`（10:10:26）；落盘 `user-data/workspace/.system/query_result/53349f99….json`（1066B，`total_count=5000`，ORG_CODE1 财付通） | 工具链 list_execution_targets 309ms → validate_dsl 1.9s → execute_query 6.7s（SQL 执行 1.98s）→ file_read 13ms；回答「5000 条」，2m20s；页面无「结果保存失败」 |
| TC-02 大结果 + 追问 | **PASS** | `.system/query_result/35da3fc7….json`（28.3KB，100 行 × 6 列）；追问 execute_query 5.2s 返回 2024-01…2026-06 按月分组 | 首轮模型长推理 4m15s 未收尾（疑逐行打印），按用户操作中断；追问 1m41s 正常完成。模型正文自述「共计5000条」与工具返回 100 条不符（模型口径，非产品） |
| TC-03 长文本读取 | **PASS（卸载未覆盖）** | 上传 415999B / 4000 行 `tc03-large-orders.txt`；file_read 按 200 行分块（1-250 / 251-450 …） | file_read 天然分页，未触发 `tool-offload` 信封；模型循环读全文件被人工中断。无归属失败、工具卡正常、无白屏 |
| TC-04 同步子智能体 | **PASS** | `子智能体任务已登记 task_id=e2f8e8ba… conversation_id=48635cd4…`（10:23:56）；右侧「工作台 → 子智能体管理」1 个智能体 | 同步 3m50s（派发卡 1m21s 时仍在跑），主对话收到「orders 6 字段 / 3 指标 / 4 维度 / 关联模型」结构化报告；**DEF-01** |
| TC-05 后台子智能体 | **PASS** | task `f043fac1…`：面板「已完成 / 3m46s / 17 次工具调用 / 上下文 30%」；10:30:07 主对话答「当前时间」时任务 running | 派发耗时 1m04s 后主对话即释放；唤醒轮 10:33:33 输出 2024/2025/2026 趋势结论 |
| TC-06 重启恢复 | **PASS（明确失败路径）** | task `48dcf6d4…`：10:35:54 转后台 → 10:36:03 停服 → 10:36:14 重启 → `10:38:31 租约过期回收 old_status=running new_status=lost` | 未永久停在 running；账本行保留（不丢失、不重复），会话历史与派发卡刷新后可回放；**OBS-01** 失联终态触发主智能体自动重派新任务 a944b9 |
| TC-07 主动取消 | **PASS** | 面板点取消 → toast「已请求取消，任务会在下一次心跳后停止」→ `10:40:42 任务收口 status=cancelled`，面板「已取消 1m41s」 | 父对话 10:41:33 输出「任务已停止 / 已取消（用户主动停止）」，未再派发同一任务；取消后工作区无新增业务产物（会话 8433fc4f 仅 `.system/query_result` + memory） |
| TC-08 插话 steer | **PASS** | 主对话调 `subagent` send →「补充消息已排队，将在子智能体下一次模型调用前注入」 | 任务 ID 仍为 `a944b9…`（未新建任务）；插话内容「修改查询范围 2025-01-01~2025-12-31」 |
| TC-09 刷新回放 | **PASS** | reload 后：会话 48635cd4 显示 4 轮问答 + 按月表格；会话 8433fc4f 3 轮 + 派发卡 + 取消结论；子任务状态 `已完成 / 执行节点失联 / 已取消` 全部保持 | 无重复助手消息、无空白卡；`GET …/subagent-tasks/{id}/trail` 对三个终态任务均 `available=false`（按设计：终态降级、回放走事件重放） |
| TC-10 定时任务 | **PASS** | 新建「TC-10 定时任务身份验证」（绑定 48635cd4 / 每天 09:00）→ 立即执行（10:43:37）→ 执行历史「成功 / 手动 / 2m57s / Agent Run 成功 / 无错误信息」 | `10:45:59 查询工具结果已保存 result_id=901e151f…`（定时链路同样走 run context 归属）；该会话 10:43:39 仅 1 次 run（无重复执行），10:46:18 输出「5000 条」 |

## 日志检查

计数窗口按「重启后」统计（≥ 10:06:54，含第二次重启窗口）：

| 关键字 | 重启后 | 全量（历史） | 说明 |
|---|---|---|---|
| `缺少工具结果归属字段: user_id` | **0** | 9 | 全部在 09:42:46–09:43:49，重启前旧进程，与本次改动前代码对应 |
| `当前会话缺少用户身份` | **0** | 0 | — |
| `回退 state` | **0** | 0 | — |
| `run context 缺少 user_id` | **0** | 0 | — |
| `缺少工具结果归属字段: conversation_id` | **0** | 62 | 全部早于 10:06:54；属子智能体报告外置时 state 缺 conversation_id 的历史问题，本轮未复现 |

前端 Console：每次 reload 后重装 `error` / `unhandledrejection` / `console.error` 钩子，全程 `window.__errs` 长度 **0**。

## 缺陷

### DEF-01 同步子智能体被租约回收标记为「执行节点失联」（与本轮身份改动无关）

- 现象：TC-04 同步任务实际 10:25:56 正常返回报告，但「子智能体管理」面板显示 `执行节点失联 / 57.0s / 结束 10:24`。
- 日志：`10:24:53 后台子智能体任务租约过期回收: task_id=e2f8e8ba… old_status=running new_status=lost`，而同步执行体继续跑到 10:25:56 才把报告回给父对话。
- 代码定位：`agent/run/subagent_recovery.py:54` `recovery_candidate()` 仅认 `execution_mode == "background"`；`subagent_task_service.reap_expired_tasks` 对非候选直接 `_mark_expired_terminal`。同步任务 `start_task` 后没有心跳续租（watcher 只在 `_run_background_task` 内），租约（`CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS`）到期即被判 lost。
- 影响：账本状态与面板展示（状态、结束时间、耗时）错误，不影响父 Run 拿到的报告；会误导用户与后续 `subagent manage` 查询。
- 建议：reaper 过滤 `execution_mode == "sync"`，或在同步执行路径续租。

## 观察项

- **OBS-01**：TC-06 中任务失联终态会唤醒主对话，主智能体据通知**自动重派**了一个新任务（`a944b9`，10:39:00）。恢复机制本身未重复创建，但用户视角是「一个失联 + 一个新任务」。建议在失联通知文案/提示词里明确「是否重派由用户决定」。
- **OBS-02**：跨进程重启后 `_recovery_runner_for()` 为空，`defer_or_expire_missing_runner` 走 120s 宽限后 fail-closed 为 lost（本次 2m28s 生效）。即「重启恢复」目前只能保证「明确失败」，不能续跑；如需真正续跑需在启动期按任务重建 runner。
- **OBS-03**：TC-02 首轮、TC-03 整文件读取都因小模型长推理/逐段循环被人工中断（模型行为，非产品缺陷）。
- **OBS-04**：`semantic_config_id` 未在界面手工指定，沿用历史成功口径由智能体在链路中选取（TC-01 实际用「订单模型」）；如需验证「界面指定语义模型」路径需另建用例。

## 未覆盖项

- `tool-offload` 信封（`_externalize_generic_text`）未压到：`file_read` 天然分块，`query_result` 走的是 `_finalize_query_result` 专用通道。建议补一条 `bash_run cat`（>32k 单次结果）用例。
- 子智能体终态过程详情（trail 面板）未展开验证，仅通过状态、派发卡与事件重放校验；trail 接口对终态返回 `available=false` 属设计行为。
- 定时任务的「到点自动执行」未等真实调度（用「立即执行」代替，执行历史来源=手动）。
- 多用户/跨角色（USER_A 之外）归属校验未覆盖。

## 截图

- `tc01-01-new-conv.png` 新建会话
- `tc01-02-ds-picker.png` 执行对象选择（6/6）
- `tc01-04-toolcard-result.png` 订单总数 5000 工具卡
- `tc03-01-read-chunks.png` 大文本分块读取
- `tc04-01-sync-subagent.png` 同步子智能体报告
- `tc04-02-subagent-panel.png` 子智能体管理入口
- `tc05-01-panel.png` 后台任务 + 主对话并行
- `tc06-01-after-restart.png` 重启后会话恢复
- `tc09-01-replay-c1.png` 回放（按月表格）
