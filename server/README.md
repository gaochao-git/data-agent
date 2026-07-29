# Data Agent Server

本目录为 Data Agent 后端服务，入口为 `main.py`，基于 FastAPI 提供智能问数、语义配置、SQL 查询、会话历史、文件与 artifact 相关接口。

当前后端只挂载智能问数相关模块：

- `apps/chat_data_app`
- `apps/chat_data_app/routers/`
- `utils/` 提供底层能力，不直接挂载 HTTP 路由

## 目录概览

```text
server/
├── main.py                         # FastAPI 入口
├── requirements.txt                # Python 依赖
├── conf/                           # 机房环境配置、示例和 IP 映射
├── apps/chat_data_app/             # 智能问数核心模块
├── utils/                          # 日志、响应、数据库、LLM、会话、artifact、文件基础服务
└── middleware/                     # API 日志等中间件
```

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
python server/main.py
```

默认端口为 `5001`。如果端口被占用：

```bash
PORT=5002 python server/main.py
```

也可以使用 uvicorn 单进程运行：

```bash
uvicorn main:app --app-dir server --host 0.0.0.0 --port 5001
```

## 生产运行

生产环境推荐使用 Gunicorn 管理多进程，UvicornWorker 负责运行 FastAPI ASGI 应用：

```bash
gunicorn main:app -c server/gunicorn.conf.py
```

常用生产参数通过环境变量控制：

- `PORT`：监听端口，默认 `5001`
- `GUNICORN_WORKERS`：worker 数量，默认 `4`
- `GUNICORN_TIMEOUT`：worker 超时时间，默认 `900` 秒，适配智能体、SQL 和沙箱长请求
- `GUNICORN_GRACEFUL_TIMEOUT`：优雅退出等待时间，默认 `30` 秒
- `GUNICORN_KEEPALIVE`：keepalive 时间，默认 `5` 秒

开发热更新仍使用 `python main.py` 和 `FASTAPI_RELOAD=true`；不要在热更新模式下使用多 worker。

停止服务：

```bash
# 优雅停止
kill -TERM $(lsof -ti :5001)

# 长任务卡住、需要立即停止时强制结束
kill -9 $(lsof -ti :5001)
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
- `GUNICORN_WORKERS` / `GUNICORN_TIMEOUT`：生产 Gunicorn 多进程和长请求超时配置
- `ALLOWED_ORIGINS`：CORS 允许来源
- `LOG_LEVEL` / `LOG_DIR` / `LOG_APP_NAME`：日志配置，默认按应用名分为应用日志、访问日志和告警日志；日志 IDC 使用启动时识别出的环境名
- `SERVICE_AUTH_TOKEN`：服务间调用认证 Token，存放 SM4 加密后的密文
- `DATABASE_URL` 或相关 MySQL 配置：业务库连接
- `CHAT_DATA_HOME`：ChatData 应用**唯一的数据根目录**（**必填**，禁止回退到其它变量推导）；会话工作区、卸载的查询结果、系统技能（`skills/system`）、用户技能（`skills/user`）、SQLite 检查点（`checkpoints.db`）均派生自此
- `OPENSANDBOX_HOST_CHAT_DATA_HOME`：OpenSandbox 宿主机看到的同一共享数据根；留空表示与应用侧路径相同，不同时按相对路径翻译
- `CHAT_DATA_API_KEY` / `CHAT_DATA_BASE_URL` / `CHAT_DATA_MODEL`：智能问数模型配置，其中 `CHAT_DATA_API_KEY` 存放 SM4 加密后的密文；`CHAT_DATA_MODEL` 格式为 `模型名;温度;上下文窗口`，多个模型用逗号分隔
- `CHAT_DATA_HTTP_*`：HTTP 模式下对接外部 Hive/查询服务，其中 `CHAT_DATA_HTTP_AUTH_TOKEN` 存放 SM4 加密后的密文
- `OPENSANDBOX_API_KEY`：智能分析 OpenSandbox API Key，存放 SM4 加密后的密文；留空表示不传 API Key
- `CHATDATA_ROW_LIMIT`：查询返回行数限制
- `CHATDATA_TOOL_RESULT_OFFLOAD_SIZE`：大结果卸载阈值，默认 32000 字符
- `CHATDATA_FILE_READ_MAX_CHARS`：`file_read` 单次返回给模型的正文上限，默认 16000 字符；按请求源行分页，超限时优先截断最长行并返回该行续读位置
- `CHATDATA_CONTEXT_COMPRESSION_ENABLED`：智能问数上下文压缩总开关，仅影响发给大模型的上下文，不影响页面历史、checkpoint 和审计记录
- `CHATDATA_CONTEXT_COMPRESSION_TRIGGER_RATIO`：输入达到模型上下文窗口的该比例时自动压缩，默认 `0.70`、最大 `0.80`；实际触发点同时受 `CHAT_DATA_MAX_TOKENS` 输出预留约束

## 运行时存储

`CHAT_DATA_HOME` 是 local 与 sandbox 共用的运行时文件根。生产多实例必须把它配置为所有应用实例和 OpenSandbox 宿主机可访问的同一 POSIX 共享目录，例如 GFS、NFS、CephFS、EFS 或 RWX PVC；代码不做跨节点文件同步。

会话文件位于 `users/{user_id}/threads/{conversation_id}/user-data/`，系统技能位于 `skills/system/`，用户技能位于 `skills/user/`，大查询结果位于会话 `outputs/.tool-results/query_result/`。系统技能由发布流程将代码包 `apps/chat_data_app/agent/skills/` 下的技能目录复制到 `skills/system/`，应用运行时只读且不写入技能数据库；用户技能继续由数据库元数据和 `skills/user/<skill-name>/versions/vN/` 管理。前端始终携带会话 ID 请求结果，后端据此校验当前用户归属并按固定路径读取。生产环境的 LangGraph checkpoint 使用 MySQL。MinIO 不在工作区、技能或查询结果的运行时链路中。

## 权限边界

`data-agent` 按 service 层运行，不直接承担前端菜单和后台角色授权。`/api/chatdata/admin/**` 是否允许访问由上层 controller/BFF 基于登录态和权限点统一拦截，例如 `chatdata:admin:manage`。

`data-agent` 仍然必须校验服务间调用身份和业务数据权限：

- 只信任带有正确 `X-Service-Token`、`X-User-Id`、`X-Username` 的请求。
- 所有 `/api/chatdata/admin/**` 接口至少解析当前用户，用于身份确认、审计字段和下游 HTTP 元数据服务透传。
- 会话、收藏、查询历史、看板、用户技能等按 `user_id`、owner 或 public/global 状态做业务数据隔离。
- 数据源执行对象权限在 service 层强制校验。HTTP 执行对象通过 `CHAT_DATA_HTTP_PERMISSION_URL` 获取当前用户可用机构编码；direct 执行对象使用服务端固定允许编码。未配置或无权限的执行对象不可见、不可执行。

## 主要接口

智能问数：

- `/api/chatdata/*`
- `/api/chatdata/conversations/*`
- `/api/chatdata/uploaded-files/*`

具体接口以 `apps/chat_data_app/routes.py` 和 `apps/chat_data_app/routers/` 为准。

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
