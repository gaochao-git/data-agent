"""SQL 查询结果缓存数据访问层。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from sqlalchemy import select

from utils.database import get_session as default_get_session
from utils.logger import get_logger
from utils.models import ChatDataSqlResultCache

logger = get_logger(__name__)


def _apply_cache_item_update(
    cache_item: ChatDataSqlResultCache,
    identity_fields: Dict[str, str],
    sql_text: str,
    payload_json: str,
    result_row_count: int,
    *,
    now: datetime,
    expire_time: datetime,
    operator_user_id: str,
    operator_username: str,
) -> None:
    """已存在的缓存行：原地覆盖身份、结果与租期。"""
    cache_item.datasource_id = identity_fields["datasource_id"]
    cache_item.member_datasource_id = identity_fields["member_datasource_id"]
    cache_item.executor_type = identity_fields["executor_type"]
    cache_item.source_type = identity_fields["source_type"]
    cache_item.sql_hash = identity_fields["sql_hash"]
    cache_item.sql_text = sql_text
    cache_item.result_payload = payload_json
    cache_item.result_row_count = int(result_row_count or 0)
    cache_item.expire_time = expire_time
    cache_item.delete_flag = 0
    cache_item.update_time = now
    cache_item.update_by = operator_user_id
    cache_item.update_by_name = operator_username


class SqlResultCacheDao:
    """封装 chat_data_sql_result_cache 表读写。"""

    def __init__(self, session_context: Optional[Callable] = None):
        self._session_context = session_context or default_get_session

    async def read_and_touch_cache(
        self,
        cache_key: str,
        *,
        now: datetime,
    ) -> Optional[Tuple[ChatDataSqlResultCache, Optional[Dict[str, Any]]]]:
        """读取未过期缓存并累计命中计数，返回 (缓存行, payload 字典)。

        payload 为 None 表示该行 JSON 解析失败，已就地作废（标记删除），
        本次按未命中处理。提交由 get_session 退出自动完成。
        """
        async with self._session_context() as db:
            result = await db.execute(
                select(ChatDataSqlResultCache)
                .filter_by(cache_key=cache_key, delete_flag=0)
                .where(ChatDataSqlResultCache.expire_time > now)
            )
            cache_item = result.scalar_one_or_none()
            if not cache_item:
                return None
            try:
                payload = json.loads(cache_item.result_payload or "{}")
            except Exception as err:
                logger.warning("SQL 结果缓存解析失败: cache_key=%s error=%s", cache_key, err)
                cache_item.delete_flag = 1
                cache_item.update_time = now
                return cache_item, None
            cache_item.hit_count = int(cache_item.hit_count or 0) + 1
            cache_item.last_hit_time = now
            cache_item.update_time = now
            return cache_item, payload

    async def upsert_cache(
        self,
        *,
        cache_key: str,
        identity_fields: Dict[str, str],
        sql_text: str,
        payload_json: str,
        result_row_count: int,
        now: datetime,
        expire_time: datetime,
        operator_user_id: str,
        operator_name: str,
    ) -> None:
        """按缓存键 upsert 结果负载；已存在的行原地覆盖，不存在则新建。"""
        async with self._session_context() as db:
            db_result = await db.execute(select(ChatDataSqlResultCache).filter_by(cache_key=cache_key))
            cache_item = db_result.scalar_one_or_none()
            if cache_item:
                _apply_cache_item_update(
                    cache_item,
                    identity_fields,
                    sql_text,
                    payload_json,
                    result_row_count,
                    now=now,
                    expire_time=expire_time,
                    operator_user_id=operator_user_id,
                    operator_username=operator_name,
                )
            else:
                db.add(
                    ChatDataSqlResultCache(
                        cache_key=cache_key,
                        **identity_fields,
                        sql_text=sql_text,
                        result_payload=payload_json,
                        result_row_count=result_row_count,
                        expire_time=expire_time,
                        hit_count=0,
                        delete_flag=0,
                        create_time=now,
                        update_time=now,
                        create_by=operator_user_id,
                        create_by_name=operator_name,
                        update_by=operator_user_id,
                        update_by_name=operator_name,
                    )
                )
