"""数据源 DAO 的格式解析与标准化辅助函数。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
UPSERT_BATCH_SIZE = 200
_PARSE_WARNING_KEYS: set[tuple[str, str]] = set()
_PARSE_WARNING_LOCK = threading.Lock()
VALID_TABLE_INDEX_TYPES = {"primary", "unique", "secondary"}


def _warn_parse_once(message: str, text: str) -> None:
    """同一条解析异常仅记录一次，避免数据源读取时重复刷屏。"""
    key = (message, text)
    with _PARSE_WARNING_LOCK:
        if key in _PARSE_WARNING_KEYS:
            return
        _PARSE_WARNING_KEYS.add(key)
    logger.warning(message, text)


def _format_time(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime(TIME_FORMAT)
    return str(value)


def _load_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except Exception:
            logger.warning("解析 JSON 字段失败，返回默认值")
            return default
    return default


def _unique_non_empty_texts(values: List[Any]) -> List[str]:
    result: List[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _normalize_column_enum_values(raw_enums: Any) -> List[Dict[str, str]]:
    """标准化枚举值列表（最多 100 个，按 value 去重）。"""
    enum_values: List[Dict[str, str]] = []
    if not isinstance(raw_enums, list):
        return enum_values
    seen_values: set[str] = set()
    for item in raw_enums:
        if len(enum_values) >= 100:
            break
        if not isinstance(item, dict):
            continue
        enum_value = str(item.get("value") or item.get("enum_value") or "").strip()[:128]
        if not enum_value or enum_value in seen_values:
            continue
        seen_values.add(enum_value)
        enum_values.append({
            "value": enum_value,
            "label": str(item.get("label") or item.get("enum_label") or "").strip()[:255],
        })
    return enum_values


def _normalize_column_synonyms(raw_synonyms: Any) -> List[str]:
    """标准化同义词列表（最多 50 个，去重）。"""
    synonyms: List[str] = []
    if not isinstance(raw_synonyms, list):
        return synonyms
    seen_synonyms: set[str] = set()
    for value in raw_synonyms:
        if len(synonyms) >= 50:
            break
        text = str(value or "").strip()[:100]
        if not text or text in seen_synonyms:
            continue
        seen_synonyms.add(text)
        synonyms.append(text)
    return synonyms


def _normalize_column_dict(raw_dict: Dict[str, Any] | None) -> Dict[str, Any]:
    """标准化字段数据字典内容。"""
    payload = raw_dict if isinstance(raw_dict, dict) else {}
    return {
        "enum_values": _normalize_column_enum_values(payload.get("enum_values") or payload.get("enums") or []),
        "synonyms": _normalize_column_synonyms(payload.get("synonyms") or []),
        "storage_format": str(payload.get("storage_format") or "").strip()[:255],
        "unit": str(payload.get("unit") or "").strip()[:100],
    }


def _column_dict_is_empty(payload: Dict[str, Any]) -> bool:
    """判断字段数据字典是否为空。"""
    normalized = _normalize_column_dict(payload)
    return not normalized["enum_values"] and not normalized["synonyms"] and not normalized["storage_format"] and not normalized["unit"]


def _normalize_table_index_items(items: Any) -> List[Dict[str, Any]]:
    """Normalize persisted table indexes without inventing missing metadata."""
    if not isinstance(items, list):
        return []
    result: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        index_type = str(raw_item.get("index_type") or "").strip().lower()
        if index_type not in VALID_TABLE_INDEX_TYPES:
            continue
        index_name = str(raw_item.get("index_name") or "").strip()
        if index_type == "primary":
            index_name = "PRIMARY"
        raw_columns = raw_item.get("columns")
        columns = _unique_non_empty_texts(raw_columns) if isinstance(raw_columns, list) else []
        if not columns:
            continue
        identity = index_name.lower() or "\x1f".join(columns)
        key = (index_type, identity)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "index_type": index_type,
            "index_name": index_name,
            "columns": columns,
        })
    return result


def _legacy_physical_indexes(columns: Any) -> List[Dict[str, Any]]:
    """Best-effort fallback for rows written before indexes_json existed."""
    if not isinstance(columns, list):
        return []
    primary_columns = [
        str(column.get("name") or "").strip()
        for column in columns
        if isinstance(column, dict) and column.get("primary_key") and str(column.get("name") or "").strip()
    ]
    result: List[Dict[str, Any]] = []
    if primary_columns:
        result.append({"index_type": "primary", "index_name": "PRIMARY", "columns": primary_columns})
    for column in columns:
        if not isinstance(column, dict):
            continue
        column_name = str(column.get("name") or "").strip()
        if not column_name:
            continue
        if column.get("unique_key"):
            result.append({"index_type": "unique", "index_name": "", "columns": [column_name]})
        if column.get("index_key"):
            result.append({"index_type": "secondary", "index_name": "", "columns": [column_name]})
    return result


def _normalize_table_indexes_payload(raw_payload: Any, columns: Any = None) -> Dict[str, List[Dict[str, Any]]]:
    """Return the canonical {physical, manual} indexes_json payload."""
    payload = _load_json(raw_payload, None)
    if not isinstance(payload, dict):
        return {
            "physical": _legacy_physical_indexes(columns),
            "manual": [],
        }
    return {
        "physical": _normalize_table_index_items(payload.get("physical")),
        "manual": _normalize_table_index_items(payload.get("manual")),
    }


def _physical_indexes_from_table(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for columns in table.get("primary_keys") or []:
        result.append({"index_type": "primary", "index_name": "PRIMARY", "columns": columns})
    for item in table.get("unique_indexes") or []:
        result.append({
            "index_type": "unique",
            "index_name": (item or {}).get("name") if isinstance(item, dict) else "",
            "columns": (item or {}).get("columns") if isinstance(item, dict) else [],
        })
    for item in table.get("indexes") or []:
        result.append({
            "index_type": "secondary",
            "index_name": (item or {}).get("name") if isinstance(item, dict) else "",
            "columns": (item or {}).get("columns") if isinstance(item, dict) else [],
        })
    return _normalize_table_index_items(result)


def _merge_table_indexes_for_sync(table: Dict[str, Any], existing_payload: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Refresh physical metadata while retaining user-maintained indexes."""
    existing = _normalize_table_indexes_payload(existing_payload, table.get("columns"))
    has_physical_metadata = any(
        key in table for key in ("primary_keys", "unique_indexes", "indexes")
    )
    physical = _physical_indexes_from_table(table) if has_physical_metadata else existing["physical"]
    return {"physical": physical, "manual": existing["manual"]}


