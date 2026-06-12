# word_fit_app

## 1. 模块定位
面向“文件字段抽取”的通用模块。接收文件标识与字段定义，完成文件获取、内容解析与 LLM 抽取，结果以 SSE 流式返回。

## 2. 主要功能
- 根据 `file_id` 下载或定位文件。
- 按文件类型选择解析路径：
  - 图片/PDF → OCR 识别
  - doc/docx/wps → `pandoc` 转 Markdown
  - xls/xlsx → `openpyxl` 读取
  - 其他文本 → 直接读取
- 基于字段定义进行 JSON 抽取。
- SSE 输出节点进度与最终结构化结果。

## 3. 入口与调用

### 3.1 API
- `POST /api/word-fit/extract`
  - Body 参数：
    - `file_id: string`
      - 含义：待抽取文件的文件标识。
      - 是否必填：是。
    - `extract_fields: Array`
      - 含义：字段定义列表，供模型按字段输出 JSON。
      - 是否必填：是。
      - 典型元素：
        - `msgkey: string`：字段键名。
        - `description: string`：字段含义说明。
    - `conversation_id?: string`
      - 含义：会话 ID，用于串联一次或多次抽取请求。
      - 默认值：为空时由服务端自动生成 UUID。
    - `locate_enabled?: boolean`
      - 含义：是否在抽取后对 PDF 做字段定位。
      - 默认值：`false`。
    - `analysis_scene?: string`
      - 含义：可选的分析场景标记；非空时可触发额外业务预热逻辑。
      - 默认值：`null`。
    - `analysis_user_id?: string`
      - 含义：配合 `analysis_scene` 使用的业务用户标识。
      - 默认值：`null`。
    - `analysis_match_strategy?: string`
      - 含义：配合 `analysis_scene` 使用的匹配策略，例如 `two_stage`、`legacy`。
      - 默认值：`null`。
  - 返回：`text/event-stream`

  - 最小请求示例：
    ```json
    {
      "file_id": "xxx.pdf",
      "extract_fields": [
        {
          "msgkey": "reporter",
          "description": "举报人姓名"
        }
      ]
    }
    ```

  - 带可选参数的请求示例：
    ```json
    {
      "file_id": "xxx.pdf",
      "extract_fields": [
        {
          "msgkey": "reporter",
          "description": "举报人姓名"
        }
      ],
      "conversation_id": "custom-conversation-id",
      "locate_enabled": false,
      "analysis_scene": "complaint",
      "analysis_user_id": "api_user",
      "analysis_match_strategy": "two_stage"
    }
    ```

### 3.2 关键逻辑
- 路由：`routes.py`
- 处理器：`handlers.py`（SSE 事件输出）
- 工作流图：`agent/graph.py`
- 节点实现：`agent/nodes.py`

## 4. 工作流（简化）
1. `fetch_file`：从外部下载文件到 `uploads/`。
2. `resolve_file`：确认本地文件路径（含 DB/文件系统兜底）。
3. `ocr_node` / `doc_node` / `read_text_node`：按文件类型解析文本。
4. `extract_info`：调用 LLM 输出 JSON 结果。
5. `locate_info`：按需定位提取信息在 PDF 中的位置。

SSE 事件类型：
- `node`：节点开始/结束
- `llm_chunk`：LLM 流式输出片段
- `result`：抽取结果（JSON 或字符串）

## 5. 配置项（环境变量）

### 5.1 文件下载
- `FILE_DOWNLOAD_API_URL`（默认 `http://localhost:8000/download`）
- `FILE_DOWNLOAD_USER`
- `FILE_DOWNLOAD_TOKEN`
- `FILE_DOWNLOAD_CLUSTER`
- `WORDFIT_DOWNLOAD_DIR`（可选，WordFit 下载文件保存目录；未配置时使用 `server/uploads` 或 `uploads`）

### 5.2 LLM / OCR
- `OPENAI_API_KEY` / `OPENAI_BASE_URL`
- `OPENAI_ANALYSIS_MODEL`（`utils/lang_util/llm.py`）
- `LLM_PROVIDER`
- `WORDFIT_OCR_MAX_WAIT_SECONDS`（可选，单张图片/单页 OCR 最长等待秒数；超时后返回已流出的部分文本）
- `OCR_LLM_MODEL` / `OCR_LLM_TIMEOUT` / `OCR_LLM_TEMPERATURE` / `OCR_LLM_TOP_P`
- `OCR_CACHE_DIR` / `OCR_TEXT_FALLBACK_DIR`

## 6. 依赖与注意事项
- `httpx` + `aiofiles`：文件下载
- `PyMuPDF(fitz)`：PDF 转图片
- `openpyxl`：Excel 读取
- `pandoc`：doc/docx 转 Markdown
- `diskcache`：OCR 缓存

## 7. 耗时日志
- `POST /api/word-fit/extract` 会输出统一格式的总耗时日志：`[timing] app=word_fit_app kind=route ...`
- 同时会按工作流节点输出阶段耗时：`[timing] app=word_fit_app kind=phase ...`
- 当前阶段名称与图节点一致：
  - `fetch_file`
  - `resolve_file`
  - `ocr_node`
  - `doc_node`
  - `read_text_node`
  - `extract_info`
  - `locate_info`
- 日志字段统一为：
  - `route`
  - `phase`
  - `duration_ms`
  - `success`
  - `conversation_id`
  - `file_id`
