# ChatData App - 智能问数功能模块

## 概述

ChatData App 是一个基于 LangGraph 实现的智能问数服务，当前主链路为语义层 NL2DSL2SQL：通过语义层将自然语言转为 DSL，再转为 SQL。

此外，模块还提供两类配套能力：
1. **SQL 编辑器 AI 辅助**：根据自然语言快速生成 SQL 草稿
2. **Builder / Dashboard 支撑接口**：服务可视化构建和数据看板

## 功能特点

- **语义问数**：基于指标、维度、关系配置完成标准化查询
- **SQL 自动生成**：根据语义配置和用户意图生成查询 SQL
- **语义层支持**：通过配置指标/维度实现标准化查询
- **多数据库支持**：支持 MySQL 和 Hive 语法
- **LangChain Agent 集成**：使用 `create_agent` 组织模型与工具循环
- **RESTful API**：提供标准的HTTP接口供前端调用

---

## 权限边界

ChatData 在 `data-agent` 中按 service 层定位，不直接承担后台菜单、页面入口和角色授权。`/api/chatdata/admin/**` 是否允许访问由上层 controller/BFF 基于登录态和权限点统一拦截。

本模块仍负责服务层身份和业务数据权限：

- 所有 `/api/chatdata/**` 接口由 `routes.py` 的根路由依赖统一校验 `X-Service-Token`，不依赖业务路由逐个记忆 service token 校验。
- 需要用户归属、审计或下游透传的接口，再调用 `_get_current_user(request)` 解析 `X-User-Id`、`X-Username`；所有 `/api/chatdata/admin/**` 接口必须解析当前用户。
- 用户侧数据按 `user_id`、owner、public/global 状态隔离。
- 数据源执行对象权限在 service 层校验；HTTP 执行对象通过权限接口获取当前用户可用机构编码，direct 执行对象使用服务端固定允许编码。
- 上层 controller 负责 admin 角色权限，本模块不重复实现菜单/角色授权，避免两套权限口径漂移。

---

## 核心查询链路

三条 SQL 来源链路（SQL 查询、可视化查询、数据智能体）在服务端汇入同一执行内核，安全校验、执行记录、缓存与历史能力全链路共用：

```text
SQL 查询（人工写 SQL）──────────────┐
                                    │
可视化查询（前端配置）─ 编译 DSL→SQL ─┤
                                    ├──> prepare 校验（单语句 + 类型白名单 + 危险 SQL 告警）
数据智能体（模型写 SQL 或 DSL）──────┘         │
  ├─ 模型直接写 SQL：原文进校验                ↓
  └─ 模型写 DSL：compile_semantic_ai_dsl   create_query_task + 预创建执行记录（查询历史可见）
       编译成 SQL 后同样进校验                ↓
                                          execute_sql_for_member（并发执行各成员数据源）
                                            ├─ 结果缓存（含 HTTP 远端引用缓存与失效重查降级）
                                            ├─ executor 边界二次校验 + 自动追加 LIMIT + 超时控制
                                            └─ 取消检查（语句间 + 执行前双重检查）
                                               ↓
                                          任务状态聚合 + 结果落库 + 查询历史
```

要点：

- 差异只在 SQL 来源；来源之后所有链路走同一套 `prepare_sql_request_for_execution()` → `execute_sql_for_member()` 管道，不存在绕过安全边界的旁路。
- 可视化查询与数据智能体的 DSL 编译分别在服务端完成（`compile_semantic_builder_sql()` / `compile_semantic_ai_dsl()`），编译产物 SQL 与人工 SQL 适用完全相同的校验标准。
- 各链路的详细调用链见下方分节。

## 会话历史与上下文压缩

智能问数与智能分析将模型运行态和用户可见历史分开保存：

- LangGraph checkpoint 的 `messages` 只保存当前模型需要的最近原始消息；`context_summary` 保存累计上下文摘要。
- `chat_data_conversation_event` 追加用户消息、思考分段、助手消息、工具结果和“上下文已压缩”分界事件，是会话历史回放的权威来源；压缩不改写已有事件，分界事件不保存内部摘要正文。
- 历史接口只读取事件表，不反序列化 checkpoint；没有事件记录的旧会话不再回退 checkpoint。

