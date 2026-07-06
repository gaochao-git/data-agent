"""Shared semantic helpers."""

from __future__ import annotations

from typing import Any, Dict, Set

AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"


def _build_table_columns_map(datasource: Dict) -> Dict[str, Set[str]]:
    """构建数据源表字段索引。"""
    table_columns: Dict[str, Set[str]] = {}
    for table in datasource.get("ds_tables", []) or []:
        table_name = table.get("table_name")
        if not table_name:
            continue
        columns = table.get("columns", []) or []
        column_names = {col.get("name") for col in columns if col.get("name")}
        table_columns[table_name] = column_names
    return table_columns


def _semantic_config_prompt_payload(config: Dict[str, Any]) -> Dict[str, Any]:
    """裁剪语义配置上下文，避免提示词过长。"""
    return {
        "name": config.get("name"),
        "description": config.get("description"),
        "ontology_rules": config.get("ontology_rules"),
        "metrics": [
            {
                "name": item.get("name"),
                "field_name": item.get("field_name"),
                "expression": item.get("expression"),
                "table": item.get("table"),
                "description": item.get("description"),
            }
            for item in (config.get("metrics") or [])[:30]
        ],
        "dimensions": [
            {
                "name": item.get("name"),
                "field_name": item.get("field_name"),
                "column": item.get("column"),
                "table": item.get("table"),
                "type": item.get("type"),
                "description": item.get("description"),
                "allowed_values": item.get("allowed_values"),
            }
            for item in (config.get("dimensions") or [])[:40]
        ],
        "relationships": (config.get("relationships") or [])[:20],
    }
