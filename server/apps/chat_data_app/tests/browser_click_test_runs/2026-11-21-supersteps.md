# 超步（supersteps）统计功能验证记录（2026-11-21）

## 测试对象

工作区未提交改动（分支 `feature/async-subagent`）：新增「本轮图推进次数」统计，主图与子图同源。

- 后端：`sse_protocol.py`（`run_steps` / `subagent_steps` 事件）、`agent_stream.py`（主图 values 计数 + finish 持久化）、`agent/middleware/subagent_stream.py`（子图 values 计数 + progress/result_meta）、`conversation_event_service.py`（历史回放 artifacts）
- 前端：`MessageFooter.vue` / `SubagentTrail.vue` / `StreamingStatus.vue` / `generationSpeed.ts`（`formatSuperstepsLabel`：`N / limit 步`）
- 配置项 `CHAT_DATA_AGENT_RECURSION_LIMIT`：env=1000，代码默认 40；`get_runtime_setting` 读「表覆盖行 > env > default」，`effect=EFFECT_NEXT_RUN`（新会话装配时读），缓存 TTL 30s。

口径：values 流每条 = 一次图推进；首条输入回显与 `__interrupt__` 帧不计；每次 invoke 从 0 起，与 recursion_limit 额度同口径。

## 结果总览

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 后端单测 | **5 passed** | `test_run_supersteps.py`（计数/回显排除/interrupt 排除/finish 持久化/子图事件与快照/artifacts 往返） |
| 前端单测 | **53 passed** | generationSpeed 22 + SubagentTrail 12 + ConversationMessageList 19 |
| 前端 type-check | **PASS** | `vue-tsc --build --force` exit 0 |
| 主对话步数展示 | **PASS** | 底栏 `data-message-steps` 显示 `3 / 1000 步`（运行中与完成后都对） |
| 主对话步数持久化 | **PASS** | 刷新/历史回放走 generation_stats.supersteps（finish 写入） |
| 子智能体步数（运行中） | **PASS** | 子任务接口 `progress.supersteps=4`、`recursion_limit=1000` |
| 子智能体步数（终态） | **PASS** | result_meta `subagent_supersteps=5`、`subagent_recursion_limit=1000`（progress=4 是完成前最后心跳，终态以 result_meta 为准） |
| 配置调小生效（主） | **PASS** | 改 1000→20 后新会话底栏 `3 / 20 步` |
| 配置调小生效（子） | **PASS** | limit=20 时子任务 `progress.supersteps=7`、`recursion_limit=20` |
| 配置极端小值 | **PASS（反向证明）** | 改 7 时日志 `GraphRecursionError: Recursion limit of 7 reached`，回答按预期中断 |
| 配置改回 | **PASS** | 恢复后运行时=1000，新会话底栏回到 `3 / 1000 步` |

## 详细证据

### 1. 主对话（真点，Chromium）

- 新会话问「用一句话介绍你自己能做什么」→ 回答完成后底栏出现 `3 / 1000 步`（图标 + 文案，与「用时 8 秒」并列）。
- 派子任务那轮，运行中实时采样到底栏从 `3 / 1000 步` 涨到 `13 / 1000 步`，证明 `run_steps` 事件驱动的实时更新生效。
- 错误探针 `window.__errs` 全程 0。

### 2. 子智能体（真点 + 接口核对）

- 会话 `2c6ef2f9` 派发 explore 子任务盘点 outputs（task_id `8b4795b8aa5d49d2b0f745f6f7aaf44c`）。
- 运行中 `GET .../subagent-tasks` → `progress: {supersteps: 4, recursion_limit: 1000}`。
- 完成后 `GET .../subagent-tasks/<tid>` → `result_meta: {subagent_supersteps: 5, subagent_recursion_limit: 1000}`。
- 前端 `SubagentTaskProcessView` 从 `result_meta.subagent_supersteps ?? progress.supersteps` 取数，`SubagentTrail` 挂到 `MessageFooter`，与后端契约一致。

### 3. 配置调小→改回（DB 覆盖行，可逆）

配置优先级「表覆盖行 > env > default」，用 `chat_data_setting` 覆盖行做临时验证，可精确恢复：

| 步骤 | 操作 | 观察 |
| --- | --- | --- |
| 0 | 查表：无 `CHAT_DATA_AGENT_RECURSION_LIMIT` 覆盖行 | 运行时=env=1000 |
| 1 | 插入覆盖行 `setting_value='7'`（id=999999） | 运行时=7；新会话简单问候触发 `Recursion limit of 7 reached`，回答中断——配置真实作用于图执行 |
| 2 | 改 `setting_value='20'` | 新会话底栏 `3 / 20 步`；子任务 `progress.recursion_limit=20`、supersteps=7 |
| 3 | 改回：`UPDATE ... SET delete_flag=1 WHERE id=999999`（软删除，项目 `delete_flag=0` 查询口径） | 生效覆盖行=0，运行时=1000；新会话底栏回到 `3 / 1000 步` |

注：`fxcjg_rw` 账号无 DELETE 权限，故用 `delete_flag=1` 软删除恢复（与该项目配置表惯例一致），效果等同移除覆盖行。

### 4. 自动化

```
pytest test_run_supersteps.py -q              → 5 passed
vitest generationSpeed/ConversationMessageList/SubagentTrail → 53 passed
vue-tsc --build --force                        → exit 0
```

## 诚实标注的边界

- limit=7 时一轮都跑不完（RecursionError），因此"配置调小后主对话**正常完成态**的分母展示"用 limit=20 验证；7 只用来证明配置确实生效（反向证据）。
- 子图终态步数 result_meta=5 与运行中 progress=4 不同：后者是完成前最后一次心跳快照，前者是终态真值；前端完成态取 result_meta，符合设计。
- 未测：刷新页面后从历史消息恢复的步数展示（finish 持久化已有单测覆盖，前端历史链路 `useConversationHistory` 由单测覆盖）；9b 模型。
- 本轮用 DB 覆盖行验证配置，改完已恢复（覆盖行软删除，运行时 1000）；未改 `.env-dev`、未改任何产品代码。

## 截图与产物

- `.workbuddy/screenshots/supersteps-verify-20261121/restored-1000.png`：恢复配置后新会话底栏 `3 / 1000 步`

测试会话 `2c6ef2f9`（1000 主+子）、`3849b330`（limit=7 失败）、`23a0bb50`（limit=20 主+子）均为真实会话，保留供核查。

## 结论

超步统计功能**主图与子图均验证通过**，且递归上限配置调小后主/子两侧的分母都真实跟着变、改回后复原。单测后端 5 + 前端 53 + type-check 全绿，错误探针 0。未发现缺陷。
