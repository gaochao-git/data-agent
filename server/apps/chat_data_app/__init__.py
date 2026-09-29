"""
Chat Data App - 对话式数据查询

提供自然语言到SQL的转换服务
"""

import traceback

# 代理标识
AGENT_ID = "chat_data"
router = None


def _init_app() -> None:
    """初始化路由与对话注册。"""
    global router
    try:
        from .agent import get_data_agent_graph
        from .agent_stream import sql_completion_handler
        from .routers.conversations import (
            register_agent,
            register_completion_handler,
        )
        from .routes import router as app_router
    except Exception:
        traceback.print_exc()
        raise

    router = app_router
    register_agent(
        agent_id=AGENT_ID,
        get_graph=lambda checkpointer=None: get_data_agent_graph(checkpointer=checkpointer),
    )
    register_completion_handler(AGENT_ID, sql_completion_handler)


_init_app()

__all__ = ["router", "AGENT_ID"]
