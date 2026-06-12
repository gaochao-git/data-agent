"""
将旧的 chat-data 本地 JSON 数据源配置迁移到业务库 MySQL。

迁移目标表：
- datasource_group
- datasource_table
- semantic_config

当前模型：
- 组即数据源
- template_source 存模板源，格式 host:port:database
- member_sources_text 存组内执行源，多行文本，格式 host:port:database

运行方式：
cd server && python scripts/migrate_chatdata_json_to_mysql.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pymysql
from dotenv import load_dotenv

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

load_dotenv(SERVER_ROOT / ".env", override=False)

from utils.crypto import decrypt_password
from utils.logger import get_logger

logger = get_logger(__name__)


def _default_source_dir() -> Path:
    data_root = str(os.getenv("DATA_ROOT") or "").strip()
    if data_root:
        return Path(data_root) / "nl2sql" / "datasources"
    return SERVER_ROOT / "uploads" / "nl2sql" / "datasources"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="迁移 chat-data JSON 数据源配置到 MySQL")
    parser.add_argument(
        "--source-dir",
        default=str(_default_source_dir()),
        help="旧 datasource JSON 目录，默认 DATA_ROOT/nl2sql/datasources，未配置 DATA_ROOT 时回退到 server/uploads/nl2sql/datasources",
    )
    return parser.parse_args()


def get_mysql_config() -> dict:
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
        "user": os.getenv("FXCJG_DB_USER"),
        "password": decrypt_password(os.getenv("FXCJG_DB_PASSWORD", "")),
        "database": os.getenv("FXCJG_DB"),
        "charset": "utf8mb4",
        "autocommit": True,
        "cursorclass": pymysql.cursors.DictCursor,
    }


def _format_time(value: Any) -> str:
    if not value:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _normalize_relationships(relationships: List[dict]) -> List[dict]:
    normalized_relationships = []
    for rel in relationships or []:
        next_rel = dict(rel or {})
        join_conditions = next_rel.get("join_conditions") or []
        normalized_conditions = []
        for cond in join_conditions:
            left_col = (cond or {}).get("left_column")
            right_col = (cond or {}).get("right_column")
            if left_col and right_col:
                normalized_conditions.append({"left_column": left_col, "right_column": right_col})

        if not normalized_conditions:
            left_col = next_rel.get("left_column")
            right_col = next_rel.get("right_column")
            if left_col and right_col:
                normalized_conditions.append({"left_column": left_col, "right_column": right_col})

        if not normalized_conditions:
            continue

        next_rel["join_conditions"] = normalized_conditions
        next_rel["left_column"] = normalized_conditions[0]["left_column"]
        next_rel["right_column"] = normalized_conditions[0]["right_column"]
        normalized_relationships.append(next_rel)
    return normalized_relationships


def _extract_semantic_configs(datasource: Dict[str, Any]) -> List[dict]:
    configs = datasource.get("semantic_configs")
    if configs is not None:
        result = []
        for config in configs:
            next_config = dict(config or {})
            next_config["relationships"] = _normalize_relationships(next_config.get("relationships", []))
            result.append(next_config)
        return result

    old_config = datasource.get("semantic_config")
    if not old_config:
        return []
    if not old_config.get("enabled") and not old_config.get("metrics") and not old_config.get("dimensions"):
        return []

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    return [{
        "id": f"sc_{now.strftime('%Y%m%d%H%M%S')}_{now.microsecond}",
        "name": "默认语义配置",
        "description": "从旧版配置自动迁移",
        "enabled": old_config.get("enabled", False),
        "metrics": old_config.get("metrics", []),
        "dimensions": old_config.get("dimensions", []),
        "relationships": _normalize_relationships(old_config.get("joins", [])),
        "create_time": now_str,
        "update_time": now_str,
    }]


def _build_source_line(source_config: Dict[str, Any]) -> str | None:
    host = str(source_config.get("host") or "").strip()
    port = source_config.get("port")
    database = str(source_config.get("database") or "").strip()
    if not host or not port or not database:
        return None
    return f"{host}:{int(port)}:{database}"


def upsert_group(conn: pymysql.connections.Connection, datasource: Dict[str, Any], group_id: str) -> bool:
    source_type = str(datasource.get("source_type") or "").strip()
    source_config = datasource.get("source_config") or {}
    template_source = _build_source_line(source_config)
    if source_type not in ("mysql", "hive"):
        logger.warning("跳过不支持的数据源类型: group_id=%s, source_type=%s", group_id, source_type)
        return False
    if not template_source:
        logger.warning("数据源缺少必要连接信息，跳过: group_id=%s", group_id)
        return False

    member_sources = []
    for line in [template_source]:
        if line and line not in member_sources:
            member_sources.append(line)
    member_sources_text = "\n".join(member_sources)

    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO datasource_group (
                group_id, group_name, group_desc, source_type, template_source,
                member_sources_text, mapped_modules_json, is_default,
                create_time, update_time, create_by, update_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                group_name = VALUES(group_name),
                group_desc = VALUES(group_desc),
                source_type = VALUES(source_type),
                template_source = VALUES(template_source),
                member_sources_text = VALUES(member_sources_text),
                mapped_modules_json = VALUES(mapped_modules_json),
                is_default = VALUES(is_default),
                update_time = VALUES(update_time),
                update_by = VALUES(update_by),
                delete_flag = 0
            """,
            (
                group_id,
                datasource.get("ds_name") or group_id,
                datasource.get("ds_description"),
                source_type,
                template_source,
                member_sources_text,
                json.dumps(datasource.get("mapped_modules") or [], ensure_ascii=False),
                1 if datasource.get("is_default") else 0,
                _format_time(datasource.get("create_time")),
                _format_time(datasource.get("update_time")),
                datasource.get("create_by") or "",
                datasource.get("update_by") or "",
            ),
        )
    return True


