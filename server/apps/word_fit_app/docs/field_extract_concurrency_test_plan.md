# WordFit 字段提取并发验证方案

## 1. 目标

对 `POST /api/word-fit/extract` 做质量与性能对比验证，优先覆盖内部 UAT 环境中的单份举报信样本，并支持后续扩展到多模型、多并发、多样本。

本方案关注端到端字段提取链路：

```text
举报信文件 -> 文件获取/解析 -> OCR -> 字段提取 LLM -> SSE 结果解析 -> gold answer 对比 -> 报告
```

## 2. 输入

### 2.1 接口输入

- `endpoint`：UAT 环境 `word-fit/extract` 地址。
- `payload_json`：可直接 POST 到 `word-fit/extract` 的完整请求 JSON；脚本可从中读取 `file_id` 与 `extract_fields`。
- `file_id`：内部举报信文件标识。
- `extract_fields_path`：字段定义 JSON 文件路径；每次请求使用相同字段 key。
- `gold_answer_path`：人工确认的标准答案 JSON 文件路径。
- `model_label`：报告中展示的模型名称，例如 `qwen3.5`。
- `extract_model`：本次字段提取临时模型名。
- `ocr_model`：本次 OCR 临时模型名。
- `force`：是否跳过 OCR 缓存。
- `concurrency`：并发数，例如 `[1, 5, 10]`。
- `rounds`：每个并发配置运行轮次。
- `timeout_seconds`：单请求超时时间。

如果已经有完整接口 payload，推荐优先使用 `payload_json`，不需要再单独拆 `file_id` 和 `extract_fields_path`。

示例配置：

```json
{
  "endpoint": "https://uat.example.com/api/word-fit/extract",
  "file_id": "internal/case1.pdf",
  "extract_fields_path": "/path/to/extract_fields.json",
  "gold_answer_path": "/path/to/gold_answer.json",
  "model_label": "qwen3.5",
  "extract_model": "qwen3.5",
  "ocr_model": "",
  "force": true,
  "concurrency": [1, 5, 10],
  "rounds": 5,
  "timeout_seconds": 600
}
```

### 2.2 标准答案

`gold_answer_path` 指向人工确认的正确 JSON。第一版按固定 key 对比：

```json
{
  "reporter": "张三",
  "reported_institution": "中国银行长春支行、太阳城支行",
  "appeals": ["移送公安机关"]
}
```

后续如包含嵌套数组，测试脚本可将 JSON 展平成路径：

```text
complaints[0].label
complaints[0].user_cited_laws[0].raw_text
```

## 3. 接口能力

`word-fit/extract` 支持以下测试参数：

```json
{
  "force": true,
  "extract_model": "qwen3.5",
  "ocr_model": "qwen2.5-vl"
}
```

语义：

- `force=true`：本次请求强制重新执行 OCR，跳过 OCR 缓存。
- `extract_model`：临时覆盖本次字段提取模型，不需要改 `.env` 或重启服务。
- `ocr_model`：临时覆盖本次 OCR 模型，不需要改 `.env` 或重启服务。

缓存说明：

- OCR 阶段存在基于文件 sha256 的缓存。
- 传入 `ocr_model` 时，OCR 缓存 key 会包含模型名，避免不同 OCR 模型共用缓存。
- 字段提取 LLM 阶段当前没有显式缓存。

## 4. 实现方案

脚本入口已落在：

```text
server/apps/word_fit_app/tools/field_extract_benchmark.py
```

### 4.1 请求执行

测试脚本按 `(model_label, concurrency)` 组合执行请求：

1. 读取 `extract_fields_path` 与 `gold_answer_path`。
2. 为每次请求生成唯一 `conversation_id`。
3. 调用 UAT `word-fit/extract`。
4. 解析 SSE，提取最终 `type=result` 中的 `data.extract`。
5. 记录请求耗时、状态、异常和原始响应。

请求体示例：

```json
{
  "file_id": "internal/case1.pdf",
  "extract_fields": [],
  "conversation_id": "bench-qwen35-c5-r001-001",
  "locate_enabled": false,
  "force": true,
  "extract_model": "qwen3.5",
  "ocr_model": ""
}
```

命令示例：

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

如果没有完整 payload，也可以拆分传入：

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

关闭异步举报事项分析预热：

- `word-fit/extract` 里只要 `analysis_scene` 非空，就会异步触发举报事项分析预热。
- 测试时推荐加 `--disable-analysis-preheat`，脚本会把 `analysis_scene`、`analysis_user_id`、`analysis_match_strategy` 清空。

### 4.2 性能统计

每次请求记录：

- `request_id`
- `model_label`
- `extract_model`
- `ocr_model`
- `concurrency`
- `round`
- `force`
- `start_time`
- `end_time`
- `latency_ms`
- `http_status`
- `success`
- `error_type`
- `error_message`