def _batch_items(items: List[Dict[str, Any]], batch_size: int = UPSERT_BATCH_SIZE) -> List[List[Dict[str, Any]]]:
    return [items[index:index + batch_size] for index in range(0, len(items), batch_size)]


def _build_source_line(host: Any, port: Any, database: Any) -> str | None:
    normalized_host = str(host or "").strip()
    normalized_database = str(database or "").strip()
    normalized_port = str(port or "").strip()
    if not normalized_host or not normalized_port or not normalized_database:
        return None
    return f"{normalized_host}:{normalized_port}:{normalized_database}"


def _normalized_source_config_field(source_config: Dict[str, Any] | None, key: str) -> str:
    """读取 source_config 字段并归一化为去除首尾空白的字符串。"""
    return str((source_config or {}).get(key) or "").strip()


def _build_member_source_text_line(source_config: Dict[str, Any] | None) -> str | None:
    org_name = _normalized_source_config_field(source_config, "org_name")
    org_code = _normalized_source_config_field(source_config, "org_code")
    if not org_name or not org_code:
        return None
    query_executor_type = _normalized_source_config_field(source_config, "query_executor_type").lower() or "direct"
    if query_executor_type == "http":
        database = _normalized_source_config_field(source_config, "database")
        if not database:
            return None
        return f"{org_name}|{org_code}|http|{database}"

    source_line = _build_source_line(
        (source_config or {}).get("host"),
        (source_config or {}).get("port"),
        (source_config or {}).get("database"),
    )
    if not source_line:
        return None
    return f"{org_name}|{org_code}|{query_executor_type}|{source_line}"


def _build_member_identity(source_type: Any, query_executor_type: Any, org_code: Any, source_line: Any) -> str | None:
    normalized_org_code = str(org_code or "").strip()
    return normalized_org_code or None


def _normalize_query_executor_type_for_parse(source_type: Any, query_executor_type: Any) -> str:
    normalized_query_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    if normalized_query_executor_type not in {"direct", "http"}:
        return "direct"
    return normalized_query_executor_type


def _parse_source_line(source_line: Any) -> Optional[Dict[str, Any]]:
    text = str(source_line or "").strip()
    if not text:
        return None
    parts = text.split(":", 2)
    if len(parts) != 3:
        _warn_parse_once("数据源格式无效，忽略: %s", text)
        return None
    host, port_text, database = parts
    host = host.strip()
    database = database.strip()
    if not host or not port_text.strip() or not database:
        return None
    try:
        port = int(port_text.strip())
    except ValueError:
        _warn_parse_once("数据源端口无效，忽略: %s", text)
        return None
    return {
        "source_line": text,
        "host": host,
        "port": port,
        "database": database,
    }


