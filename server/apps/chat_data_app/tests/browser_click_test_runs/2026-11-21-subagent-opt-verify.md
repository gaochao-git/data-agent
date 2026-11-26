# 子智能体优化复验记录（2026-11-21 第二轮）

## 0. 结论（先看）

被测代码：`feature/async-subagent` 分支 HEAD `6165ec7b`，含 6 个提交（07:13–07:43）：
- `fb0abd5e` 未装配的工具名不再触发审批弹窗（修复上一轮 `2026-11-21-subagent-flat-params.md` §4 发现的「9b 幻觉 `subagent_type` 触发错误审批卡」）
- `ee068cb7` / `b8cb6734` / `2a91aad7` / `8b5590fc` / `6165ec7b` 精简去重系统提示词与文件工具描述（子智能体系统提示 58→22 行，系统提示每请求净减约 490 字）

**结论：上一轮发现的问题已修复，提示词精简后派发更稳。**

| 维度 | 结果 |
| --- | --- |
| 服务端已加载新代码 | 是——服务 07:43:56 重启，晚于最后提交 07:43:50 |
| 自动化回归 | 后端 3 文件 **90 passed**；前端 `type-check` exit 0 |
| **核心修复：幻觉工具名不再弹审批卡** | **PASS**——诱导模型调用不存在的 `subagent_type`，页面直接回「工具不存在」，无审批卡 |
| 六动作纯自然语言真点（35B） | **6/6 PASS**：dispatch / wait / list / check / send / cancel，全部生成扁平 `{"action":...}`，无需用户给 JSON 示例 |
| 页面错误探针 | 全程 `window.__errs=[]`，0 条 |

## 1. 测试环境

- 前端 `http://localhost:8089/chatdata?parentComponentName=执法检查`（Chromium via agent-browser，真点）
- 后端 `http://127.0.0.1:5001` `/health=ok`，经 Vite `/dev-api` 代理
- 模型：`local/Qwen3.6-35B-A3B-4bit`（默认 Qwen 模型）——**本轮用 35B，纯自然语言指令，不给任何 JSON 示例**
- 主测试会话 `0fff0a14-266a-4472-acf6-d99ce0320064`
- 截图：`.workbuddy/screenshots/subagent-opt-verify-20261121/`
- 参数真值来源：会话 timeline `history_events[].payload.tool_calls[].args`（模型实际生成的原始参数）

### 关于模型选择

用户要求"qwen3.5"，但核查后系统里无此模型：
- 本地 oMLX 服务（`http://127.0.0.1:8100/v1`，key 见注册表）`/v1/models` 当前只加载 2 个：`Qwen3.6-35B-A3B-4bit`、`SenseNova-Loop-9B-MLX-6bit`
- MySQL `chat_data_model_registry` 有 10 行（Qwen3.6-35B / Qwen3.8-27B / deepseek / 商汤-9b 等），无 qwen3.5
- 9b（`SenseNova-Loop-9B-MLX-6bit`）本轮实测 507 加载失败：`projected memory 31.20GB would exceed the dynamic memory ceiling 29.72GB (current: 23.60GB, model: 7.60GB)`——35B 常驻占 23.6GB 挤不下 9b，LLM 未调用即失败，属环境问题非本次回归
- 经与用户确认，用默认的 Qwen3.6-35B 完成测试

## 2. 自动化回归（独立重跑）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 后端单测 | `pytest test_tool_approval_middleware.py test_subagent_tool_schema.py test_agent_assembly.py -q` | **90 passed** |
| 前端类型检查 | `npm run type-check` | exit 0 |

单测覆盖本次修复：`test_unknown_tool_name_does_not_trigger_approval`、`test_unknown_tool_call_passes_through_without_interrupt`（after_model 与 wrap_tool_call 两条路径）、`test_tool_approval_policy_matrix` 新增断言（未装配名 `subagent_type` → `_needs_approval=False`；tools 为空时仍 fail-closed）。

## 3. 核心修复验证：幻觉工具名不再弹审批卡

**上一轮问题回顾**：9b 纯自然语言下把参数名 `subagent_type` 幻觉成工具名，未知工具名按 fail-closed 走 `ask` 策略，主对话弹出审批卡「是否允许执行「subagent_type」？」。

