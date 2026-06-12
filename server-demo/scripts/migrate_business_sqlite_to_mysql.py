"""
将旧的 SQLite 业务表迁移到 FXCJG_DB_* 对应的 MySQL。

运行方式：
cd server && python scripts/migrate_business_sqlite_to_mysql.py --truncate
"""

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import aiomysql
from dotenv import load_dotenv

# 添加项目根目录到路径
SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

load_dotenv(SERVER_ROOT / ".env", override=False)

from utils.crypto import decrypt_password
from utils.logger import get_logger
from utils.storage_paths import DATA_ROOT
from utils.lang_util.schema import (
    MYSQL_CHAT_DATA_DASHBOARD_PANEL_TABLE,
    MYSQL_CHAT_DATA_DASHBOARD_TABLE,
    MYSQL_SQL_QUERY_FAVORITE_TABLE,
    MYSQL_SQL_QUERY_HISTORY_TABLE,
    MYSQL_UPLOADED_FILES_TABLE,
    MYSQL_USER_THREADS_TABLE,
)

logger = get_logger(__name__)

BUSINESS_TABLES = [
    "user_threads",
    "uploaded_files",
    "chat_data_dashboard",
    "chat_data_dashboard_panel",
    "sql_query_history",
    "sql_query_favorite",
]

MYSQL_DDL_BY_TABLE = {
    "user_threads": MYSQL_USER_THREADS_TABLE,
    "uploaded_files": MYSQL_UPLOADED_FILES_TABLE,
    "chat_data_dashboard": MYSQL_CHAT_DATA_DASHBOARD_TABLE,
    "chat_data_dashboard_panel": MYSQL_CHAT_DATA_DASHBOARD_PANEL_TABLE,
    "sql_query_history": MYSQL_SQL_QUERY_HISTORY_TABLE,
    "sql_query_favorite": MYSQL_SQL_QUERY_FAVORITE_TABLE,
}

NULL_VALUE_DEFAULTS = {
    "create_by": "",
    "update_by": "",
}

CURRENT_TIME_FALLBACK_COLUMNS = {
    "create_time",
    "update_time",
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="迁移 SQLite 业务表到 MySQL")
    parser.add_argument(
        "--sqlite-path",
        default=str(Path(DATA_ROOT) / "checkpoints.db"),
        help="源 SQLite 文件路径，默认 DATA_ROOT/checkpoints.db",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="批量写入大小，默认 500",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="迁移前清空目标 MySQL 表",
    )
    return parser.parse_args()


def get_mysql_config() -> dict:
    """读取业务库 MySQL 配置。"""
    required_keys = [
        "FXCJG_DB_HOST",
        "FXCJG_DB_PORT",
        "FXCJG_DB",
        "FXCJG_DB_USER",
        "FXCJG_DB_PASSWORD",
    ]
    missing_keys = [key for key in required_keys if not os.getenv(key)]
    if missing_keys:
        raise ValueError(f"缺少业务数据库配置: {', '.join(missing_keys)}")
    return {
        "host": os.getenv("FXCJG_DB_HOST"),
        "port": int(os.getenv("FXCJG_DB_PORT", "3306")),
        "db": os.getenv("FXCJG_DB"),
        "user": os.getenv("FXCJG_DB_USER"),
        "password": decrypt_password(os.getenv("FXCJG_DB_PASSWORD", "")),
        "charset": "utf8mb4",
        "autocommit": False,
    }


def normalize_mysql_ddl(ddl: str) -> str:
    """清理表结构 SQL 末尾的多余分号。"""
    return ddl.strip().rstrip(";").strip()


def table_exists_in_sqlite(conn: sqlite3.Connection, table_name: str) -> bool:
    """检查 SQLite 源表是否存在。"""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    )
    return cursor.fetchone() is not None


def get_sqlite_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    """读取 SQLite 表字段顺序。"""
    cursor = conn.cursor()
    cursor.execute(f'PRAGMA table_info("{table_name}")')
    return [row[1] for row in cursor.fetchall()]


def get_sqlite_count(conn: sqlite3.Connection, table_name: str) -> int:
    """统计 SQLite 表行数。"""
    cursor = conn.cursor()
    cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    return int(cursor.fetchone()[0])


async def get_mysql_count(conn: aiomysql.Connection, table_name: str) -> int:
    """统计 MySQL 表行数。"""
    async with conn.cursor() as cursor:
        await cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
        row = await cursor.fetchone()
    return int(row[0] if row else 0)