单次主模型请求同时遵守输出预留与质量阈值：硬输入容量为 `模型上下文窗口 - CHAT_DATA_OUTPUT_MAX_TOKENS`，安全水位为硬输入容量的 `95%`；压缩在 `min(模型窗口 * CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO, 安全水位)` 时执行。压缩触发比例默认 `0.70`，必须大于 `0` 且不超过 `0.95`，非法配置会阻止服务启动。当前输入一旦达到或超过触发点，之后每次模型调用前都会继续尝试压缩。更小的值可用于更早压缩；压缩后优先保留当前用户问题、最近完整对话轮次和最近工具调用/结果配对，旧回合以及当前回合较早的完整工具链可以进入摘要，绝不拆开工具调用与工具结果。摘要生成后即替换运行态消息并显示“上下文已压缩”；`estimated_cleared_tokens` 仅记录本次预算释放量，可能为 `0`。摘要调用单独预留 `CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS`（默认 `2000`）输出 Token；若待摘要内容仍超过摘要输入预算，先按体积从大到小确定性压缩旧工具结果，仍不足才按完整轮次省略最早历史。主模型与摘要模型最终都控制在安全水位内，为 Token 估算误差和 Provider 消息封装留出余量；正常压缩仍超出该水位时依次移除最旧轮次、旧摘要和当前回合完整工具组，必要时只保留受限的当前用户消息继续执行。服务启动时校验压缩比例、模型窗口和输出预留；动态系统提示词与工具 schema 在 Agent 创建时校验，只有固定输入自身超过安全水位才拒绝创建 Agent。前端圆圈只表示 `当前输入 / 模型完整上下文窗口`，不把输出预留混入占比。

部署前按 `next_version.md` 执行待发布的数据库和依赖变更。应用启动不会自动执行业务表迁移。

### 统一入口

前端 ChatData 主页面入口是 `web-vue/src/views/chat-data/pages/index.vue`，按 `viewMode` 分成四个用户入口：

| `viewMode` | 页面能力 | 前端主组件 |
|---|---|---|
| `conversation` | 数据智能体 | `AnalysisConversation.vue` |
| `builder` | 可视化查询 | `QueryBuilder/index.vue` |
| `editor` | SQL 查询 | `SqlEditor.vue` |
| `dashboard` | 看板查询 | `Dashboard/DashboardEditor.vue` |

前端接口统一从 `web-vue/src/views/chat-data/api/` 发起。代码中 URL 写为 `/agents/api/chatdata/...`；开发环境通常由 `VITE_APP_BASE_API=/dev-api` 拼成 `/dev-api/agents/api/chatdata/...`，再经 `web-vue/vite.config.ts` 代理转发到后端。后端实际挂载入口是 `server/main.py -> apps.chat_data_app.router`，路由总前缀是 `/api/chatdata`，聚合文件是 `server/apps/chat_data_app/routes.py`。

### 1. 数据智能体

数据智能体是唯一的 `chat_data` Agent，共用会话 SSE 入口，不直接走 SQL 查询页面的提交按钮。运行时始终提供会话工作区和文件工具；`workspace_backend`（运行环境）与 `bash_enabled`（安全策略）是两个独立维度：前者只决定工作区跑在受限本地目录还是 OpenSandbox，后者决定 `bash_run` 是否注册。两者职责不同且必须耦合选型，详见下文「运行环境后端与 Bash 安全模型」。

ChatData 文件型数据统一位于 `CHAT_DATA_HOME`（**必填**，应用唯一数据根，禁止回退到其它变量推导）：会话路径为 `threads/{user_id}/{创建日期YYYY-MM-DD}/{conversation_id}/user-data/`（按会话创建日期归档，路径统一由 `utils.storage_paths.conversation_thread_dir` 拼装），其中固定创建 `workspace/`、`outputs/`、`uploads/`，查询结果统一保存到 `outputs/query_result/<id>.json`，普通工具大结果保存到 `outputs/.tool-offload/<id>.json|txt`。查询工具只返回简短元数据和受会话约束的 `result_id`、`result_path=/mnt/user-data/outputs/query_result/<id>.json`；前端按 `result_id` 回放完整结果，Agent 后续分析按 `result_path` 使用 `file_read` 或 Bash 读取。两个系统目录均不出现在工作区文件列表，`file_edit`、`file_copy`、`file_move`、`file_delete`、`present_artifacts` 与技能导入均拒绝它们，因此不作为用户产物。前端请求时携带会话 ID，服务端完成用户归属校验后按固定路径读取。系统技能位于 `CHAT_DATA_HOME/skills/system/`，用户技能位于 `CHAT_DATA_HOME/skills/user/{user_id}/<skill-name>/versions/vN/`；管理域不直接暴露给模型，每个会话在创建/首次运行时构建技能快照 `threads/{user_id}/{创建日期YYYY-MM-DD}/{conversation_id}/runtime-skills/`（系统技能 + 自己已发布 + 他人 global 已发布，会话内冻结不变），通过 Files API 上传到 sandbox 的扁平 `/mnt/skills/<技能名>/`；sandbox 也通过 Files API 上传 `/mnt/user-data`，操作完成后下载 `workspace/`、`outputs/` 变更回应用侧，不使用宿主机目录挂载。系统技能由发布流程从代码包复制到 `skills/system/`，运行时只读且不依赖技能数据库。

应用多实例的持久化存储和并发协调由部署层负责，代码不实现跨节点文件同步。生产 checkpoint 必须使用 MySQL，不使用共享目录上的 SQLite。

### 运行环境后端与 Bash 安全模型

