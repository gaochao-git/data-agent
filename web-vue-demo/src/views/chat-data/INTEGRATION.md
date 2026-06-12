# ChatData 智能问数 - 集成文档

## 概述

ChatData（智能问数）是一个 Vue 3 功能模块，提供面向语义数据集的自然语言问数能力。模块包含四类核心能力：智能问数、辅助查询（可视化构建）、SQL 查询（代码编辑器）和数据看板。

本模块基于 **RuoYi-Vue3** 前端框架规范开发，可直接集成到 RuoYi 项目中。

### 环境要求

- Vue 3.5+
- TypeScript
- Vite 7+
- Element Plus
- RuoYi-Vue3 前端框架

### 后端服务

| 服务 | 默认地址 | 说明 |
|------|---------|------|
| AI Agent 后端 | `http://127.0.0.1:5001` | LangGraph + FastAPI，处理语义问数、会话管理、SQL 辅助生成、看板 |
| RuoYi 业务后端 | `http://127.0.0.1:8080` | Spring Boot，用户鉴权等 |

前端所有请求都带 `VITE_APP_BASE_API` 前缀，通过代理转发：
- 开发环境：`/dev-api`（`.env.development`）
- 生产环境：`/prod-api`（`.env.production`）

以 `/dev-api` 为例：
- `/dev-api/agents/*` → AI Agent 后端（5001）
- `/dev-api/*` → RuoYi 业务后端（8080）

---

## 一、复制文件

### 1.1 复制模块目录

将以下目录复制到主项目 `src/` 下（建议保持 `views/chat-data` 路径，减少改动）：

```
主项目/src/
├── views/
│   └── chat-data/          <-- 复制 chat-data/ 目录
│       ├── api/
│       ├── assets/
│       ├── components/
│       ├── composables/
│       ├── styles/
│       ├── types/
│       ├── utils/          # 包含 auth.js、mapRegistry.ts、parseContent.ts、sqlFormatter.ts
│       └── views/
└── utils/                  <-- 合并到主项目已有的 utils/ 目录
    ├── auth.js             # 覆盖或确认一致（js-cookie + Admin-Token）
    ├── request.js          # 覆盖或确认一致（RuoYi 风格拦截器）
    ├── requestStream.js    # 新增（SSE 流式请求）
    ├── errorCode.js        # 确认一致
    └── ruoyi.js            # 确认一致（tansParams）
```

### 1.2 安装额外依赖

主项目已有的依赖（element-plus、axios、js-cookie 等）跳过，只装缺少的：

```bash
npm install echarts@5.6 codemirror @codemirror/lang-sql @codemirror/state @codemirror/view vue-codemirror sql-formatter markdown-it grid-layout-plus @fortawesome/fontawesome-free && npm install -D @types/markdown-it
```

### 1.3 引入 Tailwind CSS

安装依赖：

```bash
npm install tailwindcss @tailwindcss/postcss autoprefixer
```

#### 配置 PostCSS 插件

在 `vite.config.js` 顶部添加 import：

```js
import tailwindcss from '@tailwindcss/postcss'
import autoprefixer from 'autoprefixer'
```

在 `css.postcss.plugins` 数组中添加（保留主项目已有的插件）：

```js
css: {
  postcss: {
    plugins: [
      tailwindcss(),
      autoprefixer(),
      // 主项目已有的插件保留在下方，例如：
      // { postcssPlugin: 'internal:charset-removal', ... }
    ],
  },
},
```

#### 引入 CSS

> **重要**：Tailwind 的 `@import` **不能放在 `.scss` 文件中**，因为 Sass 会尝试将其作为 Sass 模块解析而报错。必须放在独立的 `.css` 文件中。

**第一步**：新建 `src/assets/styles/tailwind.css`（注意是 `.css` 不是 `.scss`）：

```css
@import "tailwindcss/theme";
@import "tailwindcss/utilities";
```

> **说明**：只引入 theme（主题变量）和 utilities（工具类），**不引入 preflight**（CSS 重置），避免影响 Element Plus 的样式。模块内通过 `:where(.chatdata-root) button` 局部重置按钮默认样式（已内置在 `views/index.vue` 中），无需额外配置。

**第二步**：在 `main.ts`（或 `main.js`）中引入（放在现有 SCSS 样式之前）：

```js
import '@/assets/styles/tailwind.css'   // Tailwind（必须是 .css 文件）
import '@/assets/styles/index.scss'     // 主项目已有的 SCSS 样式
```

**注意事项**：

```css
/* ❌ 错误：不要放在 .scss 文件中，Sass 无法解析 tailwindcss 模块 */
@import "tailwindcss/theme";

/* ❌ 错误：不要用完整引入，preflight 会破坏 Element Plus 样式 */
@import "tailwindcss";
```

### 1.4 引入图标样式

在 `main.ts` 中添加（如果未引入）：

```js
import '@fortawesome/fontawesome-free/css/all.min.css'
```