async def ensure_mysql_tables(conn: aiomysql.Connection) -> None:
    """确保目标 MySQL 业务表存在。"""
    async with conn.cursor() as cursor:
        for table_name in BUSINESS_TABLES:
            try:
                await cursor.execute(normalize_mysql_ddl(MYSQL_DDL_BY_TABLE[table_name]))
            except Exception as exc:
                error_code = exc.args[0] if getattr(exc, "args", None) else None
                if error_code == 1050:
                    logger.info("目标表已存在，跳过建表: %s", table_name)
                    continue
                raise
    await conn.commit()


async def prepare_target_table(conn: aiomysql.Connection, table_name: str, truncate: bool) -> None:
    """准备目标表写入状态。"""
    target_count = await get_mysql_count(conn, table_name)
    if target_count == 0:
        return
    if not truncate:
        raise RuntimeError(f"MySQL 目标表 `{table_name}` 已有 {target_count} 条数据，请先清空或使用 --truncate")
    async with conn.cursor() as cursor:
        try:
            await cursor.execute(f"TRUNCATE TABLE `{table_name}`")
        except Exception as exc:
            error_code = exc.args[0] if getattr(exc, "args", None) else None
            if error_code != 1142:
                raise
            logger.warning("TRUNCATE 无权限，回退为 DELETE FROM: %s", table_name)
            await cursor.execute(f"DELETE FROM `{table_name}`")
    await conn.commit()
    logger.info("已清空目标表: %s", table_name)


async def copy_table(
    sqlite_conn: sqlite3.Connection,
    mysql_conn: aiomysql.Connection,
    table_name: str,
    batch_size: int,
) -> tuple[int, int]:
    """迁移单张业务表。"""
    source_count = get_sqlite_count(sqlite_conn, table_name)
    columns = get_sqlite_columns(sqlite_conn, table_name)
    if not columns:
        return source_count, 0

    quoted_columns = ", ".join(f'`{column}`' for column in columns)
    sqlite_columns = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT INTO `{table_name}` ({quoted_columns}) VALUES ({placeholders})"

    sqlite_cursor = sqlite_conn.cursor()
    sqlite_cursor.execute(f'SELECT {sqlite_columns} FROM "{table_name}"')

    inserted = 0
    async with mysql_conn.cursor() as mysql_cursor:
        while True:
            rows = sqlite_cursor.fetchmany(batch_size)
            if not rows:
                break
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            normalized_rows = [
                tuple(
                    current_time if value is None and column in CURRENT_TIME_FALLBACK_COLUMNS
                    else NULL_VALUE_DEFAULTS.get(column, value) if value is None
                    else value
                    for column, value in zip(columns, row)
                )
                for row in rows
            ]
            await mysql_cursor.executemany(insert_sql, normalized_rows)
            inserted += len(normalized_rows)
    await mysql_conn.commit()
    return source_count, inserted


async def migrate(sqlite_path: Path, batch_size: int, truncate: bool) -> None:
    """执行迁移。"""
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite 文件不存在: {sqlite_path}")

    logger.info("开始迁移业务表，源 SQLite: %s", sqlite_path)

    sqlite_conn = sqlite3.connect(str(sqlite_path))
    mysql_conn = await aiomysql.connect(**get_mysql_config())

    try:
        await ensure_mysql_tables(mysql_conn)

        for table_name in BUSINESS_TABLES:
            if not table_exists_in_sqlite(sqlite_conn, table_name):
                logger.warning("SQLite 源表不存在，跳过: %s", table_name)
                continue

            await prepare_target_table(mysql_conn, table_name, truncate)
            logger.info("开始迁移表: %s", table_name)

            try:
                source_count, inserted_count = await copy_table(sqlite_conn, mysql_conn, table_name, batch_size)
            except Exception:
                await mysql_conn.rollback()
                raise

            target_count = await get_mysql_count(mysql_conn, table_name)
            logger.info(
                "表迁移完成: %s, 源行数=%s, 插入行数=%s, 目标行数=%s",
                table_name,
                source_count,
                inserted_count,
                target_count,
            )
    finally:
        sqlite_conn.close()
        mysql_conn.close()


def main() -> None:
    """脚本入口。"""
    args = parse_args()
    asyncio.run(migrate(Path(args.sqlite_path), args.batch_size, args.truncate))


if __name__ == "__main__":
    main()
