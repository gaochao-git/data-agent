# 直接大模型条款对比链路

本链路不走本体 `04 -> 08`，直接用大模型基于有效规则条款做制度对初筛，并对高风险制度对继续做条款级冲突定位。

## 当前批次

以下命令按 `/data/aigc/zhidu_0430` 写死，可直接复制执行。

```bash
cd /opt/aigc/fxcjg/server

set -a
source .env
set +a

mkdir -p /data/aigc/zhidu_0430/output
```

如果 outline 和 pdfs 都变化，建议使用新批次目录完整产出一套结果：

```bash
bash apps/complaint_app/tools/zhidu_clause_extract/run_fawubu_full_batch.sh \
  /data/aigc/zhidu_0604 \
  /data/aigc/zhidu_0604/outline.xlsx \
  /data/aigc/zhidu_0604/pdfs
```

## 执行顺序

```text
outline.xlsx
  -> merged_outline.json / inter_group_pairs.json
PDF 制度文件
  -> documents.jsonl / clauses.jsonl / raw_texts/*.txt
documents.jsonl + merged_outline.json
  -> documents_mapped.jsonl
documents_mapped.jsonl + clauses.jsonl
  -> effective_rules.jsonl
effective_rules.jsonl
  -> effective_rules_with_summary.jsonl
effective_rules_with_summary.jsonl
  -> 制度对初筛 + 条款级冲突定位 + 严格复核 + 合并 Word 报告
```

如果 `01_extract` 和 `02_outline_mapping` 已经跑完，且已存在 `/data/aigc/zhidu_0430/output/02_outline_mapping/documents_mapped.jsonl`，可直接从第 4 步开始。

## 1. 生成目录元数据与组间关系初稿

当前直接比较链路使用版块作为优先比较键：组内比较按同一版块聚合，组间比较按重点版块对生成；只有制度目录中版块为空时才回退使用领域。

```bash
python apps/complaint_app/tools/zhidu_clause_extract/generate_inter_category_pairs.py \
  --outline-xlsx /data/aigc/zhidu_0430/outline.xlsx \
  --output-json /data/aigc/zhidu_0430/output/02_outline_mapping/inter_group_pairs.json \
  --model GLM-4.7-W8A8
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/merged_outline.json`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/inter_group_pairs.json`

## 2. PDF 抽取与条款切分

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --pdf-dir /data/aigc/zhidu_0430/pdfs \
  --input-mode scan \
  --output-dir /data/aigc/zhidu_0430/output/01_extract \
  --llm-model GLM-4.7-W8A8
```

输出：

- `/data/aigc/zhidu_0430/output/01_extract/documents.jsonl`
- `/data/aigc/zhidu_0430/output/01_extract/clauses.jsonl`
- `/data/aigc/zhidu_0430/output/01_extract/raw_texts/*.txt`

## 3. documents 映射 outline

