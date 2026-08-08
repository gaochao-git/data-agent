"""数据源物理元数据读取实现、适配器与注册表。"""

from __future__ import annotations

import asyncio
import os
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Type

import aiomysql
import httpx
from pyhive import hive

from utils.alarm import emit_external_api_alarm
from utils.logger import get_logger
from .datasource_credentials import (
    apply_hive_socket_timeout,
    build_hive_connect_params,
    get_http_auth_token,
    HIVE_SOCKET_TIMEOUT_SECONDS,
    resolve_hive_credentials,
    resolve_mysql_credentials,
)
from .runtime_config import read_bool_env, read_positive_int_env

logger = get_logger(__name__)

MYSQL_CONNECT_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_DB_CONNECT_TIMEOUT_SECONDS", 3)
HIVE_SCHEMA_MAX_WORKERS = read_positive_int_env("CHAT_DATA_HIVE_SCHEMA_MAX_WORKERS", 10)
# Module aliases keep the configuration names explicit for direct Hive callers.
SOCKET_TIMEOUT_SECONDS = HIVE_SOCKET_TIMEOUT_SECONDS
DEFAULT_MAX_WORKERS = HIVE_SCHEMA_MAX_WORKERS
HIVE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
META_TABLES_URL = str(os.getenv("CHAT_DATA_META_TABLES_URL") or "").strip()
META_SCHEMA_URL = str(os.getenv("CHAT_DATA_META_SCHEMA_URL") or "").strip()
META_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_META_TIMEOUT_SECONDS", 30)
META_SCHEMA_MAX_CONCURRENCY = read_positive_int_env("CHAT_DATA_META_SCHEMA_MAX_CONCURRENCY", 10)
HTTP_VERIFY_TLS = read_bool_env("CHAT_DATA_HTTP_VERIFY_TLS", False)
HTTP_AUTH_TOKEN = get_http_auth_token()


def _read_positive_int_env(name: str, default: int) -> int:
    """保留模块内测试入口，实际读取逻辑统一由 runtime_config 提供。"""
    return read_positive_int_env(name, default)


def _is_valid_hive_identifier(name: str) -> bool:
    if not name:
        return False
    parts = name.split(".")
    return len(parts) <= 2 and all(HIVE_IDENTIFIER_PATTERN.match(part) for part in parts)


def _quote_hive_identifier(name: str) -> str:
    if not _is_valid_hive_identifier(name):
        raise ValueError(f"非法表名: {name}")
    return ".".join(f"`{part}`" for part in name.split("."))