汇总指标：

- 请求数
- 成功率
- 平均耗时
- P50 / P90 / P95 / P99
- QPS
- 超时数
- 失败数
- 空结果数
- JSON 合法率

### 4.3 质量评估

字段比对以 `gold_answer` 为基准。第一版推荐：

- `exact`：姓名、手机号、账号、日期等精确字段。
- `contains`：机构、法律名称、证据材料等可包含匹配字段。
- `llm_judge`：事件经过、举报事项、诉求等长文本字段。

LLM judge 输出结构：

```json
{
  "field_key": "appeals",
  "gold_value": ["移送公安机关"],
  "actual_value": ["移送公安部门"],
  "judge_result": "correct",
  "judge_reason": "语义一致"
}
```

枚举值：

- `correct`：正确。
- `partial`：部分正确。
- `wrong`：错误。
- `missing`：缺失。
- `extra`：多提。
- `judge_error`：评估失败。

所有 judge 输入与输出需要落盘，便于后期人工复核：

```text
judge_logs/
  qwen35_c5_r001_req001_reporter.json
  qwen35_c5_r001_req001_appeals.json
```

当前脚本支持可选 LLM judge：

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

`match_rules` 示例：

```json
{
  "reporter": "exact",
  "reported_institution": "contains",
  "incident": "llm_judge",
  "appeals": "llm_judge"
}
```

如果希望默认所有字段都交给 LLM judge，只排除个别不参与统计的字段：

```json
{
  "*": "llm_judge",
  "概要": "ignore"
}
```

## 5. 输出

### 5.1 原始请求结果表

文件：`raw_results.csv`

| 模型 | 并发数 | 轮次 | 请求ID | force | extract_model | ocr_model | 成功 | 耗时ms | JSON合法 | 空结果 | 错误信息 |
|---|---:|---:|---|---|---|---|---|---:|---|---|---|
| qwen3.5 | 5 | 1 | req001 | true | qwen3.5 |  | true | 12000 | true | false |  |

### 5.2 性能对比表

文件：`performance_summary.csv`

| 模型 | 并发数 | 请求数 | 成功率 | 平均耗时ms | P50 | P90 | P95 | P99 | QPS | 超时数 | 失败数 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| qwen3.5 | 5 | 25 | 100% | 12000 | 11000 | 16000 | 18000 | 21000 | 0.42 | 0 | 0 |

### 5.3 质量对比表

文件：`quality_summary.csv`

| 模型 | 并发数 | JSON合法率 | 字段准确率 | 字段缺失率 | 关键字段召回率 | 空结果率 | judge_error率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| qwen3.5 | 5 | 100% | 92% | 4% | 96% | 0% | 0% |

### 5.4 字段级比对表

文件：`field_compare.csv`

| 模型 | 并发数 | 请求ID | 字段 | 标准答案 | 实际结果 | 匹配方式 | 判断结果 | 原因 |
|---|---:|---|---|---|---|---|---|---|
| qwen3.5 | 5 | req001 | reporter | 张三 | 张三 | exact | correct | 完全一致 |

### 5.5 Markdown 报告

文件：`report.md`

报告章节：

1. 测试目标
2. 测试环境
3. 测试样本与字段
4. 模型与并发配置
5. 缓存策略
6. 性能对比表
7. 质量对比表
8. 字段错误样例
9. LLM judge 复核说明
10. 结论与建议

## 6. 推荐测试矩阵

第一阶段只测 `qwen3.5`：

| 模型 | extract_model | ocr_model | force | 并发数 | 轮次 |
|---|---|---|---|---:|---:|
| qwen3.5 | qwen3.5 |  | true | 1 | 5 |
| qwen3.5 | qwen3.5 |  | true | 5 | 5 |
| qwen3.5 | qwen3.5 |  | true | 10 | 5 |

后续扩展：

| 模型 | extract_model | ocr_model | force | 用途 |
|---|---|---|---|---|
| qwen2.5-vl | qwen3.5 | qwen2.5-vl | true | 对比 OCR 模型对字段质量的影响 |
| glm-ocr | qwen3.5 | glm-ocr | true | 对比 OCR 模型对字段质量的影响 |

## 7. 注意事项

- UAT 压测从低并发开始，建议顺序为 `1 -> 5 -> 10`。
- `force=true` 会显著增加 OCR 成本和耗时，只用于冷启动/完整链路测试。
- 如果只想测字段提取 LLM，建议后续增加“直接传 OCR 文本”的测试入口，否则端到端耗时会混入 OCR。
- 报告必须记录 `force`、`extract_model`、`ocr_model`，否则不同测试轮次不可比较。
- LLM judge 结果只能作为自动评估参考，关键字段错误样例应保留日志供人工复核。
