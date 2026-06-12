#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_word_reports

python apps/complaint_app/tools/zhidu_clause_extract/export_high_conflict_report.py \
  --input-xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/high_conflict_clause_candidates.xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/high_conflict_clause_candidates.xlsx \
  --batch-output-dir /data/aigc/zhidu_0430/output \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_word_reports \
  --output-prefix "冲突报告-非严格版" \
  --skip-pdf \
  --overview-from-template

python apps/complaint_app/tools/zhidu_clause_extract/export_high_conflict_report.py \
  --input-xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zunei_fawubu/strict_review/high_conflict_candidates_strict.xlsx \
  /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_doc_pair_review_zujian_fawubu/strict_review_inter/high_conflict_candidates_strict.xlsx \
  --batch-output-dir /data/aigc/zhidu_0430/output \
  --output-dir /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_word_reports \
  --output-prefix "冲突报告-严格版本" \
  --candidate-confidence high \
  --skip-pdf \
  --overview-from-template

echo "Word 输出目录: /data/aigc/zhidu_0430/output/02_outline_mapping/zhidu_conflict_word_reports"
