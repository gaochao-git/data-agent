"""Semantic config routes for ChatData."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, File, Request, UploadFile
from langchain_core.messages import HumanMessage
from sqlalchemy import select

from auth import AuthenticationError
from utils.lang_util import conversation
from utils.lang_util.checkpoint import get_checkpointer
from utils.lang_util.database import get_session
from utils.lang_util.models import ChatDataQueryExecution, ChatDataQueryTask
from utils.logger import get_logger
from ..agent import get_chat_data_graph
from ..agent.data_analyze_graph import get_data_analyze_graph
from ..agent.prompts.dim_metric_auto_prefill import (
    build_auto_prefill_knowledge_prompt,
    build_metric_recommendation_prompt,
)
from ..agent.prompts.history_learning import build_history_learning_prompt
from ..schema import (
    SemanticAutoPrefillRequest,
    SemanticConfigCreateRequest,
    SemanticConfigUpdateRequest,
    SemanticHistoryLearningRequest,
    SemanticPrefillRequest,
)
from ..semantic.utils import parse_llm_json
from ..services.datasource_manager import get_datasource_manager
from ..services.file_text_extractor import extract_text_from_file_bytes
from ..services.llm_config import get_chat_data_llm
from ..services.sql_parser import extract_physical_tables
from .common import (
    AGENT_ID,
    DATA_ANALYZE_AGENT_ID,
    _build_table_columns_map,
    _get_current_user,
    _semantic_config_prompt_payload,
    business_response,
)

logger = get_logger(__name__)
router = APIRouter()

SEMANTIC_PREFILL_CONTEXT_FILE_MAX_SIZE = 10 * 1024 * 1024
SEMANTIC_PREFILL_CONTEXT_TEXT_MAX_LENGTH = 20000
PREFILL_MAX_RECOMMENDED_METRICS = 10
PREFILL_ALLOWED_METRIC_AGGS = {"SUM", "AVG", "MAX", "MIN", "COUNT"}
PREFILL_DISPLAY_NAME_MAX_LENGTH = 15


def _can_manage_semantic_config(config: Dict[str, Any], user_id: str) -> bool:
    """判断当前用户是否可管理指标集。"""
    return bool(config and str(user_id or "").strip())


def _with_semantic_config_permissions(config: Dict[str, Any], user_id: str, username: str = "") -> Dict[str, Any]:
    """补充指标集权限字段。"""
    item = dict(config or {})
    item["scope_type"] = item.get("scope_type") or "public"
    item["can_manage"] = _can_manage_semantic_config(item, user_id)
    return item


async def _get_semantic_configs_for_user(ds_manager: Any, datasource_id: str, user_id: str, username: str = "") -> List[Dict[str, Any]]:
    """读取当前用户可见的指标集。"""
    configs = await ds_manager.get_semantic_configs_async(datasource_id)
    return [_with_semantic_config_permissions(config, user_id, username) for config in (configs or [])]


def _expression_uses_physical_column(expression: str, table_columns: Set[str]) -> bool:
    """判断表达式是否引用了给定表中的任一物理字段。"""
    if not expression:
        return False
    expr = expression.replace("`", "")
    for column in table_columns:
        escaped_col = re.escape(column)
        pattern = rf"(^|[^A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped_col}([^A-Za-z0-9_]|$)"
        if re.search(pattern, expr, flags=re.IGNORECASE):
            return True
    return False


def _validate_semantic_fields(datasource: Dict, metrics: List[Dict], dimensions: List[Dict]) -> Optional[str]:
    """校验语义字段必须基于数据源物理表字段。"""
    table_columns_map = _build_table_columns_map(datasource)
    if not table_columns_map:
        return "当前数据源缺少表结构信息，请先完成数据源同步"

    seen_names: Set[str] = set()
    for item in [*(dimensions or []), *(metrics or [])]:
        display_name = str((item or {}).get("name") or "").strip()
        normalized_name = _normalize_semantic_text(display_name)
        if not normalized_name:
            return "字段显示名称不能为空"
        if normalized_name in seen_names:
            return f"字段显示名称重复: {display_name}"
        seen_names.add(normalized_name)

    for dim in dimensions:
        table_name = dim.get("table")
        column_name = dim.get("column")
        if not table_name or table_name not in table_columns_map:
            return f"维度 {dim.get('name', '')} 所属表不存在: {table_name}"
        if not column_name or column_name not in table_columns_map[table_name]:
            return f"维度 {dim.get('name', '')} 字段不存在: {table_name}.{column_name}"

    for metric in metrics:
        table_name = metric.get("table")
        expression = (metric.get("expression") or "").strip()
        if not table_name or table_name not in table_columns_map:
            return f"指标 {metric.get('name', '')} 所属表不存在: {table_name}"
        if not _expression_uses_physical_column(expression, table_columns_map[table_name]):
            return f"指标 {metric.get('name', '')} 表达式必须引用表 {table_name} 的物理字段"

    return None


def _quote_identifier(name: str) -> str:
    """转义 SQL 标识符。"""
    safe = (name or "").replace("`", "``")
    return f"`{safe}`"


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
        "不对",
        "不是",
        "错了",
        "错误",
        "不准确",
        "不应该",
        "没有",
        "漏了",
        "少了",
        "查不到",
        "不符合",
        "不合理",
        "wrong",
        "incorrect",
    ]
    return any(pattern in normalized for pattern in patterns)


def _is_history_tool_error_message(text: str) -> bool:
    """识别工具消息是否真的是失败结果。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    payload_text = raw
    if "\n" in raw and raw.startswith("["):
        payload_text = raw.split("\n", 1)[1].strip()
    try:
        payload = json.loads(payload_text)
        if isinstance(payload, dict):
            if payload.get("success") is False:
                return True
            error_value = payload.get("error")
            if error_value not in (None, "", False):
                return True
            return False
    except Exception:
        pass
    lowered = raw.lower()
    return "失败" in raw or "错误" in raw or "exception" in lowered or "traceback" in lowered


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
            clipped = text[:500]
            user_turns.append({"content": clipped, "timestamp": timestamp})
            if _is_negative_feedback_text(text):
                negative_feedback.append(clipped)
        elif role == "assistant":
            if text and not (msg or {}).get("tool_calls"):
                assistant_turns.append({"content": text[:500], "timestamp": timestamp})
        elif role == "tool":
            if _is_history_tool_error_message(text):
                tool_errors.append({
                    "tool_name": str((msg or {}).get("tool_name") or "").strip(),
                    "content": text[:600],
                    "timestamp": timestamp,
                })
    return {
        "user_turns": user_turns[-6:],
        "assistant_turns": assistant_turns[-3:],
        "negative_feedback": negative_feedback[-5:],
        "tool_errors": tool_errors[-5:],
    }


