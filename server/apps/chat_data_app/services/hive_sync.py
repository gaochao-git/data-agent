"""
Hive 表结构同步服务

从 Hive 数据库同步表结构到数据源
"""

import asyncio
import os
import re
from typing import List, Dict, Any, Optional
from pyhive import hive
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.logger import get_logger
from .datasource_credentials import build_hive_connect_params

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


SOCKET_TIMEOUT_SECONDS = _read_positive_int_env("CHAT_DATA_HIVE_SOCKET_TIMEOUT_SECONDS", 10)
DEFAULT_MAX_WORKERS = _read_positive_int_env("CHAT_DATA_HIVE_SCHEMA_MAX_WORKERS", 10)
HIVE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_valid_hive_identifier(name: str) -> bool:
    """校验 Hive 标识符（支持 table 或 db.table）"""
    if not name:
        return False
    parts = name.split(".")
    if len(parts) > 2:
        return False
    return all(HIVE_IDENTIFIER_PATTERN.match(part) for part in parts)


def _quote_hive_identifier(name: str) -> str:
    """将合法 Hive 标识符转为安全引用格式（`db`.`table`）"""
    if not _is_valid_hive_identifier(name):
        raise ValueError(f"非法表名: {name}")
    parts = name.split(".")
    return ".".join(f"`{part}`" for part in parts)


def _apply_socket_timeout(transport: Any, timeout_seconds: int) -> None:
    """尽量将超时设置到 thrift 底层 socket。"""
    if timeout_seconds <= 0 or transport is None:
        return

    visited = set()
    stack = [transport]
    timeout_ms = int(timeout_seconds * 1000)

    while stack:
        current = stack.pop()
        if current is None:
            continue
        obj_id = id(current)
        if obj_id in visited:
            continue
        visited.add(obj_id)

        set_timeout = getattr(current, "setTimeout", None)
        if callable(set_timeout):
            try:
                set_timeout(timeout_ms)
            except Exception as err:
                logger.warning("设置 Hive transport 超时失败: %s", err)

        handle = getattr(current, "handle", None)
        if handle is not None and hasattr(handle, "settimeout"):
            try:
                handle.settimeout(timeout_seconds)
            except Exception as err:
                logger.warning("设置 Hive socket 超时失败: %s", err)

        for attr_name in ("_trans", "_transport", "_sock", "sock", "_TSaslClientTransport__trans"):
            nested = getattr(current, attr_name, None)
            if nested is not None:
                stack.append(nested)


