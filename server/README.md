# Data Agent Server

本目录为 Data Agent 后端服务，入口为 `main.py`，基于 FastAPI 提供智能问数、语义配置、SQL 查询、会话历史、文件与 artifact 相关接口。

当前后端只挂载智能问数相关模块：

- `apps/chat_data_app`
- `utils/lang_util/conversation_router.py`
- `utils/file_service`

## 目录概览

```text
server/
├── main.py                         # FastAPI 入口
├── requirements.txt                # Python 依赖
├── conf/                           # 机房环境配置、示例和 IP 映射
├── apps/chat_data_app/             # 智能问数核心模块
├── utils/lang_util/                # 会话、LangGraph、artifact、LLM 公共能力
├── utils/file_service/             # 文件上传/解析基础服务
└── middleware/                     # API 日志等中间件
```

## 本地运行

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

默认端口为 `5001`。如果端口被占用：

```bash
PORT=5002 python main.py
```

也可以使用 uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 5001
```

## 关键配置

运行配置位于 `server/conf/`：

- `deploy_env_hosts.json`：维护本机 IP 到环境名的映射。
- `.env-{env}`：对应环境的变量配置，例如 `.env-dev`、`.env-bj12`。
- `.env.example`：环境变量示例。

启动时先根据本机 IP 匹配环境，再加载对应 `.env-{env}`。如果没有匹配到环境，或缺少对应配置文件，服务会直接启动失败。
本地需要强制指定环境时，可以设置 `CHAT_DATA_ENV=dev`。

常用配置包括：

- `PORT`：后端服务端口
- `ALLOWED_ORIGINS`：CORS 允许来源
- `LOG_LEVEL` / `LOG_DIR` / `LOG_APP_NAME`：日志配置，默认按应用名分为应用日志、访问日志和告警日志；日志 IDC 使用启动时识别出的环境名
- `SERVICE_AUTH_TOKEN`：服务间调用认证 Token
- `DATABASE_URL` 或相关 MySQL 配置：业务库连接
- `CHAT_DATA_API_KEY` / `CHAT_DATA_BASE_URL` / `CHAT_DATA_MODEL` / `CHAT_DATA_TEMPERATURE`：智能问数模型配置，其中 `CHAT_DATA_API_KEY` 存放 SM4 加密后的密文
- `CHAT_DATA_HTTP_*`：HTTP 模式下对接外部 Hive/查询服务，其中 `CHAT_DATA_HTTP_AUTH_TOKEN` 存放 SM4 加密后的密文
- `CHATDATA_ROW_LIMIT`：查询返回行数限制
- `CHATDATA_TOOL_RESULT_OFFLOAD_SIZE`：大结果转 artifact 阈值

## 主要接口

健康检查：

- `GET /healthz`
- `GET /api/healthz`

智能问数：

- `/agents/api/chatdata/*`
- `/agents/api/chatdata/conversations/*`
- `/api/chatdata/uploaded-files/*`

具体接口以 `apps/chat_data_app/routes.py`、`apps/chat_data_app/routers/` 和 `utils/lang_util/conversation_router.py` 为准。

## 测试

```bash
cd server
pytest apps/chat_data_app/tests
```

部分测试依赖 MySQL、LLM 或外部 HTTP 查询服务，需要按 `.env` 配置本地环境。

## 注意事项

- 不要提交真实 `.env`、Token、数据库密码或业务数据。
- `main.py` 只应挂载当前项目需要的智能问数相关路由。
- 已删除的旧业务 app 不应再被 import 或 include_router。
