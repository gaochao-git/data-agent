from __future__ import annotations

import argparse
import asyncio
import hashlib
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


logger = get_logger(__name__)
MAX_LOG_PREVIEW = 2000
DEFAULT_MAX_DOC_MENTIONS_PER_BATCH = 120
DEFAULT_MAX_GLOBAL_GROUPS_PER_BATCH = 80
MENTION_CATEGORIES = ("subject_mentions", "action_mentions", "object_mentions", "constraint_mentions")

DOC_NORMALIZATION_SYSTEM_PROMPT = """你正在做制度条款语义的“制度内归一化”。

你会收到同一份制度、同一类原始提及的一组记录。你的任务是判断哪些提及在该制度内部表达的是同一概念或高度近似概念，并产出归并组。

要求：
1. 只在语义明确一致或高度近似时才归并，不要因为词面相似就强行合并。
2. 主体、动作、对象、约束四类提及分别独立处理，不要跨类别归并。
3. 如果无法确定，宁可不归并。
4. 只输出可归并的 groups；无法归并的 mention_id 放入 ungrouped_mention_ids。
5. canonical_name 必须是短、稳定、可复用的名词短语或动作短语，不要使用整句、长枚举、字段项、临时修饰词或完整规则要求。
6. 对于 object_mentions，canonical_name 应命名为对象或事项名称；对于 constraint_mentions，canonical_name 应命名为条件、门槛、程序要求或规范力度，不要承接对象内容。
7. 输出必须是严格 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "groups": [
    {
      "canonical_name": "",
      "member_mention_ids": ["m1", "m2"],
      "reason": ""
    }
  ],
  "ungrouped_mention_ids": ["m3"]
}"""

