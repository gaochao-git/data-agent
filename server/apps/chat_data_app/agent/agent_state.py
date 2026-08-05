"""ChatData 单一 Agent 的运行态定义。"""

from typing import Annotated, Any

from langchain.agents import AgentState
from typing_extensions import TypedDict


def merge_reasoning_segments(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """追加模型原生思考分段，供历史会话恢复。"""
    return list(existing or []) + list(new or [])


def merge_context_compression_events(
    existing: list[dict] | None,
    new: list[dict] | None,
) -> list[dict]:
    """追加压缩分界事件，并按稳定 ID 去重以支持流重试。"""
    merged: list[dict] = []
    event_ids: set[str] = set()
    for event in [*(existing or []), *(new or [])]:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id or event_id in event_ids:
            continue
        event_ids.add(event_id)
        merged.append(event)
    return merged


class ExecutionContext(TypedDict, total=False):
    """工具执行所需的数据源和执行对象范围。"""

    datasource_id: str | None
    available_member_datasource_ids: list[str]
    matched_member_datasource_ids: list[str]


class ConversationWorkspaceContext(TypedDict, total=False):
    """当前会话工作区；只保存可序列化的配置。"""

    workspace_root: str
    workspace_backend: str
    snapshot_id: str


class ChatDataAgentState(AgentState, total=False):
    """唯一 ChatData Agent 状态。"""

    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    execution_context: ExecutionContext
    token_usage: dict[str, Any] | None
    context_compression: dict[str, Any] | None
    context_summary: str | None
    context_compression_events: Annotated[list[dict[str, Any]], merge_context_compression_events]
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
    workspace: ConversationWorkspaceContext
