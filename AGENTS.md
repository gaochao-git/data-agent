# AGENTS.md

本文件用于说明本仓库的“Agents”相关模块、开发规范与协作约定。请在修改前先阅读本文件与各模块 README。

## 1. Agents 范围与模块

本仓库中的 Agents 以“业务能力模块”为单位，核心入口在 `server/apps/`：
- 投诉分析：`server/apps/complaint_app/README.md`
- 文件字段抽取：`server/apps/word_fit_app/README.md`
- 对话助手：`server/apps/chat_assistant/README.md`

如需新增 Agent，请在对应目录补充 README，并在根目录 `README.md` 中加入链接。

## 2. 环境搭建

### 2.1 后端
- 入口：`server/main.py`
- 依赖：`server/requirements.txt`
- 环境变量：`server/.env.example`

### 2.2 前端
- 入口：`web/`
- 依赖：`web/package.json`
- 环境变量：`web/.env.example`

详细步骤请参考：
- `server/README.md`
- `web/README.md`

## 3. 测试与构建

- 后端测试：`cd server && pytest`
- 前端构建：`cd web && npm run build`
- 前端 E2E：`cd web && npm run test:e2e`

## 4. 代码风格与提交规范

- 代码风格：保持与现有代码一致，优先复用已有结构与命名。
- 格式化/检查：若仓库已有配置（如 eslint/prettier/ruff），以现有配置为准。
- 提交规范：若无统一规范，提交信息保持简洁清晰，描述“做了什么”。
- complaint_app模块：日志调用避免多行参数列表写法，尽量保持单行（例如 `logger.error("xxx: %s", err)`），便于阅读与检索。
- complaint_app模块：对外函数/方法签名保持单行，不使用多行参数列表形式。
- complaint_app模块：新增或改造的模型调用统一使用异步方式。
- complaint_app模块：所有改动尽可能与现有实现保持一致并控制在最小范围，除非明确要求不得新增策略/回退/字段，存在不确定项先确认再修改。
- complaint_app模块：禁止在方法内部定义新方法（内嵌函数），统一使用顶层函数组织代码。
- complaint_app模块：主流程方法应拆分为多个职责单一的函数，避免超长函数；抽取的函数需补一行简短中文注释说明用途。
- complaint_app模块：遵循变量作用域最小化原则，变量只在实际使用的方法内定义，避免在上层暴露仅用于一次下游调用的中间变量。
- complaint_app模块：新增或改造的非流式模型调用也必须显式处理模型“思考开关”，统一通过 `utils.llm_thinking.build_thinking_extra_body(model_name, enable_thinking)` 生成 `extra_body`，不要手写各模型分支。
- complaint_app模块：针对 `GLM-4.7` 及其变体，默认按“不思考”方式请求；除非需求已明确要求开启思考，否则必须显式传关闭思考参数，避免公司内网兼容问题。
- complaint_app模块：读取模型返回内容时，不能只依赖 `message.content`；若正文为空，必须按既有兼容口径回退检查 `reasoning_content`、`reasoning`、`analysis`、`thoughts`、`think` 等字段，避免不同模型/网关返回差异导致空结果。
- complaint_app模块：凡是要求模型输出 JSON 的场景，服务端必须做解析校验与失败回退，不能直接信任模型原样输出。
- complaint_app模块：禁止静默吞掉异常；凡是 `except` 后选择继续流程的场景，必须至少记录真实错误信息与必要上下文，不能只返回默认值或直接 `continue`。
- complaint_app模块：严禁擅自新增未沟通需求或变更业务口径，任何超出当前确认范围的实现必须先与需求方确认并获得明确同意后再修改代码。
- complaint_app模块：开始实现前，必须先与需求方明确实现方法并获得确认；未经确认不得自行选择实现路径或在实现中途擅自切换方案。

## 5. 目录与文件约定

### 5.1 不要随意改动
- 运行产物/缓存：`DATA_ROOT/`、`server/logs/`、`web/dist/`
- 依赖目录：`web/node_modules/`、`server/venv/`、`__pycache__/`
- 环境文件：`.env`、`*.local`、密钥配置
- 系统文件：`.DS_Store`

### 5.2 新增文件
- 新模块必须带 `README.md`。
- 新配置必须补充到对应 `*.env.example`。

## 6. 不确定时的处理方式

- 先查：`README.md`、`server/README.md`、`web/README.md`、`docs/`。
- 再查：相关模块目录下的 README 与入口文件。
- 仍不确定：记录假设与风险，先询问再改动。

## 7. 安全与合规

- 禁止提交真实密钥与业务数据。
- 涉及鉴权、CORS、上传大小等配置时，务必评估生产影响。

## 8. mysql表结构规范
### 必填字段
  - `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  - `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  - `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  - `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',          # 用户id，因为用户名有中文后续处理麻烦
  - `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',  # 用户名便于显示、审计
  - `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',          # 用户id，因为用户名有中文后续处理麻烦
  - `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',  # 用户名便于显示、审计
### 其他规则
  - 除主键id外全部业务字段全部下划线字段
  - 不能有外键
  - 不能有 json字段类型
  - 每表索引不超过 5 个
  - 组合索引字段不能超过5个
  - 表命、列名、索引名不能超过32
  - 唯一索引 uniq_
  - 二级索引 idx_

## 9. 其他
- 用的fastapi框架，要采用异步调用，避免阻塞主线程
- 所有包导入必须放在文件头部，禁止在函数或类内部 import
- 业务库访问建议统一使用 ORM/session
- 智能体消息第一条必须是system,且只能有一个system消息，必须要有Human消息,千问3.5+模型现在对消息格式要求很严格