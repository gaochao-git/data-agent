# chat_assistant

## 1. 模块定位
轻量对话助手模块，基于 Dify 的 `chat-messages` 接口提供流式聊天能力，供前端或其他服务调用。

## 2. 主要功能
- 将问题转发至 Dify 并以 SSE 流式返回。
- 支持传入 `conversation_id` 继续对话。
- 支持透传 `inputs` 作为 Dify 变量输入。

## 3. 入口与调用

### 3.1 API
- `POST /api/chat-assistant/ask`
  - Body: `{ question: string, conversation_id?: string, inputs?: object, user?: string }`
  - 返回：`text/event-stream`

### 3.2 实现文件
- 路由：`routes.py`
- 请求结构：`schema.py`
- Dify 调用：`service.py`

## 4. 配置项（环境变量）
- `DIFY_BASE_URL`：Dify 基础地址（会自动拼接 `/chat-messages`）
- `DIFY_CHAT_ASSIST_API_KEY`（优先）或 `DIFY_API_KEY`
- `DIFY_CHAT_DEFAULT_USER`：未传 user 时使用的默认值
- `CHAT_TIMEOUT_SEC`：请求超时（默认 600）

## 5. 说明
- 若未配置 `DIFY_*` 的 API Key 或 URL，将直接返回错误提示。
- 返回数据保持 Dify SSE 格式（`data:` 行）。

