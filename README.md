---
base_model:
- ""
frameworks:
- other
tasks: []
---

# Data Agent

Data Agent 是面向数据查询和分析场景的智能问数项目，包含后端 FastAPI 服务与前端 Vue 应用。

## 项目结构

```text
data-agent/
├── server/      # 后端服务，FastAPI 入口为 server/main.py
├── web-vue/     # 前端应用，Vue 3 + Vite
└── docs/        # 项目文档与历史说明
```

## 核心能力

- 数据源管理与表结构同步
- 指标集、维度、字段字典配置
- 自然语言智能问数
- 语义 DSL 转 SQL
- SQL 执行、结果展示与历史会话
- 智能分析与查询结果 artifact 管理
- 看板配置与面板查询

## 本地启动

### 后端

```bash
cd server
python main.py
```

默认端口为 `5001`，可通过 `PORT` 环境变量修改。

### 前端

```bash
cd web-vue
npm install
npm run dev
```

默认访问地址以 Vite 输出为准，通常为 `http://localhost:5173`。

## 常用检查

```bash
# 后端测试
cd server
pytest apps/chat_data_app/tests

# 前端构建
cd web-vue
npm run build-only
```

说明：当前前端完整 `npm run build` 会执行 `vue-tsc`，可能暴露历史 TypeScript 类型问题；仅验证 Vite 打包可使用 `npm run build-only`。

## 远程仓库

当前独立仓库使用 ModelScope：

```text
https://www.modelscope.cn/dzkk666/data-agent.git
```

当前约定只使用 `master` 分支。

## 安全说明

- 不要提交真实 `.env`、密钥、Token、运行日志和上传文件。
- `server/conf/.env-*`、`web-vue/.env` 等本地环境文件已在 `.gitignore` 中忽略。
- 如曾在命令行或对话中暴露 ModelScope Token，建议及时在 ModelScope 账号设置中重置。
