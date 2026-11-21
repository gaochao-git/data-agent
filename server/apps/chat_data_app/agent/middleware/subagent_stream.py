"""子智能体流式事件、消息增量与状态追踪。

从 ``subagents.py`` 拆出，避免单文件超限；本模块只负责“把子图 astream
chunk 翻译成 SSE 事件/过程快照”，不持有任务账本与后台执行策略。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from itertools import count
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from apps.chat_data_app.generation_speed import (
    GenerationSpeedSampler,
    decode_duration_ms,
    decode_tokens_per_second,
)
from apps.chat_data_app.services.runtime_config import (
    SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
    SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
    SUBAGENT_THINKING_PREVIEW_MAX_CHARS,
)
from apps.chat_data_app.sse_protocol import (
    SSE_EVENT_SUBAGENT_CONTEXT_COMPRESSION,
    SSE_EVENT_SUBAGENT_CONTEXT_USAGE,
    SSE_EVENT_SUBAGENT_GENERATION_SPEED,
    SSE_EVENT_SUBAGENT_MESSAGE,
    SSE_EVENT_SUBAGENT_MESSAGE_DELTA,
    SSE_EVENT_SUBAGENT_STEPS,
    SSE_EVENT_SUBAGENT_THINKING_DELTA,
    SSE_EVENT_TOOL_COMPLETED,
    SSE_EVENT_TOOL_FAILED,
    SSE_EVENT_TOOL_STARTED,
)
from utils.logger import get_logger
from utils.tool_call_args import coerce_tool_call_args

from ..context.context_budget import (
    compact_context_usage_for_history,
    count_messages_tokens,
    latest_model_name_from_usage,
    sanitize_context_usage_for_response,
)
from .subagent_async import _truncate_text
from .subagent_config import SUBAGENT_STATE_PASSTHROUGH_KEYS
from .subagent_history import apply_state_message_update as _apply_state_message_update
from .subagent_text import (
    StreamingToolCallMarkupFilter as _StreamingToolCallMarkupFilter,
)
from .subagent_text import (
    chunk_reasoning_text as _chunk_reasoning_text,
)
from .subagent_text import (
    chunk_text_delta as _chunk_text_delta,
)
from .subagent_text import (
    plain_text as _plain_text,
)
from .subagent_text import (
    sanitize_report_text as _sanitize_report_text,
)
from .subagent_trail import AnonymousMessageIdAllocator, _read_tool_timing

logger = get_logger(__name__)


class SubagentSuspended(RuntimeError):
    """子图在安全点中断等待审批，调用方负责挂起任务而非收口。"""

    def __init__(self, payload: dict[str, Any], messages: list[Any]) -> None:
        super().__init__(str(payload.get("kind") or "subagent_suspended"))
        self.payload = payload
        self.messages = messages


class _StreamCapState:
    """单个流式键（正文或思考）的累计字符数与封顶标记。"""

    __slots__ = ("chars", "capped")

    def __init__(self) -> None:
        self.chars = 0
        self.capped = False

    def take(self, delta: str, max_chars: int) -> tuple[str | None, bool]:
        """返回 ``(允许外发的增量片段, 本次是否触发封顶截断)``；封顶后为 ``(None, False)``。"""
        if self.capped or not delta:
            return None, False
        remaining = max_chars - self.chars
        if remaining <= 0:
            self.capped = True
            return None, False
        if len(delta) <= remaining:
            self.chars += len(delta)
            return delta, False
        self.chars = max_chars
        self.capped = True
        return delta[:remaining] + "\n…[内容过长已截断]", True


def _iter_chunk_stream_events(
    chunk: Any,
    parent_tool_call_id: str | None,
    text_caps: dict[str, _StreamCapState],
    think_caps: dict[str, _StreamCapState],
    markup_filters: dict[str, _StreamingToolCallMarkupFilter] | None = None,
    speed_chunks: list[str] | None = None,
) -> list[dict[str, Any]]:
    """把 ``messages`` 模式的 ``AIMessageChunk`` 翻译成 token 级增量事件。

    - 正文增量 → ``subagent_message_delta``（先经 ``markup_filters`` 流式剥除
      裸 ``<tool_call>`` 标记，再计封顶——封顶口径按用户实际看到的文本算）
    - 思考增量 → ``subagent_thinking_delta``
    - 无 ``message_id`` 的 chunk 不增量转发（无法与 updates 的完整消息收敛，
      由完整消息事件兜底）

    带 ``tool_call_chunks`` 的 chunk **不能整块跳过**：OpenAI 兼容实现里正文与
    工具调用是两个独立字段，同一个 chunk 完全可以「文本 + 工具增量」同时到达。
    一旦跳过，这段文本永远补不回来——该消息已因先前的 delta 进入
    ``streamed_text_ids``，完整消息事件虽仍携带预览内容供晚加入者补齐，但
    直播端按「更长者优先」收敛，不会用截断预览覆盖已拼出的全文。
    工具参数不会漏进正文：``_chunk_text_delta`` 只取 ``str`` 与 ``type=="text"``
    的内容块，结构化块（工具调用等）不参与文本增量。

    ``speed_chunks`` 收集截断前、标记过滤后的可见增量，供实时速率采样。速率
    必须看完整输出；若直接采样已封顶的 ``delta``，超过预览上限后窗口会停止
    增长，速率冻结在封顶瞬间。
    """
    if not isinstance(chunk, AIMessageChunk):
        return []
    message_id = getattr(chunk, "id", None)
    if not message_id:
        return []

    text = _chunk_text_delta(getattr(chunk, "content", ""))
    if text and markup_filters is not None:
        text = markup_filters.setdefault(message_id, _StreamingToolCallMarkupFilter()).feed(text)
    events = []
    message_event = _stream_delta_event(
        text,
        event_type=SSE_EVENT_SUBAGENT_MESSAGE_DELTA,
        message_id=message_id,
        parent_tool_call_id=parent_tool_call_id,
        caps=text_caps,
        max_chars=SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
        speed_chunks=speed_chunks,
    )
    if message_event is not None:
        events.append(message_event)
    reasoning = _chunk_reasoning_text(chunk)
    thinking_event = _stream_delta_event(
        reasoning,
        event_type=SSE_EVENT_SUBAGENT_THINKING_DELTA,
        message_id=message_id,
        parent_tool_call_id=parent_tool_call_id,
        caps=think_caps,
        max_chars=SUBAGENT_THINKING_PREVIEW_MAX_CHARS,
        speed_chunks=speed_chunks,
    )
    if thinking_event is not None:
        events.append(thinking_event)
    return events


def _stream_delta_event(
    delta: str,
    *,
    event_type: str,
    message_id: str,
    parent_tool_call_id: str | None,
    caps: dict[str, _StreamCapState],
    max_chars: int,
    speed_chunks: list[str] | None,
) -> dict[str, Any] | None:
    """生成单个正文/思考增量事件；空增量或已封顶时返回 None。"""
    if not delta:
        return None
    if speed_chunks is not None:
        speed_chunks.append(delta)
    allowed, was_capped = caps.setdefault(message_id, _StreamCapState()).take(delta, max_chars)
    if not allowed:
        return None
    return {
        "event_type": event_type,
        "message_id": message_id,
        "delta": allowed,
        "truncated": was_capped,
        "parent_tool_call_id": parent_tool_call_id,
    }


def _emit_subagent_tool_event(writer: Any, event: dict[str, Any]) -> None:
    """把子智能体内层工具事件写入父图 custom 流（writer 不可用时静默跳过）。"""
    if writer is None:
        return
    try:
        writer({"type": "subagent_tool", "event": event})
    except Exception:
        logger.warning("转发子智能体工具事件失败", exc_info=True)


def _track_subagent_superstep(
    payload: dict[str, Any],
    stream_state: _SubagentStreamState,
    writer: Any,
    parent_tool_call_id: str | None,
) -> None:
    """累计子图推进次数并外发 ``subagent_steps``（首条 values 是输入回显）。"""
    if "__interrupt__" in payload:
        return
    if not stream_state.values_echo_seen:
        stream_state.values_echo_seen = True
        return
    stream_state.supersteps += 1
    event: dict[str, Any] = {
        "event_type": SSE_EVENT_SUBAGENT_STEPS,
        "parent_tool_call_id": parent_tool_call_id,
        "supersteps": stream_state.supersteps,
    }
    if stream_state.superstep_limit is not None:
        event["recursion_limit"] = stream_state.superstep_limit
    _emit_subagent_tool_event(writer, event)


def _iter_subagent_stream_events(
    messages: list,
    parent_tool_call_id: str | None = None,
    streamed_text_ids: set[str] | None = None,
    streamed_think_ids: set[str] | None = None,
    id_resolver: Callable[[Any], str] | None = None,
    tool_started_at: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """把子图节点更新里的消息翻译成子智能体内部过程事件序列。

    - AIMessage.content（非空文本）→ ``subagent_message``，即子智能体的中间
      叙述/分析结论，用 ``message_id`` 去重
    - AIMessage.tool_calls → ``tool_started``（args 尽力规整为 dict）
    - ToolMessage → ``tool_completed`` / ``tool_failed``（结果截断为预览）
    - 每个事件都挂 ``parent_tool_call_id`` 指向所属外层 subagent 调用，便于前端
      把内部过程挂在 subagent 卡片下、而不是当作顶层调用渲染。

    收敛策略（更长者优先）：``messages`` 模式已增量转发过正文的消息
    （命中 ``streamed_text_ids``），完整消息事件仍携带内容预览：快照与游标
    非原子窗口内晚加入的订阅者可能只收到尾部增量，需要
    靠这帧补齐；直播端不会用截断预览覆盖已拼出的更长全文。

    同一条 AIMessage 既有文本又有工具调用时按「先说后做」输出，保证前端拿到的
    顺序与子智能体真实执行顺序一致。

    ``id_resolver`` 为缺失 ``id`` 的消息生成稳定兜底 id：前端以 message_id 作
    为 trail 条目主键，空 id 会被整条丢弃——不能把「中间文本是否可见」押在
    provider 一定返回 chunk id 上。
    """
    streamed_text_ids = streamed_text_ids or set()
    streamed_think_ids = streamed_think_ids or set()
    events: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AIMessage):
            message_id = getattr(message, "id", None) or (id_resolver(message) if id_resolver else None)
            text = _plain_text(getattr(message, "content", ""))
            if text.strip():
                events.append(
                    _subagent_message_event(
                        message_id,
                        text,
                        parent_tool_call_id,
                        already_streamed=bool(message_id and message_id in streamed_text_ids),
                        thinking=_chunk_reasoning_text(message),
                    )
                )
            events.extend(_subagent_tool_started_events(message, parent_tool_call_id, tool_started_at))
        elif isinstance(message, ToolMessage):
            events.append(_subagent_tool_result_event(message, parent_tool_call_id, tool_started_at))
    return events


def _subagent_message_event(
    message_id: str | None,
    text: str,
    parent_tool_call_id: str | None,
    already_streamed: bool,
    thinking: str = "",
) -> dict[str, Any]:
    """生成 subagent_message 事件。

    已流式转发过的消息（``already_streamed``）前端已拼出全文，但仍携带 content：
    从检查点快照晚加入的订阅者没有收到增量，需要靠这帧补齐当前正文；已有条目
    的客户端按「更长者优先」收敛，不会用截断预览覆盖已拼出的更长全文。两个分支
    都显式带 ``streamed``：前端按布尔值判断是否保留直播累积的截断标记，
    不区分「字段缺失」与「未流式」。

    文本先过 ``_sanitize_report_text``：完整消息事件（尤其历史回放路径）
    不允许携带裸 ``<tool_call>`` 标记（硬线摘工具轮次的退化形态）。
    """
    text = _sanitize_report_text(text)
    truncated = len(text) > SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS
    thinking = thinking or ""
    payload: dict[str, Any] = {
        "event_type": SSE_EVENT_SUBAGENT_MESSAGE,
        "message_id": message_id,
        "content": _truncate_text(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS, "内容"),
        "streamed": already_streamed,
        "truncated": truncated,
        "parent_tool_call_id": parent_tool_call_id,
    }
    if thinking.strip():
        payload["thinking"] = _truncate_text(thinking, SUBAGENT_THINKING_PREVIEW_MAX_CHARS, "思考")
        payload["thinking_truncated"] = len(thinking) > SUBAGENT_THINKING_PREVIEW_MAX_CHARS
    return payload


def _subagent_tool_started_events(
    message: AIMessage,
    parent_tool_call_id: str | None,
    tool_started_at: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """把 AIMessage 的 tool_calls 翻译成 tool_started 事件（args 尽力规整为 dict）。

    ``tool_started_at`` 仅直播路径传入：记录每个内层工具开始的后端时间，供完成事件
    在 ToolTimingMiddleware 未盖章（被技能前置等外层中间件拒绝）时兜底计时。
    """
    events: list[dict[str, Any]] = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "")
        event: dict[str, Any] = {
            "event_type": SSE_EVENT_TOOL_STARTED,
            "tool_call_id": tool_call.get("id"),
            "tool_name": tool_call.get("name"),
            "args": coerce_tool_call_args(tool_call.get("args")) or {},
            "parent_tool_call_id": parent_tool_call_id,
        }
        if tool_started_at is not None and tool_call_id:
            started_at_ms = tool_started_at.setdefault(tool_call_id, int(time.time() * 1000))
            event["started_at_ms"] = started_at_ms
        events.append(event)
    return events


def _non_negative_int(value: Any) -> int:
    """非负整数安全转换：布尔/负数/非数字一律按 0 处理。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _tool_duration_in_window(
    intervals: list[tuple[int, int]],
    window_start_ms: int,
    window_end_ms: int,
) -> int:
    """工具执行区间与解码窗口的交集时长；重叠区间先合并，避免重复扣除。"""
    clipped: list[tuple[int, int]] = []
    for raw_start, raw_end in intervals:
        start = max(int(raw_start), int(window_start_ms))
        end = min(int(raw_end), int(window_end_ms))
        if end > start:
            clipped.append((start, end))
    clipped.sort()
    total = 0
    merged_start: int | None = None
    merged_end: int | None = None
    for start, end in clipped:
        if merged_start is None or merged_end is None:
            merged_start, merged_end = start, end
        elif start <= merged_end:
            merged_end = max(merged_end, end)
        else:
            total += merged_end - merged_start
            merged_start, merged_end = start, end
    if merged_start is not None and merged_end is not None:
        total += merged_end - merged_start
    return total


