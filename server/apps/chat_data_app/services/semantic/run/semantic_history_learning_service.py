"""指标集历史学习服务。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from langchain_core.messages import HumanMessage

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.database import get_session
from utils.logger import get_logger
from utils.llm_json import parse_llm_json

from ....agent.prompts.history_learning import build_history_learning_prompt
from ....daos.semantic_history_learning_dao import SemanticHistoryLearningDao
from .... import AGENT_ID
from ... import conversation_service as conversation
from ..core.common import LOGICAL_TYPE_VALUES, build_table_columns_map
from ...llm_config import get_chat_data_llm
from ...sql_run.parser import extract_physical_tables

logger = get_logger(__name__)

ALLOWED_HISTORY_LEARNING_SOURCES = {"agent", "builder", "sql"}
HISTORY_LEARNING_EMPTY_RESULT = {"metrics": [], "dimensions": [], "issues": []}
SKIP_COUNTER_TEMPLATE = {
    "source": 0,
    "semantic_config": 0,
    "missing_conversation_context": 0,
    "empty_scope": 0,
    "sql_parse_failed": 0,
    "empty_history_tables": 0,
    "table_scope_mismatch": 0,
    "config_id_mismatch": 0,
}


def _parse_json_object(raw: Any) -> Dict[str, Any]:
    """解析 JSON 对象字符串。"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_history_task_semantic_id(payload: Dict[str, Any]) -> str:
    """从任务请求中提取语义配置ID。"""
    return str((payload or {}).get("semantic_config_id") or "").strip()


def _extract_history_task_query_text(payload: Dict[str, Any]) -> str:
    """从任务请求中提取原始问题或查询说明。"""
    return str((payload or {}).get("query_text") or "").strip()


def _extract_history_task_conversation_id(payload: Dict[str, Any]) -> str:
    """从任务请求中提取来源会话ID。"""
    return str((payload or {}).get("conversation_id") or "").strip()


def _history_message_text(content: Any) -> str:
    """提取对话消息可读文本。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _is_negative_feedback_text(text: str) -> bool:
    """识别明显负反馈，只作为学习信号，不沉淀为业务事实。"""
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    patterns = [
        "不对", "不是", "错了", "错误", "不准确", "不应该", "没有", "漏了",
        "少了", "查不到", "不符合", "不合理", "wrong", "incorrect",
    ]
    return any(pattern in normalized for pattern in patterns)


def _is_history_tool_error_message(text: str) -> bool:
    """识别工具消息是否真的是失败结果。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    payload_text = raw.split("\n", 1)[1].strip() if "\n" in raw and raw.startswith("[") else raw
    try:
        payload = json.loads(payload_text)
        if isinstance(payload, dict):
            if payload.get("success") is False:
                return True
            error_value = payload.get("error")
            return error_value not in (None, "", False)
    except Exception as exc:
        logger.warning("历史学习工具消息 JSON 解析失败,回退关键词识别失败语义: error=%s", exc)
    lowered = raw.lower()
    return "失败" in raw or "错误" in raw or "exception" in lowered or "traceback" in lowered


def _collect_user_turn(
    text: str,
    timestamp: str,
    user_turns: List[Dict[str, str]],
    negative_feedback: List[str],
) -> None:
    """记录用户发言（截断至 500 字），负反馈文本额外收集。"""
    clipped = text[:500]
    user_turns.append({"content": clipped, "timestamp": timestamp})
    if _is_negative_feedback_text(text):
        negative_feedback.append(clipped)


def _collect_tool_error(msg: Dict[str, Any], text: str, timestamp: str, tool_errors: List[Dict[str, str]]) -> None:
    """记录工具错误消息（截断至 600 字）。"""
    tool_errors.append({
        "tool_name": str((msg or {}).get("tool_name") or "").strip(),
        "content": text[:600],
        "timestamp": timestamp,
    })


