# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**注意：现在是 2025 年，langchain/langgraph 使用 1.x 版本。涉及 langchain/langgraph/deepagents 的功能请参考官方文档，避免使用过时 API：**
- LangChain: https://docs.langchain.com/oss/python/langchain/overview
- LangGraph: https://docs.langchain.com/oss/python/langgraph/overview
- DeepAgents: https://docs.langchain.com/oss/python/deepagents/overview
- Agent Skills (定义技能格式): https://agentskills.io/
- Agent Skills for Context Engineering（上下文工程技能）: https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering

**重要：除非用户明确要求，否则不要自动启动前后端服务。用户会手动管理服务的启停。**

## 项目结构

```
fxcjg/
├── server/          # 后端服务 (FastAPI + LangGraph)
├── web/             # 前端应用 (React + TypeScript + Vite)
├── client/          # 客户端
└── scripts/         # 脚本工具
```

## 后端 (server/)

### 常用命令

```bash
cd server

# 激活 conda 虚拟环境
conda activate fxcjg
# 或直接使用: /Users/gaochao/work/miniconda3/envs/fxcjg/bin/python

# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python main.py

# 运行测试
pytest
```

### 架构概述

FastAPI 后端服务，提供多个 AI Agent 应用，使用 LangGraph 进行工作流编排，SQLAlchemy 管理数据持久化。

**apps/** - 各业务应用模块
- `chat_data_app`: 自然语言到 SQL 转换（使用 LangGraph）
- `general_app`: 通用对话助手（使用 LangGraph）
- `inspection_app`: 执法检查报告生成（使用 LangGraph，支持 Human-in-the-Loop）
- `specialist_app`: 法律专家系统（不使用 LangGraph，有独立消息存储）
- `complaint_app`: 投诉举报分析（不使用 LangGraph，有独立消息存储）

**utils/lang_util/** - LangGraph 公共工具
- `checkpoint.py`: Checkpointer 配置（支持 SQLite/MySQL）
- `database.py`: SQLAlchemy 引擎和会话管理
- `models.py`: 数据模型（UserThread, UploadedFile）
- `conversation.py`: 会话 CRUD 操作
- `conversation_router.py`: 统一会话 API 路由
- `llm.py`: LLM 实例获取

### Agent 注册模式

每个使用 LangGraph 的应用在 `__init__.py` 中注册：

```python
from utils.lang_util.conversation_router import register_agent, register_completion_handler

AGENT_ID = "your_agent"
register_agent(agent_id=AGENT_ID, get_graph=get_graph)
register_completion_handler(AGENT_ID, your_completion_handler)
```

不使用 LangGraph 的应用（specialist_app, complaint_app）只注册 agent_id，使用各自独立的消息存储。

### 数据库配置

通过环境变量 `CHECKPOINTER_TYPE` 切换数据库后端：
- `sqlite`（默认）: 使用 `DATA_ROOT/checkpoints.db`，自动创建表
- `mysql`: 需配置 `CHECKPOINTER_HOST/PORT/DB/USER/PASSWORD`，手动创建表

### LangGraph 工作流

使用 LangGraph 的应用遵循以下结构：
- `agent/state.py`: 定义 State 类型
- `agent/nodes.py`: 定义节点函数
- `agent/graph.py`: 构建 StateGraph，导出 `get_graph(checkpointer)`
- `handlers.py`: SSE 流式响应处理

### Human-in-the-Loop

`inspection_app` 支持人工审核，使用 `interrupt()` 暂停工作流，通过 `Command(resume=...)` 恢复。

### 环境变量

关键配置项：
- `CHECKPOINTER_TYPE`: sqlite | mysql
- `DATA_ROOT`: 数据存储根目录
- `OPENAI_API_KEY`: OpenAI API 密钥
- `OPENAI_BASE_URL`: OpenAI API 地址
- `ALLOWED_ORIGINS`: CORS 允许的来源
- `PORT`: 服务端口（默认 5001）

## 前端 (web/)

### 常用命令

```bash
cd web

# 安装依赖
pnpm install

# 启动开发服务器
pnpm run dev

# 构建生产版本
pnpm run build

# 运行测试
pnpm run test
```

### 技术栈

- **React 18**: UI 框架
- **TypeScript**: 类型安全
- **Vite**: 构建工具
- **Tailwind CSS**: 样式框架
- **Framer Motion**: 动画库
- **Recharts**: 图表库

### 路由配置

前端默认使用 `/ai` 作为 base path，访问地址为 `http://127.0.0.1:3000/ai/`。

在 `vite.config.ts` 中配置：
- 默认 base path: `/ai`（可通过 `VITE_BASE_PATH` 覆盖）
- API 代理: `/ai/api/*` → 后端服务（自动去除 `/ai` 前缀）

### 环境变量

在 `web/.env` 中配置：
- `VITE_API_BASE_URL`: 后端 API 地址（如 `http://127.0.0.1:5001`）
- `VITE_BASE_PATH`: 前端 base path（默认 `/ai`）
- `VITE_MAX_FILE_SIZE_MB`: 文件大小限制（可选）

## 系统依赖

macOS:
```bash
brew install pandoc poppler
```

Ubuntu/Debian:
```bash
sudo apt-get install -y pandoc poppler-utils
```