def _usage_totals_from_metadata(usage: Any) -> dict[str, int] | None:
    """单次模型调用的 usage_metadata → 统一 token 计数；无有效用量返回 None。"""
    if not isinstance(usage, dict):
        return None
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    output_tokens = _non_negative_int(usage.get("output_tokens"))
    total_tokens = _non_negative_int(usage.get("total_tokens")) or input_tokens + output_tokens
    if total_tokens <= 0:
        return None
    input_details = usage.get("input_token_details")
    output_details = usage.get("output_token_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_input_tokens": _non_negative_int(input_details.get("cache_read")),
        "cache_creation_input_tokens": _non_negative_int(input_details.get("cache_creation")),
        "reasoning_tokens": _non_negative_int(output_details.get("reasoning")),
    }


def _aggregate_subagent_token_usage(messages: list, timing: dict[str, Any] | None = None) -> dict[str, Any]:
    """汇总子图内每次模型调用的 token 用量（billing 口径，不是上下文占用估算）。

    子图 ``callbacks=[]`` 且不挂主图 ContextUsageMiddleware，主对话 token 统计看不到
    这部分；这里按消息上的 ``usage_metadata`` 累加，随 subagent 结果持久化供前端展示。
    速率与主对话共用解码窗口口径：首 token → 流结束，扣除窗口内工具执行时间，
    TTFT 不进分母；旧任务没有计时印章时省略速率，不用模型调用总时长冒充。
    """
    totals: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    for message in messages or []:
        part = _usage_totals_from_metadata(getattr(message, "usage_metadata", None))
        if part is None:
            continue
        for key, value in part.items():
            totals[key] += value
    if isinstance(timing, dict):
        duration_ms = decode_duration_ms(
            first_token_wall_ms=_non_negative_int(timing.get("first_token_wall_ms")) or None,
            stream_ended_wall_ms=_non_negative_int(timing.get("stream_ended_wall_ms")) or None,
            tool_duration_ms=_non_negative_int(timing.get("tool_duration_ms")),
        )
        tokens_per_second = decode_tokens_per_second(
            output_tokens=int(totals["output_tokens"]),
            duration_ms=duration_ms,
        )
        if tokens_per_second is not None:
            totals["tokens_per_second"] = tokens_per_second
    return totals if totals["total_tokens"] > 0 else {}