class MySQLSyncService:
    """MySQL 异步元数据读取实现。"""

    def __init__(
        self,
        host: str,
        port: int = 3306,
        database: str = "",
        user: str = "",
        password: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password

    async def _get_connection(self):
        return await aiomysql.connect(
            host=self.host,
            port=self.port,
            db=self.database,
            user=self.user,
            password=self.password,
            charset="utf8mb4",
            connect_timeout=MYSQL_CONNECT_TIMEOUT_SECONDS,
        )

    async def get_tables_async(self) -> List[str]:
        conn = await self._get_connection()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SHOW TABLES")
                rows = await cursor.fetchall()
                return [list(row.values())[0] for row in rows]
        finally:
            conn.close()
            await conn.ensure_closed()

    async def get_full_schema_async(
        self,
        table_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        conn = await self._get_connection()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT
                        t.TABLE_NAME,
                        t.TABLE_COMMENT,
                        c.COLUMN_NAME,
                        c.COLUMN_TYPE,
                        c.COLUMN_COMMENT,
                        c.COLUMN_KEY,
                        c.IS_NULLABLE
                    FROM information_schema.TABLES t
                    LEFT JOIN information_schema.COLUMNS c
                        ON t.TABLE_SCHEMA = c.TABLE_SCHEMA
                        AND t.TABLE_NAME = c.TABLE_NAME
                    WHERE t.TABLE_SCHEMA = %s
                        AND t.TABLE_TYPE = 'BASE TABLE'
                    ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION
                    """,
                    (self.database,),
                )
                rows = await cursor.fetchall()
                tables_by_name: Dict[str, Dict[str, Any]] = {}
                for row in rows:
                    table_name = row["TABLE_NAME"]
                    if table_names and table_name not in table_names:
                        continue
                    table = tables_by_name.setdefault(
                        table_name,
                        {
                            "table_name": table_name,
                            "description": row["TABLE_COMMENT"] or table_name,
                            "columns": [],
                        },
                    )
                    if row["COLUMN_NAME"]:
                        column_key = row["COLUMN_KEY"]
                        table["columns"].append({
                            "name": row["COLUMN_NAME"],
                            "type": row["COLUMN_TYPE"],
                            "mysql_type": row["COLUMN_TYPE"],
                            "description": row["COLUMN_COMMENT"] or row["COLUMN_NAME"],
                            "primary_key": column_key == "PRI",
                            "unique_key": column_key == "UNI",
                            "index_key": column_key == "MUL",
                            "nullable": row["IS_NULLABLE"] == "YES",
                        })
                if table_names:
                    return [tables_by_name[name] for name in table_names if name in tables_by_name]
                return list(tables_by_name.values())
        finally:
            conn.close()
            await conn.ensure_closed()


class HiveSyncService:
    """Hive direct 元数据读取实现。"""

    def __init__(
        self,
        host: str,
        port: int = 10000,
        database: str = "default",
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password

    def _get_connection(self):
        conn = hive.connect(**build_hive_connect_params(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
        ))
        apply_hive_socket_timeout(getattr(conn, "_transport", None), SOCKET_TIMEOUT_SECONDS)
        return conn

    def _get_tables(self, conn=None) -> List[str]:
        should_close = conn is None
        conn = conn or self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES")
            return [row[0] for row in cursor.fetchall()]
        finally:
            if should_close:
                conn.close()

    def _get_table_metadata(self, table_name: str, conn=None) -> Dict[str, Any]:
        should_close = conn is None
        conn = conn or self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"DESCRIBE FORMATTED {_quote_hive_identifier(table_name)}")
            columns: List[Dict[str, Any]] = []
            partition_columns: set[str] = set()
            table_comment = ""
            seen_columns: set[str] = set()
            section = "columns"
            for row in cursor.fetchall():
                col_name = str(row[0] or "").strip()
                col_type = str(row[1] if len(row) > 1 and row[1] else "").strip()
                col_comment = str(row[2] if len(row) > 2 and row[2] else "").strip()
                if not col_name and section != "details":
                    continue
                if col_name == "# Partition Information":
                    section = "partition"
                    continue
                if col_name == "# Detailed Table Information":
                    section = "details"
                    continue
                if col_name == "# Storage Information":
                    section = "storage"
                    continue
                if col_name.startswith("#"):
                    continue
                if section in {"columns", "partition"} and col_name not in seen_columns:
                    is_partition = section == "partition"
                    seen_columns.add(col_name)
                    if is_partition:
                        partition_columns.add(col_name)
                    columns.append({
                        "name": col_name,
                        "type": col_type,
                        "hive_type": col_type,
                        "description": col_comment or col_name,
                        "primary_key": False,
                        "nullable": True,
                        "is_partition": is_partition,
                    })
                elif section == "details":
                    if not col_name and col_type.lower() == "comment":
                        table_comment = col_comment
                    elif col_name.lower() == "comment":
                        table_comment = col_type
            return {
                "comment": table_comment,
                "columns": columns,
                "partition_columns": partition_columns,
            }
        except Exception as exc:
            logger.error("获取表 %s 元数据失败: %s", table_name, exc)
            raise RuntimeError(f"获取表 {table_name} 元数据失败") from exc
        finally:
            if should_close:
                conn.close()

    def _get_full_schema(
        self,
        table_names: Optional[List[str]] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> List[Dict[str, Any]]:
        selected_names = self._get_tables() if table_names is None else list(table_names)
        invalid_names = [name for name in selected_names if not _is_valid_hive_identifier(name)]
        if invalid_names:
            raise ValueError(f"存在非法表名: {', '.join(invalid_names)}")
        if not selected_names:
            return []

        def load_table(table_name: str) -> Dict[str, Any]:
            conn = self._get_connection()
            try:
                metadata = self._get_table_metadata(table_name, conn)
                return {
                    "table_name": table_name,
                    "description": metadata.get("comment") or table_name,
                    "columns": metadata.get("columns", []),
                }
            finally:
                conn.close()

        tables: List[Dict[str, Any]] = []
        failed_tables: List[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(load_table, name): name for name in selected_names}
            for future in as_completed(futures):
                try:
                    tables.append(future.result())
                except Exception as exc:
                    table_name = futures[future]
                    logger.error("获取表 %s 结构失败: %s", table_name, exc)
                    failed_tables.append(table_name)
        if failed_tables:
            raise RuntimeError(f"Hive 表结构同步失败，失败表: {', '.join(failed_tables)}")
        table_order = {name: index for index, name in enumerate(selected_names)}
        tables.sort(key=lambda item: table_order.get(item["table_name"], len(table_order)))
        return tables

    async def get_tables_async(self) -> List[str]:
        return await asyncio.to_thread(self._get_tables)

    async def get_full_schema_async(
        self,
        table_names: Optional[List[str]] = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._get_full_schema, table_names, max_workers)


def validate_hive_meta_config() -> None:
    if not META_TABLES_URL or not META_SCHEMA_URL:
        raise ValueError("元数据 HTTP 模式需要配置 CHAT_DATA_META_TABLES_URL/CHAT_DATA_META_SCHEMA_URL")


def _build_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if HTTP_AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {HTTP_AUTH_TOKEN}"
    return headers


def _add_user_payload(
    payload: Dict[str, Any],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_user_id = str(user_id or "").strip()
    normalized_username = str(username or "").strip()
    if normalized_user_id:
        payload["userId"] = normalized_user_id
    if normalized_username:
        payload["username"] = normalized_username
    return payload


def _meta_payload_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = dict(payload or {})
    table_names = summary.get("table_names")
    if isinstance(table_names, list):
        summary["table_count"] = len(table_names)
        if len(table_names) > 10:
            summary["table_names"] = table_names[:10]
    return summary


def _meta_response_summary(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload_type": type(payload).__name__}
    data = payload.get("data")
    result = data if isinstance(data, dict) else payload
    tables = result.get("tables")
    summary = {"code": payload.get("code"), "msg": payload.get("msg") or payload.get("message")}
    if isinstance(tables, list):
        summary["table_count"] = len(tables)
    return {key: value for key, value in summary.items() if value is not None}


def _build_payload(
    source_config: Dict[str, Any],
    table_names: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    org_code = str(source_config.get("org_code") or "").strip()
    if not org_code:
        raise ValueError("元数据 HTTP 请求缺少 orgCode")
    payload: Dict[str, Any] = {
        "source_type": "hive",
        "orgCode": org_code,
        "orgName": str(source_config.get("org_name") or "").strip(),
        "database": source_config.get("database") or "default",
    }
    if source_config.get("host"):
        payload["host"] = source_config["host"]
    if source_config.get("port") is not None:
        payload["port"] = int(source_config["port"])
    _add_user_payload(payload, user_id, username)
    if table_names is not None:
        payload["table_names"] = table_names
    return payload


def _extract_response_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    code = payload.get("code")
    try:
        normalized_code = int(code) if code is not None else None
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Hive 元数据接口返回非法 code: {code}") from exc
    if normalized_code is not None and normalized_code != 20000:
        raise RuntimeError(str(payload.get("msg") or "Hive 元数据接口返回失败"))
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _normalize_schema_tables(
    tables: List[Any],
    table_descriptions: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
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
            if "is_partition" not in column:
                raise RuntimeError(f"Hive 元数据表结构 {table_name}.{column_name} 缺少 is_partition")
            is_partition = column.get("is_partition")
            if not isinstance(is_partition, bool):
                raise RuntimeError(f"Hive 元数据表结构 {table_name}.{column_name} 的 is_partition 必须是布尔值")
            normalized_columns.append({
                "name": column_name,
                "type": column_type,
                "hive_type": column_type,
                "description": str(column.get("description") or column_name).strip(),
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
    table_items: List[Dict[str, str]] = []
    for table_index, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            raise RuntimeError(f"Hive 元数据表列表第 {table_index} 项不是对象")
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            raise RuntimeError(f"Hive 元数据表列表第 {table_index} 项缺少 table_name")
        table_items.append({
            "table_name": table_name,
            "description": str(table.get("table_description") or "").strip(),
        })
    return table_items


async def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("[HiveMetaHTTP] 请求对方接口: url=%s payload=%s", url, _meta_payload_summary(payload))
    try:
        async with httpx.AsyncClient(verify=HTTP_VERIFY_TLS, timeout=META_TIMEOUT_SECONDS) as client:
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


async def get_hive_meta_tables_async(
    source_config: Dict[str, Any],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> List[str]:
    validate_hive_meta_config()
    data = await _post_json(
        META_TABLES_URL,
        _build_payload(source_config, user_id=user_id, username=username),
    )
    tables = data.get("tables")
    if not isinstance(tables, list):
        raise RuntimeError("Hive 元数据表列表接口返回缺少 data.tables")
    return [item["table_name"] for item in _normalize_table_items(tables)]


async def get_hive_meta_schema_async(
    source_config: Dict[str, Any],
    table_names: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> List[Dict[str, Any]]:
    validate_hive_meta_config()
    data = await _post_json(
        META_TABLES_URL,
        _build_payload(source_config, user_id=user_id, username=username),
    )
    tables = data.get("tables")
    if not isinstance(tables, list):
        raise RuntimeError("Hive 元数据表列表接口返回缺少 data.tables")
    table_items = _normalize_table_items(tables)
    descriptions = {item["table_name"]: item["description"] for item in table_items if item["description"]}
    selected_names = [item["table_name"] for item in table_items] if table_names is None else table_names
    normalized_names = [str(name).strip() for name in selected_names if str(name or "").strip()]
    if not normalized_names:
        return []
    semaphore = asyncio.Semaphore(META_SCHEMA_MAX_CONCURRENCY)

    async def load_table(table_name: str) -> List[Dict[str, Any]]:
        async with semaphore:
            table_data = await _post_json(
                META_SCHEMA_URL,
                _build_payload(source_config, [table_name], user_id=user_id, username=username),
            )
            schema_tables = table_data.get("tables")
            if not isinstance(schema_tables, list):
                raise RuntimeError(f"Hive 元数据表结构接口返回缺少 data.tables: {table_name}")
            return _normalize_schema_tables(schema_tables, descriptions)

    loaded = await asyncio.gather(*(load_table(name) for name in normalized_names))
    table_by_name = {table["table_name"]: table for items in loaded for table in items}
    missing_tables = [name for name in normalized_names if name not in table_by_name]
    if missing_tables:
        raise RuntimeError(f"Hive 元数据接口未返回表结构: {', '.join(missing_tables)}")
    return [table_by_name[name] for name in normalized_names]


class DataSourceMetadataAdapter(ABC):
    """统一物理元数据读取能力。"""

    def __init__(
        self,
        source_config: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> None:
        self.source_config = dict(source_config or {})
        self.user_id = user_id
        self.username = username

    @abstractmethod
    async def list_tables(self) -> List[Any]:
        """读取物理表名列表。"""

    @abstractmethod
    async def load_schema(
        self,
        table_names: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """读取指定或全部物理表结构。"""


class MySQLMetadataAdapter(DataSourceMetadataAdapter):
    def __init__(self, source_config: Dict[str, Any], **kwargs: Any) -> None:
        super().__init__(source_config, **kwargs)
        credentials = resolve_mysql_credentials(self.source_config)
        self._service = MySQLSyncService(
            host=self.source_config.get("host", "localhost"),
            port=self.source_config.get("port", 3306),
            database=self.source_config.get("database", ""),
            user=credentials["user"],
            password=credentials["password"],
        )

    async def list_tables(self) -> List[Any]:
        return await self._service.get_tables_async()

    async def load_schema(self, table_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return await self._service.get_full_schema_async(table_names)


class HiveDirectMetadataAdapter(DataSourceMetadataAdapter):
    def __init__(self, source_config: Dict[str, Any], **kwargs: Any) -> None:
        super().__init__(source_config, **kwargs)
        credentials = resolve_hive_credentials(self.source_config)
        self._service = HiveSyncService(
            host=self.source_config.get("host", "localhost"),
            port=self.source_config.get("port", 10000),
            database=self.source_config.get("database", "default"),
            username=credentials.get("username"),
            password=credentials.get("password"),
        )

    async def list_tables(self) -> List[Any]:
        return await self._service.get_tables_async()

    async def load_schema(self, table_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return await self._service.get_full_schema_async(table_names)


class HiveHttpMetadataAdapter(DataSourceMetadataAdapter):
    async def list_tables(self) -> List[Any]:
        return await get_hive_meta_tables_async(
            self.source_config,
            user_id=self.user_id,
            username=self.username,
        )

    async def load_schema(self, table_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return await get_hive_meta_schema_async(
            self.source_config,
            table_names,
            user_id=self.user_id,
            username=self.username,
        )


class MetadataAdapterNotFoundError(ValueError):
    def __init__(self, source_type: str, access_type: str) -> None:
        self.source_type = source_type
        self.access_type = access_type
        super().__init__(f"未注册元数据适配器: {source_type}/{access_type}")


class DataSourceMetadataAdapterRegistry:
    """仅由服务端代码维护的元数据适配器注册表。"""

    def __init__(self) -> None:
        self._adapter_types: Dict[tuple[str, str], Type[DataSourceMetadataAdapter]] = {}

    @staticmethod
    def _key(source_type: str, access_type: str) -> tuple[str, str]:
        normalized_source_type = str(source_type or "").strip().lower()
        normalized_access_type = str(access_type or "direct").strip().lower() or "direct"
        return normalized_source_type, normalized_access_type

    def register(
        self,
        source_type: str,
        access_type: str,
        adapter_type: Type[DataSourceMetadataAdapter],
    ) -> None:
        key = self._key(source_type, access_type)
        if key in self._adapter_types:
            raise ValueError(f"元数据适配器已注册: {key[0]}/{key[1]}")
        if not issubclass(adapter_type, DataSourceMetadataAdapter):
            raise TypeError("元数据适配器必须继承 DataSourceMetadataAdapter")
        self._adapter_types[key] = adapter_type

    def create(
        self,
        source_type: str,
        access_type: str,
        source_config: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> DataSourceMetadataAdapter:
        key = self._key(source_type, access_type)
        adapter_type = self._adapter_types.get(key)
        if adapter_type is None:
            raise MetadataAdapterNotFoundError(*key)
        return adapter_type(source_config, user_id=user_id, username=username)

    def supported_keys(self) -> List[tuple[str, str]]:
        return sorted(self._adapter_types)


def _build_default_registry() -> DataSourceMetadataAdapterRegistry:
    registry = DataSourceMetadataAdapterRegistry()
    registry.register("mysql", "direct", MySQLMetadataAdapter)
    registry.register("hive", "direct", HiveDirectMetadataAdapter)
    registry.register("hive", "http", HiveHttpMetadataAdapter)
    return registry


_metadata_adapter_registry = _build_default_registry()


def get_metadata_adapter_registry() -> DataSourceMetadataAdapterRegistry:
    return _metadata_adapter_registry
