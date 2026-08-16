"""Execution target permission helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Set

import httpx

from mock.direct_org_codes import DIRECT_ORG_CODES
from utils.alarm import emit_external_api_alarm
from utils.logger import get_logger
from ..runtime_config import read_bool_env, read_positive_int_env

logger = get_logger(__name__)
HTTP_PERMISSION_URL = str(os.getenv("CHAT_DATA_HTTP_PERMISSION_URL") or "").strip()
HTTP_PERMISSION_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_HTTP_PERMISSION_TIMEOUT_SECONDS", 5)
HTTP_VERIFY_TLS = read_bool_env("CHAT_DATA_HTTP_VERIFY_TLS", False)


def normalize_org_codes(items: Any) -> Set[str]:
    """Normalize org code payloads returned by permission providers."""
    raw_items = items if isinstance(items, list) else []
    result: Set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        org_code = item.strip()
        if org_code:
            result.add(org_code)
    return result


async def get_http_permission_org_codes(user_id: Optional[str], username: Optional[str]) -> Set[str]:
    """Read HTTP execution org permissions from the configured permission service."""
    normalized_user_id = str(user_id or "").strip()
    normalized_username = str(username or "").strip()
    if not HTTP_PERMISSION_URL:
        logger.debug("跳过 HTTP 执行对象权限查询: CHAT_DATA_HTTP_PERMISSION_URL 未配置 user_id=%s", normalized_user_id or "-")
        return set()
    if not normalized_user_id:
        logger.debug("跳过 HTTP 执行对象权限查询: user_id 为空 url=%s", HTTP_PERMISSION_URL)
        return set()

    payload = {"userId": normalized_user_id, "username": normalized_username}
    logger.debug(
        "开始查询 HTTP 执行对象权限: url=%s timeout=%ss user_id=%s username=%s",
        HTTP_PERMISSION_URL,
        HTTP_PERMISSION_TIMEOUT_SECONDS,
        normalized_user_id,
        normalized_username or "-",
    )
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_PERMISSION_TIMEOUT_SECONDS,
            verify=HTTP_VERIFY_TLS,
        ) as client:
            response = await client.post(HTTP_PERMISSION_URL, json=payload)
            response.raise_for_status()
            response_payload = response.json()
    except Exception as exc:
        emit_external_api_alarm(
            "chat_data_http_permission",
            exc,
            method="POST",
            url=HTTP_PERMISSION_URL,
            ext1={"userId": normalized_user_id, "username": normalized_username},
        )
        return set()

    try:
        code = int(response_payload.get("code"))
    except (TypeError, ValueError, AttributeError):
        logger.debug("HTTP 执行对象权限响应 code 无效: user_id=%s response_type=%s", normalized_user_id, type(response_payload).__name__)
        return set()
    if code != 20000:
        logger.debug("HTTP 执行对象权限响应非成功: user_id=%s code=%s", normalized_user_id, code)
        return set()
    org_codes = normalize_org_codes(response_payload.get("data"))
    logger.debug("HTTP 执行对象权限查询完成: user_id=%s org_codes=%s", normalized_user_id, sorted(org_codes))
    return org_codes


async def get_allowed_org_codes_by_executor(
    user_id: Optional[str],
    username: Optional[str],
    *,
    include_http_permission: bool = True,
) -> Dict[str, Set[str]]:
    """Return allowed org codes grouped by executor type.

    HTTP permission lookup is optional because direct-only datasource groups do
    not use that permission domain. The default keeps the existing behavior
    for callers that do not know the datasource executor types.
    """
    return {
        "direct": {str(code).strip() for code in DIRECT_ORG_CODES if str(code).strip()},
        "http": (
            await get_http_permission_org_codes(user_id, username)
            if include_http_permission
            else set()
        ),
    }


def datasource_requires_http_permission(datasource: Dict[str, Any]) -> bool:
    """Return whether a datasource may contain an HTTP execution target.

    Unknown or incomplete member metadata keeps the conservative old path and
    therefore still performs the HTTP permission lookup.
    """
    member_sources = datasource.get("member_sources")
    if not isinstance(member_sources, list) or not member_sources:
        return True

    datasource_executor_type = str(datasource.get("query_executor_type") or "direct").strip().lower() or "direct"
    for member in member_sources:
        if not isinstance(member, dict):
            return True
        executor_type = str(member.get("query_executor_type") or datasource_executor_type).strip().lower() or "direct"
        if executor_type not in {"direct", "http"}:
            return True
        if executor_type == "http":
            return True
    return False


def filter_datasource_members_by_allowed_org_codes(datasource: Dict[str, Any], allowed_org_codes: Dict[str, Set[str]]) -> Optional[Dict[str, Any]]:
    """Filter datasource member_sources by executor-specific org permissions."""
    member_sources = list(datasource.get("member_sources") or [])
    if not member_sources:
        return None

    source_type = str(datasource.get("source_type") or "").strip()
    datasource_executor_type = str(datasource.get("query_executor_type") or "direct").strip().lower() or "direct"
    allowed_members: List[Dict[str, Any]] = []
    for member in member_sources:
        executor_type = str((member or {}).get("query_executor_type") or datasource_executor_type).strip().lower() or "direct"
        if executor_type not in {"direct", "http"}:
            executor_type = "direct"
        org_code = str((member or {}).get("org_code") or "").strip()
        if org_code and org_code in allowed_org_codes.get(executor_type, set()):
            allowed_members.append(member)

    if not allowed_members:
        return None

    allowed_member_ids = {
        str((member or {}).get("member_datasource_id") or "").strip()
        for member in allowed_members
        if str((member or {}).get("member_datasource_id") or "").strip()
    }
    result = dict(datasource)
    result["source_type"] = source_type or result.get("source_type")
    result["member_sources"] = allowed_members
    result["member_datasource_ids"] = [
        member_id
        for member_id in (datasource.get("member_datasource_ids") or [])
        if str(member_id or "").strip() in allowed_member_ids
    ]
    template_member_id = str(datasource.get("template_datasource_id") or "").strip()
    if template_member_id not in allowed_member_ids:
        result["template_datasource_id"] = str((allowed_members[0] or {}).get("member_datasource_id") or "").strip()
    return result


def filter_datasources_by_allowed_org_codes(datasources: Iterable[Dict[str, Any]], allowed_org_codes: Dict[str, Set[str]]) -> List[Dict[str, Any]]:
    """Filter datasource list by execution target permissions."""
    result: List[Dict[str, Any]] = []
    for datasource in datasources or []:
        filtered = filter_datasource_members_by_allowed_org_codes(datasource, allowed_org_codes)
        if filtered:
            result.append(filtered)
    return result