def _compact_history_conversation_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """裁剪智能问数对话上下文，保留用户意图、反馈和工具错误。"""
    user_turns: List[Dict[str, str]] = []
    assistant_turns: List[Dict[str, str]] = []
    tool_errors: List[Dict[str, str]] = []
    negative_feedback: List[str] = []
    for msg in messages or []:
        role = str((msg or {}).get("role") or "").strip()
        text = _history_message_text((msg or {}).get("content"))
        if not text:
            continue
        timestamp = str((msg or {}).get("timestamp") or "").strip()
        if role == "user":
            _collect_user_turn(text, timestamp, user_turns, negative_feedback)
        elif role == "assistant":
            if not (msg or {}).get("tool_calls"):
                assistant_turns.append({"content": text[:500], "timestamp": timestamp})
        elif role == "tool" and _is_history_tool_error_message(text):
            _collect_tool_error(msg, text, timestamp, tool_errors)
    return {
        "user_turns": user_turns[-6:],
        "assistant_turns": assistant_turns[-3:],
        "negative_feedback": negative_feedback[-5:],
        "tool_errors": tool_errors[-5:],
    }


async def _load_history_conversation_context(conversation_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """读取智能问数对话上下文，用于历史学习。"""
    normalized_id = str(conversation_id or "").strip()
    if not normalized_id:
        return None
    try:
        agent_id = await conversation.get_agent_id(normalized_id, user_id)
        if agent_id != AGENT_ID:
            logger.warning("历史学习跳过未知 Agent 会话: conversation_id=%s agent_id=%s", normalized_id, agent_id)
            return None
        detail = await conversation.get_detail(normalized_id, user_id)
        messages = (detail or {}).get("messages") if isinstance(detail, dict) else []
        if not isinstance(messages, list):
            return None
        context = _compact_history_conversation_messages(messages)
        return context if context.get("user_turns") else None
    except Exception as err:
        logger.warning("读取历史学习对话上下文失败: conversation_id=%s error=%s", normalized_id, err, exc_info=True)
        return None


_SEMANTIC_SCOPE_FIELD_SPECS = (
    # (集合键, 该集合条目中参与取表的字段)
    ("metrics", ("table",)),
    ("dimensions", ("table",)),
    ("relationships", ("left_table", "right_table")),
)


def _semantic_scope_tables(config: Dict[str, Any]) -> Set[str]:
    """提取指标集涉及的表。"""
    tables: Set[str] = set()
    for collection_key, field_names in _SEMANTIC_SCOPE_FIELD_SPECS:
        for item in config.get(collection_key) or []:
            for field_name in field_names:
                table = str((item or {}).get(field_name) or "").strip()
                if table:
                    tables.add(table)
    return tables


def _history_belongs_to_config(
    query_type: str,
    task_payload: Dict[str, Any],
    sql: str,
    config_id: str,
    config: Dict[str, Any],
    source_type: str,
    *,
    table_parser: Optional[Callable[[str, str], Any]] = None,
) -> Tuple[bool, str, List[str], str]:
    """判断历史记录是否属于当前指标集。"""
    task_config_id = _extract_history_task_semantic_id(task_payload)
    if query_type in {"agent", "builder"}:
        if not task_config_id or task_config_id != config_id:
            return False, "config_id_mismatch", [], ""
        return True, "matched_config_id", [], ""

    scope_tables = _semantic_scope_tables(config)
    if not scope_tables:
        return False, "empty_scope", [], ""
    parser = table_parser or (lambda sql_text, source: extract_physical_tables(sql_text, source_type=source))
    parsed = parser(sql, source_type)
    if not parsed.success:
        return False, "sql_parse_failed", [], parsed.error
    history_tables = parsed.tables
    if not history_tables:
        return False, "empty_history_tables", [], ""
    if not history_tables.issubset(scope_tables):
        return False, "table_scope_mismatch", sorted(history_tables), ""
    return True, "matched_table_scope", sorted(history_tables), ""


def _history_learning_table_columns(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """裁剪单表字段列表，保留名称/类型/描述。"""
    columns: List[Dict[str, Any]] = []
    for col in table.get("columns", []) or []:
        col_name = str((col or {}).get("name") or "").strip()
        if not col_name:
            continue
        columns.append({
            "name": col_name,
            "type": (col or {}).get("mysql_type") or (col or {}).get("hive_type") or (col or {}).get("type") or "",
            "description": (col or {}).get("description") or "",
        })
    return columns


def _build_history_learning_table_context(datasource: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """裁剪指标集相关表结构给历史学习模型。"""
    scope_tables = _semantic_scope_tables(config)
    items: List[Dict[str, Any]] = []
    for table in datasource.get("ds_tables", []) or []:
        table = table or {}
        table_name = str(table.get("table_name") or "").strip()
        if scope_tables and table_name not in scope_tables:
            continue
        items.append({
            "table_name": table_name,
            "description": table.get("description") or "",
            "columns": _history_learning_table_columns(table)[:80],
        })
    return items[:20]


def _compact_history_sample_for_llm(sample: Dict[str, Any]) -> Dict[str, Any]:
    """压缩单条历史，控制 LLM 输入长度。"""
    semantic_builder_config = sample.get("semantic_builder_config") if isinstance(sample.get("semantic_builder_config"), dict) else None
    builder_config = sample.get("builder_config") if isinstance(sample.get("builder_config"), dict) else None
    compact: Dict[str, Any] = {
        "execution_id": sample.get("execution_id"),
        "source": sample.get("source"),
        "status": sample.get("status"),
        "query_text": str(sample.get("query_text") or "")[:300],
        "sql": str(sample.get("sql") or "")[:1200],
        "error_message": str(sample.get("error_message") or "")[:500],
        "executed_at": sample.get("executed_at"),
    }
    conversation_context = sample.get("conversation_context")
    if isinstance(conversation_context, dict) and sample.get("source") == "agent":
        compact["conversation_context"] = conversation_context
    builder_payload = _compact_builder_config_payload(semantic_builder_config, builder_config)
    if builder_payload is not None:
        compact["builder_config"] = builder_payload
    return compact


def _compact_builder_config_payload(
    semantic_builder_config: Optional[Dict[str, Any]],
    builder_config: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """压缩 builder 配置；两类配置都缺失时返回 None。"""
    if not semantic_builder_config and not builder_config:
        return None
    source_config = semantic_builder_config or builder_config or {}
    return {
        "mainTable": source_config.get("mainTable"),
        "joins": (source_config.get("joins") or [])[:10],
        "dimensions": (source_config.get("dimensions") or [])[:20],
        "aggregations": (source_config.get("aggregations") or source_config.get("metrics") or [])[:20],
        "filters": (source_config.get("filters") or [])[:20],
        "orderBy": (source_config.get("orderBy") or source_config.get("order_by") or [])[:10],
    }


def _sample_map_by_execution_id(samples: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按执行ID索引历史样例。"""
    return {str(item.get("execution_id") or ""): item for item in samples if str(item.get("execution_id") or "")}


def _normalize_semantic_text(value: Any) -> str:
    """统一语义字段比较口径。"""
    return str(value or "").strip().strip("`").lower()


def _normalize_semantic_expression(value: Any) -> str:
    """统一表达式比较口径。"""
    return re.sub(r"\s+", "", str(value or "").strip().strip("`").lower())


def _extract_expression_columns(expression: Any) -> Set[str]:
    """从指标表达式中提取可能引用的物理字段。"""
    text = str(expression or "").strip()
    if not text:
        return set()
    keywords = {
        "case", "when", "then", "else", "end", "filter", "where", "distinct", "count", "sum", "avg", "max", "min",
        "and", "or", "not", "null", "is", "in", "as", "on", "if", "cast", "char", "date", "date_format", "str_to_date",
        "unix_timestamp", "from_unixtime", "year", "month", "day",
    }
    columns: Set[str] = set()
    for match in re.finditer(r"`([^`]+)`|(?<!['\"])\b([A-Za-z_][A-Za-z0-9_]*)\b(?!['\"])", text):
        token = str(match.group(1) or match.group(2) or "").strip()
        if not token:
            continue
        if text[match.end():].lstrip().startswith("."):
            continue
        token = token.split(".")[-1].strip("`")
        if token and token.lower() not in keywords:
            columns.add(token)
    return columns


def _index_metric_item(item: Any, indexes: Dict[str, Set[str]]) -> None:
    """登记单个指标的名字与 表.表达式 索引。"""
    if not isinstance(item, dict):
        return
    table = _normalize_semantic_text(item.get("table"))
    name = _normalize_semantic_text(item.get("name"))
    expression = _normalize_semantic_expression(item.get("expression"))
    if name:
        indexes["metric_names"].add(name)
    if table and expression:
        indexes["metric_expressions"].add(f"{table}.{expression}")


def _index_dimension_item(item: Any, indexes: Dict[str, Set[str]]) -> None:
    """登记单个维度的名字与 表.列 / 表.字段 索引。"""
    if not isinstance(item, dict):
        return
    table = _normalize_semantic_text(item.get("table"))
    name = _normalize_semantic_text(item.get("name"))
    field_name = _normalize_semantic_text(item.get("field_name"))
    column = _normalize_semantic_text(item.get("column") or item.get("field_name"))
    if name:
        indexes["dimension_names"].add(name)
    if table and column:
        indexes["dimension_columns"].add(f"{table}.{column}")
    if table and field_name:
        indexes["dimension_fields"].add(f"{table}.{field_name}")


def _build_existing_semantic_indexes(config: Dict[str, Any]) -> Dict[str, Set[str]]:
    """构建当前指标集已维护字段索引，用于过滤历史学习重复建议。"""
    indexes: Dict[str, Set[str]] = {
        "metric_names": set(),
        "metric_expressions": set(),
        "dimension_names": set(),
        "dimension_columns": set(),
        "dimension_fields": set(),
    }
    for item in config.get("metrics") or []:
        _index_metric_item(item, indexes)
    for item in config.get("dimensions") or []:
        _index_dimension_item(item, indexes)
    return indexes


def _is_existing_history_suggestion(item: Dict[str, Any], category: str, indexes: Dict[str, Set[str]]) -> bool:
    """判断历史学习建议是否已在当前指标集维护。"""
    if category == "metrics":
        table = _normalize_semantic_text(item.get("table"))
        name = _normalize_semantic_text(item.get("name"))
        expression = _normalize_semantic_expression(item.get("expression"))
        if name and name in indexes["metric_names"]:
            return True
        return bool(table and expression and f"{table}.{expression}" in indexes["metric_expressions"])
    if category == "dimensions":
        table = _normalize_semantic_text(item.get("table"))
        name = _normalize_semantic_text(item.get("name"))
        field_name = _normalize_semantic_text(item.get("field_name"))
        column = _normalize_semantic_text(item.get("column") or item.get("field_name"))
        if name and name in indexes["dimension_names"]:
            return True
        if table and column and f"{table}.{column}" in indexes["dimension_columns"]:
            return True
        return bool(table and field_name and f"{table}.{field_name}" in indexes["dimension_fields"])
    return False


def _normalize_history_base_fields(item: Dict[str, Any], sample_map: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    source = str(item.get("source") or "").strip().lower()
    confidence = str(item.get("confidence") or "").strip().lower()
    sample_ids = item.get("sample_execution_ids")
    if source not in ALLOWED_HISTORY_LEARNING_SOURCES or confidence not in {"high", "medium", "low"}:
        return None
    if not isinstance(sample_ids, list):
        return None
    samples = [sample_map[sid] for sid in [str(s or "") for s in sample_ids] if sid in sample_map][:3]
    if not samples:
        return None
    try:
        support_count = max(1, int(item.get("support_count")))
    except Exception:
        return None
    return {
        "reason": str(item.get("reason") or "").strip(),
        "source": source,
        "confidence": confidence,
        "support_count": support_count,
        "samples": samples,
    }


def _normalize_metric_or_dimension_suggestion(item: Dict[str, Any], category: str, normalized: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    normalized.update({
        "name": str(item.get("name") or "").strip(),
        "table": str(item.get("table") or "").strip(),
        "field_name": str(item.get("field_name") or "").strip(),
        "expression": str(item.get("expression") or "").strip(),
    })
    if category == "dimensions":
        logical_type = str(item.get("logical_type") or "").strip().lower()
        if not logical_type and "type" not in item:
            logical_type = "text"
        if logical_type not in LOGICAL_TYPE_VALUES:
            return None
        normalized["logical_type"] = logical_type
        normalized["column"] = str(item.get("column") or item.get("field_name") or "").strip()
    return normalized if normalized["name"] and normalized["reason"] else None


def _normalize_issue_suggestion(item: Dict[str, Any], normalized: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    normalized.update({
        "title": str(item.get("title") or "").strip(),
        "content": str(item.get("content") or "").strip(),
    })
    return normalized if normalized["title"] and normalized["content"] and normalized["reason"] else None


def _empty_history_learning_skip_counts() -> Dict[str, Dict[str, int]]:
    """初始化历史学习建议过滤计数。"""
    return {
        category: {"invalid": 0, "existing": 0, "duplicate": 0}
        for category in ("metrics", "dimensions", "issues")
    }


def _dimension_review_reasons(
    normalized: Dict[str, Any],
    table_name: str,
    table_columns: Dict[str, Set[str]],
) -> List[str]:
    reasons: List[str] = []
    column_name = str(normalized.get("column") or normalized.get("field_name") or "").strip()
    if not column_name:
        reasons.append("缺少字段名，需要人工确认")
    if table_name and column_name and table_name in table_columns and column_name not in table_columns[table_name]:
        reasons.append(f"字段不在当前表结构中：{table_name}.{column_name}")
    return reasons


def _metric_review_reasons(
    normalized: Dict[str, Any],
    table_name: str,
    table_columns: Dict[str, Set[str]],
) -> List[str]:
    reasons: List[str] = []
    expression = str(normalized.get("expression") or "").strip()
    if not expression:
        reasons.append("缺少指标表达式，需要人工确认")
    expression_columns = _extract_expression_columns(normalized.get("expression"))
    missing_columns = sorted(col for col in expression_columns if table_name in table_columns and col not in table_columns[table_name])
    if table_name and missing_columns:
        reasons.append(f"表达式引用字段不在当前表结构中：{table_name}.{', '.join(missing_columns)}")
    return reasons


def _history_suggestion_review_reasons(
    normalized: Dict[str, Any],
    category: str,
    datasource: Dict[str, Any],
) -> List[str]:
    table_columns = build_table_columns_map(datasource)
    table_name = str(normalized.get("table") or "").strip()
    review_reasons: List[str] = []
    if category in {"metrics", "dimensions"} and not table_name:
        review_reasons.append("缺少表名，需要人工确认")
    if table_name and table_name not in table_columns:
        review_reasons.append(f"表不在当前数据源缓存中：{table_name}")
    if category == "dimensions":
        review_reasons.extend(_dimension_review_reasons(normalized, table_name, table_columns))
    if category == "metrics":
        review_reasons.extend(_metric_review_reasons(normalized, table_name, table_columns))
    return review_reasons


def _normalize_history_suggestion_item(
    item: Any,
    category: str,
    sample_map: Dict[str, Dict[str, Any]],
    datasource: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """校验并标准化 LLM 输出的历史学习建议。"""
    if not isinstance(item, dict):
        return None
    normalized = _normalize_history_base_fields(item, sample_map)
    if not normalized:
        return None
    if category in {"metrics", "dimensions"}:
        normalized = _normalize_metric_or_dimension_suggestion(item, category, normalized)
    else:
        normalized = _normalize_issue_suggestion(item, normalized)
    if not normalized:
        return None
    review_reasons = _history_suggestion_review_reasons(normalized, category, datasource)
    normalized["needs_review"] = bool(review_reasons)
    normalized["review_reason"] = "；".join(review_reasons)
    return normalized


def _normalize_history_suggestion_category(
    category: str,
    suggestions: Any,
    sample_map: Dict[str, Dict[str, Any]],
    datasource: Dict[str, Any],
    existing_indexes: Dict[str, Set[str]],
    skip_counts: Dict[str, Dict[str, int]],
) -> tuple[List[Dict[str, Any]], int]:
    """归一化单个类别的建议：剔除非法、已有维护项与重复项；返回 (items, 原始条数)。"""
    seen: Set[str] = set()
    raw_items = suggestions.get(category) if isinstance(suggestions, dict) else []
    limited_items = (raw_items if isinstance(raw_items, list) else [])[:30]
    items: List[Dict[str, Any]] = []
    for raw_item in limited_items:
        item = _normalize_history_suggestion_item(raw_item, category, sample_map, datasource)
        if not item:
            skip_counts[category]["invalid"] += 1
            continue
        if category in {"metrics", "dimensions"} and _is_existing_history_suggestion(item, category, existing_indexes):
            skip_counts[category]["existing"] += 1
            continue
        key = str(item.get("name") or item.get("title") or "") + "::" + str(item.get("table") or item.get("content") or "")
        if key in seen:
            skip_counts[category]["duplicate"] += 1
            continue
        seen.add(key)
        items.append(item)
    return items, len(limited_items)


def _normalize_history_learning_result(
    raw_result: Any,
    samples: List[Dict[str, Any]],
    datasource: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """校验 LLM 历史学习 JSON 输出。"""
    parsed = raw_result if isinstance(raw_result, dict) else {}
    suggestions = parsed.get("suggestions") if isinstance(parsed.get("suggestions"), dict) else parsed
    sample_map = _sample_map_by_execution_id(samples)
    existing_indexes = _build_existing_semantic_indexes(config)
    result: Dict[str, List[Dict[str, Any]]] = {"metrics": [], "dimensions": [], "issues": []}
    raw_counts: Dict[str, int] = {}
    skip_counts = _empty_history_learning_skip_counts()
    for category in result.keys():
        result[category], raw_counts[category] = _normalize_history_suggestion_category(
            category, suggestions, sample_map, datasource, existing_indexes, skip_counts,
        )
    logger.info(
        "history_learning normalize: raw_counts=%s normalized_counts=%s skip_counts=%s",
        raw_counts,
        {category: len(items) for category, items in result.items()},
        skip_counts,
    )
    return result


async def _generate_history_learning_suggestions_by_llm(
    datasource: Dict[str, Any],
    config: Dict[str, Any],
    samples: List[Dict[str, Any]],
    *,
    llm_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """调用大模型基于历史生成指标集维护建议。"""
    if not samples:
        return dict(HISTORY_LEARNING_EMPTY_RESULT)
    compact_samples = [_compact_history_sample_for_llm(item) for item in samples]
    prompt = build_history_learning_prompt(
        semantic_config_payload=config,
        table_context=_build_history_learning_table_context(datasource, config),
        compact_samples=compact_samples,
    )
    factory = llm_factory or get_chat_data_llm
    try:
        response = await factory(streaming=False, max_tokens=4000).ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={
                "purpose": "semantic_history_learning",
                "datasource_id": datasource.get("id") or "",
                "semantic_config_id": config.get("id") or config.get("semantic_config_id") or "",
            },
        )
        raise
    content = getattr(response, "content", response)
    parsed = parse_llm_json(str(content), strict=False)
    if not isinstance(parsed, dict):
        raise ValueError("大模型未返回合法 JSON")
    return _normalize_history_learning_result(parsed, samples, datasource, config)


class SemanticHistoryLearningService:
    """指标集历史学习编排服务。"""

    def __init__(
        self,
        dao: Optional[SemanticHistoryLearningDao] = None,
        *,
        context_loader: Optional[Callable[[str, str], Awaitable[Optional[Dict[str, Any]]]]] = None,
        suggestion_loader: Optional[Callable[[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]], Awaitable[Dict[str, List[Dict[str, Any]]]]]] = None,
        history_matcher: Optional[Callable[[str, Dict[str, Any], str, str, Dict[str, Any], str], Tuple[bool, str, List[str], str]]] = None,
    ):
        self.dao = dao or SemanticHistoryLearningDao()
        self.context_loader = context_loader or _load_history_conversation_context
        self.suggestion_loader = suggestion_loader or _generate_history_learning_suggestions_by_llm
        self.history_matcher = history_matcher or _history_belongs_to_config

    @staticmethod
    def normalize_sources(sources: List[str]) -> Set[str]:
        source_set = {str(item or "").strip().lower() for item in (sources or []) if str(item or "").strip()}
        if not source_set:
            raise ValueError("请至少选择一个学习来源")
        unsupported_sources = sorted(source_set - ALLOWED_HISTORY_LEARNING_SOURCES)
        if unsupported_sources:
            raise ValueError(f"不支持的学习来源: {', '.join(unsupported_sources)}")
        return source_set

    @staticmethod
    def build_status_values(include_failed: bool) -> List[str]:
        status_values = ["succeeded", "partial_success"]
        if include_failed:
            status_values.append("failed")
        return status_values

    @staticmethod
    def _source_stats() -> Dict[str, Dict[str, int]]:
        return {source: {"scanned": 0, "matched": 0, "skipped": 0} for source in sorted(ALLOWED_HISTORY_LEARNING_SOURCES)}

    @staticmethod
    def _build_sample(execution: Any, task: Any, task_payload: Dict[str, Any], query_type: str, history_tables: List[str]) -> Dict[str, Any]:
        executed_time = execution.finished_time or execution.started_time or execution.create_time
        sample = {
            "execution_id": execution.execution_id,
            "task_id": execution.task_id,
            "source": query_type,
            "status": execution.sql_status,
            "conversation_id": _extract_history_task_conversation_id(task_payload),
            "query_text": _extract_history_task_query_text(task_payload),
            "sql": execution.sql_text,
            "tables": history_tables,
            "error_message": execution.error_message or (task.error_message if task else "") or "",
            "executed_at": executed_time.isoformat() if executed_time else None,
        }
        if query_type == "builder" and isinstance(task_payload.get("builder_config"), dict):
            sample["builder_config"] = task_payload.get("builder_config")
        if query_type == "builder" and isinstance(task_payload.get("semantic_builder_config"), dict):
            sample["semantic_builder_config"] = task_payload.get("semantic_builder_config")
        return sample

    async def _attach_agent_context(
        self,
        sample: Dict[str, Any],
        user_id: str,
        cache: Dict[str, Optional[Dict[str, Any]]],
    ) -> bool:
        conversation_id = str(sample.get("conversation_id") or "").strip()
        if not conversation_id:
            return False
        if conversation_id not in cache:
            cache[conversation_id] = await self.context_loader(conversation_id, user_id)
        conversation_context = cache.get(conversation_id)
        if not conversation_context:
            return False
        sample["conversation_context"] = conversation_context
        return True

    async def _collect_samples(
        self,
        *,
        rows: List[Tuple[Any, Any]],
        datasource_id: str,
        source_set: Set[str],
        datasource: Dict[str, Any],
        config: Dict[str, Any],
        config_id: str,
        user_id: str,
        limit: int,
        skipped: Dict[str, int],
        source_stats: Dict[str, Dict[str, int]],
    ) -> List[Dict[str, Any]]:
        samples: List[Dict[str, Any]] = []
        context_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        for execution, task in rows:
            task_payload = _parse_json_object(task.request_payload if task else None)
            query_type = str((task.query_type if task else "") or "").strip().lower()
            if not self._should_consider_source(query_type, source_set, skipped, source_stats):
                continue
            matched, skip_reason, history_tables, skip_detail = self.history_matcher(
                query_type,
                task_payload,
                execution.sql_text,
                config_id,
                config,
                str(datasource.get("source_type") or ""),
            )
            if not matched:
                self._record_config_skip(query_type, skip_reason, skipped, source_stats, datasource_id, config_id, execution, skip_detail)
                continue
            sample = self._build_sample(execution, task, task_payload, query_type, history_tables)
            if query_type == "agent" and not await self._attach_agent_context(sample, user_id, context_cache):
                skipped["missing_conversation_context"] += 1
                source_stats[query_type]["skipped"] += 1
                continue
            samples.append(sample)
            source_stats[query_type]["matched"] += 1
            if len(samples) >= limit:
                break
        return samples

    @staticmethod
    def _should_consider_source(
        query_type: str,
        source_set: Set[str],
        skipped: Dict[str, int],
        source_stats: Dict[str, Dict[str, int]],
    ) -> bool:
        if query_type not in source_stats:
            skipped["source"] += 1
            return False
        source_stats[query_type]["scanned"] += 1
        if query_type not in source_set:
            skipped["source"] += 1
            source_stats[query_type]["skipped"] += 1
            return False
        return True

    @staticmethod
    def _record_config_skip(
        query_type: str,
        skip_reason: str,
        skipped: Dict[str, int],
        source_stats: Dict[str, Dict[str, int]],
        datasource_id: str,
        config_id: str,
        execution: Any,
        skip_detail: str,
    ) -> None:
        skipped["semantic_config"] += 1
        if skip_reason in skipped:
            skipped[skip_reason] += 1
        source_stats[query_type]["skipped"] += 1
        if skip_reason == "sql_parse_failed" and skipped[skip_reason] <= 5:
            logger.warning(
                "history_learning sql parse failed: datasource_id=%s config_id=%s query_type=%s error=%s sql=%s",
                datasource_id,
                config_id,
                query_type,
                skip_detail,
                str(execution.sql_text or "")[:300],
            )

    async def learn(
        self,
        *,
        datasource_id: str,
        config_id: str,
        user_id: str,
        datasource: Dict[str, Any],
        config: Dict[str, Any],
        days: int,
        limit: int,
        sources: List[str],
        include_failed: bool,
    ) -> Dict[str, Any]:
        source_set = self.normalize_sources(sources)
        scan_limit = min(max(limit * 5, limit), 1000)
        rows = await self.dao.list_learning_history(
            datasource_id=datasource_id,
            user_id=user_id,
            cutoff_time=datetime.now() - timedelta(days=days),
            status_values=self.build_status_values(include_failed),
            scan_limit=scan_limit,
        )
        skipped = dict(SKIP_COUNTER_TEMPLATE)
        source_stats = self._source_stats()
        samples = await self._collect_samples(
            rows=rows,
            datasource_id=datasource_id,
            source_set=source_set,
            datasource=datasource,
            config=config,
            config_id=config_id,
            user_id=user_id,
            limit=limit,
            skipped=skipped,
            source_stats=source_stats,
        )
        suggestions = await self.suggestion_loader(datasource, config, samples)
        logger.info(
            "history_learning stats: datasource_id=%s config_id=%s user_id=%s scanned=%s matched=%s source_stats=%s skipped=%s scope_tables=%s",
            datasource_id,
            config_id,
            user_id,
            len(rows),
            len(samples),
            source_stats,
            skipped,
            sorted(_semantic_scope_tables(config)),
        )
        return {
            "datasource_id": datasource_id,
            "semantic_config_id": config_id,
            "days": days,
            "limit": limit,
            "scan_limit": scan_limit,
            "scanned_count": len(rows),
            "matched_count": len(samples),
            "skipped": skipped,
            "source_stats": source_stats,
            "suggestions": suggestions,
            "samples": samples[:limit],
        }


def build_semantic_history_learning_service(
    *,
    context_loader: Optional[Callable[[str, str], Awaitable[Optional[Dict[str, Any]]]]] = None,
    suggestion_loader: Optional[Callable[[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]], Awaitable[Dict[str, List[Dict[str, Any]]]]]] = None,
    history_matcher: Optional[Callable[[str, Dict[str, Any], str, str, Dict[str, Any], str], Tuple[bool, str, List[str], str]]] = None,
    session_context: Optional[Callable[[], Any]] = None,
) -> SemanticHistoryLearningService:
    """构建指标集历史学习服务。"""
    return SemanticHistoryLearningService(
        dao=SemanticHistoryLearningDao(session_context=session_context or get_session),
        context_loader=context_loader,
        suggestion_loader=suggestion_loader,
        history_matcher=history_matcher,
    )
