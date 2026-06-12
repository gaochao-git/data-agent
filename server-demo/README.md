# Server (FastAPI)

本目录为后端服务代码，入口为 FastAPI（`server/main.py`）。负责文件上传、投诉分析、对话流式输出、NL2SQL、法规检索等能力的统一接口。

## 1. 目录概览

```
server/
├── main.py                 # FastAPI 入口
├── start.sh                # 本地启动脚本（Conda 环境）
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量示例
├── routes/                 # 通用路由（chat 等）
├── utils/                  # 公共工具（存储、日志、LLM、文件服务等）
├── apps/                   # 业务模块
│   ├── complaint_app/      # 投诉处理与分析
│   ├── complaint_tools/    # 投诉分析工具链
│   ├── word_fit_app/       # 文件字段抽取
│   ├── chat_assistant/     # 对话助手（Dify chat-messages）
│   ├── chat_data_app/      # NL2SQL
│   ├── inspection_app/     # 执法检查相关
│   ├── specialist_app/     # 专家系统
│   └── ...
└── <DATA_ROOT>/            # 数据落盘目录（必须配置）
```

## 2. 本地运行

### 2.1 安装依赖

系统依赖（必须）：\n
- `pandoc`：doc/docx 转 Markdown\n
- `poppler`：PDF 转图片（`pdftoppm`/`pdftocairo`）\n

示例安装：\n

```bash
# macOS
brew install pandoc poppler
brew install ripgrep

# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y pandoc poppler-utils
sudo yum install -y epel-release
sudo yum install -y ripgrep
```

建议使用虚拟环境：

```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2.2 环境变量

复制并修改示例配置：

```bash
cp .env.example .env
```

必要配置（至少）:
- `DIFY_BASE_URL`
- `DIFY_API_KEY` / `DIFY_*_API_KEY`
- `OPENAI_API_KEY`（如使用 LLM/OCR）

### 2.3 启动服务

方式一（直接运行）：

```bash
python main.py
```

默认端口：`5001`（可通过 `PORT` 修改）。

方式二（uvicorn）：

```bash
uvicorn main:app --host 0.0.0.0 --port 5001
```

方式三（脚本启动）：

```bash
./start.sh
```

> 注：`start.sh` 使用 Conda 环境，并设置了 FLASK 相关变量，但最终仍执行 `python main.py`。

## 3. 关键环境变量

配置集中在 `server/.env.example`，常用如下：

### 3.1 通用
- `DATA_ROOT`：数据根目录（必须配置绝对路径）
- `ALLOWED_ORIGINS`：CORS 允许域名
- `LOG_LEVEL` / `LOG_DIR` / `LOG_FILE`

### 3.2 Dify / OCR / LLM
- `DIFY_BASE_URL`
- `DIFY_API_KEY` / `DIFY_INFER_API_KEY` / `DIFY_CHAT_ASSIST_API_KEY`
- `OPENAI_API_KEY` / `OPENAI_BASE_URL`
- `OCR_LLM_MODEL` / `OCR_LLM_TIMEOUT`

### 3.3 业务并发与限制
- `MAX_UPLOAD_SIZE_MB`：上传大小限制
- `OCR_MAX_CONCURRENCY`：OCR 并发
- `TASK3_MAX_CONCURRENCY`：深度分析并发

### 3.4 认证与安全
- `JWT_SECRET_KEY` / `JWT_COOKIE_*`
- `DEBUG` / `AI_TRACE`

## 4. 主要接口（摘要）

- `/api/inner_login`：内部登录（JWT cookie）
- `/api/chat/*`：投诉与专家对话，SSE 流式输出（见 `server/routes/chat_routes.py`）
- `/api/complaint/complaint-analysis-uploaded/*`：投诉上传文件管理
- `/api/chat-assistant/ask`：Dify 对话助手（SSE）
- `/api/word-fit/extract`：文件字段抽取（SSE）
- `/api/uploaded-files/*`：通用文件上传/查询

更多模块接口请查看对应模块 README：
- `server/apps/complaint_app/README.md`
- `server/apps/word_fit_app/README.md`
- `server/apps/chat_assistant/README.md`
- `server/apps/complaint_tools/README.md`
- `server/apps/chat_data_app/README.md`

## 5. 数据与落盘

- 默认根目录：`DATA_ROOT`（例如：`/data/fxcjg`）
- 结构化数据与中间文件按模块写入子目录（见 `utils/storage_paths.py`）

## 6. 测试

```bash
cd server
pytest
```

部分测试依赖外部服务（Dify/LLM/数据库），需配置 `.env`。

## 7. 系统依赖

某些模块依赖系统工具：
- `pandoc`：doc/docx 转 Markdown
- `poppler`：PDF 转图片（`pdftoppm`/`pdftocairo`）

未安装会导致文档解析失败。

## 8. 说明

- 不要将真实密钥写入仓库。
- 生产环境请收敛 `ALLOWED_ORIGINS`，并关闭 `DEBUG`。
