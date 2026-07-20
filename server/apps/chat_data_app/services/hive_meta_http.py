"""Hive 元数据 HTTP 适配器。"""

from __future__ import annotations

import os
import asyncio
from typing import Any, Dict, List, Optional

import httpx

from utils.alarm import emit_external_api_alarm
from utils.logger import get_logger
from .sql_executor import HTTP_AUTH_TOKEN

logger = get_logger(__name__)


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = str(os.getenv(name) or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，回退默认值 %s", name, raw_value, default)
        return default
    if value < 1:
        logger.warning("环境变量 %s=%s 小于 1，回退默认值 %s", name, value, default)
        return default
    return value


META_TABLES_URL = str(os.getenv("CHAT_DATA_META_TABLES_URL") or "").strip()
META_SCHEMA_URL = str(os.getenv("CHAT_DATA_META_SCHEMA_URL") or "").strip()
META_TIMEOUT_SECONDS = _read_positive_int_env("CHAT_DATA_META_TIMEOUT_SECONDS", 30)
META_SCHEMA_MAX_CONCURRENCY = _read_positive_int_env("CHAT_DATA_META_SCHEMA_MAX_CONCURRENCY", 10)


def validate_hive_meta_config() -> None:
    """校验 Hive 元数据 HTTP 配置。"""
    if not META_TABLES_URL or not META_SCHEMA_URL:
        raise ValueError("元数据 HTTP 模式需要配置 CHAT_DATA_META_TABLES_URL/CHAT_DATA_META_SCHEMA_URL")


def _build_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if HTTP_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {HTTP_AUTH_TOKEN}"
    return headers


def _add_user_payload(payload: Dict[str, Any], user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """补充外部 HTTP 请求的用户参数。"""
    normalized_user_id = str(user_id or "").strip()
    normalized_username = str(username or "").strip()
    if normalized_user_id:
        payload["userId"] = normalized_user_id
    if normalized_username:
        payload["username"] = normalized_username
    return payload


def _meta_payload_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """生成元数据 HTTP 请求日志摘要。"""
    summary = dict(payload or {})
    table_names = summary.get("table_names")
    if isinstance(table_names, list):
        summary["table_count"] = len(table_names)
        if len(table_names) > 10:
            summary["table_names"] = table_names[:10]
    return summary


def _meta_response_summary(payload: Any) -> Dict[str, Any]:
    """生成元数据 HTTP 响应日志摘要。"""
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    data = payload.get("data")
    result = data if isinstance(data, dict) else payload
    tables = result.get("tables")
    summary = {
        "code": payload.get("code"),
        "msg": payload.get("msg") or payload.get("message"),
    }
    if isinstance(tables, list):
        summary["table_count"] = len(tables)
    return {key: value for key, value in summary.items() if value is not None}


def _build_payload(source_config: Dict[str, Any], table_names: Optional[List[str]] = None, user_id: Optional[str] = None, username: Optional[str] = None) -> Dict[str, Any]:
    org_code = str(source_config.get("org_code") or "").strip()
    if not org_code:
        raise ValueError("元数据 HTTP 请求缺少 orgCode")
    org_name = str(source_config.get("org_name") or "").strip()
    payload = {
        "source_type": "hive",
        "orgCode": org_code,
        "orgName": org_name,
        "database": source_config.get("database") or "default",
    }
    if source_config.get("host"):
        payload["host"] = source_config.get("host")
    if source_config.get("port") is not None:
        payload["port"] = int(source_config.get("port"))
    _add_user_payload(payload, user_id, username)
    if table_names is not None:
        payload["table_names"] = table_names
    return payload


def _extract_response_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    code = payload.get("code")
    if code is not None:
        try:
            normalized_code = int(code)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Hive 元数据接口返回非法 code: {code}") from exc
    else:
        normalized_code = None
    if normalized_code is not None and normalized_code != 20000:
        raise RuntimeError(str(payload.get("msg") or "Hive 元数据接口返回失败"))
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _normalize_schema_tables(tables: List[Any], table_descriptions: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """校验并补齐外部返回的 Hive 表结构。"""
    description_map = table_descriptions or {}
    validated_tables: List[Dict[str, Any]] = []
    for table_index, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            raise RuntimeError(f"Hive 元数据表结构第 {table_index} 项不是对象")
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            raise RuntimeError(f"Hive 元数据表结构第 {table_index} 项缺少 table_name")
        columns = table.get("columns")
        if not isinstance(columns, list):
            raise RuntimeError(f"Hive 元数据表结构 {table_name} 缺少 columns")
        normalized_columns: List[Dict[str, Any]] = []
        for column_index, column in enumerate(columns, start=1):
            if not isinstance(column, dict):
                raise RuntimeError(f"Hive 元数据表结构 {table_name} 第 {column_index} 个字段不是对象")
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                raise RuntimeError(f"Hive 元数据表结构 {table_name} 第 {column_index} 个字段缺少 name")
            column_type = str(column.get("type") or "").strip()
            if not column_type:
                raise RuntimeError(f"Hive 元数据表结构 {table_name}.{column_name} 缺少 type")
            column_description = str(column.get("description") or column_name).strip()
            if "is_partition" not in column:
                raise RuntimeError(f"Hive 元数据表结构 {table_name}.{column_name} 缺少 is_partition")
            is_partition = column.get("is_partition")
            if not isinstance(is_partition, bool):
                raise RuntimeError(f"Hive 元数据表结构 {table_name}.{column_name} 的 is_partition 必须是布尔值")
            normalized_columns.append({
                "name": column_name,
                "type": column_type,
                "hive_type": column_type,
                "description": column_description,
                "primary_key": False,
                "nullable": True,
                "is_partition": is_partition,
            })
        validated_tables.append({
            "table_name": table_name,
            "description": description_map.get(table_name) or table.get("description") or table_name,
            "columns": normalized_columns,
        })
    return validated_tables


def _normalize_table_items(tables: List[Any]) -> List[Dict[str, str]]:
    """从表列表接口返回中提取表名和表描述。"""
    table_items: List[Dict[str, str]] = []
    for table_index, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            raise RuntimeError(f"Hive 元数据表列表第 {table_index} 项不是对象")
        table_name = str(table.get("table_name") or "").strip()
        table_description = str(table.get("table_description") or "").strip()
        if not table_name:
            raise RuntimeError(f"Hive 元数据表列表第 {table_index} 项缺少 table_name")
        table_items.append({"table_name": table_name, "description": table_description})
    return table_items


async def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("[HiveMetaHTTP] 请求对方接口: url=%s payload=%s", url, _meta_payload_summary(payload))
    try:
        async with httpx.AsyncClient(verify=False, timeout=META_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload, headers=_build_headers())
            response.raise_for_status()
            response_payload = response.json()
            logger.debug(
                "[HiveMetaHTTP] 对方接口返回: url=%s status_code=%s result=%s",
                url,
                getattr(response, "status_code", None),
                _meta_response_summary(response_payload),
            )
            return _extract_response_data(response_payload)
    except Exception as exc:
        emit_external_api_alarm(
            "hive_meta_http",
            exc,
            method="POST",
            url=url,
            ext1=_meta_payload_summary(payload),
        )
        raise


async def get_hive_meta_tables_async(source_config: Dict[str, Any], user_id: Optional[str] = None, username: Optional[str] = None) -> List[str]:
    """通过外部 HTTP 接口获取 Hive 表名列表。"""
    validate_hive_meta_config()
    data = await _post_json(META_TABLES_URL, _build_payload(source_config, user_id=user_id, username=username))
    tables = data.get("tables")
    if not isinstance(tables, list):
        raise RuntimeError("Hive 元数据表列表接口返回缺少 data.tables")
    return [item["table_name"] for item in _normalize_table_items(tables)]


async def get_hive_meta_schema_async(source_config: Dict[str, Any], table_names: Optional[List[str]] = None, user_id: Optional[str] = None, username: Optional[str] = None) -> List[Dict[str, Any]]:
    """通过外部 HTTP 接口获取 Hive 完整表结构。"""
    validate_hive_meta_config()
    data = await _post_json(META_TABLES_URL, _build_payload(source_config, user_id=user_id, username=username))
    tables = data.get("tables")
    if not isinstance(tables, list):
        raise RuntimeError("Hive 元数据表列表接口返回缺少 data.tables")
    table_items = _normalize_table_items(tables)
    table_descriptions = {item["table_name"]: item["description"] for item in table_items if item["description"]}
    if table_names is None:
        selected_table_names = [item["table_name"] for item in table_items]
    else:
        selected_table_names = table_names
    normalized_table_names = [str(table_name).strip() for table_name in selected_table_names if str(table_name or "").strip()]
    if not normalized_table_names:
        return []

    semaphore = asyncio.Semaphore(META_SCHEMA_MAX_CONCURRENCY)

    async def fetch_single_table_schema(table_name: str) -> List[Dict[str, Any]]:
        async with semaphore:
            data = await _post_json(META_SCHEMA_URL, _build_payload(source_config, [table_name], user_id=user_id, username=username))
            tables = data.get("tables")
            if not isinstance(tables, list):
                raise RuntimeError(f"Hive 元数据表结构接口返回缺少 data.tables: {table_name}")
            return _normalize_schema_tables(tables, table_descriptions)

    results = await asyncio.gather(*(fetch_single_table_schema(table_name) for table_name in normalized_table_names))
    table_by_name = {
        table["table_name"]: table
        for tables in results
        for table in tables
    }
    missing_tables = [table_name for table_name in normalized_table_names if table_name not in table_by_name]
    if missing_tables:
        raise RuntimeError(f"Hive 元数据接口未返回表结构: {', '.join(missing_tables)}")
    return [table_by_name[table_name] for table_name in normalized_table_names]