def _subagent_process_snapshot(
    messages: list,
    context_usage: Any,
    compactions: Any = None,
    token_detail: Any = None,
    model_name: str | None = None,
    timing: dict[str, Any] | None = None,
    supersteps: Any = None,
    recursion_limit: Any = None,
) -> dict[str, Any]:
    """subagent 结果上附带的子过程快照：上下文用量 + 子图 token 用量 + 压缩事件。"""
    snapshot: dict[str, Any] = {}
    if isinstance(context_usage, dict) and context_usage:
        snapshot["subagent_context_usage"] = context_usage
    if isinstance(compactions, list) and compactions:
        snapshot["subagent_compactions"] = compactions
    if isinstance(token_detail, dict) and token_detail:
        # 压缩落库体积在 middleware 层完成（run 层不允许直接依赖 context 包）
        snapshot["subagent_token_usage_detail"] = compact_context_usage_for_history(token_detail)
    # 子图 token 用量（billing 口径）：主对话统计不含子智能体消耗，这里单独随结果持久化
    token_usage = _aggregate_subagent_token_usage(messages, timing)
    if token_usage:
        if model_name:
            token_usage["model"] = model_name
        snapshot["subagent_token_usage"] = token_usage
    # 子图本次派发的图推进次数（每次派发从 0 起），与主图 run_steps 同口径。
    if isinstance(supersteps, int) and supersteps > 0:
        snapshot["subagent_supersteps"] = supersteps
        if isinstance(recursion_limit, int) and recursion_limit > 0:
            snapshot["subagent_recursion_limit"] = recursion_limit
    return snapshot


