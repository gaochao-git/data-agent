# 模型口径与生成速率口径验证记录（2026-11-20）

## 0. 测试环境与方法

- 仓库：`/Users/gaochao/work/git_repo/data-agent`，分支 `feature/async-subagent`
- 前端：`http://localhost:8089/chatdata?parentComponentName=执法检查`（Chromium，viewport 1600×1200，agent-browser 真点）
- 后端：`http://127.0.0.1:5001`，`/health = {"status":"ok"}`（gunicorn 多 worker）
- 模型注册表 ID（页面显示带 provider 前缀）：
  - 默认 `Qwen3.6-35B-A3B-4bit` → UI `local/Qwen3.6-35B-A3B-4bit`
  - `deepseek-v4-flash` → UI `deepseek/deepseek-v4-flash`
  - `sensenova-loop-9b-mlx-6bit-713bf770` → UI `local/商汤-9b`
- 新会话缓存键：`sessionStorage['chatdata.current_window_model_id']`，**只记用户手选**（`@model-picked`→`rememberCachedModelId`），历史/运行回填不写缓存。
- 全程页面挂 `window.onerror` / `unhandledrejection` / `console.error` 探针到 `window.__errs`；每批结束均为 **0 条**。
- 截图目录：`.workbuddy/screenshots/model-rate-20261120/`

判据口径（读代码确认）：
- 后端 `get_latest_model_id`（`conversation_event_service.py`）：有 active run → 取 `runtime_context.model_id`；否则取最后一条助手消息 `generation_stats.model`；再经 `_resolve_registered_model_id` 归一到注册表 ID，空值回默认。
- 前端 `pages/index.vue`：新会话 `selectedConversationModelId = readCachedModelId()`；历史会话 `= context.modelId`；`conversationSelectionSeq` 令牌防快切竞态；运行态由 5s/30s 轮询 `syncConversationModelFromContext` 同步。
- 完成态速率（`generation_speed.py`/`agent_stream.py`）：`duration_ms = (首token墙钟 → 流结束) − 窗口内工具耗时`；`tokens_per_second = round(output_tokens / (duration_ms/1000))`；TTFT 不进分母。子智能体 `_aggregate_subagent_token_usage` 复用同一对函数。

---

## A. 新会话模型口径（5/5 PASS）

| 编号 | 场景 | 操作 | 观测结果 | 结论 |
| --- | --- | --- | --- | --- |
| A1 | 无缓存→默认 | 清缓存刷新，空白新会话 | 显示 `local/Qwen3.6-35B-A3B-4bit`，err 0 | PASS |
| A2 | 有缓存→缓存模型 | 写 `deepseek-v4-flash` 刷新 | 显示 `deepseek/deepseek-v4-flash`，err 0 | PASS |
| A3 | 缓存失效→回默认 | 写 `missing-model` 刷新 | 显示 `local/Qwen3.6-35B-A3B-4bit`，无空选择、无报错，err 0 | PASS |
| A4 | 新建任务沿用缓存 | 手选 deepseek → 点「新建任务」 | 新会话仍 `deepseek/deepseek-v4-flash`，缓存写入 `deepseek-v4-flash` | PASS |
| A5 | 换组新建沿用缓存 | 手选商汤9b → 数据源组切到 `mysql-…3307-fxcjg_ai` 并确定 | 新任务 `local/商汤-9b`（非默认 Qwen），缓存 `sensenova-loop-9b-mlx-6bit-713bf770`，err 0 | PASS |

---

## B. 历史会话模型口径（5/5 PASS）

| 编号 | 场景 | 操作 | 观测结果 | 结论 |
| --- | --- | --- | --- | --- |
| B1 | 历史显示最后使用模型 | 清缓存，打开 `bf307e9f`（派发只读盘点工作区子智能体） | 显示 `deepseek/deepseek-v4-flash`；接口 `model_id=deepseek-v4-flash` | PASS |
| B2 | 另一历史会话返回 Qwen | 打开「月报2」`ed900b04` | 显示 `local/Qwen3.6-35B-A3B-4bit`；接口 `model_id=Qwen3.6-35B-A3B-4bit` | PASS |
| B3 | 历史不污染新会话缓存（关键） | 写缓存商汤9b → 打开 B1（显示 deepseek）→ 点新建任务 | 新会话 `local/商汤-9b`，缓存仍 `sensenova-loop-9b-mlx-6bit-713bf770` | PASS |
| B4 | 历史内手选更新缓存 | 清缓存 → 打开历史 → 手选 deepseek → 新建任务 | 缓存 `deepseek-v4-flash`，新会话 deepseek | PASS |
| B5 | 旧会话无模型记录 | 打开 2026-09 老会话「用户问候对话开场」 | 正常打开、内容非空、显示默认 Qwen、缓存不被污染、err 0；接口 `model_id=Qwen3.6-35B-A3B-4bit`（空值回退默认） | PASS |

