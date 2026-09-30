"""执行对象上下文格式化。"""

from typing import Any


def normalize_member_datasource_ids(member_datasource_ids: list[str] | None) -> list[str]:
    """规整执行对象数据源 ID。"""
    normalized_ids: list[str] = []
    for item in member_datasource_ids or []:
        member_id = str(item).strip()
        if member_id and member_id not in normalized_ids:
            normalized_ids.append(member_id)
    return normalized_ids


def get_execution_target_member_id(target: dict[str, Any]) -> str:
    """Return the stable member datasource identifier for an execution target."""
    return str(target.get("member_datasource_id") or target.get("id") or "").strip()


def get_execution_target_name(target: dict[str, Any], index: int) -> str:
    """Return the user-facing execution target name."""
    source_config = target.get("source_config") if isinstance(target.get("source_config"), dict) else {}
    return str(
        target.get("member_name")
        or target.get("name")
        or target.get("org_name")
        or source_config.get("org_name")
        or get_execution_target_member_id(target)
        or f"执行对象{index}"
    ).strip()


def get_execution_target_org_code(target: dict[str, Any]) -> str:
    """Return the organization code for matching and prompt display."""
    source_config = target.get("source_config") if isinstance(target.get("source_config"), dict) else {}
    return str(
        target.get("org_code") or source_config.get("org_code") or get_execution_target_member_id(target) or ""
    ).strip()


def source_type_to_dialect(source_type: Any) -> str:
    """数据源 source_type → SQL 方言展示名（mysql→MySQL，其余一律按 Hive 处理）。

    方言映射的唯一权威入口：data_analyzing_tool 的 list_execution_targets
    返回与 SQL 助手链路共用，避免两套映射口径漂移。
    """
    normalized = str(source_type or "").strip().lower()
    return "MySQL" if normalized == "mysql" else "Hive"
