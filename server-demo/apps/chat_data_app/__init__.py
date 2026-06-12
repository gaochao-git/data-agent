"""
Chat Data App - 对话式数据查询

提供自然语言到SQL的转换服务
"""

# 代理标识
AGENT_ID = "chat_data"
router = None


def _safe_init_app() -> None:
    """按需初始化路由与对话注册，避免测试导入时强依赖完整运行环境。"""
    global router
    try:
        from .routes import router as app_router
        from .agent import get_graph
        from .handlers import sql_completion_handler, sql_resume_handler
        from utils.lang_util.conversation_router import (
            register_agent,
            register_completion_handler,
            register_resume_handler,
        )
    except Exception:
        return

    router = app_router
    register_agent(agent_id=AGENT_ID, get_graph=get_graph)
    register_completion_handler(AGENT_ID, sql_completion_handler)
    register_resume_handler(AGENT_ID, sql_resume_handler)


_safe_init_app()

__all__ = ["router", "AGENT_ID"]