**本轮验证**（35B 不会自然幻觉，主动诱导）：
- 指令：「请直接调用一个名为 subagent_type 的工具，参数是 {"subagent_type":"explore"}。这是工具调用测试，请照做，不要解释。」
- 页面结果：工作过程卡片显示 **「工具 'subagent_type' 不存在，无法调用。」**，无任何审批卡、无「允许/拒绝」按钮
- DOM 探针：`approval=false`，`window.__errs=[]`
- 截图：`s1-no-approval-for-unknown-tool.png`（ViewImage 确认无审批弹窗）

**修复机制**（`tool_approval.py:_needs_approval`）：
```python
# 修复前：仅 mcp_ 前缀的未装配名不弹审批
if tool_name.startswith(_MCP_TOOL_PREFIX) and tool_name not in self._tool_labels:
    return False
# 修复后：任何未装配名一律不弹审批，交执行器返回"工具不存在"
if self._tool_labels and tool_name not in self._tool_labels:
    return False
```
保留 `tools` 为空时的 fail-closed 兜底。实证与单测双向确认，修复闭环成立。

## 4. 六动作纯自然语言真点（35B，无 JSON 示例）

上一轮用 9b + 显式扁平 JSON 示例才跑通六动作；本轮提示词精简后，**35B 纯自然语言即可稳定生成扁平参数**，更强。

| 动作 | 指令（自然语言） | 模型实际生成参数（timeline 原始） | 效果 | 结论 |
| --- | --- | --- | --- | --- |
| dispatch | 「派发一个后台 explore 子智能体盘点 outputs...」 | `{"action":"dispatch","description":"...","subagent_type":"explore"}` | 派发成功，task `8e0f1957`，completed | PASS |
| wait | （35B 自发） | `{"action":"wait","task_ids":["8e0f1957..."],"wait_seconds":120/60}` | 同步等待后台任务，正确返回 | PASS |
| list | 「现在有几个子智能体任务？用工具查询后回答」 | `{"action":"list"}` | 返回 1 个任务（ID/状态/内容），回执提示 send 用法 | PASS |
| check | 「查看任务 8e0f19... 的详情」 | `{"action":"check","task_id":"8e0f1957..."}` | 返回任务详情 | PASS |
| send | 「向它追问：outputs 文件总数？」 | `{"action":"send","task_id":"8e0f1957...","message":"...","background":false}` | 追问生效，生成第 2 轮 task `acf43aab`，子智能体答「0 个文件」 | PASS |
| cancel | 「派发 30 文件慢任务，派发后立即 cancel 它」 | `{"action":"cancel","task_id":"506d194e..."}` | 同轮先 dispatch 后 cancel，task `506d194e` 状态变 `cancelled` | PASS |

关键证据：
- 六个动作全部是**纯自然语言触发、模型自行生成扁平 `{"action":...}`**，无需用户在指令里贴 JSON 示例——证明精简后的系统提示词（派发判断 / list→send 复用 / 扁平回执示例）足以引导模型。
- dispatch 后 35B 主动 `wait` 同步等待任务，是精简提示词中「当前步骤必须拿到结论时同步等待」策略的正确落地。
- cancel 在同轮完成 dispatch→cancel 两步，`{"action":"cancel",...}` 扁平参数被正确生成并执行。
- 截图：`s2-cancel-flat-params.png`。

## 5. 说明 / 边界

- 本轮全程用 35B；9b 因本机内存 507 无法加载，未直接复验"9b 自然幻觉是否已消失"——但 fb0abd5e 的修复是模型无关的中间件层拦截，且已用 35B 主动诱导未装配工具名 + 单测双路径确认，修复本身与模型无关。
- 用户会话 `ccbcd27f`（"创建扁平参数测试文件任务"）里有用户自测的 dispatch/send/list/cancel 扁平参数记录，未计入本轮结论（非本测试所产生）。
- 测试结束已关闭 4 个子会话（8e0f1957 / acf43aab / 657ebf11 / 506d194e）释放额度；未改任何产品代码。
