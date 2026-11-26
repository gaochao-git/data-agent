# e0789447 续跑 Run 复用入口上下文契约 — 深度测试记录

- 日期：2026-11-23 15:28-15:38
- Commit：`e07894471d0ed7382a8774a2a70105f972ffa965`（fix: 续跑 Run 复用入口上下文契约）
- 关联：`a1f54092`（文档优化，仅 README +7 行）；基线 `400ab784`（上轮已测）
- 模型：`local/Qwen3.6-35B-A3B-4bit`；后端 5001 / 前端 vite 8089 / 真 MySQL `fxcjg_ai`
- 结论：**未发现缺陷**。续跑来源保留、旧契约兼容、真实 continuation 落库全部验证通过。

## 一、改动内容（实质 3 处 + README + 测试）

| 文件 | 改动 |
|---|---|
| `run_context.py::inherit_run_context` | `source` 参数由必填改为 `RunSource \| None = None`；不传时**保留父上下文的 `task_source`**，父为旧契约（无该字段）时原样保留 |
| `agent_inbox_service.py:779` | continuation 分支 `{**runtime_context, "query": ""}` → `inherit_run_context(runtime_context, overrides={"query": ""})` |
| `README.md` | 补"续跑 Run 不经过 start_run 但同样走 inherit_run_context，只覆盖 query 空串、保留父 task_source 与模型/数据源选择" |
| `test_run_context.py` | +2 例：`source=None` 保留父 task_source；父无 task_source 时保持缺失 |
| `test_agent_inbox_service.py` | +1 例：start_run 对带 task_source 但缺必填键的上下文抛 ValueError |

**意图**：上一轮 `400ab784` 的 continuation 路径仍用裸 dict 拼接，是契约漏洞——续跑会丢父来源。本 commit 把续跑也纳入统一构造器，`source=None` 表示"来源不变"。

## 二、自动化验证

| 项 | 结果 |
|---|---|
| 定向单测（run_context / agent_inbox_service / inbox_branch / completion_run_options / schedule_runtime / subagent_events） | **134 passed** |
| 本次新增 3 例精跑 | **3 passed**（`test_inherit_context_without_source_keeps_parent_task_source`、`...without_parent_task_source_keeps_it_absent`、`test_start_run_rejects_task_source_context_missing_required_keys`） |
| 复杂度（阈值15） | 基线 4260 函数仅 1 处超标 `agent_stream_events.py:712`（分数17；即上轮 701 处，行号因工作区并行改动偏移），非本 commit 引入 |

## 三、验证矩阵

| # | 场景 | 实测证据 | 结论 |
|---|---|---|---|
| 1 | 服务加载最新代码 | 源码 mtime 15:24:04，workers 15:27:59 启动（晚于源码），无需重启；5001/8089 健康 | ✅ |
| 2 | 直调：三种来源父 Run 续跑 | `inherit_run_context(parent, overrides={"query":""})`：user/schedule/subagent 三种父均 `src` 原样保留、`query=''`、`model` 继承、三必填键齐全 | ✅ |
| 3 | 直调：旧契约父 Run 续跑 | 父无 task_source → 续跑保持缺失（不引入新字段）、query 置空、model 保留旧值 | ✅ 兼容 |
| 4 | 直调：通知入口显式 source 不受影响 | `inherit_run_context(parent, source=SUBAGENT, ...)` → `src=subagent`（行为不变） | ✅ |
| 5 | 直调：空父上下文兜底 | `inherit_run_context({}, overrides={"query":""})` → `{"query":""}`，无异常 | ✅ |
| 6 | **真实 continuation 落库（核心）** | steer 接口入队消息 → 父 Run 完成后自动生成 continuation：**15:30:21 父 `kind=normal src=user q='请派发一个子智能体…'` → 15:31:14 子 `kind=continuation src=user q=''`** | ✅ |
| 7 | 定时入口真实落库（补上轮边界） | DB 15:22:54 `src=schedule q='1+1=几'`，证明 schedule 入口在新代码下正常提交 | ✅ |
| 8 | 通知入口真实落库（上轮已证） | DB 15:21:54 `src=subagent q='' dis=True evt=terminal` | ✅ |
| 9 | 页面全程错误 | `window.__errs`=[]；continuation 正常执行排队的子智能体盘点任务（8 YAML 文件、后台运行中），非悬挂 | ✅ |

## 四、关键 DB 证据（只读 `chat_data_agent_inbox`, record_type=run）

```
2026-11-23 15:31:14  kind=continuation  src=user      q=''                       ← 续跑继承父来源(本次核心)
2026-11-23 15:30:21  kind=normal        src=user      q='请派发一个子智能体…'      ← 父 Run
2026-11-23 15:22:54  kind=normal        src=schedule  q='1+1=几'                  ← 定时入口
2026-11-23 15:21:54  kind=normal        src=subagent  q=''          evt=terminal ← 通知入口
2026-11-23 14:52:15  kind=normal        src=<none>    q='…'                       ← commit 前旧数据(兼容)
```

三种入口 + 续跑 + 旧数据在同一张表正确共存，`task_source` 口径全程一致。

## 五、触发 continuation 的方法（留档）

1. 浏览器发一个耗时任务（本次用"派子智能体盘点语义模型"，约 40s）；
2. 任务运行中通过 steer 接口入队一条消息：
   `POST /dev-api/agents/api/chatdata/conversations/{conv_id}/steer`，body `{"query":"...","queue_type":"next-turn"}`
   （`/dev-api/agents` 是 vite 代理前缀；直接写 `/agents/api/...` 或 `/api/...` 会 404）；
3. 父 Run 完成 → `complete_run` 检测 `has_pending_messages` → 生成 `run_kind=continuation` Run；
4. 查 DB 确认续跑 `runtime_context.task_source` == 父_run 的来源、`query=''`。

## 六、诚实边界与注意事项

- 续跑的**来源保留**由直调 + 2 个单测精确锁定，`schedule`/`subagent` 父的续跑未在浏览器逐一触发（只需父来源存在即可继承，逻辑同一分支）。
- 测试产生的 continuation Run 在执行排队的子智能体盘点任务（后台、超时 3600s），已尝试停止；属测试副作用，非缺陷。
- **工作区有另一智能体的并行未提交改动**：`agent/middleware/subagent_stream.py`、`agent_stream_events.py`、新增 `tool_call_chunks.py` / `test_tool_call_chunks.py`。这些**不在本 commit 范围**，本次未测试、未触碰；复杂度扫描行号偏移（701→712）即由该并行改动引起。
- 未改任何产品代码、未提交；本次仅新增本测试记录文件。