运行环境后端（`workspace_backend`）与 Bash 开关（`bash_enabled`）职责不同，选型必须耦合，不能分开理解：

- **`workspace_backend` 是「运行环境标准化」，不是安全设备。** 选 `sandbox`（OpenSandbox）的目的只是把代码执行场所钉死为统一的 Python 版本、预装依赖、可复现环境，且不让 `pip install` / 临时进程 / 环境变量污染宿主生产服务。它的容器隔离（FS/网络/进程边界）只是顺带能力，本职是给代码一个一致的运行场所。
- **`bash_enabled` 是真正的「安全策略控制」。** 它决定 Bash 工具是否注册；注册后对命令做黑名单（`nohup` / `setsid` / `su` / `sudo` / ...）+ 路径词法校验（`agent/tools/bash_run.py` 的 `_validate_command` / `_validate_command_paths`）。安全意图全部表达在这一层。

**但 Bash 工具的校验是「策略轨道」而非「执行之墙」：** agent 可先用 `file_edit` 把任意脚本写到 `workspace/`，再 `bash evil.sh` 执行——脚本内容完全不被检查；local 模式下子进程以宿主权限运行，校验是唯一闸门且可被引号/进程替换绕过。因此 Bash 工具的开关是 policy（是否允许代码执行），**enforcement（真正把越界代码关住）要由沙箱容器兜底**。

两个维度正交，组合成 2×2 矩阵，由部署者自行选择（代码不强制）：

| 运行模式（`bash_enabled`） | 隔离模式（`workspace_backend`） | 含义与风险 |
|---|---|---|
| 受控（`false`） | 本地（`local`） | 无代码执行；文件工具经 `resolve_workspace_path` 严格限制在 `workspace` / `outputs` / `uploads` + 受权限管控的只读技能目录，100% 受控。**最轻量、最安全，推荐默认**。此时 sandbox 只增加 VM 开销与 I/O 延迟，无安全收益 |
| 受控（`false`） | 沙箱（`sandbox`） | 无代码执行，但工具仍走容器 RPC；标准化运行环境在此无代码可作用，仅增开销，通常无意义 |
| 灵活（`true`） | 沙箱（`sandbox`，推荐） | agent 可执行任意代码；sandbox 承担「标准化运行环境」与「Bash 校验被绕过时的隔离兜底」，宿主不直接暴露。**灵活模式推荐 sandbox** |
| 灵活（`true`） | 本地（`local`） | agent 可执行任意代码，子进程以宿主权限运行、无容器隔离；Bash 校验被绕过即宿主直接暴露。**仅在可信 / 纯本地开发场景使用**，勿用于多租户或暴露环境 |

- **文件工具（`file_read` / `file_edit` / `file_write` / `file_move` / `file_copy` / `file_delete` / `file_glob` / `file_grep` / `dir_create` / `dir_list` / `dir_delete` / `present_artifacts`）始终可用且始终严格路径管控**。工具只调用 `ConversationWorkspaceRuntime`；运行时将读写、目录遍历和产物解析委托给 `WorkspaceBackend`。会话开始前构建可见技能快照，模型通过 `file_read` 只读访问 `/mnt/skills/<name>/SKILL.md` 及其在技能入口中声明的资源。`local` 直接访问受控会话目录，`sandbox` 经 OpenSandbox Files API 访问一次性沙箱副本，不允许工具层按 backend 分支或直接读宿主路径。只有 Bash 工具会在受控模式（`bash_enabled=false`）下不注册。
- `CHAT_DATA_CAPABILITY_USER_OVERRIDABLE=false` 时前端不展示运行模式切换 UI，完全依赖系统默认配置。

```text
index.vue
  -> AnalysisConversation.vue
  -> composables/useSqlGenerator.ts
  -> api/index.ts: streamCompletion()
  -> POST /agents/api/chatdata/conversations/{conversation_id}/completion
  -> routers/conversations.py: completion()
  -> agent_stream.py: sql_completion_handler()
  -> agent/data_agent.py
  -> data_analyzing_tool（execute_query）
  -> services/semantic/run/semantic_query_service.py
  -> services/sql_run/runtime.py: execute_agent_sql()
  -> execute_sql_for_member()
  -> services/sql_run/executor.py
```

查询支持 DSL 模式和 SQL 模式，统一由合并工具 `data_analyzing_tool` 承载（`dsl2sql_tool` / `semantic_query_tool` / `semantic_yaml_check_tool` 已于 2026-09-18 合并，按 `action` 分派）。DSL 模式为默认主链路：`NL -> DSL -> SQL -> execute_query`；只有 DSL 模式无法表达查询结构时，智能体才允许使用 SQL 模式，通过 `execute_query` 的 `action_payload.sql` 直接执行只读 SQL。两种模式通过 `action_payload` 的 `dsl` / `sql` 参数二选一表达，不传 `mode`；分析、技能和产物处理都在同一 Agent 的会话工作区内完成。