---

## 二、路由注册

在主项目 `src/router/index.ts`（或 `index.js`）中添加路由：

```js
{
  path: '/tools/collect_query',
  component: Layout,
  hidden: true,
  permissions: ['rule:info:list'],  // 按实际权限标识调整
  children: [
    {
      path: '',
      component: () => import('@/views/chat-data/views/index.vue'),
      name: 'ChatData',
      meta: { title: '智能问数'}
    }
  ]
}
```

> 路径和权限标识根据实际菜单配置调整。

---

## 三、认证说明

### Token（主项目已有）

业务请求使用 `@/utils/auth.js`（通过 `@/utils/request.js` 注入 Authorization），与主项目共享同一个 `Admin-Token` cookie：

```ts
import Cookies from 'js-cookie'
const TokenKey = 'Admin-Token'
export function getToken() { return Cookies.get(TokenKey) }
```

用户在主项目登录后，cookie 中已有 `Admin-Token`，模块的请求会自动携带。

### inner_token（AI Agent 后端）

模块入口 `views/index.vue` 在挂载时会调用 `initChatDataAuth()`（定义在 `src/views/chat-data/utils/auth.js`），自动请求 `/agents/api/inner_login` 获取一个 httpOnly 的 `inner_token` cookie，用于 AI Agent 后端的会话接口认证。

这两套认证并行，互不冲突：
- `Admin-Token` → 主项目 Spring Boot 后端
- `inner_token` → AI Agent FastAPI 后端

---

## 四、组件嵌入（弹窗/悬浮窗）

除路由整页接入外，也可直接在任意页面嵌入：

```vue
<script setup lang="ts">
import { ref } from 'vue'
import { ChatDataDialog, ChatDataFloat } from '@/views/chat-data'

const showDialog = ref(false)
const floatRef = ref<InstanceType<typeof ChatDataFloat> | null>(null)

const initialSql = `SELECT * FROM complaint_orders LIMIT 20;`
const savedSql = ref(initialSql)
const savedBuilderConfig = ref<Record<string, unknown> | null>(null)

const handleSqlChange = (payload: { mode: "editor" | "builder"; sql: string; builderConfig?: Record<string, unknown> }) => {
  if (payload.mode === 'builder') {
    // 可视化保存：同时保存 SQL + 可视化配置
    savedSql.value = payload.sql
    savedBuilderConfig.value = payload.builderConfig || null
  } else {
    // SQL 保存：只保存 SQL，并清空可视化配置
    savedSql.value = payload.sql
    savedBuilderConfig.value = null
  }
  console.log('保存后的 SQL 载荷:', payload)
}
</script>

<template>
  <el-button @click="showDialog = true">打开弹窗</el-button>
  <el-button @click="floatRef?.open()">打开悬浮窗</el-button>

  <ChatDataDialog
    v-model:visible="showDialog"
    :initial-width="1200"
    :initial-height="760"
    :fullscreen="true"
    :options="{
      parentComponentName: '投诉举报',
      defaultMode: 'builder',
      hideTabs: ['dashboard']
    }"
  />

  <ChatDataFloat
    ref="floatRef"
    :show-fab="false"
    :fullscreen="false"
    :initial-width="1100"
    :initial-height="700"
    :options="{
      parentComponentName: '执法检查',
      defaultMode: 'editor',
      initialSql: savedSql,
      initialBuilderConfig: savedBuilderConfig || undefined,
      onSqlChange: handleSqlChange,
      hideBuilderChartPanel: false,
      hideTabs: ['dashboard']
    }"
  />
</template>
```

`ChatDataDialog` 组件额外参数：

| 参数 | 说明 |
|------|------|
| `initialWidth` | 弹窗初始宽度，默认 `90%`；支持数字或 CSS 尺寸字符串 |
| `initialHeight` | 弹窗初始高度，默认 `80vh`；支持数字或 CSS 尺寸字符串 |
| `fullscreen` | 是否默认全屏打开，默认 `false`；传 `true` 时父组件打开弹窗即全屏 |

`ChatDataFloat` 组件额外参数：

| 参数 | 说明 |
|------|------|
| `showFab` | 是否显示内置悬浮按钮，默认 `true`；传 `false` 时通常配合 `ref.open()` 或 `v-model:visible` 使用 |
| `fullscreen` | 是否默认最大化打开，默认 `false` |
| `initialWidth` | 悬浮窗初始宽度，默认 `1100` |
| `initialHeight` | 悬浮窗初始高度，默认 `700` |

`options` 常用参数：

