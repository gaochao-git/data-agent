"""DataAnalyze 智能体上下文构建。"""

from typing import Any, Dict, List, Optional

from utils.logger import get_logger
from ..agent.analyze_data_agent import get_data_analyze_tool_chain
from .datasource_manager import get_datasource_manager

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


def _summarize_semantic_configs(configs: List[Dict[str, Any]], semantic_config_id: Optional[str]) -> List[Dict[str, Any]]:
    """提取语义配置摘要。"""
    result: List[Dict[str, Any]] = []
    normalized_selected_id = str(semantic_config_id or "").strip()
    for config in configs or []:
        config_id = str(config.get("id") or config.get("semantic_config_id") or "").strip()
        metrics = config.get("metrics") or []
        dimensions = config.get("dimensions") or []
        is_selected = bool(normalized_selected_id and config_id == normalized_selected_id)
        metric_items = metrics if is_selected else metrics[:20]
        dimension_items = dimensions if is_selected else dimensions[:20]
        result.append({
            "id": config_id,
            "name": config.get("name") or config.get("semantic_config_name") or "",
            "source_type": config.get("source_type") or "",
            "description": _truncate(config.get("description") or config.get("semantic_config_desc") or "", 120),
            "business_description": _truncate(config.get("ontology_rules") or "", 300),
            "selected": is_selected,
            "metrics_total": len(metrics),
            "dimensions_total": len(dimensions),
            "metrics": [
                {
                    "name": item.get("name") or "",
                    "description": _truncate(item.get("description") or "", 80),
                    "table": item.get("table") or "",
                    "expression": _truncate(item.get("expression") or "", 120),
                }
                for item in metric_items
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
                for item in dimension_items
            ],
        })
    return result


async def build_data_analyze_context(
    datasource_id: Optional[str],
    semantic_config_id: Optional[str] = None,
    user_id: Optional[str] = None,
    member_datasource_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """构建 DataAnalyze 可用工具和数据上下文。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
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
        selected_member_ids = [str(item or "").strip() for item in member_datasource_ids or [] if str(item or "").strip()]
        execution_targets = await ds_manager.get_execution_datasources_async(normalized_datasource_id, selected_member_ids) if selected_member_ids else []
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
            "tables": _summarize_tables(datasource.get("ds_tables") or []),
            "semantic_configs": _summarize_semantic_configs(semantic_configs or [], semantic_config_id),
            "tool_chain": get_data_analyze_tool_chain(),
            "notes": [
                "数据源、表字段、语义指标和维度已注入提示词，可作为默认分析口径。",
                "每次语义查询必须在工具参数中明确传入 execution_targets 里的 member_datasource_ids。",
                "用户没有明确口径时，优先从已有指标和维度中选择最匹配项并继续推进，在结果中说明默认口径和可调整参数。",
            ],
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
