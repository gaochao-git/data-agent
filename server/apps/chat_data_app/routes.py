"""ChatData API 路由聚合入口。"""

from fastapi import APIRouter
from utils.logger import get_logger
from .routers.config import router as config_router
from .routers.conversations import router as conversations_router
from .routers.datasource_admin import router as datasource_admin_router
from .routers.datasource_public import router as datasource_public_router
from .routers.evaluation import router as evaluation_router
from .routers.conversation_workspace import router as conversation_workspace_router
from .routers.skills import router as skills_router
from .routers.dashboard import router as dashboard_router
from .routers.semantic_config import router as semantic_config_router
from .routers.uploaded_files import router as uploaded_files_router
from .routers.query_favorites import router as query_favorites_router
from .routers.query_history import router as query_history_router
from .routers.query_execution import router as query_execution_router

logger = get_logger(__name__)
AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"

# 创建路由器
router = APIRouter(prefix="/api/chatdata")
router.include_router(conversations_router)
router.include_router(config_router)
router.include_router(evaluation_router)
router.include_router(conversation_workspace_router)
router.include_router(skills_router)
router.include_router(dashboard_router)
router.include_router(semantic_config_router)
router.include_router(datasource_public_router)
router.include_router(datasource_admin_router)
router.include_router(uploaded_files_router)
router.include_router(query_history_router)
router.include_router(query_favorites_router)
router.include_router(query_execution_router)