接口证据（真 dev MySQL 经 Vite 代理 `/dev-api`）：
- `bf307e9f-6852-495e-b2e0-701a29e4e964/context` → `model_id=deepseek-v4-flash`
- `ed900b04-0f12-496c-a439-85ac563552c0/context` → `model_id=Qwen3.6-35B-A3B-4bit`
- 老会话 `7b59c85e…`/`32065100…`/`bbb74754…` → 均 `model_id=Qwen3.6-35B-A3B-4bit`（回退默认）

---

## C. 运行中模型口径（5/5 PASS）

| 编号 | 场景 | 操作 | 观测结果 | 结论 |
| --- | --- | --- | --- | --- |
| C1 | 当前 Run 模型立即显示 | 新会话手选 deepseek，发长任务，运行中两次采样 | 运行期选择器持续 `deepseek/deepseek-v4-flash`；active run `443fd5f4…` `runtime_context.model_id=deepseek-v4-flash` | PASS |
| C2 | 刷新后恢复运行中模型（强证据） | 缓存改商汤9b → 刷新 → 重开运行中会话 `ef17ece7…` | 显示 `deepseek/deepseek-v4-flash`（来自 run，非缓存商汤、非默认）；接口 `model_id=deepseek-v4-flash` | PASS |
| C3 | 另一窗口启动 Run 本窗口轮询同步 | Tab A 打开 `bf307e9f`（显示商汤，未刷新）；Tab B 同会话手选 deepseek 启动 active run（model=deepseek）；Tab A 前台等待轮询 | Tab A 从 `local/商汤-9b` 自动变为 `deepseek/deepseek-v4-flash`，全程未刷新/未手选；err 0 | PASS |
| C4 | Run 结束后保持最后模型 | run 结束（finished/cancelled）后清缓存，切走再重开 `bf307e9f` | 显示 `deepseek/deepseek-v4-flash`，与 context `model_id=deepseek-v4-flash` 一致 | PASS |
| C5 | 快速切会话竞态 | ①连点「月报2(Qwen)」→「派发会话(deepseek)」；②点历史后立即新建任务 | ①停在最后点击会话且显示 `deepseek/deepseek-v4-flash`（与该会话 context 一致），晚返回 Qwen 请求未拉回；②停在新会话（DataBuddy 空态），未被旧 context 拉回，err 0 | PASS |

C3 备注：首次 6s 未同步是因 Tab A 空闲轮询周期 30s（且 `document.hidden` 时跳过），等覆盖一个周期后同步成功——符合代码设计（有 active run 时 5s、空闲 30s）。
C5 备注：最初用「数据仓库分层建模」会话测出显示 Qwen 曾疑似 FAIL，复核发现该会话遗留一个卡在 `waiting_user` 的脏 run（context=deepseek 但最后助手消息气泡为 Qwen，接口与消息口径不一致），属测试数据污染；改用干净会话 `bf307e9f` 重测 PASS。此处不作为产品缺陷，但记录该数据不一致现象供后端侧关注。

---

## D. 速率口径（D1/D2/D3/D5 验证，D4 环境受限）

### D1 主对话完成态公式 — PASS
从 timeline `history_events` 取真实 `generation_stats`，三条均满足 `tps = round(output_tokens/(duration_ms/1000))`：
| model | output_tokens | duration_ms | 计算 | tokens_per_second |
| --- | --- | --- | --- | --- |
| SenseNova-Loop-9B | 35 | 3116 | 35/3.116=11.2 | 11 ✓ |
| deepseek-flash | 46 | 2434 | 46/2.434=18.9 | 19 ✓ |
| deepseek-flash | 207 | 8175 | 207/8.175=25.3 | 25 ✓ |

TTFT 不进分母：第三条 `first_token_ms=7364` 远大于 `duration_ms=8175` 的解码口径，若 TTFT 计入则速率会显著更低。页面气泡显示 `25 tok/s` / `用时15秒`，与 `tokens_per_second=25`、`total_duration_ms=15539` 完全一致。截图 `D1-D3-speed-badges.png`。

