#!/usr/bin/env python3
"""一次性迁移：把磁盘插件目录的启停状态导入 ``chat_data_plugin`` 表。

启停改由 DB 表为唯一真值后，运行期不再读 ``.disabled`` 标记。本脚本扫描
``CHAT_DATA_HOME/plugins``，为每个合法插件目录写入/更新一条 ``chat_data_plugin``
记录，``enabled_flag`` 取自当前 ``.disabled`` 标记（无标记=启用）。幂等，可重复执行。

不删除 ``.disabled`` 文件（loader 已不再读取，留着无害）。运行：

    cd server && python scripts/migrate_plugins_to_db.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from utils.common_util import load_runtime_env  # noqa: E402

load_runtime_env(SERVER_DIR)

from sqlalchemy import select  # noqa: E402

from apps.chat_data_app.agent.plugins.loader import plugins_root  # noqa: E402
from apps.chat_data_app.agent.plugins.manifest import (  # noqa: E402
    PluginManifest,
    PluginManifestError,
    load_plugin_manifest,
)
from utils.database import get_session  # noqa: E402
from utils.logger import get_logger  # noqa: E402
from utils.models import ChatDataPlugin  # noqa: E402

logger = get_logger(__name__)

_DISABLED_MARKER = ".disabled"
_OPERATOR_ID = ""
_OPERATOR_NAME = "plugin-state-migration"


async def _upsert_row(db, manifest: PluginManifest, *, enabled: bool) -> None:
    """写入或更新单个插件记录：已存在则刷新元数据与启停、复活未删除；否则新建。"""
    manifest_text = json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True)
    result = await db.execute(select(ChatDataPlugin).filter_by(plugin_name=manifest.name))
    row = result.scalar_one_or_none()
    if row is None:
        db.add(ChatDataPlugin(
            plugin_name=manifest.name,
            version=manifest.version,
            display_name=manifest.display_name,
            description=manifest.description,
            manifest_text=manifest_text,
            enabled_flag=1 if enabled else 0,
            delete_flag=0,
            create_by=_OPERATOR_ID,
            create_by_name=_OPERATOR_NAME,
            update_by=_OPERATOR_ID,
            update_by_name=_OPERATOR_NAME,
        ))
        return
    row.version = manifest.version
    row.display_name = manifest.display_name
    row.description = manifest.description
    row.manifest_text = manifest_text
    row.enabled_flag = 1 if enabled else 0
    row.delete_flag = 0
    row.update_by = _OPERATOR_ID
    row.update_by_name = _OPERATOR_NAME


async def migrate() -> int:
    """扫描磁盘插件目录，幂等地把启停状态写入 DB，返回处理条数。"""
    root = plugins_root()
    if not root.is_dir():
        print(f"插件根目录不存在，无需迁移: {root}")
        return 0
    imported = 0
    async with get_session() as db:
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.is_symlink() or entry.name.startswith("."):
                continue
            try:
                manifest = load_plugin_manifest(entry)
            except PluginManifestError as err:
                logger.warning("跳过非法插件目录 %s: %s", entry.name, err)
                continue
            if manifest.name != entry.name:
                logger.warning(
                    "跳过目录名与 manifest.name 不一致: dir=%s manifest=%s",
                    entry.name,
                    manifest.name,
                )
                continue
            await _upsert_row(db, manifest, enabled=not (entry / _DISABLED_MARKER).exists())
            imported += 1
    print(f"迁移完成：处理 {imported} 个插件，启停状态已写入 chat_data_plugin")
    return imported


if __name__ == "__main__":
    asyncio.run(migrate())
