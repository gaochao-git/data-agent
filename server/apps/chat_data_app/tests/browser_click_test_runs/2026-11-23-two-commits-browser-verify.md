# 最近 2 个 commit 浏览器点击验证（2026-11-23 10:53–11:05）

## 最近 2 个 commit

| commit | 内容 | 性质 |
| --- | --- | --- |
| `41c8aa07` (HEAD, 10:45:34) | LangChain 1.5.0→1.5.2 / core 1.8.0→1.8.4 / langgraph 1.2.22→1.2.24 + next_version | 依赖补丁升级，无 UI 改动 |
| `d283fdba` (10:45:27) | 修正工具预览与图表统计口径，补齐取消态展示 | 功能改动，本轮验证重点 |

**d283fdba 四个功能点**：
1. `tool_args_preview.py`：参数预览恰好 8000 字符时不再误报截断，仅在确有内容被丢弃时补发 truncated 信号（新增 `test_args_preview_buffer_exact_cap_is_not_reported_as_truncated` / `..._flushes_pending_exact_cap_with_truncation`）
2. `data2image.py` + `chart_render.py`：`skipped_rows` → `skipped_points`，多列共享同一行时按"无效点"累计（新增 `test_data2image_counts_invalid_points_across_series`，断言同一行两个 Y 列都无效 → `skipped_points==2`）
3. `ToolCallCard.vue`：cancelled 状态显示"已停止"灰标 + 灰色样式，compact 模式同步（新增 `marks a stopped tool in compact chat rows`）
4. `types/index.ts`：`ToolStatus` 收敛为共享类型并纳入 `cancelled`，与子智能体 trail 同口径

## 环境准备
- 发现后端 gunicorn 09:41 启动、早于源码 mtime（10:31-10:33），未加载最新代码 → **重启后端**（`CHAT_DATA_ENV=dev`，本机 IP 172.20.10.3 不在 deploy_env_hosts 的 dev 列表 .1/.2 中，需显式指定环境；这是本机 IP 变更，非代码问题）。新 workers 10:56:25 启动，加载最新代码。依赖版本实测已是 1.5.2/1.8.4/1.2.24。
- 前端 vite HMR 自动最新。URL 带 `?parentComponentName=执法检查`（否则数据源为空无法发消息）。
- 模型 `local/Qwen3.6-35B-A3B-4bit`。抓帧法 fetch+tee（本轮 reload 后 hook 被页面覆盖，部分帧未抓全，但单测补足）。

## 自动化验证（全绿）
- 后端定向（`test_tool_args_preview` + `test_data2image_tool`）：**75 passed**（含 8000 边界 2 例、skipped_points 2 例）
- 前端定向（`ToolCallCard.spec.ts`）：**30 passed**（含"已停止"compact 用例）

## 浏览器验证结果（诚实 matrix）

| 功能点 | 浏览器实测 | 单测 | 结论 |
| --- | --- | --- | --- |
| 8000 截断边界 | Qwen 参数远小于 8000，无法在浏览器触发超长参数预览 | 75 passed 含 2 个精确边界用例（恰好填满不报截断、窗口内尾片+截断） | ✅ 单测充分覆盖，浏览器不可达 |
| skipped_points 字段口径 | 发画图任务，模型最终回复"跳过了 2 个点（2024-03 和 2024-09），有效数据点 10 个"，与按点计数口径一致；但 data2image 工具结果 JSON 因传自建 csv 被安全策略正确拒绝（只接受系统结果文件），未直接显示 `skipped_points` 字段名 | `test_data2image_counts_invalid_points_across_series` 断言同 2 行×2 列无效 → `skipped_points==2` | ✅ 单测直接验证字段；浏览器侧安全拒绝行为正确（非缺陷） |
| "已停止"标识（compact 灰标） | 多次尝试取消：①工具 start 后取消撞任务自然完成；②参数生成阶段取消→整轮清除无工具卡；③模型思考阶段取消→无真正工具卡。**未稳定复现 cancelled 工具卡**（取消时机竞态 + Qwen 工具执行快） | `marks a stopped tool in compact chat rows` 断言含"已停止"、无"错误"、无 spinner | ✅ 单测覆盖渲染逻辑；浏览器因取消竞态未复现（非缺陷，测试方法学限制） |
| ToolStatus 收敛 | 类型层改动，无 UI 变化；全流程 type-check 通过 | 前端 type-check exit 0 | ✅ |
| 回归 | 发 file_write / data2image / 子智能体派发 / 取消等多轮，`window.__errs`=0，服务 5001/8089 健康 | — | ✅ 无回归 |

## 结论
最近 2 个 commit 未发现缺陷。`d283fdba` 四个功能点中，可由单测精确验证的三个（8000 边界、skipped_points、"已停止"渲染）单测全绿；浏览器实测受限于：①Qwen 参数长度达不到 8000；②data2image 安全策略拒绝自建数据导致无法直接看到成功回执的 `skipped_points` 字段（模型回复的"跳过2个点"与口径一致，间接印证）；③取消操作撞任务快速完成/参数阶段清除的竞态，无法稳定产出 cancelled 工具卡。这些都是**验证手段的可达性问题，非产品缺陷**。全流程 0 页面错误，依赖升级后无回归。

## 收尾
未改产品代码；仅重启后端加载最新代码（必要）+ 新增本记录。浏览器保留最终页面。服务 5001/8089 正常。
