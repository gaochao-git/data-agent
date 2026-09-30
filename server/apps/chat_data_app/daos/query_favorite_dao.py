"""查询收藏数据访问层。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select

from utils.database import get_session as default_get_session
from utils.models import SqlQueryFavorite


class QueryFavoriteDao:
    """封装查询收藏相关 ORM 读写。"""

    def __init__(self, session_context: Callable | None = None) -> None:
        self._session_context = session_context or default_get_session

    async def list_favorites(
        self,
        *,
        datasource_id: str,
        user_id: str,
        query_type: str | None,
        limit: int,
    ) -> list[SqlQueryFavorite]:
        async with self._session_context() as db:
            stmt = select(SqlQueryFavorite).filter_by(datasource_id=datasource_id, user_id=user_id, delete_flag=0)
            if query_type:
                stmt = stmt.filter(SqlQueryFavorite.query_type == query_type)
            stmt = stmt.order_by(SqlQueryFavorite.update_time.desc(), SqlQueryFavorite.create_time.desc()).limit(limit)
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def create_favorite(
        self,
        *,
        datasource_id: str,
        user_id: str,
        username: str,
        semantic_config_id: str | None,
        query_type: str,
        favorite_title: str,
        query_text: str,
        builder_config: str | None,
        now: datetime,
    ) -> SqlQueryFavorite:
        async with self._session_context() as db:
            item = SqlQueryFavorite(
                datasource_id=datasource_id,
                user_id=user_id,
                semantic_config_id=semantic_config_id,
                query_type=query_type,
                favorite_title=favorite_title,
                query_text=query_text,
                builder_config=builder_config,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=username,
                update_by=user_id,
                update_by_name=username,
            )
            db.add(item)
            await db.flush()
            return item

    async def get_user_favorite(self, *, favorite_id: int, user_id: str) -> SqlQueryFavorite | None:
        async with self._session_context() as db:
            result = await db.execute(
                select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            )
            return result.scalar_one_or_none()

    async def update_favorite(
        self,
        *,
        favorite_id: int,
        user_id: str,
        username: str,
        favorite_title: str | None = None,
        query_text: str | None = None,
        builder_config: str | None = None,
        semantic_config_id: str | None = None,
        now: datetime | None = None,
    ) -> SqlQueryFavorite | None:
        async with self._session_context() as db:
            result = await db.execute(
                select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            )
            item = result.scalar_one_or_none()
            if item is None:
                return None
            if favorite_title is not None:
                item.favorite_title = favorite_title
            if query_text is not None:
                item.query_text = query_text
            if builder_config is not None:
                item.builder_config = builder_config
            if semantic_config_id is not None:
                item.semantic_config_id = semantic_config_id
            item.update_time = now or datetime.now()
            item.update_by = user_id
            item.update_by_name = username
            await db.flush()
            return item

    async def soft_delete_favorite(
        self,
        *,
        favorite_id: int,
        user_id: str,
        username: str,
        now: datetime | None = None,
    ) -> bool:
        async with self._session_context() as db:
            result = await db.execute(
                select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            )
            item = result.scalar_one_or_none()
            if item is None:
                return False
            item.delete_flag = 1
            item.update_time = now or datetime.now()
            item.update_by = user_id
            item.update_by_name = username
            await db.flush()
            return True