GLOBAL_NORMALIZATION_SYSTEM_PROMPT = """你正在做制度条款语义的“跨制度归一化”。

你会收到同一类提及在不同制度内已经完成制度内归一化后的结果。你的任务是继续判断哪些归并组在全局上表达的是同一概念或高度近似概念，并产出新的全局归并组。

要求：
1. 只有在语义明确一致或高度近似时才归并，不要因为词面相似就强行合并。
2. 如果存在上下位关系但不适合直接合并，不要硬合并。
3. 如果无法确定，宁可不归并。
4. 只输出可归并的 groups；无法继续归并的 group_id 放入 ungrouped_group_ids。
5. canonical_name 必须是短、稳定、可复用的名词短语或动作短语，不要使用整句、长枚举、字段项、临时修饰词或完整规则要求。
6. 对于 object_mentions，canonical_name 应命名为对象或事项名称；对于 constraint_mentions，canonical_name 应命名为条件、门槛、程序要求或规范力度，不要承接对象内容。
7. 输出必须是严格 JSON，不要输出解释，不要输出 Markdown 代码块。

输出格式：
{
  "groups": [
    {
      "canonical_name": "",
      "member_group_ids": ["g1", "g2"],
      "reason": ""
    }
  ],
  "ungrouped_group_ids": ["g3"]
}"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="对条款原始提及做制度内与跨制度归一化。")
    parser.add_argument("--mentions-jsonl", required=True, help="clause_semantics_mentions.jsonl 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--doc-id", default="", help="只处理指定 doc_id")
    parser.add_argument("--model", default="", help="模型名，默认读取 OPENAI_ANALYSIS_MODEL")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数，默认 3")
    parser.add_argument("--business-retries", type=int, default=3, help="模型返回非 JSON 时的业务重试次数，默认 3")
    parser.add_argument("--enable-thinking", action="store_true", help="显式开启模型思考")
    parser.add_argument("--max-doc-mentions-per-batch", type=int, default=DEFAULT_MAX_DOC_MENTIONS_PER_BATCH, help="制度内归一化每批 mention 数，默认 120")
    parser.add_argument("--max-global-groups-per-batch", type=int, default=DEFAULT_MAX_GLOBAL_GROUPS_PER_BATCH, help="跨制度归一化每批组数，默认 80")
    parser.add_argument("--no-resume", action="store_true", help="禁用中间结果续跑，默认启用")
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


def read_checkpoint_rows(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"续跑文件格式错误: {path}")
    logger.info("读取归一化续跑文件 path=%s row_count=%s", path, len(rows))
    print(f"[resume] read checkpoint: {path} rows={len(rows)}", flush=True)
    return [row for row in rows if isinstance(row, dict)]


def write_checkpoint_rows(path: Path, rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "rows": rows}
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    logger.info("写入归一化续跑文件 path=%s row_count=%s", path, len(rows))
    print(f"[resume] write checkpoint: {path} rows={len(rows)}", flush=True)


def checkpoint_file_name(*parts: str) -> str:
    raw = "::".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    safe = "".join(ch if ch.isalnum() else "_" for ch in raw)[:80].strip("_")
    return f"{safe}_{digest}.json"


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
            logger.warning("条款语义归一化 content 为空，回退字段=%s", field)
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


def filter_rows(rows: list[dict[str, Any]], doc_id: str) -> list[dict[str, Any]]:
    if not doc_id:
        return rows
    return [row for row in rows if str(row.get("doc_id") or "").strip() == doc_id]


def mention_type_field(mention_category: str) -> str:
    if mention_category == "constraint_mentions":
        return "constraint_type"
    return "mention_type"


def build_raw_mention_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mention_rows: list[dict[str, Any]] = []
    for row in rows:
        doc_id = str(row.get("doc_id") or "").strip()
        title = str(row.get("title") or "").strip()
        article_no = str(row.get("article_no") or "").strip()
        article_text = str(row.get("article_text") or "").strip()
        for mention_category in MENTION_CATEGORIES:
            type_field = mention_type_field(mention_category)
            items = list(row.get(mention_category) or [])
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                mention_text = str(item.get("mention_text") or "").strip()
                if not mention_text:
                    continue
                mention_rows.append(
                    {
                        "mention_id": f"{doc_id}::{article_no}::{mention_category}::{index}",
                        "doc_id": doc_id,
                        "title": title,
                        "article_no": article_no,
                        "article_text": article_text,
                        "mention_category": mention_category,
                        "mention_text": mention_text,
                        "mention_type": str(item.get(type_field) or "").strip(),
                        "description": str(item.get("description") or "").strip(),
                    }
                )
    return mention_rows


def group_mentions_by_doc_and_category(mention_rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in mention_rows:
        key = (str(row.get("doc_id") or "").strip(), str(row.get("mention_category") or "").strip())
        grouped[key].append(row)
    return grouped


def build_doc_normalization_user_prompt(doc_id: str, title: str, mention_category: str, mention_rows: list[dict[str, Any]]) -> str:
    payload = {
        "doc_id": doc_id,
        "title": title,
        "mention_category": mention_category,
        "mentions": [
            {
                "mention_id": str(row.get("mention_id") or "").strip(),
                "mention_text": str(row.get("mention_text") or "").strip(),
                "mention_type": str(row.get("mention_type") or "").strip(),
                "description": str(row.get("description") or "").strip(),
                "article_no": str(row.get("article_no") or "").strip(),
                "article_text": str(row.get("article_text") or "").strip(),
            }
            for row in mention_rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def normalize_doc_groups_payload(payload: dict[str, Any], valid_mention_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise ValueError("制度内归一化结果缺少 groups 数组")
    normalized_groups: list[dict[str, Any]] = []
    grouped_ids: set[str] = set()
    for item in groups:
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name") or "").strip()
        member_mention_ids = item.get("member_mention_ids")
        if not canonical_name or not isinstance(member_mention_ids, list):
            continue
        member_ids = [str(mention_id).strip() for mention_id in member_mention_ids if str(mention_id).strip() in valid_mention_ids]
        if len(member_ids) < 2:
            continue
        grouped_ids.update(member_ids)
        normalized_groups.append(
            {
                "canonical_name": canonical_name,
                "member_mention_ids": member_ids,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    ungrouped_ids = payload.get("ungrouped_mention_ids")
    normalized_ungrouped_ids = []
    if isinstance(ungrouped_ids, list):
        normalized_ungrouped_ids = [str(mention_id).strip() for mention_id in ungrouped_ids if str(mention_id).strip() in valid_mention_ids and str(mention_id).strip() not in grouped_ids]
    return normalized_groups, normalized_ungrouped_ids


def build_doc_level_groups(doc_id: str, title: str, mention_category: str, mention_rows: list[dict[str, Any]], normalized_groups: list[dict[str, Any]], ungrouped_ids: list[str]) -> list[dict[str, Any]]:
    mention_map = {str(row.get("mention_id") or "").strip(): row for row in mention_rows}
    doc_groups: list[dict[str, Any]] = []
    covered_ids: set[str] = set()
    group_index = 1
    for item in normalized_groups:
        member_ids = [mention_id for mention_id in list(item.get("member_mention_ids") or []) if mention_id in mention_map]
        if len(member_ids) < 2:
            continue
        covered_ids.update(member_ids)
        doc_groups.append(
            {
                "doc_group_id": f"{doc_id}::{mention_category}::G{group_index}",
                "doc_id": doc_id,
                "title": title,
                "mention_category": mention_category,
                "canonical_name": str(item.get("canonical_name") or "").strip(),
                "member_mentions": [mention_map[mention_id] for mention_id in member_ids],
                "reason": str(item.get("reason") or "").strip(),
            }
        )
        group_index += 1
    singleton_ids = [mention_id for mention_id in ungrouped_ids if mention_id in mention_map and mention_id not in covered_ids]
    remaining_ids = [mention_id for mention_id in mention_map if mention_id not in covered_ids and mention_id not in singleton_ids]
    for mention_id in singleton_ids + remaining_ids:
        mention_row = mention_map[mention_id]
        doc_groups.append(
            {
                "doc_group_id": f"{doc_id}::{mention_category}::G{group_index}",
                "doc_id": doc_id,
                "title": title,
                "mention_category": mention_category,
                "canonical_name": str(mention_row.get("mention_text") or "").strip(),
                "member_mentions": [mention_row],
                "reason": "制度内未与其他提及归并",
            }
        )
        group_index += 1
    return doc_groups


# 调用大模型做单个制度批次的制度内归一化。
async def normalize_doc_category_batch(llm: AsyncLLMClient, doc_id: str, title: str, mention_category: str, mention_rows: list[dict[str, Any]], batch_index: int, batch_total: int, model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    async with sem:
        logger.info("开始制度内归一化 doc_id=%s mention_category=%s batch=%s/%s mention_count=%s", doc_id, mention_category, batch_index, batch_total, len(mention_rows))
        messages = [{"role": "system", "content": DOC_NORMALIZATION_SYSTEM_PROMPT}, {"role": "user", "content": build_doc_normalization_user_prompt(doc_id, title, mention_category, mention_rows)}]
        valid_mention_ids = {str(row.get("mention_id") or "").strip() for row in mention_rows}
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("制度内归一化模型返回 doc_id=%s mention_category=%s batch=%s/%s mention_count=%s attempt=%s/%s content=%s", doc_id, mention_category, batch_index, batch_total, len(mention_rows), attempt, attempts, preview_text(content))
            try:
                payload = parse_json_payload(content)
                normalized_groups, ungrouped_ids = normalize_doc_groups_payload(payload, valid_mention_ids)
                result_rows = build_doc_level_groups(doc_id, title, mention_category, mention_rows, normalized_groups, ungrouped_ids)
                logger.info("完成制度内归一化 doc_id=%s mention_category=%s batch=%s/%s mention_count=%s group_count=%s", doc_id, mention_category, batch_index, batch_total, len(mention_rows), len(result_rows))
                return result_rows
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("制度内归一化解析失败 doc_id=%s mention_category=%s batch=%s/%s attempt=%s/%s err=%s", doc_id, mention_category, batch_index, batch_total, attempt, attempts, last_error)
        raise ValueError(f"制度内归一化失败 doc_id={doc_id} mention_category={mention_category} batch={batch_index}/{batch_total}: {last_error}; 原始输出: {last_content}")


def group_doc_level_groups_by_category(doc_level_groups: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in doc_level_groups:
        mention_category = str(row.get("mention_category") or "").strip()
        grouped[mention_category].append(row)
    return grouped


def build_global_normalization_user_prompt(mention_category: str, groups: list[dict[str, Any]]) -> str:
    payload = {
        "mention_category": mention_category,
        "groups": [
            {
                "group_id": str(row.get("group_id") or "").strip(),
                "canonical_name": str(row.get("canonical_name") or "").strip(),
                "doc_count": len({str(item.get("doc_id") or "").strip() for item in list(row.get("member_mentions") or [])}),
                "member_texts": collect_member_texts(row),
                "sample_contexts": collect_sample_contexts(row),
            }
            for row in groups
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def collect_member_texts(group: dict[str, Any], limit: int = 8) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()
    for item in list(group.get("member_mentions") or []):
        mention_text = str(item.get("mention_text") or "").strip()
        if not mention_text or mention_text in seen:
            continue
        seen.add(mention_text)
        texts.append(mention_text)
        if len(texts) >= limit:
            break
    return texts


def collect_sample_contexts(group: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    contexts: list[dict[str, str]] = []
    for item in list(group.get("member_mentions") or [])[:limit]:
        contexts.append(
            {
                "doc_id": str(item.get("doc_id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "article_no": str(item.get("article_no") or "").strip(),
                "article_text": str(item.get("article_text") or "").strip(),
            }
        )
    return contexts


def normalize_global_groups_payload(payload: dict[str, Any], valid_group_ids: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise ValueError("跨制度归一化结果缺少 groups 数组")
    normalized_groups: list[dict[str, Any]] = []
    grouped_ids: set[str] = set()
    for item in groups:
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name") or "").strip()
        member_group_ids = item.get("member_group_ids")
        if not canonical_name or not isinstance(member_group_ids, list):
            continue
        group_ids = [str(group_id).strip() for group_id in member_group_ids if str(group_id).strip() in valid_group_ids]
        if len(group_ids) < 2:
            continue
        grouped_ids.update(group_ids)
        normalized_groups.append(
            {
                "canonical_name": canonical_name,
                "member_group_ids": group_ids,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    ungrouped_group_ids = payload.get("ungrouped_group_ids")
    normalized_ungrouped_ids = []
    if isinstance(ungrouped_group_ids, list):
        normalized_ungrouped_ids = [str(group_id).strip() for group_id in ungrouped_group_ids if str(group_id).strip() in valid_group_ids and str(group_id).strip() not in grouped_ids]
    return normalized_groups, normalized_ungrouped_ids


def add_runtime_group_ids(groups: list[dict[str, Any]], mention_category: str, round_index: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(sorted(groups, key=lambda row: str(row.get("canonical_name") or "").strip()), start=1):
        row = dict(item)
        row["group_id"] = f"{mention_category}::R{round_index}::G{index}"
        rows.append(row)
    return rows


def merge_batch_groups(batch_groups: list[dict[str, Any]], normalized_groups: list[dict[str, Any]], ungrouped_group_ids: list[str]) -> list[dict[str, Any]]:
    group_map = {str(row.get("group_id") or "").strip(): row for row in batch_groups}
    merged_rows: list[dict[str, Any]] = []
    covered_group_ids: set[str] = set()
    for item in normalized_groups:
        member_group_ids = [group_id for group_id in list(item.get("member_group_ids") or []) if group_id in group_map]
        if len(member_group_ids) < 2:
            continue
        covered_group_ids.update(member_group_ids)
        member_mentions: list[dict[str, Any]] = []
        for group_id in member_group_ids:
            member_mentions.extend(list(group_map[group_id].get("member_mentions") or []))
        merged_rows.append(
            {
                "mention_category": str(batch_groups[0].get("mention_category") or "").strip() if batch_groups else "",
                "canonical_name": str(item.get("canonical_name") or "").strip(),
                "member_mentions": member_mentions,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    passthrough_ids = [group_id for group_id in ungrouped_group_ids if group_id in group_map and group_id not in covered_group_ids]
    remaining_ids = [group_id for group_id in group_map if group_id not in covered_group_ids and group_id not in passthrough_ids]
    for group_id in passthrough_ids + remaining_ids:
        group_row = group_map[group_id]
        merged_rows.append(
            {
                "mention_category": str(group_row.get("mention_category") or "").strip(),
                "canonical_name": str(group_row.get("canonical_name") or "").strip(),
                "member_mentions": list(group_row.get("member_mentions") or []),
                "reason": str(group_row.get("reason") or "").strip() or "跨制度未与其他归并组继续归并",
            }
        )
    return merged_rows


def split_list(items: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    size = max(1, int(batch_size))
    return [items[index:index + size] for index in range(0, len(items), size)]


def prepare_groups_for_merge(groups: list[dict[str, Any]], mention_category: str, group_prefix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(sorted(groups, key=lambda row: str(row.get("canonical_name") or "").strip()), start=1):
        row = dict(item)
        row["group_id"] = f"{group_prefix}::{mention_category}::G{index}"
        rows.append(row)
    return rows


# 调用大模型做单批跨制度归一化。
async def normalize_global_group_batch(llm: AsyncLLMClient, mention_category: str, batch_groups: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    async with sem:
        messages = [{"role": "system", "content": GLOBAL_NORMALIZATION_SYSTEM_PROMPT}, {"role": "user", "content": build_global_normalization_user_prompt(mention_category, batch_groups)}]
        valid_group_ids = {str(row.get("group_id") or "").strip() for row in batch_groups}
        last_error = ""
        last_content = ""
        attempts = max(1, int(business_retries))
        for attempt in range(1, attempts + 1):
            resp = await llm.chat(messages=messages, model=model, temperature=0.0, extra_body=thinking_extra_body)
            content = extract_message_text(resp)
            last_content = content
            logger.info("跨制度归一化模型返回 mention_category=%s batch_size=%s attempt=%s/%s content=%s", mention_category, len(batch_groups), attempt, attempts, preview_text(content))
            try:
                payload = parse_json_payload(content)
                normalized_groups, ungrouped_group_ids = normalize_global_groups_payload(payload, valid_group_ids)
                return merge_batch_groups(batch_groups, normalized_groups, ungrouped_group_ids)
            except Exception as err:  # pylint: disable=broad-except
                last_error = f"{err}; 模型输出预览: {preview_text(content)}"
                logger.warning("跨制度归一化解析失败 mention_category=%s batch_size=%s attempt=%s/%s err=%s", mention_category, len(batch_groups), attempt, attempts, last_error)
        raise ValueError(f"跨制度归一化失败 mention_category={mention_category}: {last_error}; 原始输出: {last_content}")


def build_doc_level_merged_rows(doc_id: str, title: str, mention_category: str, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(groups, start=1):
        member_mentions = list(item.get("member_mentions") or [])
        if not member_mentions:
            continue
        rows.append(
            {
                "doc_group_id": f"{doc_id}::{mention_category}::G{index}",
                "doc_id": doc_id,
                "title": title,
                "mention_category": mention_category,
                "canonical_name": str(item.get("canonical_name") or "").strip(),
                "member_mentions": member_mentions,
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return rows


async def normalize_doc_category_mentions(llm: AsyncLLMClient, doc_id: str, title: str, mention_category: str, mention_rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, max_doc_mentions_per_batch: int, max_global_groups_per_batch: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    batches = split_list(mention_rows, max_doc_mentions_per_batch)
    if not batches:
        return []
    if len(batches) == 1:
        return await normalize_doc_category_batch(llm, doc_id, title, mention_category, batches[0], 1, 1, model, thinking_extra_body, business_retries, sem)
    batch_tasks = [
        asyncio.create_task(normalize_doc_category_batch(llm, doc_id, title, mention_category, batch_rows, batch_index, len(batches), model, thinking_extra_body, business_retries, sem))
        for batch_index, batch_rows in enumerate(batches, start=1)
    ]
    batch_results = await asyncio.gather(*batch_tasks)
    doc_level_groups = [item for batch in batch_results for item in batch]
    logger.info("开始制度内跨批次归一化 doc_id=%s mention_category=%s batch_count=%s pre_group_count=%s", doc_id, mention_category, len(batches), len(doc_level_groups))
    prepared_groups = prepare_groups_for_merge(doc_level_groups, mention_category, f"{doc_id}::DOCMERGE")
    merged_groups = await normalize_global_groups(llm, mention_category, prepared_groups, model, thinking_extra_body, business_retries, max_global_groups_per_batch, sem)
    result_rows = build_doc_level_merged_rows(doc_id, title, mention_category, merged_groups)
    logger.info("完成制度内跨批次归一化 doc_id=%s mention_category=%s final_group_count=%s", doc_id, mention_category, len(result_rows))
    return result_rows


async def normalize_doc_category_mentions_with_checkpoint(llm: AsyncLLMClient, doc_id: str, title: str, mention_category: str, mention_rows: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, max_doc_mentions_per_batch: int, max_global_groups_per_batch: int, sem: asyncio.Semaphore, checkpoint_dir: Path, resume_enabled: bool) -> list[dict[str, Any]]:
    checkpoint_path = checkpoint_dir / "doc_level" / checkpoint_file_name(doc_id, mention_category)
    if resume_enabled:
        cached_rows = read_checkpoint_rows(checkpoint_path)
        if cached_rows is not None:
            return cached_rows
    print(f"[doc-level] start doc_id={doc_id} category={mention_category} mentions={len(mention_rows)}", flush=True)
    rows = await normalize_doc_category_mentions(llm, doc_id, title, mention_category, mention_rows, model, thinking_extra_body, business_retries, max_doc_mentions_per_batch, max_global_groups_per_batch, sem)
    print(f"[doc-level] done doc_id={doc_id} category={mention_category} groups={len(rows)}", flush=True)
    if resume_enabled:
        write_checkpoint_rows(checkpoint_path, rows, {"stage": "doc_level", "doc_id": doc_id, "title": title, "mention_category": mention_category, "mention_count": len(mention_rows)})
    return rows


async def normalize_global_groups(llm: AsyncLLMClient, mention_category: str, groups: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, max_groups_per_batch: int, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    if not groups:
        return []
    current_groups = list(groups)
    round_index = 1
    while True:
        if not all(str(row.get("group_id") or "").strip() for row in current_groups):
            current_groups = add_runtime_group_ids(current_groups, mention_category, round_index)
        batches = split_list(current_groups, max_groups_per_batch)
        if not batches:
            return []
        tasks = [
            asyncio.create_task(normalize_global_group_batch(llm, mention_category, batch_groups, model, thinking_extra_body, business_retries, sem))
            for batch_groups in batches
        ]
        batch_results = await asyncio.gather(*tasks)
        next_groups = [item for batch in batch_results for item in batch]
        logger.info("跨制度归一化完成一轮 mention_category=%s round=%s before=%s after=%s", mention_category, round_index, len(current_groups), len(next_groups))
        if len(batches) == 1 or len(next_groups) >= len(current_groups):
            return add_final_group_ids(next_groups, mention_category)
        current_groups = next_groups
        round_index += 1


async def normalize_global_groups_with_checkpoint(llm: AsyncLLMClient, mention_category: str, groups: list[dict[str, Any]], model: str, thinking_extra_body: dict[str, Any] | None, business_retries: int, max_groups_per_batch: int, sem: asyncio.Semaphore, checkpoint_dir: Path, resume_enabled: bool) -> list[dict[str, Any]]:
    checkpoint_path = checkpoint_dir / "global" / checkpoint_file_name(mention_category)
    if resume_enabled:
        cached_rows = read_checkpoint_rows(checkpoint_path)
        if cached_rows is not None:
            return cached_rows
    print(f"[global] start category={mention_category} input_groups={len(groups)}", flush=True)
    rows = await normalize_global_groups(llm, mention_category, groups, model, thinking_extra_body, business_retries, max_groups_per_batch, sem)
    print(f"[global] done category={mention_category} groups={len(rows)}", flush=True)
    if resume_enabled:
        write_checkpoint_rows(checkpoint_path, rows, {"stage": "global", "mention_category": mention_category, "input_group_count": len(groups)})
    return rows


def add_final_group_ids(groups: list[dict[str, Any]], mention_category: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(sorted(groups, key=lambda row: str(row.get("canonical_name") or "").strip()), start=1):
        row = dict(item)
        row["normalization_group_id"] = f"{mention_category}::N{index}"
        rows.append(row)
    return rows


def flatten_final_normalization_rows(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in groups:
        rows.append(
            {
                "normalization_group_id": str(item.get("normalization_group_id") or "").strip(),
                "mention_category": str(item.get("mention_category") or "").strip(),
                "canonical_name": str(item.get("canonical_name") or "").strip(),
                "member_mentions": list(item.get("member_mentions") or []),
                "reason": str(item.get("reason") or "").strip(),
            }
        )
    return rows


def build_summary(mention_rows: list[dict[str, Any]], doc_level_groups: list[dict[str, Any]], final_rows: list[dict[str, Any]]) -> dict[str, Any]:
    mention_category_counts: dict[str, int] = defaultdict(int)
    for row in mention_rows:
        mention_category_counts[str(row.get("mention_category") or "").strip()] += 1
    doc_group_counts: dict[str, int] = defaultdict(int)
    for row in doc_level_groups:
        doc_group_counts[str(row.get("mention_category") or "").strip()] += 1
    normalization_group_counts: dict[str, int] = defaultdict(int)
    for row in final_rows:
        normalization_group_counts[str(row.get("mention_category") or "").strip()] += 1
    return {
        "doc_count": len({str(row.get("doc_id") or "").strip() for row in mention_rows}),
        "mention_count": len(mention_rows),
        "doc_level_group_count": len(doc_level_groups),
        "normalization_group_count": len(final_rows),
        "mention_category_counts": dict(mention_category_counts),
        "doc_level_group_category_counts": dict(doc_group_counts),
        "normalization_group_category_counts": dict(normalization_group_counts),
    }


async def async_main() -> int:
    args = build_parser().parse_args()
    load_env_files()
    mentions_jsonl = Path(args.mentions_jsonl).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    resume_enabled = not bool(args.no_resume)
    checkpoint_dir = output_dir / "checkpoints"

    mention_source_rows = filter_rows(load_jsonl_rows(mentions_jsonl), str(args.doc_id or "").strip())
    if not mention_source_rows:
        raise ValueError("未找到符合条件的原始提及记录")
    mention_rows = build_raw_mention_rows(mention_source_rows)
    if not mention_rows:
        raise ValueError("原始提及记录为空，无法归一化")

    model = str(args.model or "").strip() or get_analysis_model()
    thinking_extra_body = build_thinking_extra_body(model, bool(args.enable_thinking))
    llm = AsyncLLMClient(config=ModelConfig(default_model=model), name="zhidu_clause_semantics_normalize")
    sem = asyncio.Semaphore(max(1, int(args.concurrency)))

    grouped_doc_mentions = group_mentions_by_doc_and_category(mention_rows)
    logger.info("开始条款语义归一化 doc_count=%s mention_count=%s model=%s thinking_extra_body=%s", len({key[0] for key in grouped_doc_mentions}), len(mention_rows), model, thinking_extra_body)
    print(f"[start] doc_count={len({key[0] for key in grouped_doc_mentions})} task_count={len(grouped_doc_mentions)} mention_count={len(mention_rows)} resume={resume_enabled} checkpoint_dir={checkpoint_dir}", flush=True)
    doc_tasks = []
    for (doc_id, mention_category), rows in grouped_doc_mentions.items():
        title = str(rows[0].get("title") or "").strip() if rows else ""
        doc_tasks.append(asyncio.create_task(normalize_doc_category_mentions_with_checkpoint(llm, doc_id, title, mention_category, rows, model, thinking_extra_body, int(args.business_retries), int(args.max_doc_mentions_per_batch), int(args.max_global_groups_per_batch), sem, checkpoint_dir, resume_enabled)))
    doc_task_results = await asyncio.gather(*doc_tasks, return_exceptions=True)
    doc_errors = [result for result in doc_task_results if isinstance(result, Exception)]
    doc_level_groups = [item for batch in doc_task_results if isinstance(batch, list) for item in batch]
    if doc_errors:
        error_preview = "; ".join(str(error) for error in doc_errors[:3])
        logger.error("制度内归一化存在失败 success_group_count=%s error_count=%s errors=%s", len(doc_level_groups), len(doc_errors), error_preview)
        raise RuntimeError(f"制度内归一化存在失败，已保留成功块，可重跑续跑。error_count={len(doc_errors)} errors={error_preview}")

    grouped_doc_level = group_doc_level_groups_by_category(doc_level_groups)
    final_group_rows: list[dict[str, Any]] = []
    for mention_category, groups in grouped_doc_level.items():
        normalized_groups = await normalize_global_groups_with_checkpoint(llm, mention_category, groups, model, thinking_extra_body, int(args.business_retries), int(args.max_global_groups_per_batch), sem, checkpoint_dir, resume_enabled)
        final_group_rows.extend(flatten_final_normalization_rows(normalized_groups))

    output_jsonl = output_dir / "clause_semantics_normalization.jsonl"
    output_summary = output_dir / "clause_semantics_normalization_summary.json"
    write_jsonl_rows(output_jsonl, final_group_rows)
    write_json(output_summary, build_summary(mention_rows, doc_level_groups, final_group_rows))
    print(f"normalization jsonl: {output_jsonl}")
    print(f"summary: {output_summary}")
    print(f"checkpoints: {checkpoint_dir}")
    print(f"doc_count={len({str(row.get('doc_id') or '').strip() for row in mention_rows})} normalization_group_count={len(final_group_rows)}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
