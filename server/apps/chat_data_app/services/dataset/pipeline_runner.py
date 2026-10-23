"""Pipeline 输入读取适配层：从 Dataset member binding 读取源数据。

执行外部数据源走 ``sql_run.runtime.execute_sql_for_member``（成员 SQL 执行唯一权威入口）。
该导入放在函数内延迟执行：dataset 服务在模块导入期不应拉起查询运行时/语义服务依赖，
同时单测可以直接替换 :func:`load_dataset_rows_async`。
"""

from __future__ import annotations

import re
from typing import Any

from apps.chat_data_app.services.datasource.manager import get_datasource_manager
from apps.chat_data_app.services.datasource.target_context import get_execution_target_member_id

from .pipeline_errors import PipelineBuildError

DEFAULT_FETCH_LIMIT = 5000
MAX_FETCH_LIMIT = 50000
_EXECUTE_TIMEOUT_SECONDS = 30
_SAFE_TABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_table_name(value: Any) -> str:
    table = _text(value)
    if not table or not _SAFE_TABLE_PATTERN.match(table):
        raise PipelineBuildError(f"physical_table 不是合法标识符: {table}")
    return table


def _select_binding(dataset: dict[str, Any], member_ids: list[str] | None) -> dict[str, Any]:
    requested = {_text(item) for item in member_ids or [] if _text(item)}
    bindings = [item for item in dataset.get("bindings") or [] if isinstance(item, dict)]
    if requested:
        bindings = [item for item in bindings if _text(item.get("member_datasource_id")) in requested]
    if not bindings:
        raise PipelineBuildError("输入 Dataset 没有可用 member binding")
    if len(bindings) > 1:
        raise PipelineBuildError("输入 Dataset 有多个 member binding，请在 source 节点指定 member_datasource_ids")
    return bindings[0]


def _normalize_fetch_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FETCH_LIMIT
    return max(1, min(limit, MAX_FETCH_LIMIT))


async def _select_execution_target(
    group_id: str,
    member_id: str,
    *,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    manager = get_datasource_manager()
    targets = await manager.list_authorized_execution_targets_async(
        group_id,
        user_id=user_id or None,
        username=username or None,
    )
    for target in targets:
        if get_execution_target_member_id(target) == member_id:
            return target
    raise PipelineBuildError(f"当前用户无权执行成员数据源: {member_id}")


async def _execute_member_sql(
    target: dict[str, Any],
    sql: str,
    *,
    group_id: str,
    limit: int,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    # 延迟导入：避免 dataset 服务导入期拉起查询运行时（依赖 sql_run/semantic）
    from apps.chat_data_app.services.sql_run.runtime import execute_sql_for_member

    return await execute_sql_for_member(
        target,
        sql,
        limit=limit,
        timeout=_EXECUTE_TIMEOUT_SECONDS,
        user_id=user_id or None,
        username=username or None,
        datasource_id=group_id,
    )


def _physical_to_logical(row: dict[str, Any], columns: list[dict[str, Any]]) -> dict[str, Any]:
    """按 Dataset 列定义把物理列映射成逻辑列；无 schema 时原样返回。"""
    mappings: list[tuple[str, str]] = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        logical = _text(column.get("name"))
        physical = _text(column.get("physical_column")) or logical
        if logical and physical:
            mappings.append((physical, logical))
    if not mappings:
        return dict(row)
    # 部分执行器返回带表前缀的列名（table.column），按物理列尾段归一化。
    physical_values = {str(key).split(".")[-1]: value for key, value in row.items()}
    return {logical: physical_values.get(physical) for physical, logical in mappings}


def _normalize_rows(data: Any, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in data or []:
        if isinstance(item, dict):
            rows.append(_physical_to_logical(item, columns))
    return rows


async def load_dataset_rows_async(
    dataset: dict[str, Any],
    *,
    member_datasource_ids: list[str] | None = None,
    user_id: str = "",
    username: str = "",
    limit: Any = DEFAULT_FETCH_LIMIT,
) -> list[dict[str, Any]]:
    """读取输入 Dataset 首个 member 的物理表数据（最多 limit 行）。"""
    binding = _select_binding(dataset, member_datasource_ids)
    group_id = _text(binding.get("datasource_group_id"))
    member_id = _text(binding.get("member_datasource_id"))
    if not group_id or not member_id:
        raise PipelineBuildError("Dataset binding 缺少 datasource_group_id/member_datasource_id")
    table = _safe_table_name(binding.get("physical_table"))
    target = await _select_execution_target(group_id, member_id, user_id=user_id, username=username)
    fetch_limit = _normalize_fetch_limit(limit)
    result = await _execute_member_sql(
        target,
        f"SELECT * FROM {table}",
        group_id=group_id,
        limit=fetch_limit,
        user_id=user_id,
        username=username,
    )
    if result.get("status") != "succeeded":
        raise PipelineBuildError(f"输入 Dataset 读取失败: {_text(result.get('error')) or '未知错误'}")
    columns = [item for item in dataset.get("columns") or [] if isinstance(item, dict)]
    return _normalize_rows(result.get("data"), columns)


__all__ = [
    "DEFAULT_FETCH_LIMIT",
    "MAX_FETCH_LIMIT",
    "load_dataset_rows_async",
]