执行模式：

- 前端只发起 `streamCompletion()` SSE，不拿 `task_id` 轮询。
- `data_analyzing_tool` 的 `execute_query` 内部调用 `query_runtime.execute_agent_sql()`，会创建 `query_task` 和 `query_execution` 记录用于历史、审计和结果落库。
- SQL 执行在当前工具调用内等待完成，结果通过结构化 JSON 工具结果回到智能体和 SSE 流。
- 如果任务记录或执行明细创建失败，当前 SQL 工具调用直接失败，不再绕过任务审计降级查询；智能问数的用户交互主口径仍是“对话流内同步等待工具结果”。

`data_analyzing_tool` 的 `execute_query` 查询结果返回协议固定为 JSON 字符串，不再使用 XML 标签：

```json
{
  "type": "query_result",
  "version": 1,
  "status": "succeeded",
  "data": {
    "sql": "SELECT ...",
    "results": []
  },
  "error": null,
  "meta": {
    "query_mode": "dsl",
    "result_id": null
  }
}
```

前端先根据 SSE `tool` 事件的 `tool_name=data_analyzing_tool` 判断工具来源，再解析 `content.type=query_result` 判断内容类型。后续扩展图表、大结果卸载、分页和审计字段时，只扩展 `data` 或 `meta`，不要再增加字符串标签协议。

### 2. 可视化查询

可视化查询的预览和执行入口分离：预览由后端编译 DSL 返回 SQL；执行只提交 DSL，由查询任务服务端编译后进入统一异步任务。

```text
点击“预览 SQL”
  -> QueryBuilder/index.vue
  -> compileCurrentBuilderSql()
  -> api/query.ts: compileSemanticBuilderSql()
  -> POST /agents/api/chatdata/compile-semantic-builder-sql
  -> routers/query_execution.py: compile_semantic_builder_sql_endpoint()
  -> services/semantic/run/semantic_query_service.py

QueryBuilder/index.vue
  -> useQueryTask.submitSqlTask(query_type='builder')
  -> api/query.ts: createQueryTask()
  -> POST /agents/api/chatdata/query-tasks
  -> routers/query_execution.py: create_query_task()
  -> services/sql_run/task_service.py: create_query_task_for_user()
  -> services/sql_run/runtime.py: prepare_sql_batch_request_for_execution()
  -> prepare_sql_request_for_execution()          # 服务端编译 DSL 为实际 SQL
  -> create_query_task()
  -> QueryTaskService.schedule_query_task()
  -> query_runtime.run_sql_query_task()
  -> build_execute_sql_result_data()
  -> execute_sql_for_member()
```

前端通过 `getQueryTask()` 轮询 `/agents/api/chatdata/query-tasks/{task_id}`，结果只消费任务返回的 `results[]`。取消执行走 `cancelQueryTask()`，后端入口是 `/query-tasks/{task_id}/cancel`。

执行模式：

- `compileSemanticBuilderSql()` 只做 DSL 编译预览，不执行 SQL，不写查询任务。
- 点击执行只传 `semantic_builder_config`、`semantic_config_id` 和执行对象；不调用预览接口，也不传 SQL。
- `createQueryTask()` 在服务端按认证用户和指标集编译 Builder DSL，再创建 `task_type=sql`、`query_type=builder` 的异步任务。
- 后端 `create_query_task_for_user()` 创建任务后立即 `schedule_query_task()` 后台执行。
- 前端 `useQueryTask.waitForTask()` 轮询 `GET /query-tasks/{task_id}`，只在任务进入 `succeeded`、`partial_success`、`failed`、`cancelled` 等终态后更新页面结果。
- 可视化查询和 SQL 查询共用 `query_runtime.run_sql_query_task()`、`build_execute_sql_result_data()`、`execute_sql_for_member()`，区别在请求中额外带 `semantic_builder_config`、`semantic_config_id` 和 `query_type=builder`。

### 3. SQL 查询

SQL 查询页面不再使用同步 `/execute-sql` 旧入口，主链路只有异步任务。

```text
index.vue
  -> SqlEditor.vue
  -> handleExecute()
  -> parseSQLStatements()
  -> useQueryTask.submitSqlTask(query_type='sql')
  -> api/query.ts: createQueryTask()
  -> POST /agents/api/chatdata/query-tasks
  -> routers/query_execution.py: create_query_task()
  -> services/sql_run/task_service.py: create_query_task_for_user()
  -> services/sql_run/runtime.py: prepare_sql_batch_request_for_execution()
  -> create_query_task()
  -> QueryTaskService.schedule_query_task()
  -> query_runtime.run_sql_query_task() / run_sql_batch_query_task()
  -> create_query_executions_for_task()
  -> build_execute_sql_result_data()
  -> execute_sql_for_member()
  -> services/sql_run/executor.py
```

