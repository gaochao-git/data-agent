# Agents Overview

本项目的“智能体（Agents）”主要指向两类对接 Dify 平台能力的功能模块：OCR 识别与文本抽取。这些模块并非长期驻留的多步代理，而是围绕 API 的“调用型 Agent”。本文档描述其职责、配置方式与运行约束，便于开发与运维。

## 架构与职责

- OCR Agent（server/ocr.py）
  - 负责：将图片上传至 Dify 文件接口，随后调用对话接口完成 OCR 文本抽取。
  - 接口：
    - 上传：`POST {DIFY_BASE_URL}/files/upload`
    - 识别：`POST {DIFY_BASE_URL}/chat-messages`
  - 关键点：网络失败或超时内置 3 次重试（指数退避）；上传阶段默认 30s 超时（必要时可在代码中调高），识别阶段 600s 超时以覆盖长时间模型推理。

- Inference Agent（server/inference.py）
  - 负责：对完整文本执行举报事项抽取（结构化结果）。
  - 接口：`POST {DIFY_API_URL}`（优先从 `DIFY_INFER_API_URL`/`DIFY_API_URL` 读取，回退至 `{DIFY_BASE_URL}/chat-messages`）。
  - 关键点：同样内置 3 次重试与 600s 超时；优先尝试将 `answer` 解析为 JSON 数组。

## 环境变量

- 通用
  - `DIFY_BASE_URL`：Dify 基础地址（默认 `https://localhost/v1`）。
  - `ALLOWED_ORIGINS`：后端允许的前端域名（逗号分隔）。

- OCR Agent
  - `DIFY_OCR_API_KEY`：OCR 专用 API Key（优先）。
  - 回退：`DIFY_API_KEY` 若未设置 OCR 专用 Key。
  - 可选：`DIFY_OCR_BASE_URL`，未设置时回退 `DIFY_BASE_URL`。

- Inference Agent
  - `DIFY_INFER_API_KEY`：抽取专用 API Key（优先）。
  - 回退：`DIFY_API_KEY`。
  - `DIFY_INFER_API_URL` 或 `DIFY_API_URL`：抽取使用的 API 路径，未设置回退 `{DIFY_BASE_URL}/chat-messages`。

- 前端
  - `VITE_API_BASE_URL`：前端调用后端的基地址（示例参见 `web/.env.example`）。

## 安全与合规

- 切勿将真实密钥写入代码库；本仓库提供 `.env.example` 与示例 README。
- CORS：默认代码对 `/upload` 与 `/upload_sse` 放宽为 `origins="*"`，部署时可按需收敛并结合 `ALLOWED_ORIGINS`。
- 上传校验：后端对扩展名、MIME 与文件结构（PDF 魔数、图片格式）进行校验，并限制 10MB；超限返回 413。

## 调试建议

- 本地联调：
  - 后端：`python server/app.py`（或 `server/start.sh`）
  - 前端：`pnpm -C web dev`，浏览器访问 `http://localhost:3000`
  - 确保：`VITE_API_BASE_URL=http://127.0.0.1:5000`，后端 `ALLOWED_ORIGINS` 包含前端域名。

- 常见问题：
  - PDF 转图片失败：检查系统是否安装 `poppler`。
  - 超时或网络错误：Dify 接口默认有重试与长超时，网络抖动可自动恢复；持续失败请检查密钥/URL。
