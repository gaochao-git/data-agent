"""
Chat Data App - 对话式数据查询

提供自然语言到SQL的转换服务
"""

import traceback

# 代理标识
AGENT_ID = "chat_data"
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
router = None


def _init_app() -> None:
    """初始化路由与对话注册。"""
    global router
    try:
        from .routes import router as app_router
        from .agent import get_chat_data_graph
        from .agent.data_analyze_graph import get_data_analyze_graph
        from .agent.sql_assist_graph import get_sql_assist_graph
        from .handlers import data_analyze_completion_handler, sql_completion_handler
        from .services.feature_gate import get_data_analyze_availability
        from utils.lang_util.conversation_router import (
            register_agent,
            register_agent_availability_checker,
            register_completion_handler,
        )
    except Exception:
        traceback.print_exc()
        raise

    router = app_router
    register_agent(agent_id=AGENT_ID, get_graph=get_chat_data_graph)
    register_agent(agent_id=SQL_ASSIST_AGENT_ID, get_graph=get_sql_assist_graph)
    register_agent(agent_id=DATA_ANALYZE_AGENT_ID, get_graph=get_data_analyze_graph)
    register_agent_availability_checker(
        DATA_ANALYZE_AGENT_ID,
        get_data_analyze_availability,
    )
    register_completion_handler(AGENT_ID, sql_completion_handler)
    register_completion_handler(DATA_ANALYZE_AGENT_ID, data_analyze_completion_handler)


_init_app()

__all__ = ["router", "AGENT_ID", "SQL_ASSIST_AGENT_ID", "DATA_ANALYZE_AGENT_ID"]