SQL 页面会把选中 SQL 或编辑器 SQL 按分号拆成 `sql: string[]` 一次提交。后端按数组长度派生内部任务类型：单条 SQL 为 `task_type=sql`，多条 SQL 为 `task_type=sql_batch`。`prepare_sql_batch_request_for_execution()` 在任务创建时逐条做快速 SQL 安全校验和 limit 改写预检；真正执行前仍在 `execute_sql_for_member()` / executor 边界按成员数据源再生成 `effective_sql` 并校验，保证 MySQL、Hive direct、Hive HTTP 等不同执行器不会绕过安全边界。

执行模式：

- 前端只创建一个异步任务，不传 `task_type`；后端根据 `sql` 数组长度派生 `sql` 或 `sql_batch`。
- `sql_batch` 内部按 SQL 语句顺序执行；同一条 SQL 选择的多个执行对象仍并发执行。
- 前端轮询 `GET /query-tasks/{task_id}` 获取任务状态；单条 SQL 返回 `results[]`，多条 SQL 返回 `statement_results[]`，每个语句节点内继续使用统一 `results[]` 成员结果。
- 任务取消走 `POST /query-tasks/{task_id}/cancel`；HTTP Hive 通过远端 `remote_task_id` 停止，MySQL direct 通过连接 ID `KILL QUERY`，Hive direct 当前没有真实 kill 能力。
- SQL 查询页面不消费智能体 `data_analyzing_tool` 工具结果，也不直接调用 `execute_agent_sql()`。

### 4. 看板查询

看板配置和看板查询分开：配置类接口在 `routers/dashboard.py` / `services/dashboard/service.py`，面板刷新复用查询运行时。

```text
DashboardEditor.vue
  -> composables/useDashboard.ts
  -> api/dashboard.ts: getDashboard() / updateDashboard() / addPanel() / updatePanel()
  -> /agents/api/chatdata/dashboards...
  -> routers/dashboard.py
  -> services/dashboard/service.py
```

看板面板执行有两种前端调用：

```text
单面板刷新:
DashboardEditor.vue
  -> useDashboard.loadPanelData()
  -> api/dashboard.ts: executePanel()
  -> POST /agents/api/chatdata/dashboards/{dashboard_id}/query-tasks
  -> 轮询 GET /agents/api/chatdata/query-tasks/{task_id}
  -> routers/dashboard.py: create_dashboard_query_task()
  -> QueryTaskService.schedule_query_task()
  -> dashboard_runtime.run_dashboard_batch_task()
  -> execute_dashboard_panel_task_payload()
  -> build_dashboard_panel_execution_result()
  -> execute_dashboard_panel_sql_with_target()
  -> execute_sql_for_member()

批量加载:
DashboardEditor.vue
  -> useDashboard.loadDashboardPanelsInBackground()
  -> api/dashboard.ts: executePanelsBatch()
  -> POST /agents/api/chatdata/dashboards/{dashboard_id}/panels/execute-batch
  -> routers/dashboard.py: execute_panels_batch()
  -> dashboard_runtime.stream_dashboard_batch_execution()
  -> execute_dashboard_panel_task_payload()
  -> execute_sql_for_member()
```

`services/sql_run/providers.py` 初始化 `QueryTaskService` 后会调用 `query_task_service.configure_dashboard_runtime()`，把 `execute_sql_for_member`、任务创建、任务更新和调度函数注入 `dashboard_runtime.py`，避免看板 runtime 反向导入查询 router。

执行模式：

- 看板配置 CRUD 不执行 SQL，只维护 `dashboard` 和 `panel` 配置。
- 单面板刷新当前前端走 `executePanel()`：先 `POST /dashboards/{dashboard_id}/query-tasks` 创建 `task_type=dashboard_batch` 任务，再轮询通用 `GET /query-tasks/{task_id}`，最后从 `task.result.results[panel_id].payload` 取面板数据。
- 批量加载当前前端走 `executePanelsBatch()`：调用 `POST /dashboards/{dashboard_id}/panels/execute-batch`，后端立即创建 `task_type=dashboard_batch` 记录并返回 SSE；每个面板完成后推送一个 `PanelData`。
- 批量 SSE 失败时，前端 `useDashboard.loadAllPanelData()` 会回退到多个单面板 `executePanel()`。
- 两种看板执行最终都进入 `dashboard_runtime.execute_dashboard_panel_task_payload()`，再到 `build_dashboard_panel_execution_result()` 和 `execute_sql_for_member()`，统一做 execution 记录、成员数据源权限、limit、安全校验、缓存和真实执行。
- 看板任务返回结构不同于 SQL 任务：`GET /query-tasks/{task_id}` 对 `dashboard_batch` 返回 `result`，其中 `result.results` 是按 `panel_id` 索引的面板结果；SQL/Builder 查询返回顶层 `results[]` 成员结果。

### 统一结果口径

智能体、可视化查询、SQL 查询和看板查询只要涉及多个执行对象，成员结果统一使用 `results[]`：

