"""SQL 查询相关 service 依赖提供器。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from utils.database import get_session

from ...daos.query_favorite_dao import QueryFavoriteDao
from ...daos.query_history_dao import QueryHistoryDao
from ..datasource.manager import get_datasource_manager
from .favorite_service import QueryFavoriteService
from .history_service import QueryHistoryService
from .task_service import QueryTaskService
from .executor import fetch_http_query_task_result, kill_mysql_query, stop_http_query_task

query_history_service = QueryHistoryService(QueryHistoryDao(session_context=lambda: get_session()))
query_task_service = QueryTaskService(
    query_history_service=query_history_service,
    datasource_manager_factory=lambda: get_datasource_manager(),
    fetch_http_query_task_result=fetch_http_query_task_result,
    stop_http_query_task=stop_http_query_task,
    kill_mysql_query=kill_mysql_query,
)
query_task_service.configure_dashboard_runtime()
query_favorite_service = QueryFavoriteService(QueryFavoriteDao(session_context=lambda: get_session()))


def get_query_history_service() -> QueryHistoryService:
    """获取查询历史 service。"""
    return query_history_service


def get_query_task_service() -> QueryTaskService:
    """获取查询任务 service。"""
    return query_task_service


def get_query_favorite_service() -> QueryFavoriteService:
    """获取查询收藏 service。"""
    return query_favorite_service


def get_query_history_remote_result_fetcher() -> Callable[..., Awaitable[Dict[str, Any]]]:
    """获取查询历史远端结果读取函数。"""
    return fetch_http_query_task_result