def _parse_member_source_entry(raw_line: Any, source_type: Any, query_executor_type: Any) -> Optional[Dict[str, Any]]:
    text = str(raw_line or "").strip()
    if not text:
        return None
    fields = _split_member_source_fields(text)
    if fields is None:
        return None
    org_name, org_code, executor_type, source_text = fields
    normalized_query_executor_type = _normalize_query_executor_type_for_parse(source_type, executor_type)

    if normalized_query_executor_type == "http":
        parsed = _parse_http_member_source(source_text, text)
        if parsed is None:
            return None
    else:
        parsed = _parse_source_line(source_text)
        if not parsed:
            return None
    member_identity = _build_member_identity(source_type, normalized_query_executor_type, org_code, parsed.get("source_line"))
    if not member_identity:
        _warn_parse_once("成员执行源缺少唯一标识，忽略: %s", text)
        return None
    parsed["datasource_id"] = member_identity
    parsed["org_name"] = org_name or None
    parsed["org_code"] = org_code or None
    parsed["query_executor_type"] = normalized_query_executor_type
    parsed["raw_line"] = text
    return parsed


def _split_member_source_fields(text: str) -> Optional[tuple[str, str, str, str]]:
    """校验并拆分「机构名称|机构编码|执行方式|源描述」四段格式。"""
    parts = [part.strip() for part in text.split("|")]
    if len(parts) != 4:
        _warn_parse_once("成员数据源格式无效，仅支持 HTTP=机构名称|机构编码|http|db_name 或直连=机构名称|机构编码|direct|ip:port:db_name: %s", text)
        return None
    org_name, org_code, executor_type, source_text = parts
    if not org_name:
        _warn_parse_once("成员数据源机构名称为空，忽略: %s", text)
        return None
    if not org_code:
        _warn_parse_once("成员数据源机构编码为空，忽略: %s", text)
        return None
    if executor_type not in {"direct", "http"}:
        _warn_parse_once("成员数据源执行方式无效，忽略: %s", text)
        return None
    return org_name, org_code, executor_type, source_text


def _parse_http_member_source(source_text: str, raw_text: str) -> Optional[Dict[str, Any]]:
    """解析 HTTP 成员的数据库名，兼容旧版 ip:port:db_name 格式。"""
    database = source_text
    parsed_legacy_source = _parse_source_line(source_text) if ":" in source_text else None
    if parsed_legacy_source:
        database = str(parsed_legacy_source.get("database") or "").strip()
    if not database:
        _warn_parse_once("HTTP 成员数据源数据库名为空，忽略: %s", raw_text)
        return None
    return {
        "source_line": database,
        "host": (parsed_legacy_source or {}).get("host"),
        "port": (parsed_legacy_source or {}).get("port"),
        "database": database,
    }


def _parse_member_source_entries(
    member_sources_text: Any,
    template_source: Any,
    source_type: Any,
    query_executor_type: Any,
) -> List[Dict[str, Any]]:
    template_line = str(template_source or "").strip()
    seen: set[str] = set()
    parsed_entries: List[Dict[str, Any]] = []

    for raw_line in str(member_sources_text or "").splitlines():
        entry = _parse_member_source_entry(raw_line, source_type, query_executor_type)
        if not entry:
            continue
        member_identity = entry["datasource_id"]
        if member_identity in seen:
            continue
        seen.add(member_identity)
        parsed_entries.append(entry)

    template_entry = _resolve_template_entry(parsed_entries, template_line, seen, source_type, query_executor_type)
    result: List[Dict[str, Any]] = []
    if template_entry is not None:
        result.append(template_entry)
    result.extend(item for item in parsed_entries if item["datasource_id"] != (template_entry or {}).get("datasource_id"))
    return result


def _resolve_template_entry(
    parsed_entries: List[Dict[str, Any]],
    template_line: str,
    seen: set[str],
    source_type: Any,
    query_executor_type: Any,
) -> Optional[Dict[str, Any]]:
    """确定模板成员条目：优先匹配行文本，其次解析模板行，最后取首个条目。"""
    template_entry: Optional[Dict[str, Any]] = None
    if template_line:
        template_entry = next(
            (
                item for item in parsed_entries
                if item["raw_line"] == template_line or item["source_line"] == template_line
            ),
            None,
        )
        if template_entry is None:
            fallback_template_entry = _parse_member_source_entry(template_line, source_type, query_executor_type)
            if fallback_template_entry and fallback_template_entry["datasource_id"] not in seen:
                template_entry = fallback_template_entry
    if template_entry is None and parsed_entries:
        template_entry = parsed_entries[0]
    return template_entry


def _parse_member_sources_text(member_sources_text: Any, template_source: Any, source_type: Any, query_executor_type: Any) -> List[str]:
    return [
        item["datasource_id"]
        for item in _parse_member_source_entries(member_sources_text, template_source, source_type, query_executor_type)
    ]
