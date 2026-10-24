"""Hive 写适配器：唯一 Hive 物理写入入口。

只负责连接与执行已校验的 SQL；DDL/DML 生成仍由各业务服务负责。
同步 pyhive 调用通过线程池执行，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


class HiveWriteAdapter:
    """Hive direct 写适配器。"""

    def execute_statements(self, target: dict[str, Any], statements: list[str]) -> None:
        """同步执行写入语句（供线程池/脚本调用）。"""
        source_config = target.get("source_config") or {}
        try:
            from pyhive import hive
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("当前环境缺少 Hive 客户端依赖") from exc
        kwargs = {
            "host": _text(source_config.get("host")),
            "port": int(source_config.get("port") or 10000),
            "username": _text(source_config.get("username")) or "hive",
            "database": _text(source_config.get("database")) or "default",
        }
        if source_config.get("password"):
            kwargs["password"] = str(source_config.get("password"))
        conn = hive.Connection(**kwargs)
        try:
            cursor = conn.cursor()
            for sql in statements:
                cursor.execute(sql)
        finally:
            conn.close()

    async def execute_statements_async(self, target: dict[str, Any], statements: list[str]) -> None:
        await asyncio.to_thread(self.execute_statements, target, statements)


def get_write_adapter(target: dict[str, Any]) -> HiveWriteAdapter:
    """按执行目标返回写适配器；当前只支持 Hive direct。"""
    source_type = _text((target or {}).get("source_type")).lower()
    if source_type not in {"hive", "hive_direct"}:
        raise RuntimeError(f"不支持的写数据源类型: {source_type or 'unknown'}")
    return HiveWriteAdapter()
