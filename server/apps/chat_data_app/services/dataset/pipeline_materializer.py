"""Pipeline 输出物理物化：把 build 结果写入输入 member 对应的物理表。

MVP 只支持 Hive direct 成员；MySQL/HTTP 在后续版本补执行适配器。
输出表名与 Dataset 名一致，且拒绝覆盖输入 Dataset 的物理表。
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from apps.chat_data_app.services.datasource.manager import get_datasource_manager
from apps.chat_data_app.services.datasource.target_context import get_execution_target_member_id
from apps.chat_data_app.services.write import get_write_adapter

from .pipeline_errors import PipelineBuildError
from .pipeline_runner import _select_binding

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_identifier(value: Any, field: str) -> str:
    name = _text(value)
    if not name or not _IDENTIFIER.match(name):
        raise PipelineBuildError(f"{field} 不是合法标识符: {name}")
    return name


def _hive_type(data_type: Any) -> str:
    value = _text(data_type).lower()
    if value in {"integer", "int", "bigint", "smallint"}:
        return "int" if value != "bigint" else "bigint"
    if value in {"decimal", "float", "double"}:
        return "decimal(18,4)" if value == "decimal" else "double"
    if value == "boolean":
        return "boolean"
    if value == "date":
        return "date"
    if value in {"timestamp", "datetime"}:
        return "timestamp"
    return "string"


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return "'" + value.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(value, date):
        return "'" + value.strftime("%Y-%m-%d") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def build_create_table_sql(database: str, table: str, columns: list[dict[str, Any]]) -> str:
    safe_db = _safe_identifier(database, "database")
    safe_table = _safe_identifier(table, "physical_table")
    definitions = []
    for column in columns:
        name = _safe_identifier(column.get("name"), "column")
        definitions.append(f"`{name}` {_hive_type(column.get('data_type'))}")
    if not definitions:
        raise PipelineBuildError("输出 Dataset 没有可物化列")
    return f"CREATE TABLE `{safe_db}`.`{safe_table}` ({', '.join(definitions)})"


def build_insert_sql(database: str, table: str, columns: list[dict[str, Any]], row: dict[str, Any]) -> str:
    safe_db = _safe_identifier(database, "database")
    safe_table = _safe_identifier(table, "physical_table")
    values = [_sql_literal(row.get(_text(column.get("name")))) for column in columns]
    return f"INSERT INTO `{safe_db}`.`{safe_table}` VALUES ({', '.join(values)})"


async def materialize_hive_output_async(
    target: dict[str, Any],
    *,
    table: str,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    input_tables: set[str],
) -> str:
    """写 Hive 输出表，返回实际物理表名。"""
    source_config = target.get("source_config") or {}
    database = _safe_identifier(source_config.get("database") or "default", "database")
    safe_table = _safe_identifier(table, "physical_table")
    if safe_table in input_tables:
        raise PipelineBuildError(f"输出表不能覆盖输入 Dataset 物理表: {safe_table}")
    statements = [
        f"DROP TABLE IF EXISTS `{database}`.`{safe_table}`",
        build_create_table_sql(database, safe_table, columns),
    ]
    statements.extend(build_insert_sql(database, safe_table, columns, row) for row in rows)
    adapter = get_write_adapter(target)
    try:
        await adapter.execute_statements_async(target, statements)
    except PipelineBuildError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成 Pipeline 构建错误
        raise PipelineBuildError(f"Hive 写入失败: {exc}") from exc
    return safe_table


async def materialize_output_async(
    dataset_name: str,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    input_dataset: dict[str, Any],
    *,
    member_datasource_ids: list[str] | None,
    user_id: str,
    username: str,
) -> list[dict[str, Any]]:
    """按输入 member 物化输出表，返回 Dataset binding 列表。"""
    binding = _select_binding(input_dataset, member_datasource_ids)
    group_id = _text(binding.get("datasource_group_id"))
    member_id = _text(binding.get("member_datasource_id"))
    if not group_id or not member_id:
        raise PipelineBuildError("输入 Dataset binding 缺少数据源组或成员")
    manager = get_datasource_manager()
    targets = await manager.list_authorized_execution_targets_async(
        group_id, user_id=user_id or None, username=username or None
    )
    target = next((item for item in targets if get_execution_target_member_id(item) == member_id), None)
    if target is None:
        raise PipelineBuildError(f"当前用户无权执行成员数据源: {member_id}")
    if _text(target.get("source_type")).lower() != "hive":
        raise PipelineBuildError("Pipeline 输出物化当前只支持 Hive direct 数据源")
    input_tables = {_text(item.get("physical_table")) for item in input_dataset.get("bindings") or []}
    physical_table = await materialize_hive_output_async(
        target,
        table=dataset_name,
        columns=columns,
        rows=rows,
        input_tables=input_tables,
    )
    return [
        {
            "datasource_group_id": group_id,
            "member_datasource_id": member_id,
            "physical_table": physical_table,
        }
    ]
