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
- `CHAT_DATA_API_KEY` / `CHAT_DATA_BASE_URL` / `CHAT_DATA_MODELS_FILE`：智能问数模型配置，其中 `CHAT_DATA_API_KEY` 存放 SM4 加密后的密文；模型清单来自 `CHAT_DATA_MODELS_FILE` 指向的 JSON 注册表（`conf/` 下按供应商分文件，如 `models.openrouter.json`），数组首项为默认模型。每个条目包含 `id` / `temperature`（0~2）/ `context_window`（正整数）/ `vlm`（是否接受图片视觉输入，必须只配置给真实具备视觉能力的模型）/ `thinking`（思考能力声明：`mode` 为 `none`/`optional`/`forced`，`dialect` 为 `chat_template`/`reasoning_effort`/`openrouter`）。任何字段非法都会阻止服务启动；旧变量 `CHAT_DATA_MODEL` 已废弃，残留会导致启动报错
- `CHAT_DATA_HTTP_*`：HTTP 模式下对接外部 Hive/查询服务，其中 `CHAT_DATA_HTTP_AUTH_TOKEN` 存放 SM4 加密后的密文
- `OPENSANDBOX_API_KEY`：智能分析 OpenSandbox API Key，存放 SM4 加密后的密文；留空表示不传 API Key
- `CHATDATA_ROW_LIMIT`：查询返回行数限制
- `CHAT_DATA_TOOL_RESULT_OFFLOAD_SIZE`：普通工具结果卸载阈值，默认 32000 字符；查询结果统一保存到会话 `outputs/query_result/<id>.json`，普通工具大结果保存到 `outputs/.tool-offload/<id>.txt|json`
- `CHAT_DATA_FILE_READ_MAX_CHARS`：`file_read` 单次返回给模型的正文上限，默认 25000 字符；按请求源行分页，单行最多返回 500 字符
- `CHAT_DATA_MAX_UPLOAD_SIZE_MB`：单文件上传大小上限（MB，正整数），默认 `50`；流式边读边判，超限即拒绝并清理临时文件。前端另有一份 50MB 的客户端预检（`AnalysisConversation.vue` 的 `MAX_FILE_SIZE`），调大本值时须同步前端，否则大文件会在前端被拦下
- `CHAT_DATA_MAX_IMAGE_SIZE_MB`：单张图片进入模型上下文的大小上限（MB，正整数），默认 `5`；同时约束 `read_image` 工具与视觉注入中间件
- `CHAT_DATA_TOOL_CALL_RATE_ALERT_THRESHOLD` / `CHAT_DATA_TOOL_CALL_RATE_ALERT_WINDOW_SECONDS`：单会话工具调用频率告警的阈值与统计窗口，默认 `100` 次 / `60` 秒（合规要求）；窗口内超限写告警日志（`TOOL_CALL_RATE_EXCEEDED`）但不阻断工具执行，同一窗口每个会话最多一条。计数按会话在单个 worker 进程内聚合，多进程并发同一会话时只可能低估
- `CHAT_DATA_CONTEXT_COMPRESSION_ENABLED`：智能问数上下文压缩总开关，仅影响发给大模型的上下文，不影响页面历史、checkpoint 和审计记录
- `CHAT_DATA_CONTEXT_COMPRESSION_TRIGGER_RATIO`：输入达到模型上下文窗口的该比例时自动压缩，默认 `0.70`，必须大于 `0` 且不超过 `0.95`；非法配置会阻止服务启动，实际触发点同时受 `CHAT_DATA_OUTPUT_MAX_TOKENS` 输出预留和 `95%` 输入安全水位约束
- `CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS`：上下文压缩摘要调用的最大输出 token，默认 `2000`；独立于主模型调用的 `CHAT_DATA_OUTPUT_MAX_TOKENS`
- `apache-ossie`：语义模型物料化依赖。Ossie 尚未发布 PyPI 正式版，requirements 使用 `wheels/apache_ossie-0.2.0.dev0-py3-none-any.whl` 离线 wheel（由官方仓库固定 commit `88e0011148283302c9a04cd0287e00e0b9d87354` 的 `python/` 子目录构建），内网机器无需访问 GitHub 即可安装。升级时按 requirements.txt 内注释从新 commit 重建 wheel 替换旧文件；不能使用开发机本地路径或静默降级。
- `numpy` / `onnxruntime`：`markitdown` → `magika` 的传递依赖，同样以 `wheels/` 离线 wheel 提供，因为内网 PyPI 镜像（pypi.nucc.com）下载这两个包会超时。二者版本还受目标机器 CPU 约束：**numpy 固定 1.26.4**，因为 numpy 2.0+ 把 manylinux wheel 基线提升到 `x86_V2`（AVX2），不支持 AVX2 的机器导入即报 `Numpy was built with baseline optimizations: (x86_V2) but the machine doesn't support (x86_V2)`；1.26.4 的 `manylinux_2_17` wheel 仅需 SSE2。`onnxruntime` 固定 1.23.2（与开发机验证过的 `magika 0.6.3` / `markitdown 0.1.7` 组合一致），它只提供 `manylinux_2_28+` 变体，故宿主机 glibc 需 ≥ 2.28。两个 wheel 均为 Linux x86_64 / CPython 3.12，换平台按 requirements.txt 内注释用 `pip download --platform` 重新生成。

