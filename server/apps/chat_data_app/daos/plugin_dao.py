"""插件状态数据访问层：chat_data_plugin 表读写。

启停以本表为唯一真值（替代磁盘 ``.disabled`` 标记）。DAO 只做读写与持久化映射，
不主动 commit（由 ``get_session`` 退出统一提交）。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Set

from sqlalchemy import select, update

from utils.database import get_session as default_get_session
from utils.models import ChatDataPlugin


class PluginDao:
    """封装 chat_data_plugin 表读写。"""

    def __init__(self, session_context: Optional[Callable] = None):
        self._session_context = session_context or default_get_session

    async def list_enabled_names(self) -> Set[str]:
        """返回当前启用且未删除的插件名集合（供装配/技能快照读取）。"""
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataPlugin.plugin_name).filter_by(enabled_flag=1, delete_flag=0)
            )
            return {str(name) for name in result.scalars().all()}

    async def list_active(self) -> List[ChatDataPlugin]:
        """返回全部未删除插件记录（供管理端列表）。"""
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataPlugin)
                .filter_by(delete_flag=0)
                .order_by(ChatDataPlugin.id.asc())
            )
            return list(result.scalars().all())

    async def upsert_installed(
        self,
        *,
        plugin_name: str,
        version: str,
        display_name: str,
        description: str,
        manifest_text: str,
        operator_id: str,
        operator_name: str,
    ) -> None:
        """安装/重装：复活墓碑行或新建，配合单列 UNIQUE(plugin_name) 支持无限次装卸循环。

        生产 DB 账号无 DELETE 权限，卸载只软删（``delete_flag=1``），因此重装必须【复活
        同一行】而非插新行——这里按 ``plugin_name`` 查询时**刻意不加 delete_flag 过滤**，
        正是为了命中卸载留下的墓碑行并复活（``delete_flag=0``、``enabled_flag=1``）。
        若误加 ``delete_flag==0`` 过滤，会查不到墓碑转而 insert，撞上单列唯一索引直接失败。
        """
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataPlugin).filter_by(plugin_name=plugin_name)
            )
            row = result.scalar_one_or_none()
            if row is None:
                db.add(ChatDataPlugin(
                    plugin_name=plugin_name,
                    version=version,
                    display_name=display_name,
                    description=description,
                    manifest_text=manifest_text,
                    enabled_flag=1,
                    delete_flag=0,
                    create_by=operator_id,
                    create_by_name=operator_name,
                    update_by=operator_id,
                    update_by_name=operator_name,
                ))
                return
            row.version = version
            row.display_name = display_name
            row.description = description
            row.manifest_text = manifest_text
            row.delete_flag = 0
            row.enabled_flag = 1
            row.update_by = operator_id
            row.update_by_name = operator_name

    async def set_enabled(
        self,
        *,
        plugin_name: str,
        enabled: bool,
        operator_id: str,
        operator_name: str,
    ) -> bool:
        """切换启停；返回插件是否存在（未删除）。

        先查存在性再更新：MySQL 的 UPDATE rowcount 反映"实际改变的行数"，对已处于
        目标状态的插件重复设置会得到 0，不能用它判定是否存在（否则幂等切换会误报不存在）。
        """
        async with self._session_context() as db:
            exists = (
                await db.execute(
                    select(ChatDataPlugin.id)
                    .filter_by(plugin_name=plugin_name, delete_flag=0)
                    .limit(1)
                )
            ).first()
            if exists is None:
                return False
            await db.execute(
                update(ChatDataPlugin)
                .where(
                    ChatDataPlugin.plugin_name == plugin_name,
                    ChatDataPlugin.delete_flag == 0,
                )
                .values(
                    enabled_flag=1 if enabled else 0,
                    update_by=operator_id,
                    update_by_name=operator_name,
                )
            )
            return True

    async def mark_deleted(
        self,
        *,
        plugin_name: str,
        operator_id: str,
        operator_name: str,
    ) -> bool:
        """卸载时软删除记录；返回是否命中（不存在返回 False）。"""
        async with self._session_context() as db:
            result = await db.execute(
                update(ChatDataPlugin)
                .where(
                    ChatDataPlugin.plugin_name == plugin_name,
                    ChatDataPlugin.delete_flag == 0,
                )
                .values(
                    delete_flag=1,
                    enabled_flag=0,
                    update_by=operator_id,
                    update_by_name=operator_name,
                )
            )
            return int(result.rowcount or 0) > 0
