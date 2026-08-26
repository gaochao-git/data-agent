# Agent Run

本目录负责 Agent 会话运行时、上下文预算、事件记录、工作区、文件、技能快照和 Office 适配。

Agent 工具通过本目录的运行时访问工作区，不直接依赖宿主机路径或沙箱 SDK。

## Run 事件流：MySQL + Redis 可断点恢复持久化设计

本节是该设计的规格契约。修改事件流相关代码前先读这里——哪些行为是设计保证、哪些是可丢弃的，均以此为准。

### 分层模型（按时间尺度切分职责）

| 层 | 粒度 | 介质 | 寿命 | 承载代码 |
|---|---|---|---|---|
| 直播 | chunk/token | Redis Stream（per-run key `chatdata:run:{run_id}:events`） | TTL 1h，可丢弃 | `redis_event_bus.py`（Lua 原子 publish） |
| 快照 | 消息（步骤定稿） | `chat_data_conversation_event` | 永久 | handlers 增量幂等追加（event_key 唯一约束） |
| 锚点 | 终态游标 | inbox 行 `terminal_event` | 永久 | runtime → `record_terminal_event` |
| 账本 | Run 状态/租约/队列/下一轮关联 | `chat_data_agent_inbox` | 永久 | `agent_inbox_service.py` |

核心判断：**持久化粒度是消息级，不是 token 级**。token 级只存在于直播层；历史、审计、恢复等持久化需求全部由消息级快照满足。

### 游标协议

- token 级游标 = Redis `stream_id`（`毫秒时间戳-序号`），per-run 命名空间内全局单调；`"0-0"` 只表示 Redis 订阅从头读取，不作为已落库 checkpoint。
- 历史恢复先读取 `chat_data_conversation_event` 的完整消息级历史，再读取活跃 Run 的 `content.history_checkpoint`。checkpoint 中的 `stream_id` 是最近一次已和历史快照一起提交的 Redis 位置，前端以它作为 `Last-Event-ID` 订阅增量。
- 浏览器不保存 Redis 事件游标。页面刷新、换 worker 或重新打开会话都以服务端返回的历史和 checkpoint 为准；没有有效 checkpoint 时才从 `"0-0"` 订阅，重复事件由前端现有消息幂等逻辑处理。
- SSE 断连时，当前订阅请求可携带服务端 checkpoint 重新建立 `XREAD`；Redis Stream 只负责短期实时/增量传输，不是永久历史源。
- 发布幂等：Lua 脚本内 owner 校验 + event_id 去重原子完成；同 event_id 重发返回原 stream_id，网络重试安全。

### 写入路径与终态顺序契约

```
token     ──► Redis Lua 原子: owner校验 + event_id去重 + XADD + 刷TTL
步骤完成  ──► conversation_event 幂等追加
终态      ──► ① complete_run / finish_run（MySQL 定稿：status、可选 content.next_run_id、
                    下一轮调度、清 MySQL owner）
             ──► ② publish done 到 Redis（Redis owner key 仍有效——独立于 MySQL，
                    由 ④ 才清除，故 Lua 校验必过）
             ──► ③ record_terminal_event 锚点落库
             ──► ④ release_owner 清 Redis key
```

契约要点：

1. ②③ 必须在 ④ 之前：done 进流、锚点落库时发布方必须仍持有 Redis 所有权。
2. ① 先于 ② 是刻意设计：done 对观看者可见时最终历史已提交（read-your-writes）。
3. ①② 之间的窗口由 SSE 读取侧宽限期兜底（`TERMINAL_PERSISTENCE_GRACE_SECONDS = 10s`）：Run 已终态但 Redis 中没有 done 时，宽限期内继续读流，超时后用 MySQL `status + content.next_run_id` 合成同一结构化 done。
4. `done` 数据固定携带 `status=finished|failed|cancelled|waiting_user`，成功且已创建下一轮时附带 `next_run_id`；纯文本 `[DONE]` 只作为旧协议哨兵，不承载业务终态。

### Run 调度与本进程 fast-path

- 创建首个 Run、恢复 `ask_user_question` 的 Run 时，在写入 MySQL 后立即调用当前进程的 `AgentRunRuntime.submit()`，避免等待调度轮询。
- 当前 Run 的 `complete_run()` 在同一事务中创建后续 `pending` Run 后，Runtime 先完成旧 Run 的 done 发布、终态游标记录和 Redis owner 释放，再立即 `submit(next_run_id)`。因此连续的 `next-step` / `next-turn` 会在当前进程直接接续。
- fast-path 只负责降低调度延迟，不承担一致性。提交失败、进程重启或请求落到其他 Worker 时，MySQL 中的 `pending` Run 仍由每 2 秒一次的调度轮询发现并通过 `prepare_pending_run()` 原子领取；数据库状态校验保证不会重复执行同一 Run。

### 断点恢复矩阵

| 断点场景 | 恢复路径 | 精度 |
|---|---|---|
| SSE 断连/页面刷新 | Last-Event-ID → XREAD 续读 | token 级无损 |
| 换实例（多 worker 路由） | stream 在 Redis，任意实例可读 | token 级无损 |
| 哨兵 failover | 命令级重试 + ReadOnlyError 跟随新主 | 无损 |
| 页面刷新/重新打开运行中会话 | 历史事件 + 活跃 Run 的 `history_checkpoint` → Redis 增量 | 消息级历史 + checkpoint 后 token 级增量 |
| Redis 数据清空 | 进行中 run 因 owner key 丢失在下次 publish 时失败标记；已完成步骤在账本不丢 | 消息级 |
| TTL 过期后打开旧会话 | 历史 API 读 conversation_event | 消息级 |
| Worker 崩溃 | MySQL 租约 120s 过期 → requeue/标记失败 → 用户重发 | 消息级 |
| ask-user 恢复 | `latest_event_id`（XREVRANGE）+ terminal_event MySQL 兜底 | 精确 |

### 持久化边界（明确不做）

- 不做 token 级 MySQL 持久化（写放大，无消费方）。
- 不做 Redis/MySQL 双写一致性协议（Lua 幂等 + event_key 唯一约束各自保证重试安全，terminal_event 单锚点足够）。
- 不做 Redis 跨中心复制（三中心定稿：Redis 各中心独立，run 事件是中心内可丢弃运行态）。
- 不做 run 级自动接管续跑（产品决策：断了标记失败、用户重发；账本保证已完成部分不丢）。

### 运维语义

- **Redis 全量清空是可接受的故障**：进行中 run 会失败，但权威数据（状态、历史、终态锚点）全在 MySQL，不迁数据、全新重建——这是三中心设计前提。
- Redis 仅支持哨兵模式接入（`CHAT_DATA_REDIS_SENTINELS` + `CHAT_DATA_REDIS_MASTER_NAME`），failover 自愈依赖 ReadOnlyError 进命令重试列表。
- stream TTL 由 `CHAT_DATA_REDIS_STREAM_TTL_SECONDS` 控制（默认 1h），过期后恢复自动降级为消息级历史回放；已结束 Run 不要求 Redis 继续保留。
