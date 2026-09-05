"""智能问数指标集选择器。"""

from __future__ import annotations

import json
import asyncio
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from utils.llm_json import parse_llm_json
from apps.chat_data_app.agent.context.context_budget import REQUEST_OVERHEAD_TOKENS, estimate_text_tokens, get_model_context_budget
from ...datasource.manager import get_datasource_manager
from ...llm_config import get_chat_data_llm
from .semantic_config_service import get_semantic_config_service

logger = get_logger(__name__)

SELECTOR_MAX_CONCURRENCY = 4
SELECTOR_MAX_SELECTED_CONFIGS = 5


def _truncate(value: Any, max_length: int) -> str:
    """裁剪指标集摘要文本，避免选择器上下文过大。"""
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _config_id(config: Dict[str, Any]) -> str:
    """读取指标集 ID。"""
    return str(config.get("id") or config.get("semantic_config_id") or "").strip()


def _config_name(config: Dict[str, Any]) -> str:
    """读取指标集名称。"""
    return str(config.get("name") or config.get("semantic_config_name") or "").strip()


def _config_description(config: Dict[str, Any]) -> str:
    """读取指标集描述。"""
    return str(config.get("description") or config.get("semantic_config_desc") or "").strip()


def _compact_metric_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    """构造指标选择摘要，只保留业务语义字段。"""
    summary = {
        "name": item.get("name") or "",
        "description": _truncate(item.get("description") or "", 160),
        "supplement": _truncate(item.get("metadata_supplement") or "", 240),
    }
    return {key: value for key, value in summary.items() if value}


def _dimension_allowed_values(item: Dict[str, Any]) -> List[str]:
    """归一化 allowed_values 为截断字符串列表。"""
    allowed_values = item.get("allowed_values") if isinstance(item.get("allowed_values"), list) else []
    return [str(value)[:80] for value in allowed_values if str(value or "").strip()]


def _dimension_value_mapping(item: Dict[str, Any]) -> Dict[str, str]:
    """归一化 mapping 为截断字符串字典（键值均非空才保留）。"""
    mapping = item.get("mapping") if isinstance(item.get("mapping"), dict) else {}
    return {str(key)[:80]: str(value)[:80] for key, value in mapping.items() if str(key or "").strip() and str(value or "").strip()}


def _compact_dimension_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    """构造维度选择摘要，只保留业务语义字段。"""
    summary = {
        "name": item.get("name") or "",
        "description": _truncate(item.get("description") or "", 160),
        "supplement": _truncate(item.get("metadata_supplement") or "", 240),
        "logical_type": item.get("logical_type") or "",
        "allowed_values": _dimension_allowed_values(item),
        "mapping": _dimension_value_mapping(item),
    }
    return {key: value for key, value in summary.items() if value not in ("", [], {})}


def _semantic_table_names(config: Dict[str, Any]) -> set[str]:
    """提取当前指标集实际引用的物理表名。"""
    table_names: set[str] = set()
    for item in [*(config.get("metrics") or []), *(config.get("dimensions") or [])]:
        table_name = str((item or {}).get("table") or "").strip()
        if table_name:
            table_names.add(table_name)
    for relationship in config.get("relationships") or []:
        left_table = str((relationship or {}).get("left_table") or "").strip()
        right_table = str((relationship or {}).get("right_table") or "").strip()
        if left_table:
            table_names.add(left_table)
        if right_table:
            table_names.add(right_table)
    return table_names


def _column_display_name(column: Dict[str, Any]) -> str:
    return str((column or {}).get("name") or (column or {}).get("column_name") or "").strip()


def _column_display_type(column: Dict[str, Any]) -> str:
    return str(
        (column or {}).get("type")
        or (column or {}).get("mysql_type")
        or (column or {}).get("hive_type")
        or (column or {}).get("data_type")
        or ""
    ).strip()


def _column_display_description(column: Dict[str, Any]) -> str:
    return _truncate((column or {}).get("description") or (column or {}).get("comment") or "", 80)


