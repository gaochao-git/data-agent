"""DataAnalyze prompt context built on the shared semantic conversation context."""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

from ..agent.analyze_data_agent import get_data_analyze_tool_chain
from .datasource_manager import get_datasource_manager
from .semantic_conversation_context import (
    AgentPromptContext,
)

logger = get_logger(__name__)


def _truncate(value: Any, max_length: int = 120) -> str:
    """Trim text before it is included in an analysis prompt."""
    text = str(value or "").strip()
    return text if len(text) <= max_length else f"{text[:max_length]}..."


def _summarize_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the bounded table summary needed only by DataAnalyze prompts."""
    result: list[dict[str, Any]] = []
    for table in tables[:12]:
        columns = [
            {
                "name": column.get("name") or column.get("column_name") or "",
                "type": column.get("type") or column.get("mysql_type") or column.get("data_type") or "",
                "description": _truncate(column.get("description") or column.get("comment") or "", 80),
            }
            for column in (table.get("columns") or [])[:12]
        ]
        result.append({
            "table_name": table.get("table_name") or "",
            "description": _truncate(table.get("description") or "", 120),
            "columns": columns,
        })
    return result


def _selected_semantic_config_item(config: dict[str, Any]) -> dict[str, Any]:
    """Adapt the shared runtime config to the bounded analysis prompt format."""
    return {
        "id": config.get("id") or config.get("semantic_config_id") or "",
        "name": config.get("name") or config.get("semantic_config_name") or "",
        "selected": True,
        "source_type": config.get("source_type") or "",
        "business_description": _truncate(config.get("ontology_rules") or "", 300),
        "metrics": [
            {
                "name": item.get("name") or "",
                "description": _truncate(item.get("description") or "", 80),
                "table": item.get("table") or "",
                "column": item.get("column") or "",
                "expression": _truncate(item.get("expression") or "", 120),
                "aggregation": item.get("aggregation") or item.get("agg_func") or "",
            }
            for item in config.get("metrics") or []
        ],
        "dimensions": [
            {
                "name": item.get("name") or "",
                "description": _truncate(item.get("description") or "", 80),
                "table": item.get("table") or "",
                "column": item.get("column") or "",
                "logical_type": item.get("logical_type") or "",
                "allowed_values": item.get("allowed_values") or [],
                "mapping": item.get("mapping") or {},
            }
            for item in config.get("dimensions") or []
        ],
        "relationships": config.get("relationships") or [],
    }


def _build_data_context_notes(has_selected_semantic_config: bool) -> list[str]:
    notes = ["每次语义查询必须在工具参数中明确传入当前用户可用的 member_datasource_ids。"]
    if has_selected_semantic_config:
        return [
            "当前已手动选择指标集，完整指标、维度和关联关系已注入提示词。",
            *notes,
            "后续只使用当前选中指标集；口径不清时先澄清。",
        ]
    return [
        "未手动选择指标集时仅注入指标集轻量目录；业务数据分析必须先选择指标集，再使用工具返回的完整指标和维度。",
        *notes,
        "用户没有明确口径时，先选择最匹配的指标集；候选不唯一或口径不清时先澄清。",
    ]


async def build_data_analyze_context(
    prompt_context: AgentPromptContext,
) -> dict[str, Any]:
    """Add DataAnalyze-only prompt fields to the shared prompt contract."""
    datasource = prompt_context.get("datasource")
    if datasource is None:
        return _empty_data_context(
            prompt_context,
            "当前未选择数据源，可先按通用分析框架生成分析思路、指标假设和后续工具步骤。",
        )
    if datasource.get("missing"):
        return _empty_data_context(
            prompt_context,
            "未找到当前数据源，可先按通用分析框架生成分析思路、指标假设和后续工具步骤。",
        )

    selected_config = prompt_context.get("semantic_config")
    datasource_id = str(datasource.get("id") or "").strip()
    resolved_datasource = dict(datasource)
    tables: list[dict[str, Any]] = []
    if selected_config is not None and datasource_id:
        resolved_datasource = await _load_datasource_with_tables(datasource_id, resolved_datasource)
        tables = _summarize_tables(resolved_datasource.get("ds_tables") or [])

    return {
        "datasource": {
            "id": datasource_id,
            "name": resolved_datasource.get("group_name") or resolved_datasource.get("name") or "",
            "description": _truncate(resolved_datasource.get("ds_description") or resolved_datasource.get("description") or "", 160),
            "source_type": resolved_datasource.get("source_type") or "",
        },
        "execution_targets": list(prompt_context.get("execution_targets") or []),
        "datasource_group_context": str(prompt_context.get("datasource_group_context") or ""),
        "tables": tables,
        "semantic_config_directory_info": str(prompt_context.get("semantic_config_directory_info") or ""),
        "semantic_configs": [_selected_semantic_config_item(selected_config)] if selected_config else [],
        "tool_chain": get_data_analyze_tool_chain(),
        "notes": _build_data_context_notes(selected_config is not None),
    }


async def _load_datasource_with_tables(
    datasource_id: str,
    fallback_datasource: dict[str, Any],
) -> dict[str, Any]:
    """Load table metadata only when a selected config makes it useful to analysis."""
    try:
        datasource = await get_datasource_manager().get_datasource_async(datasource_id)
        return datasource or fallback_datasource
    except Exception as exc:
        logger.warning("读取智能分析表结构失败: datasource_id=%s error=%s", datasource_id, exc)
        return fallback_datasource


def _empty_data_context(prompt_context: AgentPromptContext, message: str) -> dict[str, Any]:
    return {
        "datasource": prompt_context.get("datasource"),
        "execution_targets": list(prompt_context.get("execution_targets") or []),
        "datasource_group_context": str(prompt_context.get("datasource_group_context") or ""),
        "tables": [],
        "semantic_config_directory_info": str(prompt_context.get("semantic_config_directory_info") or ""),
        "semantic_configs": [],
        "tool_chain": get_data_analyze_tool_chain(),
        "notes": [message],
    }
