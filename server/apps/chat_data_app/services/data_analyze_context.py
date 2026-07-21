"""DataAnalyze 智能体上下文构建。"""

from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from ..agent.analyze_data_agent import get_data_analyze_tool_chain
from .semantic_tools import format_semantic_config_directory_for_prompt
from .datasource_manager import get_datasource_manager
from .execution_target_context import (
    append_selected_execution_targets_to_context,
    normalize_member_datasource_ids,
)

logger = get_logger(__name__)


def _truncate(value: Any, max_length: int = 120) -> str:
    """截断文本，避免上下文过大。"""
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


def _summarize_tables(tables: List[Dict[str, Any]], max_tables: int = 12, max_columns: int = 12) -> List[Dict[str, Any]]:
    """提取表字段摘要。"""
    result: List[Dict[str, Any]] = []
    for table in (tables or [])[:max_tables]:
        columns = []
        for column in (table.get("columns") or [])[:max_columns]:
            columns.append({
                "name": column.get("name") or column.get("column_name") or "",
                "type": column.get("type") or column.get("mysql_type") or column.get("data_type") or "",
                "description": _truncate(column.get("description") or column.get("comment") or "", 80),
            })
        result.append({
            "table_name": table.get("table_name") or "",
            "description": _truncate(table.get("description") or "", 120),
            "columns": columns,
        })
    return result


def _semantic_config_directory_item(
    config: Dict[str, Any],
    metrics: List[Dict[str, Any]],
    dimensions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """构造未选中指标集的轻量目录项。"""
    return {
        "name": config.get("name") or config.get("semantic_config_name") or "",
        "description": _truncate(config.get("description") or config.get("semantic_config_desc") or "", 120),
        "selected": False,
        "metrics_total": len(metrics),
        "dimensions_total": len(dimensions),
    }


def _selected_semantic_config_item(
    config: Dict[str, Any],
    config_id: str,
    metrics: List[Dict[str, Any]],
    dimensions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """构造已选中指标集的完整语义上下文。"""
    return {
        **_semantic_config_directory_item(config, metrics, dimensions),
        "id": config_id,
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
            for item in metrics
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
            for item in dimensions
        ],
        "relationships": config.get("relationships") or [],
    }


def _build_data_context_notes(has_selected_semantic_config: bool) -> List[str]:
    """构造与指标集注入状态一致的上下文说明。"""
    notes = [
        "每次语义查询必须在工具参数中明确传入 execution_targets 里的 member_datasource_ids。",
    ]
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


def _build_data_analyze_tables(
    datasource: Dict[str, Any],
    has_selected_semantic_config: bool,
) -> List[Dict[str, Any]]:
    """仅在已选择指标集后注入表结构摘要。"""
    if not has_selected_semantic_config:
        return []
    return _summarize_tables(datasource.get("ds_tables") or [])


def _summarize_semantic_configs(configs: List[Dict[str, Any]], semantic_config_id: Optional[str]) -> List[Dict[str, Any]]:
    """提取语义配置摘要。"""
    result: List[Dict[str, Any]] = []
    normalized_selected_id = str(semantic_config_id or "").strip()
    for config in configs or []:
        config_id = str(config.get("id") or config.get("semantic_config_id") or "").strip()
        metrics = config.get("metrics") or []
        dimensions = config.get("dimensions") or []
        is_selected = bool(normalized_selected_id and config_id == normalized_selected_id)
        if is_selected:
            result.append(_selected_semantic_config_item(config, config_id, metrics, dimensions))
        else:
            result.append(_semantic_config_directory_item(config, metrics, dimensions))
    return result


async def build_data_analyze_context(
    datasource_id: Optional[str],
    semantic_config_id: Optional[str] = None,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    member_datasource_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """构建 DataAnalyze 可用工具和数据上下文。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    normalized_username = str(username or "").strip()
    if not normalized_datasource_id:
        return {
            "datasource": None,
            "tables": [],
            "semantic_configs": [],
            "tool_chain": get_data_analyze_tool_chain(),
            "notes": ["当前未选择数据源，可先按通用分析框架生成分析思路、指标假设和后续工具步骤。"],
        }

    try:
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(normalized_datasource_id)
        if not datasource:
            return {
                "datasource": {"id": normalized_datasource_id, "missing": True},
                "tables": [],
                "semantic_configs": [],
                "tool_chain": get_data_analyze_tool_chain(),
                "notes": ["未找到当前数据源，可先按通用分析框架生成分析思路、指标假设和后续工具步骤。"],
            }
        if normalized_user_id:
            semantic_configs = await ds_manager.get_semantic_configs_async(normalized_datasource_id, normalized_user_id)
        else:
            semantic_configs = datasource.get("semantic_configs")
            if semantic_configs is None:
                semantic_configs = await ds_manager.get_semantic_configs_async(normalized_datasource_id)
        semantic_config_context = _summarize_semantic_configs(semantic_configs or [], semantic_config_id)
        has_selected_semantic_config = any(bool(item.get("selected")) for item in semantic_config_context)
        selected_member_ids = normalize_member_datasource_ids(member_datasource_ids)
        execution_targets = await ds_manager.get_authorized_execution_datasources_async(
            normalized_datasource_id,
            selected_member_ids,
            user_id=normalized_user_id,
            username=normalized_username,
        ) if selected_member_ids else []
        execution_target_context = append_selected_execution_targets_to_context("", execution_targets, selected_member_ids)
        semantic_config_directory_info = ""
        if not has_selected_semantic_config:
            semantic_config_directory_info = format_semantic_config_directory_for_prompt(semantic_config_context)
            if execution_target_context:
                semantic_config_directory_info = "\n\n".join([
                    execution_target_context,
                    semantic_config_directory_info,
                ]).strip()
            semantic_config_context = []
        return {
            "datasource": {
                "id": normalized_datasource_id,
                "name": datasource.get("group_name") or datasource.get("name") or "",
                "description": _truncate(datasource.get("ds_description") or datasource.get("description") or "", 160),
                "source_type": datasource.get("source_type") or "",
            },
            "execution_targets": [
                {
                    "member_datasource_id": str(item.get("member_datasource_id") or "").strip(),
                    "member_name": item.get("member_name") or item.get("name") or "",
                    "org_code": item.get("org_code") or (item.get("source_config") or {}).get("org_code") or "",
                }
                for item in execution_targets
                if str(item.get("member_datasource_id") or "").strip()
            ],
            "tables": _build_data_analyze_tables(datasource, has_selected_semantic_config),
            "semantic_config_directory_info": semantic_config_directory_info,
            "semantic_configs": semantic_config_context,
            "tool_chain": get_data_analyze_tool_chain(),
            "notes": _build_data_context_notes(has_selected_semantic_config),
        }
    except Exception as err:
        logger.error("构建 DataAnalyze 上下文失败: datasource_id=%s error=%s", normalized_datasource_id, err, exc_info=True)
        return {
            "datasource": {"id": normalized_datasource_id, "error": str(err)},
            "tables": [],
            "semantic_configs": [],
            "tool_chain": get_data_analyze_tool_chain(),
            "notes": ["读取数据源上下文失败，可先按通用分析框架生成分析思路、指标假设和后续工具步骤。"],
        }
