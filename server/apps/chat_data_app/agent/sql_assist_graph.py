"""SQL 编辑器 AI 辅助专用图。"""

from datetime import datetime
from typing import Any

from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START

from .state import SqlGeneratorState
from .prompts.nl2sql import SQL_GENERATOR_PROMPT_GENERATE
from ..services.llm_config import get_chat_data_llm


def create_sql_assist_node(sql_dialect: str):
    """创建只生成 SQL 的轻量节点。"""
    async def sql_assist_node(state: SqlGeneratorState) -> dict[str, Any]:
        system_prompt = SQL_GENERATOR_PROMPT_GENERATE.format(
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tables_ddl_info=state.get("tables_ddl_info") or "",
            sql_dialect=sql_dialect,
        )
        llm = get_chat_data_llm(streaming=True)
        response = await llm.ainvoke([SystemMessage(content=system_prompt)] + list(state["messages"]))
        return {"messages": [response]}

    return sql_assist_node


def get_sql_assist_graph(checkpointer=None, sql_dialect: str = "MySQL"):
    """编译 SQL 辅助专用图。"""
    builder = StateGraph(SqlGeneratorState)
    builder.add_node("sql_assist", create_sql_assist_node(sql_dialect))
    builder.add_edge(START, "sql_assist")
    return builder.compile(checkpointer=checkpointer)
