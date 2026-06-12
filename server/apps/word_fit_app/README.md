# word_fit_app

## 1. 模块定位
面向“文件字段抽取”的通用模块。接收文件标识与字段定义，完成文件获取、内容解析与 LLM 抽取，结果以 SSE 流式返回。

## 2. 主要功能
- 根据 `file_id` 或 `file_ids` 下载或定位文件。
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
      - 含义：待抽取文件的文件标识，兼容单文件调用。
      - 是否必填：与 `file_ids` 二选一。
    - `file_ids: string[]`
      - 含义：多个文件共同组成一次字段提取时传入；服务端会去重、排序后合并材料文本，输出一个抽取结果。
      - 是否必填：与 `file_id` 二选一。
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
    - `force?: boolean`
      - 含义：是否强制重新执行 OCR，跳过 OCR 缓存。
      - 默认值：`false`。
    - `extract_model?: string`
      - 含义：本次字段提取临时使用的模型名；不传时优先使用 `WORDFIT_EXTRACT_MODEL`，再回落到 `OPENAI_ANALYSIS_MODEL`。
      - 默认值：`null`。
    - `ocr_model?: string`
      - 含义：本次 OCR 临时使用的模型名；不传时使用 `OCR_LLM_MODEL`。
      - 默认值：`null`。
  - 返回：`text/event-stream`

  - 举报分析预热复用：
    - 当 `analysis_scene` 与 `analysis_user_id` 非空时，接口会异步触发举报事项分析预热。
    - 预热前会把本次字段提取阶段得到的文件 OCR 合并文本写入举报分析的 `text/<version>/final.txt` 缓存，后续预热链路在 `force=false` 时直接复用该文本，不再重复从 PDF/图片执行 OCR。
    - 用户手动调用 `/api/complaint/complaint-analysis` 且传 `force=true` 时，仍会忽略该文本缓存，从原始 PDF/图片重新 OCR、重新摘要、重新分析。

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

  - 多文件合并请求示例：
    ```json
    {
      "file_ids": ["case_1.pdf", "case_2.pdf"],
      "extract_fields": [
        {
          "msgkey": "reporter",
          "description": "举报人姓名"
        }
      ],
      "locate_enabled": false
    }
    ```

  - 说明：多文件抽取第一版返回一个合并结果；多文件场景不执行 PDF 坐标定位，`locate` 返回空对象。

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
      "analysis_match_strategy": "two_stage",
      "force": true,
      "extract_model": "qwen3.5",
      "ocr_model": "qwen2.5-vl"
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
- `WORDFIT_EXTRACT_MODEL`（可选，WordFit 字段提取默认模型；未配置时回落到 `OPENAI_ANALYSIS_MODEL`）
- `WORDFIT_LLM_REQUEST_LOG_DEBUG`（默认 `false`；开启后打印字段提取大模型完整请求 messages）
- `LLM_PROVIDER`
- `WORDFIT_OCR_MAX_WAIT_SECONDS`（可选，单张图片/单页 OCR 最长等待秒数；超时后返回已流出的部分文本）
- `OCR_LLM_MODEL` / `OCR_LLM_TIMEOUT` / `OCR_LLM_TEMPERATURE` / `OCR_LLM_TOP_P`
- `OCR_CACHE_DIR` / `OCR_TEXT_FALLBACK_DIR`
- `OCR_TEXT_FALLBACK_DIR` 下的 WordFit OCR 文本会按 `base_id/request_id/文件名-路径哈希/` 分层保存，避免不同请求或同名文件互相覆盖。
- `WORDFIT_OCR_METRICS_DIR`（可选，OCR 耗时结构化落盘目录；benchmark 使用 `--ocr-metrics-dir` 读取后生成 OCR 对比报告）

请求级覆盖：
- `extract_model` 仅覆盖本次字段提取模型，不修改 `.env`。
- `ocr_model` 仅覆盖本次 OCR 模型，不修改 `.env`；使用该参数时 OCR 缓存 key 会带模型名，避免不同 OCR 模型共用同一个文件缓存。
- `ocr_metrics_batch_id` 用于把本次请求的 OCR 耗时写入 `WORDFIT_OCR_METRICS_DIR/<batch_id>/<conversation_id>.jsonl`，不影响 SSE 返回内容。
- `force=true` 仅跳过 OCR 缓存；字段提取阶段当前没有显式缓存。

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

## 8. 并发与质量验证脚本

脚本入口：

```bash
python3 server/apps/word_fit_app/tools/field_extract_benchmark.py \
  --endpoint "https://uat.example.com/api/word-fit/extract" \
  --payload-json "/path/to/extract_payload.json" \
  --gold-answer "/path/to/gold_answer.json" \
  --output-dir "/tmp/wordfit-benchmark-qwen35" \
  --model-label "qwen3.5" \
  --extract-model "qwen3.5" \
  --disable-analysis-preheat \
  --force \
  --concurrency 1 5 10 \
  --rounds 5
```

如果不想使用完整 payload，也可以拆分传入：

```bash
python3 server/apps/word_fit_app/tools/field_extract_benchmark.py \
  --endpoint "https://uat.example.com/api/word-fit/extract" \
  --file-id "internal-case-file-id" \
  --extract-fields "/path/to/extract_fields.json" \
  --gold-answer "/path/to/gold_answer.json" \
  --output-dir "/tmp/wordfit-benchmark-qwen35" \
  --model-label "qwen3.5" \
  --extract-model "qwen3.5" \
  --force \
  --concurrency 1 5 10 \
  --rounds 5
```

`--disable-analysis-preheat` 会清空请求中的 `analysis_scene`、`analysis_user_id`、`analysis_match_strategy`，用于测试时关闭异步举报事项分析预热。

可选 LLM judge：

```bash
python3 server/apps/word_fit_app/tools/field_extract_benchmark.py \
  --endpoint "https://uat.example.com/api/word-fit/extract" \
  --payload-json "/path/to/extract_payload.json" \
  --gold-answer "/path/to/gold_answer.json" \
  --match-rules "/path/to/match_rules.json" \
  --output-dir "/tmp/wordfit-benchmark-qwen35" \
  --model-label "qwen3.5" \
  --extract-model "qwen3.5" \
  --judge-mode llm \
  --judge-base-url "$OPENAI_BASE_URL" \
  --judge-model "qwen3.5" \
  --concurrency 1 \
  --rounds 3
```

如果希望默认所有字段都交给 LLM judge，只排除个别不参与统计的字段，可以这样写 `match_rules.json`：

```json
{
  "*": "llm_judge",
  "概要": "ignore"
}
```

输出文件：

- `raw_results.csv`：请求级明细。
- `performance_summary.csv`：性能汇总。
- `quality_summary.csv`：质量汇总。
- `field_compare.csv`：字段级比对。
- `responses.jsonl`：原始 SSE 解析结果与最终 extract。
- `report.md`：Markdown 汇总报告。
- `judge_logs/`：LLM judge 输入输出日志，仅 `--judge-mode llm` 时生成。
