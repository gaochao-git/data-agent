"""查询收藏业务服务。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from ...daos.query_favorite_dao import QueryFavoriteDao
from ...schema import QueryFavoriteCreateRequest, QueryFavoriteUpdateRequest


class QueryFavoriteNotFoundError(ValueError):
    """查询收藏不存在。"""


class QueryFavoriteOperationNotAllowedError(ValueError):
    """查询收藏操作不允许。"""


class QueryFavoriteValidationError(ValueError):
    """查询收藏参数不合法。"""


def _dump_builder_config(builder_config: Optional[dict]) -> Optional[str]:
    if builder_config is None:
        return None
    return json.dumps(builder_config, ensure_ascii=False)


class QueryFavoriteService:
    """查询收藏列表、创建、更新和删除编排。"""

    def __init__(self, dao: Optional[QueryFavoriteDao] = None):
        self.dao = dao or QueryFavoriteDao()

    async def list_favorites(
        self,
        *,
        datasource_id: str,
        user_id: str,
        query_type: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        favorites = await self.dao.list_favorites(
            datasource_id=datasource_id,
            user_id=user_id,
            query_type=query_type,
            limit=limit,
        )
        results = [item.to_dict() for item in favorites]
        return {"queries": results, "total": len(results)}

    async def create_favorite(
        self,
        payload: QueryFavoriteCreateRequest,
        *,
        user_id: str,
        username: str,
    ) -> Dict[str, Any]:
        if payload.query_type == "builder" and payload.builder_config is None:
            raise QueryFavoriteValidationError("builder 类型收藏必须包含 builder_config")
        item = await self.dao.create_favorite(
            datasource_id=payload.datasource_id,
            user_id=user_id,
            username=username,
            semantic_config_id=payload.semantic_config_id,
            query_type=payload.query_type,
            favorite_title=payload.favorite_title.strip(),
            query_text=payload.query_text,
            builder_config=_dump_builder_config(payload.builder_config),
            now=datetime.now(),
        )
        return item.to_dict()

    async def update_favorite(
        self,
        favorite_id: int,
        payload: QueryFavoriteUpdateRequest,
        *,
        user_id: str,
        username: str,
    ) -> Dict[str, Any]:
        current = await self.dao.get_user_favorite(favorite_id=favorite_id, user_id=user_id)
        if current is None:
            raise QueryFavoriteNotFoundError("收藏不存在")

        if current.query_type == "builder" and (payload.query_text is not None or payload.builder_config is not None):
            raise QueryFavoriteOperationNotAllowedError("builder 收藏不支持编辑原始内容")

        item = await self.dao.update_favorite(
            favorite_id=favorite_id,
            user_id=user_id,
            username=username,
            favorite_title=(payload.favorite_title.strip() if payload.favorite_title is not None else None),
            query_text=(payload.query_text if current.query_type != "builder" else None),
            builder_config=(_dump_builder_config(payload.builder_config) if current.query_type != "builder" else None),
            semantic_config_id=payload.semantic_config_id,
            now=datetime.now(),
        )
        if item is None:
            raise QueryFavoriteNotFoundError("收藏不存在")
        return item.to_dict()

    async def delete_favorite(self, *, favorite_id: int, user_id: str, username: str) -> Dict[str, Any]:
        deleted = await self.dao.soft_delete_favorite(
            favorite_id=favorite_id,
            user_id=user_id,
            username=username,
            now=datetime.now(),
        )
        if not deleted:
            raise QueryFavoriteNotFoundError("收藏不存在")
        return {"id": favorite_id}
