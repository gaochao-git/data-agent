# 400ab784 统一 Run 入口期上下文契约 — 深度测试记录

- 日期：2026-11-23 15:18-15:30
- Commit：`400ab784a51445af2cf2e3f97ca9a09a60ee16bf`
- 模型：`local/Qwen3.6-35B-A3B-4bit`
- 环境：后端 5001（重启加载最新代码）/ 前端 vite 8089 / 真 MySQL `fxcjg_ai`
- 结论：**未发现缺陷**。三入口契约、审计字段落库、旧数据兼容、历史回放全部验证通过。

## 一、改动范围（8 文件，+298/-50）

| 文件 | 改动 |
|---|---|
| `agent/run/run_context.py`（新增） | `RunSource` 枚举(user/schedule/subagent)、`RunContext` TypedDict、`build_run_context`/`inherit_run_context`/`validate_run_context` |
| `agent_stream.py` | 用户入口 `sql_completion_handler` 改用 `build_run_context(USER)` |
| `agent/run/agent_schedule_runtime.py` | 定时入口 `_build_runtime_context` 改用 `build_run_context(SCHEDULE)`，显式 `include_annotations=False` 保留历史口径 |
| `agent/run/subagent_event_service.py` | 通知入口 `_runtime_context` 改用 `inherit_run_context(SUBAGENT)` |
| `agent/run/agent_inbox_service.py` | `start_run` 对带 `task_source` 的上下文做必填校验；旧裸 dict 兼容 |
| `agent/run/completion_options.py` | 仅注释更新（disable_user_interaction 来源口径） |
| `agent/run/README.md` | 补任务入口契约 + 三入口冲突策略表 |
| `tests/unit/test_run_context.py`（新增） | 4 个契约测试 |

契约要点：`RUN_CONTEXT_REQUIRED_KEYS = (task_source, query, disable_user_interaction)`；校验仅检查 key 存在（`query=""` 为合法空值）。覆盖顺序固定「父上下文 → overrides → task_source」。

## 二、自动化验证

| 项 | 结果 |
|---|---|
| 定向单测（run_context / completion_run_options / agent_inbox_service / inbox_branch / schedule_runtime / subagent_events / agent_stream） | **190 passed** |
| 新增契约单测精跑 `test_run_context.py` | **4 passed** |
| 复杂度扫描（阈值15） | 4250 函数中仅 1 个超标：`agent_stream_events.py:701`（17分，既有欠账，非本次文件） |

## 三、真浏览器验证矩阵（Qwen3.6，抓 SSE 帧 + 只读查库 + 截图，全程 0 页面错误）

| # | 场景 | 实测证据 | 结论 |
|---|---|---|---|
| 1 | 用户入口普通对话 | `file_write` 全链路：261帧，`tool_args_delta×2 → tool_started → tool_progress → tool → tool_completed → done`，0 错误 | ✅ |
| 2 | 用户入口落库审计 | DB 最新 run(15:20:31) `src=user q='请直接用file_write…' dis=False annot=Y` | ✅ |
| 3 | 子智能体通知入口 | 派发子任务：329帧，`subagent` 工具完成；DB 通知 run(15:21:54) `src=subagent q='' dis=True evt=terminal`，父 run(15:21:27) `src=user` | ✅ |
| 4 | 新旧数据共存 | 同表内 commit 前旧 run(14:52 及之前) `src=<none>` 照常 finished/cancelled；新 run 带 src | ✅ 兼容旧裸 dict |
| 5 | 历史回放（老会话无 task_source） | 打开 11-22 21:24 老会话，工具卡 `写入文件 +20`、回复完整渲染，0 错误 | ✅ |
| 6 | 三入口构造器直调 | user/queued/schedule/subagent 的 task_source/query/disable/annotations 继承均符合契约；旧裸 dict 跳过校验；缺必填键正确抛 ValueError | ✅ |
| 7 | queued_message_id（query=""） | 直调 `user-queued` 形态：`src=user q='' dis=False`，三键齐全校验通过（浏览器需特定队列 UI，属可达性边界） | ✅ |
| 8 | 定时入口（schedule） | 浏览器不可达；直调构造 `src=schedule dis=True annot=N` + 单测 `test_schedule_context_omits_annotations` 覆盖 | ✅ |

## 四、关键 DB 证据（只读查询 `chat_data_agent_inbox`，record_type=run）

```
2026-11-23 15:21:54  src=subagent   q=''                   dis=True   evt=terminal   ← 通知 Run
2026-11-23 15:21:27  src=user       q='请派发一个子智能体：让它用 file'  dis=False               ← 用户派发
2026-11-23 15:20:31  src=user       q='请直接用 file_write 工具'       dis=False               ← 用户普通
2026-11-23 14:52:15  src=<none>     q='我们做的是多轮会话子agent'       dis=False               ← commit 前旧数据
```

新旧契约 Run 在同一张表共存，旧数据 `task_source` 缺失不影响任何消费侧（全仓搜索 `task_source` 仅新代码引用，纯审计字段，无严格 schema 消费）。

## 五、潜在回归面排查（均排除）

- **校验误伤**：生产 `start_run` 唯一调用方是用户入口，其 `build_run_context` 必带三键；定时/通知入口各自构造也带三键；continuation 路径直接写 DB 不走 start_run 校验 → 无误伤路径。
- **通知入口 parent 为空**：`overrides` 恒带 `query=""` + `disable=True`，`inherit_run_context` 最后强制写 `task_source` → 三键齐全。
- **annotations 口径漂移**：定时入口显式 `include_annotations=False`，与改动前"定时不携带 annotations"一致；用户/通知入口保留继承。
- **旧裸 dict 兼容**：`agent_inbox_service.py:184` 仅当 `"task_source" in runtime_context` 才校验，历史调用/旧 Run 完全不受影响。

## 六、服务状态

- 后端 gunicorn 重启（15:19:48，晚于源码 mtime 15:11）加载最新代码；端口 5001/8089 健康。
- 本次连远端库 `fxcjg_ai`（conf/.env-dev），只读查询未做任何写操作。
- 未改任何产品代码；工作区干净；全程未提交。

## 七、诚实边界

- 定时入口（schedule）与 queued_message_id 分支浏览器不可直接触发，靠后端构造器直调 + 单测覆盖，已精确锁住字段形态。
- task_source 为审计字段，无独立 UI 展示，验证以 DB 落库为准（最直接证据）。
