"""SQL 编辑器 AI 辅助 Agent。"""

from langchain.agents import AgentState, create_agent

from .agent_runtime import format_current_time_for_prompt
from .prompts.nl2sql import SQL_GENERATOR_PROMPT_GENERATE
from ..services.llm_config import get_chat_data_llm


class SqlAssistState(AgentState, total=False):
    """SQL 助手只保存生成 SQL 必需的上下文。"""

    tables_ddl_info: str


def build_sql_assist_system_prompt(*, sql_dialect: str, tables_ddl_info: str) -> str:
    """构造 SQL 助手 system prompt。"""
    return SQL_GENERATOR_PROMPT_GENERATE.format(
        current_time=format_current_time_for_prompt(),
        tables_ddl_info=tables_ddl_info or "",
        sql_dialect=sql_dialect or "MySQL",
    )


def create_sql_assist_agent(
    checkpointer=None,
    *,
    sql_dialect: str = "MySQL",
    tables_ddl_info: str = "",
):
    """创建只生成 SQL 的轻量 create_agent。"""
    return create_agent(
        model=get_chat_data_llm(streaming=True),
        tools=[],
        system_prompt=build_sql_assist_system_prompt(
            sql_dialect=sql_dialect,
            tables_ddl_info=tables_ddl_info,
        ),
        state_schema=SqlAssistState,
        checkpointer=checkpointer,
        name="sql_assist_agent",
    )
