"""
SQL Generator Agent

标准 LangGraph 目录结构:
- state.py: 状态定义和 reducer
- nodes.py: 节点函数
- graph.py: 图结构定义
- prompts.py: 提示词管理
- tools.py: 工具函数
"""

from .state import SqlGeneratorState
from .graph import get_graph
from .nodes import agent_node, create_agent_node
from .prompts import TABLE_SELECTOR_PROMPT, SQL_GENERATOR_PROMPT_GENERATE
from utils.lang_util.checkpoint import get_checkpointer, DB_PATH
from .tools import (
    get_all_tables,
    get_tables_name_comment,
    get_tables_create_format,
    llm_select_tables,
)

__all__ = [
    "SqlGeneratorState",
    "get_graph",
    "get_checkpointer",
    "agent_node",
    "create_agent_node",
    "DB_PATH",
    "TABLE_SELECTOR_PROMPT",
    "SQL_GENERATOR_PROMPT_GENERATE",
    "get_all_tables",
    "get_tables_name_comment",
    "get_tables_create_format",
    "llm_select_tables",
]
