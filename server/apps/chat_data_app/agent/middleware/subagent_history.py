"""后台子智能体多轮会话历史的序列化与状态追踪。

任务账本（``chat_data_agent_inbox``）只保留每轮结果；本模块负责：
- 把子图最终状态压成可持久化的最小消息结构（不写入 trail/用量等附加快照）；
- 从会话快照还原 LangChain 消息，供追问继续执行；
- 按 LangGraph ``add_messages`` 语义追踪每次节点更新后的完整状态消息。

独立成模块是为了控制 ``subagents.py`` 体积；这里不依赖父图运行时。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from ..contracts.subagent_task import SubagentTaskView
from .message_integrity import repair_tool_call_messages

SUBAGENT_HISTORY_MESSAGE_MAX_CHARS = 100_000
SUBAGENT_HISTORY_TOTAL_MAX_CHARS = 2_000_000
SUBAGENT_HISTORY_TRUNCATED_NOTE = "…[更早的会话历史因长度限制已省略]"


def _plain_text(content: Any) -> str:
    """把消息 content 归一化成纯文本（多模态 list 按行拼接）。"""
    if isinstance(content, list):
        return "\n".join(part if isinstance(part, str) else str(part) for part in content)
    return content if isinstance(content, str) else str(content or "")


def _fit_history_text(text: str) -> str:
    """单条历史消息封顶：保留头尾，避免一条超大工具结果把整份快照撑爆。"""
    if len(text) <= SUBAGENT_HISTORY_MESSAGE_MAX_CHARS:
        return text
    head = SUBAGENT_HISTORY_MESSAGE_MAX_CHARS * 2 // 3
    tail = SUBAGENT_HISTORY_MESSAGE_MAX_CHARS - head
    return text[:head] + "\n…[历史消息过长已截断]…\n" + text[-tail:]


def _message_type(message: Any) -> str | None:
    """把 LangChain 消息类映射为快照类型名；未知类型不持久化。"""
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, SystemMessage):
        return "system"
    return None


def _message_extra_payload(message: Any) -> dict[str, Any]:
    """提取消息 id/name、AI tool_calls 与 ToolMessage 调用关联字段。"""
    payload: dict[str, Any] = {}
    message_id = str(getattr(message, "id", "") or "")
    if message_id:
        payload["id"] = message_id
    name = str(getattr(message, "name", "") or "")
    if name:
        payload["name"] = name
    if isinstance(message, AIMessage):
        tool_calls = [dict(call) for call in (message.tool_calls or []) if isinstance(call, dict)]
        if tool_calls:
            payload["tool_calls"] = tool_calls
    if isinstance(message, ToolMessage):
        payload["tool_call_id"] = str(message.tool_call_id or "")
        status = str(getattr(message, "status", "") or "")
        if status:
            payload["status"] = status
    return payload


def _message_payload(message: Any) -> dict[str, Any] | None:
    """把子图消息压成可持久化的最小结构；过程 trail/用量等附加快照不写入历史。"""
    message_type = _message_type(message)
    if message_type is None:
        return None
    return {
        "type": message_type,
        "content": _fit_history_text(_plain_text(message.content)),
        **_message_extra_payload(message),
    }


def serialize_subagent_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """把最终状态消息序列化成会话快照；从旧到新丢弃直到总长度受控。

    超时/取消可能停在工具调用中途，快照末尾会留下没有 ToolMessage 的
    AIMessage.tool_calls。直接续聊会形成非法工具调用组，因此先按主图同源
    规则剔除残缺组；完整组原样保留。
    """
    repaired, _report = repair_tool_call_messages(messages)
    payloads = [payload for message in repaired if (payload := _message_payload(message)) is not None]
    total = sum(len(item.get("content") or "") for item in payloads)
    dropped = False
    while payloads and total > SUBAGENT_HISTORY_TOTAL_MAX_CHARS:
        removed = payloads.pop(0)
        total -= len(removed.get("content") or "")
        dropped = True
    # 丢弃后可能以孤立工具结果/AI 工具轮开头，模型无法配对；继续丢到人类消息。
    while dropped and payloads and payloads[0].get("type") not in {"human", "system"}:
        payloads.pop(0)
    if dropped:
        payloads.insert(0, {"type": "human", "content": SUBAGENT_HISTORY_TRUNCATED_NOTE})
    return payloads


def _deserialize_message(item: dict[str, Any]) -> Any:
    """还原单条消息；未知类型交给调用方跳过。"""
    message_type = str(item.get("type") or "")
    content = str(item.get("content") or "")
    message_id = str(item.get("id") or "") or None
    name = str(item.get("name") or "") or None
    if message_type == "human":
        return HumanMessage(content=content, id=message_id, name=name)
    if message_type == "system":
        return SystemMessage(content=content, id=message_id, name=name)
    if message_type == "ai":
        tool_calls = [call for call in (item.get("tool_calls") or []) if isinstance(call, dict)]
        return AIMessage(content=content, id=message_id, name=name, tool_calls=tool_calls)
    if message_type == "tool":
        return ToolMessage(
            content=content,
            id=message_id,
            name=name,
            tool_call_id=str(item.get("tool_call_id") or ""),
            status=str(item.get("status") or "success"),
        )
    return None


def deserialize_subagent_messages(payloads: Sequence[dict[str, Any]]) -> list[Any]:
    """把会话快照还原成 LangChain 消息；损坏条目跳过，保证续聊可降级。"""
    messages: list[Any] = []
    for item in payloads:
        if not isinstance(item, dict):
            continue
        message = _deserialize_message(item)
        if message is not None:
            messages.append(message)
    return messages


def legacy_session_history(task: SubagentTaskView) -> list[dict[str, Any]]:
    """改造前任务没有快照：用任务书 + 最终报告兜底一轮，至少可继续追问。"""
    history: list[dict[str, Any]] = []
    if task.description:
        history.append({"type": "human", "content": _fit_history_text(task.description)})
    conclusion = task.report or task.error
    if conclusion:
        history.append({"type": "ai", "content": _fit_history_text(conclusion)})
    return history


def _message_identity(message: Any) -> str:
    value = getattr(message, "id", None)
    return str(value) if value else ""


def _resets_state(updates: list[Any]) -> bool:
    return any(
        isinstance(message, RemoveMessage) and getattr(message, "id", None) == REMOVE_ALL_MESSAGES
        for message in updates
    )


def _removed_message_ids(updates: list[Any]) -> set[str]:
    return {
        str(message.id)
        for message in updates
        if isinstance(message, RemoveMessage) and getattr(message, "id", None) != REMOVE_ALL_MESSAGES
    }


def _upsert_state_message(result: list[Any], message: Any) -> None:
    """追加消息；带 id 时按 add_messages 语义覆盖同 id 旧值。"""
    if isinstance(message, (RemoveMessage, dict)):
        return
    identity = _message_identity(message)
    if not identity:
        result.append(message)
        return
    for index, existing in enumerate(result):
        if _message_identity(existing) == identity:
            result[index] = message
            return
    result.append(message)


def apply_state_message_update(current: list[Any], updates: list[Any]) -> list[Any]:
    """按 add_messages reducer 语义应用一次节点更新。

    只覆盖子图实际使用的形态：``RemoveMessage(REMOVE_ALL_MESSAGES)`` 全量替换、
    按 id 定点删除、带 id 的消息覆盖或追加。dict 形态的 reducer 指令不在此
    展开（当前中间件不产生），保持“未知更新不动状态”的保守策略。
    """
    result = [] if _resets_state(updates) else list(current)
    removed_ids = _removed_message_ids(updates)
    if removed_ids:
        result = [message for message in result if _message_identity(message) not in removed_ids]
    for message in updates:
        _upsert_state_message(result, message)
    return result
