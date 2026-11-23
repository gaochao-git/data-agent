# 2026-11-23 停机优雅收口 Run — 浏览器点击验证

## 范围
commit `9f44ec16` fix(chat-data): 停机时优雅收口执行中的 Run（已提交，工作区干净）。
新增 `chatdata_uvicorn_worker.py`（SSE 排空 5s）、`shutdown_config.py`（统一预算）、
`AgentRunRuntime.stop()` 有界等待 5s + runtime_shutdown 落 interrupted 补发 done、
finish_run 支持 interrupted 重排 claimed + cancelling 竞争保 cancelled 语义、gunicorn worker_class 切换、main.py drain 参数。

## 环境
- 源码 mtime 最晚 16:38:11 < worker 16:47:12 → 新代码已加载（无需重启）。
- 启动式验证：`from chatdata_uvicorn_worker import ChatDataUvicornWorker` → class 正确、
  `CONFIG_KWARGS.timeout_graceful_shutdown=5`、预算 request=5s/run=5s。
- 模型 `local/Qwen3.6-35B-A3B-4bit`，入口带 `?parentComponentName=执法检查`。
- SSE hook：fetch + tee 抓 event/data；错误钩子 error/unhandledrejection/console.error。

## 核心场景：单 worker SIGTERM（模拟滚动停机）

| 步骤 | 判据 | 结果 |
|---|---|---|
| 发耗时任务（orders 状态/金额分布分析） | SSE 持续增长(522 帧)、0 错误、工作中、多个工具调用(load_skill/data_analyzing_tool/file_read) | ✅ Run 真实在途 |
| lsof 定位 SSE 连接落点 | 连接 58689 → worker 41219 | ✅ 精确定位承载 worker |
| `kill -TERM 41219` 后 10s | 41219 退出、master 补起新 worker(43064/43066, 16:50:26) | ✅ worker 自动补起 |
| 收口终态 | 页面显示"任务因服务重启中断，请重新发起"、Run 结束无"工作中"；工作过程保留已执行工具(加载技能226ms/数据分析238ms/读取文件17ms/重新加载技能3ms) | ✅ interrupted + done 补发 |
| `/health` | 200 `{"status":"ok"}` | ✅ |
| 刷新历史回放 | 问答、工具过程从 DB 恢复；终态 error 文案不随历史显示（既有设计，见下） | ✅ 无回归 |

- 唯一 err `sse: signal is aborted without reason` = worker 被 SIGTERM 导致旧 SSE 连接物理断开（预期），
  前端随后经恢复机制拿到终态并渲染，非产品缺陷。
- 终态文案"任务因服务重启中断，请重新发起"来自 `Conversation.vue:215`（`applyRunDoneState` status=interrupted），
  由本次后端补发的 done payload 驱动；该逻辑属 commit `c559dce2`（本次未改前端）。
- **历史回放不显示 error 文案**：`setActiveAssistantError` 写的是 assistant 消息的运行时 `error` 字段，
  历史回放路径 (`applyTerminal` 未开) 不重放该 UI 状态——既有设计，非本次缺陷。

## 验证结论
**未发现缺陷**。真浏览器 + 真实 SIGTERM 单 worker 跑通本次功能全链路：
在途 Run 落 interrupted、补发 done、worker 自动补起、/health 200、前端无产品报错。
配合定向单测 140 passed（含本次新增 shutdown 4 例 + worker 2 例 + cancelling/claim 竞争）。

## 覆盖边界（诚实说明）
- 未复现"Ctrl+C 整个 master"与"预算小于 gunicorn 宽限期"的实测——后者由 `test_shutdown_budget_fits_default_gunicorn_grace` 单测锁定；
  前者与单 worker SIGTERM 共用同一 stop() 收口链路。
- "停机拒绝新提交"由 `_stopping` 门禁 + `test_runtime_shutdown_before_claim_skips_terminal` 覆盖，浏览器未单独触发。
- 查库受密码密文/ENCRYPT_KEY 不匹配限制未直连确认 DB run 行；interrupted 终态以 done payload 驱动的页面文案 + 历史回放工具过程为证据。
