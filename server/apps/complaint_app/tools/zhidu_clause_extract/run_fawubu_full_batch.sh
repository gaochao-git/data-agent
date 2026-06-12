#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "用法: bash apps/complaint_app/tools/zhidu_clause_extract/run_fawubu_full_batch.sh 批次目录 outline.xlsx pdfs目录"
  exit 1
fi

BATCH_DIR="$1"
OUTLINE_XLSX="$2"
PDF_DIR="$3"

mkdir -p "${BATCH_DIR}/output"

python apps/complaint_app/tools/zhidu_clause_extract/generate_inter_category_pairs.py \
  --outline-xlsx "${OUTLINE_XLSX}" \
  --output-json "${BATCH_DIR}/output/02_outline_mapping/inter_group_pairs.json" \
  --model GLM-4.7-W8A8

python apps/complaint_app/tools/zhidu_clause_extract/extract_pdf_clauses.py \
  --pdf-dir "${PDF_DIR}" \
  --input-mode scan \
  --output-dir "${BATCH_DIR}/output/01_extract" \
  --llm-model GLM-4.7-W8A8

python apps/complaint_app/tools/zhidu_clause_extract/map_documents_to_outline.py \
  --documents-jsonl "${BATCH_DIR}/output/01_extract/documents.jsonl" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping" \
  --merged-outline-json "${BATCH_DIR}/output/02_outline_mapping/merged_outline.json" \
  --model GLM-4.7-W8A8

python apps/complaint_app/tools/zhidu_clause_extract/extract_effective_rules.py \
  --documents-json "${BATCH_DIR}/output/02_outline_mapping/documents_mapped.jsonl" \
  --clauses-jsonl "${BATCH_DIR}/output/01_extract/clauses.jsonl" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping/zhidu_effective_rules_fawubu" \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3

python apps/complaint_app/tools/zhidu_clause_extract/add_document_summary_to_effective_rules.py \
  --effective-rules-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules.jsonl" \
  --output-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl" \
  --raw-text-dir "${BATCH_DIR}/output/01_extract/raw_texts" \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3

python apps/complaint_app/tools/zhidu_clause_extract/detect_clause_conflicts.py \
  --effective-rules-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu" \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3 \
  --run-high-clause-locate

python apps/complaint_app/tools/zhidu_clause_extract/detect_clause_conflicts.py \
  --effective-rules-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu" \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3 \
  --run-inter-category \
  --skip-intra-category \
  --run-high-clause-locate

python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/high_conflict_clauses.jsonl" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review" \
  --effective-rules-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl" \
  --pair-scope intra \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3

python apps/complaint_app/tools/zhidu_clause_extract/strict_review_conflict_candidates.py \
  --input-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/high_conflict_clauses.jsonl" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter" \
  --effective-rules-jsonl "${BATCH_DIR}/output/02_outline_mapping/zhidu_effective_rules_fawubu/effective_rules_with_summary.jsonl" \
  --pair-scope inter \
  --model GLM-4.7-W8A8 \
  --concurrency 3 \
  --business-retries 3

python apps/complaint_app/tools/zhidu_clause_extract/export_high_conflict_report.py \
  --input-xlsx \
  "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/high_conflict_clause_candidates.xlsx" \
  "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/high_conflict_clause_candidates.xlsx" \
  --batch-output-dir "${BATCH_DIR}/output" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping/zhidu_conflict_word_reports" \
  --output-prefix "冲突报告-非严格版" \
  --skip-pdf \
  --overview-from-template

python apps/complaint_app/tools/zhidu_clause_extract/export_high_conflict_report.py \
  --input-xlsx \
  "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_strict.xlsx" \
  "${BATCH_DIR}/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_strict.xlsx" \
  --batch-output-dir "${BATCH_DIR}/output" \
  --output-dir "${BATCH_DIR}/output/02_outline_mapping/zhidu_conflict_word_reports" \
  --output-prefix "冲突报告-严格版本" \
  --candidate-confidence high \
  --skip-pdf \
  --overview-from-template

echo "批次输出目录: ${BATCH_DIR}/output"
