# ChatData 代码治理收敛记录

本文档用于记录 ChatData 智能问数模块的代码治理方向，目标是后续按小批次逐步收敛公共能力，避免历史问题继续扩大。

治理原则：

- 先盘点再修改，不做无边界大重构。
- 每次只治理一个主题，保持接口行为稳定。
- 公共能力只保留一个主入口，避免新增平行实现。
- 核心链路改动必须同时检查后端接口、服务层、前端类型、页面消费和测试。
- 改完必须做对应验证；涉及智能体消息结构、提示词或工具链时，需要真实链路验证。

## 当前范围

重点模块：

- 后端应用：`server/apps/chat_data_app/`
- 对话与 LangGraph 公共能力：`server/utils/lang_util/`
- 上传文件服务：`server/utils/file_service/`
- 日志、响应、加密、存储路径：`server/utils/`
- 中间件：`server/middleware/`
- 前端页面：`web-vue/src/views/chat-data/`

## 现状判断

### 已经有统一方向的能力

- 日志集中在 `server/utils/logger.py`，已区分应用日志、访问日志和告警日志。
- 运行环境加载集中在 `server/utils/lang_util/common_util.py`，入口由 `server/main.py` 调用。
- 业务库会话集中在 `server/utils/lang_util/database.py`。
- MySQL 节点解析和 failover 判断集中在 `server/utils/lang_util/db_failover.py`。
- LLM 底层封装在 `server/utils/lang_util/llm.py`，ChatData 专用配置在 `server/apps/chat_data_app/services/llm_config.py`。
- 查询结果前端已有共享组件基础：`QueryResultPanel`、`AsyncQueryResult`、`ExecutionTargetPicker`。

### 需要继续收敛的能力

- 认证 helper 存在多处包装：
  - `server/auth.py`
  - `server/apps/chat_data_app/routers/common.py`
  - `server/utils/lang_util/conversation_router.py`
- 统一响应主入口是 `server/utils/response.py`，但不少接口仍在 `except Exception` 后拼接 `str(e)` 返回，错误输出口径需要继续收敛。
- `server/utils/lang_util/conversation_router.py` 名义上是公共会话路由，但路径和业务行为已经偏 ChatData，需要明确边界。
- `server/apps/chat_data_app/routes.py` 仍承载数据源、SQL 执行、查询历史、收藏、停止任务等多类职责。
- SQL 查询链路存在多个入口适配层，必须保持 `results[]`、`member_datasource_id`、`member_name`、`status`、`execution_time_ms`、`error` 等字段口径一致。
- 前端多个入口仍各自维护查询结果适配逻辑：
  - 智能问数：`SqlConversation.vue`
  - SQL 查询：`SqlEditor.vue`
  - 可视化查询：`QueryBuilder/index.vue`

## 治理批次

### 1. 基础设施收口

目标：统一底层公共能力，减少后续业务层重复实现。

建议任务：

- 统一认证读取入口，后续路由只通过一个 helper 获取当前用户。
- 统一普通 HTTP JSON 错误输出策略，避免数据库、驱动、框架、堆栈等内部异常泄露到前端。
- 明确 SSE 错误事件的用户可见文案和日志记录方式。
- 明确环境加载约束，避免脚本或测试绕过 `main.py` 导致 import 阶段缺少环境变量。
- 检查日志上下文 `trace_id`、`user_id` 是否贯穿应用日志、访问日志和告警日志。

验证要求：

- 后端导入检查：`cd server && python - <<'PY'\nimport main\nprint('backend import ok')\nPY`
- 认证、响应、日志相关单测。
- 关键接口返回结构不变。

### 2. SQL 查询结果契约收口

目标：让智能问数、SQL 查询、可视化查询、看板共用同一套结果结构。

统一字段：

- `success`
- `status`
- `sql`
- `columns`
- `data`
- `total_count`
- `execution_time_ms`
- `error`
- `member_datasource_id`
- `member_name`
- `results[]`

建议任务：

- 后端只在一个位置构造成员执行结果。
- HTTP 查询、MySQL 直连查询、缓存结果、历史回放都复用同一结构。
- 前端 `QueryResultPanel` 和 `AsyncQueryResult` 作为结果展示主入口。
- 智能问数、SQL 查询、可视化查询只做轻量映射，不再各自拼接标题、状态和执行对象展示。

验证要求：

- 单执行对象、多执行对象、HTTP 异步、MySQL 直连、缓存命中、历史回放都要验证。
- 保存到看板时确认保存的是当前结果对应的 `datasource_id + member_datasource_id + sql`。

### 3. 路由职责拆分

目标：降低 `routes.py` 复杂度，但不改变 URL 行为。

建议拆分方向：

- 数据源管理：迁移到独立 route module。
- 查询历史：迁移到独立 route module。
- SQL 执行与查询任务：迁移到独立 route module。
- 收藏查询：迁移到独立 route module。
- 保留 `routes.py` 作为聚合入口，只 include 子路由。

约束：

- URL 先保持兼容。
- 后端返回结构保持兼容。
- 前端 API 调用不随意迁移路径，除非明确进入后台管理命名空间治理。

### 4. 智能体链路治理

目标：稳定智能问数、智能分析、SQL 助手的状态、工具和提示词边界。

建议任务：

- 明确 `ChatDataAgentState`、`SqlAssistState`、智能分析 state 的职责，避免共享状态混用。
- 工具按能力分组：
  - 智能问数：只读技能、指标集选择、DSL2SQL、语义查询、计算器。
  - SQL 助手：SQL 生成，不隐式决定执行对象。
  - 智能分析：沙箱、文件、产物、脚本能力。
- 智能问数只加载只读技能，不引入脚本、资产、沙箱运行能力。
- 统一真实思考块来源，避免假思考和模型原生 reasoning 重复渲染。

验证要求：

- 千问 3.5+ 严格消息格式验证。
- GLM/vLLM 流式工具调用兼容验证。
- checkpoint 历史消息恢复验证。

### 5. 前端组件治理

目标：把重复 UI 和数据适配收敛到共享组件。

建议任务：

- 执行对象选择统一使用 `ExecutionTargetPicker`。
- 查询结果统一使用 `QueryResultPanel` / `AsyncQueryResult`。
- SQL 历史列表抽出可复用列配置和筛选逻辑。
- 大组件继续拆分：
  - `QueryBuilder/index.vue`
  - `SemanticConfigPanel.vue`
  - `SqlEditor.vue`
  - `SqlConversation.vue`

验证要求：

- `cd web-vue && npm run build-only`
- 智能问数、SQL 查询、可视化查询、看板预览至少做一次页面验证。

## 高风险区域

这些文件不要在无关需求里顺手大改：

- `server/apps/chat_data_app/services/query_runtime.py`
- `server/apps/chat_data_app/services/sql_executor.py`
- `server/apps/chat_data_app/agent/tools/sql.py`
- `server/apps/chat_data_app/handlers.py`
- `server/apps/chat_data_app/services/semantic_sql_generator.py`
- `server/apps/chat_data_app/services/semantic_builder_sql_compiler.py`
- `web-vue/src/views/chat-data/components/QueryBuilder/index.vue`
- `web-vue/src/views/chat-data/components/SqlEditor.vue`
- `web-vue/src/views/chat-data/components/SqlConversation.vue`

## 后续执行建议

每次治理按以下模板推进：

1. 明确本次治理主题和边界。
2. 先列出受影响接口、服务、前端组件和测试。
3. 小范围修改，不混入无关重构。
4. 跑对应单测、导入检查或前端构建。
5. 更新本文档中对应条目的状态或补充发现。
