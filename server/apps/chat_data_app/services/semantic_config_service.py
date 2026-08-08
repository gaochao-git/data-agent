"""Semantic configuration access rules and save-time validation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .semantic_core.config_validator import (
    expression_uses_physical_column,
    validate_semantic_fields,
)


def _scope_type(config: Dict[str, Any]) -> str:
    """Read the scope, treating missing scope as a public configuration."""
    return str((config or {}).get("scope_type") or "public").strip().lower()


def is_public_semantic_config(config: Dict[str, Any]) -> bool:
    return bool(config and _scope_type(config) == "public")


def is_private_semantic_config_owner(config: Dict[str, Any], user_id: str) -> bool:
    return bool(
        config
        and _scope_type(config) == "private"
        and str((config or {}).get("create_by") or "").strip() == str(user_id or "").strip()
    )


def can_mutate_semantic_config(
    config: Dict[str, Any],
    user_id: str,
    admin_manage: bool = False,
) -> bool:
    return is_public_semantic_config(config) if admin_manage else is_private_semantic_config_owner(config, user_id)


def column_dict_has_enum_mapping(column_dict: Optional[Dict[str, Any]]) -> bool:
    enum_values = (column_dict or {}).get("enum_values") if isinstance(column_dict, dict) else []
    if not isinstance(enum_values, list):
        return False
    return any(str((item or {}).get("value") or "").strip() for item in enum_values if isinstance(item, dict))


async def validate_semantic_logical_types(ds_manager: Any, datasource_id: str, dimensions: List[Dict]) -> Optional[str]:
    """Validate save-time logical type dependencies."""
    for dim in dimensions or []:
        if str((dim or {}).get("logical_type") or "").strip().lower() != "enum":
            continue
        table_name = str((dim or {}).get("table") or "").strip()
        column_name = str((dim or {}).get("column") or "").strip()
        column_dict = await ds_manager.get_column_dict_async(datasource_id, table_name, column_name)
        if not column_dict_has_enum_mapping(column_dict):
            return f"维度 {dim.get('name', '')} 选择了枚举逻辑类型，请先维护字段字典枚举值: {table_name}.{column_name}"
    return None


async def validate_semantic_config_for_save(
    ds_manager: Any,
    datasource_id: str,
    datasource: Dict,
    metrics: List[Dict],
    dimensions: List[Dict],
) -> Optional[str]:
    """Validate semantic config before create/update persistence."""
    validation_error = validate_semantic_fields(datasource, metrics, dimensions)
    if validation_error:
        return validation_error
    return await validate_semantic_logical_types(ds_manager, datasource_id, dimensions)
