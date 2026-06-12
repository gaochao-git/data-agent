# OCR Report Command

## UAT 服务端前提

UAT 服务端需要配置 OCR metrics 落盘目录，并部署支持 `ocr_metrics_batch_id` 的代码：

```bash
WORDFIT_OCR_METRICS_DIR=/data/aigc/fxcjg_data/wordfit/metrics
```

## 执行命令

```bash
python3 server/apps/word_fit_app/tools/field_extract_benchmark.py \
  --endpoint "https://你的UAT地址/api/word-fit/extract" \
  --payload-json "/path/to/extract_payload.json" \
  --gold-answer "/path/to/gold_answer.json" \
  --match-rules "/path/to/match_rules.json" \
  --output-dir "/path/to/output-dir" \
  --model-label "qwen25-vl-7b-ocr-rerun" \
  --ocr-model "Qwen2.5-VL-7B-Instruct" \
  --ocr-metrics-dir "/data/aigc/fxcjg_data/wordfit/metrics" \
  --ocr-metrics-batch-id "1111111" \
  --disable-analysis-preheat \
  --force \
  --concurrency 1 \
  --rounds 1
```

`--match-rules` 用于控制字段比对规则；如果本次验证需要按字段指定 `exact`、`contains`、`llm_judge` 或 `ignore`，必须传入该参数。

## 关键输出

- `report.md`：总报告，包含 OCR/字段提取字数、首 token、耗时和趋势图。
- `charts/`：SVG 趋势图和热力图。
- `matrix/`：图表对应的矩阵 CSV。
- `llm_call_detail.csv`：OCR 与字段提取模型调用明细。
- `llm_metrics_summary.csv`：OCR 与字段提取字数、首 token、耗时汇总。
- `ocr_detail.csv`：OCR 每页/每图明细。
- `ocr_overview.csv`：OCR 按并发维度汇总。

## 图片目录 OCR 直压命令

`--image-dir` 指向一个 base 目录，脚本会把其下一层子目录作为扫描件类别，例如 `普通文本类/`、`流水类/`、`截图类/`。

```bash
python3 server/apps/word_fit_app/tools/ocr_image_benchmark.py \
  --image-dir "/path/to/image_base_dir" \
  --ocr-models "Qwen2.5-VL-7B-Instruct,另一个OCR模型" \
  --output-dir "/path/to/output-dir" \
  --concurrency 1 3 5 10 \
  --rounds 3
```

关键输出：

- `report.md`：图片目录 OCR 直压报告。
- `raw_results.csv`：每张图片每次 OCR 请求明细。
- `summary_by_category_concurrency.csv`：按模型、类别、并发汇总，平均耗时单位秒，保留 2 位。
- `charts/`：按类别的热力图，以及按并发的平均耗时折线图。
- `matrix/`：热力图对应的矩阵 CSV。
