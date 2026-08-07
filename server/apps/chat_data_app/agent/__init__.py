"""
ChatData 单一 Agent

标准 LangGraph 目录结构:
- data_agent.py: 单一智能体定义
- runtime_options.py: 工作区后端和 Bash 运行选项
- agent_runtime.py: 共享运行时工具
- prompts/chat_data.py: ChatData system prompt 构建
- middleware/: create_agent middleware
- prompts/: 提示词包
- tools/: 工具包

ChatData 只有一个 Agent；运行选项只决定工作区后端及 Bash 是否注册。
"""

from .agent_state import ChatDataAgentState
from .runtime_options import (
    AgentRuntimeOptions,
    get_chat_data_default_runtime_options,
    resolve_agent_runtime_options,
)
from .data_agent import (
    create_data_agent,
    get_data_agent_graph,
)
from .prompts.nl2sql import SQL_GENERATOR_PROMPT_GENERATE
from .prompts.chat_data import build_unified_system_prompt
from utils.checkpoint import get_checkpointer, DB_PATH

__all__ = [
    "ChatDataAgentState",
    "AgentRuntimeOptions",
    "get_chat_data_default_runtime_options",
    "resolve_agent_runtime_options",
    "create_data_agent",
    "get_data_agent_graph",
    "build_unified_system_prompt",
    "get_checkpointer",
    "DB_PATH",
    "SQL_GENERATOR_PROMPT_GENERATE",
]
