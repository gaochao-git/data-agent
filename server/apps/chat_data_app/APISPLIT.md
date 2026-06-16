# Chat Data API 拆分说明

## 1. 背景

`chat-data` 当前同时包含两类能力：

1. 与大模型直接相关的智能体能力
2. 与数据源、配置、执行、存储直接相关的业务能力

随着后续分工调整，计划将 `chat-data` 中不强依赖大模型的接口逐步迁移为 Java 服务；AI 团队继续维护与大模型、智能体编排、会话流转直接相关的接口。

本文件用于明确：

- 为什么要拆分
- 拆分边界怎么定义
- 当前接口分别归属哪个团队
- 后续迁移时需要注意什么

## 2. 拆分目标

接口拆分不是按“代码里有没有调用一次 LLM”来划分，而是按“接口主能力属于什么层”来划分。

目标边界如下：

- AI 团队负责：会话、智能体编排、自然语言理解、语义问数、SQL 辅助生成、AI 增强
- Java 团队负责：数据源管理、元数据同步、配置管理、SQL 执行、Builder 编译、Dashboard/Panel 持久化与执行

这样划分的原因：

- 智能体接口关注 Prompt、模型效果、多轮对话和流程编排
- 数据接口关注稳定性、性能、权限、事务、审计和可维护性

## 3. 划分原则

判断一个接口归属哪个团队，优先看下面三个问题：

1. 去掉大模型后，这个接口的主能力还能不能成立
2. 这个接口是不是结构化、确定性的业务服务
3. 这个接口是不是更关注数据正确性、权限、性能和存储，而不是模型理解和生成

判定规则：

- 如果主能力依赖模型理解、推理、生成，归 AI 团队
- 如果主能力是结构化数据服务、配置管理、执行服务，归 Java 团队
- 如果主接口是数据服务，但内部夹带图表推荐、命名、分析等 AI 增强，主接口仍归 Java，AI 能力后续建议拆出

## 4. 当前共识

### 4.1 AI 团队负责

#### 公共会话接口

- `GET /api/chatdata/conversations`
- `POST /api/chatdata/conversations`
- `GET /api/chatdata/conversations/{id}`
- `PUT /api/chatdata/conversations/{id}`
- `DELETE /api/chatdata/conversations/{id}`
- `POST /api/chatdata/conversations/{id}/completion`
- `POST /api/chatdata/conversations/{id}/resume`
- `POST /api/chatdata/conversations/{id}/title`

#### 智能问数接口
- `POST /api/chatdata/nl2sql`              # SQL 编辑器中的 AI 辅助生成 SQL 能力
- `POST /api/chatdata/semantic/prefill`    # 是语义字段自动预填，本质是字段语义识别
- `POST /api/chatdata/compile-builder-sql` # 拖拽调用后端产出SQL
- `POST /api/chatdata/panel-name-generate` # 生成卡片标题



### 4.2 Java 团队负责

#### 查询执行能力

- `POST /api/chatdata/execute-sql`  # 执行 SQL 并返回结果

#### 数据源管理

- `GET /api/chatdata/datasources`
- `PATCH /api/chatdata/datasources/{datasource_id}`
- `PATCH /api/chatdata/datasources/{datasource_id}/mapped-modules`
- `POST /api/chatdata/datasources/{datasource_id}/set-default`
- `DELETE /api/chatdata/datasources/{datasource_id}/set-default`
- `DELETE /api/chatdata/datasources/{datasource_id}`
- `GET /api/chatdata/datasources/{datasource_id}/schema`
- `POST /api/chatdata/datasources/db-tables`

说明：

- 这部分是数据源与元数据管理能力
- 主要关注连库、拉表、同步结构、维护默认数据源和元数据

#### 语义配置管理

- `GET /api/chatdata/datasources/{datasource_id}/semantic-configs`
- `POST /api/chatdata/datasources/{datasource_id}/semantic-configs`
- `PUT /api/chatdata/datasources/{datasource_id}/semantic-configs/{config_id}`
- `DELETE /api/chatdata/datasources/{datasource_id}/semantic-configs/{config_id}`
- `PATCH /api/chatdata/datasources/{datasource_id}/semantic-configs/{config_id}/toggle`

#### 查询收藏

- `GET /api/chatdata/query-favorites`
- `POST /api/chatdata/query-favorites`
- `PUT /api/chatdata/query-favorites/{favorite_id}`
- `DELETE /api/chatdata/query-favorites/{favorite_id}`

#### 看板 / 卡片

- `GET /api/chatdata/dashboards`
- `POST /api/chatdata/dashboards`
- `GET /api/chatdata/dashboards/{dashboard_id}`
- `PUT /api/chatdata/dashboards/{dashboard_id}`
- `DELETE /api/chatdata/dashboards/{dashboard_id}`
- `POST /api/chatdata/dashboards/{dashboard_id}/panels`
- `PUT /api/chatdata/dashboards/{dashboard_id}/panels/{panel_id}`
- `DELETE /api/chatdata/dashboards/{dashboard_id}/panels/{panel_id}`
- `PUT /api/chatdata/dashboards/{dashboard_id}/layout`
- `POST /api/chatdata/dashboards/{dashboard_id}/panels/{panel_id}/execute`
- `POST /api/chatdata/dashboards/{dashboard_id}/panels/execute-batch`
