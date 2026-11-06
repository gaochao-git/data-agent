"""chat-data SSE 线上协议层：帧名词汇表（单一事实来源）与唯一序列化器。

三条词汇轴互不等价，本模块只拥有第一条：

1. **SSE 帧名**（本模块 ``SSE_EVENT_*`` 常量）：``event:`` 行上出现的字符串，
   由 :func:`format_sse` 序列化后进入 SSE 响应体；Redis Run 记录的 ``payload``
   存的就是整帧文本，断线续传原样转发，因此帧名只有本模块这一个来源。
2. **持久化事件类型**（``agent/run/conversation_event_service.py`` 的 ``EVENT_*``：
   ``user_message`` / ``reasoning`` / ``assistant_message`` / ``tool_result`` 等）：
   落库与历史回放的事件轴，**不是**帧名。其中 ``context_compression`` / ``token_usage``
   与本模块常量恰好等值纯属巧合——两条轴独立演进，禁止互相引用或共用常量，
   否则任一端改名会静默打断另一端。
   注意区分：Redis Run 记录的 ``event_type`` 字段**是**帧名的派生物
   （``agent_run_event_service._event_type`` 用正则从帧里反解，只用于 done 终态判定），
   它随帧继承取值，不需要也不应改成引用本模块常量。
3. **LangGraph custom channel 的 ``type`` 判别键**（``agent_stream._handle_custom_payload``
   的分发表：``token_usage`` / ``subagent_tool`` 等）：内部路由用，``subagent_tool``
   就没有对应帧名，其余同名只是约定——与本表无派生关系，不要替换成帧名常量。
4. **上游 provider 协议名**（如 ``reasoning_content``）：由 provider 定义，属于
   外部协议边界，不进本表（见 AGENTS.md §7：只在边界层转换对方要求的命名）。

前端消费侧的清单由门禁与本表比对（``scripts/checks/sse_vocabulary.py``）：两侧各自
手写、无人校验的帧名清单即为待收敛的漂移源。
"""

from __future__ import annotations

import json
from typing import Any

# 主回复流
SSE_EVENT_MESSAGE = "message"
SSE_EVENT_MESSAGE_END = "message_end"
SSE_EVENT_THINKING = "thinking"
SSE_EVENT_ERROR = "error"
SSE_EVENT_DONE = "done"
# 运行度量
SSE_EVENT_TOKEN_USAGE = "token_usage"
SSE_EVENT_GENERATION_SPEED = "generation_speed"
SSE_EVENT_CONTEXT_COMPRESSION = "context_compression"
SSE_EVENT_HEARTBEAT = "heartbeat"
SSE_EVENT_INBOX_ADMITTED = "inbox_admitted"
SSE_EVENT_QUESTION_REQUESTED = "question_requested"
# 工具生命周期：``tool`` 是旧的全量快照帧，与细粒度帧并行下发，
# 前端按 tool_call_id 在顶层 tool 帧里去重（同一工具的完整快照已由细粒度帧先行分发）。
SSE_EVENT_TOOL = "tool"
SSE_EVENT_TOOL_STARTED = "tool_started"
SSE_EVENT_TOOL_PROGRESS = "tool_progress"
SSE_EVENT_TOOL_COMPLETED = "tool_completed"
SSE_EVENT_TOOL_FAILED = "tool_failed"
# 子智能体内层帧：经 LangGraph custom channel 透传，事件名由子智能体侧决定
SSE_EVENT_SUBAGENT_MESSAGE = "subagent_message"
SSE_EVENT_SUBAGENT_MESSAGE_DELTA = "subagent_message_delta"
SSE_EVENT_SUBAGENT_THINKING_DELTA = "subagent_thinking_delta"
SSE_EVENT_SUBAGENT_CONTEXT_USAGE = "subagent_context_usage"
SSE_EVENT_SUBAGENT_CONTEXT_COMPRESSION = "subagent_context_compression"

SSE_EVENT_NAMES: frozenset[str] = frozenset(
    {
        SSE_EVENT_MESSAGE,
        SSE_EVENT_MESSAGE_END,
        SSE_EVENT_THINKING,
        SSE_EVENT_ERROR,
        SSE_EVENT_DONE,
        SSE_EVENT_TOKEN_USAGE,
        SSE_EVENT_GENERATION_SPEED,
        SSE_EVENT_CONTEXT_COMPRESSION,
        SSE_EVENT_HEARTBEAT,
        SSE_EVENT_INBOX_ADMITTED,
        SSE_EVENT_QUESTION_REQUESTED,
        SSE_EVENT_TOOL,
        SSE_EVENT_TOOL_STARTED,
        SSE_EVENT_TOOL_PROGRESS,
        SSE_EVENT_TOOL_COMPLETED,
        SSE_EVENT_TOOL_FAILED,
        SSE_EVENT_SUBAGENT_MESSAGE,
        SSE_EVENT_SUBAGENT_MESSAGE_DELTA,
        SSE_EVENT_SUBAGENT_THINKING_DELTA,
        SSE_EVENT_SUBAGENT_CONTEXT_USAGE,
    }
)


def format_sse(event: str, data: dict[str, Any] | str) -> str:
    """序列化一帧 SSE：``event:`` 取本模块帧名常量，``data:`` 取 dict 或已序列化字符串。

    Args:
        event: 帧名，必须是 :data:`SSE_EVENT_NAMES` 中的成员
        data: 事件数据（dict 会被 JSON 序列化，字符串原样使用）
    """
    data_str = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    return f"event: {event}\ndata: {data_str}\n\n"
