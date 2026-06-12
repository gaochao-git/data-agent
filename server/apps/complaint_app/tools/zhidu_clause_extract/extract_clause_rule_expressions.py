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
from apps.complaint_app.tools.zhidu_clause_extract.progress_utils import collect_doc_ids_from_rows, gather_with_doc_progress


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000

RULE_EXPRESSION_SYSTEM_PROMPT = """你正在做制度条款语义的“规制表达”生成。

你会收到单条条款的原文、原始提及、归并结果和概念筛选结果。你的任务是把这条条款整理成一个或多个稳定的规则表达。

要求：
1. 如果一条条款中存在多个相对独立的规则，应拆成多个 rule expression。
2. `subject`、`actions`、`object`、`condition`、`constraint` 都使用数组；即使当前只有一个元素，也保持数组结构。
3. `subject` 只放规则中承担规范角色的主体。
4. `actions` 只放规则中的核心动作；并列动作应分别保留。
5. `object` 放规则动作作用的对象、受影响对象或被处理对象；如果输入里有 `object_mentions`，应优先参考它。
6. `business_context` 用一句简短短语概括该规则所处的业务语境，不要抄整句原文。
7. `condition` 放规则适用、触发或成立的条件。
8. `constraint` 放规则中的约束性表达，如“应当”“不得”“由……决定”“经……批准”等，不要把明确的动作对象误放进 constraint。
9. `effect` 用一句话概括这条规则希望达到的规范结果，不要照抄原文。
10. `evidence_text` 必须来自该条款原文，可为整条条款或其中相关句子。
11. 不要输出明显噪音概念；优先使用 `concept_status` 为 `core_concept` 或 `context_concept` 的结果。
12. 只输出 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "rule_expressions": [
    {
      "subject": [""],
      "actions": [""],
      "object": [""],
      "business_context": "",
      "condition": [""],
      "constraint": [""],
      "effect": "",
      "evidence_text": ""
    }
  ]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从条款原始提及、归并结果和概念筛选结果中生成规制表达。")
    parser.add_argument("--mentions-jsonl", required=True, help="clause_semantics_mentions.jsonl 路径")
    parser.add_argument("--normalization-jsonl", required=True, help="clause_semantics_normalization.jsonl 路径")
    parser.add_argument("--concepts-jsonl", required=True, help="clause_semantics_concepts.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--doc-id", default="", help="只处理指定 doc_id")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条条款，0 表示全部")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 JSON 时的业务重试次数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
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


def build_failure_row(mention_row: dict[str, Any], error: str, raw_output: str) -> dict[str, Any]:
    return {
        "doc_id": str(mention_row.get("doc_id") or "").strip(),
        "title": str(mention_row.get("title") or "").strip(),
        "article_no": str(mention_row.get("article_no") or "").strip(),
        "error": str(error or "").strip(),
        "raw_output": str(raw_output or "").strip(),
    }


def filter_mention_rows(rows: list[dict[str, Any]], doc_id: str, limit: int) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if doc_id and str(row.get("doc_id") or "").strip() != doc_id:
            continue
        filtered.append(row)
        if limit > 0 and len(filtered) >= limit:
            break
    return filtered


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
            logger.warning("规制表达生成 content 为空，回退字段=%s", field)
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


def unique_str_list(items: list[Any]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return rows


def concept_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    role = str(row.get("ontology_role") or "").strip()
    name = str(row.get("canonical_name") or "").strip()
    return role, name


def build_clause_key(doc_id: str, article_no: str) -> str:
    return f"{doc_id}::{article_no}"


def index_concepts_by_clause(concept_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in concept_rows:
        concept_status = str(row.get("concept_status") or "").strip()
        if concept_status == "noise_concept":
            continue
        for member in list(row.get("member_mentions") or []):
            if not isinstance(member, dict):
                continue
            doc_id = str(member.get("doc_id") or "").strip()
            article_no = str(member.get("article_no") or "").strip()
            if not doc_id or not article_no:
                continue
            clause_key = build_clause_key(doc_id, article_no)
            grouped[clause_key].append(
                {
                    "normalization_group_id": str(row.get("normalization_group_id") or "").strip(),
                    "mention_category": str(row.get("mention_category") or "").strip(),
                    "canonical_name": str(row.get("canonical_name") or "").strip(),
                    "concept_status": concept_status,
                    "ontology_role": str(row.get("ontology_role") or "").strip(),
                    "screen_reason": str(row.get("screen_reason") or "").strip(),
                    "matched_mention_text": str(member.get("mention_text") or "").strip(),
                    "matched_mention_type": str(member.get("mention_type") or "").strip(),
                    "matched_description": str(member.get("description") or "").strip(),
                }
            )
    return grouped


def index_normalization_by_clause(normalization_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalization_rows:
        for member in list(row.get("member_mentions") or []):
            if not isinstance(member, dict):
                continue
            doc_id = str(member.get("doc_id") or "").strip()
            article_no = str(member.get("article_no") or "").strip()
            if not doc_id or not article_no:
                continue
            clause_key = build_clause_key(doc_id, article_no)
            grouped[clause_key].append(
                {
                    "normalization_group_id": str(row.get("normalization_group_id") or "").strip(),
                    "mention_category": str(row.get("mention_category") or "").strip(),
                    "canonical_name": str(row.get("canonical_name") or "").strip(),
                    "reason": str(row.get("reason") or "").strip(),
                    "matched_mention_text": str(member.get("mention_text") or "").strip(),
                }
            )
    return grouped


def build_clause_concepts_payload(concept_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(concept_rows, key=concept_sort_key):
        mention_category = str(row.get("mention_category") or "").strip()
        grouped[mention_category].append(
            {
                "normalization_group_id": str(row.get("normalization_group_id") or "").strip(),
                "canonical_name": str(row.get("canonical_name") or "").strip(),
                "concept_status": str(row.get("concept_status") or "").strip(),
                "ontology_role": str(row.get("ontology_role") or "").strip(),
                "matched_mention_text": str(row.get("matched_mention_text") or "").strip(),
                "matched_mention_type": str(row.get("matched_mention_type") or "").strip(),
                "matched_description": str(row.get("matched_description") or "").strip(),
                "screen_reason": str(row.get("screen_reason") or "").strip(),
            }
        )
    return grouped


def build_clause_normalization_payload(normalization_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in normalization_rows:
        mention_category = str(row.get("mention_category") or "").strip()
        grouped[mention_category].append(
            {
                "normalization_group_id": str(row.get("normalization_group_id") or "").strip(),
                "canonical_name": str(row.get("canonical_name") or "").strip(),
                "matched_mention_text": str(row.get("matched_mention_text") or "").strip(),
                "reason": str(row.get("reason") or "").strip(),
            }
        )
    return grouped


def build_user_prompt(mention_row: dict[str, Any], clause_normalization_rows: list[dict[str, Any]], clause_concept_rows: list[dict[str, Any]]) -> str:
    payload = {
        "document": {
            "doc_id": str(mention_row.get("doc_id") or "").strip(),
            "title": str(mention_row.get("title") or "").strip(),
            "chapter_no": str(mention_row.get("chapter_no") or "").strip(),
            "chapter_title": str(mention_row.get("chapter_title") or "").strip(),
            "section_no": str(mention_row.get("section_no") or "").strip(),
            "section_title": str(mention_row.get("section_title") or "").strip(),
        },
        "clause": {
            "article_no": str(mention_row.get("article_no") or "").strip(),
            "article_text": str(mention_row.get("article_text") or "").strip(),
            "subject_mentions": list(mention_row.get("subject_mentions") or []),
            "action_mentions": list(mention_row.get("action_mentions") or []),
            "object_mentions": list(mention_row.get("object_mentions") or []),
            "constraint_mentions": list(mention_row.get("constraint_mentions") or []),
        },
        "normalization": build_clause_normalization_payload(clause_normalization_rows),
        "concepts": build_clause_concepts_payload(clause_concept_rows),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_rule_expression_items(items: list[Any], mention_row: dict[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        subject = unique_str_list(list(item.get("subject") or []))
        actions = unique_str_list(list(item.get("actions") or []))
        objects = unique_str_list(list(item.get("object") or []))
        condition = unique_str_list(list(item.get("condition") or []))
        constraint = unique_str_list(list(item.get("constraint") or []))
        business_context = str(item.get("business_context") or "").strip()
        effect = str(item.get("effect") or "").strip()
        evidence_text = str(item.get("evidence_text") or "").strip() or str(mention_row.get("article_text") or "").strip()
        if not subject and not actions and not objects and not condition and not constraint and not business_context and not effect:
            continue
        normalized.append(
            {
                "doc_id": str(mention_row.get("doc_id") or "").strip(),
                "title": str(mention_row.get("title") or "").strip(),
                "article_no": str(mention_row.get("article_no") or "").strip(),
                "subject": subject,
                "actions": actions,
                "object": objects,
                "business_context": business_context,
                "condition": condition,
                "constraint": constraint,
                "effect": effect,
                "evidence_text": evidence_text,
            }
        )
    return normalized


class RuleExpressionGenerationError(ValueError):
    def __init__(self, message: str, raw_output: str) -> None:
        super().__init__(message)
        self.raw_output = raw_output


# 调用大模型生成单条条款的规制表达。
async def extract_single_clause_rule_expressions(llm: AsyncLLMClient, mention_row: dict[str, Any], clause_normalization_rows: list[dict[str, Any]], clause_concept_rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    async with sem:
        doc_id = str(mention_row.get("doc_id") or "").strip()
        article_no = str(mention_row.get("article_no") or "").strip()
        logger.info("开始规制表达生成 doc_id=%s article_no=%s normalization_count=%s concept_count=%s", doc_id, article_no, len(clause_normalization_rows), len(clause_concept_rows))
        messages = [{"role": "system", "content": RULE_EXPRESSION_SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(mention_row, clause_normalization_rows, clause_concept_rows)}]
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("规制表达模型返回 doc_id=%s article_no=%s attempt=%s/%s content=%s", doc_id, article_no, attempt, attempts, preview_text(content))
            try:
                payload = parse_json_payload(content)
                result_rows = normalize_rule_expression_items(list(payload.get("rule_expressions") or []), mention_row)
                logger.info("完成规制表达生成 doc_id=%s article_no=%s rule_expression_count=%s", doc_id, article_no, len(result_rows))
                return result_rows
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("规制表达解析失败 doc_id=%s article_no=%s attempt=%s/%s err=%s", doc_id, article_no, attempt, attempts, last_error)
        raise RuleExpressionGenerationError(f"规制表达生成失败 doc_id={doc_id} article_no={article_no}: {last_error}", last_content)


# 生成单条条款规制表达，失败时返回失败记录，避免中断整体流程。
async def process_single_clause_rule_expressions(llm: AsyncLLMClient, mention_row: dict[str, Any], clause_normalization_rows: list[dict[str, Any]], clause_concept_rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    doc_id = str(mention_row.get("doc_id") or "").strip()
    article_no = str(mention_row.get("article_no") or "").strip()
    try:
        result_rows = await extract_single_clause_rule_expressions(llm, mention_row, clause_normalization_rows, clause_concept_rows, model, thinking_extra_body, business_retries, sem)
        return {"ok": True, "rows": result_rows, "failure": None}
    except Exception as err:  # pylint: disable=broad-except
        raw_output = err.raw_output if isinstance(err, RuleExpressionGenerationError) else ""
        logger.error("规制表达任务失败，跳过当前条款 doc_id=%s article_no=%s err=%s", doc_id, article_no, err)
        return {"ok": False, "rows": [], "failure": build_failure_row(mention_row, str(err), raw_output)}


def build_summary(rule_rows: list[dict[str, Any]], failed_rows: list[dict[str, Any]]) -> dict[str, Any]:
    doc_ids = {str(row.get("doc_id") or "").strip() for row in rule_rows}
    article_keys = {build_clause_key(str(row.get("doc_id") or "").strip(), str(row.get("article_no") or "").strip()) for row in rule_rows}
    return {
        "doc_count": len(doc_ids),
        "article_count": len(article_keys),
        "rule_expression_count": len(rule_rows),
        "failed_clause_count": len(failed_rows),
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    mentions_jsonl = Path(args.mentions_jsonl).expanduser().resolve()
    normalization_jsonl = Path(args.normalization_jsonl).expanduser().resolve()
    concepts_jsonl = Path(args.concepts_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    mention_rows = filter_mention_rows(load_jsonl_rows(mentions_jsonl), str(args.doc_id or "").strip(), int(args.limit))
    if not mention_rows:
        raise ValueError("未找到符合条件的条款原始提及记录")
    normalization_by_clause = index_normalization_by_clause(load_jsonl_rows(normalization_jsonl))
    concepts_by_clause = index_concepts_by_clause(load_jsonl_rows(concepts_jsonl))

    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_clause_rule_expressions")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    logger.info("开始规制表达生成 clause_count=%s model=%s thinking_extra_body=%s", len(mention_rows), model, thinking_extra_body)
    tasks = []
    for mention_row in mention_rows:
        clause_key = build_clause_key(str(mention_row.get("doc_id") or "").strip(), str(mention_row.get("article_no") or "").strip())
        doc_id = str(mention_row.get("doc_id") or "").strip()
        article_no = str(mention_row.get("article_no") or "").strip()
        tasks.append(
            (
                asyncio.create_task(process_single_clause_rule_expressions(llm, mention_row, normalization_by_clause.get(clause_key, []), concepts_by_clause.get(clause_key, []), model, thinking_extra_body, int(args.business_retries), sem)),
                {doc_id} if doc_id else set(),
                f"{doc_id}:{article_no}",
            )
        )
    results = await gather_with_doc_progress("07", tasks, collect_doc_ids_from_rows(mention_rows))
    rule_rows = [item for result in results for item in list(result.get("rows") or [])]
    failed_rows = [result.get("failure") for result in results if result.get("failure")]

    output_jsonl = output_dir / "clause_rule_expressions.jsonl"
    failed_jsonl = output_dir / "failed_rule_expressions.jsonl"
    output_summary = output_dir / "clause_rule_expressions_summary.json"
    write_jsonl_rows(output_jsonl, rule_rows)
    write_jsonl_rows(failed_jsonl, failed_rows)
    write_json(output_summary, build_summary(rule_rows, failed_rows))
    print(f"rule expressions jsonl: {output_jsonl}")
    print(f"failed rule expressions jsonl: {failed_jsonl}")
    print(f"summary: {output_summary}")
    print(f"clause_count={len(mention_rows)} rule_expression_count={len(rule_rows)} failed_clause_count={len(failed_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
