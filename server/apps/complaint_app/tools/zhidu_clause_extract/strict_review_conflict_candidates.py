from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

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
from utils.logger import get_logger


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000

STRICT_REVIEW_PROMPT = """你正在参与公司内部规章制度冲突分析项目。

你当前的任务是对“已经被初步识别为冲突候选的条款对”做严格复核。

请先判断两条条款是否满足冲突成立的前提：
1. 是否约束同一个业务对象
2. 是否约束同一个事项
3. 是否约束同一项管理动作或权限
4. 是否存在明确不一致、矛盾或无法同时执行的要求

硬性排除规则：
1. 如果两条条款只是不同制度各自管理本文件自身，例如解释权、修订权、生效时间、废止关系、附件更新、发布实施、版本管理，不构成业务规则冲突。
2. 如果两条条款主体、对象、事项、管理动作不同，即使部门名称、审批层级、职责描述看起来不同，也不得判定为冲突。
3. 如果两条条款是上下位规则、一般规则与特殊规则、流程前后衔接、职责分工互补、适用范围不同，不得判定为冲突。
4. 冲突证据必须落到两条具体条款文本，不能只依据制度标题、摘要、部门名称相似或主题相关。

输出严格 JSON，不要输出解释文字，不要输出 Markdown 代码块。

输出格式：
{
  "is_conflict": false,
  "confidence": "low",
  "conflict_type": "",
  "same_object": false,
  "same_matter": false,
  "same_action_or_authority": false,
  "is_file_self_management_clause": true,
  "reason": "两条分别规定各自文件自身解释/修订权限，不是同一业务对象或同一管理事项。"
}

confidence 只能取 high、medium、low。
只有同时满足“同一对象/同一事项/同一管理动作或权限”且要求明确不一致时，is_conflict 才能为 true。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对 high_conflict_clauses.jsonl 做严格复核，压低不同对象/不同事项导致的误报。")
    parser.add_argument("--input-jsonl", required=True, help="high_conflict_clauses.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--effective-rules-jsonl", default="", help="可选：effective_rules_with_summary.jsonl 路径，用于按 doc_id 补充两份制度摘要")
    parser.add_argument("--pair-scope", choices=["", "intra", "inter"], default="", help="只复核指定 pair_scope；空表示全部")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非JSON时的业务重试次数，默认 3")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个制度对，0 表示全部")
    parser.add_argument("--export-medium-only", action="store_true", help="只从已复核的 high_conflict_clauses_strict.jsonl 导出 medium 候选，不重新调用模型")
    parser.add_argument("--export-all-candidates-only", action="store_true", help="只从已复核的 high_conflict_clauses_strict.jsonl 导出全部候选条款级 Excel，不重新调用模型")
    return parser


def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            rows.append(json.loads(text))
    return rows


def filter_rows_by_pair_scope(rows: list[dict[str, Any]], pair_scope: str) -> list[dict[str, Any]]:
    scope = str(pair_scope or "").strip()
    if not scope:
        return rows
    filtered = []
    for row in rows:
        row_scope = str(row.get("pair_scope") or "").strip()
        if not row_scope:
            row_scope = "inter" if "<->" in str(row.get("category") or "") else "intra"
        if row_scope == scope:
            filtered.append(row)
    return filtered


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_excel(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


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
            logger.warning("严格复核 content 为空，回退字段=%s", field)
            return fallback
    return ""


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


def parse_review_payload(text: str) -> dict[str, Any]:
    fragment = extract_json_fragment(text)
    if not fragment:
        raise ValueError("模型返回中未找到 JSON 对象")
    payload = json.loads(fragment)
    if not isinstance(payload, dict):
        raise ValueError("模型返回不是 JSON 对象")
    confidence = str(payload.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    return {
        "is_conflict": bool(payload.get("is_conflict")),
        "confidence": confidence,
        "conflict_type": str(payload.get("conflict_type") or "").strip(),
        "same_object": bool(payload.get("same_object")),
        "same_matter": bool(payload.get("same_matter")),
        "same_action_or_authority": bool(payload.get("same_action_or_authority")),
        "is_file_self_management_clause": bool(payload.get("is_file_self_management_clause")),
        "reason": str(payload.get("reason") or "").strip(),
    }


def build_document_summary_text(document: dict[str, Any]) -> str:
    summary = document.get("document_summary")
    if not isinstance(summary, dict):
        return ""
    parts = []
    for label, key in (("制度目的", "purpose"), ("适用范围", "scope"), ("整体摘要", "summary")):
        value = str(summary.get(key) or "").strip()
        if value:
            parts.append(f"{label}：{value}")
    for label, key in (("主要责任主体", "main_subjects"), ("主要管理对象", "managed_objects"), ("关键流程", "key_processes"), ("风险/控制重点", "risk_points")):
        value = summary.get(key)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item or "").strip()]
            if items:
                parts.append(f"{label}：{'、'.join(items)}")
    return "\n".join(parts).strip()


def load_documents_by_id(path_text: str) -> dict[str, dict[str, Any]]:
    path_value = str(path_text or "").strip()
    if not path_value:
        return {}
    rows = load_jsonl_rows(Path(path_value).expanduser().resolve())
    return {str(row.get("doc_id") or "").strip(): row for row in rows if str(row.get("doc_id") or "").strip()}


def attach_document_summaries(rows: list[dict[str, Any]], documents_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not documents_by_id:
        return rows
    attached = []
    for row in rows:
        item = dict(row)
        left_doc = documents_by_id.get(str(row.get("left_doc_id") or "").strip(), {})
        right_doc = documents_by_id.get(str(row.get("right_doc_id") or "").strip(), {})
        item["left_document_summary"] = build_document_summary_text(left_doc)
        item["right_document_summary"] = build_document_summary_text(right_doc)
        attached.append(item)
    return attached


def build_candidate_prompt(row: dict[str, Any], candidate: dict[str, Any], index: int) -> str:
    return "\n".join(
        [
            f"类别：{row.get('category', '')}",
            f"左侧制度：{row.get('left_title', '')}",
            f"左侧制度摘要（仅辅助判断适用范围/对象/事项，不能作为冲突证据）：{row.get('left_document_summary') or '无'}",
            f"右侧制度：{row.get('right_title', '')}",
            f"右侧制度摘要（仅辅助判断适用范围/对象/事项，不能作为冲突证据）：{row.get('right_document_summary') or '无'}",
            f"制度对初筛置信度：{row.get('pair_confidence', '')}",
            f"制度对初筛原因：{row.get('pair_reason', '')}",
            f"候选序号：{index}",
            f"左侧条款号：{candidate.get('left_article_no', '')}",
            f"左侧条款：{candidate.get('left_text', '')}",
            f"右侧条款号：{candidate.get('right_article_no', '')}",
            f"右侧条款：{candidate.get('right_text', '')}",
            f"原冲突类型：{candidate.get('conflict_type', '')}",
            f"原判断原因：{candidate.get('reason', '')}",
            f"原置信度：{candidate.get('confidence', '')}",
        ]
    )


def build_error_review(error: str) -> dict[str, Any]:
    return {"is_conflict": False, "confidence": "low", "conflict_type": "", "same_object": False, "same_matter": False, "same_action_or_authority": False, "is_file_self_management_clause": False, "reason": error}


async def review_single_candidate(llm: AsyncLLMClient, row: dict[str, Any], candidate: dict[str, Any], index: int, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        messages = [{"role": "system", "content": STRICT_REVIEW_PROMPT}, {"role": "user", "content": build_candidate_prompt(row, candidate, index)}]
        last_error = ""
        last_content = ""
        for attempt in range(1, max(1, int(business_retries)) + 1):
            try:
                resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
                content = extract_message_text(resp)
                last_content = content
                logger.info("严格复核模型返回 left=%s right=%s candidate_index=%s attempt=%s content=%s", row.get("left_doc_id", ""), row.get("right_doc_id", ""), index, attempt, preview_text(content))
                review = parse_review_payload(content)
                reviewed = dict(candidate)
                reviewed["strict_review"] = review
                reviewed["strict_status"] = "ok"
                reviewed["strict_attempt_count"] = attempt
                return reviewed
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(last_content)}"
                logger.warning("严格复核失败 left=%s right=%s candidate_index=%s attempt=%s err=%s", row.get("left_doc_id", ""), row.get("right_doc_id", ""), index, attempt, last_error)
        reviewed = dict(candidate)
        reviewed["strict_review"] = build_error_review(last_error or "unknown_error")
        reviewed["strict_status"] = "error"
        reviewed["strict_error"] = last_error
        return reviewed


def flatten_candidates(candidates: list[dict[str, Any]]) -> str:
    return json.dumps(candidates, ensure_ascii=False, indent=2)


def build_pair_key(row: dict[str, Any]) -> str:
    left = str(row.get("left_doc_id") or "").strip()
    right = str(row.get("right_doc_id") or "").strip()
    return "||".join(sorted([left, right]))


def build_candidate_excel_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    excel_rows = []
    for row in rows:
        for candidate in row.get("strict_conflict_candidates", []):
            review = candidate.get("strict_review", {})
            excel_rows.append(
                {
                    "category": row.get("category", ""),
                    "left_title": row.get("left_title", ""),
                    "right_title": row.get("right_title", ""),
                    "left_primary_department": row.get("left_primary_department", ""),
                    "right_primary_department": row.get("right_primary_department", ""),
                    "left_policy_level": row.get("left_policy_level", ""),
                    "right_policy_level": row.get("right_policy_level", ""),
                    "left_article_no": candidate.get("left_article_no", ""),
                    "left_text": candidate.get("left_text", ""),
                    "right_article_no": candidate.get("right_article_no", ""),
                    "right_text": candidate.get("right_text", ""),
                    "conflict_type": candidate.get("conflict_type", ""),
                    "confidence": candidate.get("confidence", ""),
                    "strict_confidence": review.get("confidence", ""),
                    "strict_conflict_type": review.get("conflict_type", ""),
                    "strict_reason": review.get("reason", ""),
                    "same_object": review.get("same_object", False),
                    "same_matter": review.get("same_matter", False),
                    "same_action_or_authority": review.get("same_action_or_authority", False),
                    "is_file_self_management_clause": review.get("is_file_self_management_clause", False),
                }
            )
    return excel_rows


def build_medium_candidate_excel_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    excel_rows = []
    for row in rows:
        reviewed_candidates = list(row.get("strict_conflict_candidates", [])) + list(row.get("strict_rejected_candidates", []))
        for candidate in reviewed_candidates:
            if str(candidate.get("confidence") or "").strip().lower() != "medium":
                continue
            review = candidate.get("strict_review", {})
            excel_rows.append(
                {
                    "category": row.get("category", ""),
                    "left_title": row.get("left_title", ""),
                    "right_title": row.get("right_title", ""),
                    "left_primary_department": row.get("left_primary_department", ""),
                    "right_primary_department": row.get("right_primary_department", ""),
                    "left_policy_level": row.get("left_policy_level", ""),
                    "right_policy_level": row.get("right_policy_level", ""),
                    "left_article_no": candidate.get("left_article_no", ""),
                    "left_text": candidate.get("left_text", ""),
                    "right_article_no": candidate.get("right_article_no", ""),
                    "right_text": candidate.get("right_text", ""),
                    "conflict_type": candidate.get("conflict_type", ""),
                    "confidence": candidate.get("confidence", ""),
                    "reason": candidate.get("reason", ""),
                    "strict_is_conflict": review.get("is_conflict", False),
                    "strict_confidence": review.get("confidence", ""),
                    "strict_conflict_type": review.get("conflict_type", ""),
                    "strict_reason": review.get("reason", ""),
                    "same_object": review.get("same_object", False),
                    "same_matter": review.get("same_matter", False),
                    "same_action_or_authority": review.get("same_action_or_authority", False),
                    "is_file_self_management_clause": review.get("is_file_self_management_clause", False),
                }
            )
    return excel_rows


def build_all_candidate_excel_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    excel_rows = []
    for row in rows:
        reviewed_candidates = list(row.get("strict_conflict_candidates", [])) + list(row.get("strict_rejected_candidates", []))
        for index, candidate in enumerate(reviewed_candidates, start=1):
            review = candidate.get("strict_review", {})
            excel_rows.append(
                {
                    "pair_scope": row.get("pair_scope", ""),
                    "category": row.get("category", ""),
                    "left_category": row.get("left_category", ""),
                    "right_category": row.get("right_category", ""),
                    "left_doc_id": row.get("left_doc_id", ""),
                    "right_doc_id": row.get("right_doc_id", ""),
                    "left_title": row.get("left_title", ""),
                    "right_title": row.get("right_title", ""),
                    "left_primary_department": row.get("left_primary_department", ""),
                    "right_primary_department": row.get("right_primary_department", ""),
                    "left_policy_level": row.get("left_policy_level", ""),
                    "right_policy_level": row.get("right_policy_level", ""),
                    "pair_confidence": row.get("pair_confidence", ""),
                    "pair_reason": row.get("pair_reason", ""),
                    "candidate_index": index,
                    "left_article_no": candidate.get("left_article_no", ""),
                    "left_text": candidate.get("left_text", ""),
                    "right_article_no": candidate.get("right_article_no", ""),
                    "right_text": candidate.get("right_text", ""),
                    "conflict_type": candidate.get("conflict_type", ""),
                    "reason": candidate.get("reason", ""),
                    "confidence": candidate.get("confidence", ""),
                    "strict_is_conflict": review.get("is_conflict", False),
                    "strict_confidence": review.get("confidence", ""),
                    "strict_conflict_type": review.get("conflict_type", ""),
                    "strict_reason": review.get("reason", ""),
                    "same_object": review.get("same_object", False),
                    "same_matter": review.get("same_matter", False),
                    "same_action_or_authority": review.get("same_action_or_authority", False),
                    "is_file_self_management_clause": review.get("is_file_self_management_clause", False),
                }
            )
    return excel_rows


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pair_count = len(rows)
    original_candidate_count = 0
    strict_candidate_count = 0
    rejected_candidate_count = 0
    confidence_counts: dict[str, int] = {}
    reject_reason_counts = {"file_self_management": 0, "not_same_object": 0, "not_same_matter": 0, "not_same_action_or_authority": 0}
    for row in rows:
        original_candidate_count += int(row.get("candidate_count", 0) or 0)
        strict_candidate_count += int(row.get("strict_candidate_count", 0) or 0)
        rejected = row.get("strict_rejected_candidates", [])
        rejected_candidate_count += len(rejected)
        for candidate in row.get("strict_conflict_candidates", []):
            confidence = str((candidate.get("strict_review") or {}).get("confidence") or "unknown")
            confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        for candidate in rejected:
            review = candidate.get("strict_review") or {}
            if bool(review.get("is_file_self_management_clause")):
                reject_reason_counts["file_self_management"] += 1
            if not bool(review.get("same_object")):
                reject_reason_counts["not_same_object"] += 1
            if not bool(review.get("same_matter")):
                reject_reason_counts["not_same_matter"] += 1
            if not bool(review.get("same_action_or_authority")):
                reject_reason_counts["not_same_action_or_authority"] += 1
    return {
        "pair_count": pair_count,
        "original_candidate_count": original_candidate_count,
        "strict_candidate_count": strict_candidate_count,
        "rejected_candidate_count": rejected_candidate_count,
        "strict_confidence_counts": confidence_counts,
        "reject_reason_counts": reject_reason_counts,
    }


async def review_rows(rows: list[dict[str, Any]], llm: AsyncLLMClient, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    result_rows = []
    for row in rows:
        candidates = list(row.get("conflict_candidates", []))
        tasks = [asyncio.create_task(review_single_candidate(llm, row, candidate, index + 1, model, thinking_extra_body, business_retries, sem)) for index, candidate in enumerate(candidates)]
        reviewed_candidates = await asyncio.gather(*tasks) if tasks else []
        accepted = [candidate for candidate in reviewed_candidates if bool((candidate.get("strict_review") or {}).get("is_conflict"))]
        rejected = [candidate for candidate in reviewed_candidates if not bool((candidate.get("strict_review") or {}).get("is_conflict"))]
        result = dict(row)
        result["strict_conflict_candidates"] = accepted
        result["strict_rejected_candidates"] = rejected
        result["strict_candidate_count"] = len(accepted)
        result["strict_rejected_candidate_count"] = len(rejected)
        result["strict_conflict_candidates_json"] = flatten_candidates(accepted)
        result["strict_rejected_candidates_json"] = flatten_candidates(rejected)
        result_rows.append(result)
    return result_rows


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    input_path = Path(args.input_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_jsonl_rows(input_path)
    rows = filter_rows_by_pair_scope(rows, args.pair_scope)
    if int(args.limit) > 0:
        rows = rows[: int(args.limit)]
    if args.export_medium_only:
        output_path = output_dir / "high_conflict_candidates_medium.xlsx"
        write_excel(output_path, build_medium_candidate_excel_rows(rows))
        print(f"medium candidate excel 输出: {output_path}")
        return 0
    if args.export_all_candidates_only:
        output_path = output_dir / "high_conflict_candidates_all.xlsx"
        write_excel(output_path, build_all_candidate_excel_rows(rows))
        print(f"all candidate excel 输出: {output_path}")
        return 0
    rows = attach_document_summaries(rows, load_documents_by_id(args.effective_rules_jsonl))
    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_strict_conflict_review")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    logger.info("开始严格复核冲突候选 pair_count=%s model=%s", len(rows), model)
    result_rows = await review_rows(rows, llm, model, thinking_extra_body, int(args.business_retries), sem)
    write_jsonl_rows(output_dir / "high_conflict_clauses_strict.jsonl", result_rows)
    write_excel(output_dir / "high_conflict_clauses_strict.xlsx", result_rows)
    write_excel(output_dir / "high_conflict_candidates_strict.xlsx", build_candidate_excel_rows(result_rows))
    write_excel(output_dir / "high_conflict_candidates_medium.xlsx", build_medium_candidate_excel_rows(result_rows))
    write_excel(output_dir / "high_conflict_candidates_all.xlsx", build_all_candidate_excel_rows(result_rows))
    write_json(output_dir / "strict_review_summary.json", build_summary(result_rows))
    print(f"jsonl 输出: {output_dir / 'high_conflict_clauses_strict.jsonl'}")
    print(f"excel 输出: {output_dir / 'high_conflict_clauses_strict.xlsx'}")
    print(f"candidate excel 输出: {output_dir / 'high_conflict_candidates_strict.xlsx'}")
    print(f"medium candidate excel 输出: {output_dir / 'high_conflict_candidates_medium.xlsx'}")
    print(f"summary 输出: {output_dir / 'strict_review_summary.json'}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