def _subagent_tool_result_event(
    message: ToolMessage,
    parent_tool_call_id: str | None,
    tool_started_at: dict[str, int] | None = None,
) -> dict[str, Any]:
    """把 ToolMessage 翻译成 tool_completed / tool_failed 事件（结果截断为预览）。"""
    raw = _plain_text(getattr(message, "content", ""))
    status = str(getattr(message, "status", "") or "")
    event: dict[str, Any] = {
        "event_type": SSE_EVENT_TOOL_FAILED if status == "error" else SSE_EVENT_TOOL_COMPLETED,
        "tool_call_id": getattr(message, "tool_call_id", None),
        "tool_name": getattr(message, "name", None),
        "content": _truncate_text(raw, SUBAGENT_RESULT_PREVIEW_MAX_CHARS),
        "truncated": len(raw) > SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
        "parent_tool_call_id": parent_tool_call_id,
        **_read_tool_timing(message),
    }
    if tool_started_at is not None:
        tool_call_id = str(event.get("tool_call_id") or "")
        started_at_ms = event.get("started_at_ms") or tool_started_at.get(tool_call_id)
        if tool_call_id and started_at_ms:
            ended_at_ms = event.get("ended_at_ms") or int(time.time() * 1000)
            event["started_at_ms"] = started_at_ms
            event["ended_at_ms"] = max(int(ended_at_ms), int(started_at_ms))
            tool_started_at.pop(tool_call_id, None)
    return event