def _compact_table_columns(table: Dict[str, Any], max_columns: int) -> List[Dict[str, Any]]:
    columns = []
    for column in (table.get("columns") or [])[:max_columns]:
        column_name = _column_display_name(column)
        if not column_name:
            continue
        column_item = {
            "name": column_name,
            "type": _column_display_type(column),
            "description": _column_display_description(column),
        }
        columns.append({key: value for key, value in column_item.items() if value})
    return columns


def _compact_table_summary(config: Dict[str, Any], max_columns: int = 80) -> List[Dict[str, Any]]:
    """构造指标集相关表字段摘要，只保留表名、字段名、类型和备注。"""
    table_names = _semantic_table_names(config)
    if not table_names:
        return []
    result: List[Dict[str, Any]] = []
    for table in config.get("tables") or []:
        table_name = str((table or {}).get("table_name") or "").strip()
        if not table_name or table_name not in table_names:
            continue
        result.append({
            "table_name": table_name,
            "description": _truncate((table or {}).get("description") or "", 120),
            "columns": _compact_table_columns(table or {}, max_columns),
        })
    return result


def _compact_relationship_summary(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """构造表关系摘要，帮助指标集检索识别跨表语义。"""
    result: List[Dict[str, Any]] = []
    for relationship in config.get("relationships") or []:
        left_table = str((relationship or {}).get("left_table") or "").strip()
        right_table = str((relationship or {}).get("right_table") or "").strip()
        if not left_table or not right_table:
            continue
        result.append({
            "left_table": left_table,
            "right_table": right_table,
            "join_type": str((relationship or {}).get("join_type") or "").strip(),
            "conditions": _compact_join_conditions(relationship),
        })
    return result


def _compact_join_conditions(relationship: Dict[str, Any]) -> List[str]:
    """拼接有效的 join 条件列对，格式为 left=right。"""
    conditions: List[str] = []
    for condition in (relationship or {}).get("join_conditions") or []:
        left_column = str((condition or {}).get("left_column") or "").strip()
        right_column = str((condition or {}).get("right_column") or "").strip()
        if left_column and right_column:
            conditions.append(f"{left_column}={right_column}")
    return conditions


def _summarize_semantic_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """构造给大模型判断的指标集摘要。"""
    metrics = config.get("metrics") or []
    dimensions = config.get("dimensions") or []
    config_id = _config_id(config)
    return {
        "id": config_id,
        "name": _config_name(config),
        "description": _truncate(config.get("description") or config.get("semantic_config_desc") or "", 180),
        "business_description": _truncate(config.get("ontology_rules") or "", 500),
        "dimensions_total": len(dimensions),
        "metrics_total": len(metrics),
        "dimensions": [_compact_dimension_summary(item) for item in dimensions],
        "tables": _compact_table_summary(config),
        "relationships": _compact_relationship_summary(config),
        "metrics": [_compact_metric_summary(item) for item in metrics],
    }


def _build_selector_prompt(user_query: str, configs: List[Dict[str, Any]], datasource_group_context: str = "") -> str:
    """构造只负责指标集检索的提示词。"""
    payload = {
        "user_query": str(user_query or "").strip(),
        "datasource_group_context": str(datasource_group_context or "").strip(),
        "semantic_configs": configs,
    }
    return (
        "你是智能问数的指标集检索器，不负责回答、澄清、查询规划或计算。\n"
        "要求：\n"
        "1) 只从给定 semantic_configs 中返回与用户问题业务对象、指标、维度、筛选字段或业务术语相关的 1 至 5 个 id，不能编造 id。\n"
        "2) 这是检索，不是覆盖校验。不要判断问题是否可回答，不要判断口径是否充分，不要生成澄清问题、执行计划、子问题、计算公式或跨指标集方案。\n"
        "3) 可以返回多个相关指标集；主模型会依据完整运行时语义配置自行决定后续使用哪个指标集、是否查询或是否澄清。\n"
        "4) datasource_group_context 仅用于理解当前业务背景，不能替代 semantic_configs 中的真实指标、维度和口径。\n"
        "5) 没有相关指标集时返回空数组。\n"
        "6) 输出 JSON，不要解释文本，格式：{\"semantic_config_ids\":[\"...\"]}\n"
        "输入：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _estimate_selector_prompt_tokens(user_query: str, configs: List[Dict[str, Any]], datasource_group_context: str = "") -> int:
    """估算选择器提示词 token 数。"""
    return estimate_text_tokens(_build_selector_prompt(user_query, configs, datasource_group_context)) + REQUEST_OVERHEAD_TOKENS


def _chunk_semantic_config_summaries(
    summaries: List[Dict[str, Any]],
    user_query: str,
    token_budget: int,
    datasource_group_context: str = "",
) -> List[List[Dict[str, Any]]]:
    """按完整指标集分片，禁止把单个指标集拆到两个分片。"""
    chunks: List[List[Dict[str, Any]]] = []
    current_chunk: List[Dict[str, Any]] = []
    safe_budget = max(int(token_budget or 0), 1)
    for summary in summaries:
        single_tokens = _estimate_selector_prompt_tokens(user_query, [summary], datasource_group_context)
        if single_tokens > safe_budget:
            logger.warning(
                "单个指标集选择上下文超过统一输入容量但不会拆分: config_id=%s tokens=%s hard_input_limit=%s",
                summary.get("id") or "",
                single_tokens,
                safe_budget,
            )
        if not current_chunk:
            current_chunk = [summary]
            continue
        next_tokens = _estimate_selector_prompt_tokens(user_query, [*current_chunk, summary], datasource_group_context)
        if next_tokens > safe_budget:
            chunks.append(current_chunk)
            current_chunk = [summary]
        else:
            current_chunk.append(summary)
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def _normalize_semantic_config_ids(value: Any, config_names: Dict[str, str]) -> List[str]:
    """标准化选择器返回的指标集 ID 列表。"""
    if not isinstance(value, list):
        return []
    valid_ids = set(config_names)
    result: List[str] = []
    for item in value:
        config_id = str(item or "").strip()
        if not config_id or config_id not in valid_ids:
            continue
        if config_id in result:
            continue
        result.append(config_id)
        if len(result) >= SELECTOR_MAX_SELECTED_CONFIGS:
            break
    return result


def _parse_selection_response(content: str, configs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """解析并校验指标集检索模型返回。"""
    parsed = parse_llm_json(str(content or ""), strict=False)
    if not isinstance(parsed, dict):
        parsed = {}
    config_names = {_config_id(config): _config_name(config) or _config_id(config) for config in configs if _config_id(config)}
    return {"semantic_config_ids": _normalize_semantic_config_ids(parsed.get("semantic_config_ids"), config_names)}


async def _invoke_selector_prompt(llm: Any, prompt: str) -> str:
    """执行一次选择器模型调用。"""
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={"purpose": "semantic_model_search"},
        )
        raise
    return str(getattr(response, "content", response) or "")


async def _select_semantic_config_chunk(
    llm: Any,
    user_query: str,
    chunk: List[Dict[str, Any]],
    datasource_group_context: str = "",
) -> Dict[str, Any]:
    """选择单个分片内最匹配的指标集。"""
    prompt = _build_selector_prompt(user_query, chunk, datasource_group_context)
    content = await _invoke_selector_prompt(llm, prompt)
    return _parse_selection_response(content, chunk)


async def _select_semantic_config_chunks(
    llm: Any,
    user_query: str,
    chunks: List[List[Dict[str, Any]]],
    datasource_group_context: str = "",
) -> List[Dict[str, Any]]:
    """并发执行多个指标集分片选择。"""
    semaphore = asyncio.Semaphore(SELECTOR_MAX_CONCURRENCY)

    async def run_chunk(chunk: List[Dict[str, Any]]) -> Dict[str, Any]:
        async with semaphore:
            return await _select_semantic_config_chunk(llm, user_query, chunk, datasource_group_context)

    results = await asyncio.gather(*(run_chunk(chunk) for chunk in chunks), return_exceptions=True)
    selections: List[Dict[str, Any]] = []
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("指标集分片选择失败: chunk_index=%s error=%s", index + 1, result, exc_info=True)
            continue
        selections.append(result)
    return selections


def _merge_semantic_config_selections(selections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按分片顺序去重合并检索结果，不再进行二次业务决策。"""
    semantic_config_ids: List[str] = []
    for selection in selections:
        for config_id in selection.get("semantic_config_ids") or []:
            normalized_id = str(config_id or "").strip()
            if not normalized_id or normalized_id in semantic_config_ids:
                continue
            semantic_config_ids.append(normalized_id)
            if len(semantic_config_ids) >= SELECTOR_MAX_SELECTED_CONFIGS:
                return {"semantic_config_ids": semantic_config_ids}
    return {"semantic_config_ids": semantic_config_ids}


async def _enabled_semantic_configs(
    datasource_id: str,
    user_id: str,
    semantic_configs: List[Dict[str, Any]] | None,
) -> List[Dict[str, Any]]:
    """configs 缺省时读取运行时配置，筛出有 ID 且启用的指标集。"""
    if semantic_configs is None:
        configs = await get_semantic_config_service().get_runtime_configs_async(
            datasource_id,
            user_id,
        )
    else:
        configs = semantic_configs
    return [
        config for config in configs or []
        if _config_id(config) and bool(config.get("enabled", True))
    ]


async def search_semantic_models(
    datasource_id: str | None,
    user_query: str,
    model_id: str | None = None,
    user_id: str | None = None,
    datasource_group_context: str | None = None,
    semantic_configs: List[Dict[str, Any]] | None = None,
) -> Optional[Dict[str, Any]]:
    """用大模型从当前数据源组可用指标集中选择最匹配项。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_query = str(user_query or "").strip()
    normalized_user_id = str(user_id or "").strip()
    normalized_group_context = str(datasource_group_context or "").strip()
    if not normalized_datasource_id or not normalized_query:
        return None

    enabled_configs = await _enabled_semantic_configs(normalized_datasource_id, normalized_user_id, semantic_configs)
    if not enabled_configs:
        return None

    summaries = [_summarize_semantic_config(config) for config in enabled_configs]
    selector_budget = get_model_context_budget(model_id, max_output_tokens=1200)
    chunks = _chunk_semantic_config_summaries(
        summaries,
        normalized_query,
        token_budget=selector_budget.hard_input_limit_tokens,
        datasource_group_context=normalized_group_context,
    )
    chunk_tokens = [
        _estimate_selector_prompt_tokens(normalized_query, chunk, normalized_group_context)
        for chunk in chunks
    ]
    logger.info(
        "智能问数指标集选择上下文: datasource_id=%s configs=%s chunks=%s chunk_tokens=%s hard_input_limit_tokens=%s",
        normalized_datasource_id,
        len(enabled_configs),
        len(chunks),
        chunk_tokens,
        selector_budget.hard_input_limit_tokens,
    )
    llm = get_chat_data_llm(model=model_id, streaming=False, max_tokens=1200)
    if len(chunks) == 1:
        selection = await _select_semantic_config_chunk(llm, normalized_query, chunks[0], normalized_group_context)
    else:
        chunk_selections = await _select_semantic_config_chunks(llm, normalized_query, chunks, normalized_group_context)
        selection = _merge_semantic_config_selections(chunk_selections)
    logger.info(
        "智能问数指标集检索: datasource_id=%s configs=%s chunks=%s semantic_config_ids=%s",
        normalized_datasource_id,
        len(enabled_configs),
        len(chunks),
        selection.get("semantic_config_ids") or [],
    )
    return selection


__all__ = [
    "search_semantic_models",
    "_parse_selection_response",
    "_summarize_semantic_config",
]
