from __future__ import annotations

import argparse
import asyncio
import json
import sys
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


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000

SYSTEM_PROMPT = """你正在做制度条款的“原始提及”抽取。

目标不是做冲突识别，也不是做完整本体，而是先把每条条款中的规制核心原样抽出来，供后续再做归一化。

请针对单条条款，抽取四类原始提及：
1. subject_mentions：条款中的规则主体，即承担义务、享有权利、发起动作、被赋予职责的主体
2. action_mentions：条款中的动作、行为、权力行为、义务行为、程序行为等
3. object_mentions：动作作用对象、受影响对象、被处理对象、权利义务指向对象、事项对象等
4. constraint_mentions：约束性表达，只抽条件、适用范围、触发情形、例外、程序要求、门槛、规范力度、标准要求等

要求：
1. 优先忠实抽取原文里的关键提及，不要急着归一化。
2. 不要把不同词强行合并成一个概念。
3. 如果同一句里有并列动作，例如“聘任或者解聘”，应分别抽成两个 action mention。
4. 如果条款里存在多个主体、对象或约束条件，应分别列出。
5. 每个 mention 必须保留 mention_text 原文表达，并给出一句简短说明。
6. 不要把动作对象、属性对象、事项清单放进 constraint_mentions；这类内容应放入 object_mentions。
7. 只输出 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "subject_mentions": [
    {
      "mention_text": "",
      "mention_type": "",
      "description": ""
    }
  ],
  "action_mentions": [
    {
      "mention_text": "",
      "mention_type": "",
      "description": ""
    }
  ],
  "object_mentions": [
    {
      "mention_text": "",
      "mention_type": "",
      "description": ""
    }
  ],
  "constraint_mentions": [
    {
      "mention_text": "",
      "constraint_type": "",
      "description": ""
    }
  ]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 clauses.jsonl 中抽取条款级原始提及。")
    parser.add_argument("--clauses-jsonl", required=True, help="clauses.jsonl 路径")
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


def filter_clause_rows(rows: list[dict[str, Any]], doc_id: str, limit: int) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if doc_id and str(row.get("doc_id") or "").strip() != doc_id:
            continue
        filtered.append(row)
        if limit > 0 and len(filtered) >= limit:
            break
    return filtered


def build_user_prompt(row: dict[str, Any]) -> str:
    payload = {
        "doc_id": str(row.get("doc_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "article_no": str(row.get("article_no") or "").strip(),
        "chapter_no": str(row.get("chapter_no") or "").strip(),
        "chapter_title": str(row.get("chapter_title") or "").strip(),
        "section_no": str(row.get("section_no") or "").strip(),
        "section_title": str(row.get("section_title") or "").strip(),
        "article_text": str(row.get("article_text") or "").strip(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


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
            logger.warning("条款原始提及抽取 content 为空，回退字段=%s", field)
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


def normalize_subject_mentions(items: list[Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mention_text = str(item.get("mention_text") or "").strip()
        if not mention_text:
            continue
        rows.append(
            {
                "mention_text": mention_text,
                "mention_type": str(item.get("mention_type") or "").strip(),
                "description": str(item.get("description") or "").strip(),
            }
        )
    return rows


def normalize_action_mentions(items: list[Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mention_text = str(item.get("mention_text") or "").strip()
        if not mention_text:
            continue
        rows.append(
            {
                "mention_text": mention_text,
                "mention_type": str(item.get("mention_type") or "").strip(),
                "description": str(item.get("description") or "").strip(),
            }
        )
    return rows


def normalize_object_mentions(items: list[Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mention_text = str(item.get("mention_text") or "").strip()
        if not mention_text:
            continue
        rows.append(
            {
                "mention_text": mention_text,
                "mention_type": str(item.get("mention_type") or "").strip(),
                "description": str(item.get("description") or "").strip(),
            }
        )
    return rows


def normalize_constraint_mentions(items: list[Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        mention_text = str(item.get("mention_text") or "").strip()
        if not mention_text:
            continue
        rows.append(
            {
                "mention_text": mention_text,
                "constraint_type": str(item.get("constraint_type") or "").strip(),
                "description": str(item.get("description") or "").strip(),
            }
        )
    return rows


def normalize_mentions_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject_mentions": normalize_subject_mentions(list(payload.get("subject_mentions") or [])),
        "action_mentions": normalize_action_mentions(list(payload.get("action_mentions") or [])),
        "object_mentions": normalize_object_mentions(list(payload.get("object_mentions") or [])),
        "constraint_mentions": normalize_constraint_mentions(list(payload.get("constraint_mentions") or [])),
    }


# 调用大模型抽取单条条款的原始提及。
async def extract_single_clause_mentions(llm: AsyncLLMClient, row: dict[str, Any], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": build_user_prompt(row)}]
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("条款原始提及抽取模型返回 doc_id=%s article_no=%s attempt=%s/%s content=%s", row.get("doc_id"), row.get("article_no"), attempt, attempts, preview_text(content))
            try:
                payload = normalize_mentions_payload(parse_json_payload(content))
                return build_result_row(row, payload)
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("条款原始提及抽取解析失败 doc_id=%s article_no=%s attempt=%s/%s err=%s", row.get("doc_id"), row.get("article_no"), attempt, attempts, last_error)
        raise ValueError(f"条款原始提及抽取失败 doc_id={row.get('doc_id')} article_no={row.get('article_no')}: {last_error}; 原始输出: {last_content}")


def build_result_row(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": str(row.get("doc_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "article_no": str(row.get("article_no") or "").strip(),
        "article_text": str(row.get("article_text") or "").strip(),
        "chapter_no": str(row.get("chapter_no") or "").strip(),
        "chapter_title": str(row.get("chapter_title") or "").strip(),
        "section_no": str(row.get("section_no") or "").strip(),
        "section_title": str(row.get("section_title") or "").strip(),
        "subject_mentions": list(payload.get("subject_mentions") or []),
        "action_mentions": list(payload.get("action_mentions") or []),
        "object_mentions": list(payload.get("object_mentions") or []),
        "constraint_mentions": list(payload.get("constraint_mentions") or []),
    }


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    subject_count = sum(len(list(row.get("subject_mentions") or [])) for row in rows)
    action_count = sum(len(list(row.get("action_mentions") or [])) for row in rows)
    object_count = sum(len(list(row.get("object_mentions") or [])) for row in rows)
    constraint_count = sum(len(list(row.get("constraint_mentions") or [])) for row in rows)
    return {
        "clause_count": len(rows),
        "subject_mention_count": subject_count,
        "action_mention_count": action_count,
        "object_mention_count": object_count,
        "constraint_mention_count": constraint_count,
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    clauses_jsonl = Path(args.clauses_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = filter_clause_rows(load_jsonl_rows(clauses_jsonl), str(args.doc_id or "").strip(), int(args.limit))
    if not rows:
        raise ValueError("未找到符合条件的条款记录")
    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_clause_semantics_mentions")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))
    logger.info("开始条款原始提及抽取 clause_count=%s doc_id=%s model=%s thinking_extra_body=%s", len(rows), str(args.doc_id or "").strip(), model, thinking_extra_body)
    tasks = [
        asyncio.create_task(extract_single_clause_mentions(llm, row, model, thinking_extra_body, int(args.business_retries), sem))
        for row in rows
    ]
    result_rows = await asyncio.gather(*tasks)
    output_jsonl = output_dir / "clause_semantics_mentions.jsonl"
    output_summary = output_dir / "clause_semantics_mentions_summary.json"
    write_jsonl_rows(output_jsonl, result_rows)
    write_json(output_summary, build_summary(result_rows))
    print(f"mentions jsonl: {output_jsonl}")
    print(f"summary: {output_summary}")
    print(f"clause_count={len(result_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
