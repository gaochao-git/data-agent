from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

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
from apps.complaint_app.tools.zhidu_clause_extract.progress_utils import collect_doc_ids_from_member_mentions, gather_with_doc_progress


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
DEFAULT_MAX_GROUPS_PER_BATCH = 50

TERM_ENRICH_SYSTEM_PROMPT = """你正在做制度条款语义概念的“术语增强”。

你会收到一批已经完成归并与概念筛选的概念组。你的任务是为每个概念组补充术语语义信息。

请对每个概念组输出：
1. definition：一句简短定义，说明该概念在制度语境中指什么
2. broader_terms：上位概念列表；如果没有明确上位概念，输出空数组
3. narrower_terms：下位概念列表；如果当前输入无法支持，不要猜，输出空数组
4. related_terms：相关概念列表；必须是相关但不同义的概念，不要把 aliases 重复放进 related_terms

要求：
1. 只基于输入里的 canonical_name、aliases、sample_contexts 做判断，不要臆造业务知识。
2. broader_terms / narrower_terms / related_terms 都必须是简短、稳定、可复用的名词短语。
3. 如果概念信息不足，definition 可以保守，关系项可以输出空数组。
4. 不要把同义词、别名当成 broader_terms / narrower_terms / related_terms。
5. 必须覆盖输入中的每个 normalization_group_id。
6. 只输出 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "items": [
    {
      "normalization_group_id": "",
      "definition": "",
      "broader_terms": [],
      "narrower_terms": [],
      "related_terms": []
    }
  ]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对已归并并完成概念筛选的概念组做术语增强。")
    parser.add_argument("--normalization-jsonl", required=True, help="clause_semantics_normalization.jsonl 路径")
    parser.add_argument("--concepts-jsonl", required=True, help="clause_semantics_concepts.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 JSON 时的业务重试次数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--max-groups-per-batch", type=int, default=DEFAULT_MAX_GROUPS_PER_BATCH, help="每批概念组数，默认 50")
    return parser


# 加载环境变量，便于直接读取 server/.env。
def load_env_files() -> None:
    if load_dotenv is None:
        return
    load_dotenv()
    server_env = SERVER_ROOT / ".env"
    if server_env.is_file():
        load_dotenv(server_env, override=False)


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


# 从模型响应中提取最终文本，content 为空时回退 reasoning 字段。
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
            logger.warning("术语增强 content 为空，回退字段=%s", field)
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
    return payload


def preview_text(text: str, limit: int = MAX_LOG_PREVIEW) -> str:
    source = str(text or "").strip()
    if len(source) <= limit:
        return source
    return f"{source[:limit]}... [truncated {len(source) - limit} chars]"


def split_list(items: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    size = max(1, int(batch_size))
    return [items[index:index + size] for index in range(0, len(items), size)]


def index_rows_by_group_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("normalization_group_id") or "").strip(): row for row in rows if str(row.get("normalization_group_id") or "").strip()}


def collect_aliases(row: dict[str, Any], limit: int = 20) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    canonical_name = str(row.get("canonical_name") or "").strip()
    for item in list(row.get("member_mentions") or []):
        mention_text = str(item.get("mention_text") or "").strip()
        if not mention_text or mention_text == canonical_name or mention_text in seen:
            continue
        seen.add(mention_text)
        aliases.append(mention_text)
        if len(aliases) >= limit:
            break
    return aliases


def collect_sample_contexts(row: dict[str, Any], limit: int = 4) -> list[dict[str, str]]:
    contexts: list[dict[str, str]] = []
    for item in list(row.get("member_mentions") or [])[:limit]:
        contexts.append(
            {
                "doc_id": str(item.get("doc_id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "article_no": str(item.get("article_no") or "").strip(),
                "article_text": str(item.get("article_text") or "").strip(),
            }
        )
    return contexts


def build_term_input_rows(normalization_rows: list[dict[str, Any]], concept_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalization_map = index_rows_by_group_id(normalization_rows)
    rows: list[dict[str, Any]] = []
    for concept_row in concept_rows:
        normalization_group_id = str(concept_row.get("normalization_group_id") or "").strip()
        concept_status = str(concept_row.get("concept_status") or "").strip()
        if not normalization_group_id or concept_status == "noise_concept":
            continue
        source_row = normalization_map.get(normalization_group_id) or concept_row
        row = dict(concept_row)
        row["member_mentions"] = list(source_row.get("member_mentions") or concept_row.get("member_mentions") or [])
        row["reason"] = str(source_row.get("reason") or concept_row.get("reason") or "").strip()
        rows.append(row)
    return rows


def build_user_prompt(rows: list[dict[str, Any]]) -> str:
    payload = {
        "concept_groups": [
            {
                "normalization_group_id": str(row.get("normalization_group_id") or "").strip(),
                "mention_category": str(row.get("mention_category") or "").strip(),
                "canonical_name": str(row.get("canonical_name") or "").strip(),
                "aliases": collect_aliases(row),
                "concept_status": str(row.get("concept_status") or "").strip(),
                "ontology_role": str(row.get("ontology_role") or "").strip(),
                "normalization_reason": str(row.get("reason") or "").strip(),
                "screen_reason": str(row.get("screen_reason") or "").strip(),
                "sample_contexts": collect_sample_contexts(row),
            }
            for row in rows
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_term_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def normalize_term_payload(payload: dict[str, Any], valid_group_ids: set[str]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("术语增强结果缺少 items 数组")
    normalized: list[dict[str, Any]] = []
    covered_group_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        normalization_group_id = str(item.get("normalization_group_id") or "").strip()
        if normalization_group_id not in valid_group_ids:
            continue
        normalized.append(
            {
                "normalization_group_id": normalization_group_id,
                "definition": str(item.get("definition") or "").strip(),
                "broader_terms": normalize_term_list(item.get("broader_terms")),
                "narrower_terms": normalize_term_list(item.get("narrower_terms")),
                "related_terms": normalize_term_list(item.get("related_terms")),
            }
        )
        covered_group_ids.add(normalization_group_id)
    missing_group_ids = valid_group_ids - covered_group_ids
    if missing_group_ids:
        raise ValueError(f"术语增强结果缺少 group_id: {sorted(missing_group_ids)}")
    return normalized


# 调用大模型做单批术语增强。
async def enrich_term_batch(llm: AsyncLLMClient, rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    async with sem:
        logger.info("开始术语增强 batch_group_count=%s", len(rows))
        messages = [{"role": "system", "content": TERM_ENRICH_SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(rows)}]
        valid_group_ids = {str(row.get("normalization_group_id") or "").strip() for row in rows}
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("术语增强模型返回 batch_group_count=%s attempt=%s/%s content=%s", len(rows), attempt, attempts, preview_text(content))
            try:
                result_rows = normalize_term_payload(parse_json_payload(content), valid_group_ids)
                logger.info("完成术语增强 batch_group_count=%s", len(rows))
                return result_rows
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("术语增强解析失败 batch_group_count=%s attempt=%s/%s err=%s", len(rows), attempt, attempts, last_error)
        raise ValueError(f"术语增强失败: {last_error}; 原始输出: {last_content}")


def merge_term_results(input_rows: list[dict[str, Any]], term_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result_map = {str(row.get("normalization_group_id") or "").strip(): row for row in term_rows}
    merged_rows: list[dict[str, Any]] = []
    for row in input_rows:
        normalization_group_id = str(row.get("normalization_group_id") or "").strip()
        term_row = result_map.get(normalization_group_id)
        if not term_row:
            raise ValueError(f"术语增强缺少 normalization_group_id={normalization_group_id}")
        merged_rows.append(
            {
                "normalization_group_id": normalization_group_id,
                "mention_category": str(row.get("mention_category") or "").strip(),
                "canonical_name": str(row.get("canonical_name") or "").strip(),
                "aliases": collect_aliases(row),
                "concept_status": str(row.get("concept_status") or "").strip(),
                "ontology_role": str(row.get("ontology_role") or "").strip(),
                "definition": str(term_row.get("definition") or "").strip(),
                "broader_terms": list(term_row.get("broader_terms") or []),
                "narrower_terms": list(term_row.get("narrower_terms") or []),
                "related_terms": list(term_row.get("related_terms") or []),
                "normalization_reason": str(row.get("reason") or "").strip(),
                "screen_reason": str(row.get("screen_reason") or "").strip(),
            }
        )
    return merged_rows


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mention_category_counts: dict[str, int] = defaultdict(int)
    concept_status_counts: dict[str, int] = defaultdict(int)
    ontology_role_counts: dict[str, int] = defaultdict(int)
    definition_filled_count = 0
    broader_term_count = 0
    narrower_term_count = 0
    related_term_count = 0
    alias_count = 0
    for row in rows:
        mention_category_counts[str(row.get("mention_category") or "").strip()] += 1
        concept_status_counts[str(row.get("concept_status") or "").strip()] += 1
        ontology_role_counts[str(row.get("ontology_role") or "").strip()] += 1
        if str(row.get("definition") or "").strip():
            definition_filled_count += 1
        broader_term_count += len(list(row.get("broader_terms") or []))
        narrower_term_count += len(list(row.get("narrower_terms") or []))
        related_term_count += len(list(row.get("related_terms") or []))
        alias_count += len(list(row.get("aliases") or []))
    return {
        "term_count": len(rows),
        "definition_filled_count": definition_filled_count,
        "alias_count": alias_count,
        "broader_term_count": broader_term_count,
        "narrower_term_count": narrower_term_count,
        "related_term_count": related_term_count,
        "mention_category_counts": dict(mention_category_counts),
        "concept_status_counts": dict(concept_status_counts),
        "ontology_role_counts": dict(ontology_role_counts),
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    normalization_jsonl = Path(args.normalization_jsonl).expanduser().resolve()
    concepts_jsonl = Path(args.concepts_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    normalization_rows = load_jsonl_rows(normalization_jsonl)
    concept_rows = load_jsonl_rows(concepts_jsonl)
    if not normalization_rows:
        raise ValueError("归并结果为空，无法做术语增强")
    if not concept_rows:
        raise ValueError("概念筛选结果为空，无法做术语增强")

    term_input_rows = build_term_input_rows(normalization_rows, concept_rows)
    if not term_input_rows:
        raise ValueError("未找到可做术语增强的概念组")

    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_clause_semantics_terms")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    logger.info("开始术语增强 group_count=%s model=%s thinking_extra_body=%s", len(term_input_rows), model, thinking_extra_body)
    batches = split_list(term_input_rows, int(args.max_groups_per_batch))
    total_doc_ids = collect_doc_ids_from_member_mentions(term_input_rows)
    tasks = [
        (
            asyncio.create_task(enrich_term_batch(llm, batch_rows, model, thinking_extra_body, int(args.business_retries), sem)),
            collect_doc_ids_from_member_mentions(batch_rows),
            f"batch={index}/{len(batches)}",
        )
        for index, batch_rows in enumerate(batches, start=1)
    ]
    batch_results = await gather_with_doc_progress("06b", tasks, total_doc_ids)
    term_rows = [item for batch in batch_results for item in batch]
    merged_rows = merge_term_results(term_input_rows, term_rows)

    output_jsonl = output_dir / "clause_semantics_terms.jsonl"
    output_summary = output_dir / "clause_semantics_terms_summary.json"
    write_jsonl_rows(output_jsonl, merged_rows)
    write_json(output_summary, build_summary(merged_rows))
    print(f"terms jsonl: {output_jsonl}")
    print(f"summary: {output_summary}")
    print(f"term_count={len(merged_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