| 参数 | 说明 |
|------|------|
| `parentComponentName` | 调用方模块名，用于按模块映射过滤数据源；未传时页面会进入无数据源提示态 |
| `defaultMode` | 默认页签：`conversation` / `builder` / `editor` / `dashboard` |
| `defaultDashboardName` | 默认打开的看板名称（仅 `dashboard` 模式生效，不传默认第一个） |
| `modeLabels` | 自定义四个主模式名称（必须一次性传全：`conversation/builder/editor/dashboard`）；不传则默认 `数据智能体/可视化查询/SQL查询/数据看板` |
| `hideTabs` | 隐藏指定主模式 Tab，支持传模式 key 或当前展示名称（支持与 `modeLabels` 联动），例如 `['dashboard']` 或 `['数据看板']` |
| `hideBuilderChartPanel` | 隐藏可视化中部“图表/分析”面板，默认 `false` |
| `initialSql` | SQL 查询页初始化 SQL（`defaultMode: 'editor'` 时常用） |
| `initialBuilderConfig` | 可视化查询初始化配置（`defaultMode: 'builder'` 时用于恢复编排） |
| `onSqlChange` | SQL 查询/可视化查询点击“保存SQL”后回调，回传 `{ mode, sql, builderConfig? }` |

`parentComponentName` 也支持通过 URL query 传入，适合独立路由直开场景：

- 示例：`/chatdata?parentComponentName=举报管理`
- 优先级：`options.parentComponentName` 高于 URL query
- 若 `options.parentComponentName` 和 URL query 都未传，当前页面不会展示任何数据源，只会显示无数据源提示；如有权限，可在页面内点击“配置数据源”进入管理弹窗

### modeLabels / hideTabs 联动案例

```vue
<ChatDataDialog
  v-model:visible="showDialog"
  :options="{
    parentComponentName: 'SQL规则',
    defaultMode: 'dashboard',
    defaultDashboardName: '某电商人力资源分析',
    modeLabels: {
      conversation: '数据智能体',
      builder: '可视化',
      editor: 'SQL',
      dashboard: '数据看板'
    },
    hideTabs: ['数据看板']
  }"
/>
```

说明：
- `modeLabels` 需要四个一起传，缺任何一个会回退默认名称。
- `hideTabs` 可传 key（如 `dashboard`）或当前展示名称（如 `数据看板`）。

---

## 五、Vite Proxy（本地开发）

主项目 `vite.config.js` 的 `server.proxy` 中已有 `/dev-api` 代理，需要在它**前面**添加 AI Agent 后端代理：

```js
proxy: {
  // AI Agent 后端（写在 /dev-api 前面，优先匹配）
  '/dev-api/agents': {
    target: 'http://127.0.0.1:5001',
    changeOrigin: true,
    // 外网：直连 Agent 后端，去掉 /dev-api/agents 前缀
    rewrite: (path) => path.replace(/^\/dev-api\/agents/, ''),
    // 内网：通过 Nginx 转发，只去掉 /dev-api，保留 /agents（Nginx 会处理）
    // rewrite: (path) => path.replace(/^\/dev-api/, ''),
  },
  // 主项目已有的业务后端代理保持不变
  // '/dev-api': { target: baseUrl, changeOrigin: true, rewrite: ... }
}
```

> 注意：主项目开发环境使用 `/dev-api` 前缀（`VITE_APP_BASE_API = '/dev-api'`），生产环境使用 `/prod-api`。代理规则中的前缀需与当前环境一致。根据网络环境切换 rewrite 规则。

---

## 六、Nginx 配置（生产部署）

在 Nginx 中添加 AI Agent 后端代理（写在 `/prod-api/` 规则前面）：

```nginx
# AI Agent 后端
location /prod-api/agents/ {
    proxy_pass http://127.0.0.1:5001/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    # SSE 流式响应必须关闭缓冲
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

注意事项：
1. `/prod-api/agents/` 必须写在 `/prod-api/` 前面，否则会被业务后端匹配
2. `proxy_buffering off` 是 SSE 流式接口必须的
3. `proxy_read_timeout 300s` 防止长时间查询被超时断开

---

## 七、文件清单

### `src/utils/`（通用工具，与主项目共享）

| 文件 | 说明 | 主项目是否已有 |
|------|------|--------------|
| `auth.js` | Token 管理（Admin-Token） | 已有 auth.js，确认一致即可 |
| `request.js` | axios 封装（RuoYi 风格） | 已有 request.js，确认一致即可 |
| `requestStream.js` | SSE 流式请求 | **新增** |
| `errorCode.js` | 错误码映射 | 已有，确认一致即可 |
| `ruoyi.js` | tansParams 等工具 | 已有 ruoyi.js，确认一致即可 |

### `src/views/chat-data/`（业务模块）

| 目录 | 说明 |
|------|------|
| `api/` | 接口定义（数据源、会话、看板） |
| `assets/` | 图片资源（按钮 SVG、看板背景、地图 JSON） |
| `components/` | 业务组件（会话、编辑器、看板、数据源管理等） |
| `composables/` | Vue 组合式函数 |
| `styles/` | 样式文件 |
| `types/` | TypeScript 类型定义 |
| `utils/` | 业务工具（模块认证、SQL 格式化、地图注册、内容解析） |
| `views/index.vue` | 模块入口页面 |

---