def replace_datasource_tables(
    conn: pymysql.connections.Connection,
    group_id: str,
    tables: List[dict],
    create_by: str,
    update_by: str,
) -> int:
    table_list = tables or []
    active_table_names = [str((table or {}).get("table_name") or "").strip() for table in table_list]
    active_table_names = [name for name in active_table_names if name]

    with conn.cursor() as cursor:
        if active_table_names:
            placeholders = ", ".join(["%s"] * len(active_table_names))
            cursor.execute(
                f"""
                UPDATE datasource_table
                SET delete_flag = 1
                WHERE group_id = %s AND table_names NOT IN ({placeholders})
                """,
                (group_id, *active_table_names),
            )
        else:
            cursor.execute(
                "UPDATE datasource_table SET delete_flag = 1 WHERE group_id = %s",
                (group_id,),
            )
            return 0

        written = 0
        for index, table in enumerate(table_list):
            table_name = str((table or {}).get("table_name") or "").strip()
            if not table_name:
                continue
            cursor.execute(
                """
                INSERT INTO datasource_table (
                    group_id, table_names, table_desc, columns_json,
                    sort_no, create_time, update_time, create_by, update_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    table_desc = VALUES(table_desc),
                    columns_json = VALUES(columns_json),
                    sort_no = VALUES(sort_no),
                    update_time = VALUES(update_time),
                    update_by = VALUES(update_by),
                    delete_flag = 0
                """,
                (
                    group_id,
                    table_name,
                    table.get("description"),
                    json.dumps(table.get("columns") or [], ensure_ascii=False),
                    index,
                    _format_time(table.get("create_time")),
                    _format_time(table.get("update_time")),
                    create_by,
                    update_by,
                ),
            )
            written += 1
    return written


def replace_semantic_configs(conn: pymysql.connections.Connection, group_id: str, semantic_configs: List[dict]) -> int:
    config_list = semantic_configs or []
    active_config_ids = [str((config or {}).get("id") or "").strip() for config in config_list]
    active_config_ids = [config_id for config_id in active_config_ids if config_id]

    with conn.cursor() as cursor:
        if active_config_ids:
            placeholders = ", ".join(["%s"] * len(active_config_ids))
            cursor.execute(
                f"""
                UPDATE semantic_config
                SET delete_flag = 1
                WHERE group_id = %s AND semantic_config_id NOT IN ({placeholders})
                """,
                (group_id, *active_config_ids),
            )
        else:
            cursor.execute(
                "UPDATE semantic_config SET delete_flag = 1 WHERE group_id = %s",
                (group_id,),
            )
            return 0

        inserted = 0
        for index, config in enumerate(config_list):
            config_id = str(config.get("id") or "").strip()
            if not config_id:
                logger.warning("语义配置缺少 id，跳过: group_id=%s", group_id)
                continue
            cursor.execute(
                """
                INSERT INTO semantic_config (
                    semantic_config_id, group_id, semantic_config_name, semantic_config_desc,
                    ontology_rules, enabled_flag, metrics_json, dimensions_json, relationships_json,
                    sort_no, create_time, update_time, create_by, update_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    update_time = VALUES(update_time),
                    update_by = VALUES(update_by),
                    delete_flag = 0
                """,
                (
                    config_id,
                    group_id,
                    config.get("name") or config_id,
                    config.get("description"),
                    config.get("ontology_rules"),
                    1 if config.get("enabled", True) else 0,
                    json.dumps(config.get("metrics") or [], ensure_ascii=False),
                    json.dumps(config.get("dimensions") or [], ensure_ascii=False),
                    json.dumps(config.get("relationships") or [], ensure_ascii=False),
                    index,
                    _format_time(config.get("create_time")),
                    _format_time(config.get("update_time")),
                    config.get("create_by") or "",
                    config.get("update_by") or "",
                ),
            )
            inserted += 1
    return inserted


def migrate(source_dir: Path) -> None:
    if not source_dir.exists():
        raise FileNotFoundError(f"源目录不存在: {source_dir}")

    files = sorted(source_dir.glob("*.json"))
    logger.info("开始迁移 chat-data JSON，源目录: %s, 文件数=%s", source_dir, len(files))

    conn = pymysql.connect(**get_mysql_config())
    try:
        migrated_group_count = 0
        migrated_table_count = 0
        migrated_config_count = 0

        for path in files:
            with path.open("r", encoding="utf-8") as f:
                datasource = json.load(f)

            datasource_id = str(datasource.get("id") or "").strip()
            if not datasource_id:
                logger.warning("数据源文件缺少 id，跳过: %s", path.name)
                continue

            group_id = str(datasource.get("group_id") or datasource_id).strip()
            semantic_configs = _extract_semantic_configs(datasource)
            if not upsert_group(conn, datasource, group_id):
                continue

            migrated_group_count += 1
            migrated_table_count += replace_datasource_tables(
                conn,
                group_id=group_id,
                tables=datasource.get("ds_tables") or [],
                create_by=datasource.get("create_by") or "",
                update_by=datasource.get("update_by") or "",
            )
            migrated_config_count += replace_semantic_configs(conn, group_id, semantic_configs)
            logger.info(
                "迁移完成: source_datasource_id=%s, group_id=%s, tables=%s, semantic_configs=%s",
                datasource_id,
                group_id,
                len(datasource.get("ds_tables") or []),
                len(semantic_configs),
            )

        logger.info(
            "迁移结束: datasource_group=%s, datasource_table=%s, semantic_config=%s",
            migrated_group_count,
            migrated_table_count,
            migrated_config_count,
        )
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    migrate(Path(args.source_dir))


if __name__ == "__main__":
    main()
