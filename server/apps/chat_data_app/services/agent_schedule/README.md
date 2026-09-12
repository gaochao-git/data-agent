# 智能体定时任务（Agent Schedule）

用户在会话内创建的智能体定时任务：到点自动以任务归属用户的身份，在绑定会话里执行一次智能问数（复用 Agent Run 全链路），结果留在会话历史中。不承载系统级巡检/清理任务。

## 模块结构

| 文件 | 职责 |
| --- | --- |
| `cron_parser.py` | 五字段 cron 表达式（分 时 日 月 周）解析与下一次触发时间计算；无第三方依赖 |
| `trigger_time.py` | 任务定义字段校验与 `next_trigger_time` 换算的纯函数入口，管理 service 与调度 runtime 共用 |
| `schedule_service.py` | 任务 CRUD / 执行历史 / 后台全局管理（routers -> services -> daos 分层的 service 层） |
| `../../daos/agent_schedule_dao.py` | 任务、触发实例与全局控制表的事务内读写 |
| `../../agent/run/agent_schedule_runtime.py` | 常驻调度循环：终态收口 ->（未暂停时）计划推进 + 抢占派发 |
| `../../routers/agent_schedules.py` | 用户侧接口：`/api/chatdata/agent-schedules` |
| `../../routers/agent_schedule_admin.py` | 后台管理接口：`/api/chatdata/admin/agent-schedules*` |

前端入口（`web-vue/src/views/chat-data/`）：

- 侧边栏「定时任务」入口（`ConversationSidebar.vue`）-> 用户侧管理弹窗（`AgentScheduleManager.vue`）：新建/编辑/启停/删除/执行历史。
- 后台管理弹窗「定时任务」tab（`ChatDataAdminManager.vue` -> `AgentScheduleAdminPanel.vue`）：全局概览统计、一键暂停/恢复、全量任务列表与单任务启停。

## 接口

用户侧（归属校验：只能看到/操作自己的任务）：

- `GET /api/chatdata/agent-schedules`：当前用户的任务列表
- `POST /api/chatdata/agent-schedules`：创建（title/prompt/conversation_id/schedule_type/cron_expr|plan_time/effective_end_time/datasource_id/options）
- `PUT /api/chatdata/agent-schedules/{job_id}`：更新；调度字段变更后按服务端时钟重算 `next_trigger_time`
- `DELETE /api/chatdata/agent-schedules/{job_id}`：软删除并停止调度
- `POST /api/chatdata/agent-schedules/{job_id}/trigger-now`：立即执行（仅归属用户；创建 `manual` 触发实例并同步派发，会话正忙转后台队列自动重试，便于用户验证任务）
- `GET /api/chatdata/agent-schedules/{job_id}/runs`：执行历史（附关联 Agent Run 状态）

后台管理（service token 保护，与数据源管理模式一致；管理员不能替用户执行任务，无立即执行接口）：

- `GET /api/chatdata/admin/agent-schedules/overview`：全局概览（暂停状态、任务总数、执行统计）
- `PUT /api/chatdata/admin/agent-schedules/pause`：全局一键暂停/恢复调度（不影响在途 Run 的状态收口）
- `GET /api/chatdata/admin/agent-schedules`：全量任务分页列表（附执行次数/成功/失败/最近执行）
- `PUT /api/chatdata/admin/agent-schedules/{job_id}/enabled`：强制启停任意任务
- `GET /api/chatdata/admin/agent-schedules/{job_id}/runs`：查看任意任务的执行历史

## 调度设计要点（多点多活）

- **全局单写主库**：所有节点连同一主库，抢占互斥完全依赖 MySQL 行锁（`with_for_update` + 状态迁移），不引入分布式锁/选主组件；主库 failover 由 `utils/database.get_session` 的切换机制承接。
- **唯一键兜底**：触发实例 `uniq_schedule_job_plan (job_id, plan_time)` 保证同一任务同一计划时间点最多一次触发；调度器推进 `next_trigger_time` 游标避免重复计划。
- **misfire**：停机/failover 期间过期的触发点超过 `CHAT_DATA_SCHEDULE_MISFIRE_WINDOW_SECONDS` 窗口直接快进跳过，不补跑；长停机的计划推进有 `_MAX_PLAN_STEPS` 迭代上限，超限跳变到窗口起点的下一个触发点，剩余触发点由后续 tick 补齐，避免事务 + 行锁内空转数十万次。
- **悬挂回收**：claimed 状态超时（默认 300s）释放回 pending 重试，attempt 耗尽转 skipped。
- **停用/删除清退**：任务停用或删除时，其 pending/claimed 触发实例批量置为 cancelled，不产生僵尸。
- **并发闸门**：派发前按用户检查在途（dispatched 未收口）数量，超过 `CHAT_DATA_SCHEDULE_USER_MAX_CONCURRENCY` 释放回 pending 排队；派发批次受 `CHAT_DATA_SCHEDULE_DISPATCH_BATCH_SIZE` 限制；执行侧并发由 agent_run_runtime 的信号量统一约束。
- **可重试错误**：会话已有运行中任务（AgentRunConflictError）释放回 pending 重试，`CHAT_DATA_SCHEDULE_MAX_DISPATCH_ATTEMPTS` 次耗尽置 `skipped`；会话不存在等永久错误直接置 `failed`。
- **终态收口**：dispatched 状态的触发实例随 Agent Run 终态映射（finished→succeeded、failed→failed、cancelled→cancelled）；`waiting_user` 视为未收口，需要用户到会话里回答问题。

