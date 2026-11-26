# 今日全部 commit 测试矩阵（2026-11-21）

- 日期：2026-11-21 15:48~15:52
- 范围：今日 16 个 commit（`git log --since 2026-11-21`），按每个 commit 的改动文件映射到测试逐组实跑，形成覆盖证据。
- 环境：后端 `env -u PYTHONPATH fxcjg python -m pytest`（每组独立 `--basetemp`）；前端 `env -u NODE_OPTIONS ./node_modules/.bin/vitest run` + `vue-tsc --build --force`。HEAD=`2658d0f5`，工作区干净。

## 后端单测（按 commit 分组实跑）
| commit | 时间 | 主题 | 映射测试文件 | 结果 |
|---|---|---|---|---|
| `2658d0f5` | 15:31 | 模型注册表刷新事务冲突 + turn_error 持久化 | db_session_transaction / model_registry_service / conversation_event_service / agent_stream / agent_run_support_branch_coverage / run_supersteps | **200 passed** |
| `6af3d663` | 12:50 | 失败步数保留 | run_supersteps | **7 passed** |
| `4c87653e` | 12:31 | 主/子智能体步数展示与持久化 | run_supersteps / subagents_middleware | **79 passed** |
| `74505b2d` | 11:54 | data2image 输入约束与折线图可读性 | data2image_tool | **63 passed** |
| `55b72d01` | 11:12 | data2image 仅消费系统结果 | data2image_tool | **63 passed** |
| `9e86a414` | 10:59 | 新增 data2image 工具 | data2image_tool / conversation_workspace / workspace_branch_coverage / chat_data_agent_middleware / data_agent_graph_flow / tools_registry | **268 passed** |
| `2a91aad7` | 07:39 | 系统提示词去重与冗余约束清理 | agent_assembly / cov_middleware_branch_gaps / data_agent_graph_flow / subagent_tool_schema | **111 passed** |
| `fb0abd5e` | 07:13 | 未装配工具名不弹审批 | tool_approval_middleware | **34 passed** |
| `b493d30f` | 07:00 | 子智能体参数扁平化 | subagent_tool_schema / subagents_background / compressing_state / list_format / middleware / sandbox_renewal | **170 passed** |
| `b3d9bc64` | 06:42 | 兜底弹窗遮罩死锁与门禁收敛 | subagents_list_format / subagents_middleware | **76 passed** |

## 前端单测（按 commit 改动组件映射实跑）
| commit | spec | 结果 |
|---|---|---|
| `2658d0f5` | useConversationHistory.spec.ts | 23 passed |
| `6af3d663` | ConversationMessageList.spec.ts | 19 passed |
| `4c87653e` | ConversationMessageList / SubagentTrail / generationSpeed | 19+12+22 passed |
| `b493d30f` | SubagentTaskProcessView / ConversationWorkspacePanel / toolDisplay | 5+20+24 passed |
| `b3d9bc64` | overlayClickGuard.spec.ts | 5 passed |
| type-check | `vue-tsc --build --force` | exit 0 |

前端 spec 合计 **130 passed**（8 个 spec）。`ConversationWorkspacePanel.spec.ts` 输出里的 axios Network Error 是测试 mock 的 `console.error` 日志（用例本身 20 passed），非失败。

## 纯文本/文档 commit（无直接单测，验证可加载 + 端到端）
| commit | 改动 | 验证方式 | 结果 |
|---|---|---|---|
| `dafbe660` | officecli SKILL.md 说明 | YAML frontmatter 解析（name=officecli, description 有效）+ 早间 AG-17 技能链路端到端 | PASS |
| `ee068cb7` | filesystem.py 工具描述精简 | 模块导入 OK + 工具描述在工具卡显示（早间 CT 系列） | PASS |
| `6165ec7b` | 优化提示词 chat_data.py | prompt 模块导入 OK（12 个常量）+ 真机对话验证（本轮新会话「你好」正常） | PASS |
| `8b5590fc` | 优化提示词 chat_data.py | 同上（同模块，HEAD 合并态） | PASS |
| `b8cb6734` | 子智能体提示词 subagents.py | subagents 模块导入 OK + 早间六动作 6/6 端到端 | PASS |
| `7c744002` | 纯验证记录 md | 无代码，文档提交，无需测试 | N/A |

纯文本模块验证口径：加载 `.env-dev` 后实际 `import`（`chat_data` prompt 12 常量、`subagents`/`filesystem` 导入 OK）、`SKILL.md` frontmatter YAML 解析有效——证明改动未引入语法/格式错误，服务可加载。行为层由真机对话（本轮新会话）与早间端到端记录覆盖。

## 汇总
- 16 个 commit 全部覆盖：10 个有后端单测（全 PASS，**1071 passed** 累计按组计）、5 个有前端 spec（130 passed + type-check 0）、6 个纯文本/文档类（可加载验证 + 端到端记录覆盖，1 个纯 md 无需测试）。
- 浏览器真机回归另见 `2026-11-21-all-changes-regression.md`（模型注册表写操作刷新、主对话步数、错误持久化历史回放）。
- 诚实边界：纯提示词改动的「效果质量」无法用单测证伪，本轮验证的是模块可加载 + 服务路径不报错 + 早间行为端到端记录；commit 级无法做到像逻辑改动那样的断言覆盖。

## 收尾
- 临时脚本 `/tmp/run_commit_tests.py` 用后清理；DB/配置无改动；未改产品代码；浏览器已关；服务 5001/8089 正常。