async def _load_history_conversation_context(conversation_id: str) -> Optional[Dict[str, Any]]:
    """读取智能问数对话上下文，用于历史学习。"""
    normalized_id = str(conversation_id or "").strip()
    if not normalized_id:
        return None
    try:
        agent_id = await conversation.get_agent_id("", normalized_id)
        if agent_id == DATA_ANALYZE_AGENT_ID:
            graph_getter = get_data_analyze_graph
        elif agent_id == AGENT_ID:
            graph_getter = get_chat_data_graph
        else:
            logger.warning("历史学习跳过未知 Agent 会话: conversation_id=%s agent_id=%s", normalized_id, agent_id)
            return None
        detail = await conversation.get_detail("", normalized_id, graph_getter, get_checkpointer)
        messages = (detail or {}).get("messages") if isinstance(detail, dict) else []
        if not isinstance(messages, list):
            return None
        context = _compact_history_conversation_messages(messages)
        return context if context.get("user_turns") else None
    except Exception as err:
        logger.warning("读取历史学习对话上下文失败: conversation_id=%s error=%s", normalized_id, err, exc_info=True)
        return None


def _semantic_scope_tables(config: Dict[str, Any]) -> Set[str]:
    """提取指标集涉及的表。"""
    tables: Set[str] = set()
    for item in config.get("metrics") or []:
        table = str((item or {}).get("table") or "").strip()
        if table:
            tables.add(table)
    for item in config.get("dimensions") or []:
        table = str((item or {}).get("table") or "").strip()
        if table:
            tables.add(table)
    for item in config.get("relationships") or []:
        left = str((item or {}).get("left_table") or "").strip()
        right = str((item or {}).get("right_table") or "").strip()
        if left:
            tables.add(left)
        if right:
            tables.add(right)
    return tables


def _history_belongs_to_config(
    query_type: str,
    task_payload: Dict[str, Any],
    sql: str,
    config_id: str,
    config: Dict[str, Any],
    source_type: str,
) -> tuple[bool, str, List[str], str]:
    """判断历史记录是否属于当前指标集。"""
    task_config_id = _extract_history_task_semantic_id(task_payload)
    if query_type in {"agent", "builder"}:
        if not task_config_id or task_config_id != config_id:
            return False, "config_id_mismatch", [], ""
        return True, "matched_config_id", [], ""

    scope_tables = _semantic_scope_tables(config)
    if not scope_tables:
        return False, "empty_scope", [], ""
    parsed = extract_physical_tables(sql, source_type=source_type)
    if not parsed.success:
        return False, "sql_parse_failed", [], parsed.error
    history_tables = parsed.tables
    if not history_tables:
        return False, "empty_history_tables", [], ""
    if not history_tables.issubset(scope_tables):
        return False, "table_scope_mismatch", sorted(history_tables), ""
    return True, "matched_table_scope", sorted(history_tables), ""


