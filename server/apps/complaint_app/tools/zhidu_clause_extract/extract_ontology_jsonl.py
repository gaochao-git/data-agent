from __future__ import annotations

import argparse
import asyncio
import json
import math
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


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
DEFAULT_MAX_CHARS_PER_DOC = 20000
DEFAULT_MAX_NORMALIZE_ITEMS_PER_BATCH = 80

ONTOLOGY_EXTRACTION_SYSTEM_PROMPT = """你正在做“制度知识本体”的第一阶段抽取。

你的任务不是做冲突识别，也不是做固定字段模板抽取，而是把制度文本中的知识表达抽成三类结构：
1. 知识对象 entities
2. 对象关系 relations
3. 规则语义 rule_semantics

请遵循以下原则：
1. 优先抽取制度中稳定、可复用的知识对象，不要为了凑数量把大量噪音词都当实体。
2. 关系要表达对象之间的真实联系，例如属于、负责、适用、约束、依赖、引用、触发、前置于、后置于、例外于等。
3. 规则语义用于表达制度中的规范性语句，例如要求、禁止、允许、授权、条件生效、流程约束、责任归属等。
4. 不要为了迎合“冲突识别”场景硬编码字段；如果文本里没有金额、时限、审批，不要强行抽。
5. 每个实体、关系、规则语义都必须带证据条号和证据文本。
6. entity_id、relation_id、rule_id 只需要在当前制度内唯一即可。
7. 只输出 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "document_summary": "",
  "entities": [
    {
      "entity_id": "E1",
      "name": "",
      "entity_type": "",
      "description": "",
      "aliases": [],
      "evidence_article_no": "",
      "evidence_text": ""
    }
  ],
  "relations": [
    {
      "relation_id": "R1",
      "source_entity_id": "E1",
      "relation_type": "",
      "target_entity_id": "E2",
      "description": "",
      "evidence_article_no": "",
      "evidence_text": ""
    }
  ],
  "rule_semantics": [
    {
      "rule_id": "S1",
      "rule_type": "",
      "subject_entity_id": "E1",
      "action": "",
      "target_entity_id": "E2",
      "conditions": "",
      "effect": "",
      "evidence_article_no": "",
      "evidence_text": ""
    }
  ]
}"""

