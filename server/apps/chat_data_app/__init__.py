"""
Chat Data App - 对话式数据查询

提供自然语言到SQL的转换服务
"""

import traceback

# 代理标识
AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
router = None


def _init_app() -> None:
    """初始化路由与对话注册。"""
    global router
    try:
        from .routes import router as app_router
        from .agent import get_data_agent_graph
        from .agent.sql_assist_agent import create_sql_assist_agent
        from .handlers import sql_completion_handler
        from .routers.conversations import (
            register_agent,
            register_completion_handler,
        )
    except Exception:
        traceback.print_exc()
        raise

    router = app_router
    register_agent(
        agent_id=AGENT_ID,
        get_graph=lambda checkpointer=None: get_data_agent_graph(checkpointer=checkpointer),
    )
    register_agent(agent_id=SQL_ASSIST_AGENT_ID, get_graph=create_sql_assist_agent)
    register_completion_handler(AGENT_ID, sql_completion_handler)


_init_app()

__all__ = ["router", "AGENT_ID", "SQL_ASSIST_AGENT_ID"]