## 触发实例状态机

```
pending --抢占(行锁)--> claimed --start_run成功--> dispatched --Run终态--> succeeded/failed/cancelled
   ^                      |                                |
   |                      +--冲突释放回 pending（重试耗尽-> skipped）
   +---------------- 会话不存在等永久错误 --> failed
```

## 配置（server/.env.example）

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `CHAT_DATA_SCHEDULE_ENABLED` | `false` | 调度器总开关（建表后再开启） |
| `CHAT_DATA_SCHEDULE_POLL_SECONDS` | `30` | 调度轮询间隔（秒） |
| `CHAT_DATA_SCHEDULE_DISPATCH_BATCH_SIZE` | `10` | 每进程每轮最大派发数 |
| `CHAT_DATA_SCHEDULE_USER_MAX_CONCURRENCY` | `2` | 单用户在途定时执行上限 |
| `CHAT_DATA_SCHEDULE_MISFIRE_WINDOW_SECONDS` | `3600` | 错过触发快进窗口（秒） |
| `CHAT_DATA_SCHEDULE_MAX_DISPATCH_ATTEMPTS` | `10` | 单触发实例最大派发尝试次数 |
| `CHAT_DATA_SCHEDULE_CLAIM_TIMEOUT_SECONDS` | `300` | claimed 悬挂回收阈值（秒） |
| `CHAT_DATA_SCHEDULE_MAX_JOBS_PER_USER` | `20` | 单用户任务数上限 |

## 上线步骤

1. 在全局唯一写主库执行 `apps/chat_data_app/next_version.md` 数据库变更中的建表 SQL（`chat_data_agent_schedule` / `chat_data_agent_schedule_run` / `chat_data_schedule_control`）。
2. 配置 `CHAT_DATA_SCHEDULE_ENABLED=true` 并滚动重启；每个 gunicorn worker 都会运行调度循环，正确性不依赖 worker 数量。
3. 多机房部署时确保各机房时钟经 NTP 对齐（触发时间按服务器本地时区的 naive datetime 计算，与库内 DateTime 口径一致）。

## 停止调度（全局开关）语义

`chat_data_schedule_control` 固定单行（id=1，缺行时服务端惰性补建）。`paused_flag=1`（后台「停止任务调度」开关）时调度 tick 只执行终态收口与悬挂回收，跳过触发计划与派发：暂停期间不再产生新执行，已派发的 Run 照常结束并回写触发实例状态；任务本身的启停状态不受影响。恢复时把已到期的 cron 任务游标快进到当前时刻的下一个触发点——**停止期间落进 misfire 窗口的触发点不补跑**（否则停止 1 小时的每 5 分钟任务恢复即补 12 次）；单次任务不快进（计划时刻是用户显式指定的）。停止调度只冻结计划触发，不拦截用户主动的「立即执行」（显式用户行为，用于验证任务）。

## 数据库写入约定

时间字段遵循仓库标注用法：写入点用 DB 时钟（`agent_inbox_dao.now`，MySQL 取 `now()`、单测 SQLite 回退应用时钟）显式赋值 `create_time/update_time`。禁止依赖 `server_default` 回读——MySQL 下新 INSERT 行的 server_default 列 flush 后是过期态，同步读取会抛 MissingGreenlet（SQLite 有 RETURNING 掩盖，测试发现不了）；同理，ORM 属性读取必须留在 `get_session()` 事务块内，派发跨事务时先转字段快照（见 `AgentScheduleRuntime._load_job`）。

## 已知边界

- 定时 Run 触发智能体反问（waiting_user）时会挂起等待用户到会话回答；任务指令应尽量要求直接作答。
- 单次任务触发完后靠 `next_trigger_time=NULL` 停止计划（`enabled_flag` 保持 1，保证派发重试不受影响）；编辑执行时间即重新武装。
- 执行历史关联的 Run 状态为派发后快照读，不在派发链路内强同步 Run 结果。