ONTOLOGY_NORMALIZATION_SYSTEM_PROMPT = """你正在做“制度知识本体”的概念归一化。

你会拿到一批同类型实体，它们来自不同制度。
你的任务是识别其中哪些实体实际上表达的是同一概念、近义概念或可归并概念，并给出一个规范名称。

请遵循以下原则：
1. 只有在语义上明显指向同一概念时才归并，不要因为词面相似就强行合并。
2. 如果存在上下位概念关系、但不适合直接合并，也不要硬归并。
3. 如果无法确定，宁可不合并。
4. 只输出归并组，不需要输出单个孤立实体。
5. 只输出 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "groups": [
    {
      "canonical_name": "",
      "entity_type": "",
      "member_global_entity_ids": ["doc1::E1", "doc2::E3"],
      "reason": ""
    }
  ]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从制度正文中抽取本体实例，并输出多层 JSONL。")
    parser.add_argument("--documents-json", required=True, help="documents_mapped.jsonl 或 documents.jsonl 路径")
    parser.add_argument("--raw-text-dir", required=True, help="raw_texts 目录路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--category", default="", help="只处理指定分类")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份文档，0 表示全部")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 JSON 时的业务重试次数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--max-chars-per-doc", type=int, default=DEFAULT_MAX_CHARS_PER_DOC, help="每份制度送给模型的最大字符数，默认 20000")
    parser.add_argument("--max-normalize-items-per-batch", type=int, default=DEFAULT_MAX_NORMALIZE_ITEMS_PER_BATCH, help="归一化阶段每批实体数，默认 80")
    parser.add_argument("--include-unmatched-outline", action="store_true", help="是否纳入 outline 未匹配文档，默认只处理 matched")
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


def write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_raw_text_path(raw_text_dir: Path, doc_id: str) -> Path:
    return raw_text_dir / f"{doc_id}.txt"


def read_raw_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def truncate_text(text: str, max_chars: int) -> str:
    source = str(text or "")
    if len(source) <= max_chars:
        return source
    return source[:max_chars]


def filter_documents(document_rows: list[dict[str, Any]], category: str, limit: int, include_unmatched_outline: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in document_rows:
        if category and str(row.get("category") or "").strip() != category:
            continue
        if not include_unmatched_outline:
            outline_match_status = str(row.get("outline_match_status") or row.get("match_status") or "").strip()
            if outline_match_status and outline_match_status != "matched":
                continue
        rows.append(row)
        if limit > 0 and len(rows) >= limit:
            break
    return rows


def build_ontology_user_prompt(document: dict[str, Any], raw_text: str) -> str:
    payload = {
        "document": {
            "doc_id": str(document.get("doc_id") or "").strip(),
            "title": str(document.get("title") or "").strip(),
            "category": str(document.get("category") or "").strip(),
            "版块": str(document.get("版块") or "").strip(),
            "领域": str(document.get("领域") or "").strip(),
            "制度文号": str(document.get("制度文号") or "").strip(),
            "制度级别": str(document.get("制度级别") or "").strip(),
            "发布时间": str(document.get("发布时间") or "").strip(),
            "主责部门": str(document.get("主责部门") or "").strip(),
        },
        "raw_text": raw_text,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_normalization_user_prompt(entity_type: str, entity_rows: list[dict[str, Any]]) -> str:
    payload = {
        "entity_type": entity_type,
        "entities": [
            {
                "global_entity_id": str(row.get("global_entity_id") or "").strip(),
                "name": str(row.get("name") or "").strip(),
                "description": str(row.get("description") or "").strip(),
                "aliases": list(row.get("aliases") or []),
                "doc_id": str(row.get("doc_id") or "").strip(),
                "document_title": str(row.get("document_title") or "").strip(),
                "evidence_article_no": str(row.get("evidence_article_no") or "").strip(),
                "evidence_text": str(row.get("evidence_text") or "").strip(),
            }
            for row in entity_rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# 统一读取响应消息字段值。
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


# 兼容 content 为字符串或数组的场景。
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
            logger.warning("本体抽取 content 为空，回退字段=%s", field)
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


def normalize_entity_items(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get("entity_id") or f"E{index}").strip()
        name = str(item.get("name") or "").strip()
        entity_type = str(item.get("entity_type") or "").strip()
        if not name or not entity_type:
            continue
        aliases = item.get("aliases")
        alias_list = [str(alias).strip() for alias in aliases if str(alias).strip()] if isinstance(aliases, list) else []
        normalized.append(
            {
                "entity_id": entity_id,
                "name": name,
                "entity_type": entity_type,
                "description": str(item.get("description") or "").strip(),
                "aliases": alias_list,
                "evidence_article_no": str(item.get("evidence_article_no") or "").strip(),
                "evidence_text": str(item.get("evidence_text") or "").strip(),
            }
        )
    return normalized


def normalize_relation_items(items: list[Any], valid_entity_ids: set[str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        relation_id = str(item.get("relation_id") or f"R{index}").strip()
        source_entity_id = str(item.get("source_entity_id") or "").strip()
        target_entity_id = str(item.get("target_entity_id") or "").strip()
        relation_type = str(item.get("relation_type") or "").strip()
        if not relation_type or not source_entity_id or not target_entity_id:
            continue
        if source_entity_id not in valid_entity_ids or target_entity_id not in valid_entity_ids:
            continue
        normalized.append(
            {
                "relation_id": relation_id,
                "source_entity_id": source_entity_id,
                "relation_type": relation_type,
                "target_entity_id": target_entity_id,
                "description": str(item.get("description") or "").strip(),
                "evidence_article_no": str(item.get("evidence_article_no") or "").strip(),
                "evidence_text": str(item.get("evidence_text") or "").strip(),
            }
        )
    return normalized


def normalize_rule_items(items: list[Any], valid_entity_ids: set[str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or f"S{index}").strip()
        subject_entity_id = str(item.get("subject_entity_id") or "").strip()
        target_entity_id = str(item.get("target_entity_id") or "").strip()
        if subject_entity_id and subject_entity_id not in valid_entity_ids:
            continue
        if target_entity_id and target_entity_id not in valid_entity_ids:
            continue
        rule_type = str(item.get("rule_type") or "").strip()
        action = str(item.get("action") or "").strip()
        if not rule_type and not action:
            continue
        normalized.append(
            {
                "rule_id": rule_id,
                "rule_type": rule_type,
                "subject_entity_id": subject_entity_id,
                "action": action,
                "target_entity_id": target_entity_id,
                "conditions": str(item.get("conditions") or "").strip(),
                "effect": str(item.get("effect") or "").strip(),
                "evidence_article_no": str(item.get("evidence_article_no") or "").strip(),
                "evidence_text": str(item.get("evidence_text") or "").strip(),
            }
        )
    return normalized


def normalize_ontology_payload(payload: dict[str, Any]) -> dict[str, Any]:
    entities = normalize_entity_items(list(payload.get("entities") or []))
    valid_entity_ids = {str(item.get("entity_id") or "").strip() for item in entities}
    return {
        "document_summary": str(payload.get("document_summary") or "").strip(),
        "entities": entities,
        "relations": normalize_relation_items(list(payload.get("relations") or []), valid_entity_ids),
        "rule_semantics": normalize_rule_items(list(payload.get("rule_semantics") or []), valid_entity_ids),
    }


# 调用大模型抽取单份制度的本体实例。
async def extract_single_document_ontology(llm: AsyncLLMClient, document: dict[str, Any], raw_text_dir: Path, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, max_chars_per_doc: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        doc_id = str(document.get("doc_id") or "").strip()
        raw_text_path = resolve_raw_text_path(raw_text_dir, doc_id)
        raw_text = truncate_text(read_raw_text(raw_text_path), max_chars_per_doc)
        messages = [{"role": "system", "content": ONTOLOGY_EXTRACTION_SYSTEM_PROMPT}, {"role": "user", "content": build_ontology_user_prompt(document, raw_text)}]
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("本体实例抽取模型返回 doc_id=%s attempt=%s/%s content=%s", doc_id, attempt, attempts, preview_text(content))
            try:
                payload = normalize_ontology_payload(parse_json_payload(content))
                return build_instance_row(document, payload)
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("本体实例抽取解析失败 doc_id=%s attempt=%s/%s err=%s", doc_id, attempt, attempts, last_error)
        raise ValueError(f"本体实例抽取失败 doc_id={doc_id}: {last_error}; 原始输出: {last_content}")


def build_instance_row(document: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": str(document.get("doc_id") or "").strip(),
        "document_title": str(document.get("title") or "").strip(),
        "category": str(document.get("category") or "").strip(),
        "版块": str(document.get("版块") or "").strip(),
        "领域": str(document.get("领域") or "").strip(),
        "制度文号": str(document.get("制度文号") or "").strip(),
        "制度级别": str(document.get("制度级别") or "").strip(),
        "发布时间": str(document.get("发布时间") or "").strip(),
        "主责部门": str(document.get("主责部门") or "").strip(),
        "document_summary": str(payload.get("document_summary") or "").strip(),
        "entities": list(payload.get("entities") or []),
        "relations": list(payload.get("relations") or []),
        "rule_semantics": list(payload.get("rule_semantics") or []),
    }


# 将实例层拉平成全局实体行。
def flatten_entities(instance_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entity_rows: list[dict[str, Any]] = []
    for instance in instance_rows:
        doc_id = str(instance.get("doc_id") or "").strip()
        document_title = str(instance.get("document_title") or "").strip()
        for item in list(instance.get("entities") or []):
            entity_id = str(item.get("entity_id") or "").strip()
            entity_rows.append(
                {
                    "global_entity_id": f"{doc_id}::{entity_id}",
                    "doc_id": doc_id,
                    "document_title": document_title,
                    "category": str(instance.get("category") or "").strip(),
                    "版块": str(instance.get("版块") or "").strip(),
                    "领域": str(instance.get("领域") or "").strip(),
                    "entity_id": entity_id,
                    "name": str(item.get("name") or "").strip(),
                    "entity_type": str(item.get("entity_type") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "aliases": list(item.get("aliases") or []),
                    "evidence_article_no": str(item.get("evidence_article_no") or "").strip(),
                    "evidence_text": str(item.get("evidence_text") or "").strip(),
                }
            )
    return entity_rows


# 将实例层拉平成全局关系行。
def flatten_relations(instance_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relation_rows: list[dict[str, Any]] = []
    for instance in instance_rows:
        doc_id = str(instance.get("doc_id") or "").strip()
        document_title = str(instance.get("document_title") or "").strip()
        for item in list(instance.get("relations") or []):
            relation_id = str(item.get("relation_id") or "").strip()
            source_entity_id = str(item.get("source_entity_id") or "").strip()
            target_entity_id = str(item.get("target_entity_id") or "").strip()
            relation_rows.append(
                {
                    "global_relation_id": f"{doc_id}::{relation_id}",
                    "doc_id": doc_id,
                    "document_title": document_title,
                    "category": str(instance.get("category") or "").strip(),
                    "版块": str(instance.get("版块") or "").strip(),
                    "领域": str(instance.get("领域") or "").strip(),
                    "relation_id": relation_id,
                    "source_entity_id": source_entity_id,
                    "target_entity_id": target_entity_id,
                    "source_global_entity_id": f"{doc_id}::{source_entity_id}",
                    "target_global_entity_id": f"{doc_id}::{target_entity_id}",
                    "relation_type": str(item.get("relation_type") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                    "evidence_article_no": str(item.get("evidence_article_no") or "").strip(),
                    "evidence_text": str(item.get("evidence_text") or "").strip(),
                }
            )
    return relation_rows


# 计算归一化批次数量。
def count_batches(total: int, batch_size: int) -> int:
    if total <= 0:
        return 0
    return int(math.ceil(total / max(1, batch_size)))


def split_entity_rows_for_normalization(entity_rows: list[dict[str, Any]], max_items_per_batch: int) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in entity_rows:
        entity_type = str(row.get("entity_type") or "").strip() or "未分类实体"
        grouped_rows[entity_type].append(row)
    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for entity_type, rows in grouped_rows.items():
        batch_size = max(1, int(max_items_per_batch))
        total_batches = count_batches(len(rows), batch_size)
        for index in range(total_batches):
            start = index * batch_size
            end = start + batch_size
            batches.append((entity_type, rows[start:end]))
    return batches


def normalize_normalization_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise ValueError("归一化结果缺少 groups 数组")
    normalized: list[dict[str, Any]] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name") or "").strip()
        entity_type = str(item.get("entity_type") or "").strip()
        member_global_entity_ids = item.get("member_global_entity_ids")
        if not canonical_name or not entity_type or not isinstance(member_global_entity_ids, list):
            continue
        members = [str(member).strip() for member in member_global_entity_ids if str(member).strip()]
        if len(members) < 2:
            continue
        normalized.append(
            {
                "canonical_name": canonical_name,
                "entity_type": entity_type,
                "member_global_entity_ids": members,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return normalized


# 调用大模型对一批实体做概念归一化。
async def normalize_entity_batch(llm: AsyncLLMClient, entity_type: str, entity_rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    async with sem:
        messages = [{"role": "system", "content": ONTOLOGY_NORMALIZATION_SYSTEM_PROMPT}, {"role": "user", "content": build_normalization_user_prompt(entity_type, entity_rows)}]
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("本体归一化模型返回 entity_type=%s batch_size=%s attempt=%s/%s content=%s", entity_type, len(entity_rows), attempt, attempts, preview_text(content))
            try:
                return normalize_normalization_payload(parse_json_payload(content))
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("本体归一化解析失败 entity_type=%s batch_size=%s attempt=%s/%s err=%s", entity_type, len(entity_rows), attempt, attempts, last_error)
        raise ValueError(f"本体归一化失败 entity_type={entity_type}: {last_error}; 原始输出: {last_content}")


def add_normalization_group_ids(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(groups, start=1):
        row = dict(item)
        row["normalization_group_id"] = f"N{index}"
        rows.append(row)
    return rows


def build_summary(instance_rows: list[dict[str, Any]], entity_rows: list[dict[str, Any]], relation_rows: list[dict[str, Any]], normalization_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_entity_type: dict[str, int] = {}
    for row in entity_rows:
        entity_type = str(row.get("entity_type") or "").strip() or "未分类实体"
        by_entity_type[entity_type] = by_entity_type.get(entity_type, 0) + 1
    return {
        "document_count": len(instance_rows),
        "entity_count": len(entity_rows),
        "relation_count": len(relation_rows),
        "normalization_group_count": len(normalization_rows),
        "entity_type_counts": by_entity_type,
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    documents_json = Path(args.documents_json).expanduser().resolve()
    raw_text_dir = Path(args.raw_text_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    documents = filter_documents(load_jsonl_rows(documents_json), str(args.category or "").strip(), int(args.limit), bool(args.include_unmatched_outline))
    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_ontology_extract")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    logger.info("开始本体实例抽取 documents=%s model=%s thinking_extra_body=%s", len(documents), model, thinking_extra_body)
    instance_tasks = [
        asyncio.create_task(
            extract_single_document_ontology(
                llm,
                document,
                raw_text_dir,
                model,
                thinking_extra_body,
                int(args.business_retries),
                int(args.max_chars_per_doc),
                sem,
            )
        )
        for document in documents
    ]
    instance_rows = await asyncio.gather(*instance_tasks)
    entity_rows = flatten_entities(instance_rows)
    relation_rows = flatten_relations(instance_rows)

    normalization_batches = split_entity_rows_for_normalization(entity_rows, int(args.max_normalize_items_per_batch))
    logger.info("开始本体归一化 batches=%s", len(normalization_batches))
    normalization_tasks = [
        asyncio.create_task(normalize_entity_batch(llm, entity_type, batch_rows, model, thinking_extra_body, int(args.business_retries), sem))
        for entity_type, batch_rows in normalization_batches
    ]
    normalization_groups_nested = await asyncio.gather(*normalization_tasks) if normalization_tasks else []
    normalization_rows = add_normalization_group_ids([item for group in normalization_groups_nested for item in group])

    ontology_instances_path = output_dir / "ontology_instances.jsonl"
    ontology_entities_path = output_dir / "ontology_entities.jsonl"
    ontology_relations_path = output_dir / "ontology_relations.jsonl"
    ontology_normalization_path = output_dir / "ontology_normalization.jsonl"
    summary_path = output_dir / "ontology_summary.json"
    write_jsonl_rows(ontology_instances_path, instance_rows)
    write_jsonl_rows(ontology_entities_path, entity_rows)
    write_jsonl_rows(ontology_relations_path, relation_rows)
    write_jsonl_rows(ontology_normalization_path, normalization_rows)
    write_json(summary_path, build_summary(instance_rows, entity_rows, relation_rows, normalization_rows))
    print(f"ontology instances: {ontology_instances_path}")
    print(f"ontology entities: {ontology_entities_path}")
    print(f"ontology relations: {ontology_relations_path}")
    print(f"ontology normalization: {ontology_normalization_path}")
    print(f"summary: {summary_path}")
    print(f"document_count={len(instance_rows)} entity_count={len(entity_rows)} relation_count={len(relation_rows)} normalization_group_count={len(normalization_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
