"""
MySQL 表结构同步服务

从 MySQL 数据库同步表结构到数据源
"""

import os
from typing import List, Dict, Any, Optional
import aiomysql

from utils.logger import get_logger

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


CONNECT_TIMEOUT_SECONDS = _read_positive_int_env("CHAT_DATA_DB_CONNECT_TIMEOUT_SECONDS", 3)


class MySQLSyncService:
    """MySQL 同步服务（异步）"""

    def __init__(
        self,
        host: str,
        port: int = 3306,
        database: str = "",
        user: str = "",
        password: str = ""
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password

    async def _get_connection(self):
        """获取 MySQL 异步连接"""
        return await aiomysql.connect(
            host=self.host,
            port=self.port,
            db=self.database,
            user=self.user,
            password=self.password,
            charset='utf8mb4',
            connect_timeout=CONNECT_TIMEOUT_SECONDS
        )

    async def get_tables_async(self) -> List[str]:
        """异步获取数据库中所有表名"""
        conn = await self._get_connection()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SHOW TABLES")
                rows = await cursor.fetchall()
                tables = [list(row.values())[0] for row in rows]
                return tables
        finally:
            conn.close()
            await conn.ensure_closed()

    async def get_full_schema_async(self, table_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        异步获取所有表结构（表名+注释+列信息）

        使用 aiomysql 异步驱动，不阻塞事件循环

        Args:
            table_names: 可选，指定表名列表，None 表示全部

        Returns:
            表结构列表: [{table_name, description, columns: [{name, type, ...}]}]
        """
        conn = await self._get_connection()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                sql = """
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
                """
                await cursor.execute(sql, (self.database,))
                rows = await cursor.fetchall()

                # 按表名分组
                tables_dict = {}
                for row in rows:
                    table_name = row['TABLE_NAME']

                    if table_names and table_name not in table_names:
                        continue

                    if table_name not in tables_dict:
                        tables_dict[table_name] = {
                            "table_name": table_name,
                            "description": row['TABLE_COMMENT'] or table_name,
                            "columns": []
                        }

                    if row['COLUMN_NAME']:
                        column_key = row['COLUMN_KEY']
                        tables_dict[table_name]["columns"].append({
                            "name": row['COLUMN_NAME'],
                            "type": row['COLUMN_TYPE'],
                            "mysql_type": row['COLUMN_TYPE'],
                            "description": row['COLUMN_COMMENT'] or row['COLUMN_NAME'],
                            "primary_key": column_key == 'PRI',
                            "unique_key": column_key == 'UNI',
                            "index_key": column_key == 'MUL',
                            "nullable": row['IS_NULLABLE'] == 'YES'
                        })

                if table_names:
                    return [tables_dict[t] for t in table_names if t in tables_dict]

                return list(tables_dict.values())
        finally:
            conn.close()
            await conn.ensure_closed()
