"""语义模型自动预填服务（自 routers/semantic_config.py 下沉的 LLM 编排块）。

职责：构造紧凑表结构 → 调用大模型推荐显示名与少量原子指标 → 解析校验并
去重。router 只保留端点与鉴权，LLM 调用与解析一律走本模块。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.messages import HumanMessage

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from utils.llm_json import parse_llm_json

from ....agent.prompts.dim_metric_auto_prefill import build_metric_recommendation_prompt
from ...llm_config import get_chat_data_llm

logger = get_logger(__name__)

PREFILL_MAX_RECOMMENDED_METRICS = 10
PREFILL_ALLOWED_METRIC_AGGS = {"SUM", "AVG", "MAX", "MIN", "COUNT"}
PREFILL_DISPLAY_NAME_MAX_LENGTH = 15


def _quote_identifier(name: str) -> str:
    """转义 SQL 标识符。"""
    safe = (name or "").replace("`", "``")
    return f"`{safe}`"


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


def _build_prefixed_prefill_name(table_alias: str, name: str) -> str:
    """重名字段追加表角色前缀；超长时按预算截断别名。"""
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
    alias_part = alias_parts[-1] if alias_parts and len(alias_parts[-1]) >= 2 else alias
    return _normalize_prefill_display_name(f"{alias_part[:budget]}_{name}", name)


def _count_prefill_name_occurrences(items: List[Dict[str, str]]) -> Dict[str, int]:
    """统计自动预填字段名出现次数。"""
    name_counts: Dict[str, int] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        if name:
            name_counts[name] = name_counts.get(name, 0) + 1
    return name_counts


def _resolve_prefill_unique_name(name: str, used_names: Set[str]) -> str:
    """重名时追加自增数字后缀，保证展示名唯一。"""
    candidate = name
    index = 2
    while candidate in used_names:
        suffix = str(index)
        candidate = f"{name[:max(PREFILL_DISPLAY_NAME_MAX_LENGTH - len(suffix), 1)]}{suffix}"
        index += 1
    return candidate


def _dedupe_prefill_display_names(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """自动预填字段重名时追加表角色前缀。"""
    name_counts = _count_prefill_name_occurrences(items)
    used_names: Set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        table_alias = str(item.get("table_alias") or item.get("table") or "").strip()
        if name_counts.get(name, 0) > 1 and table_alias:
            name = _build_prefixed_prefill_name(table_alias, name)
        candidate = _resolve_prefill_unique_name(name, used_names)
        item["name"] = candidate
        used_names.add(candidate)
    return items


def _build_prefill_columns(table: Dict) -> List[Dict[str, str]]:
    """构造单张表的紧凑字段列表。"""
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
    return columns


def _build_prefill_table_payload(tables: List[Dict]) -> List[Dict[str, Any]]:
    """构造自动预填使用的紧凑表结构。"""
    table_payload = []
    seen_tables: Set[str] = set()
    for table in tables or []:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name or table_name in seen_tables:
            continue
        seen_tables.add(table_name)
        table_payload.append({
            "table": table_name,
            "description": table.get("description") or "",
            "columns": _build_prefill_columns(table),
        })
    return table_payload


def _build_table_columns_index(table_payload: List[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """构建物理表字段名索引，供预填解析校验使用。"""
    return {
        table.get("table", ""): {col.get("name", "") for col in table.get("columns", []) if col.get("name")}
        for table in table_payload
        if table.get("table")
    }


def _resolve_prefill_alias_map(
    table_payload: List[Dict[str, Any]],
    table_alias_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, List[str]]:
    """解析表别名映射，缺省回退为表名自身。"""
    return table_alias_map or {table.get("table", ""): [table.get("table", "")] for table in table_payload if table.get("table")}


def _parse_prefill_dimension_name_map(parsed: Dict[str, Any], table_payload: List[Dict[str, Any]]) -> Dict[str, str]:
    """解析模型生成的物理字段显示名称。"""
    raw_fields = parsed.get("fields", []) if isinstance(parsed, dict) else []
    if not isinstance(raw_fields, list):
        return {}
    table_columns = _build_table_columns_index(table_payload)
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
    alias_map = _resolve_prefill_alias_map(table_payload, table_alias_map)
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


def _resolve_metric_candidate(item: Any, table_columns: Dict[str, Set[str]]) -> Optional[Tuple[str, str, str]]:
    """提取并校验单条推荐指标的 (表, 字段, 聚合) 三元组；非法时返回 None。"""
    if not isinstance(item, dict):
        return None
    table_name = str(item.get("table") or "").strip()
    column_name = str(item.get("column") or item.get("source_column") or "").strip()
    agg = str(item.get("agg") or "").strip().upper()
    if table_name not in table_columns or column_name not in table_columns[table_name] or agg not in PREFILL_ALLOWED_METRIC_AGGS:
        return None
    return table_name, column_name, agg


def _build_metric_entry(item: Dict[str, Any], table_name: str, column_name: str, agg: str) -> Dict[str, str]:
    """构造单条原子指标条目（不含表别名）。"""
    fallback_name = f"{column_name}_{agg.lower()}"
    return {
        "table": table_name,
        "table_alias": "",
        "column": column_name,
        "source_column": column_name,
        "role": "metric",
        "field_name": str(item.get("field_name") or fallback_name).strip() or fallback_name,
        "name": _normalize_prefill_display_name(item.get("name"), column_name),
        "agg": agg,
        "expression": f"{agg}({_quote_identifier(column_name)})",
    }


def _append_metric_entries(
    normalized: List[Dict[str, str]],
    item: Dict[str, Any],
    table_name: str,
    column_name: str,
    agg: str,
    aliases: List[str],
    max_metrics: int,
) -> bool:
    """按表别名展开指标条目；达到数量上限时返回 True。"""
    entry = _build_metric_entry(item, table_name, column_name, agg)
    for table_alias in aliases:
        normalized.append(dict(entry, table_alias=table_alias))
        if len(normalized) >= max_metrics:
            return True
    return False


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
    table_columns = _build_table_columns_index(table_payload)
    alias_map = _resolve_prefill_alias_map(table_payload, table_alias_map)
    normalized: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in raw_metrics:
        candidate = _resolve_metric_candidate(item, table_columns)
        if candidate is None:
            continue
        table_name, column_name, agg = candidate
        key = f"{table_name}.{column_name}.{agg}"
        if key in seen:
            continue
        seen.add(key)
        aliases = alias_map.get(table_name) or [table_name]
        if _append_metric_entries(normalized, item, table_name, column_name, agg, aliases, max_metrics):
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
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={"purpose": "semantic_metric_recommendation"},
        )
        raise
    content = response.content if hasattr(response, "content") else str(response)
    return _parse_prefill_recommendation_response(str(content), table_payload, max_metrics, table_alias_map)


async def prefill_by_llm(
    tables: List[Dict],
    business_context: str = "",
) -> Optional[Dict[str, Any]]:
    """优化字段显示名称，并推荐少量高置信度原子指标。"""
    if not tables:
        return {"fields": []}
    table_alias_map = _build_prefill_alias_map(tables)
    table_payload = _build_prefill_table_payload(tables)
    if not table_payload:
        return {"fields": []}
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
    return {"fields": _dedupe_prefill_display_names(fields + metric_fields)}
