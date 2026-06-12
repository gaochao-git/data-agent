from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient, ModelConfig
from apps.complaint_app.core.model_config import get_analysis_model
from utils.llm_thinking import build_thinking_extra_body
from utils.logger import get_logger

from outline_data_store import load_merged_outline_rows
from outline_data_store import read_merged_outline_rows


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
MAPPED_META_FIELDS = ["版块", "领域", "制度文号", "制度级别", "发布时间", "主责部门"]
SYSTEM_PROMPT = """你正在做制度抽取结果与制度目录主数据的映射。

你会拿到：
1. 一条 documents.jsonl 中的制度抽取记录
2. 整个 merged_outline.json 中的制度主数据列表

你的任务是判断：
这条 documents 记录最可能对应哪一条 outline 记录。

判断原则：
1. 优先综合制度名称、制度文号、文件名、发布时间、主责部门等信息整体判断，不要只看单一字段。
2. documents.title 可能是“制度文号：制度名称”的拼接形式，outline 的制度名称可能只保留正式名称。
3. 同一个制度文号可能对应多条制度，不能仅凭制度文号直接下结论。
4. 如果候选里存在唯一明显匹配，输出 matched。
5. 如果有多个候选都像，无法唯一确定，输出 ambiguous。
6. 如果这些候选都不像，输出 unmatched。
7. 只输出 JSON，不要输出解释文字，不要输出 Markdown 代码块。

输出格式：
{
  "match_status": "matched",
  "matched_outline_index": 3,
  "confidence": "high",
  "reason": "documents 标题中的制度文号与候选一致，且制度名称主体高度一致。"
}

如果无法唯一确定：
{
  "match_status": "ambiguous",
  "matched_outline_index": 0,
  "confidence": "medium",
  "reason": "有多个候选都可能匹配，建议人工确认。"
}

如果未匹配：
{
  "match_status": "unmatched",
  "matched_outline_index": 0,
  "confidence": "low",
  "reason": "候选中未发现可信匹配。"
}

match_status 只能取值：matched、ambiguous、unmatched
confidence 只能取值：high、medium、low"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过大模型将 documents.jsonl 映射到 merged_outline.json。")
    parser.add_argument("--documents-jsonl", required=True, help="documents.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--merged-outline-json", default="", help="显式指定 merged_outline.json 路径；不传则读取环境变量或默认 mock")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 JSON 时的业务重试次数，默认 3")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条 documents，0 表示全部")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    return parser


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        source = str(line or "").strip()
        if not source:
            continue
        rows.append(json.loads(source))
    return rows


def write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_excel(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False)


def serialize_outline_rows(outline_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for index, row in enumerate(outline_rows, start=1):
        payload.append(
            {
                "outline_index": index,
                "制度名称": str(row.get("制度名称") or "").strip(),
                "制度文号": str(row.get("制度文号") or "").strip(),
                "版块": str(row.get("版块") or "").strip(),
                "领域": str(row.get("领域") or "").strip(),
                "制度级别": str(row.get("制度级别") or "").strip(),
                "发布时间": str(row.get("发布时间") or row.get("sheet1_发布时间") or row.get("sheet2_发布时间") or "").strip(),
                "主责部门": str(row.get("主责部门") or row.get("sheet1_主责部门") or row.get("sheet2_主责部门") or "").strip(),
            }
        )
    return payload


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
        except Exception as err:
            logger.warning("message.model_dump 读取字段失败 field=%s err=%s", field, err)
            return None
    return None


def normalize_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
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
            logger.warning("documents-outline 映射 content 为空，回退字段=%s", field)
            return fallback
    return ""


def parse_json_payload(text: str) -> dict[str, Any]:
    source = str(text or "").strip()
    lower_source = source.lower()
    if lower_source.startswith("```json"):
        source = source[7:].lstrip()
    if source.endswith("```"):
        source = source[:-3].rstrip()
    payload = json.loads(source)
    if not isinstance(payload, dict):
        raise ValueError("模型返回顶层必须是 JSON 对象")
    match_status = str(payload.get("match_status") or "").strip()
    if match_status not in {"matched", "ambiguous", "unmatched"}:
        raise ValueError("模型返回缺少合法的 match_status")
    confidence = str(payload.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("模型返回缺少合法的 confidence")
    matched_outline_index = int(payload.get("matched_outline_index", 0) or 0)
    return {
        "match_status": match_status,
        "matched_outline_index": matched_outline_index,
        "confidence": confidence,
        "reason": str(payload.get("reason") or "").strip(),
    }


def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


def build_user_prompt(document: dict[str, Any], outline_rows: list[dict[str, Any]]) -> str:
    payload = {
        "document": {
            "doc_id": str(document.get("doc_id") or "").strip(),
            "category": str(document.get("category") or "").strip(),
            "title": str(document.get("title") or "").strip(),
            "file_name": str(document.get("file_name") or "").strip(),
            "file_path": str(document.get("file_path") or "").strip(),
        },
        "outline_rows": serialize_outline_rows(outline_rows),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def find_outline_row_by_index(outline_rows: list[dict[str, Any]], outline_index: int) -> dict[str, Any] | None:
    if outline_index <= 0 or outline_index > len(outline_rows):
        return None
    return outline_rows[outline_index - 1]


# 调用大模型为单条 documents 决定最佳 outline 映射。
async def map_single_document(llm: AsyncLLMClient, document: dict[str, Any], outline_rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(document, outline_rows)}]
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("documents-outline 映射模型返回 doc_id=%s attempt=%s/%s content=%s", document.get("doc_id"), attempt, attempts, preview_text(content))
            try:
                payload = parse_json_payload(content)
                matched_outline_row = find_outline_row_by_index(outline_rows, int(payload["matched_outline_index"]))
                return build_mapping_result(document, outline_rows, payload, matched_outline_row)
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("documents-outline 映射解析失败 doc_id=%s attempt=%s/%s err=%s", document.get("doc_id"), attempt, attempts, last_error)
        raise ValueError(f"documents-outline 映射失败 doc_id={document.get('doc_id')}: {last_error}; 原始输出: {last_content}")


def build_mapping_result(document: dict[str, Any], outline_rows: list[dict[str, Any]], payload: dict[str, Any], matched_outline_row: dict[str, Any] | None) -> dict[str, Any]:
    result = {
        "doc_id": str(document.get("doc_id") or "").strip(),
        "documents_title": str(document.get("title") or "").strip(),
        "file_name": str(document.get("file_name") or "").strip(),
        "file_path": str(document.get("file_path") or "").strip(),
        "documents_category": str(document.get("category") or "").strip(),
        "match_status": str(payload.get("match_status") or "").strip(),
        "confidence": str(payload.get("confidence") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
        "matched_outline_index": int(payload.get("matched_outline_index", 0) or 0),
        "outline_row_count": len(outline_rows),
    }
    if matched_outline_row is None:
        return result
    source_row = dict(matched_outline_row)
    result["matched_outline_title"] = str(source_row.get("制度名称") or "").strip()
    for field in MAPPED_META_FIELDS:
        value = str(source_row.get(field) or "").strip()
        if not value:
            if field == "发布时间":
                value = str(source_row.get("sheet1_发布时间") or source_row.get("sheet2_发布时间") or "").strip()
            elif field == "主责部门":
                value = str(source_row.get("sheet1_主责部门") or source_row.get("sheet2_主责部门") or "").strip()
        result[field] = value
    result["source_sheets"] = list(source_row.get("source_sheets") or [])
    return result


def normalize_compare_key(value: Any) -> str:
    return "".join(str(value or "").split())


def apply_mapping_to_documents(documents: list[dict[str, Any]], mapping_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping_by_doc_id = {str(row.get("doc_id") or "").strip(): row for row in mapping_rows}
    mapped_documents: list[dict[str, Any]] = []
    for document in documents:
        item = dict(document)
        mapping_row = mapping_by_doc_id.get(str(document.get("doc_id") or "").strip(), {})
        item["outline_match_status"] = str(mapping_row.get("match_status") or "").strip()
        item["outline_match_confidence"] = str(mapping_row.get("confidence") or "").strip()
        item["outline_match_reason"] = str(mapping_row.get("reason") or "").strip()
        item["outline_title"] = str(mapping_row.get("matched_outline_title") or "").strip()
        for field in MAPPED_META_FIELDS:
            if str(mapping_row.get(field) or "").strip():
                item[field] = normalize_compare_key(mapping_row.get(field)) if field in {"版块", "领域"} else str(mapping_row.get(field) or "").strip()
        if normalize_compare_key(mapping_row.get("版块")):
            item["category"] = normalize_compare_key(mapping_row.get("版块"))
        elif normalize_compare_key(mapping_row.get("领域")):
            item["category"] = normalize_compare_key(mapping_row.get("领域"))
        mapped_documents.append(item)
    return mapped_documents


def build_summary(mapping_rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"total": len(mapping_rows), "matched": 0, "ambiguous": 0, "unmatched": 0}
    for row in mapping_rows:
        status = str(row.get("match_status") or "").strip()
        if status in summary:
            summary[status] += 1
    return summary


async def async_main() -> int:
    args = build_parser().parse_args()
    documents_jsonl = Path(args.documents_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = load_jsonl_rows(documents_jsonl)
    if int(args.limit) > 0:
        documents = documents[: int(args.limit)]
    merged_outline_json = Path(str(args.merged_outline_json or "").strip()).expanduser().resolve() if str(args.merged_outline_json or "").strip() else None
    outline_rows = read_merged_outline_rows(merged_outline_json) if merged_outline_json else load_merged_outline_rows()
    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_document_outline_mapper")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    logger.info("开始 documents-outline 映射 documents=%s outline_rows=%s model=%s merged_outline_json=%s thinking_extra_body=%s", len(documents), len(outline_rows), model, merged_outline_json or "env_or_default", thinking_extra_body)
    tasks = [
        asyncio.create_task(
            map_single_document(
                llm,
                document,
                outline_rows,
                model,
                thinking_extra_body,
                int(args.business_retries),
                sem,
            )
        )
        for document in documents
    ]
    mapping_rows = await asyncio.gather(*tasks)
    mapped_documents = apply_mapping_to_documents(documents, mapping_rows)
    summary = build_summary(mapping_rows)
    mapping_jsonl = output_dir / "document_outline_mapping.jsonl"
    mapping_xlsx = output_dir / "document_outline_mapping.xlsx"
    mapped_documents_jsonl = output_dir / "documents_mapped.jsonl"
    summary_json = output_dir / "document_outline_mapping_summary.json"
    write_jsonl_rows(mapping_jsonl, mapping_rows)
    write_excel(mapping_xlsx, mapping_rows)
    write_jsonl_rows(mapped_documents_jsonl, mapped_documents)
    write_json(summary_json, summary)
    print(f"mapping jsonl: {mapping_jsonl}")
    print(f"mapping xlsx: {mapping_xlsx}")
    print(f"mapped documents: {mapped_documents_jsonl}")
    print(f"summary: {summary_json}")
    print(f"matched={summary['matched']} ambiguous={summary['ambiguous']} unmatched={summary['unmatched']}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
