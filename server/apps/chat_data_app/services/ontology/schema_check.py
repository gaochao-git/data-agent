"""本体 schema 启动自检。

历史事故：代码已依赖 ``chat_data_ontology_object_type.write_mode``，但环境未执行
DDL，``query_mode`` 探测失败后静默降级 semantic，问题被掩盖到运行时。这里把
「本体依赖的关键表/列是否存在」提前到启动阶段：缺失时直接拒绝启动，而不是让
服务带着不可用的本体链路继续运行。

只在 ``CHAT_DATA_ONTOLOGY_INJECTION_ENABLED`` 开启时检查；Action 写回相关表只在
``CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED`` 开启时要求。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import inspect

from utils.database import get_engine
from utils.logger import get_logger

from .object_projection import ontology_action_execute_enabled
from .query_mode import ontology_injection_enabled

logger = get_logger(__name__)

_OBJECT_TYPE_TABLE = "chat_data_ontology_object_type"
_OBJECT_SET_TABLE = "chat_data_ontology_object_set"
_ACTION_TABLES = (
    "chat_data_ontology_action_object",
    "chat_data_ontology_action_execution",
    "chat_data_ontology_object_edit",
)
_REQUIRED_OBJECT_TYPE_COLUMNS = frozenset({"write_mode"})


def required_tables(*, action_execute_enabled: bool) -> set[str]:
    """当前部署形态依赖的本体表集合。"""
    tables = {_OBJECT_TYPE_TABLE, _OBJECT_SET_TABLE}
    if action_execute_enabled:
        tables.update(_ACTION_TABLES)
    return tables


def missing_schema_issues(
    table_columns: Mapping[str, set[str]],
    *,
    action_execute_enabled: bool,
) -> list[str]:
    """纯函数：根据已读取的表/列返回缺失项；便于单测覆盖所有分支。"""
    issues: list[str] = []
    for table in sorted(required_tables(action_execute_enabled=action_execute_enabled)):
        columns = table_columns.get(table)
        if columns is None:
            issues.append(f"缺少表 {table}")
            continue
        if table == _OBJECT_TYPE_TABLE:
            missing = sorted(_REQUIRED_OBJECT_TYPE_COLUMNS - set(columns))
            if missing:
                issues.append(f"表 {table} 缺少列: {', '.join(missing)}")
    return issues


async def require_ontology_schema_async() -> None:
    """本体开启时校验关键 schema；缺失直接抛错，禁止静默带病启动。"""
    if not ontology_injection_enabled():
        return
    action_enabled = ontology_action_execute_enabled()
    required = required_tables(action_execute_enabled=action_enabled)
    engine = get_engine()

    def _read_schema(sync_connection: Any) -> dict[str, set[str]]:
        inspector = inspect(sync_connection)
        existing = set(inspector.get_table_names())
        return {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in required
            if table in existing
        }

    async with engine.connect() as connection:
        table_columns = await connection.run_sync(_read_schema)
    issues = missing_schema_issues(table_columns, action_execute_enabled=action_enabled)
    if issues:
        detail = "；".join(issues)
        logger.error("本体 schema 自检失败: %s", detail)
        raise RuntimeError(f"本体 schema 自检失败，拒绝启动: {detail}")
    logger.info("本体 schema 自检通过: tables=%s action_execute=%s", sorted(required), action_enabled)


__all__ = ["missing_schema_issues", "require_ontology_schema_async", "required_tables"]
