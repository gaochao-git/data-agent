"""
数据源/语义配置数据库存储。

当前运行时以 datasource_group 作为唯一数据源入口：
- datasource_group: 组即数据源
- datasource_table: 数据源表结构缓存
- semantic_config: 数据源语义配置
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiomysql

from utils.crypto import decrypt_password
from utils.logger import get_logger

logger = get_logger(__name__)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_UNSET = object()
_TX_CONNECTION: ContextVar[Any] = ContextVar("chat_data_tx_connection", default=None)
CONNECT_TIMEOUT_SECONDS = 5


class _AsyncDBRunner:
    """在后台线程维护独立事件循环，供同步调用方执行 aiomysql 协程。"""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="chatdata-db-runner", daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()


_ASYNC_DB_RUNNER = _AsyncDBRunner()


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(f"缺少业务数据库配置: {name}")


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


def _build_source_line(host: Any, port: Any, database: Any) -> str | None:
    normalized_host = str(host or "").strip()
    normalized_database = str(database or "").strip()
    normalized_port = str(port or "").strip()
    if not normalized_host or not normalized_port or not normalized_database:
        return None
    return f"{normalized_host}:{normalized_port}:{normalized_database}"


def _build_member_source_text_line(source_config: Dict[str, Any] | None) -> str | None:
    source_line = _build_source_line(
        (source_config or {}).get("host"),
        (source_config or {}).get("port"),
        (source_config or {}).get("database"),
    )
    if not source_line:
        return None
    datasource_name = str((source_config or {}).get("datasource_name") or "").strip()
    if not datasource_name:
        return None
    query_executor_type = str((source_config or {}).get("query_executor_type") or "direct").strip().lower() or "direct"
    return f"{datasource_name}|{query_executor_type}|{source_line}"


def _build_member_identity(source_type: Any, query_executor_type: Any, datasource_name: Any, source_line: Any) -> str | None:
    normalized_query_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    normalized_source_line = str(source_line or "").strip()
    normalized_name = str(datasource_name or "").strip()
    if normalized_query_executor_type == "http":
        return normalized_name or None
    return normalized_source_line or None


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
        logger.warning("数据源格式无效，忽略: %s", text)
        return None
    host, port_text, database = parts
    host = host.strip()
    database = database.strip()
    if not host or not port_text.strip() or not database:
        return None
    try:
        port = int(port_text.strip())
    except ValueError:
        logger.warning("数据源端口无效，忽略: %s", text)
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

    parts = [part.strip() for part in text.split("|")]
    if len(parts) != 3:
        logger.warning("成员数据源格式无效，仅支持 名称|direct/http|ip:port:db_name: %s", text)
        return None
    datasource_name, executor_type, source_text = parts
    if not datasource_name:
        logger.warning("成员数据源名称为空，忽略: %s", text)
        return None
    if executor_type not in {"direct", "http"}:
        logger.warning("成员数据源执行方式无效，忽略: %s", text)
        return None
    normalized_query_executor_type = _normalize_query_executor_type_for_parse(source_type, executor_type)

    parsed = _parse_source_line(source_text)
    if not parsed:
        return None
    member_identity = _build_member_identity(source_type, normalized_query_executor_type, datasource_name, parsed.get("source_line"))
    if not member_identity:
        logger.warning("成员执行源缺少唯一标识，忽略: %s", text)
        return None
    parsed["datasource_id"] = member_identity
    parsed["datasource_name"] = datasource_name or None
    parsed["query_executor_type"] = normalized_query_executor_type
    parsed["raw_line"] = text
    return parsed


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

    result: List[Dict[str, Any]] = []
    if template_entry is not None:
        result.append(template_entry)
    result.extend(item for item in parsed_entries if item["datasource_id"] != (template_entry or {}).get("datasource_id"))
    return result


def _parse_member_sources_text(member_sources_text: Any, template_source: Any, source_type: Any, query_executor_type: Any) -> List[str]:
    return [
        item["datasource_id"]
        for item in _parse_member_source_entries(member_sources_text, template_source, source_type, query_executor_type)
    ]


class DataSourceDBStore:
    """基于 MySQL 的数据源/语义配置存储层。"""

    def __init__(self):
        self.host = _get_env("FXCJG_DB_HOST")
        self.port = int(_get_env("FXCJG_DB_PORT"))
        self.database = _get_env("FXCJG_DB")
        self.user = _get_env("FXCJG_DB_USER")
        self.password = decrypt_password(_get_env("FXCJG_DB_PASSWORD"))

    async def _connect(self, autocommit: bool = True):
        return await aiomysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            db=self.database,
            charset="utf8mb4",
            autocommit=autocommit,
            cursorclass=aiomysql.DictCursor,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
        )

    def _run_async(self, coro):
        return _ASYNC_DB_RUNNER.run(coro)

    async def _fetchall_async(self, conn, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        async with conn.cursor() as cursor:
            await cursor.execute(sql, params)
            return list(await cursor.fetchall() or [])

    async def _fetchone_async(self, conn, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        async with conn.cursor() as cursor:
            await cursor.execute(sql, params)
            return await cursor.fetchone()

    async def _execute_async(self, conn, sql: str, params: tuple = ()) -> int:
        async with conn.cursor() as cursor:
            affected = await cursor.execute(sql, params)
        if conn.get_autocommit():
            await conn.commit()
        return affected

    async def _close_async(self, conn) -> None:
        conn.close()

    @contextmanager
    def transaction(self):
        tx_conn = _TX_CONNECTION.get()
        if tx_conn is not None:
            yield tx_conn
            return

        conn = self._run_async(self._connect(autocommit=False))
        token = _TX_CONNECTION.set(conn)
        try:
            yield conn
            self._run_async(conn.commit())
        except Exception:
            self._run_async(conn.rollback())
            raise
        finally:
            _TX_CONNECTION.reset(token)
            self._run_async(self._close_async(conn))

    def _fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        tx_conn = _TX_CONNECTION.get()
        if tx_conn is not None:
            return self._run_async(self._fetchall_async(tx_conn, sql, params))
        conn = self._run_async(self._connect())
        try:
            return self._run_async(self._fetchall_async(conn, sql, params))
        finally:
            self._run_async(self._close_async(conn))

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        tx_conn = _TX_CONNECTION.get()
        if tx_conn is not None:
            return self._run_async(self._fetchone_async(tx_conn, sql, params))
        conn = self._run_async(self._connect())
        try:
            return self._run_async(self._fetchone_async(conn, sql, params))
        finally:
            self._run_async(self._close_async(conn))

    def _execute(self, sql: str, params: tuple = ()) -> int:
        tx_conn = _TX_CONNECTION.get()
        if tx_conn is not None:
            return self._run_async(self._execute_async(tx_conn, sql, params))
        conn = self._run_async(self._connect())
        try:
            return self._run_async(self._execute_async(conn, sql, params))
        finally:
            self._run_async(self._close_async(conn))

    def _group_select_sql(self) -> str:
        return """
            SELECT
                group_id,
                group_name,
                group_desc,
                source_type,
                template_source,
                member_sources_text,
                mapped_modules_json,
                is_default,
                create_time,
                update_time,
                create_by,
                update_by
            FROM datasource_group
            WHERE delete_flag = 0
        """

    def _map_group_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        group_id = row.get("group_id") or ""
        template_source = str(row.get("template_source") or "").strip()
        parsed_template = _parse_member_source_entry(
            template_source,
            row.get("source_type"),
            None,
        ) or {}
        if not parsed_template:
            parsed_template = _parse_source_line(template_source) or {}
        member_entries = _parse_member_source_entries(
            row.get("member_sources_text"),
            template_source,
            row.get("source_type"),
            None,
        )
        template_member_id = member_entries[0]["datasource_id"] if member_entries else template_source
        return {
            "id": group_id,
            "datasource_id": group_id,
            "group_id": group_id,
            "group_ids": [group_id] if group_id else [],
            "template_datasource_id": template_member_id,
            "template_source": template_source,
            "member_sources_text": row.get("member_sources_text") or "",
            "ds_name": row.get("group_name") or "",
            "ds_description": row.get("group_desc") or "",
            "source_type": row.get("source_type") or "",
            "query_executor_type": parsed_template.get("query_executor_type") or "direct",
            "create_time": _format_time(row.get("create_time")) or "",
            "update_time": _format_time(row.get("update_time")) or "",
            "create_by": row.get("create_by") or "",
            "update_by": row.get("update_by") or "",
            "ds_tables": [],
            "mapped_modules": _load_json(row.get("mapped_modules_json"), []),
            "is_default": bool(row.get("is_default", 0)),
            "source_config": {
                "host": parsed_template.get("host"),
                "port": parsed_template.get("port"),
                "database": parsed_template.get("database"),
            },
        }

    def _map_semantic_config_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row["semantic_config_id"],
            "semantic_config_id": row["semantic_config_id"],
            "group_id": row["group_id"],
            "name": row.get("semantic_config_name") or "",
            "description": row.get("semantic_config_desc") or "",
            "ontology_rules": row.get("ontology_rules"),
            "enabled": bool(row.get("enabled_flag", 1)),
            "metrics": _load_json(row.get("metrics_json"), []),
            "dimensions": _load_json(row.get("dimensions_json"), []),
            "relationships": _load_json(row.get("relationships_json"), []),
            "create_time": _format_time(row.get("create_time")) or "",
            "update_time": _format_time(row.get("update_time")) or "",
            "create_by": row.get("create_by") or "",
            "update_by": row.get("update_by") or "",
        }

    def _map_datasource_table_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "table_name": row.get("table_name") or "",
            "description": row.get("table_desc") or row.get("table_name") or "",
            "columns": _load_json(row.get("columns_json"), []),
        }

    def _build_member_sources(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        source_type = row.get("source_type") or ""
        query_executor_type = None
        template_source = row.get("template_source")
        group_name = row.get("group_name") or ""
        result: List[Dict[str, Any]] = []
        for parsed in _parse_member_source_entries(
            row.get("member_sources_text"),
            template_source,
            source_type,
            query_executor_type,
        ):
            source_line = parsed["source_line"]
            is_template_member = parsed.get("raw_line") == str(template_source or "").strip() or source_line == str(template_source or "").strip()
            label = parsed.get("datasource_name") or (group_name if is_template_member else source_line)
            member_query_executor_type = parsed.get("query_executor_type") or query_executor_type
            namespace = label if member_query_executor_type == "http" else None
            result.append({
                "member_datasource_id": parsed["datasource_id"],
                "datasource_name": label,
                "source_type": source_type,
                "query_executor_type": member_query_executor_type,
                "namespace": namespace,
                "host": parsed["host"],
                "port": parsed["port"],
                "database": parsed["database"],
            })
        return result

    def _get_group_row_by_id(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        return self._fetchone(
            f"""
            {self._group_select_sql()}
              AND group_id = %s
            LIMIT 1
            """,
            (datasource_id,),
        )

    def has_datasource(self, datasource_id: str) -> bool:
        if not datasource_id:
            return False
        row = self._fetchone(
            """
            SELECT 1
            FROM datasource_group
            WHERE group_id = %s AND delete_flag = 0
            LIMIT 1
            """,
            (datasource_id,),
        )
        return bool(row)

    def _get_group_row_by_name(self, datasource_name: str) -> Optional[Dict[str, Any]]:
        return self._fetchone(
            f"""
            {self._group_select_sql()}
              AND group_name = %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (datasource_name,),
        )

    def get_group_member_count(self, datasource_id: str) -> int:
        if not datasource_id:
            return 0
        rows = self._fetchall(
            """
            SELECT source_type, template_source, member_sources_text
            FROM datasource_group
            WHERE delete_flag = 0
            """
        )
        total = 0
        for row in rows:
            members = _parse_member_sources_text(
                row.get("member_sources_text"),
                row.get("template_source"),
                row.get("source_type"),
                None,
            )
            if datasource_id in members:
                total += 1
        return total

    def get_group_member_datasource_ids(self, group_id: str) -> List[str]:
        row = self._fetchone(
            """
            SELECT source_type, template_source, member_sources_text
            FROM datasource_group
            WHERE group_id = %s AND delete_flag = 0
            LIMIT 1
            """,
            (group_id,),
        )
        if not row:
            return []
        return _parse_member_sources_text(
            row.get("member_sources_text"),
            row.get("template_source"),
            row.get("source_type"),
            None,
        )

    def get_group_member_sources(self, group_id: str) -> List[Dict[str, Any]]:
        row = self._fetchone(
            f"""
            {self._group_select_sql()}
              AND group_id = %s
            LIMIT 1
            """,
            (group_id,),
        )
        if not row:
            return []
        return self._build_member_sources(row)

    def get_all_datasources(self, include_tables: bool = True) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            f"""
            {self._group_select_sql()}
            ORDER BY is_default DESC, id ASC
            """
        )
        tables_map: Dict[str, List[Dict[str, Any]]] = {}
        if include_tables:
            group_ids = [row.get("group_id") for row in rows if row.get("group_id")]
            tables_map = self._get_group_tables_map(group_ids)
        result = []
        for row in rows:
            datasource = self._map_group_row(row)
            group_id = datasource.get("group_id")
            datasource["ds_tables"] = tables_map.get(group_id, []) if include_tables else []
            datasource["member_datasource_ids"] = _parse_member_sources_text(
                row.get("member_sources_text"),
                row.get("template_source"),
                row.get("source_type"),
                None,
            )
            datasource["member_sources"] = self._build_member_sources(row)
            result.append(datasource)
        return result

    def _get_group_tables_map(self, group_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        if not group_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(group_ids))
        rows = self._fetchall(
            f"""
            SELECT group_id, table_names AS table_name, table_desc, columns_json
            FROM datasource_table
            WHERE group_id IN ({placeholders}) AND delete_flag = 0
            ORDER BY group_id ASC, sort_no ASC, id ASC
            """,
            tuple(group_ids),
        )
        result: Dict[str, List[Dict[str, Any]]] = {group_id: [] for group_id in group_ids}
        for row in rows:
            group_id = row.get("group_id")
            if not group_id:
                continue
            result.setdefault(group_id, []).append(self._map_datasource_table_row(row))
        return result

    def get_datasource_tables_by_group_id(self, group_id: str) -> List[Dict[str, Any]]:
        if not group_id:
            return []
        rows = self._fetchall(
            """
            SELECT table_names AS table_name, table_desc, columns_json
            FROM datasource_table
            WHERE group_id = %s AND delete_flag = 0
            ORDER BY sort_no ASC, id ASC
            """,
            (group_id,),
        )
        return [self._map_datasource_table_row(row) for row in rows]

    def get_datasource(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        row = self._get_group_row_by_id(datasource_id)
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["ds_tables"] = self.get_datasource_tables_by_group_id(datasource.get("group_id"))
        datasource["member_datasource_ids"] = self.get_group_member_datasource_ids(datasource.get("group_id"))
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    def get_datasource_by_name(self, datasource_name: str) -> Optional[Dict[str, Any]]:
        row = self._get_group_row_by_name(datasource_name)
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["ds_tables"] = self.get_datasource_tables_by_group_id(datasource.get("group_id"))
        datasource["member_datasource_ids"] = self.get_group_member_datasource_ids(datasource.get("group_id"))
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    def get_default_datasource(self) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            f"""
            {self._group_select_sql()}
              AND is_default = 1
            ORDER BY id ASC
            LIMIT 1
            """
        )
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["ds_tables"] = self.get_datasource_tables_by_group_id(datasource.get("group_id"))
        datasource["member_datasource_ids"] = self.get_group_member_datasource_ids(datasource.get("group_id"))
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    def get_semantic_configs(self, datasource_id: str) -> List[Dict[str, Any]]:
        row = self._get_group_row_by_id(datasource_id)
        if not row:
            return []
        return self.get_semantic_configs_by_group_id(row.get("group_id"))

    def get_semantic_configs_by_group_id(self, group_id: str) -> List[Dict[str, Any]]:
        if not group_id:
            return []
        rows = self._fetchall(
            """
            SELECT semantic_config_id, group_id, semantic_config_name, semantic_config_desc,
                   ontology_rules, enabled_flag, metrics_json, dimensions_json, relationships_json,
                   create_time, update_time, create_by, update_by
            FROM semantic_config
            WHERE group_id = %s AND delete_flag = 0
            ORDER BY sort_no ASC, id ASC
            """,
            (group_id,),
        )
        return [self._map_semantic_config_row(row) for row in rows]

    def get_semantic_config(self, datasource_id: str, config_id: str) -> Optional[Dict[str, Any]]:
        datasource = self.get_datasource(datasource_id)
        if not datasource:
            return None
        return self.get_semantic_config_by_group_id(datasource.get("group_id"), config_id)

    def get_semantic_config_by_group_id(self, group_id: str, config_id: str) -> Optional[Dict[str, Any]]:
        if not group_id:
            return None
        row = self._fetchone(
            """
            SELECT semantic_config_id, group_id, semantic_config_name, semantic_config_desc,
                   ontology_rules, enabled_flag, metrics_json, dimensions_json, relationships_json,
                   create_time, update_time, create_by, update_by
            FROM semantic_config
            WHERE group_id = %s AND semantic_config_id = %s AND delete_flag = 0
            LIMIT 1
            """,
            (group_id, config_id),
        )
        if not row:
            return None
        return self._map_semantic_config_row(row)

    def upsert_datasource_group(
        self,
        group_id: str,
        group_name: str,
        group_desc: Optional[str] = None,
        source_type: str = "",
        template_source: str = "",
        member_sources_text: Optional[str] = None,
        create_by: str = "",
        update_by: str = "",
    ) -> None:
        self._execute(
            """
            INSERT INTO datasource_group (
                group_id, group_name, group_desc, source_type, template_source,
                member_sources_text, create_by, update_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                group_name = VALUES(group_name),
                group_desc = VALUES(group_desc),
                source_type = VALUES(source_type),
                template_source = VALUES(template_source),
                member_sources_text = VALUES(member_sources_text),
                update_by = VALUES(update_by),
                delete_flag = 0
            """,
            (
                group_id,
                group_name,
                group_desc,
                source_type,
                template_source,
                member_sources_text or template_source,
                create_by,
                update_by,
            ),
        )

    def update_datasource_group(
        self,
        group_id: str,
        group_name: Any = _UNSET,
        group_desc: Any = _UNSET,
        source_type: Any = _UNSET,
        template_source: Any = _UNSET,
        member_sources_text: Any = _UNSET,
        mapped_modules: Any = _UNSET,
        is_default: Any = _UNSET,
        update_by: str = "",
    ) -> None:
        assignments: List[str] = []
        params: List[Any] = []

        if group_name is not _UNSET:
            assignments.append("group_name = %s")
            params.append(group_name)
        if group_desc is not _UNSET:
            assignments.append("group_desc = %s")
            params.append(group_desc)
        if source_type is not _UNSET:
            assignments.append("source_type = %s")
            params.append(source_type)
        if template_source is not _UNSET:
            assignments.append("template_source = %s")
            params.append(template_source)
        if member_sources_text is not _UNSET:
            assignments.append("member_sources_text = %s")
            params.append(member_sources_text)
        if mapped_modules is not _UNSET:
            assignments.append("mapped_modules_json = %s")
            params.append(json.dumps(mapped_modules or [], ensure_ascii=False))
        if is_default is not _UNSET:
            assignments.append("is_default = %s")
            params.append(1 if is_default else 0)

        assignments.append("update_by = %s")
        params.append(update_by or "")
        assignments.append("delete_flag = 0")

        self._execute(
            f"UPDATE datasource_group SET {', '.join(assignments)} WHERE group_id = %s",
            tuple(params + [group_id]),
        )

    def clear_all_default_datasources(self, update_by: str = "") -> None:
        self._execute(
            """
            UPDATE datasource_group
            SET is_default = 0, update_by = %s
            WHERE delete_flag = 0 AND is_default = 1
            """,
            (update_by or "",),
        )

    def replace_datasource_tables(
        self,
        group_id: str,
        tables: List[Dict[str, Any]],
        create_by: str = "",
        update_by: str = "",
    ) -> None:
        if not group_id:
            return

        table_list = tables or []
        active_table_names = [str((table or {}).get("table_name") or "").strip() for table in table_list]
        active_table_names = [name for name in active_table_names if name]

        if active_table_names:
            placeholders = ", ".join(["%s"] * len(active_table_names))
            self._execute(
                f"""
                UPDATE datasource_table
                SET delete_flag = 1
                WHERE group_id = %s AND table_names NOT IN ({placeholders})
                """,
                (group_id, *active_table_names),
            )
        else:
            self._execute(
                "UPDATE datasource_table SET delete_flag = 1 WHERE group_id = %s",
                (group_id,),
            )
            return

        for index, table in enumerate(table_list):
            table_name = str((table or {}).get("table_name") or "").strip()
            if not table_name:
                continue
            self._execute(
                """
                INSERT INTO datasource_table (
                    group_id, table_names, table_desc, columns_json,
                    sort_no, create_by, update_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    table_desc = VALUES(table_desc),
                    columns_json = VALUES(columns_json),
                    sort_no = VALUES(sort_no),
                    update_by = VALUES(update_by),
                    delete_flag = 0
                """,
                (
                    group_id,
                    table_name,
                    table.get("description"),
                    json.dumps(table.get("columns") or [], ensure_ascii=False),
                    index,
                    create_by,
                    update_by,
                ),
            )

    def soft_delete_datasource(self, datasource_id: str) -> None:
        if not datasource_id:
            return
        rows = self._fetchall(
            """
            SELECT group_id, source_type, template_source, member_sources_text
            FROM datasource_group
            WHERE delete_flag = 0
            """
        )
        for row in rows:
            group_id = row.get("group_id")
            members = _parse_member_source_entries(
                row.get("member_sources_text"),
                row.get("template_source"),
                row.get("source_type"),
                None,
            )
            if datasource_id not in [item.get("datasource_id") for item in members]:
                continue
            remaining = [item for item in members if item.get("datasource_id") != datasource_id]
            next_template = ""
            if remaining:
                next_template = str(remaining[0].get("raw_line") or remaining[0].get("source_line") or "").strip()
            self.update_datasource_group(
                group_id=group_id,
                template_source=next_template,
                member_sources_text="\n".join(item.get("raw_line") or "" for item in remaining if item.get("raw_line")),
                update_by="",
            )

    def soft_delete_group(self, group_id: str) -> None:
        self._execute(
            "UPDATE datasource_group SET delete_flag = 1, is_default = 0 WHERE group_id = %s",
            (group_id,),
        )
        self._execute(
            "UPDATE semantic_config SET delete_flag = 1 WHERE group_id = %s",
            (group_id,),
        )
        self._execute(
            "UPDATE datasource_table SET delete_flag = 1 WHERE group_id = %s",
            (group_id,),
        )

    def upsert_semantic_config(self, datasource_id: str, config: Dict[str, Any]) -> bool:
        datasource = self.get_datasource(datasource_id)
        group_id = config.get("group_id") or (datasource or {}).get("group_id")
        if not group_id:
            logger.warning("数据源不存在或缺少 group_id，无法写入语义配置: datasource_id=%s", datasource_id)
            return False

        self._execute(
            """
            INSERT INTO semantic_config (
                semantic_config_id, group_id, semantic_config_name, semantic_config_desc,
                ontology_rules, enabled_flag, metrics_json, dimensions_json, relationships_json,
                sort_no, create_by, update_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                group_id = VALUES(group_id),
                semantic_config_name = VALUES(semantic_config_name),
                semantic_config_desc = VALUES(semantic_config_desc),
                ontology_rules = VALUES(ontology_rules),
                enabled_flag = VALUES(enabled_flag),
                metrics_json = VALUES(metrics_json),
                dimensions_json = VALUES(dimensions_json),
                relationships_json = VALUES(relationships_json),
                sort_no = VALUES(sort_no),
                update_by = VALUES(update_by),
                delete_flag = 0
            """,
            (
                config.get("id") or config.get("semantic_config_id"),
                group_id,
                config.get("name") or config.get("semantic_config_name") or "",
                config.get("description") or config.get("semantic_config_desc"),
                config.get("ontology_rules"),
                1 if config.get("enabled", True) else 0,
                json.dumps(config.get("metrics") or [], ensure_ascii=False),
                json.dumps(config.get("dimensions") or [], ensure_ascii=False),
                json.dumps(config.get("relationships") or [], ensure_ascii=False),
                int(config.get("sort_no") or 0),
                config.get("create_by") or "",
                config.get("update_by") or "",
            ),
        )
        return True

    def soft_delete_semantic_config(self, semantic_config_id: str) -> None:
        self._execute(
            "UPDATE semantic_config SET delete_flag = 1 WHERE semantic_config_id = %s",
            (semantic_config_id,),
        )