```json
{
  "results": [
    {
      "member_datasource_id": "ORG_CODE1",
      "member_name": "财付通",
      "status": "succeeded",
      "sql": "SELECT ...",
      "columns": ["字段A"],
      "data": [{"字段A": 1}],
      "total_count": 1,
      "execution_time_ms": 56,
      "error": null
    }
  ]
}
```

字段约定：

- `member_datasource_id` 是执行对象标识，用于权限、历史、看板保存和回放。
- `member_name` 是用户可见名称，用于结果标题和图表展示。
- `status` 是成员执行状态主口径；成功为 `succeeded`，失败或取消由具体状态和 `error` 表达。
- `columns` 保留服务端返回顺序，前端表格不要仅从 `data[0]` 推断列顺序。
- `data` 是当前成员结果行，不承载多成员合并身份字段。

聚合级 `status`/`error` 约定（与 `build_query_result_payload` 对齐），状态按序判定：任一成员仍在 `pending`/`running` 时顶层为 `running`、`error` 为固定提示；无运行中成员且有任一成员成功时顶层为 `succeeded` 且 `error` 为 `null`，部分成员失败的信息只在对应 `results[]` 成员的 `status`/`error` 上，消费方判断整体成败必须遍历 `results[]`，不能只看顶层字段；全部成员取消时顶层为 `cancelled`，其余为 `failed`，这两种情形顶层 `error` 聚合各成员错误。

新增查询入口或结果字段时，需要同步检查：

```text
后端 router/service/runtime
-> web-vue/src/views/chat-data/api/* 类型
-> web-vue/src/views/chat-data/types/query.ts
-> web-vue/src/views/chat-data/utils/queryResult.ts
-> 智能体、可视化查询、SQL 查询、看板消费侧
```

---

## 语义层架构

### 核心流程：NL → DSL → SQL

```
用户自然语言
     ↓
[1. 智能体选择指标集和语义配置]
     ↓
[2. 智能体只生成 DSL 参数（指标/维度/过滤/排序/limit）
     ↓
[3. SemanticAIQueryAdapter 校验 DSL 并映射语义配置
     ↓
[4. compiler.compile_query_plan_sql 生成 SQL
     ↓
[5. engine.dialect 按 source_type 适配 MySQL/Hive 方言
     ↓
最终 SQL
```

### 语义配置结构

```python
semantic_config = {
    "id": "config_id",
    "name": "配置名称",
    "source_type": "mysql",  # mysql / hive

    # 可查询的指标
    "metrics": [
        {
            "name": "集群数量",
            "expression": "COUNT(DISTINCT cluster_id)",
            "table": "db_cluster",
            "description": "统计集群总数",
            "filter": "is_deleted = 0"  # 可选：指标自带过滤
        }
    ],

    # 可用维度
    "dimensions": [
        {
            "name": "数据库类型",
            "column": "db_type",
            "table": "db_cluster",
            "logical_type": "enum",  # text / number / date / datetime / enum
            "allowed_values": ["MySQL", "PostgreSQL"],  # 枚举约束
            "mapping": {"mysql": "MySQL"}  # 同义词映射
        }
    ],

    # 表关系（用于 JOIN）
    "joins": [
        {
            "left_table": "db_cluster",
            "right_table": "db_instance",
            "left_column": "cluster_id",
            "right_column": "cluster_id",
            "join_type": "left"
        }
    ]
}
```

### DSL 入参结构

```python
dsl = {
    "metrics": ["集群数量"],
    "dimensions": ["数据库类型"],  # 或 [{"name": "创建时间", "time_grain": "month"}]
    "filters": [],                # WHERE 条件，支持树形 AND/OR
    "having": [],                 # HAVING 条件
    "order_by": [],               # [{"field": "集群数量", "direction": "DESC"}]
    "limit": 100
}
```

---

## SQL 场景覆盖

### 已支持场景

| 类别 | 支持的功能 | 示例 |
|-----|----------|------|
| **聚合函数** | COUNT, SUM, AVG, MAX, MIN, COUNT(DISTINCT) | `COUNT(DISTINCT cluster_id)` |
| **自定义表达式** | 复杂计算表达式 | `SUM(quantity * unit_price)` |
| **WHERE 过滤** | =, !=, >, <, >=, <=, IN, NOT IN, LIKE | `WHERE db_type = 'MySQL'` |
| **树形过滤** | AND/OR 嵌套组合 | `(A OR B) AND C` |
| **GROUP BY** | 单/多维度分组 | `GROUP BY db_type, status` |
| **时间粒度** | year/quarter/month/week/day/hour | `DATE_FORMAT(col, '%Y-%m')` |
| **HAVING** | 聚合结果过滤（支持 AND/OR 树形结构） | `HAVING (a > 10 OR b > 5)` |
| **ORDER BY** | 单/多字段排序 | `ORDER BY count DESC` |
| **LIMIT/OFFSET** | 分页 | `LIMIT 10 OFFSET 20` |
| **JOIN** | LEFT/INNER/RIGHT，自动路径查找 | 自动多表关联 |
| **SQL函数值** | 时间函数等 | `date_sub(current_date(), 7)` |
| **枚举验证** | 值约束和同义词映射 | `mysql` → `MySQL` |
| **明细查询** | 非聚合原始数据查询 | 列出明细数据 |