class HiveSyncService:
    """Hive 同步服务"""

    def __init__(
        self,
        host: str,
        port: int = 10000,
        database: str = "default",
        username: str = None,
        password: str = None
    ):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password

    def _get_connection(self):
        """获取 Hive 连接"""
        conn_params = build_hive_connect_params(
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
        )
        conn = hive.connect(**conn_params)
        _apply_socket_timeout(getattr(conn, "_transport", None), SOCKET_TIMEOUT_SECONDS)
        return conn

    def _get_tables(self, conn=None) -> List[str]:
        """获取数据库中所有表名（内部同步方法）"""
        should_close = conn is None
        if conn is None:
            conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]
            return tables
        finally:
            if should_close:
                conn.close()

    def _get_table_metadata(self, table_name: str, conn=None) -> Dict[str, Any]:
        """
        获取表的元数据（字段、分区、注释）

        只执行一次 DESCRIBE FORMATTED，解析出所有信息

        Returns:
            {
                "comment": "表注释",
                "columns": [...],
                "partition_columns": set()
            }
        """
        should_close = conn is None
        if conn is None:
            conn = self._get_connection()

        try:
            cursor = conn.cursor()
            safe_table_name = _quote_hive_identifier(table_name)
            cursor.execute(f"DESCRIBE FORMATTED {safe_table_name}")

            columns = []
            partition_cols = set()
            table_comment = ""
            seen_columns = set()

            # 解析状态
            section = "columns"  # columns -> partition -> details -> storage

            for row in cursor.fetchall():
                col_name = row[0] if row[0] else ""
                col_type = row[1] if len(row) > 1 and row[1] else ""
                col_comment = row[2] if len(row) > 2 and row[2] else ""

                col_name_stripped = col_name.strip()
                col_type_stripped = col_type.strip() if col_type else ""

                # 在 details 段，需要处理 col_name 为空但 data_type 有值的行（如表注释）
                # 其他段落跳过空行
                if not col_name_stripped and section != "details":
                    continue

                # 检测段落切换
                if col_name_stripped == "# Partition Information":
                    section = "partition"
                    continue
                elif col_name_stripped == "# Detailed Table Information":
                    section = "details"
                    continue
                elif col_name_stripped == "# Storage Information":
                    section = "storage"
                    continue
                elif col_name_stripped.startswith("#"):
                    # 跳过其他 # 开头的行（如 # col_name）
                    continue

                # 根据当前段落处理
                if section == "columns":
                    # 普通字段
                    if col_name_stripped not in seen_columns:
                        seen_columns.add(col_name_stripped)
                        columns.append({
                            "name": col_name_stripped,
                            "type": col_type_stripped,
                            "hive_type": col_type_stripped,
                            "description": col_comment.strip() if col_comment else col_name_stripped,
                            "primary_key": False,
                            "nullable": True,
                            "is_partition": False
                        })

                elif section == "partition":
                    # 分区字段
                    if col_name_stripped and col_name_stripped not in seen_columns:
                        partition_cols.add(col_name_stripped)
                        seen_columns.add(col_name_stripped)
                        columns.append({
                            "name": col_name_stripped,
                            "type": col_type_stripped,
                            "hive_type": col_type_stripped,
                            "description": col_comment.strip() if col_comment else col_name_stripped,
                            "primary_key": False,
                            "nullable": True,
                            "is_partition": True
                        })

                elif section == "details":
                    # 提取表注释：col_name 为空，data_type 为 "comment"
                    if not col_name_stripped and col_type_stripped.lower() == "comment":
                        table_comment = col_comment.strip() if col_comment else ""
                    # 也可能是：col_name 为 "comment"（某些 Hive 版本）
                    elif col_name_stripped.lower() == "comment":
                        table_comment = col_type_stripped if col_type_stripped else ""

            return {
                "comment": table_comment,
                "columns": columns,
                "partition_columns": partition_cols
            }

        except Exception as e:
            logger.error(f"获取表 {table_name} 元数据失败: {e}")
            raise RuntimeError(f"获取表 {table_name} 元数据失败") from e
        finally:
            if should_close:
                conn.close()

    def _get_full_schema(
        self,
        table_names: Optional[List[str]] = None,
        max_workers: int = DEFAULT_MAX_WORKERS
    ) -> List[Dict[str, Any]]:
        """
        并发获取所有表结构（内部同步方法）

        使用线程池并发获取

        Args:
            table_names: 可选，指定表名列表，None 表示全部
            max_workers: 最大并发数，默认10

        Returns:
            表结构列表: [{table_name, description, columns: [{name, type, ...}]}]
        """
        # 1. 获取表名列表
        if table_names is None:
            table_names = self._get_tables()
        else:
            invalid_names = [name for name in table_names if not _is_valid_hive_identifier(name)]
            if invalid_names:
                raise ValueError(f"存在非法表名: {', '.join(invalid_names)}")

        if not table_names:
            return []

        # 2. 并发获取每个表的元数据
        def get_single_table_schema(table_name: str) -> Dict[str, Any]:
            """获取单个表的完整信息"""
            conn = self._get_connection()
            try:
                metadata = self._get_table_metadata(table_name, conn)
                return {
                    "table_name": table_name,
                    "description": metadata.get("comment") or table_name,
                    "columns": metadata.get("columns", [])
                }
            finally:
                conn.close()

        tables = []
        failed_tables = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_table = {
                executor.submit(get_single_table_schema, table_name): table_name
                for table_name in table_names
            }

            # 收集结果
            for future in as_completed(future_to_table):
                try:
                    result = future.result()
                    tables.append(result)
                except Exception as e:
                    table_name = future_to_table[future]
                    logger.error(f"获取表 {table_name} 结构失败: {e}")
                    failed_tables.append(table_name)

        if failed_tables:
            raise RuntimeError(f"Hive 表结构同步失败，失败表: {', '.join(failed_tables)}")

        # 按原始表名顺序排序
        table_order = {name: i for i, name in enumerate(table_names)}
        tables.sort(key=lambda x: table_order.get(x["table_name"], 999999))

        return tables

    async def get_tables_async(self) -> List[str]:
        """异步获取数据库中所有表名"""
        return await asyncio.to_thread(self._get_tables)

    async def get_full_schema_async(
        self,
        table_names: Optional[List[str]] = None,
        max_workers: int = DEFAULT_MAX_WORKERS
    ) -> List[Dict[str, Any]]:
        """
        异步获取所有表结构

        Hive 没有异步驱动，使用 asyncio.to_thread 包装同步方法

        Args:
            table_names: 可选，指定表名列表，None 表示全部
            max_workers: 最大并发数

        Returns:
            表结构列表: [{table_name, description, columns: [...]}]
        """
        return await asyncio.to_thread(
            self._get_full_schema,
            table_names,
            max_workers
        )