class _SubagentStreamState:
    """子图直播的状态载体：去重、增量转发达标与匿名 id 兜底。"""

    def __init__(self, parent_tool_call_id: str | None) -> None:
        self.parent_tool_call_id = parent_tool_call_id
        self.seen_ids: set[str] = set()
        self.text_caps: dict[str, _StreamCapState] = {}
        self.think_caps: dict[str, _StreamCapState] = {}
        # 按消息隔离的流式标记剥除器：硬线摘工具轮次的退化文本不允许进直播
        self.text_markup_filters: dict[str, _StreamingToolCallMarkupFilter] = {}
        self.streamed_text_ids: set[str] = set()
        self.streamed_think_ids: set[str] = set()
        self.anonymous_seq = count(1)
        self.anonymous_ids: dict[int, str] = {}
        self.anonymous_ai_ids = AnonymousMessageIdAllocator()
        self.collected: list = []
        # 内层工具开始的后端时间：完成事件缺少工具计时盖章时兜底（每个内层工具都有耗时）
        self.tool_started_at: dict[str, int] = {}
        # 与主对话共用的实时速率滑窗，以及完成态解码窗口锚点。
        self.speed_sampler = GenerationSpeedSampler()
        self.stream_ended_wall_ms: int | None = None
        self.tool_intervals: dict[str, tuple[int, int]] = {}
        # 累计工具 id→name（完成也保留）：tool_started_at 只放正在执行的。
        self.tool_history: dict[str, str] = {}
        # 子图上下文压缩事件（压缩节点 updates 里的 context_compression_events）
        self.compression_events: list[dict[str, Any]] = []
        self.seen_compression_ids: set[str] = set()
        # 已收到 started、还没收到 completed：随心跳落进 progress，让运行中
        # 的任务卡显示「正在压缩」。心跳 5s 采样，跳过摘要的百毫秒压缩天然
        # 不会被采样到，只有真正调用摘要模型的长压缩才会出现，不会闪一下。
        self.compressing: bool = False
        # 压缩发生前最后一条过程条目的 id：与 _build_trail_snapshot 的条目 id
        # 同源，历史回放按它把分隔卡插回真实位置，避免多条压缩挤成一堆。
        self.last_trail_anchor: str = ""
        # 续聊快照用的“当前完整状态消息”。collected 只记本轮新增，且压缩后会
        # 重建；追问需要的是包含历史在内的最终状态，因此单独按 reducer 维护。
        self.state_messages: list[Any] = []
        # 子图中断（待审批）载荷：由调用方决定挂起并持久化。
        self.interrupt_payload: dict[str, Any] | None = None
        # 本次派发的图推进次数（values 流每条一次；首条为输入回显），
        # 与主图 run_steps 同口径，按子图 invoke 归零。
        self.supersteps: int = 0
        self.superstep_limit: int | None = None
        self.values_echo_seen: bool = False

    def prime_state_messages(self, messages: list[Any]) -> None:
        self.state_messages = list(messages)
        # 追问的历史消息不带 id；先按历史出现次数预占分配器序号，否则新一轮
        # 同内容消息会拿到与历史相同的 key，被最终快照按 seen_ids 吞掉。
        self.anonymous_ai_ids.prime(self.state_messages)

    def track_state_messages(self, update: dict[str, Any]) -> None:
        """按 LangGraph add_messages 语义应用节点更新，得到最终完整消息列表。"""
        for node_result in update.values():
            if not isinstance(node_result, dict):
                continue
            node_messages = node_result.get("messages")
            if not isinstance(node_messages, list):
                continue
            self.state_messages = _apply_state_message_update(self.state_messages, node_messages)

    def resolve_anonymous_id(self, message: Any) -> str:
        """给缺失 id 的消息生成稳定 id。

        按对象身份缓存：同一条消息（无 id）可能出现在多个节点的 updates 里，
        必须收敛到同一个 id，否则 seen_ids 去重失效、前端会渲染出重复条目。
        消息对象在 collected 中持有强引用，``id()`` 作为 key 不会被复用。
        AIMessage 与快照共用同一个分配器：内容指纹保证跨对象对齐，同内容
        出现序号避免两条相同消息被误合并；生成的 ID 回写消息供检查点复用。
        其它类型不进入 trail，用带调用前缀的序号即可。
        """
        key = id(message)
        generated = self.anonymous_ids.get(key)
        if generated is None:
            if isinstance(message, AIMessage):
                generated = self.anonymous_ai_ids.resolve(message)
            else:
                generated = f"{self.parent_tool_call_id or 'subagent'}:anon-{next(self.anonymous_seq)}"
            self.anonymous_ids[key] = generated
        return generated

    def emit_messages_chunk(self, chunk_obj: Any, writer: Any) -> None:
        """messages 模式：正文/思考增量转发并记录已达标的 message id。"""
        now_ms = int(time.time() * 1000)
        speed_chunks: list[str] = []
        for event in _iter_chunk_stream_events(
            chunk_obj,
            self.parent_tool_call_id,
            self.text_caps,
            self.think_caps,
            self.text_markup_filters,
            speed_chunks,
        ):
            if event["event_type"] == "subagent_message_delta":
                self.streamed_text_ids.add(event["message_id"])
            else:
                self.streamed_think_ids.add(event["message_id"])
            _emit_subagent_tool_event(writer, event)
        for speed_chunk in speed_chunks:
            self.speed_sampler.observe(speed_chunk, now_ms=now_ms)
        speed = self.speed_sampler.maybe_snapshot(now_ms=now_ms)
        if speed:
            _emit_subagent_tool_event(
                writer,
                {
                    "event_type": SSE_EVENT_SUBAGENT_GENERATION_SPEED,
                    "parent_tool_call_id": self.parent_tool_call_id,
                    **speed,
                },
            )

    def record_tool_event(self, event: dict[str, Any]) -> None:
        """记录内层工具的真实执行区间，供完成态解码窗口扣除。"""
        if event.get("event_type") not in {SSE_EVENT_TOOL_COMPLETED, SSE_EVENT_TOOL_FAILED}:
            return
        tool_call_id = str(event.get("tool_call_id") or "")
        started_at_ms = _non_negative_int(event.get("started_at_ms")) or self.tool_started_at.get(tool_call_id, 0)
        ended_at_ms = _non_negative_int(event.get("ended_at_ms"))
        if tool_call_id and started_at_ms > 0 and ended_at_ms > 0:
            self.tool_intervals[tool_call_id] = (started_at_ms, max(ended_at_ms, started_at_ms))

    def generation_timing(self, *, now_ms: int | None = None) -> dict[str, int | None]:
        """生成统计锚点：首可见 token、流结束、窗口内工具耗时。"""
        ended_ms = self.stream_ended_wall_ms or now_ms or int(time.time() * 1000)
        first_token_wall_ms = self.speed_sampler.first_visible_token_wall_ms
        intervals = list(self.tool_intervals.values())
        intervals.extend(
            (started_at_ms, ended_ms)
            for tool_call_id, started_at_ms in self.tool_started_at.items()
            if tool_call_id not in self.tool_intervals
        )
        return {
            "first_token_wall_ms": first_token_wall_ms,
            "stream_ended_wall_ms": ended_ms,
            "tool_duration_ms": _tool_duration_in_window(
                intervals,
                first_token_wall_ms or 0,
                ended_ms,
            ),
        }

    def collect_update_messages(self, update: dict[str, Any]) -> list:
        """updates 模式：收集本节点新增消息（去重后），返回给事件提取。

        子图压缩（``ContextCompressionMiddleware.before_model`` 是独立图节点，
        其 state 更新会作为节点更新出现在 updates 流）以
        ``[RemoveMessage(REMOVE_ALL_MESSAGES), *compressed]`` 整体替换消息列表：
        此时 ``collected``/``seen_ids`` 必须按压缩后列表重建，否则会滞留已被
        丢弃的旧消息——用量徽标虚高、trail 混入已压缩内容、最终报告取到旧文。
        压缩保留的是早先已见过的同一批消息对象，重建时不再作为「新增」外发，
        避免前端收到重复的 message / tool_started 条目。
        """
        new_messages: list = []
        for node_result in update.values():
            if not isinstance(node_result, dict):
                continue
            node_messages = node_result.get("messages") or []
            if isinstance(node_messages, dict):
                # 带 reducer 指令的消息更新（如 REMOVE_ALL_MESSAGES），跳过
                continue
            if any(isinstance(message, RemoveMessage) for message in node_messages):
                self._apply_message_removals(node_messages, new_messages)
                continue
            for message in node_messages:
                if isinstance(message, dict):
                    continue
                self._collect_new_message(message, new_messages)
        return new_messages

    def _collect_new_message(self, message: Any, new_messages: list) -> None:
        """正常节点更新的单条消息收集：按 id 去重后入 collected 并标记为新增。"""
        message_id = getattr(message, "id", None) or self.resolve_anonymous_id(message)
        if message_id in self.seen_ids:
            return
        self.seen_ids.add(message_id)
        self.collected.append(message)
        new_messages.append(message)

    @staticmethod
    def _parse_removal_update(node_messages: list) -> tuple[bool, set[str]]:
        """解析移除更新：返回（是否全量替换, 定点删除的消息 id 集合）。"""
        full_reset = any(
            isinstance(message, RemoveMessage) and getattr(message, "id", None) == REMOVE_ALL_MESSAGES
            for message in node_messages
        )
        targeted_ids = {
            str(message.id)
            for message in node_messages
            if isinstance(message, RemoveMessage) and getattr(message, "id", None) != REMOVE_ALL_MESSAGES
        }
        return full_reset, targeted_ids

    def _drop_collected_messages(self, targeted_ids: set[str]) -> None:
        """从记账与已收集列表中就地摘除定点删除的消息。

        collected 可能被调用方持有引用（失败路径 trail 快照依赖它），
        必须原地修改而非重新绑定。
        """
        for removed_id in targeted_ids:
            self.seen_ids.discard(removed_id)
            self.collected[:] = [
                message for message in self.collected if str(getattr(message, "id", "") or "") != removed_id
            ]

    def _apply_message_removals(self, node_messages: list, new_messages: list) -> None:
        """处理压缩节点的消息移除更新，重建 collected/seen_ids 记账。

        - ``RemoveMessage(REMOVE_ALL_MESSAGES)``：全量替换——collected 清空后
          按更新里携带的保留消息（compressed 列表）重建；
        - 按 id 的定点 ``RemoveMessage``：只从记账里摘除对应消息，其余消息
          沿用既有记账（同一节点更新里未删除的消息不重复入 collected）。
        """
        previous_ids = set(self.seen_ids)
        full_reset, targeted_ids = self._parse_removal_update(node_messages)
        if full_reset:
            self.collected.clear()
            self.seen_ids.clear()
        else:
            self._drop_collected_messages(targeted_ids)
        for message in node_messages:
            if isinstance(message, (RemoveMessage, dict)):
                continue
            message_id = getattr(message, "id", None) or self.resolve_anonymous_id(message)
            if str(message_id) in targeted_ids or message_id in self.seen_ids:
                continue
            self.seen_ids.add(message_id)
            self.collected.append(message)
            if message_id not in previous_ids:
                new_messages.append(message)


