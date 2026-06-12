"""
LangChain/LangGraph 公共工具模块

提供：
- llm: LLM 实例获取
- checkpoint: Checkpointer 配置（DB_PATH, get_checkpointer）
- database: SQLAlchemy 数据库管理（get_session, init_database）
- models: 数据模型（UserThread, UploadedFile）
- conversation: 会话管理（增删改查等）
- conversation_router: 会话 API 路由
"""

from .llm import get_llm
from .checkpoint import DB_PATH, get_checkpointer, setup_checkpointer
from .database import get_session, init_database, get_database_info
from .models import UserThread, UploadedFile
from .conversation import (
    get_or_create_conversation,
    create,
    list,
    get_detail,
    delete,
    update_title,
    generate_title,
    toggle_favorite,
    get_agent_id,
    serialize_messages,
)
from .conversation_router import (
    router,
    register_agent,
    register_completion_handler,
)

__all__ = [
    # llm
    "get_llm",
    # checkpoint
    "DB_PATH",
    "get_checkpointer",
    "setup_checkpointer",
    # database
    "get_session",
    "init_database",
    "get_database_info",
    # models
    "UserThread",
    "UploadedFile",
    # conversation
    "get_or_create_conversation",
    "create",
    "list",
    "get_detail",
    "delete",
    "update_title",
    "generate_title",
    "toggle_favorite",
    "get_agent_id",
    "serialize_messages",
    # conversation_router
    "router",
    "register_agent",
    "register_completion_handler",
]
