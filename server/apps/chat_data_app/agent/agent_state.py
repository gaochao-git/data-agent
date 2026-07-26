"""ChatData 单一 Agent 的运行态定义。"""

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


class ConversationWorkspaceContext(TypedDict, total=False):
    """当前会话工作区；只保存可序列化的配置。"""

    workspace_root: str
    workspace_backend: str


class ChatDataAgentState(AgentState, total=False):
    """唯一 ChatData Agent 状态。"""

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
    workspace: ConversationWorkspaceContext
