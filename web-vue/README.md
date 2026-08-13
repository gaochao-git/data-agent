# Data Agent Web

本目录为 Data Agent 前端应用，基于 Vue 3、Vite、TypeScript 构建。当前前端主要保留智能问数页面与相关组件。

## 目录概览

```text
web-vue/
├── src/router/                      # 前端路由
├── src/layouts/                     # 页面布局
├── src/views/chat-data/             # 智能问数主功能
│   ├── pages/                       # 页面入口
│   ├── components/                  # 查询、指标集、看板等组件
│   ├── api/                         # 后端接口封装
│   ├── composables/                 # 组合式逻辑
│   ├── types/                       # 类型定义
│   └── utils/                       # 工具函数
└── src/views/ComingSoon.vue         # 已移除模块的占位页
```

## 本地启动

```bash
cd web-vue
npm install
npm run dev
```

启动后访问 Vite 输出的地址，通常是：

```text
http://localhost:5173/chatdata
```

## 环境配置

本地配置文件为 `web-vue/.env`，示例见 `web-vue/.env.example`。

常用配置：

- `VITE_API_BASE_URL`：后端 API 基础地址
- `AGENT_SERVICE_TOKEN`：本地服务调用 Token
- `AGENT_USER_ID`：本地模拟用户 ID
- `AGENT_USERNAME`：本地模拟用户名，按现有约定使用 Base64 编码

## 构建与检查

```bash
# 仅执行 Vite 打包
npm run build-only

# 完整构建，包含 vue-tsc 类型检查
npm run build
```

说明：`npm run build` 会同时执行 `vue-tsc` 类型检查与 Vite 打包，当前可完整通过；仅需快速验证打包时可用 `npm run build-only`。

## 路由说明

当前核心页面：

- `/chatdata`：智能问数主页面
- `/test-dialog`：智能问数弹窗测试页

已删除的旧业务页面会临时指向 `ComingSoon.vue`，避免路由扫描不存在文件。

## 注意事项

- 不要提交 `web-vue/.env` 或其他本地密钥文件。
- 删除旧业务页面时，需要同步检查 `src/router/index.ts` 是否仍引用对应路径。
- 新增智能问数页面或组件时，优先放在 `src/views/chat-data/` 下。
