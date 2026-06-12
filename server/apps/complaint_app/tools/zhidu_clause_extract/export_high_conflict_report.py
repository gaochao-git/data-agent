from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from docx import Document
from docx.shared import Pt

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_analysis_model
from utils.llm_thinking import build_thinking_extra_body

OVERVIEW_SYSTEM_PROMPT = """你正在撰写一份公司制度冲突分析报告的首页说明。

用户会提供已经整理好的“输入数据草稿、实现原理步骤、输出结果草稿”和对应统计数据。

你的任务只是把草稿改写成更通俗、自然、适合报告首页展示的中文说明。

严格要求：
1. 不得编造数字。
2. 不得新增统计口径。
3. 不得改变草稿中的事实含义。
4. 不要出现内部目录名、脚本名、字段名。
5. 实现原理要体现关键步骤、输入、处理动作、产出和关键数字，但不要写成流水账，不要机械罗列执行过程。
6. 每个输出字段只写一段话；整体表达要有总结性，优先说明“为什么这样做”和“产出了什么价值”，再带出必要数字。
7. 语言要简洁、客观、面向业务读者。

输出严格 JSON，不要输出 Markdown，不要输出代码块。

输出格式：
{
  "input_data": "改写后的输入数据说明",
  "method": "改写后的实现原理说明，概括关键步骤，不写流水账",
  "output_result": "改写后的输出结果说明"
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从冲突候选 Excel 导出高置信冲突报告。")
    parser.add_argument("--input-xlsx", nargs="*", default=[], help="一个或多个 doc_pair_review_merged.xlsx / high_conflict_candidates_strict.xlsx 路径")
    parser.add_argument("--input-jsonl", nargs="*", default=[], help="一个或多个 high_conflict_clauses_strict.jsonl 路径")
    parser.add_argument("--batch-output-dir", default="", help="批次输出根目录，例如 /data/aigc/zhidu_0430/output；传入后首页说明由 LLM 基于统计数字生成")
    parser.add_argument("--model", default="", help="首页说明生成模型，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--enable-thinking", action="store_true", help="首页说明生成时显式开启模型思考")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--output-prefix", default="high_conflict_report", help="输出文件名前缀")
    parser.add_argument("--skip-pdf", action="store_true", help="只生成 Excel 和 Word，不转 PDF")
    parser.add_argument("--soffice-bin", default="", help="LibreOffice soffice 可执行文件路径，默认自动查找")
    parser.add_argument("--overview-from-template", action="store_true", help="使用本地模板生成首页说明，不调用模型")
    parser.add_argument("--candidate-confidence", choices=["all", "high", "medium", "low"], default="all", help="候选置信度过滤，默认 all")
    return parser


# 加载 server/.env，便于脚本直接运行。
def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


# 统一判断单元格是否为 true。
def is_true_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"true", "1", "yes"}


# 统一判断单元格是否为空。
def is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return str(value).strip() == ""


# 读取 JSONL 行。
def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


# 读取 JSON 文件。
def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


# 从多个候选字段中取第一个非空值。
def first_text(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


# 统计列表字段数量。
def count_list_field(rows: list[dict[str, Any]], keys: list[str]) -> int:
    total = 0
    for row in rows:
        for key in keys:
            value = row.get(key)
            if isinstance(value, list):
                total += len(value)
                break
    return total


def first_existing_dir(*paths: Path) -> Path:
    for path in paths:
        if path.is_dir():
            return path
    return paths[0]


# 从批次目录提取报告首页所需统计数字。
def collect_batch_stats(batch_output_dir: Path) -> dict[str, Any]:
    documents = load_jsonl_rows(batch_output_dir / "01_extract" / "documents.jsonl")
    clauses = load_jsonl_rows(batch_output_dir / "01_extract" / "clauses.jsonl")
    mapped_documents = load_jsonl_rows(batch_output_dir / "02_outline_mapping" / "documents_mapped.jsonl")
    effective_rules_dir = first_existing_dir(batch_output_dir / "02_outline_mapping" / "zhidu_effective_rules_fawubu", batch_output_dir / "02_outline_mapping" / "zhidu_effective_rules")
    effective_rows = load_jsonl_rows(effective_rules_dir / "effective_rules.jsonl")
    effective_summary = load_json(effective_rules_dir / "effective_rules_summary.json")
    summary_summary = load_json(effective_rules_dir / "effective_rules_with_summary_summary.json")
    mapping_summary = load_json(batch_output_dir / "02_outline_mapping" / "document_outline_mapping_summary.json")
    intra_review_dir = first_existing_dir(batch_output_dir / "02_outline_mapping" / "zhidu_doc_pair_review_zunei_fawubu" / "strict_review", batch_output_dir / "02_outline_mapping" / "zhidu_doc_pair_review_zunei" / "strict_review")
    inter_review_dir = first_existing_dir(batch_output_dir / "02_outline_mapping" / "zhidu_doc_pair_review_zujian_fawubu" / "strict_review_inter", batch_output_dir / "02_outline_mapping" / "zhidu_doc_pair_review_zujian" / "strict_review_inter")
    intra_summary = load_json(intra_review_dir / "strict_review_summary.json")
    inter_summary = load_json(inter_review_dir / "strict_review_summary.json")
    categories = {first_text(row, ["category", "mapped_category", "outline_category", "first_level_category"]) for row in mapped_documents}
    categories.discard("")
    valid_rule_count = int(effective_summary.get("total_valid_rules") or effective_summary.get("total_valid_rule") or 0)
    invalid_rule_count = int(effective_summary.get("total_invalid_rules") or effective_summary.get("total_invalid_rule") or 0)
    if valid_rule_count == 0:
        valid_rule_count = count_list_field(effective_rows, ["valid_rules"])
    if invalid_rule_count == 0:
        invalid_rule_count = count_list_field(effective_rows, ["invalid_rules"])
    summary_status_counts = summary_summary.get("summary_status_counts") if isinstance(summary_summary.get("summary_status_counts"), dict) else {}
    summary_ok_count = int(summary_status_counts.get("ok") or summary_summary.get("ok") or 0)
    return {
        "batch_output_dir": str(batch_output_dir),
        "document_count": len(documents) or len(mapped_documents) or len(effective_rows),
        "category_count": len(categories),
        "clause_count": len(clauses),
        "valid_rule_count": valid_rule_count,
        "invalid_rule_count": invalid_rule_count,
        "document_summary_count": summary_ok_count,
        "mapped_document_count": int(mapping_summary.get("matched") or len(mapped_documents)),
        "mapping_total_count": int(mapping_summary.get("total") or len(mapped_documents)),
        "intra_original_candidate_count": int(intra_summary.get("original_candidate_count") or 0),
        "intra_strict_candidate_count": int(intra_summary.get("strict_candidate_count") or 0),
        "intra_rejected_candidate_count": int(intra_summary.get("rejected_candidate_count") or 0),
        "inter_original_candidate_count": int(inter_summary.get("original_candidate_count") or 0),
        "inter_strict_candidate_count": int(inter_summary.get("strict_candidate_count") or 0),
        "inter_rejected_candidate_count": int(inter_summary.get("rejected_candidate_count") or 0),
    }


# 将 stage2_conflict_candidates_json 解析为列表。
def parse_stage2_candidates(raw: Any) -> list[dict]:
    if is_empty_value(raw):
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    text = str(raw).strip()
    if not text:
        return []
    payload = json.loads(text)
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


# 过滤出初筛 high 且二筛含 high 候选的制度对。
def filter_pair_rows(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    pair_rows: list[dict] = []
    candidate_rows: list[dict] = []
    records = df.to_dict(orient="records")
    for row in records:
        possible_conflict = is_true_value(row.get("possible_conflict"))
        confidence = str(row.get("confidence", "")).strip().lower()
        candidates = parse_stage2_candidates(row.get("stage2_conflict_candidates_json"))
        high_candidates = [item for item in candidates if str(item.get("confidence", "")).strip().lower() == "high"]
        if not possible_conflict or confidence != "high" or not high_candidates:
            continue
        pair_rows.append(dict(row))
        for index, item in enumerate(high_candidates, start=1):
            candidate_rows.append(build_candidate_row(row, item, index))
    return pair_rows, candidate_rows


# 兼容严格复核后的候选级 Excel。
def filter_strict_candidate_rows(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    pair_map: dict[str, dict] = {}
    candidate_rows: list[dict] = []
    for index, row in enumerate(df.to_dict(orient="records"), start=1):
        left_text = str(row.get("left_text", "") or "").strip()
        right_text = str(row.get("right_text", "") or "").strip()
        if not left_text or not right_text:
            continue
        candidate_row = build_strict_candidate_row(row, index)
        candidate_rows.append(candidate_row)
        pair_key = "|".join([candidate_row.get("category", ""), candidate_row.get("left_title", ""), candidate_row.get("right_title", "")])
        if pair_key not in pair_map:
            pair_map[pair_key] = {
                "category": candidate_row.get("category", ""),
                "left_title": candidate_row.get("left_title", ""),
                "right_title": candidate_row.get("right_title", ""),
                "possible_conflict": True,
                "confidence": candidate_row.get("candidate_confidence", ""),
            }
    return list(pair_map.values()), candidate_rows


# 构造严格复核候选级明细行。
def build_strict_candidate_row(row: dict, index: int) -> dict:
    conflict_type = str(row.get("strict_conflict_type") or row.get("conflict_type") or "").strip()
    reason = str(row.get("strict_reason") or row.get("candidate_reason") or "").strip()
    candidate = {
        "conflict_type": conflict_type,
        "reason": reason,
        "left_article_no": str(row.get("left_article_no", "") or "").strip(),
        "left_text": str(row.get("left_text", "") or "").strip(),
        "right_article_no": str(row.get("right_article_no", "") or "").strip(),
        "right_text": str(row.get("right_text", "") or "").strip(),
    }
    pair_row = {
        "category": str(row.get("category", "") or "").strip(),
        "left_title": str(row.get("left_title", "") or "").strip(),
        "right_title": str(row.get("right_title", "") or "").strip(),
    }
    return {
        "pair_scope": str(row.get("pair_scope", "") or "").strip(),
        "category": pair_row["category"],
        "left_category": str(row.get("left_category", "") or "").strip(),
        "right_category": str(row.get("right_category", "") or "").strip(),
        "left_doc_id": str(row.get("left_doc_id", "") or "").strip(),
        "right_doc_id": str(row.get("right_doc_id", "") or "").strip(),
        "left_title": pair_row["left_title"],
        "right_title": pair_row["right_title"],
        "left_primary_department": str(row.get("left_primary_department", "") or "").strip(),
        "right_primary_department": str(row.get("right_primary_department", "") or "").strip(),
        "left_policy_level": str(row.get("left_policy_level", "") or "").strip(),
        "right_policy_level": str(row.get("right_policy_level", "") or "").strip(),
        "possible_conflict": True,
        "pair_confidence": "",
        "pair_reason": "",
        "stage2_candidate_count": "",
        "candidate_index": index,
        "candidate_confidence": str(row.get("strict_confidence") or row.get("confidence") or "").strip().lower(),
        "conflict_type": conflict_type,
        "candidate_reason": reason,
        "left_article_no": candidate["left_article_no"],
        "left_text": candidate["left_text"],
        "right_article_no": candidate["right_article_no"],
        "right_text": candidate["right_text"],
        "formatted_sentence": build_formatted_sentence(pair_row, candidate),
        "review_result": "",
        "review_comment": "",
    }


# 推断结果来源范围。
def infer_pair_scope_from_path(path: Path) -> str:
    text = str(path)
    if "zunei" in text or "strict_review/" in text:
        return "intra"
    if "zujian" in text or "strict_review_inter" in text:
        return "inter"
    return ""


# 给报告行补充来源文件和范围。
def attach_source_info(rows: list[dict], path: Path, source_scope: str) -> list[dict]:
    result = []
    for row in rows:
        item = dict(row)
        item["source_file"] = str(path)
        if not str(item.get("pair_scope") or "").strip() and source_scope:
            item["pair_scope"] = source_scope
        result.append(item)
    return result


# 根据输入表结构选择报告数据来源。
def build_report_rows(df: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    if "stage2_conflict_candidates_json" in set(df.columns):
        return filter_pair_rows(df)
    return filter_strict_candidate_rows(df)


# 从严格复核 jsonl 展开候选。
def build_report_rows_from_strict_jsonl(path: Path) -> tuple[list[dict], list[dict]]:
    pair_map: dict[str, dict] = {}
    candidate_rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        for candidate in row.get("strict_conflict_candidates", []) or []:
            item = dict(row)
            item.update(candidate)
            review = candidate.get("strict_review") or {}
            item["strict_confidence"] = review.get("confidence") or candidate.get("confidence")
            item["strict_conflict_type"] = review.get("conflict_type") or candidate.get("conflict_type")
            item["strict_reason"] = review.get("reason") or candidate.get("reason")
            candidate_row = build_strict_candidate_row(item, len(candidate_rows) + 1)
            candidate_rows.append(candidate_row)
            pair_key = "|".join([candidate_row.get("category", ""), candidate_row.get("left_title", ""), candidate_row.get("right_title", "")])
            if pair_key not in pair_map:
                pair_map[pair_key] = {
                    "category": candidate_row.get("category", ""),
                    "left_title": candidate_row.get("left_title", ""),
                    "right_title": candidate_row.get("right_title", ""),
                    "possible_conflict": True,
                    "confidence": candidate_row.get("candidate_confidence", ""),
                }
    return list(pair_map.values()), candidate_rows


# 合并多个输入文件。
def build_report_rows_from_inputs(xlsx_paths: list[str], jsonl_paths: list[str]) -> tuple[list[dict], list[dict]]:
    pair_rows: list[dict] = []
    candidate_rows: list[dict] = []
    for path_text in xlsx_paths:
        path = Path(path_text).expanduser().resolve()
        source_scope = infer_pair_scope_from_path(path)
        current_pairs, current_candidates = build_report_rows(pd.read_excel(path))
        pair_rows.extend(attach_source_info(current_pairs, path, source_scope))
        candidate_rows.extend(attach_source_info(current_candidates, path, source_scope))
    for path_text in jsonl_paths:
        path = Path(path_text).expanduser().resolve()
        source_scope = infer_pair_scope_from_path(path)
        current_pairs, current_candidates = build_report_rows_from_strict_jsonl(path)
        pair_rows.extend(attach_source_info(current_pairs, path, source_scope))
        candidate_rows.extend(attach_source_info(current_candidates, path, source_scope))
    return pair_rows, candidate_rows


# 构造候选级明细行。
def build_candidate_row(pair_row: dict, candidate: dict, index: int) -> dict:
    return {
        "pair_scope": str(pair_row.get("pair_scope", "")).strip(),
        "category": str(pair_row.get("category", "")).strip(),
        "left_category": str(pair_row.get("left_category", "")).strip(),
        "right_category": str(pair_row.get("right_category", "")).strip(),
        "left_doc_id": str(pair_row.get("left_doc_id", "")).strip(),
        "right_doc_id": str(pair_row.get("right_doc_id", "")).strip(),
        "left_title": str(pair_row.get("left_title", "")).strip(),
        "right_title": str(pair_row.get("right_title", "")).strip(),
        "left_primary_department": str(pair_row.get("left_primary_department", "")).strip(),
        "right_primary_department": str(pair_row.get("right_primary_department", "")).strip(),
        "left_policy_level": str(pair_row.get("left_policy_level", "")).strip(),
        "right_policy_level": str(pair_row.get("right_policy_level", "")).strip(),
        "possible_conflict": bool(is_true_value(pair_row.get("possible_conflict"))),
        "pair_confidence": str(pair_row.get("confidence", "")).strip().lower(),
        "pair_reason": str(pair_row.get("reason", "")).strip(),
        "stage2_candidate_count": int(pair_row.get("stage2_candidate_count", 0) or 0),
        "candidate_index": index,
        "candidate_confidence": str(candidate.get("confidence", "")).strip().lower(),
        "conflict_type": str(candidate.get("conflict_type", "")).strip(),
        "candidate_reason": str(candidate.get("reason", "")).strip(),
        "left_article_no": str(candidate.get("left_article_no", "")).strip(),
        "left_text": str(candidate.get("left_text", "")).strip(),
        "right_article_no": str(candidate.get("right_article_no", "")).strip(),
        "right_text": str(candidate.get("right_text", "")).strip(),
        "formatted_sentence": build_formatted_sentence(pair_row, candidate),
        "review_result": "",
        "review_comment": "",
    }


# 生成用于报告展示的自然语言句子。
def build_formatted_sentence(pair_row: dict, candidate: dict) -> str:
    left_title = str(pair_row.get("left_title", "")).strip()
    right_title = str(pair_row.get("right_title", "")).strip()
    left_article_no = str(candidate.get("left_article_no", "")).strip()
    right_article_no = str(candidate.get("right_article_no", "")).strip()
    left_text = str(candidate.get("left_text", "")).strip()
    right_text = str(candidate.get("right_text", "")).strip()
    conflict_type = str(candidate.get("conflict_type", "")).strip()
    reason = str(candidate.get("reason", "")).strip()
    return (
        f"制度《{left_title}》的{left_article_no}，内容是“{left_text}”，"
        f"和制度《{right_title}》的{right_article_no}，内容是“{right_text}”，"
        f"存在“{conflict_type}”的冲突，理由是：“{reason}”。"
    )


def confidence_rank(value: str) -> int:
    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return 0
    if normalized == "medium":
        return 1
    if normalized == "low":
        return 2
    return 3


def filter_and_sort_candidate_rows(candidate_rows: list[dict], confidence_filter: str) -> list[dict]:
    filtered = []
    for row in candidate_rows:
        confidence = str(row.get("candidate_confidence") or "").strip().lower()
        if confidence_filter != "all" and confidence != confidence_filter:
            continue
        filtered.append(row)
    return sorted(filtered, key=lambda row: (confidence_rank(str(row.get("candidate_confidence") or "")), str(row.get("pair_scope") or ""), str(row.get("category") or ""), str(row.get("left_title") or ""), str(row.get("right_title") or ""), int(row.get("candidate_index") or 0)))


def build_pair_rows_from_candidates(candidate_rows: list[dict]) -> list[dict]:
    pair_map: dict[str, dict] = {}
    for row in candidate_rows:
        pair_key = "|".join([str(row.get("pair_scope") or ""), str(row.get("category") or ""), str(row.get("left_title") or ""), str(row.get("right_title") or "")])
        if pair_key in pair_map:
            continue
        pair_map[pair_key] = {
            "pair_scope": row.get("pair_scope", ""),
            "category": row.get("category", ""),
            "left_category": row.get("left_category", ""),
            "right_category": row.get("right_category", ""),
            "left_title": row.get("left_title", ""),
            "right_title": row.get("right_title", ""),
            "left_primary_department": row.get("left_primary_department", ""),
            "right_primary_department": row.get("right_primary_department", ""),
            "left_policy_level": row.get("left_policy_level", ""),
            "right_policy_level": row.get("right_policy_level", ""),
            "possible_conflict": True,
            "confidence": row.get("candidate_confidence", ""),
        }
    return list(pair_map.values())


# 输出 Excel 文件。
def write_excel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


# 按组内/组间统计候选数。
def count_candidates_by_scope(candidate_rows: list[dict]) -> dict[str, int]:
    counts = {"intra": 0, "inter": 0, "unknown": 0}
    for row in candidate_rows:
        scope = str(row.get("pair_scope") or "").strip()
        if scope in {"intra", "inter"}:
            counts[scope] += 1
        else:
            counts["unknown"] += 1
    return counts


# 构造给 LLM 的首页统计数据。
def build_overview_metrics(batch_stats: dict[str, Any], pair_rows: list[dict], candidate_rows: list[dict], input_xlsx_paths: list[str], input_jsonl_paths: list[str]) -> dict[str, Any]:
    scope_counts = count_candidates_by_scope(candidate_rows)
    return {
        **batch_stats,
        "report_pair_count": len(pair_rows),
        "report_candidate_count": len(candidate_rows),
        "intra_candidate_count": scope_counts["intra"],
        "inter_candidate_count": scope_counts["inter"],
        "unknown_scope_candidate_count": scope_counts["unknown"],
        "input_files": [*input_xlsx_paths, *input_jsonl_paths],
        "invalid_rule_definition": "无效规则指背景说明、制定目的、适用范围说明、定义解释、章节标题、过渡性描述、文件自身解释/修订/生效/废止等不直接形成具体管理约束的内容。",
    }


# 生成交给 LLM 改写的明确草稿。
def build_overview_material(metrics: dict[str, Any]) -> dict[str, Any]:
    input_draft = (
        f"本报告基于本批次 {metrics.get('document_count', 0)} 份制度文件生成，"
        f"覆盖 {metrics.get('category_count', 0)} 个制度分类；系统共抽取 {metrics.get('clause_count', 0)} 条制度条款，"
        f"其中筛选出 {metrics.get('valid_rule_count', 0)} 条有效规则，识别出 {metrics.get('invalid_rule_count', 0)} 条无效规则，"
        f"并为 {metrics.get('document_summary_count', 0)} 份制度生成了摘要信息。"
        f"{metrics.get('invalid_rule_definition', '')}"
    )
    method_steps = [
        {
            "step": "制度原文识别与条款切分",
            "input": f"{metrics.get('document_count', 0)} 份制度文件",
            "process": "识别制度正文，并将正文切分为可用于后续分析的制度条款。",
            "output": f"{metrics.get('clause_count', 0)} 条制度条款",
        },
        {
            "step": "制度目录分类映射",
            "input": f"{metrics.get('document_count', 0)} 份制度文件和制度目录分类信息",
            "process": "将每份制度匹配到对应的目录分类，便于后续按同类制度和相关制度进行比较。",
            "output": f"{metrics.get('mapped_document_count', 0)} 份制度完成分类映射，覆盖 {metrics.get('category_count', 0)} 个分类",
        },
        {
            "step": "有效规则筛选",
            "input": f"{metrics.get('clause_count', 0)} 条制度条款",
            "process": "从条款中筛选真正形成管理约束的规则，剔除背景说明、制定目的、定义解释、章节标题、文件自身解释修订生效废止等不直接形成约束的内容。",
            "output": f"{metrics.get('valid_rule_count', 0)} 条有效规则、{metrics.get('invalid_rule_count', 0)} 条无效规则",
        },
        {
            "step": "制度摘要补充",
            "input": f"{metrics.get('document_count', 0)} 份制度原文和有效规则结果",
            "process": "为制度补充目的、适用范围、主要责任主体、管理对象和关键流程等摘要信息，用于辅助判断条款是否属于同一业务语境。",
            "output": f"{metrics.get('document_summary_count', 0)} 份制度摘要",
        },
        {
            "step": "组内与组间冲突识别",
            "input": "有效规则、制度摘要和制度分类结果",
            "process": "分别比较同一分类内的制度，以及不同但存在业务关联的分类之间的制度，识别可能存在冲突的条款候选。",
            "output": f"组内初步候选 {metrics.get('intra_original_candidate_count', 0)} 条，组间初步候选 {metrics.get('inter_original_candidate_count', 0)} 条",
        },
        {
            "step": "严格复核与报告合并",
            "input": "组内和组间的冲突候选",
            "process": "逐条复核候选是否属于同一对象、同一事项、同一管理动作或权限，并判断是否存在明确不一致；不满足条件的候选被剔除。",
            "output": f"组内保留 {metrics.get('intra_strict_candidate_count', 0)} 条、剔除 {metrics.get('intra_rejected_candidate_count', 0)} 条；组间保留 {metrics.get('inter_strict_candidate_count', 0)} 条、剔除 {metrics.get('inter_rejected_candidate_count', 0)} 条",
        },
    ]
    output_draft = (
        f"本报告汇总展示严格复核通过的制度冲突候选，包括涉及制度、条款内容、冲突类型、判断理由和人工复核栏位。"
        f"本次合并结果共涉及 {metrics.get('report_pair_count', 0)} 个制度对，形成 {metrics.get('report_candidate_count', 0)} 条冲突候选；"
        f"其中组内候选 {metrics.get('intra_candidate_count', 0)} 条，组间候选 {metrics.get('inter_candidate_count', 0)} 条。"
    )
    return {
        "draft": {
            "input_data": input_draft,
            "method_requirement": "请把以下步骤整合为一段简洁、有总结性的实现原理说明。不要写成流水账，不要逐条照抄；需要体现关键输入、处理动作、产出数字，以及这些步骤如何共同支撑冲突识别结果。",
            "method_steps": method_steps,
            "output_result": output_draft,
        },
        "metrics": metrics,
    }


def build_template_overview(material: dict[str, Any]) -> dict[str, str]:
    draft = material.get("draft") if isinstance(material.get("draft"), dict) else {}
    steps = draft.get("method_steps") if isinstance(draft.get("method_steps"), list) else []
    method_parts = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        method_parts.append(f"{step.get('step', '')}：{step.get('process', '')}产出{step.get('output', '')}。")
    return {
        "input_data": str(draft.get("input_data") or "").strip(),
        "method": "".join(method_parts).strip(),
        "output_result": str(draft.get("output_result") or "").strip(),
    }


# 读取模型消息字段。
def message_field(message: Any, field: str) -> Any:
    if isinstance(message, dict):
        return message.get(field)
    value = getattr(message, field, None)
    if value is not None:
        return value
    if hasattr(message, "model_dump"):
        try:
            dumped = message.model_dump()
            if isinstance(dumped, dict):
                return dumped.get(field)
        except Exception:
            return None
    return None


# 兼容不同 SDK 的 content 结构。
def normalize_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("value")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    return "" if value is None else str(value)


# 提取模型返回正文，兼容 reasoning 字段。
def extract_message_text(resp: Any) -> str:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    if not message:
        return ""
    content = normalize_message_content(message_field(message, "content")).strip()
    if content:
        return content
    for field in ("reasoning_content", "reasoning", "analysis", "thoughts", "think"):
        fallback = normalize_message_content(message_field(message, field)).strip()
        if fallback:
            return fallback
    return ""


# 从文本中提取 JSON 对象。
def extract_json_fragment(text: str) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    start = source.find("{")
    if start < 0:
        return ""
    for end in range(len(source), start, -1):
        candidate = source[start:end].strip()
        if not candidate or candidate[-1] != "}":
            continue
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            continue
    return ""


# 调用 LLM 改写首页三段说明。
async def generate_llm_overview(material: dict[str, Any], model: str, thinking_extra_body: dict[str, Any] | None) -> dict[str, str]:
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_conflict_report_overview")
    messages = [
        {"role": "system", "content": OVERVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(material, ensure_ascii=False, indent=2)},
    ]
    resp = await llm.chat(messages=messages, model=model, extra_body=thinking_extra_body, temperature=0.1)
    fragment = extract_json_fragment(extract_message_text(resp))
    if not fragment:
        raise ValueError("报告首页 LLM 返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("报告首页 LLM 返回不是 JSON 对象")
    return {
        "input_data": str(payload.get("input_data") or "").strip(),
        "method": str(payload.get("method") or "").strip(),
        "output_result": str(payload.get("output_result") or "").strip(),
    }


# 为 Word 正文增加一个加粗标签片段。
def add_labeled_run(paragraph: Any, label: str, value: str) -> None:
    run = paragraph.add_run(label)
    run.bold = True
    paragraph.add_run(value)


# 生成报告首页说明。
def write_report_overview(document: Document, overview: dict[str, str]) -> None:
    document.add_heading("一、报告说明", level=1)
    input_paragraph = document.add_paragraph()
    add_labeled_run(input_paragraph, "输入数据：", str(overview.get("input_data") or "").strip())
    method_paragraph = document.add_paragraph()
    add_labeled_run(method_paragraph, "实现方式：", str(overview.get("method") or "").strip())
    output_paragraph = document.add_paragraph()
    add_labeled_run(output_paragraph, "输出结果：", str(overview.get("output_result") or "").strip())


# 生成 Word 报告。
def write_docx(path: Path, candidate_rows: list[dict], overview: dict[str, str]) -> None:
    document = Document()
    title = document.add_heading("制度冲突高置信候选报告", level=0)
    for run in title.runs:
        run.font.size = Pt(16)
    write_report_overview(document, overview)
    document.add_heading("二、冲突候选明细", level=1)
    for index, row in enumerate(candidate_rows, start=1):
        document.add_heading(f"{index}. {row.get('left_title', '')} vs {row.get('right_title', '')}", level=2)
        meta = document.add_paragraph()
        add_labeled_run(meta, "分类：", f"{row.get('category', '')}\n")
        add_labeled_run(meta, "左侧主责部门：", f"{row.get('left_primary_department', '')}\n")
        add_labeled_run(meta, "右侧主责部门：", f"{row.get('right_primary_department', '')}\n")
        add_labeled_run(meta, "左侧制度级别：", f"{row.get('left_policy_level', '')}\n")
        add_labeled_run(meta, "右侧制度级别：", f"{row.get('right_policy_level', '')}\n")
        add_labeled_run(meta, "初筛置信度：", f"{row.get('pair_confidence', '')}\n")
        add_labeled_run(meta, "二筛候选置信度：", f"{row.get('candidate_confidence', '')}")

        paragraph = document.add_paragraph()
        add_labeled_run(paragraph, "条款：", f"制度《{row.get('left_title', '')}》的{row.get('left_article_no', '')}")
        paragraph.add_run(f"，内容是“{row.get('left_text', '')}”；")
        paragraph.add_run(f"制度《{row.get('right_title', '')}》的{row.get('right_article_no', '')}")
        paragraph.add_run(f"，内容是“{row.get('right_text', '')}”。")

        conflict = document.add_paragraph()
        add_labeled_run(conflict, "冲突类型：", str(row.get("conflict_type", "")).strip())

        reason = document.add_paragraph()
        add_labeled_run(reason, "理由：", str(row.get("candidate_reason", "")).strip())

        summary = document.add_paragraph()
        add_labeled_run(summary, "一句话描述：", str(row.get("formatted_sentence", "")).strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)


# 解析可用的 soffice 路径。
def resolve_soffice_bin(soffice_bin: str) -> str:
    explicit = str(soffice_bin or "").strip()
    if explicit:
        return explicit
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/soffice", "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice"):
        if Path(candidate).is_file():
            return candidate
    return ""


# 调用 soffice 将 docx 转为 pdf。
def convert_docx_to_pdf(docx_path: Path, output_dir: Path, soffice_bin: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            soffice_bin,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(docx_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return output_dir / f"{docx_path.stem}.pdf"


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.input_xlsx and not args.input_jsonl:
        raise ValueError("至少需要传入一个 --input-xlsx 或 --input-jsonl")
    pair_rows, candidate_rows = build_report_rows_from_inputs(list(args.input_xlsx), list(args.input_jsonl))
    candidate_rows = filter_and_sort_candidate_rows(candidate_rows, str(args.candidate_confidence))
    pair_rows = build_pair_rows_from_candidates(candidate_rows)
    batch_stats = collect_batch_stats(Path(args.batch_output_dir).expanduser().resolve()) if str(args.batch_output_dir or "").strip() else {}
    metrics = build_overview_metrics(batch_stats, pair_rows, candidate_rows, list(args.input_xlsx), list(args.input_jsonl))
    overview_material = build_overview_material(metrics)
    if args.overview_from_template:
        overview = build_template_overview(overview_material)
    else:
        model = str(args.model or "").strip() or get_analysis_model()
        thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
        overview = await generate_llm_overview(overview_material, model, thinking_extra_body)

    pair_excel = output_dir / f"{args.output_prefix}_pairs.xlsx"
    candidate_excel = output_dir / f"{args.output_prefix}_candidates.xlsx"
    docx_path = output_dir / f"{args.output_prefix}.docx"

    write_excel(pair_excel, pair_rows)
    write_excel(candidate_excel, candidate_rows)
    write_docx(docx_path, candidate_rows, overview)

    pdf_path = None
    if not args.skip_pdf:
        soffice_bin = resolve_soffice_bin(args.soffice_bin)
        if soffice_bin:
            pdf_path = convert_docx_to_pdf(docx_path, output_dir, soffice_bin)
        else:
            print("pdf skipped: current environment has no LibreOffice soffice; xlsx/docx were generated")

    print(f"pair excel: {pair_excel}")
    print(f"candidate excel: {candidate_excel}")
    print(f"docx: {docx_path}")
    if pdf_path:
        print(f"pdf: {pdf_path}")
    print(f"pair count: {len(pair_rows)}")
    print(f"candidate count: {len(candidate_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