### MySQL/Hive 语法适配

| 时间粒度 | MySQL | Hive |
|---------|-------|------|
| year | `DATE_FORMAT(col, '%Y')` | `DATE_FORMAT(col, 'yyyy')` |
| month | `DATE_FORMAT(col, '%Y-%m')` | `DATE_FORMAT(col, 'yyyy-MM')` |
| day | `DATE(col)` | `TO_DATE(col)` |
| hour | `DATE_FORMAT(col, '%Y-%m-%d %H:00')` | `DATE_FORMAT(col, 'yyyy-MM-dd HH:00')` |

### 不支持/受限场景

| 类别 | 缺失功能 | 影响 | 替代方案 |
|-----|---------|-----|---------|
| **子查询** | IN (SELECT...), EXISTS | 无法做关联过滤 | 分步查询 |
| **UNION** | UNION / UNION ALL | 无法合并结果集 | 分别查询后合并 |
| **窗口函数** | ROW_NUMBER, RANK, LAG/LEAD | 无法做排名 | 暂无 |
| **同比/环比** | compare SQL | 不在 DSL2SQL 内生成 | 基础 DSL 查询后由智能体脚本计算 |
| **FULL OUTER JOIN** | 全外连接 | - | LEFT + UNION |
| **CASE WHEN 动态** | 只能配置静态表达式 | - | 预定义表达式 |

### 场景覆盖率

```
┌──────────────────────────────────────────────────────────┐
│ 日常 BI 报表查询（聚合统计、多维分析）     ████████████ 95% │
│ 多表关联查询                              █████████░░ 85% │
│ 时间序列分析（趋势、时间粒度）             █████████░░ 85% │
│ 高级分析（排名、复杂过滤）                 ██░░░░░░░░░ 15% │
└──────────────────────────────────────────────────────────┘
```

---

## 核心组件

### 文件结构

```
chat_data_app/
├── agent/
│   ├── chat_data_agent.py # 智能问数 create_agent 组装和状态定义
│   ├── analyze_data_agent.py # 智能分析 create_agent 组装和状态定义
│   ├── sql_assist_agent.py# SQL 辅助 create_agent 组装和状态定义
│   ├── agent_runtime.py   # 问数和分析共用运行时工具
│   ├── middleware/        # create_agent middleware、上下文编辑和 token usage
│   ├── tools/             # 工具包
│   ├── config.py          # 模式配置
│   ├── prompts/           # 提示词模板和 system prompt builder
│   └── skills/            # 系统技能源目录；发布时复制到 CHAT_DATA_HOME/skills/system
├── services/
│   ├── datasource_manager.py          # 数据源管理
│   └── datasource_metadata.py         # MySQL/Hive 物理元数据实现、适配器与注册表
├── evaluate/
│   └── accuracy/                      # 页面/API 准确率评测执行器
├── tests/
│   ├── unit/                          # 默认单元测试
│   └── api/                           # 开发阶段手动执行的 API 契约测试
└── agent_stream.py                    # SSE 流式响应
```

### 关键组件说明

| 组件 | 文件 | 职责 |
|---|-----|------|
| `SemanticAIQueryAdapter` | semantic/semantic_query_service.py | 校验 DSL，映射指标/维度，组装 builder_config |
| `compile_query_plan_sql` | semantic/builder_sql_compiler.py | 将 builder_config 编译为 SQL |
| `get_dialect` | semantic/dialect.py | 按 source_type 提供 MySQL/Hive 方言 |
| `InputValidator` | semantic/input_validator.py | 校验指标、维度、过滤、排序和 limit |

### create_agent 工作流

```
create_agent
  ├─ model 节点：构造动态 system prompt、执行上下文裁剪、调用模型、写入 token_usage
  └─ tools 节点：执行 file_read、data_analyzing_tool 等工具

model → tools → model，直到模型不再发起工具调用后结束。
```

### 工具列表

| 工具 | 说明 |
|-----|------|
| `file_read` | 读取受控会话文件，或读取运行时技能快照中的 `SKILL.md` 与其声明资源 |
| `data_analyzing_tool` | 数据分析合并工具：按 `action` 分派——`validate_dsl` 根据语义参数校验并生成 SQL、`execute_query` 执行语义查询、`validate_dashboard` 批量校验看板规格、`check_yaml` 语义模型 YAML 自检 |

### 智能问数上下文边界

智能问数按四层组织上下文，避免系统提示词、指标集、技能和复杂任务流程互相覆盖：