def _subagent_usage_snapshot(usage_meta: dict[str, int], collected: list) -> dict[str, int]:
    """按静态口径+已收集消息估算当前子图上下文用量快照。

    两个百分比口径明确分工：
    - ``percent``：占**模型上下文窗口**比例，与主对话「上下文 N%」同口径，可直接对比；
    - ``budget_percent``：占**压缩触发阈值**（窗口 × 压缩触发比例）比例，
      前端用它展示与主图一致的压缩水位。
    """
    used = usage_meta["static_tokens"] + count_messages_tokens(collected)
    threshold_tokens = max(usage_meta["threshold_tokens"], 1)
    context_window = max(int(usage_meta.get("context_window") or threshold_tokens), 1)
    return {
        "used_tokens": used,
        "threshold_tokens": threshold_tokens,
        "context_window": context_window,
        "percent": min(int(used * 100 / context_window), 999),
        "budget_percent": min(int(used * 100 / threshold_tokens), 999),
    }


def _compaction_record_from_event(compaction: Any, seen_ids: set[str]) -> dict[str, Any] | None:
    """校验并归一一条压缩事件；完成态且首次出现才返回记录。"""
    if not isinstance(compaction, dict) or compaction.get("phase") != "completed":
        return None
    compaction_id = str(compaction.get("id") or "").strip()
    if not compaction_id or compaction_id in seen_ids:
        return None
    seen_ids.add(compaction_id)
    return {
        "id": compaction_id,
        "timestamp": str(compaction.get("timestamp") or ""),
        "anchor_message_id": str(compaction.get("anchor_message_id") or ""),
        "estimated_tokens_before": int(compaction.get("estimated_tokens_before") or 0),
        "estimated_tokens_after": int(compaction.get("estimated_tokens_after") or 0),
        "estimated_cleared_tokens": int(compaction.get("estimated_cleared_tokens") or 0),
        "compressed_messages": int(compaction.get("compressed_messages") or 0),
    }


def _forward_subagent_compaction_events(
    payload: dict,
    stream_state: _SubagentStreamState,
    writer: Any,
    parent_tool_call_id: str | None,
) -> None:
    """把压缩节点更新里的 completed 事件转发为 subagent_context_compression（去重 + 记录）。"""
    for node_result in payload.values():
        if not isinstance(node_result, dict):
            continue
        for compaction in node_result.get("context_compression_events") or []:
            record = _compaction_record_from_event(compaction, stream_state.seen_compression_ids)
            if record is None:
                continue
            # 压缩 middleware 的 anchor 取的是 LangChain message.id，工具结果的 id
            # 常为空或与 trail 的 tool_call_id 不同源；改用实时过程里最后一条
            # 已发生的条目 id，历史快照才能精确插回压缩当时所在的位置。
            if stream_state.last_trail_anchor:
                record["anchor_message_id"] = stream_state.last_trail_anchor
            stream_state.compression_events.append(record)
            # completed 到达即收起「正在压缩」：重复帧被 seen_compression_ids 挡掉，
            # 不会误清；异常中断没收到 completed 时由终态 progress 归一兜底。
            stream_state.compressing = False
            _emit_subagent_tool_event(
                writer,
                {
                    "event_type": SSE_EVENT_SUBAGENT_CONTEXT_COMPRESSION,
                    "parent_tool_call_id": parent_tool_call_id,
                    **record,
                },
            )