def _build_history_learning_table_context(datasource: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """裁剪指标集相关表结构给历史学习模型。"""
    scope_tables = _semantic_scope_tables(config)
    items: List[Dict[str, Any]] = []
    for table in datasource.get("ds_tables", []) or []:
        table_name = str((table or {}).get("table_name") or "").strip()
        if scope_tables and table_name not in scope_tables:
            continue
        columns = []
        for col in (table or {}).get("columns", []) or []:
            col_name = str((col or {}).get("name") or "").strip()
            if not col_name:
                continue
            columns.append({
                "name": col_name,
                "type": (col or {}).get("mysql_type") or (col or {}).get("hive_type") or (col or {}).get("type") or "",
                "description": (col or {}).get("description") or "",
            })
        items.append({
            "table_name": table_name,
            "description": (table or {}).get("description") or "",
            "columns": columns[:80],
        })
    return items[:20]


def _compact_history_sample_for_llm(sample: Dict[str, Any]) -> Dict[str, Any]:
    """压缩单条历史，控制 LLM 输入长度。"""
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
    if builder_config:
        compact["builder_config"] = {
            "mainTable": builder_config.get("mainTable"),
            "joins": (builder_config.get("joins") or [])[:10],
            "dimensions": (builder_config.get("dimensions") or [])[:20],
            "aggregations": (builder_config.get("aggregations") or [])[:20],
            "filters": (builder_config.get("filters") or [])[:20],
            "orderBy": (builder_config.get("orderBy") or [])[:10],
        }
    return compact


def _sample_map_by_execution_id(samples: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按执行ID索引历史样例。"""
    return {str(item.get("execution_id") or ""): item for item in samples if str(item.get("execution_id") or "")}


def _normalize_semantic_text(value: Any) -> str:
    """统一语义字段比较口径。"""
    return str(value or "").strip().strip("`").lower()


def _normalize_prefill_display_name(value: Any, fallback: str) -> str:
    """归一化自动预填返回的短显示名。"""
    source = str(value or fallback or "").strip()
    if not source:
        return ""
    first_part = re.split(r"[，,。；;：:\n\r]", source, maxsplit=1)[0].strip() or source
    without_relation = re.sub(r"(?:同样)?关联\s+[A-Za-z_][A-Za-z0-9_.]*.*$", "", first_part, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[`\"'“”‘’、/\\|()（）\[\]【】{}<>《》.!！?？~@#$%^&*+=-]", "", without_relation or first_part)
    cleaned = re.sub(r"\s+", "", cleaned).strip()
    display_name = cleaned or str(fallback or "").strip()
    return display_name[:PREFILL_DISPLAY_NAME_MAX_LENGTH]


def _build_prefill_alias_map(tables: List[Dict]) -> Dict[str, List[str]]:
    """构造物理表到表角色别名列表的映射。"""
    alias_map: Dict[str, List[str]] = {}
    for table in tables or []:
        table_name = str((table or {}).get("table_name") or (table or {}).get("table") or "").strip()
        table_alias = str((table or {}).get("table_alias") or table_name).strip()
        if not table_name or not table_alias:
            continue
        aliases = alias_map.setdefault(table_name, [])
        if table_alias not in aliases:
            aliases.append(table_alias)
    return alias_map


def _dedupe_prefill_display_names(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """自动预填字段重名时追加表角色前缀。"""
    def build_prefixed_name(table_alias: str, name: str) -> str:
        alias = re.sub(r"[`\"'“”‘’、/\\|()（）\[\]【】{}<>《》.!！?？~@#$%^&*+=-]", "", str(table_alias or "").strip())
        alias = re.sub(r"\s+", "", alias)
        if not alias:
            return name
        candidate = _normalize_prefill_display_name(f"{alias}_{name}", name)
        if len(f"{alias}_{name}") <= PREFILL_DISPLAY_NAME_MAX_LENGTH:
            return candidate
        budget = PREFILL_DISPLAY_NAME_MAX_LENGTH - len(name) - 1
        if budget <= 0:
            return name[:PREFILL_DISPLAY_NAME_MAX_LENGTH]
        alias_parts = [part for part in alias.split("_") if part]
        alias_part = alias
        if alias_parts and len(alias_parts[-1]) >= 2:
            alias_part = alias_parts[-1]
        alias_part = alias_part[:budget]
        return _normalize_prefill_display_name(f"{alias_part}_{name}", name)

    name_counts: Dict[str, int] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
    used_names: Set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        table_alias = str(item.get("table_alias") or item.get("table") or "").strip()
        if name_counts.get(name, 0) > 1 and table_alias:
            name = build_prefixed_name(table_alias, name)
        candidate = name
        index = 2
        while candidate in used_names:
            suffix = str(index)
            candidate = f"{name[:max(PREFILL_DISPLAY_NAME_MAX_LENGTH - len(suffix), 1)]}{suffix}"
            index += 1
        item["name"] = candidate
        used_names.add(candidate)
    return items


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
        if not token or token.lower() in keywords:
            continue
        columns.add(token)
    return columns


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
        if not isinstance(item, dict):
            continue
        table = _normalize_semantic_text(item.get("table"))
        name = _normalize_semantic_text(item.get("name"))
        expression = _normalize_semantic_expression(item.get("expression"))
        if name:
            indexes["metric_names"].add(name)
        if table and expression:
            indexes["metric_expressions"].add(f"{table}.{expression}")
    for item in config.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
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
    return indexes


def _is_existing_history_suggestion(item: Dict[str, Any], category: str, indexes: Dict[str, Set[str]]) -> bool:
    """判断历史学习建议是否已在当前指标集维护。"""
    if category == "metrics":
        table = _normalize_semantic_text(item.get("table"))
        name = _normalize_semantic_text(item.get("name"))
        expression = _normalize_semantic_expression(item.get("expression"))
        if name and name in indexes["metric_names"]:
            return True
        if table and expression and f"{table}.{expression}" in indexes["metric_expressions"]:
            return True
        return False
    if category == "dimensions":
        table = _normalize_semantic_text(item.get("table"))
        name = _normalize_semantic_text(item.get("name"))
        field_name = _normalize_semantic_text(item.get("field_name"))
        column = _normalize_semantic_text(item.get("column") or item.get("field_name"))
        if name and name in indexes["dimension_names"]:
            return True
        if table and column and f"{table}.{column}" in indexes["dimension_columns"]:
            return True
        if table and field_name and f"{table}.{field_name}" in indexes["dimension_fields"]:
            return True
        return False
    return False


def _normalize_history_suggestion_item(
    item: Any,
    category: str,
    sample_map: Dict[str, Dict[str, Any]],
    datasource: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """校验并标准化 LLM 输出的历史学习建议。"""
    if not isinstance(item, dict):
        return None
    source = str(item.get("source") or "").strip().lower()
    if source not in {"agent", "builder", "sql"}:
        return None
    confidence = str(item.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        return None
    sample_ids = item.get("sample_execution_ids")
    if not isinstance(sample_ids, list):
        return None
    samples = [sample_map[sid] for sid in [str(s or "") for s in sample_ids] if sid in sample_map][:3]
    if not samples:
        return None
    support_count = item.get("support_count")
    try:
        support_count_int = max(1, int(support_count))
    except Exception:
        return None

    normalized = {
        "reason": str(item.get("reason") or "").strip(),
        "source": source,
        "confidence": confidence,
        "support_count": support_count_int,
        "samples": samples,
    }
    if category in {"metrics", "dimensions"}:
        normalized.update({
            "name": str(item.get("name") or "").strip(),
            "table": str(item.get("table") or "").strip(),
            "field_name": str(item.get("field_name") or "").strip(),
            "expression": str(item.get("expression") or "").strip(),
        })
        if category == "dimensions":
            dim_type = str(item.get("type") or "").strip().lower()
            if dim_type not in {"categorical", "time", "number"}:
                return None
            normalized["type"] = dim_type
            normalized["column"] = str(item.get("column") or item.get("field_name") or "").strip()
        if not normalized["name"] or not normalized["reason"]:
            return None
    else:
        normalized.update({
            "title": str(item.get("title") or "").strip(),
            "content": str(item.get("content") or "").strip(),
        })
        if not normalized["title"] or not normalized["content"] or not normalized["reason"]:
            return None

    table_columns = _build_table_columns_map(datasource)
    table_name = str(normalized.get("table") or "").strip()
    review_reasons: List[str] = []
    if table_name and table_name not in table_columns:
        review_reasons.append(f"表不在当前数据源缓存中：{table_name}")
    if category == "dimensions":
        column_name = str(normalized.get("column") or normalized.get("field_name") or "").strip()
        if table_name and column_name and table_name in table_columns and column_name not in table_columns[table_name]:
            review_reasons.append(f"字段不在当前表结构中：{table_name}.{column_name}")
    if category == "metrics":
        expression_columns = _extract_expression_columns(normalized.get("expression"))
        missing_columns = sorted(col for col in expression_columns if table_name in table_columns and col not in table_columns[table_name])
        if table_name and missing_columns:
            review_reasons.append(f"表达式引用字段不在当前表结构中：{table_name}.{', '.join(missing_columns)}")
    normalized["needs_review"] = bool(review_reasons)
    normalized["review_reason"] = "；".join(review_reasons)
    return normalized


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
    for category in result.keys():
        raw_items = suggestions.get(category) if isinstance(suggestions, dict) else []
        if not isinstance(raw_items, list):
            raw_items = []
        seen: Set[str] = set()
        for raw_item in raw_items[:30]:
            item = _normalize_history_suggestion_item(raw_item, category, sample_map, datasource)
            if not item:
                continue
            if category in {"metrics", "dimensions"} and _is_existing_history_suggestion(item, category, existing_indexes):
                continue
            key = str(item.get("name") or item.get("title") or "") + "::" + str(item.get("table") or item.get("content") or "")
            if key in seen:
                continue
            seen.add(key)
            result[category].append(item)
    return result


async def _generate_history_learning_suggestions_by_llm(
    datasource: Dict[str, Any],
    config: Dict[str, Any],
    samples: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """调用大模型基于历史生成指标集维护建议。"""
    if not samples:
        return {"metrics": [], "dimensions": [], "issues": []}
    compact_samples = [_compact_history_sample_for_llm(item) for item in samples]
    prompt = build_history_learning_prompt(
        semantic_config_payload=_semantic_config_prompt_payload(config),
        table_context=_build_history_learning_table_context(datasource, config),
        compact_samples=compact_samples,
    )
    llm = get_chat_data_llm(streaming=False, max_tokens=4000)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", response)
    parsed = parse_llm_json(str(content), strict=False)
    if not isinstance(parsed, dict):
        raise ValueError("大模型未返回合法 JSON")
    return _normalize_history_learning_result(parsed, samples, datasource, config)


def _normalize_prefill_item(item: Dict) -> Optional[Dict]:
    """标准化 LLM 预填项。"""
    table_name = (item.get("table") or "").strip()
    column_name = (item.get("column") or "").strip()
    if not table_name or not column_name:
        return None
    role = str(item.get("role") or "").strip().lower()
    if role not in ("metric", "dimension"):
        if item.get("is_metric") is True:
            role = "metric"
        elif item.get("is_dimension") is True:
            role = "dimension"
        else:
            return None

    return {
        "table": table_name,
        "column": column_name,
        "role": role,
        "expression": _quote_identifier(column_name),
    }


def _normalize_prefill_table_model_type(table_model_type: Any) -> str:
    """标准化自动预填的表建模类型。"""
    normalized = str(table_model_type or "detail").strip().lower()
    return normalized if normalized in {"detail", "aggregated"} else "detail"


def _filter_prefill_knowledge(raw_knowledge: Any, generate_business_flow: bool = False) -> Dict[str, str]:
    """只保留本次请求允许生成的业务知识。"""
    if not isinstance(raw_knowledge, dict):
        return {}
    knowledge: Dict[str, str] = {}
    if generate_business_flow:
        business_flow = str(raw_knowledge.get("business_flow") or "").strip()
        if business_flow:
            knowledge["business_flow"] = business_flow
    return knowledge


def _normalize_prefill_knowledge(parsed: Dict[str, Any], generate_business_flow: bool = False) -> Dict[str, str]:
    """标准化自动预填生成的业务知识。"""
    raw_knowledge = parsed.get("knowledge") if isinstance(parsed, dict) else None
    return _filter_prefill_knowledge(raw_knowledge, generate_business_flow)


def _parse_prefill_response(content: str, generate_business_flow: bool = False) -> Dict[str, Any]:
    """解析自动预填模型返回。"""
    parsed = parse_llm_json(str(content))
    raw_fields = parsed.get("fields", []) if isinstance(parsed, dict) else []
    normalized_map = {}
    if isinstance(raw_fields, list):
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_prefill_item(item)
            if not normalized:
                continue
            key = f"{normalized['table']}.{normalized['column']}"
            normalized_map[key] = normalized
    return {
        "fields": normalized_map,
        "knowledge": _normalize_prefill_knowledge(parsed, generate_business_flow),
    }


def _parse_prefill_fields(content: str) -> Dict[str, Dict]:
    """解析自动预填模型返回字段。"""
    return _parse_prefill_response(content).get("fields", {})


def _build_prefill_table_payload(tables: List[Dict]) -> List[Dict[str, Any]]:
    """构造自动预填使用的紧凑表结构。"""
    table_payload = []
    seen_tables: Set[str] = set()
    for table in tables or []:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name or table_name in seen_tables:
            continue
        seen_tables.add(table_name)
        columns = []
        for col in table.get("columns", []) or []:
            col_name = str(col.get("name") or "").strip()
            if not col_name:
                continue
            columns.append({
                "name": col_name,
                "type": col.get("mysql_type") or col.get("hive_type") or col.get("type") or "",
                "description": col.get("description") or "",
            })
        table_payload.append({
            "table": table_name,
            "description": table.get("description") or "",
            "columns": columns,
        })
    return table_payload


def _parse_prefill_dimension_name_map(parsed: Dict[str, Any], table_payload: List[Dict[str, Any]]) -> Dict[str, str]:
    """解析模型生成的物理字段显示名称。"""
    raw_fields = parsed.get("fields", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_fields, list):
        return {}
    table_columns = {
        table.get("table", ""): {col.get("name", "") for col in table.get("columns", []) if col.get("name")}
        for table in table_payload
        if table.get("table")
    }
    name_map: Dict[str, str] = {}
    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        table_name = str(item.get("table") or "").strip()
        column_name = str(item.get("column") or "").strip()
        if table_name not in table_columns or column_name not in table_columns[table_name]:
            continue
        name_map[f"{table_name}.{column_name}"] = _normalize_prefill_display_name(item.get("name"), column_name)
    return name_map


def _build_prefill_dimension_fields(
    table_payload: List[Dict[str, Any]],
    table_alias_map: Optional[Dict[str, List[str]]] = None,
    dimension_name_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    """将物理字段全部作为维度预填。"""
    fields: List[Dict[str, str]] = []
    alias_map = table_alias_map or {table.get("table", ""): [table.get("table", "")] for table in table_payload if table.get("table")}
    name_map = dimension_name_map or {}
    for table in table_payload:
        table_name = table.get("table", "")
        if not table_name:
            continue
        aliases = alias_map.get(table_name) or [table_name]
        for col in table.get("columns", []):
            column_name = col.get("name", "")
            if not column_name:
                continue
            display_name = name_map.get(f"{table_name}.{column_name}") or _normalize_prefill_display_name(column_name, column_name)
            for table_alias in aliases:
                fields.append({
                    "table": table_name,
                    "table_alias": table_alias,
                    "column": column_name,
                    "role": "dimension",
                    "expression": _quote_identifier(column_name),
                    "name": display_name,
                })
    return fields


def _parse_metric_items(
    parsed: Dict[str, Any],
    table_payload: List[Dict[str, Any]],
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, str]]:
    """解析并校验模型推荐的原子指标列表。"""
    raw_metrics = parsed.get("metrics", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_metrics, list):
        return []
    table_columns = {
        table.get("table", ""): {col.get("name", "") for col in table.get("columns", []) if col.get("name")}
        for table in table_payload
        if table.get("table")
    }
    alias_map = table_alias_map or {table.get("table", ""): [table.get("table", "")] for table in table_payload if table.get("table")}
    normalized: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in raw_metrics:
        if not isinstance(item, dict):
            continue
        table_name = str(item.get("table") or "").strip()
        column_name = str(item.get("column") or item.get("source_column") or "").strip()
        agg = str(item.get("agg") or "").strip().upper()
        if table_name not in table_columns or column_name not in table_columns[table_name] or agg not in PREFILL_ALLOWED_METRIC_AGGS:
            continue
        key = f"{table_name}.{column_name}.{agg}"
        if key in seen:
            continue
        seen.add(key)
        expression = f"{agg}({_quote_identifier(column_name)})"
        aliases = alias_map.get(table_name) or [table_name]
        for table_alias in aliases:
            normalized.append({
                "table": table_name,
                "table_alias": table_alias,
                "column": column_name,
                "source_column": column_name,
                "role": "metric",
                "field_name": str(item.get("field_name") or f"{column_name}_{agg.lower()}").strip() or f"{column_name}_{agg.lower()}",
                "name": _normalize_prefill_display_name(item.get("name"), column_name),
                "agg": agg,
                "expression": expression,
            })
            if len(normalized) >= max_metrics:
                break
        if len(normalized) >= max_metrics:
            break
    return _dedupe_prefill_display_names(normalized)


def _parse_metric_recommendation_response(
    content: str,
    table_payload: List[Dict[str, Any]],
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, str]]:
    """解析并校验模型推荐的原子指标。"""
    parsed = parse_llm_json(str(content))
    return _parse_metric_items(parsed, table_payload, max_metrics, table_alias_map)


def _parse_prefill_recommendation_response(
    content: str,
    table_payload: List[Dict[str, Any]],
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """解析模型返回的字段显示名称和推荐指标。"""
    parsed = parse_llm_json(str(content))
    dimension_name_map = _parse_prefill_dimension_name_map(parsed, table_payload)
    fields = _build_prefill_dimension_fields(table_payload, table_alias_map, dimension_name_map)
    metric_fields = _parse_metric_items(parsed, table_payload, max_metrics, table_alias_map)
    return {"fields": fields, "metrics": metric_fields}


async def _call_metric_recommendation_llm(
    table_payload: List[Dict[str, Any]],
    business_context: str = "",
    max_metrics: int = PREFILL_MAX_RECOMMENDED_METRICS,
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """调用大模型推荐少量原子指标。"""
    prompt = build_metric_recommendation_prompt(
        table_payload=table_payload,
        business_context=business_context,
        max_metrics=max_metrics,
    )
    llm = get_chat_data_llm(streaming=False, max_tokens=2000)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_prefill_recommendation_response(str(content), table_payload, max_metrics, table_alias_map)


async def _call_prefill_llm(prompt: str, generate_business_flow: bool = False) -> Dict[str, Any]:
    """调用大模型并解析自动预填字段。"""
    llm = get_chat_data_llm(streaming=False, max_tokens=4000)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_prefill_response(str(content), generate_business_flow)


def _prefill_knowledge_missing(knowledge: Dict[str, str], generate_business_flow: bool = False) -> bool:
    """判断哪些业务知识仍需生成。"""
    need_business_flow = bool(generate_business_flow and not str((knowledge or {}).get("business_flow") or "").strip())
    return need_business_flow


async def _fill_missing_prefill_knowledge(
    *,
    table_payload: List[Dict],
    knowledge: Dict[str, str],
    generate_business_flow: bool,
) -> Dict[str, str]:
    """补问大模型生成缺失的业务知识。"""
    knowledge = _filter_prefill_knowledge(knowledge, generate_business_flow)
    need_business_flow = _prefill_knowledge_missing(knowledge, generate_business_flow)
    if not need_business_flow:
        return knowledge
    prompt = build_auto_prefill_knowledge_prompt(
        table_payload=table_payload,
        generate_business_flow=need_business_flow,
    )
    result = await _call_prefill_llm(prompt, need_business_flow)
    filled = dict(knowledge or {})
    filled.update(result.get("knowledge", {}))
    return _filter_prefill_knowledge(filled, generate_business_flow)


async def _prefill_by_llm(
    tables: List[Dict],
    table_model_type: str = "detail",
    generate_business_flow: bool = False,
    business_context: str = "",
) -> Optional[Dict[str, Any]]:
    """预填字段角色：物理字段全维度，大模型只推荐少量原子指标。"""
    if not tables:
        return {"fields": [], "knowledge": {}}
    _normalize_prefill_table_model_type(table_model_type)
    table_alias_map = _build_prefill_alias_map(tables)
    table_payload = _build_prefill_table_payload(tables)
    if not table_payload:
        return {"fields": [], "knowledge": {}}
    fields = _build_prefill_dimension_fields(table_payload, table_alias_map)
    try:
        recommendation = await _call_metric_recommendation_llm(
            table_payload=table_payload,
            business_context=business_context,
            max_metrics=PREFILL_MAX_RECOMMENDED_METRICS,
            table_alias_map=table_alias_map,
        )
        if isinstance(recommendation, dict):
            fields = recommendation.get("fields") or fields
            metric_fields = recommendation.get("metrics") or []
        else:
            metric_fields = recommendation or []
    except Exception as e:
        logger.warning("自动预填调用大模型失败: %s", e)
        metric_fields = []
    return {"fields": _dedupe_prefill_display_names(fields + metric_fields), "knowledge": {}}


def _resolve_prefill_tables(datasource: Dict, selected_tables: List[str]) -> List[Dict]:
    """根据勾选的表名过滤待预填表结构。"""
    selected = set(selected_tables or [])
    target_tables = []
    for table in datasource.get("ds_tables", []) or []:
        table_name = table.get("table_name")
        if not table_name:
            continue
        if selected and table_name not in selected:
            continue
        target_tables.append(table)
    return target_tables


@router.get("/datasources/{datasource_id}/semantic-configs")
async def list_semantic_configs(datasource_id: str, request: Request):
    """获取数据源的所有语义配置。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        if not await ds_manager.has_datasource_async(datasource_id):
            return business_response(msg=f"数据源不存在: {datasource_id}", code=40404, status="error")

        configs = await _get_semantic_configs_for_user(ds_manager, datasource_id, user_id, username)
        return business_response(data={"configs": configs, "total": len(configs)}, msg="获取成功")
    except Exception as e:
        logger.error(f"获取语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=50010, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs")
async def create_semantic_config(datasource_id: str, payload: SemanticConfigCreateRequest, request: Request):
    """创建新的语义配置。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=40404, status="error")

        metrics = [m.model_dump() for m in payload.metrics]
        dimensions = [d.model_dump() for d in payload.dimensions]
        validation_error = _validate_semantic_fields(datasource, metrics, dimensions)
        if validation_error:
            return business_response(msg=validation_error, code=40010, status="error")

        config = await ds_manager.create_semantic_config_async(
            datasource_id=datasource_id,
            name=payload.name,
            description=payload.description,
            ontology_rules=payload.ontology_rules,
            enabled=payload.enabled,
            metrics=metrics,
            dimensions=dimensions,
            relationships=[r.model_dump() for r in payload.relationships],
            scope_type="private",
            create_by=user_id,
            create_by_name=username,
        )
        if not config:
            return business_response(msg="创建失败", code=50010, status="error")

        logger.info(f"创建语义配置: {config['id']} - {config['name']}")
        return business_response(data=_with_semantic_config_permissions(config, user_id, username), msg="创建成功")
    except Exception as e:
        logger.error(f"创建语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"创建失败: {str(e)}", code=50010, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/auto-prefill")
async def auto_prefill_semantic_fields(datasource_id: str, config_id: str, request: SemanticAutoPrefillRequest):
    """按规则自动预填维度/指标/表达式。"""
    try:
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=40404, status="error")
        config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")
        target_tables = _resolve_prefill_tables(datasource, request.selected_tables)

        prefill_result = await _prefill_by_llm(
            target_tables,
            request.table_model_type,
            request.generate_business_flow,
            request.business_context or "",
        )
        if prefill_result is None:
            return business_response(msg="自动预填失败：大模型返回结果不完整或不可解析，请重试", code=50010, status="error")
        fields = prefill_result.get("fields", [])

        return business_response(data={"fields": fields, "total": len(fields), "knowledge": prefill_result.get("knowledge", {})}, msg="自动预填完成")
    except Exception as e:
        logger.error(f"自动预填字段失败: {str(e)}", exc_info=True)
        return business_response(msg=f"自动预填失败: {str(e)}", code=50010, status="error")


@router.post("/semantic/prefill/context-file")
async def semantic_prefill_context_file(file: UploadFile = File(...)):
    """解析自动预填业务补充文件，不持久化文件。"""
    try:
        data = await file.read()
        if len(data) > SEMANTIC_PREFILL_CONTEXT_FILE_MAX_SIZE:
            return business_response(msg="文件大小不能超过 10MB", code=40010, status="error")
        text = extract_text_from_file_bytes(file.filename or "", data).strip()
        return business_response(
            data={
                "filename": file.filename or "",
                "size": len(data),
                "text": text[:SEMANTIC_PREFILL_CONTEXT_TEXT_MAX_LENGTH],
            },
            msg="解析成功",
        )
    except ValueError as e:
        return business_response(msg=str(e), code=40010, status="error")
    except Exception as e:
        logger.error("解析自动预填业务补充文件失败: %s", e, exc_info=True)
        return business_response(msg=f"解析失败: {str(e)}", code=50010, status="error")


@router.post("/semantic/prefill")
async def semantic_prefill(request: SemanticPrefillRequest):
    """语义字段预填短路由，前端直接传待预填表结构。"""
    try:
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(request.datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {request.datasource_id}", code=40404, status="error")

        config = await ds_manager.get_semantic_config_async(request.datasource_id, request.config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {request.config_id}", code=40404, status="error")

        target_tables = [table.model_dump() for table in request.tables]
        prefill_result = await _prefill_by_llm(
            target_tables,
            request.table_model_type,
            request.generate_business_flow,
            request.business_context or "",
        )
        if prefill_result is None:
            return business_response(msg="自动预填失败：大模型返回结果不完整或不可解析，请重试", code=50010, status="error")
        fields = prefill_result.get("fields", [])

        return business_response(data={"fields": fields, "total": len(fields), "knowledge": prefill_result.get("knowledge", {})}, msg="自动预填完成")
    except Exception as e:
        logger.error(f"语义字段预填失败: {str(e)}", exc_info=True)
        return business_response(msg=f"自动预填失败: {str(e)}", code=50010, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/history-learning")
async def learn_semantic_config_from_history(
    datasource_id: str,
    config_id: str,
    payload: SemanticHistoryLearningRequest,
    request: Request,
):
    """基于当前指标集的智能问数/可视化查询历史生成维护建议。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=40404, status="error")
        config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")

        source_set = {str(item or "").strip().lower() for item in (payload.sources or []) if str(item or "").strip()}
        if not source_set:
            return business_response(msg="请至少选择一个学习来源", code=40010, status="error")
        unsupported_sources = sorted(source_set - {"agent", "builder", "sql"})
        if unsupported_sources:
            return business_response(msg=f"不支持的学习来源: {', '.join(unsupported_sources)}", code=40010, status="error")
        cutoff_time = datetime.now() - timedelta(days=payload.days)
        status_values = ["succeeded", "partial_success"]
        if payload.include_failed:
            status_values.append("failed")
        scan_limit = min(max(payload.limit * 5, payload.limit), 1000)

        async with get_session() as db:
            stmt = (
                select(ChatDataQueryExecution, ChatDataQueryTask)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(
                    ChatDataQueryExecution.datasource_id == datasource_id,
                    ChatDataQueryExecution.user_id == user_id,
                    ChatDataQueryExecution.delete_flag == 0,
                    ChatDataQueryExecution.create_time >= cutoff_time,
                    ChatDataQueryExecution.sql_status.in_(status_values),
                    ChatDataQueryTask.query_type.in_(["agent", "builder", "sql"]),
                )
                .order_by(ChatDataQueryExecution.create_time.desc(), ChatDataQueryExecution.id.desc())
                .limit(scan_limit)
            )
            rows = (await db.execute(stmt)).all()

        samples: List[Dict[str, Any]] = []
        conversation_context_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        skipped = {
            "source": 0,
            "semantic_config": 0,
            "missing_conversation_context": 0,
            "empty_scope": 0,
            "sql_parse_failed": 0,
            "empty_history_tables": 0,
            "table_scope_mismatch": 0,
            "config_id_mismatch": 0,
        }
        source_stats: Dict[str, Dict[str, int]] = {
            "agent": {"scanned": 0, "matched": 0, "skipped": 0},
            "builder": {"scanned": 0, "matched": 0, "skipped": 0},
            "sql": {"scanned": 0, "matched": 0, "skipped": 0},
        }

        for execution, task in rows:
            task_payload = _parse_json_object(task.request_payload if task else None)
            raw_query_type = str((task.query_type if task else "") or "").strip().lower()
            query_type = raw_query_type
            if query_type not in source_stats:
                skipped["source"] += 1
                continue
            source_stats[query_type]["scanned"] += 1
            if query_type not in source_set:
                skipped["source"] += 1
                source_stats[query_type]["skipped"] += 1
                continue
            matched, skip_reason, history_tables, skip_detail = _history_belongs_to_config(
                query_type,
                task_payload,
                execution.sql_text,
                config_id,
                config,
                str(datasource.get("source_type") or ""),
            )
            if not matched:
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
                continue
            conversation_id = _extract_history_task_conversation_id(task_payload)
            sample = {
                "execution_id": execution.execution_id,
                "task_id": execution.task_id,
                "source": query_type,
                "status": execution.sql_status,
                "conversation_id": conversation_id,
                "query_text": _extract_history_task_query_text(task_payload),
                "sql": execution.sql_text,
                "tables": history_tables,
                "error_message": execution.error_message or (task.error_message if task else "") or "",
                "executed_at": (execution.finished_time or execution.started_time or execution.create_time).isoformat() if (execution.finished_time or execution.started_time or execution.create_time) else None,
            }
            if query_type == "builder" and isinstance(task_payload.get("builder_config"), dict):
                sample["builder_config"] = task_payload.get("builder_config")
            if query_type == "agent" and conversation_id:
                if conversation_id not in conversation_context_cache:
                    conversation_context_cache[conversation_id] = await _load_history_conversation_context(conversation_id)
                conversation_context = conversation_context_cache.get(conversation_id)
                if not conversation_context:
                    skipped["missing_conversation_context"] += 1
                    source_stats[query_type]["skipped"] += 1
                    continue
                sample["conversation_context"] = conversation_context
            elif query_type == "agent":
                skipped["missing_conversation_context"] += 1
                source_stats[query_type]["skipped"] += 1
                continue
            samples.append(sample)
            source_stats[query_type]["matched"] += 1
            if len(samples) >= payload.limit:
                break

        suggestions = await _generate_history_learning_suggestions_by_llm(datasource, config, samples)
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

        return business_response(
            data={
                "datasource_id": datasource_id,
                "semantic_config_id": config_id,
                "days": payload.days,
                "limit": payload.limit,
                "scan_limit": scan_limit,
                "scanned_count": len(rows),
                "matched_count": len(samples),
                "skipped": skipped,
                "source_stats": source_stats,
                "suggestions": suggestions,
                "samples": samples[:payload.limit],
            },
            msg="学习完成",
        )
    except AuthenticationError as e:
        return business_response(msg=e.message, code=40101, status="error")
    except Exception as e:
        logger.error("历史学习失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True)
        return business_response(msg=f"历史学习失败: {str(e)}", code=50010, status="error")


@router.put("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def update_semantic_config(
    datasource_id: str,
    config_id: str,
    payload: SemanticConfigUpdateRequest,
    request: Request,
):
    """更新语义配置。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(msg=f"数据源不存在: {datasource_id}", code=40404, status="error")
        current_config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")

        update_kwargs = {}
        if payload.name is not None:
            update_kwargs["name"] = payload.name
        if payload.description is not None:
            update_kwargs["description"] = payload.description
        if payload.ontology_rules is not None:
            update_kwargs["ontology_rules"] = payload.ontology_rules
        if payload.enabled is not None:
            update_kwargs["enabled"] = payload.enabled
        if payload.metrics is not None:
            update_kwargs["metrics"] = [m.model_dump() for m in payload.metrics]
        if payload.dimensions is not None:
            update_kwargs["dimensions"] = [d.model_dump() for d in payload.dimensions]
        if payload.relationships is not None:
            update_kwargs["relationships"] = [r.model_dump() for r in payload.relationships]

        merged_metrics = update_kwargs.get("metrics", current_config.get("metrics", []))
        merged_dimensions = update_kwargs.get("dimensions", current_config.get("dimensions", []))
        validation_error = _validate_semantic_fields(datasource, merged_metrics, merged_dimensions)
        if validation_error:
            return business_response(msg=validation_error, code=40010, status="error")

        config = await ds_manager.update_semantic_config_async(
            datasource_id=datasource_id,
            config_id=config_id,
            update_by=user_id,
            update_by_name=username,
            **update_kwargs,
        )
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")

        logger.info(f"更新语义配置: {config_id}")
        return business_response(data=_with_semantic_config_permissions(config, user_id, username), msg="更新成功")
    except Exception as e:
        logger.error(f"更新语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"更新失败: {str(e)}", code=50010, status="error")


@router.delete("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def delete_semantic_config(datasource_id: str, config_id: str, request: Request):
    """删除语义配置。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        current_config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")

        success = await ds_manager.delete_semantic_config_async(datasource_id, config_id, update_by=user_id, update_by_name=username)
        if not success:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")

        logger.info(f"删除语义配置: {config_id}")
        return business_response(data={"config_id": config_id}, msg="删除成功")
    except Exception as e:
        logger.error(f"删除语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"删除失败: {str(e)}", code=50010, status="error")


@router.patch("/datasources/{datasource_id}/semantic-configs/{config_id}/toggle")
async def toggle_semantic_config(datasource_id: str, config_id: str, enabled: bool, request: Request):
    """切换单个语义配置的启用状态。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        current_config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")

        config = await ds_manager.update_semantic_config_async(
            datasource_id=datasource_id,
            config_id=config_id,
            enabled=enabled,
            update_by=user_id,
            update_by_name=username,
        )
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")

        logger.info(f"切换语义配置: {config_id}, enabled={enabled}")
        return business_response(data={"config_id": config_id, "enabled": enabled}, msg=f"语义配置已{'启用' if enabled else '禁用'}")
    except Exception as e:
        logger.error(f"切换语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"操作失败: {str(e)}", code=50010, status="error")