| 层级 | 职责 | 不负责 |
|---|---|---|
| 系统提示词 | 全局问数工作流、工具协议、安全边界、输出约束 | 具体业务口径、物理表细节 |
| 指标集 | 可执行事实源：指标、维度、关系、枚举、日期格式、单位、口径 | 业务培训材料、跨领域常识 |
| 技能 | 用户显式引用时补充业务术语、口径、操作步骤和产物处理规则 | 当前指标集的事实定义与数据权限 |

优先级固定为：

```text
安全规则 > 系统工具协议 > 数据源组说明 > 当前指标集 > 用户明确补充 > 显式指定的技能 > 模型常识
```

智能问数主链路由系统提示词和工具链固定为：

```text
判断是否查数
 -> 第一步 file_read 读取 semantic-query 技能的 SKILL.md（语义查询门禁）
-> 当前数据源组全部启用语义模型的 YAML 已预物化到 /mnt/user-data/workspace/semantic/
-> 按 semantic-query 技能选模型（目录检索、换词规则、无命中判定与精读口径确认的唯一维护点都在该技能，此处不展开）
-> 只对命中的候选模型 file_read 精读 YAML，确认指标/维度/关系与口径后选定模型
-> 生成 DSL
-> DSL2SQL
-> 查询
-> 回答
```

系统技能、当前用户已发布技能及他人 global 已发布技能会在会话创建或首次运行时构建为冻结的运行时快照；模型只可通过 `file_read` 访问快照中的 `/mnt/skills/<name>/SKILL.md` 与技能入口明确声明的只读资源。脚本是否能执行只由 `bash_enabled` 决定。但 Bash 工具自身校验可被「写脚本再执行」绕过，开启 Bash 时必须配合 `sandbox` 后端做隔离兜底（见「运行环境后端与 Bash 安全模型」）。技能不替代当前指标集，业务背景优先通过数据源组智能问数说明和指标集配置维护。

指标集检索分两层：

1. 目录召回：只使用当前用户可见、启用指标集的 `id/name/description`，召回 topK 候选；召回失败时回退全量。
2. 候选检索：只对候选指标集加载完整运行时语义配置，返回相关指标集 ID；不判断问题能否回答、不生成执行计划。

主模型根据检索返回的相关指标集和用户问题决定是否查询、澄清，或使用多个独立指标集分别查询后再汇总/计算：

```text
每次 DSL / 查询 -> 一个 semantic_config_id -> 独立 DSL -> 独立查询
多个独立查询 -> 最终由智能体汇总；需要比例、差值时使用 calculator_tool
```

不支持跨指标集 JOIN、自动补关系、混合维度分组或拼成一张宽表。

---

## 高级特性

### 1. 树形过滤条件（WHERE）

支持复杂的 AND/OR 嵌套：

```python
# (数据库类型=MySQL OR 数据库类型=PostgreSQL) AND 状态=active
filters = [{
    "and": [
        {"or": [
            {"dimension": "数据库类型", "operator": "=", "value": "MySQL"},
            {"dimension": "数据库类型", "operator": "=", "value": "PostgreSQL"}
        ]},
        {"dimension": "状态", "operator": "=", "value": "active"}
    ]
}]
```

### 2. 树形 HAVING 条件

HAVING 也支持 AND/OR 嵌套，用于复杂的聚合结果过滤：

```python
# 集群数量 > 10 OR 实例数量 > 100
having = [{
    "or": [
        {"metric": "集群数量", "operator": ">", "value": 10},
        {"metric": "实例数量", "operator": ">", "value": 100}
    ]
}]

# 生成: HAVING (`集群数量` > 10 OR `实例数量` > 100)
```

### 3. 自动 JOIN 路径查找

使用 BFS 算法找到表间最短连接路径，支持多表链式 JOIN。

### 4. 时间粒度转换

支持 year/quarter/month/week/day/hour，自动适配 MySQL/Hive 语法。

### 5. 枚举值验证

- 自动验证值是否在 `allowed_values` 中
- 支持同义词映射（如 `mysql` → `MySQL`）

### 6. SQL 函数识别

自动识别 `date_sub()`、`current_date()` 等函数，不加引号处理。

---

## 错误处理

| 返回前缀 | 说明 | 处理方式 |
|---------|------|---------|
| `【需要确认】` | 需要用户补充信息（含参数遗漏） | 直接展示给用户，等待补充 |
| `抱歉，当前配置不支持` | 配置无法满足需求 | 直接告知用户 |

# 参考的一些NL2SQL / 语义层产品全景

### 云厂商方案
- AWS NL2SQL
- Google BigQuery
- Azure AI
- QuickSight Q

### BI 产品
- ThoughtSpot
- Tableau GPT
- Power BI Q&A/Copilot
- Qlik
- FineBI

### 语义层工具
- Cube.js
- dbt Metrics
- LookML
- AtScale
- MetricFlow

### 开源项目
- Vanna AI
- Chat2DB
- SQLCoder
- WrenAI
- Dataherald