```bash
python apps/complaint_app/tools/zhidu_clause_extract/map_documents_to_outline.py \
  --documents-jsonl /data/aigc/zhidu_0430/output/01_extract/documents.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping \
  --merged-outline-json /data/aigc/zhidu_0430/output/02_outline_mapping/merged_outline.json \
  --model GLM-4.7-W8A8
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/document_outline_mapping.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/document_outline_mapping.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/document_outline_mapping_summary.json`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/documents_mapped.jsonl`

确认：

```bash
cat /data/aigc/zhidu_0430/output/02_outline_mapping/document_outline_mapping_summary.json
```

## 3.1 可选：只重切指定文档条款并原地更新

如果已经人工优化了某个 `raw_texts/*.txt`，但不想换目录，可以只重切该文档，并原地替换当前 `documents.jsonl/clauses.jsonl` 中该文档的旧记录。

按 `doc_id` 指定：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --output-dir /data/aigc/zhidu_0430/output/01_extract \
  --clauses-from-raw-text \
  --rerun-doc-id 你的doc_id \
  --llm-model GLM-4.7-W8A8
```

按 `doc_id` 清单批量指定，清单文件一行一个 `doc_id`：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --output-dir /data/aigc/zhidu_0430/output/01_extract \
  --clauses-from-raw-text \
  --rerun-doc-id-list /data/aigc/zhidu_0430/rerun_doc_ids.txt \
  --clauses-from-raw-text-use-page-chunks \
  --llm-model GLM-4.7-W8A8
```

按原始文件名指定：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --output-dir /data/aigc/zhidu_0430/output/01_extract \
  --clauses-from-raw-text \
  --rerun-file-name '原始文件名.pdf' \
  --llm-model GLM-4.7-W8A8
```

说明：

- 不会换目录。
- 不会生成 `clauses_v*.jsonl`。
- 只替换 `documents.jsonl/clauses.jsonl` 中该文档对应的记录。
- 如果后续已经跑过 `02_outline_mapping`，条款重切后需要重新跑从 `02_outline_mapping` 开始依赖 `documents.jsonl/clauses.jsonl` 的后续步骤。

## 4. 生成有效规则

```bash
python apps/complaint_app/tools/zhidu_clause_extract/extract_effective_rules.py \
  --documents-json /data/aigc/zhidu_0430/output/02_outline_mapping/documents_mapped.jsonl \
  --clauses-jsonl /data/aigc/zhidu_0430/output/01_extract/clauses.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_summary.json`

确认：

```bash
ls -lh /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules.jsonl
```

## 5. 可选：补充每份制度摘要

如果需要保留整份制度的背景语义，可基于已有 `effective_rules.jsonl` 生成带摘要的新文件：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/add_document_summary_to_effective_rules.py \
  --effective-rules-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules.jsonl \
  --output-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl \
  --raw-text-dir /data/aigc/zhidu_0430/output/01_extract/raw_texts \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary_summary.json`

说明：

- 这一步不会修改原始 `effective_rules.jsonl`。
- `detect_clause_conflicts.py` 已支持读取 `document_summary`；冲突分析输入建议使用 `effective_rules_with_summary.jsonl`。

## 6. 组内制度对初筛 + 高风险条款级定位

```bash
python apps/complaint_app/tools/zhidu_clause_extract/detect_clause_conflicts.py \
  --effective-rules-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3 \
  --run-high-clause-locate
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/doc_pair_review.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/doc_pair_review.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/high_conflict_clauses.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/high_conflict_clauses.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/high_conflict_clause_candidates.xlsx`

## 7. 组间制度对初筛 + 高风险条款级定位

```bash
python apps/complaint_app/tools/zhidu_clause_extract/detect_clause_conflicts.py \
  --effective-rules-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3 \
  --run-inter-category \
  --skip-intra-category \
  --run-high-clause-locate
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/doc_pair_review.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/doc_pair_review.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/high_conflict_clauses.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/high_conflict_clauses.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/high_conflict_clause_candidates.xlsx`

说明：

- `high_conflict_clauses.xlsx` 是制度对级结果，一行是一对制度，`conflict_candidates` 保留原始数组。
- `high_conflict_clause_candidates.xlsx` 是候选条款级结果，一行是一条候选冲突，适合人工查看和筛选。

如果组内/组间已经跑完，只需要从现有 `high_conflict_clauses.jsonl` 导出候选条款级 Excel，不需要重新调用模型：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/detect_clause_conflicts.py \
  --effective-rules-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu \
  --export-clause-candidates-only
```

```bash
python apps/complaint_app/tools/zhidu_clause_extract/detect_clause_conflicts.py \
  --effective-rules-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu \
  --export-clause-candidates-only
```

## 8. 可选：严格复核条款级冲突候选

如果 `high_conflict_clauses.jsonl` 中出现“不同对象/不同事项/文件自身解释修订条款”导致的误报，可基于现有输出再严格筛一遍，不需要重跑前面步骤。

复核组内结果：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/high_conflict_clauses.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review \
  --effective-rules-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl \
  --pair-scope intra \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3
```

复核组间结果：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/high_conflict_clauses.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter \
  --effective-rules-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl \
  --pair-scope inter \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3
```

组内严格复核输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_clauses_strict.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_clauses_strict.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_strict.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_medium.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_all.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/strict_review_summary.json`

组间严格复核输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_clauses_strict.jsonl`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_clauses_strict.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_strict.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_medium.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_all.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/strict_review_summary.json`

如果严格复核已经跑完，只需要从 `high_conflict_clauses_strict.jsonl` 中导出全部候选条款级 Excel，不需要重新调用模型：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_clauses_strict.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review \
  --pair-scope intra \
  --export-all-candidates-only
```

```bash
python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_clauses_strict.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter \
  --pair-scope inter \
  --export-all-candidates-only
```

如果严格复核已经跑完，只需要从 `high_conflict_clauses_strict.jsonl` 中导出原始置信度为 `medium` 的候选，不需要重新调用模型：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_clauses_strict.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review \
  --pair-scope intra \
  --export-medium-only
```

```bash
python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_clauses_strict.jsonl \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter \
  --pair-scope inter \
  --export-medium-only
```

## 9. 合并导出严格复核报告

合并组内和组间严格复核结果，导出一份总报告：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/export_high_conflict_report.py \
  --input-xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_strict.xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_strict.xlsx \
  --batch-output-dir /data/aigc/zhidu_0430/output \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_report \
  --output-prefix strict_conflict_report
```

如果当前机器没有 LibreOffice，或者只想生成 `xlsx + docx`：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/export_high_conflict_report.py \
  --input-xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_strict.xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_strict.xlsx \
  --batch-output-dir /data/aigc/zhidu_0430/output \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_report \
  --output-prefix strict_conflict_report \
  --skip-pdf
```

如果要把原始置信度为 `medium` 的候选也生成 Word 报告：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/export_high_conflict_report.py \
  --input-xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_medium.xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_medium.xlsx \
  --batch-output-dir /data/aigc/zhidu_0430/output \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_report_medium \
  --output-prefix medium_conflict_report \
  --skip-pdf
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_report/strict_conflict_report_pairs.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_report/strict_conflict_report_candidates.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_report/strict_conflict_report.docx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_report/strict_conflict_report.pdf`

最新报告口径只需要最终两个 Word：

- `冲突报告-非严格版.docx`：合并组内和组间，候选按 high、medium、low 排序，便于优先查看 high。
- `冲突报告-严格版本.docx`：合并组内和组间，只保留 high 候选。

报告每条候选会展示两份制度的主责部门和制度级别；组内比较会跳过同一版块内“一级制度 vs 二级制度”的制度对。

## 关键说明

- `extract_effective_rules.py` 的输入是 `/data/aigc/zhidu_0430/output/02_outline_mapping/documents_mapped.jsonl` + `/data/aigc/zhidu_0430/output/01_extract/clauses.jsonl`；它只基于人工确认后的条款做 valid/invalid 分类，不再从 raw_texts 重新切条款。
- `detect_clause_conflicts.py` 的输入建议使用 `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl`。
- 这条链路不依赖 `04_clause_semantics_mentions`、`05_clause_semantics_normalization`、`06_clause_semantics_concepts`、`07_clause_rule_expressions`、`08_clause_semantics_ontology`。
- `effective_rules.jsonl` 用于保留有约束意义的规则条款，降低制度对比噪音；背景说明、制定目的等通常不参与条款级冲突判断。
- 严格复核用于处理现有候选误报；如果大量误报都来自解释权、修订权、生效废止、附件更新等文件自身管理条款，再考虑重跑 `extract_effective_rules.py` 优化无效条款筛选。

## 10. 关键词命中扫描报告

如果只需要判断制度正文中是否存在指定关键词，先在 UAT 准备 `/data/aigc/zhidu_0430/keywords.txt`，一行一个关键词，然后执行：

```bash
python apps/complaint_app/tools/zhidu_clause_extract/scan_policy_keywords.py \
  --documents-jsonl /data/aigc/zhidu_0430/output/02_outline_mapping/documents_mapped_for_conflict.jsonl \
  --raw-text-dir /data/aigc/zhidu_0430/output/01_extract/raw_texts \
  --keywords-txt /data/aigc/zhidu_0430/keywords.txt \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_keyword_report \
  --output-prefix zhidu_keyword_report
```

输出：

- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_keyword_report/zhidu_keyword_report_matches.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_keyword_report/zhidu_keyword_report_documents.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_keyword_report/zhidu_keyword_report_keywords.xlsx`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_keyword_report/zhidu_keyword_report_summary.json`
- `/data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_keyword_report/zhidu_keyword_report.docx`