### D2 主对话实时速率 — PASS
长回答生成中，页面实时速率徽标持续变化：`~38.8 → ~33.6 → ~27.3 → ~41.1 tok/s`（`~` 前缀表示实时滑窗估算），同期正文长度 4033→4230→4444 同步增长，证明约每 500ms 按 2s 窗口刷新、不冻结。代码侧 `GenerationSpeedSampler`：`SPEED_WINDOW_MS=2000`、`SPEED_EMIT_INTERVAL_MS=500`。

### D3 子智能体完成态 — 口径 PASS（页面新派发受模型行为限制）
- 公式一致性（读代码）：`subagent_stream.py::_aggregate_subagent_token_usage` 复用 `decode_duration_ms` + `decode_tokens_per_second`，与主对话 D1 同一实现；首 token→流结束扣工具耗时，TTFT 不进分母。
- 真实 DB 数据（`chat_data_agent_inbox` task 的 `subagent_token_usage`）：
  - `c2ff7d57…` deepseek-v4-flash，input 52085 / output 1485 / tps 41
  - `03479a96…` deepseek-v4-flash，input 31161 / output 1968 / tps 166
  - `476d8442…` 商汤9b，input 14816 / output 500 / tps 4
  模型、输入/输出 token、完成态 tok/s 均落库且可展示（单工具测试轮已验证工作过程面板渲染这些字段）。
- 限制：本轮用 deepseek 高思考档派发时模型陷入冗长思考（“思考/子智能体 2m14s”）未及时产出新任务卡，故未取到**本轮新派发**的页面完成态截图；口径由代码 + 真实存量数据 + 前轮页面证据三方确认。

### D4 子智能体实时速率不受预览封顶影响 — 未测（环境受限）
需要子智能体单次输出 > 32000 字符触发预览封顶，本地 9b/deepseek 在测试时限内未产出该量级输出，无法真实验证“预览停止增长后实时 tok/s 仍更新”。如实标注为 **未覆盖**，不伪造 PASS。代码侧实时速率来自 SSE `generation_speed` 采样，与预览文本截断是两条独立链路（`SubagentTrail` 的 `liveSpeed` 与文本预览分别取值），但本轮无页面实证。

### D5 旧任务无计时锚点 — PASS（代码+数据）
- 后端：旧任务 `_aggregate_subagent_token_usage` 无 timing 印章时不产 `tokens_per_second`（如 `56a931dd…`/`a63dbe1e…` 存量任务 tps=None）。
- 前端：`MessageFooter.vue` 中 `usageSpeedText` 仅当 `usageSpeed>0` 显示具体值，否则显示 `-- tok/s`，不伪造异常高值；token 明细行 `buildSubagentTokenUsageRows` 同样只在 `speed>0` 输出速率行。口径正确。

---

## E. 自动化回归（全绿）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 前端定向 spec | `vitest run ConversationInputBar.spec.ts ModelPicker.spec.ts` | **28 passed**（各 14），exit 0 |
| 前端类型检查 | `npm run type-check`（vue-tsc --build） | exit 0，无输出错误 |
| 后端模型选择单测 | `pytest apps/chat_data_app/tests/unit/test_conversation_model_selection.py -q` | **4 passed** |

接口抽查：`context.data.model_id` 均为注册表 ID；timeline 最后助手消息 `generation_stats.model` 与 context 一致；运行中取 `runtime_context.model_id`；子智能体取 `subagent_token_usage.model/tokens_per_second`。

---

## 汇总

| 批次 | 结果 |
| --- | --- |
| A 新会话模型口径 | 5/5 PASS |
| B 历史会话模型口径 | 5/5 PASS |
| C 运行中模型口径 | 5/5 PASS |
| D 速率口径 | D1/D2/D3(口径)/D5 PASS；D4 环境受限未测 |
| E 自动化回归 | 全绿（28 + 4 测试、type-check） |

全程控制台错误探针 **0 条**（A/B/C/D/E 各阶段复查均为 0）。旧快照数据不一致（C5 备注的脏 run）与 D4 未覆盖两项如实标注，不冒充 PASS。

证据截图：`C5-race-wrong-model.png`（曾疑似的竞态画面，复核为脏 run 数据）、`D1-D3-speed-badges.png`（完成态速率徽标）。
