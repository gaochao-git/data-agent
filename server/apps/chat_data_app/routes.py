"""ChatData API 路由聚合入口。"""

from fastapi import APIRouter

from .services.datasource_manager import get_datasource_manager
from .services.sql_executor import fetch_http_query_task_result, stop_http_query_task, kill_mysql_query
from .services.query_history_service import QueryHistoryService
from .services.query_task_service import QueryTaskService
from .daos.query_history_dao import QueryHistoryDao
from .daos.query_favorite_dao import QueryFavoriteDao
from .services.query_favorite_service import QueryFavoriteService
from utils.database import get_session
from utils.logger import get_logger
from .routers.dependencies import _get_current_user
from .routers.config import router as config_router
from .routers.conversations import router as conversations_router
from .routers.datasource_admin import router as datasource_admin_router
from .routers.datasource_public import router as datasource_public_router
from .routers.evaluation import router as evaluation_router
from .routers.data_analyze_workspace import router as data_analyze_workspace_router
from .routers.skills import router as skills_router
from .routers.dashboard import router as dashboard_router
from .routers.semantic_config import router as semantic_config_router
from .routers.uploaded_files import router as uploaded_files_router
from .routers.query_favorites import (configure_query_favorite_router,router as query_favorites_router)
from .routers.query_history import (configure_query_history_router,router as query_history_router)
from .routers.query_execution import (configure_query_execution_router,router as query_execution_router)

logger = get_logger(__name__)
AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
query_history_service = QueryHistoryService(QueryHistoryDao(session_context=lambda: get_session()))
query_task_service = QueryTaskService(
    query_history_service=query_history_service,
    datasource_manager_factory=lambda: get_datasource_manager(),
    fetch_http_query_task_result=lambda *args, **kwargs: fetch_http_query_task_result(*args, **kwargs),
    stop_http_query_task=lambda *args, **kwargs: stop_http_query_task(*args, **kwargs),
    kill_mysql_query=lambda *args, **kwargs: kill_mysql_query(*args, **kwargs),
)
query_task_service.configure_dashboard_runtime()
query_favorite_service = QueryFavoriteService(QueryFavoriteDao(session_context=lambda: get_session()))
configure_query_history_router(
    query_history_service=query_history_service,
    query_task_service=query_task_service,
    fetch_remote_result=lambda *args, **kwargs: fetch_http_query_task_result(*args, **kwargs),
    current_user_getter=lambda request: _get_current_user(request),
)
configure_query_favorite_router(
    query_favorite_service=query_favorite_service,
    current_user_getter=lambda request: _get_current_user(request),
)
configure_query_execution_router(
    query_task_service=query_task_service,
    current_user_getter=lambda request: _get_current_user(request),
    sql_assist_agent_id=SQL_ASSIST_AGENT_ID,
)

# 创建路由器
router = APIRouter(prefix="/api/chatdata",tags=["ChatData NL2SQL"])
router.include_router(conversations_router)
router.include_router(config_router)
router.include_router(evaluation_router)
router.include_router(data_analyze_workspace_router)
router.include_router(skills_router)
router.include_router(dashboard_router)
router.include_router(semantic_config_router)
router.include_router(datasource_public_router)
router.include_router(datasource_admin_router)
router.include_router(uploaded_files_router)
router.include_router(query_history_router)
router.include_router(query_favorites_router)
router.include_router(query_execution_router)
