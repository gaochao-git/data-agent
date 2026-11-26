# 2026-11-23 工具 chunk 索引重构 — 浏览器点击验证

## 范围
工作区另一智能体的并行未提交改动（不在 commit 内）：新增 `tool_call_chunks.py::ToolCallChunkIndex`，
主图 `agent_stream_events._ToolStreamState` / 子图 `subagent_stream._SubagentStreamState`
把各自重复的 chunk id/name/index 索引字段收口到公共类，旧字段留 property 兼容。
本轮真浏览器验证改动后主图/子图工具流是否回归。

## 环境
- 模型 `local/Qwen3.6-35B-A3B-4bit`（会话页输入栏默认值，未切换）
- 入口 `http://localhost:8089/chatdata?parentComponentName=执法检查`（必带，否则无数据源）
- 后端源码 mtime 15:30:31~39 > 原 worker 15:27:59 → `kill -HUP` gunicorn master(7137) 热重载，
  worker 全换成 15:44 新进程后开测；后端 401(缺 header)=健康、带 header 数据源接口 200。
- SSE hook：`fetch` + `ReadableStream.tee()` 抓 event/data 行；错误钩子 `error`/`unhandledrejection`/`console.error`。

## 场景与结果（全部 0 页面错误）

| # | 场景 | 触发 | 关键判据 | 结果 |
|---|---|---|---|---|
| 1 | 主图工具调用（写入文件） | "用python算1到100的和，写入 outputs/sum_result.txt" | 工作过程"思考2·工具1"；工具卡"写入文件/+1/277ms"；详情参数 operation=write、file_path=/mnt/user-data/outputs/sum_result.txt、mode=overwrite、bytes=4；行数"1行 5050"；结果 5050 | ✅ |
| 2 | 子智能体派发（子图工具流） | "派发子智能体算2的10次方" | 主卡"派发子智能体/计算 2 的 10 次方是多少/14.8s"；点开工作台轨迹"general/7/聊天/工具"；子回复含 2¹~2¹⁰=1024 完整计算；结果 1024 | ✅ |
| 3 | 历史会话回放 | 刷新后点侧栏"请子智能体计算二的十次方" | 问答完整、"思考2·工具1"保留；展开工作过程"派发子智能体/14.8s"从历史数据正确重建 | ✅ |

- 主图 chunk 索引：工具名解析(`file_write`→"写入文件")、参数增量最终值、行数统计(+1)均正常，公共类 `resolve_id/resolve_name` 未破坏主图链路。
- 子图 chunk 索引：子智能体 task 工具名/描述解析正常，轨迹面板与子回复完整，未破坏子图链路。
- 完整消息收口：历史回放保留了工具卡与统计字段，property 兼容层未丢数据。

## 结论
**未发现缺陷**。真浏览器三场景（主图工具 / 子图工具 / 历史回放）全过、0 页面错误，
与静态等价性核对 + 定向单测(新增5例 + 回归95 passed)结论一致。
本轮未改产品代码、未提交。

## 覆盖边界（诚实说明）
- 定向回归，非发布裁决：基线 144 条中未逐条重跑，仅覆盖工具 chunk 索引相关路径。
- Qwen3.6 派发子智能体需 2+ 分钟推理，参数构造阶段流式 chunk 高频（evt 2000+ 帧）但仍稳定；
  未构造「首片只有 index、name 迟到」等极端 chunk 形状（已由 `test_tool_call_chunks.py` 单测锁定）。
