"""Shared helpers for ChatData routers."""

from __future__ import annotations

from typing import Any, Dict, Set

from fastapi import HTTPException, Request

from auth import AuthenticationError, authenticate_request

AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"

def _get_current_user(request: Request):
    """解析当前登录用户。"""
    try:
        user = authenticate_request(request)
    except AuthenticationError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err
    user_id = str(user.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing user_id in token")
    return user


def _optional_current_user(request: Request):
    """尽量解析当前用户，兼容未登录测试场景。"""
    try:
        return _get_current_user(request)
    except Exception:
        return None


def _current_user_names(user: Any) -> Dict[str, str]:
    """构造当前用户 ID 到用户名的映射。"""
    user_id = str(getattr(user, "user_id", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    return {user_id: username} if user_id and username else {}


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


def _semantic_config_scope_type(config: Dict[str, Any]) -> str:
    """读取指标集范围，缺省按公共指标集处理。"""
    return str((config or {}).get("scope_type") or "public").strip().lower()


def _is_public_semantic_config(config: Dict[str, Any]) -> bool:
    """判断是否为系统公共指标集。"""
    return bool(config and _semantic_config_scope_type(config) == "public")


def _is_private_semantic_config_owner(config: Dict[str, Any], user_id: str) -> bool:
    """判断当前用户是否拥有该个人指标集。"""
    return bool(
        config
        and _semantic_config_scope_type(config) == "private"
        and str((config or {}).get("create_by") or "").strip() == str(user_id or "").strip()
    )


def _can_mutate_semantic_config(config: Dict[str, Any], user_id: str, admin_manage: bool = False) -> bool:
    """判断当前入口是否允许修改指标集。"""
    return _is_public_semantic_config(config) if admin_manage else _is_private_semantic_config_owner(config, user_id)


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
