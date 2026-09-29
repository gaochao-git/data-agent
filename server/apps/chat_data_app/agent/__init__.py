"""
ChatData 单一 Agent

目录结构与职责详见本目录 README.md：
- data_agent.py: 单一智能体装配（create_data_agent / get_data_agent_graph）
- runtime_options.py: 工作区后端（local/sandbox）与 Bash 注册开关
- agent_state.py / agent_runtime.py / tool_results.py: 状态、运行时工具、工具结果结构
- tools/ middleware/ prompts/ workspace/ run/ skills/ context/: 各子系统（各有 README）

ChatData 只有一个 Agent；运行选项只决定工作区后端及 Bash 是否注册。
"""

from utils.checkpoint import DB_PATH, get_checkpointer

from .agent_state import ChatDataAgentState
from .data_agent import (
    CHAT_DATA_AGENT_SPEC,
    AgentAssemblySpec,
    AgentRunControls,
    DatasourceContext,
    IdentityContext,
    ModelInvocationConfig,
    create_agent_from_spec,
    create_data_agent,
    get_data_agent_graph,
    validate_agent_assembly_spec,
)
from .prompts.chat_data import build_unified_system_prompt
from .runtime_options import (
    AgentRuntimeOptions,
    get_chat_data_default_runtime_options,
    get_subagent_hot_defaults,
    resolve_agent_runtime_options,
)

__all__ = [
    "ChatDataAgentState",
    "AgentRuntimeOptions",
    "AgentAssemblySpec",
    "AgentRunControls",
    "CHAT_DATA_AGENT_SPEC",
    "DatasourceContext",
    "IdentityContext",
    "ModelInvocationConfig",
    "get_chat_data_default_runtime_options",
    "get_subagent_hot_defaults",
    "resolve_agent_runtime_options",
    "create_agent_from_spec",
    "create_data_agent",
    "get_data_agent_graph",
    "validate_agent_assembly_spec",
    "build_unified_system_prompt",
    "get_checkpointer",
    "DB_PATH",
]
