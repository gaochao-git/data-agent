"""SQL assist schema context helpers."""

from __future__ import annotations

from typing import Any

from .datasource_manager import get_datasource_manager


async def get_all_tables_async(datasource_id: str | None) -> list[dict[str, Any]]:
    """读取 SQL 辅助生成需要的数据源表结构缓存。"""
    datasource_manager = get_datasource_manager()

    if datasource_id:
        datasource = await datasource_manager.get_datasource_async(datasource_id)
        return datasource.get("ds_tables", []) if datasource else []

    all_datasources = await datasource_manager.get_all_datasources_async()
    tables: list[dict[str, Any]] = []
    for datasource in all_datasources:
        tables.extend(datasource.get("ds_tables", []))
    return tables


def get_tables_create_format(tables: list[dict[str, Any]], selected_table_names: list[str]) -> str:
    """将已选表结构格式化为 SQL 辅助生成使用的 CREATE TABLE 文本。"""
    if not tables or not selected_table_names:
        return ""

    selected_tables = [table for table in tables if table.get("table_name") in selected_table_names]
    if not selected_tables:
        return ""

    lines: list[str] = []
    for table in selected_tables:
        table_name = table.get("table_name", "")
        description = table.get("description", "")
        columns = table.get("columns", [])

        lines.append(f"### {table_name}")
        lines.append(f"描述: {description}")
        lines.append("```sql")
        lines.append(f"CREATE TABLE {table_name} (")

        column_definitions: list[str] = []
        for column in columns:
            column_definition = f"    {column.get('name', '')} {column.get('type', 'TEXT')}"
            if column.get("primary_key"):
                column_definition += " PRIMARY KEY"
            if column.get("description"):
                column_definition += f"  -- {column.get('description')}"
            column_definitions.append(column_definition)

        lines.append(",\n".join(column_definitions))
        lines.append(");")
        lines.append("```\n")

    return "\n".join(lines)
