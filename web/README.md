# Web (Frontend)

本目录为前端应用，基于 Vite + React + TypeScript。提供统一的对话界面、投诉/专家/执法检查场景入口、NL2SQL 页面与管理页面。

## 1. 目录概览

```
web/
├── src/                 # 业务源码
├── public/              # 静态资源
├── index.html           # 入口 HTML
├── vite.config.ts       # Vite 配置
├── tailwind.config.js   # Tailwind 配置
├── postcss.config.js    # PostCSS 配置
└── tests/               # E2E 测试
```

## 2. 本地运行

### 2.1 安装依赖

```bash
cd web
npm install
```

> 如使用 pnpm，请确保与锁文件匹配。

### 2.2 环境变量

```bash
cp .env.example .env
```

常用配置：
- `VITE_API_BASE_URL`：后端地址（默认 `http://127.0.0.1:5000`）
- `VITE_MAX_FILE_SIZE_MB`（可选）：前端文件大小校验

### 2.3 启动开发服务

```bash
npm run dev
```

默认端口：`3000`。

## 3. 构建与测试

```bash
npm run build
npm run test:e2e
```

构建产物位于 `web/dist/`。

## 4. 路由入口（摘要）

前端主要页面入口：
- `/`：通用对话（GeneralAssistant）
- `/specialist`：专家系统入口
- `/complaint/:conversationId?`：投诉场景
- `/expert/:conversationId?`：专家场景
- `/nl2sql/:conversationId?`：智能问数
- `/admin/complaint-analysis-uploaded`：投诉上传管理

完整路由定义见 `src/App.tsx`。

## 5. 组件与页面说明

- 通用对话页面说明：`src/pages/GeneralAssistant/README.md`
- NL2SQL 页面：`src/pages/ChatData`
- 管理页面：`src/pages/AdminComplaintAnalysisUploaded*`

