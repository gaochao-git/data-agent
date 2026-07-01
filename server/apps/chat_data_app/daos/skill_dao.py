"""ChatData 用户技能数据访问层。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_, select, update

from utils.lang_util.models import ChatDataSkill, ChatDataSkillFavorite, ChatDataSkillVersion


def get_session():
    """运行时获取业务库会话，避免无数据库环境导入技能模块失败。"""
    from utils.lang_util.database import get_session as session_context
    return session_context()


class SkillDao:
    """封装用户技能元数据和收藏关系数据库读写。"""

    def _skill_visible_filter(self, user_id: str):
        return or_(
            ChatDataSkill.skill_source == "system",
            ChatDataSkill.visibility_type == "global",
            ChatDataSkill.owner_user_id == (user_id or ""),
        )

    def _skill_base_filters(self, user_id: str, include_disabled: bool = True):
        filters = [ChatDataSkill.delete_flag == 0, self._skill_visible_filter(user_id)]
        if not include_disabled:
            filters.extend([
                ChatDataSkill.skill_publish_status != "disabled",
                ChatDataSkill.enabled_flag == 1,
            ])
        return filters

    @staticmethod
    def _skill_row_to_dict(row: ChatDataSkill, draft_skill_version_no: Any = None, favorite_flag: Any = 0) -> Dict[str, Any]:
        return {
            "skill_id": row.skill_id,
            "skill_name": row.skill_name,
            "skill_title": row.skill_title,
            "skill_desc": row.skill_desc,
            "skill_category": row.skill_category,
            "skill_scope": getattr(row, "skill_scope", "analyze") or "analyze",
            "skill_source": row.skill_source,
            "visibility_type": row.visibility_type,
            "enabled_flag": row.enabled_flag,
            "skill_publish_status": row.skill_publish_status,
            "current_skill_version_no": row.current_skill_version_no,
            "owner_user_id": row.owner_user_id,
            "skill_root_dir": row.skill_root_dir,
            "create_by": row.create_by,
            "create_by_name": row.create_by_name,
            "update_by": row.update_by,
            "update_by_name": row.update_by_name,
            "create_time": row.create_time,
            "update_time": row.update_time,
            "draft_skill_version_no": draft_skill_version_no,
            "favorite_flag": int(favorite_flag or 0),
        }

    @staticmethod
    def _version_row_to_dict(row: ChatDataSkillVersion) -> Dict[str, Any]:
        return {
            "skill_id": row.skill_id,
            "skill_version_no": row.skill_version_no,
            "skill_version_dir": row.skill_version_dir,
            "version_publish_status": row.version_publish_status,
            "create_time": row.create_time,
            "update_time": row.update_time,
            "create_by": row.create_by,
            "create_by_name": row.create_by_name,
            "update_by": row.update_by,
            "update_by_name": row.update_by_name,
        }

    def _skill_select(self, user_id: str, include_disabled: bool):
        draft_subquery = (
            select(func.max(ChatDataSkillVersion.skill_version_no))
            .where(
                ChatDataSkillVersion.skill_id == ChatDataSkill.skill_id,
                ChatDataSkillVersion.version_publish_status == "draft",
                ChatDataSkillVersion.delete_flag == 0,
            )
            .scalar_subquery()
        )
        stmt = (
            select(ChatDataSkill, draft_subquery.label("draft_skill_version_no"), ChatDataSkillFavorite.id.label("favorite_id"))
            .outerjoin(
                ChatDataSkillFavorite,
                and_(
                    ChatDataSkillFavorite.skill_id == ChatDataSkill.skill_id,
                    ChatDataSkillFavorite.favorite_user_id == (user_id or ""),
                    ChatDataSkillFavorite.delete_flag == 0,
                ),
            )
            .where(*self._skill_base_filters(user_id, include_disabled=include_disabled))
        )
        return stmt

    async def list_skills_async(self, user_id: str = "", include_disabled: bool = False) -> List[Dict[str, Any]]:
        stmt = (
            self._skill_select(user_id, include_disabled=include_disabled)
            .order_by(ChatDataSkill.skill_source.asc(), ChatDataSkill.update_time.desc())
        )
        async with get_session() as db:
            result = await db.execute(stmt)
            return [
                self._skill_row_to_dict(row, draft_skill_version_no=draft_no, favorite_flag=1 if favorite_id else 0)
                for row, draft_no, favorite_id in result.all()
            ]

    async def get_skill_async(self, skill_id_or_name: str, user_id: str = "") -> Optional[Dict[str, Any]]:
        value = str(skill_id_or_name or "").strip()
        if not value:
            return None
        stmt = (
            self._skill_select(user_id, include_disabled=True)
            .where(or_(ChatDataSkill.skill_id == value, ChatDataSkill.skill_name == value))
            .limit(1)
        )
        async with get_session() as db:
            result = await db.execute(stmt)
            item = result.first()
            if not item:
                return None
            row, draft_no, favorite_id = item
            return self._skill_row_to_dict(row, draft_skill_version_no=draft_no, favorite_flag=1 if favorite_id else 0)

    async def get_published_version_async(self, skill_id: str, skill_version_no: int) -> Optional[Dict[str, Any]]:
        stmt = (
            select(ChatDataSkillVersion)
            .where(
                ChatDataSkillVersion.delete_flag == 0,
                ChatDataSkillVersion.skill_id == skill_id,
                ChatDataSkillVersion.skill_version_no == int(skill_version_no or 0),
                ChatDataSkillVersion.version_publish_status == "published",
            )
            .limit(1)
        )
        async with get_session() as db:
            row = (await db.execute(stmt)).scalars().first()
            return self._version_row_to_dict(row) if row else None

    async def list_versions_async(self, skill_id: str) -> List[Dict[str, Any]]:
        stmt = (
            select(ChatDataSkillVersion)
            .where(ChatDataSkillVersion.delete_flag == 0, ChatDataSkillVersion.skill_id == skill_id)
            .order_by(ChatDataSkillVersion.skill_version_no.desc())
        )
        async with get_session() as db:
            rows = (await db.execute(stmt)).scalars().all()
            return [self._version_row_to_dict(row) for row in rows]

    async def get_version_async(self, skill_id: str, skill_version_no: int) -> Optional[Dict[str, Any]]:
        stmt = (
            select(ChatDataSkillVersion)
            .where(
                ChatDataSkillVersion.delete_flag == 0,
                ChatDataSkillVersion.skill_id == skill_id,
                ChatDataSkillVersion.skill_version_no == int(skill_version_no or 0),
            )
            .limit(1)
        )
        async with get_session() as db:
            row = (await db.execute(stmt)).scalars().first()
            return self._version_row_to_dict(row) if row else None

    async def get_latest_draft_version_async(self, skill_id: str) -> Optional[Dict[str, Any]]:
        return await self._get_latest_version_by_status_async(skill_id, "draft")

    async def get_latest_published_version_async(self, skill_id: str) -> Optional[Dict[str, Any]]:
        return await self._get_latest_version_by_status_async(skill_id, "published")

    async def _get_latest_version_by_status_async(self, skill_id: str, status: str) -> Optional[Dict[str, Any]]:
        stmt = (
            select(ChatDataSkillVersion)
            .where(
                ChatDataSkillVersion.delete_flag == 0,
                ChatDataSkillVersion.skill_id == skill_id,
                ChatDataSkillVersion.version_publish_status == status,
            )
            .order_by(ChatDataSkillVersion.skill_version_no.desc())
            .limit(1)
        )
        async with get_session() as db:
            row = (await db.execute(stmt)).scalars().first()
            return self._version_row_to_dict(row) if row else None

    async def next_version_no_async(self, skill_id: str) -> int:
        stmt = select(func.coalesce(func.max(ChatDataSkillVersion.skill_version_no), 0)).where(
            ChatDataSkillVersion.skill_id == skill_id,
            ChatDataSkillVersion.delete_flag == 0,
        )
        async with get_session() as db:
            max_version_no = (await db.execute(stmt)).scalar_one()
            return int(max_version_no or 0) + 1

    async def create_skill_with_version_async(self, skill: Dict[str, Any], version: Dict[str, Any]) -> None:
        async with get_session() as db:
            db.add(ChatDataSkill(
                skill_id=skill.get("skill_id") or "",
                skill_name=skill.get("skill_name") or "",
                skill_title=skill.get("skill_title") or "",
                skill_desc=skill.get("skill_desc") or "",
                skill_category=skill.get("skill_category") or "",
                skill_scope=skill.get("skill_scope") or "analyze",
                skill_source=skill.get("skill_source") or "user",
                visibility_type=skill.get("visibility_type") or "private",
                enabled_flag=int(skill.get("enabled_flag") or 1),
                skill_publish_status=skill.get("skill_publish_status") or "draft",
                current_skill_version_no=int(skill.get("current_skill_version_no") or 0),
                owner_user_id=skill.get("owner_user_id") or "",
                skill_root_dir=skill.get("skill_root_dir") or "",
                create_by=skill.get("create_by") or "",
                create_by_name=skill.get("create_by_name") or "",
                update_by=skill.get("update_by") or "",
                update_by_name=skill.get("update_by_name") or "",
            ))
            db.add(self._build_version_model(version))

    async def update_skill_async(self, skill_id: str, values: Dict[str, Any], update_by: str = "", update_by_name: str = "") -> int:
        allowed = {
            "skill_title", "skill_desc", "skill_category", "skill_scope",
            "visibility_type", "enabled_flag", "skill_publish_status",
            "current_skill_version_no", "skill_root_dir",
        }
        update_values = {key: values[key] for key in allowed if key in values}
        update_values["update_by"] = update_by or ""
        update_values["update_by_name"] = update_by_name or ""
        stmt = (
            update(ChatDataSkill)
            .where(ChatDataSkill.skill_id == skill_id, ChatDataSkill.delete_flag == 0)
            .values(**update_values)
        )
        async with get_session() as db:
            result = await db.execute(stmt)
            return int(result.rowcount or 0)

    async def insert_version_async(self, version: Dict[str, Any]) -> int:
        async with get_session() as db:
            db.add(self._build_version_model(version))
            return 1

    def _build_version_model(self, version: Dict[str, Any]) -> ChatDataSkillVersion:
        return ChatDataSkillVersion(
            skill_id=version.get("skill_id") or "",
            skill_version_no=int(version.get("skill_version_no") or 0),
            skill_version_dir=version.get("skill_version_dir") or "",
            version_publish_status=version.get("version_publish_status") or "draft",
            create_by=version.get("create_by") or "",
            create_by_name=version.get("create_by_name") or "",
            update_by=version.get("update_by") or "",
            update_by_name=version.get("update_by_name") or "",
        )

    async def publish_version_async(self, skill_id: str, skill_version_no: int, update_by: str = "", update_by_name: str = "") -> None:
        async with get_session() as db:
            await db.execute(
                update(ChatDataSkillVersion)
                .where(
                    ChatDataSkillVersion.skill_id == skill_id,
                    ChatDataSkillVersion.skill_version_no == int(skill_version_no or 0),
                    ChatDataSkillVersion.delete_flag == 0,
                )
                .values(version_publish_status="published", update_by=update_by or "", update_by_name=update_by_name or "")
            )
        await self.update_skill_async(skill_id, {
            "skill_publish_status": "published",
            "current_skill_version_no": int(skill_version_no or 0),
            "enabled_flag": 1,
        }, update_by=update_by, update_by_name=update_by_name)

    async def activate_version_async(self, skill_id: str, skill_version_no: int, update_by: str = "", update_by_name: str = "") -> None:
        await self.update_skill_async(skill_id, {
            "skill_publish_status": "published",
            "current_skill_version_no": int(skill_version_no or 0),
            "enabled_flag": 1,
        }, update_by=update_by, update_by_name=update_by_name)

    async def delete_skill_async(self, skill_id: str, deleted_skill_name: str, deleted_root_dir: str, update_by: str = "", update_by_name: str = "") -> int:
        """逻辑删除技能并释放唯一技能名。"""
        stmt = (
            update(ChatDataSkill)
            .where(ChatDataSkill.skill_id == skill_id, ChatDataSkill.delete_flag == 0)
            .values(
                skill_name=deleted_skill_name,
                skill_publish_status="disabled",
                enabled_flag=0,
                delete_flag=1,
                skill_root_dir=deleted_root_dir,
                update_by=update_by or "",
                update_by_name=update_by_name or "",
            )
        )
        async with get_session() as db:
            result = await db.execute(stmt)
            return int(result.rowcount or 0)

    async def update_version_dir_async(self, skill_id: str, skill_version_no: int, version_dir: str, update_by: str = "", update_by_name: str = "") -> int:
        """更新技能版本目录。"""
        stmt = (
            update(ChatDataSkillVersion)
            .where(
                ChatDataSkillVersion.skill_id == skill_id,
                ChatDataSkillVersion.skill_version_no == int(skill_version_no or 0),
                ChatDataSkillVersion.delete_flag == 0,
            )
            .values(skill_version_dir=version_dir, update_by=update_by or "", update_by_name=update_by_name or "")
        )
        async with get_session() as db:
            result = await db.execute(stmt)
            return int(result.rowcount or 0)

    async def set_favorite_async(self, favorite_user_id: str, skill_id: str, favorite: bool, operator: str = "", operator_name: str = "") -> None:
        async with get_session() as db:
            stmt = select(ChatDataSkillFavorite).where(
                ChatDataSkillFavorite.favorite_user_id == favorite_user_id,
                ChatDataSkillFavorite.skill_id == skill_id,
            ).limit(1)
            row = (await db.execute(stmt)).scalars().first()
            if favorite:
                if row:
                    row.delete_flag = 0
                    row.update_by = operator or ""
                    row.update_by_name = operator_name or ""
                    return
                db.add(ChatDataSkillFavorite(
                    favorite_user_id=favorite_user_id,
                    skill_id=skill_id,
                    create_by=operator or "",
                    create_by_name=operator_name or "",
                    update_by=operator or "",
                    update_by_name=operator_name or "",
                ))
                return
            if row:
                row.delete_flag = 1
                row.update_by = operator or ""
                row.update_by_name = operator_name or ""
