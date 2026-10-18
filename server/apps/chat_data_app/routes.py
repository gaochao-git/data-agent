"""ChatData API 路由聚合入口。"""

from fastapi import APIRouter, Depends, HTTPException

from auth import AuthenticationError, service_token_header, validate_service_token
from utils.logger import get_logger

from .routers.agent_schedule_admin import router as agent_schedule_admin_router
from .routers.agent_schedules import router as agent_schedules_router
from .routers.config import router as config_router
from .routers.conversation_workspace import router as conversation_workspace_router
from .routers.conversations import router as conversations_router
from .routers.dashboard import router as dashboard_router
from .routers.datasource_admin import router as datasource_admin_router
from .routers.datasource_public import router as datasource_public_router
from .routers.evaluation import router as evaluation_router
from .routers.meta_admin import router as meta_admin_router
from .routers.model_admin import router as model_admin_router
from .routers.plugins import router as plugins_router
from .routers.query_execution import router as query_execution_router
from .routers.query_favorites import router as query_favorites_router
from .routers.query_history import router as query_history_router
from .routers.semantic_config import router as semantic_config_router
from .routers.setting_admin import router as setting_admin_router
from .routers.skills import router as skills_router
from .routers.uploaded_files import router as uploaded_files_router

logger = get_logger(__name__)
AGENT_ID = "chat_data"


# 创建路由器
async def require_service_token(service_token: str | None = Depends(service_token_header)) -> None:
    """统一保护 ChatData service 接口，避免路由遗漏 service token 校验。"""
    try:
        validate_service_token(service_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


router = APIRouter(
    prefix="/api/chatdata",
    dependencies=[Depends(require_service_token)],
)
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
router.include_router(agent_schedules_router)
router.include_router(agent_schedule_admin_router)
router.include_router(plugins_router)
router.include_router(setting_admin_router)
router.include_router(meta_admin_router)
router.include_router(model_admin_router)
