"""插件状态数据访问层：chat_data_plugin 表读写。

启停以本表为唯一真值（替代磁盘 ``.disabled`` 标记）。DAO 只做读写与持久化映射，
不主动 commit（由 ``get_session`` 退出统一提交）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from sqlalchemy import CursorResult, select, update

from utils.database import get_session as default_get_session
from utils.models import ChatDataPlugin, ChatDataPluginPermission


class PluginDao:
    """封装 chat_data_plugin 表读写。"""

    def __init__(self, session_context: Callable | None = None) -> None:
        self._session_context = session_context or default_get_session

    async def list_enabled_names(self) -> set[str]:
        """返回当前启用且未删除的插件名集合（供装配/技能快照读取）。"""
        async with self._session_context() as db:
            result = await db.execute(select(ChatDataPlugin.plugin_name).filter_by(enabled_flag=1, delete_flag=0))
            return {str(name) for name in result.scalars().all()}

    async def list_enabled_access(self) -> dict[str, tuple[str, frozenset[str]]]:
        """返回启用插件的权限快照：``{plugin_name: (scope_type, authorized_user_names)}``。"""
        async with self._session_context() as db:
            plugin_result = await db.execute(
                select(ChatDataPlugin.plugin_name, ChatDataPlugin.scope_type).filter_by(
                    enabled_flag=1,
                    delete_flag=0,
                )
            )
            scopes = {str(plugin_name): str(scope_type or "all") for plugin_name, scope_type in plugin_result.all()}
            if not scopes:
                return {}
            permission_result = await db.execute(
                select(ChatDataPluginPermission.plugin_name, ChatDataPluginPermission.user_name)
                .where(
                    ChatDataPluginPermission.plugin_name.in_(scopes),
                    ChatDataPluginPermission.delete_flag == 0,
                )
                .order_by(ChatDataPluginPermission.user_name.asc())
            )
            users_by_plugin: dict[str, set[str]] = {}
            for plugin_name, user_name in permission_result.all():
                users_by_plugin.setdefault(str(plugin_name), set()).add(str(user_name))
            return {
                plugin_name: (scope_type, frozenset(users_by_plugin.get(plugin_name, set())))
                for plugin_name, scope_type in scopes.items()
            }

    async def list_active(self) -> list[ChatDataPlugin]:
        """返回全部未删除插件记录（供管理端列表）。"""
        async with self._session_context() as db:
            result = await db.execute(select(ChatDataPlugin).filter_by(delete_flag=0).order_by(ChatDataPlugin.id.asc()))
            return list(result.scalars().all())

    async def list_active_permissions(self) -> dict[str, list[str]]:
        """批量返回未删除插件的有效授权用户名（供管理端列表展示）。"""
        async with self._session_context() as db:
            active_names = (
                (await db.execute(select(ChatDataPlugin.plugin_name).filter_by(delete_flag=0))).scalars().all()
            )
            if not active_names:
                return {}
            result = await db.execute(
                select(ChatDataPluginPermission.plugin_name, ChatDataPluginPermission.user_name)
                .where(
                    ChatDataPluginPermission.plugin_name.in_(active_names),
                    ChatDataPluginPermission.delete_flag == 0,
                )
                .order_by(
                    ChatDataPluginPermission.plugin_name.asc(),
                    ChatDataPluginPermission.user_name.asc(),
                )
            )
            permissions: dict[str, list[str]] = {}
            for plugin_name, user_name in result.all():
                permissions.setdefault(str(plugin_name), []).append(str(user_name))
            return permissions

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
        重装保留原 ``scope_type``；卸载已软删人员授权，``selected`` 不会自动恢复授权，
        需管理员重新配置，避免重装静默扩大或恢复历史权限。
        """
        async with self._session_context() as db:
            result = await db.execute(select(ChatDataPlugin).filter_by(plugin_name=plugin_name))
            row = result.scalar_one_or_none()
            if row is None:
                db.add(
                    ChatDataPlugin(
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
                    )
                )
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
                await db.execute(select(ChatDataPlugin.id).filter_by(plugin_name=plugin_name, delete_flag=0).limit(1))
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
        """卸载时同步软删插件及其人员授权；返回是否命中插件记录。"""
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
            hit = int(cast(CursorResult[Any], result).rowcount or 0) > 0
            if hit:
                await db.execute(
                    update(ChatDataPluginPermission)
                    .where(
                        ChatDataPluginPermission.plugin_name == plugin_name,
                        ChatDataPluginPermission.delete_flag == 0,
                    )
                    .values(
                        delete_flag=1,
                        update_by=operator_id,
                        update_by_name=operator_name,
                    )
                )
            return hit

    async def replace_permissions(
        self,
        *,
        plugin_name: str,
        scope_type: str,
        user_names: list[str],
        operator_id: str,
        operator_name: str,
    ) -> bool:
        """单事务替换插件权限；返回插件是否存在（未删除）。

        唯一键不含 ``delete_flag``：被撤销的用户行保留墓碑，重新授权时复活同一行。
        """
        async with self._session_context() as db:
            plugin = (
                await db.execute(select(ChatDataPlugin).filter_by(plugin_name=plugin_name, delete_flag=0).limit(1))
            ).scalar_one_or_none()
            if plugin is None:
                return False

            plugin.scope_type = scope_type
            plugin.update_by = operator_id
            plugin.update_by_name = operator_name
            requested = set(user_names)
            existing_rows = list(
                (await db.execute(select(ChatDataPluginPermission).filter_by(plugin_name=plugin_name))).scalars().all()
            )
            rows_by_name = {row.user_name: row for row in existing_rows}
            for row in existing_rows:
                if row.user_name not in requested and row.delete_flag == 0:
                    row.delete_flag = 1
                    row.update_by = operator_id
                    row.update_by_name = operator_name
            for user_name in user_names:
                row = rows_by_name.get(user_name)
                if row is None:
                    db.add(
                        ChatDataPluginPermission(
                            plugin_name=plugin_name,
                            user_name=user_name,
                            delete_flag=0,
                            create_by=operator_id,
                            create_by_name=operator_name,
                            update_by=operator_id,
                            update_by_name=operator_name,
                        )
                    )
                    continue
                row.delete_flag = 0
                row.update_by = operator_id
                row.update_by_name = operator_name
            return True