## 运行时存储

`CHAT_DATA_HOME` 是应用侧唯一的运行时文件根。local 直接在该目录读写；sandbox 不访问该目录，也不使用宿主机目录挂载，而是通过 OpenSandbox Files API 上传会话快照，并在操作完成后把允许同步的 `workspace/`、`outputs/` 文件下载回应用侧。多应用实例之间的持久化存储和并发协调由部署层负责，代码不实现跨节点文件同步。

会话文件位于 `threads/{user_id}/{创建日期YYYY-MM-DD}/{conversation_id}/user-data/`（按会话创建日期归档，路径统一由 `utils.storage_paths.conversation_thread_dir` 拼装），系统技能位于 `skills/system/`，用户技能位于 `skills/user/{user_id}/<skill-name>/versions/vN/`。每个会话在创建/首次运行时构建技能快照 `threads/{user_id}/{创建日期YYYY-MM-DD}/{conversation_id}/runtime-skills/`（系统技能 + 自己已发布 + 他人 global 已发布），会话内冻结不变。sandbox 运行时通过 Files API 上传该快照到 `/mnt/skills`，并上传 `user-data/` 到 `/mnt/user-data`；技能在沙箱内只读，工具对 `workspace/`、`outputs/` 的变更在操作完成后下载回应用侧。大工具结果统一位于会话 `outputs/.tool-offload/`：查询结果保存为 `<id>.json`，其他工具结果保存为 `<id>.txt`，虚拟路径为 `/mnt/user-data/outputs/.tool-offload/<id>.<suffix>`。该目录可由 Agent 的 `file_read` 分页读取或 Bash 处理，但前端工作区列表、产物展示、技能导入和 `file_edit` 均忽略或拒绝它。系统技能由发布流程将代码包 `apps/chat_data_app/agent/skills/` 下的技能目录复制到 `skills/system/`，应用运行时只读且不写入技能数据库；用户技能继续由数据库元数据和版本目录管理。前端始终携带会话 ID 请求查询结果，后端据此校验当前用户归属并按固定路径读取。生产环境的 LangGraph checkpoint 使用 MySQL。MinIO 不在工作区、技能或查询结果的运行时链路中。

## 权限边界

`data-agent` 按 service 层运行，不直接承担前端菜单和后台角色授权。`/api/chatdata/admin/**` 是否允许访问由上层 controller/BFF 基于登录态和权限点统一拦截，例如 `chatdata:admin:manage`。

`data-agent` 仍然必须校验服务间调用身份和业务数据权限：

- 所有 `/api/chatdata/**` 接口先由 ChatData 根路由统一校验正确的 `X-Service-Token`；该校验不依赖各业务 handler 手动调用认证函数。
- 需要用户归属、审计或下游 HTTP 元数据透传的接口，再解析可信代理转发的 `X-User-Id`、`X-Username`。
- 所有 `/api/chatdata/admin/**` 接口必须解析当前用户；后台角色和权限点由上层 controller/BFF 负责。
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

覆盖率门槛（行覆盖率要求 > 95%，当前实测约 97%；日常单文件调试直接 `pytest <文件>` 不带覆盖率参数）：

```bash
pytest apps/chat_data_app/tests --cov=apps --cov-fail-under=95
```

部分测试依赖 MySQL、LLM 或外部 HTTP 查询服务，需要按 `.env` 配置本地环境。

## 注意事项

- 不要提交真实 `.env`、Token、数据库密码或业务数据。
- `main.py` 只应挂载当前项目需要的智能问数相关路由。
- 已删除的旧业务 app 不应再被 import 或 include_router。
- **SQL 兼容红线：业务库需兼容 MySQL 5.7**（UAT 为 5.7 实例）。新查询禁止使用窗口函数（`row_number() OVER` 等）和 CTE（`WITH` 子句），二者均要求 MySQL 8.0+，在 5.7 上报 1064 语法错误。「每组取前 N 条」类需求参照 `daos/conversation_dao.py::list_thread_group_first_pages` 的写法：`DISTINCT` 查分组清单后逐组 `LIMIT`，应用层归并排序。若部署基线统一升级到 8.0+，可解除该限制。
