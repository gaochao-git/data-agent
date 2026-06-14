# AGENTS.md

本文件用于说明 Data Agent 项目的智能问数模块、开发规范与协作约定。修改前先阅读本文件、根目录 `README.md` 以及相关模块 README。

## 1. 模块范围

当前项目以智能问数为核心：
- 后端应用：`server/apps/chat_data_app/`
- 对话与 LangGraph 通用能力：`server/utils/lang_util/`
- 上传文件服务：`server/utils/file_service/`
- 前端页面：`web-vue/src/views/chat-data/`
- 前端路由入口：`web-vue/src/router/index.ts`

如需新增 Agent 或业务模块，需先明确边界，再补充对应 README、路由挂载与环境变量示例。

## 2. 环境搭建

### 2.1 后端
- 入口：`server/main.py`
- 依赖：`server/requirements.txt`
- 环境变量示例：`server/.env.example`

### 2.2 前端
- 入口：`web-vue/`
- 依赖：`web-vue/package.json`
- 环境变量示例：`web-vue/.env.example`

详细步骤参考：
- `server/README.md`
- `web-vue/README.md`

## 3. 测试与构建

- 后端测试：`cd server && pytest`
- 后端导入检查：`cd server && python - <<'PY'\nimport main\nprint('backend import ok')\nPY`
- 前端构建：`cd web-vue && npm run build-only`
- 前端完整检查：`cd web-vue && npm run build`

## 4. 代码风格

- 保持与现有代码一致，优先复用已有结构、命名和工具函数。
- 所有改动控制在当前需求范围内，避免顺手重构无关模块。
- FastAPI 接口和智能体链路优先使用异步实现，避免阻塞主线程。
- Hive direct 等无法真正 async 的同步库调用，使用线程池包裹；后续下线前不做大改。
- 业务库访问建议统一使用 ORM/session；历史手写 SQL 逐步收敛，不在无关需求里大面积改造。
- 所有包导入放在文件头部，避免在函数或类内部 import，除非为了解决明确的循环依赖或可选依赖。
- 模型输出 JSON 的场景，服务端必须做解析校验与失败处理，不能直接信任模型原样输出。
- 异常不能静默吞掉；`except` 后继续流程时，至少记录真实错误信息和必要上下文。
- 智能体消息第一条必须是 system，且只能有一个 system 消息；必须包含 Human 消息。
- 千问 3.5+ 模型对消息格式要求严格，改提示词或消息结构时必须验证。

## 5. 目录与文件约定

### 5.1 不要随意改动
- 运行产物/缓存：`DATA_ROOT/`、`server/logs/`、`web-vue/dist/`
- 依赖目录：`web-vue/node_modules/`、`server/venv/`、`__pycache__/`
- 环境文件：`.env`、`*.local`、密钥配置
- 系统文件：`.DS_Store`

### 5.2 新增文件
- 新模块必须带 README。
- 新配置必须补充到对应 `*.env.example`。
- 新接口应与现有 URL 风格、认证方式和返回结构保持一致。

## 6. MySQL 表结构规范

### 必填字段

- `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID'
- `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
- `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'
- `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人'
- `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称'
- `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人'
- `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称'

字段语义：
- `create_by`、`update_by` 存用户 ID，用于权限、归属和审计。
- `create_by_name`、`update_by_name` 存用户名，用于前端展示和审计可读性。
- `user_id` 表示业务归属用户，与创建人/修改人不是同一个概念。

### 其他规则

- 除主键 `id` 外，业务字段使用小写下划线命名。
- 不使用外键。
- 不使用 MySQL JSON 字段类型；需要扩展结构时优先使用字符串字段存储 JSON 文本，并在服务端解析校验。
- 每表索引不超过 5 个。
- 组合索引字段不超过 5 个。
- 表名、列名、索引名不超过 32 个字符。
- 唯一索引以 `uniq_` 开头。
- 二级索引以 `idx_` 开头。

## 7. 安全与合规

- 禁止提交真实密钥、Token、账号密码和业务数据。
- 涉及鉴权、CORS、上传大小、HTTP Hive 对接参数时，必须评估联调和生产影响。
- HTTP 调用外部 Hive 服务时，只在边界层转换对方要求的字段命名；项目内部字段保持小写下划线。
- 相关功能不允许SQL注入
- 相关功能不允许横向越权

## 8. 不确定时的处理方式

- 先查：`README.md`、`server/README.md`、`web-vue/README.md`、相关模块 README。
- 再查：入口文件、路由、服务层和测试。
- 仍不确定：记录假设与风险，先询问再改动。
