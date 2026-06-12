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
DEFAULT_MAX_GROUPS_PER_BATCH = 80

CONCEPT_SCREEN_SYSTEM_PROMPT = """你正在做制度条款语义归并结果的“概念筛选”。

你会收到一批已经完成归并的概念组。你的任务是判断每个概念组是否适合进入后续规制表达与本体层。

请对每个概念组输出：
1. concept_status：
   - core_concept：稳定、可复用、适合进入后续规制表达和本体
   - context_concept：有意义，但更适合作为上下文、对象侧或弱概念保留
   - noise_concept：编号残片、格式碎片、无独立语义价值或不适合作为概念的内容
2. ontology_role：
   - subject：更适合作为主体概念
   - action：更适合作为动作概念
   - object：更适合作为对象概念
   - context：更适合作为上下文概念
   - discard：应丢弃，不进入后续本体
3. screen_reason：一句简短理由

要求：
1. 不要因为 member_mentions 只有 1 个，就直接判成 noise_concept。
2. 像“债权人”“党务工作人员”这类稳定角色，即使只有 1 个成员，也可能是 core_concept。
3. 像“（一）”“（1）”这类编号、格式标记、残片，应判为 noise_concept。
4. 像“他人”“第三方”这类泛化对象通常不是噪音，但更适合作为 context_concept 或 object。
5. 来自 object_mentions 的稳定对象、事项、财产、权利义务指向对象，优先判为 object；如果只是字段项、长枚举原文、临时修饰片段，应降级为 context_concept 或 noise_concept。
6. 来自 constraint_mentions 的概念应重点判断是否真的是条件、门槛、程序要求、规范力度；不要把对象类内容继续判成 context。
7. 必须覆盖输入中的每个 normalization_group_id。
8. 只输出 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "items": [
    {
      "normalization_group_id": "",
      "concept_status": "core_concept",
      "ontology_role": "subject",
      "screen_reason": ""
    }
  ]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对归并后的条款语义概念组做概念筛选。")
    parser.add_argument("--normalization-jsonl", required=True, help="clause_semantics_normalization.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 JSON 时的业务重试次数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--max-groups-per-batch", type=int, default=DEFAULT_MAX_GROUPS_PER_BATCH, help="每批概念组数，默认 80")
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
            logger.warning("概念筛选 content 为空，回退字段=%s", field)
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


def collect_member_texts(row: dict[str, Any], limit: int = 12) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for item in list(row.get("member_mentions") or []):
        mention_text = str(item.get("mention_text") or "").strip()
        if not mention_text or mention_text in seen:
            continue
        seen.add(mention_text)
        texts.append(mention_text)
        if len(texts) >= limit:
            break
    return texts


def collect_member_contexts(row: dict[str, Any], limit: int = 4) -> list[dict[str, str]]:
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


def build_user_prompt(rows: list[dict[str, Any]]) -> str:
    payload = {
        "groups": [
            {
                "normalization_group_id": str(row.get("normalization_group_id") or "").strip(),
                "mention_category": str(row.get("mention_category") or "").strip(),
                "canonical_name": str(row.get("canonical_name") or "").strip(),
                "member_count": len(list(row.get("member_mentions") or [])),
                "member_texts": collect_member_texts(row),
                "sample_contexts": collect_member_contexts(row),
                "normalization_reason": str(row.get("reason") or "").strip(),
            }
            for row in rows
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_screen_payload(payload: dict[str, Any], valid_group_ids: set[str]) -> list[dict[str, str]]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("概念筛选结果缺少 items 数组")
    normalized: list[dict[str, str]] = []
    covered_group_ids: set[str] = set()
    valid_status = {"core_concept", "context_concept", "noise_concept"}
    valid_roles = {"subject", "action", "object", "context", "discard"}
    for item in items:
        if not isinstance(item, dict):
            continue
        normalization_group_id = str(item.get("normalization_group_id") or "").strip()
        concept_status = str(item.get("concept_status") or "").strip()
        ontology_role = str(item.get("ontology_role") or "").strip()
        if normalization_group_id not in valid_group_ids:
            continue
        if concept_status not in valid_status:
            continue
        if ontology_role not in valid_roles:
            continue
        normalized.append(
            {
                "normalization_group_id": normalization_group_id,
                "concept_status": concept_status,
                "ontology_role": ontology_role,
                "screen_reason": str(item.get("screen_reason") or "").strip(),
            }
        )
        covered_group_ids.add(normalization_group_id)
    missing_group_ids = valid_group_ids - covered_group_ids
    if missing_group_ids:
        raise ValueError(f"概念筛选结果缺少 group_id: {sorted(missing_group_ids)}")
    return normalized


# 调用大模型做单批概念筛选。
async def screen_group_batch(llm: AsyncLLMClient, rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> list[dict[str, str]]:
    async with sem:
        logger.info("开始概念筛选 batch_group_count=%s", len(rows))
        messages = [{"role": "system", "content": CONCEPT_SCREEN_SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(rows)}]
        valid_group_ids = {str(row.get("normalization_group_id") or "").strip() for row in rows}
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("概念筛选模型返回 batch_group_count=%s attempt=%s/%s content=%s", len(rows), attempt, attempts, preview_text(content))
            try:
                result_rows = normalize_screen_payload(parse_json_payload(content), valid_group_ids)
                logger.info("完成概念筛选 batch_group_count=%s", len(rows))
                return result_rows
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("概念筛选解析失败 batch_group_count=%s attempt=%s/%s err=%s", len(rows), attempt, attempts, last_error)
        raise ValueError(f"概念筛选失败: {last_error}; 原始输出: {last_content}")


def merge_screen_results(group_rows: list[dict[str, Any]], screen_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    result_map = {str(row.get("normalization_group_id") or "").strip(): row for row in screen_rows}
    merged_rows: list[dict[str, Any]] = []
    for row in group_rows:
        normalization_group_id = str(row.get("normalization_group_id") or "").strip()
        screen_row = result_map.get(normalization_group_id)
        if not screen_row:
            raise ValueError(f"概念筛选缺少 normalization_group_id={normalization_group_id}")
        merged = dict(row)
        merged["concept_status"] = str(screen_row.get("concept_status") or "").strip()
        merged["ontology_role"] = str(screen_row.get("ontology_role") or "").strip()
        merged["screen_reason"] = str(screen_row.get("screen_reason") or "").strip()
        merged_rows.append(merged)
    return merged_rows


def filter_noise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("concept_status") or "").strip() == "noise_concept"]


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    concept_status_counts: dict[str, int] = defaultdict(int)
    ontology_role_counts: dict[str, int] = defaultdict(int)
    mention_category_counts: dict[str, int] = defaultdict(int)
    single_member_group_count = 0
    for row in rows:
        concept_status_counts[str(row.get("concept_status") or "").strip()] += 1
        ontology_role_counts[str(row.get("ontology_role") or "").strip()] += 1
        mention_category_counts[str(row.get("mention_category") or "").strip()] += 1
        if len(list(row.get("member_mentions") or [])) == 1:
            single_member_group_count += 1
    return {
        "concept_group_count": len(rows),
        "single_member_group_count": single_member_group_count,
        "concept_status_counts": dict(concept_status_counts),
        "ontology_role_counts": dict(ontology_role_counts),
        "mention_category_counts": dict(mention_category_counts),
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    normalization_jsonl = Path(args.normalization_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    group_rows = load_jsonl_rows(normalization_jsonl)
    if not group_rows:
        raise ValueError("归并结果为空，无法做概念筛选")

    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_clause_semantics_concepts")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    logger.info("开始概念筛选 group_count=%s model=%s thinking_extra_body=%s", len(group_rows), model, thinking_extra_body)
    batches = split_list(group_rows, int(args.max_groups_per_batch))
    total_doc_ids = collect_doc_ids_from_member_mentions(group_rows)
    tasks = [
        (
            asyncio.create_task(screen_group_batch(llm, batch_rows, model, thinking_extra_body, int(args.business_retries), sem)),
            collect_doc_ids_from_member_mentions(batch_rows),
            f"batch={index}/{len(batches)}",
        )
        for index, batch_rows in enumerate(batches, start=1)
    ]
    batch_results = await gather_with_doc_progress("06", tasks, total_doc_ids)
    screen_rows = [item for batch in batch_results for item in batch]
    concept_rows = merge_screen_results(group_rows, screen_rows)
    review_noise_rows = filter_noise_rows(concept_rows)

    output_jsonl = output_dir / "clause_semantics_concepts.jsonl"
    output_review_noise = output_dir / "clause_semantics_concepts_review_noise.jsonl"
    output_summary = output_dir / "clause_semantics_concepts_summary.json"
    write_jsonl_rows(output_jsonl, concept_rows)
    write_jsonl_rows(output_review_noise, review_noise_rows)
    write_json(output_summary, build_summary(concept_rows))
    print(f"concepts jsonl: {output_jsonl}")
    print(f"review noise jsonl: {output_review_noise}")
    print(f"summary: {output_summary}")
    print(f"concept_group_count={len(concept_rows)} noise_group_count={len(review_noise_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
