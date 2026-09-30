"""数据源物理元数据读取实现、适配器与注册表。"""

from __future__ import annotations

import asyncio
import os
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import aiomysql
from pyhive import hive

from utils.alarm import emit_external_api_alarm
from utils.logger import get_logger

from ..runtime_config import read_positive_int_env
from ..setting_service import get_runtime_setting
from .credentials import (
    HIVE_SOCKET_TIMEOUT_SECONDS,
    add_http_user_params,
    apply_hive_socket_timeout,
    build_hive_connect_params,
    build_http_auth_headers,
    get_http_auth_token,
    open_http_client,
    resolve_hive_credentials,
    resolve_mysql_credentials,
)

logger = get_logger(__name__)

MYSQL_CONNECT_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_DB_CONNECT_TIMEOUT_SECONDS", 3)
# Hive 表结构同步线程池并发：同步函数 _get_full_schema 的签名兜底默认值；异步入口
# get_full_schema_async 运行期经后台配置变量表读取（CHAT_DATA_HIVE_SCHEMA_MAX_WORKERS）。
HIVE_SCHEMA_MAX_WORKERS = read_positive_int_env("CHAT_DATA_HIVE_SCHEMA_MAX_WORKERS", 10)
# Module aliases keep the configuration names explicit for direct Hive callers.
SOCKET_TIMEOUT_SECONDS = HIVE_SOCKET_TIMEOUT_SECONDS
DEFAULT_MAX_WORKERS = HIVE_SCHEMA_MAX_WORKERS
HIVE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
META_TABLES_URL = str(os.getenv("CHAT_DATA_META_TABLES_URL") or "").strip()
META_SCHEMA_URL = str(os.getenv("CHAT_DATA_META_SCHEMA_URL") or "").strip()
# 元数据 HTTP 单次请求超时已迁移到后台配置变量表（CHAT_DATA_META_TIMEOUT_SECONDS，运行期读取）。
META_SCHEMA_MAX_CONCURRENCY = read_positive_int_env("CHAT_DATA_META_SCHEMA_MAX_CONCURRENCY", 10)
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

    async def get_tables_async(self) -> list[str]:
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
        table_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        conn = await self._get_connection()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                rows = await self._fetch_table_column_rows(cursor)
                tables_by_name = MySQLSyncService._group_rows_into_tables(rows, table_names)
                stat_rows = await self._fetch_index_stat_rows(cursor)
                stats_by_table = MySQLSyncService._group_index_stats(stat_rows, table_names)
                MySQLSyncService._apply_index_stats(tables_by_name, stats_by_table)
                return MySQLSyncService._select_requested_tables(tables_by_name, table_names)
        finally:
            conn.close()
            await conn.ensure_closed()

    async def _fetch_table_column_rows(self, cursor) -> list[dict[str, Any]]:
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
        return await cursor.fetchall()

    async def _fetch_index_stat_rows(self, cursor) -> list[dict[str, Any]]:
        await cursor.execute(
            """
            SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
            """,
            (self.database,),
        )
        return await cursor.fetchall()

    @staticmethod
    def _group_rows_into_tables(rows: list[dict[str, Any]], table_names: list[str] | None) -> dict[str, dict[str, Any]]:
        tables_by_name: dict[str, dict[str, Any]] = {}
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
                    "primary_keys": [],
                    "unique_indexes": [],
                    "indexes": [],
                },
            )
            if row["COLUMN_NAME"]:
                column_key = row["COLUMN_KEY"]
                table["columns"].append(
                    {
                        "name": row["COLUMN_NAME"],
                        "type": row["COLUMN_TYPE"],
                        "mysql_type": row["COLUMN_TYPE"],
                        "description": row["COLUMN_COMMENT"] or row["COLUMN_NAME"],
                        "primary_key": column_key == "PRI",
                        "unique_key": column_key == "UNI",
                        "index_key": column_key == "MUL",
                        "nullable": row["IS_NULLABLE"] == "YES",
                    }
                )
        return tables_by_name

    @staticmethod
    def _group_index_stats(
        stat_rows: list[dict[str, Any]], table_names: list[str] | None
    ) -> dict[str, list[dict[str, Any]]]:
        stats_by_table: dict[str, list[dict[str, Any]]] = {}
        for sr in stat_rows:
            table_name = sr.get("TABLE_NAME")
            if not table_name:
                continue
            if table_names and table_name not in table_names:
                continue
            stats_by_table.setdefault(table_name, []).append(
                {
                    "index_name": sr.get("INDEX_NAME"),
                    "non_unique": bool(sr.get("NON_UNIQUE")),
                    "seq": sr.get("SEQ_IN_INDEX") or 0,
                    "column_name": sr.get("COLUMN_NAME"),
                }
            )
        return stats_by_table

    @staticmethod
    def _build_index_groups(stats_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """将统计行按索引名分组，返回 {index_name: {non_unique, columns}}。"""
        by_index: dict[str, dict[str, Any]] = {}
        for s in stats_list:
            index_name = s["index_name"]
            if not index_name or not s["column_name"]:
                continue
            if index_name not in by_index:
                by_index[index_name] = {"non_unique": s["non_unique"], "columns": []}
            by_index[index_name]["columns"].append((s["seq"], s["column_name"]))
        return by_index

    @staticmethod
    def _classify_index_groups(by_index: dict[str, dict[str, Any]]):
        """把分组后的索引拆成主键 / 唯一索引 / 普通索引三类。"""
        primary_keys: list[list[str]] = []
        unique_indexes: list[dict[str, Any]] = []
        indexes: list[dict[str, Any]] = []
        for index_name, info in by_index.items():
            columns = [col for _, col in sorted(info["columns"])]
            if index_name == "PRIMARY":
                primary_keys.append(columns)
            elif not info["non_unique"]:
                unique_indexes.append({"name": index_name, "columns": columns})
            else:
                indexes.append({"name": index_name, "columns": columns})
        return primary_keys, unique_indexes, indexes

    @staticmethod
    def _apply_index_stats(
        tables_by_name: dict[str, dict[str, Any]], stats_by_table: dict[str, list[dict[str, Any]]]
    ) -> None:
        for table_name, stats_list in stats_by_table.items():
            table = tables_by_name.get(table_name)
            if table is None:
                continue
            by_index = MySQLSyncService._build_index_groups(stats_list)
            primary_keys, unique_indexes, indexes = MySQLSyncService._classify_index_groups(by_index)
            table["primary_keys"] = primary_keys
            table["unique_indexes"] = unique_indexes
            table["indexes"] = indexes

    @staticmethod
    def _select_requested_tables(
        tables_by_name: dict[str, dict[str, Any]], table_names: list[str] | None
    ) -> list[dict[str, Any]]:
        if table_names:
            return [tables_by_name[name] for name in table_names if name in tables_by_name]
        return list(tables_by_name.values())


class HiveSyncService:
    """Hive direct 元数据读取实现。"""

    def __init__(
        self,
        host: str,
        port: int = 10000,
        database: str = "default",
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password

    def _get_connection(self):
        conn = hive.connect(
            **build_hive_connect_params(
                host=self.host,
                port=self.port,
                database=self.database,
                username=self.username,
                password=self.password,
            )
        )
        apply_hive_socket_timeout(getattr(conn, "_transport", None), SOCKET_TIMEOUT_SECONDS)
        return conn

    def _get_tables(self, conn=None) -> list[str]:
        should_close = conn is None
        conn = conn or self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES")
            return [row[0] for row in cursor.fetchall()]
        finally:
            if should_close:
                conn.close()

    @staticmethod
    def _hive_section_marker(col_name: str) -> str | None:
        """识别 DESCRIBE FORMATTED 输出中的分段标记行，非标记行返回 None。"""
        if col_name == "# Partition Information":
            return "partition"
        if col_name == "# Detailed Table Information":
            return "details"
        if col_name == "# Storage Information":
            return "storage"
        return None

    @staticmethod
    def _extract_hive_row_fields(row) -> tuple[str, str, str]:
        col_name = str(row[0] or "").strip()
        col_type = str(row[1] if len(row) > 1 and row[1] else "").strip()
        col_comment = str(row[2] if len(row) > 2 and row[2] else "").strip()
        return col_name, col_type, col_comment

    @staticmethod
    def _append_hive_column(
        columns: list[dict[str, Any]],
        partition_columns: set[str],
        seen_columns: set[str],
        section: str,
        col_name: str,
        col_type: str,
        col_comment: str,
    ) -> None:
        is_partition = section == "partition"
        seen_columns.add(col_name)
        if is_partition:
            partition_columns.add(col_name)
        columns.append(
            {
                "name": col_name,
                "type": col_type,
                "hive_type": col_type,
                "description": col_comment or col_name,
                "primary_key": False,
                "nullable": True,
                "is_partition": is_partition,
            }
        )

    @staticmethod
    def _resolve_hive_table_comment(col_name: str, col_type: str, col_comment: str, table_comment: str) -> str:
        if not col_name and col_type.lower() == "comment":
            return col_comment
        if col_name.lower() == "comment":
            return col_type
        return table_comment

    @classmethod
    def _parse_hive_metadata_rows(cls, rows) -> dict[str, Any]:
        columns: list[dict[str, Any]] = []
        partition_columns: set[str] = set()
        table_comment = ""
        seen_columns: set[str] = set()
        section = "columns"
        for row in rows:
            col_name, col_type, col_comment = cls._extract_hive_row_fields(row)
            if not col_name and section != "details":
                continue
            marker = cls._hive_section_marker(col_name)
            if marker:
                section = marker
                continue
            if col_name.startswith("#"):
                continue
            if section in {"columns", "partition"} and col_name not in seen_columns:
                cls._append_hive_column(
                    columns, partition_columns, seen_columns, section, col_name, col_type, col_comment
                )
            elif section == "details":
                table_comment = cls._resolve_hive_table_comment(col_name, col_type, col_comment, table_comment)
        return {
            "comment": table_comment,
            "columns": columns,
            "partition_columns": partition_columns,
        }

    def _get_table_metadata(self, table_name: str, conn=None) -> dict[str, Any]:
        should_close = conn is None
        conn = conn or self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(f"DESCRIBE FORMATTED {_quote_hive_identifier(table_name)}")
            metadata = self._parse_hive_metadata_rows(cursor.fetchall())
            return metadata
        except Exception as exc:
            logger.error("获取表 %s 元数据失败: %s", table_name, exc)
            raise RuntimeError(f"获取表 {table_name} 元数据失败") from exc
        finally:
            if should_close:
                conn.close()

    def _get_full_schema(
        self,
        table_names: list[str] | None = None,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> list[dict[str, Any]]:
        selected_names = self._get_tables() if table_names is None else list(table_names)
        invalid_names = [name for name in selected_names if not _is_valid_hive_identifier(name)]
        if invalid_names:
            raise ValueError(f"存在非法表名: {', '.join(invalid_names)}")
        if not selected_names:
            return []

        def load_table(table_name: str) -> dict[str, Any]:
            conn = self._get_connection()
            try:
                metadata = self._get_table_metadata(table_name, conn)
                return {
                    "table_name": table_name,
                    "description": metadata.get("comment") or table_name,
                    "columns": metadata.get("columns", []),
                    "primary_keys": [],
                    "unique_indexes": [],
                    "indexes": [],
                }
            finally:
                conn.close()

        tables: list[dict[str, Any]] = []
        failed_tables: list[str] = []
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

    async def get_tables_async(self) -> list[str]:
        return await asyncio.to_thread(self._get_tables)

    async def get_full_schema_async(
        self,
        table_names: list[str] | None = None,
        max_workers: int | None = None,
    ) -> list[dict[str, Any]]:
        """异步读取完整表结构；max_workers=None 时运行期经配置服务解析并发上限。"""
        if max_workers is None:
            hive_schema_max_workers = await get_runtime_setting("CHAT_DATA_HIVE_SCHEMA_MAX_WORKERS")
            max_workers = hive_schema_max_workers
        return await asyncio.to_thread(self._get_full_schema, table_names, max_workers)


def validate_hive_meta_config() -> None:
    if not META_TABLES_URL or not META_SCHEMA_URL:
        raise ValueError("元数据 HTTP 模式需要配置 CHAT_DATA_META_TABLES_URL/CHAT_DATA_META_SCHEMA_URL")


def _build_headers() -> dict[str, str]:
    return build_http_auth_headers(HTTP_AUTH_TOKEN)


def _meta_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(payload or {})
    table_names = summary.get("table_names")
    if isinstance(table_names, list):
        summary["table_count"] = len(table_names)
        if len(table_names) > 10:
            summary["table_names"] = table_names[:10]
    return summary


def _meta_response_summary(payload: Any) -> dict[str, Any]:
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
    source_config: dict[str, Any],
    table_names: list[str] | None = None,
    user_id: str | None = None,
    username: str | None = None,
) -> dict[str, Any]:
    org_code = str(source_config.get("org_code") or "").strip()
    if not org_code:
        raise ValueError("元数据 HTTP 请求缺少 orgCode")
    payload: dict[str, Any] = {
        "source_type": "hive",
        "orgCode": org_code,
        "orgName": str(source_config.get("org_name") or "").strip(),
        "database": source_config.get("database") or "default",
    }
    if source_config.get("host"):
        payload["host"] = source_config["host"]
    if source_config.get("port") is not None:
        payload["port"] = int(source_config["port"])
    add_http_user_params(payload, user_id, username)
    if table_names is not None:
        payload["table_names"] = table_names
    return payload


def _extract_response_data(payload: dict[str, Any]) -> dict[str, Any]:
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
    tables: list[Any],
    table_descriptions: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    description_map = table_descriptions or {}
    validated_tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            raise RuntimeError(f"Hive 元数据表结构第 {table_index} 项不是对象")
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            raise RuntimeError(f"Hive 元数据表结构第 {table_index} 项缺少 table_name")
        columns = table.get("columns")
        if not isinstance(columns, list):
            raise RuntimeError(f"Hive 元数据表结构 {table_name} 缺少 columns")
        normalized_columns = [
            _normalize_hive_column(table_name, column, column_index)
            for column_index, column in enumerate(columns, start=1)
        ]
        validated_tables.append(
            {
                "table_name": table_name,
                "description": description_map.get(table_name) or table.get("description") or table_name,
                "columns": normalized_columns,
                "primary_keys": [],
                "unique_indexes": [],
                "indexes": [],
            }
        )
    return validated_tables


def _normalize_hive_column(table_name: str, column: Any, column_index: int) -> dict[str, Any]:
    """校验并标准化单个 Hive 字段描述。"""
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
    return {
        "name": column_name,
        "type": column_type,
        "hive_type": column_type,
        "description": str(column.get("description") or column_name).strip(),
        "primary_key": False,
        "nullable": True,
        "is_partition": is_partition,
    }


def _normalize_table_items(tables: list[Any]) -> list[dict[str, str]]:
    table_items: list[dict[str, str]] = []
    for table_index, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            raise RuntimeError(f"Hive 元数据表列表第 {table_index} 项不是对象")
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            raise RuntimeError(f"Hive 元数据表列表第 {table_index} 项缺少 table_name")
        table_items.append(
            {
                "table_name": table_name,
                "description": str(table.get("table_description") or "").strip(),
            }
        )
    return table_items


async def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    logger.info("[HiveMetaHTTP] 请求对方接口: url=%s payload=%s", url, _meta_payload_summary(payload))
    try:
        async with open_http_client(
            timeout_seconds=await get_runtime_setting("CHAT_DATA_META_TIMEOUT_SECONDS"),
            headers=_build_headers(),
        ) as client:
            response = await client.post(url, json=payload)
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
    source_config: dict[str, Any],
    user_id: str | None = None,
    username: str | None = None,
) -> list[str]:
    validate_hive_meta_config()
    data = await _post_json(
        META_TABLES_URL,
        _build_payload(source_config, user_id=user_id, username=username),
    )
    tables = data.get("tables")
    if not isinstance(tables, list):
        raise RuntimeError("Hive 元数据表列表接口返回缺少 data.tables")
    return [item["table_name"] for item in _normalize_table_items(tables)]


async def _fetch_hive_meta_table_index(
    source_config: dict[str, Any],
    user_id: str | None,
    username: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """请求表列表接口并归一化，返回 (table_items, 表名→描述 索引)。"""
    data = await _post_json(
        META_TABLES_URL,
        _build_payload(source_config, user_id=user_id, username=username),
    )
    tables = data.get("tables")
    if not isinstance(tables, list):
        raise RuntimeError("Hive 元数据表列表接口返回缺少 data.tables")
    table_items = _normalize_table_items(tables)
    descriptions = {item["table_name"]: item["description"] for item in table_items if item["description"]}
    return table_items, descriptions


def _normalize_selected_table_names(table_names: list[str] | None, table_items: list[dict[str, Any]]) -> list[str]:
    """按请求名单（缺省全量）归一化待加载表名，剔除空值。"""
    selected_names = [item["table_name"] for item in table_items] if table_names is None else table_names
    return [str(name).strip() for name in selected_names if str(name or "").strip()]


async def _load_hive_table_schemas(
    source_config: dict[str, Any],
    normalized_names: list[str],
    descriptions: dict[str, Any],
    user_id: str | None,
    username: str | None,
) -> list[dict[str, Any]]:
    """并发加载各表结构并按请求顺序排列；接口未返回的表抛 RuntimeError。"""
    semaphore = asyncio.Semaphore(META_SCHEMA_MAX_CONCURRENCY)

    async def load_table(table_name: str) -> list[dict[str, Any]]:
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


async def get_hive_meta_schema_async(
    source_config: dict[str, Any],
    table_names: list[str] | None = None,
    user_id: str | None = None,
    username: str | None = None,
) -> list[dict[str, Any]]:
    validate_hive_meta_config()
    table_items, descriptions = await _fetch_hive_meta_table_index(source_config, user_id, username)
    normalized_names = _normalize_selected_table_names(table_names, table_items)
    if not normalized_names:
        return []
    return await _load_hive_table_schemas(source_config, normalized_names, descriptions, user_id, username)


class DataSourceMetadataAdapter(ABC):
    """统一物理元数据读取能力。"""

    def __init__(
        self,
        source_config: dict[str, Any],
        *,
        user_id: str | None = None,
        username: str | None = None,
    ) -> None:
        self.source_config = dict(source_config or {})
        self.user_id = user_id
        self.username = username

    @abstractmethod
    async def list_tables(self) -> list[Any]:
        """读取物理表名列表。"""

    @abstractmethod
    async def load_schema(
        self,
        table_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """读取指定或全部物理表结构。"""


class MySQLMetadataAdapter(DataSourceMetadataAdapter):
    def __init__(self, source_config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(source_config, **kwargs)
        credentials = resolve_mysql_credentials(self.source_config)
        self._service = MySQLSyncService(
            host=self.source_config.get("host", "localhost"),
            port=self.source_config.get("port", 3306),
            database=self.source_config.get("database", ""),
            user=credentials["user"],
            password=credentials["password"],
        )

    async def list_tables(self) -> list[Any]:
        return await self._service.get_tables_async()

    async def load_schema(self, table_names: list[str] | None = None) -> list[dict[str, Any]]:
        return await self._service.get_full_schema_async(table_names)


class HiveDirectMetadataAdapter(DataSourceMetadataAdapter):
    def __init__(self, source_config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(source_config, **kwargs)
        credentials = resolve_hive_credentials(self.source_config)
        self._service = HiveSyncService(
            host=self.source_config.get("host", "localhost"),
            port=self.source_config.get("port", 10000),
            database=self.source_config.get("database", "default"),
            username=credentials.get("username"),
            password=credentials.get("password"),
        )

    async def list_tables(self) -> list[Any]:
        return await self._service.get_tables_async()

    async def load_schema(self, table_names: list[str] | None = None) -> list[dict[str, Any]]:
        return await self._service.get_full_schema_async(table_names)


class HiveHttpMetadataAdapter(DataSourceMetadataAdapter):
    async def list_tables(self) -> list[Any]:
        return await get_hive_meta_tables_async(
            self.source_config,
            user_id=self.user_id,
            username=self.username,
        )

    async def load_schema(self, table_names: list[str] | None = None) -> list[dict[str, Any]]:
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
        self._adapter_types: dict[tuple[str, str], type[DataSourceMetadataAdapter]] = {}

    @staticmethod
    def _key(source_type: str, access_type: str) -> tuple[str, str]:
        normalized_source_type = str(source_type or "").strip().lower()
        normalized_access_type = str(access_type or "direct").strip().lower() or "direct"
        return normalized_source_type, normalized_access_type

    def register(
        self,
        source_type: str,
        access_type: str,
        adapter_type: type[DataSourceMetadataAdapter],
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
        source_config: dict[str, Any],
        *,
        user_id: str | None = None,
        username: str | None = None,
    ) -> DataSourceMetadataAdapter:
        key = self._key(source_type, access_type)
        adapter_type = self._adapter_types.get(key)
        if adapter_type is None:
            raise MetadataAdapterNotFoundError(*key)
        return adapter_type(source_config, user_id=user_id, username=username)

    def supported_keys(self) -> list[tuple[str, str]]:
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