def _forward_subagent_compression_started(
    payload: Any,
    writer: Any,
    parent_tool_call_id: str | None,
    stream_state: _SubagentStreamState | None = None,
) -> None:
    """把子图 custom 流里的「压缩中」占位转发为 subagent_context_compression。

    压缩 middleware 的 started 事件只走 custom channel、不写检查点，因此历史
    回放不会残留占位；完成态仍由 ``_forward_subagent_compaction_events`` 从
    updates 转发。子图 custom 流里的其它事件一律忽略，避免污染主对话帧。
    有效 started 同时把运行期「正在压缩」标记写进 stream_state，让心跳里的
    progress 带上 compressing，任务卡不必等任务收口才知道正在压缩。
    """
    if not isinstance(payload, dict) or payload.get("type") != "context_compression":
        return
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("phase") != "started":
        return
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        return
    if stream_state is not None:
        stream_state.compressing = True
    _emit_subagent_tool_event(
        writer,
        {
            "event_type": SSE_EVENT_SUBAGENT_CONTEXT_COMPRESSION,
            "parent_tool_call_id": parent_tool_call_id,
            "phase": "started",
            "id": event_id,
            "timestamp": str(event.get("timestamp") or ""),
        },
    )


def _process_subagent_chunk(
    chunk: Any, stream_state: _SubagentStreamState, writer: Any, parent_tool_call_id: str | None
) -> bool:
    """消费子图流 chunk；返回是否为 updates 模式。"""
    if not (isinstance(chunk, tuple) and len(chunk) == 2):
        return False
    mode, payload = chunk
    if mode == "values":
        if isinstance(payload, dict):
            _track_subagent_superstep(payload, stream_state, writer, parent_tool_call_id)
        return False
    if mode == "messages":
        if isinstance(payload, tuple) and len(payload) == 2:
            stream_state.emit_messages_chunk(payload[0], writer)
        return False
    if mode == "custom":
        _forward_subagent_compression_started(payload, writer, parent_tool_call_id, stream_state)
        return False
    if mode != "updates" or not isinstance(payload, dict):
        return False
    _process_subagent_update(payload, stream_state, writer, parent_tool_call_id)
    return True


def _process_subagent_update(
    payload: dict[str, Any],
    stream_state: _SubagentStreamState,
    writer: Any,
    parent_tool_call_id: str | None,
) -> None:
    """处理一条 updates 载荷：消息收敛、工具事件与压缩事件转发。"""
    interrupt_payload = _extract_interrupt_payload(payload.get("__interrupt__"))
    if interrupt_payload is not None:
        stream_state.interrupt_payload = interrupt_payload
    stream_state.track_state_messages(payload)
    new_messages = stream_state.collect_update_messages(payload)
    _forward_subagent_compaction_events(payload, stream_state, writer, parent_tool_call_id)
    for event in _iter_subagent_stream_events(
        new_messages,
        parent_tool_call_id,
        stream_state.streamed_text_ids,
        stream_state.streamed_think_ids,
        stream_state.resolve_anonymous_id,
        stream_state.tool_started_at,
    ):
        _track_subagent_tool_event(stream_state, event)
        stream_state.record_tool_event(event)
        _emit_subagent_tool_event(writer, event)
        anchor = str(event.get("tool_call_id") or event.get("message_id") or "").strip()
        if anchor:
            stream_state.last_trail_anchor = anchor


def _extract_interrupt_payload(raw: Any) -> dict[str, Any] | None:
    """把 LangGraph ``__interrupt__`` 的元组载荷归一成可持久化的 dict。"""
    if raw is None:
        return None
    items = raw if isinstance(raw, (list, tuple)) else (raw,)
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            return value
        return {"value": value}
    return {}


def _track_subagent_tool_event(stream_state: _SubagentStreamState, event: dict[str, Any]) -> None:
    """记录累计工具（完成后仍保留），避免进度只剩正在执行的工具数。"""
    if event.get("event_type") != SSE_EVENT_TOOL_STARTED:
        return
    tool_call_id = str(event.get("tool_call_id") or "")
    if tool_call_id:
        stream_state.tool_history[tool_call_id] = str(event.get("tool_name") or "")


