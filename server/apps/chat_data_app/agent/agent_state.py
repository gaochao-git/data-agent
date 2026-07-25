"""ChatData Agent 运行态定义。

两个 Agent 的状态字段、Reducer 和可序列化执行上下文统一维护在此处，
避免工具和编排层依赖某一个具体 Agent 模块。
"""

from typing import Annotated, Any

from langchain.agents import AgentState
from typing_extensions import TypedDict


def merge_reasoning_segments(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """追加模型原生思考分段，供历史会话恢复。"""
    return list(existing or []) + list(new or [])


def keep_conversation_datasource_id(existing: str | None, new: str | None) -> str | None:
    """数据源组首次绑定后不可被后续轮次覆盖。"""
    return existing or new


class ExecutionContext(TypedDict, total=False):
    """工具执行所需的数据源和执行对象范围。"""

    datasource_id: str | None
    available_member_datasource_ids: list[str]
    matched_member_datasource_ids: list[str]


class AnalysisWorkspaceContext(TypedDict, total=False):
    """智能分析专属工作区能力；只保存可序列化的路径标识。"""

    workspace_root: str


class ChatDataAgentState(AgentState, total=False):
    """智能问数 Agent 状态。"""

    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    conversation_datasource_id: Annotated[str | None, keep_conversation_datasource_id]
    execution_context: ExecutionContext
    semantic_config_id: str | None
    # 临时指标集不一定持久化到业务库，语义查询工具需要从状态读取它。
    semantic_config: dict[str, Any] | None
    token_usage: dict[str, Any] | None
    context_compression: dict[str, Any] | None
    context_summary: str | None
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]


class DataAnalyzeAgentState(AgentState, total=False):
    """智能分析 Agent 状态。"""

    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    conversation_datasource_id: Annotated[str | None, keep_conversation_datasource_id]
    execution_context: ExecutionContext
    semantic_config_id: str | None
    token_usage: dict[str, Any] | None
    context_compression: dict[str, Any] | None
    context_summary: str | None
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
    analysis_workspace: AnalysisWorkspaceContext