def _resolve_subagent_usage_detail(captured: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 updates 捕获的 state 明细（已含 breakdown）取最近一次 usage。"""
    detail = captured.get("token_usage_detail") if isinstance(captured, dict) else None
    return sanitize_context_usage_for_response(detail) if isinstance(detail, dict) and detail else None


def _capture_subagent_token_usage(chunk: Any, captured: dict[str, Any] | None) -> None:
    """从子图 updates 里抓最近一次 token_usage 明细（ContextUsageMiddleware 写入）。"""
    if captured is None or not (isinstance(chunk, tuple) and len(chunk) == 2):
        return
    mode, payload = chunk
    if mode != "updates" or not isinstance(payload, dict):
        return
    for node_result in payload.values():
        if not isinstance(node_result, dict):
            continue
        detail = node_result.get("token_usage")
        if isinstance(detail, dict) and detail:
            captured["token_usage_detail"] = detail
            model_name = latest_model_name_from_usage(detail)
            if model_name:
                captured["model"] = model_name


async def _run_subagent_with_events(
    runnable: Any,
    state: Any,
    config: dict[str, Any],
    writer: Any,
    parent_tool_call_id: str | None = None,
    collected: list | None = None,
    captured: dict[str, Any] | None = None,
    usage_meta: dict[str, int] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    initial_messages: list[Any] | None = None,
) -> list:
    """流式运行子图：转发内层事件并返回完整消息列表。

    ``collected`` / ``captured`` / ``usage_meta`` 分别承载已收集消息（失败路径
    trail 快照）、上下文用量（写入 ToolMessage 供回放）与实时
    ``subagent_context_usage`` 推送（口径与压缩阈值共用 ``_subagent_budget_meta``）。

    双模式：``messages`` 转发 token 增量；``updates`` 负责工具生命周期与最终报告，
    已增量转发的消息仍带完整内容供晚加入者补齐，前端按「更长者优先」收敛
    （见 ``_iter_subagent_stream_events``）。
    """
    stream_state = _SubagentStreamState(parent_tool_call_id)
    seed_messages = initial_messages if initial_messages is not None else list(state.get("messages") or [])
    stream_state.prime_state_messages(list(seed_messages))
    if collected is not None:
        stream_state.collected = collected
    try:
        await _consume_subagent_stream(
            runnable,
            state,
            config,
            stream_state,
            writer,
            parent_tool_call_id,
            captured,
            usage_meta,
            on_progress,
        )
    finally:
        stream_state.stream_ended_wall_ms = int(time.time() * 1000)
        generation_timing = stream_state.generation_timing(now_ms=stream_state.stream_ended_wall_ms)
        if captured is not None:
            captured["context_usage"] = _emit_context_usage_snapshot(
                writer=writer,
                parent_tool_call_id=parent_tool_call_id,
                usage_meta=usage_meta,
                collected=stream_state.collected,
                captured=captured,
                timing=generation_timing,
                emit=False,
            )
            captured["compactions"] = list(stream_state.compression_events)
            captured["final_messages"] = list(stream_state.state_messages)
            captured["generation_timing"] = generation_timing
            captured["supersteps"] = stream_state.supersteps
            captured["recursion_limit"] = stream_state.superstep_limit
    if stream_state.interrupt_payload is not None:
        raise SubagentSuspended(stream_state.interrupt_payload, list(stream_state.collected))
    return stream_state.collected


async def _consume_subagent_stream(
    runnable: Any,
    state: Any,
    config: dict[str, Any],
    stream_state: _SubagentStreamState,
    writer: Any,
    parent_tool_call_id: str | None,
    captured: dict[str, Any] | None,
    usage_meta: dict[str, int] | None,
    on_progress: Callable[[dict[str, Any]], None] | None,
) -> None:
    limit = config.get("recursion_limit") if isinstance(config, dict) else None
    stream_state.superstep_limit = limit if isinstance(limit, int) and limit > 0 else None
    async for chunk in runnable.astream(state, config, stream_mode=["values", "updates", "messages", "custom"]):
        _capture_subagent_token_usage(chunk, captured)
        context_usage = None
        if _process_subagent_chunk(chunk, stream_state, writer, parent_tool_call_id):
            context_usage = _emit_context_usage_snapshot(
                writer=writer,
                parent_tool_call_id=parent_tool_call_id,
                usage_meta=usage_meta,
                collected=stream_state.collected,
                captured=captured,
                timing=stream_state.generation_timing(),
            )
        if on_progress is not None:
            on_progress(_subagent_progress_snapshot(stream_state, context_usage))


def _emit_context_usage_snapshot(
    *,
    writer: Any,
    parent_tool_call_id: str | None,
    usage_meta: dict[str, int] | None,
    collected: list,
    captured: dict[str, Any] | None,
    timing: dict[str, Any] | None = None,
    emit: bool = True,
) -> dict[str, Any] | None:
    """计算（并按需外发）上下文用量；直播与失败快照共用同一口径。"""
    if usage_meta is None:
        return None
    usage: dict[str, Any] = dict(_subagent_usage_snapshot(usage_meta, collected))
    detail = _resolve_subagent_usage_detail(captured)
    if detail:
        usage["token_usage_detail"] = detail
    if emit:
        event = {
            "event_type": SSE_EVENT_SUBAGENT_CONTEXT_USAGE,
            "parent_tool_call_id": parent_tool_call_id,
            **usage,
        }
        token_usage = _aggregate_subagent_token_usage(collected, timing)
        if token_usage:
            model_name = str(captured.get("model") or "") if isinstance(captured, dict) else ""
            if model_name:
                token_usage["model"] = model_name
            event["subagent_token_usage"] = token_usage
        _emit_subagent_tool_event(
            writer,
            event,
        )
    return usage


def _subagent_progress_snapshot(
    stream_state: _SubagentStreamState,
    context_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """后台任务心跳落库用的轻量进度：不携带正文，避免任务账本膨胀。"""
    last_tool = next(reversed(stream_state.tool_history.values()), "")
    snapshot = {
        "phase": "running",
        "message_count": len(stream_state.collected),
        "tool_calls": len(stream_state.tool_history),
        "running_tools": len(stream_state.tool_started_at),
        "last_tool": last_tool,
        # 运行中还没有 result_meta，压缩次数只能随心跳落 progress，任务卡才不会
        # 一直显示 0；终态由 SubagentTaskRecord.compaction_count 以结果快照为准。
        "compaction_count": len(stream_state.compression_events),
        # 运行中当前派发已推进的图步数；与终态 result_meta 的
        # subagent_supersteps 同口径（每次派发归零）。
        "supersteps": stream_state.supersteps,
        # 只有 started 已到、completed 未到的窗口内为 True，任务卡据此显示
        # 「正在压缩上下文」；心跳 5s 采样，百毫秒级跳过摘要不会闪一下。
        "compressing": stream_state.compressing,
    }
    if stream_state.superstep_limit is not None:
        snapshot["recursion_limit"] = stream_state.superstep_limit
    if context_usage:
        # 上下文快照随心跳落进任务账本的 progress：列表接口不返回 result_meta，
        # 运行中/取消/失联状态也能从统一进度里展示占比；明细体积大，只留计数。
        snapshot["context_usage"] = {key: value for key, value in context_usage.items() if key != "token_usage_detail"}
    return snapshot


def _build_subagent_state(
    runtime: ToolRuntime,
    description: str,
    history: list[Any] | None = None,
    *,
    task_id: str | None = None,
    owner_token: str | None = None,
) -> dict[str, Any]:
    state = {key: value for key, value in dict(runtime.state or {}).items() if key in SUBAGENT_STATE_PASSTHROUGH_KEYS}
    if task_id:
        state["subagent_task_id"] = task_id
    if owner_token:
        state["subagent_owner_token"] = owner_token
    # 追问：历史消息（含上一轮工具调用与结论）+ 本轮用户消息，直接在同一个
    # 子会话上下文里继续；首轮 history 为空，行为与旧版一致。
    state["messages"] = [*(history or []), HumanMessage(content=description)]
    return state
