"""ChatData completion handler."""

import asyncio
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from typing import Any
from uuid import uuid4

import openai
from fastapi import Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    ToolMessage,
    ToolMessageChunk,
)

from utils.alarm import emit_llm_connection_alarm_if_needed, emit_llm_first_token_timeout_alarm
from utils.logger import get_logger

from .agent import (
    AgentRunControls,
    DatasourceContext,
    IdentityContext,
    ModelInvocationConfig,
    build_unified_system_prompt,
    create_data_agent,
    get_checkpointer,
    get_subagent_hot_defaults,
    resolve_agent_runtime_options,
)
from .agent.context.context_budget import (
    ContextInputLimitExceededError,
    latest_model_name_from_usage,
    sanitize_context_usage_for_response,
)
from .agent.middleware.steering import SteeringMessageTracker
from .agent.plugins.loader import refresh_plugin_state
from .agent.run.agent_inbox_service import AgentRunLeaseLostError, agent_inbox_service
from .agent.run.agent_run_event_service import agent_run_event_service
from .agent.run.agent_run_runtime import agent_run_runtime
from .agent.run.chat_data_stream_request import ChatDataStreamRequest
from .agent.run.completion_options import parse_completion_run_options, parse_file_selections, parse_quoted_snippets
from .agent.run.conversation_event_service import conversation_event_service
from .agent.run.conversation_runtime import (
    TurnLifecycleHooks,
    TurnPersistenceHooks,
    TurnResourceOptions,
    stream_agent_turn,
)
from .agent.workspace.runtime import is_system_result_workspace_path, resolve_workspace_path
from .agent.workspace.uploaded_file_parser import get_image_size_limit
from .agent.workspace.workspace_path_policy import USER_DATA_VIRTUAL_ROOT, WORKSPACE_PATH_POLICY

# 运行准备与历史持久化层（源模块：agent_run_support），以下均为 handlers 内部直接使用的符号。
from .agent_run_support import (
    USER_INPUT_OFFLOAD_THRESHOLD,
    _AgentRunPreparationError,
    _AgentRunStreamCallbacks,
    _get_timestamp,
    _LatestModelSnapshotWriter,
    _load_runtime_skills_xml,
    _mark_agent_resume_checkpointed,
    _merge_message_snapshots,
    _offload_oversized_user_input,
    _persist_agent_model_snapshot,
    _persist_agent_partial_history,
    _persist_agent_turn_history_snapshot,
    _persist_agent_user_message,
    _persist_latest_ai_message_timestamp,
    _prepare_agent_semantic_context,
    _prepare_agent_workspace_runtime,
    _resolve_agent_initial_state,
    _resolve_file_attachments,
    _resolve_workspace_created_date,
    _write_agent_model_snapshot,
)
from .agent_run_support import (
    persist_early_user_message_if_needed as _persist_early_user_message_if_needed,
)

# 思考流/工具事件/工具参数运行态格式化助手（源模块：agent_stream_events，agent_stream 行数红线拆出）。
# re-export 保持既有模块属性引用（含测试的 agent_stream._xxx 直接调用）不变。
from .agent_stream_events import (  # noqa: F401  # 部分为有意重导出，供测试按模块属性引用
    REASONING_TOOL_DISPLAY_NAMES,
    _append_reasoning_segment,
    _coerce_tool_args,
    _collect_reasoning_candidates,
    _compile_reasoning_tool_name_patterns,
    _extract_reasoning_content,
    _extract_reasoning_from_candidates,
    _finish_running_tool,
    _format_reasoning_for_display,
    _format_thinking_event,
    _format_tool_event,
    _format_tool_lifecycle_event,
    _is_tool_result_error,
    _parse_tool_args_buffer,
    _register_running_tool,
    _resolve_active_tool_call_id,
    _ToolStreamState,
    _visible_tool_count,
)
from .services import conversation_service as conversation
from .services.agent_run.conversation_workspace import ConversationWorkspaceRuntime
from .services.agent_run.message_compose import (
    build_file_attachment_text,
    select_vision_attachments,
)
from .services.memory.conversation_memory_service import (
    after_agent_turn_with_memory,
    render_session_memory_block,
)
from .services.runtime_config import (
    LLM_FIRST_TOKEN_TIMEOUT_SECONDS,
)
from .services.setting_service import get_runtime_setting
from .sse_protocol import (
    SSE_EVENT_CONTEXT_COMPRESSION,
    SSE_EVENT_ERROR,
    SSE_EVENT_GENERATION_SPEED,
    SSE_EVENT_INBOX_ADMITTED,
    SSE_EVENT_MESSAGE,
    SSE_EVENT_SUBAGENT_CONTEXT_USAGE,
    SSE_EVENT_SUBAGENT_MESSAGE,
    SSE_EVENT_SUBAGENT_MESSAGE_DELTA,
    SSE_EVENT_SUBAGENT_THINKING_DELTA,
    SSE_EVENT_TOKEN_USAGE,
    SSE_EVENT_TOOL,
    SSE_EVENT_TOOL_COMPLETED,
    SSE_EVENT_TOOL_FAILED,
    SSE_EVENT_TOOL_PROGRESS,
    SSE_EVENT_TOOL_STARTED,
)

# 工具参数流式进度与 SSE 格式化（源模块：tool_stream_progress，agent_stream 行数红线拆出）。
# re-export 保持既有模块属性引用（含测试）不变。
from .tool_stream_progress import (
    _count_text_lines,  # noqa: F401  # 测试按模块属性引用 _count_text_lines，属有意重导出
    _extract_streaming_text_field,  # noqa: F401  # 测试按模块属性引用 _extract_streaming_text_field，属有意重导出
    _format_sse,
)

logger = get_logger(__name__)

AGENT_ID = "chat_data"


class AgentRunStreamFailedError(RuntimeError):
    """SSE 已发送用户可见错误，外层只需按失败状态收口 Run。"""


def _is_omlx_prefill_memory_rejected(exc: Exception) -> bool:
    """识别 oMLX 内存护栏拒绝（prefill_memory_exceeded）。

    openai SDK 已把 400 响应体解析进 ``APIStatusError.body``，按结构取 code；
    body 缺失或形状变化时按 message 关键字兜底。命中时重试无效——上下文
    体量不变则服务端仍会拒绝。
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        payload = error if isinstance(error, dict) else body
        code = str(payload.get("omlx_code") or payload.get("code") or "")
        if code == "prefill_memory_exceeded":
            return True
    return "prefill_memory_exceeded" in str(exc)


def _extract_status_error_message(exc: Exception) -> str | None:
    """从 APIStatusError 提取服务商 message 文本用于用户透出。

    只取 body.error.message（body 为字符串时取原文），剥离 request_id 噪音；
    空值或超长（>200 字符，防服务商透出大段内部诊断）返回 None，由调用方走
    兜底文案。永不透出堆栈与响应原文全文。
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        payload = error if isinstance(error, dict) else body
        message = str(payload.get("message") or "").strip()
    elif isinstance(body, str):
        message = body.strip()
    else:
        return None
    message = re.sub(r"\s*\(request_id[:：][^)]*\)", "", message, flags=re.IGNORECASE).strip()
    return message if 0 < len(message) <= 200 else None


def _model_failure_user_message(exc: Exception) -> str:
    """将模型服务异常映射为用户可读文案；不向用户透出原始错误细节。"""
    if isinstance(exc, openai.APITimeoutError):
        return "模型服务响应超时，请点击继续重试。"
    if isinstance(exc, openai.AuthenticationError):
        return "模型服务认证失败，请检查模型服务 API Key 配置后重试。"
    if isinstance(exc, openai.RateLimitError):
        return "模型服务当前繁忙，请稍后点击继续重试，或更换其他模型。"
    if isinstance(exc, openai.APIConnectionError):
        return "模型服务连接中断，请点击继续重试。"
    if isinstance(exc, TimeoutError):
        # 含 langchain-openai StreamChunkTimeoutError（其为内置 TimeoutError 子类）：
        # TCP 连接存活但长时间无任何流式内容返回（模型服务挂起/过载），由分块超时看门狗主动中断
        return "模型服务长时间未返回内容，请点击继续重试，或稍后再试。"
    if _is_omlx_prefill_memory_rejected(exc):
        # oMLX 内存护栏拒绝的 400：上下文体量超服务端推理内存上限，重试无效，
        # 引导用户缩减上下文而不是反复点击重试
        return "本轮对话上下文超出模型服务的内存上限，请新开会话，或减少本条消息引用的文件后重试。"
    if isinstance(exc, openai.APIStatusError):
        # 非枚举状态码（402 余额不足、403 配额、404 模型下线、5xx 网关错等）：
        # 透出服务商 message（提取+护栏），取不到时走兜底。具体子类
        # （超时/认证/限流）已在上方命中，不会进入本分支。
        detail = _extract_status_error_message(exc)
        if detail:
            return f"模型服务返回错误：{detail}"
    return "智能问数执行失败，请稍后重试。"


def _file_selection_mention_suffix(file_selections: list[str]) -> str:
    """用户在输入框 @ 引用工作区文件时，给 Human 消息追加模型可见的引用短句。

    前端 @ 菜单数据源是会话工作区（uploads/outputs/workspace）文件树，chip 随
    消息传 file_selections（相对路径列表）；引用的感知完全靠这行短句，模型从
    对话即可看到本轮用户点名了哪些文件。未引用时不追加任何内容。
    """
    if not file_selections:
        return ""
    references = "、".join(f"{USER_DATA_VIRTUAL_ROOT}/{path}" for path in file_selections)
    return f"\n\n[引用文件：{references}]"


def _quote_snippets_suffix(quoted_snippets: list[dict[str, str]] | None) -> str:
    """划选引用的模型可见后缀：片段原文按标记块逐一包住，追加在正文之后。

    前端划选助手回复片段后随消息传 quoted_snippets（[{"text": ...}]），
    语义等同 IM 引用回复——模型能看到被引用的原文与「继续问答」意图。
    开始/结束标记成对出现，前端气泡展示时按同一标记剥离（引用原文以
    additional_kwargs 的结构化字段渲染 chip，不在正文重复平铺）。
    """
    blocks = [str(item.get("text") or "").strip() for item in (quoted_snippets or [])]
    blocks = [block for block in blocks if block]
    if not blocks:
        return ""
    lines = "\n\n".join(f"[引用助手回复片段开始]\n{block}\n[引用助手回复片段结束]" for block in blocks)
    return f"\n\n{lines}"


def _build_user_message(
    content: str | list[str | dict[str, Any]],
    file_selections: list[str] | None = None,
    *,
    message_id: str | None = None,
    quoted_snippets: list[dict[str, str]] | None = None,
    **extra_kwargs: Any,
) -> HumanMessage:
    """构造带服务端时间戳和前端选择留痕的用户消息。

    content 为纯字符串（正文 + 附件占位行）；图片附件经 additional_kwargs
    的 ``vision_attachments`` 路径标记由 middleware 在请求层装载。
    用户 @ 引用工作区文件时在正文末尾追加引用短句（多选逐一列出），模型从对话即可看到本轮引用。
    划选引用（quoted_snippets）在正文之后追加原文标记块，并留痕 additional_kwargs
    供事件表持久化（历史回放据此渲染引用 chip）。
    """
    mention_suffix = _file_selection_mention_suffix(file_selections or [])
    quote_suffix = _quote_snippets_suffix(quoted_snippets)
    if isinstance(content, str):
        content = f"{content}{mention_suffix}{quote_suffix}"
    reasoning_turn_id = message_id or str(uuid4())
    additional_kwargs: dict[str, Any] = {
        "timestamp": _get_timestamp(),
        "reasoning_turn_id": reasoning_turn_id,
    }
    if file_selections:
        additional_kwargs["file_selections"] = list(file_selections)
    if quoted_snippets:
        additional_kwargs["quoted_snippets"] = [dict(item) for item in quoted_snippets]
    additional_kwargs.update({key: value for key, value in extra_kwargs.items() if value})
    return HumanMessage(id=reasoning_turn_id, content=content, additional_kwargs=additional_kwargs)


def _message_end_payload(usage: Any | None = None) -> dict[str, Any]:
    """构造消息结束事件，timestamp 表示服务端确认完成的时间。"""
    payload: dict[str, Any] = {"timestamp": _get_timestamp()}
    if usage:
        payload["usage"] = sanitize_context_usage_for_response(usage)
    return payload


def _latest_assistant_timestamp(state: Any) -> str:
    """从 checkpoint state 中读取最后一条 AI 消息的持久化时间。"""
    messages = (getattr(state, "values", None) or {}).get("messages") or []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        timestamp = str(additional_kwargs.get("timestamp") or "").strip()
        if timestamp:
            return timestamp
        break
    return ""


def _message_end_payload_from_state(state: Any, usage: Any | None = None) -> dict[str, Any]:
    """构造消息结束事件，优先使用已随 AIMessage 持久化的完成时间。"""
    payload = _message_end_payload(usage)
    persisted_timestamp = _latest_assistant_timestamp(state)
    if persisted_timestamp:
        payload["timestamp"] = persisted_timestamp
    return payload


def _tool_duration_in_window(breakdown: Any, window_start_ms: int, window_end_ms: int) -> int:
    """统计 usage.breakdown 中落在时间窗内的 tool 行执行时长总和（毫秒）。

    只认带精确起止毫秒戳的 tool 行（tool_timing 中间件盖章）；窗口外或数据不完整的行跳过。
    breakdown 为嵌套结构（消息行挂在「会话消息」节点的 children 下），需递归遍历。
    """
    tool_duration_ms = 0
    for item in _iter_breakdown_rows(breakdown):
        if item.get("role") != "tool":
            continue
        started_at_ms, ended_at_ms = item.get("started_at_ms"), item.get("ended_at_ms")
        if not isinstance(started_at_ms, int) or not isinstance(ended_at_ms, int) or ended_at_ms <= started_at_ms:
            continue
        if started_at_ms < window_start_ms or ended_at_ms > window_end_ms:
            continue
        tool_duration_ms += ended_at_ms - started_at_ms
    return tool_duration_ms


def _iter_breakdown_rows(items: Any) -> Iterator[dict[str, Any]]:
    """深度优先遍历 usage.breakdown（嵌套 children），产出所有 dict 节点。"""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        yield item
        yield from _iter_breakdown_rows(item.get("children"))


def _model_first_token_from_messages(messages: Any, first_token_wall_ms: int | None) -> int | None:
    """从 state 消息列表解析模型侧首 token 等待（毫秒）。

    context_usage 中间件把每次模型调用起止（epoch 毫秒）盖进响应 AIMessage 的
    additional_kwargs，随 checkpoint 持久化。模型侧首 token = 首 token 墙钟 −
    「覆盖首 token 时刻的那次模型调用」起点：即该次调用内从被调用到吐出首个
    可见 payload 的等待。首 token 不落在任何调用窗口内（调用间隙收包等数据
    瑕疵）视为不可信返回 None。
    """
    if first_token_wall_ms is None:
        return None
    for message in messages or []:
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        # 压缩摘要消息（context_summary）也是带戳 AIMessage，但摘要是编排行为，
        # 不参与模型侧口径。
        if additional_kwargs.get("context_summary"):
            continue
        started_at_ms = additional_kwargs.get("started_at_ms")
        ended_at_ms = additional_kwargs.get("ended_at_ms")
        if not isinstance(started_at_ms, int) or not isinstance(ended_at_ms, int):
            continue
        if started_at_ms <= first_token_wall_ms <= ended_at_ms:
            return first_token_wall_ms - started_at_ms
    return None


def _generation_stats_from_usage(
    *,
    first_token_ms: int | None,
    first_token_wall_ms: int | None,
    stream_ended_wall_ms: int | None,
    usage: Any | None,
    messages: Any | None = None,
    model_name: str | None = None,
) -> dict[str, int] | None:
    """汇总本轮生成统计：token 用量 + 速率 + 两侧首 token + 任务总耗时。

    口径对齐解码吞吐（业界 tok/s 惯例）：分母 = 首 token → 流结束的墙钟 − 工具
    执行时间（usage.breakdown 中 tool 行的精确起止，只统计落在解码窗口内的行）；
    TTFT 由 first_token_ms 单独表达，不进分母。流结束锚点缺失（异常中断路径）
    回退当前时刻。任一关键数据缺失不产 stats，不用估算冒充。
    任务总耗时 total_duration_ms 由 finish 层负责（需叠加打断段历史累计），
    不在本函数职责内。模型侧首 token（model_first_token_ms）从 state 消息列表
    的盖章解析；输入总 token（input_tokens）取 usage 累计口径；模型名优先取
    adapter 在 sanitize 前提取的记录（usage 的 llm_calls 已被剥离），回退从
    usage 提取。任一辅助字段缺失时省略该字段，不硬凑。
    """
    if first_token_ms is None or first_token_wall_ms is None:
        return None
    output_tokens = int((usage or {}).get("output_tokens") or 0) if isinstance(usage, dict) else 0
    if output_tokens <= 0:
        return None
    ended_wall_ms = int(stream_ended_wall_ms or time.time() * 1000)
    tool_duration_ms = _tool_duration_in_window((usage or {}).get("breakdown"), first_token_wall_ms, ended_wall_ms)
    duration_ms = (ended_wall_ms - first_token_wall_ms) - tool_duration_ms
    if duration_ms <= 0:
        return None
    stats = {
        "first_token_ms": first_token_ms,
        "duration_ms": duration_ms,
        "output_tokens": output_tokens,
        "tokens_per_second": round(output_tokens / (duration_ms / 1000)),
    }
    # 模型侧首 token：覆盖首 token 的那次调用起点 → 首 token 的等待（首 token 不
    # 落在任何调用窗口内则省略，不硬凑）。
    model_first_token_ms = _model_first_token_from_messages(messages, first_token_wall_ms)
    if model_first_token_ms is not None and model_first_token_ms > 0:
        stats["model_first_token_ms"] = model_first_token_ms
    stats.update(_usage_aux_stats(usage, model_name=model_name))
    return stats


def _usage_aux_stats(usage: Any | None, *, model_name: str | None = None) -> dict[str, Any]:
    """从 usage 提取辅助统计字段：输入总 token、缓存命中与模型名；缺失时省略不硬凑。

    model_name：adapter 在 sanitize 前从 llm_calls 提取的最后一次调用模型名，
    显式传入优先——收口时的 usage 可能已被 sanitize（llm_calls 剥离）。
    缓存命中：cumulative_cached_input_tokens / cumulative_cache_hit_rate 由
    context_usage 侧按模型实际上报聚合（未上报缓存信息的模型不产出，与
    ConversationTokenUsage 面板的「模型缓存」累计口径同源）。
    """
    if not isinstance(usage, dict):
        return {}
    aux: dict[str, Any] = {}
    # 输入总 token：本轮全部模型调用的输入累计。
    input_tokens = int(usage.get("cumulative_input_tokens") or usage.get("input_tokens") or 0)
    if input_tokens > 0:
        aux["input_tokens"] = input_tokens
    # 缓存命中：本轮全部模型调用的缓存输入累计（命中率随行，仅在输入可报时存在）。
    cached_input_tokens = int(usage.get("cumulative_cached_input_tokens") or 0)
    if cached_input_tokens > 0:
        aux["cached_input_tokens"] = cached_input_tokens
        cache_hit_rate = usage.get("cumulative_cache_hit_rate")
        if isinstance(cache_hit_rate, (int, float)) and cache_hit_rate > 0:
            aux["cache_hit_rate"] = round(float(cache_hit_rate), 1)
    # 模型名：多工具来回时以最终回复的调用为准。
    resolved_model_name = model_name or latest_model_name_from_usage(usage)
    if resolved_model_name:
        aux["model"] = resolved_model_name
    return aux


# 实时生成速率（generation_speed SSE）：滑窗口径的「当前速率」，与收口的整体平均
# （generation_stats）是两个指标。滑窗 = 最近 N 毫秒内流出的模型输出粗估 token ÷
# 活跃跨度；窗口静默（工具执行、调用间隙）不推送，前端保持上次值自然冻结。
_SPEED_WINDOW_MS = 2000
_SPEED_EMIT_INTERVAL_MS = 500
_SPEED_MIN_SPAN_MS = 300
# 与前端 estimateTokensFromText 同口径：CJK 字符 1 token/字，其余非空白 4 字符/token
_SPEED_CJK_CHAR_REGEX = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff01-\uff5e]")


def _estimate_stream_tokens(text: str) -> int:
    """流式文本的 token 粗估（与前端实时估算同公式），仅用于生成中的实时速率展示。"""
    cjk_count = 0
    other_count = 0
    for char in text:
        if char.isspace():
            continue
        if _SPEED_CJK_CHAR_REGEX.match(char):
            cjk_count += 1
        else:
            other_count += 1
    return cjk_count + (other_count + 3) // 4


def _validate_workspace_file_selections(file_selections: list[str], workspace_root: str) -> list[str]:
    """校验 @ 引用文件：必须落在当前会话工作区白名单目录内且真实存在。

    白名单校验复用 WORKSPACE_PATH_POLICY（拒越界/裸别名），系统结果区
    （outputs/query_result、outputs/.tool-offload）不可引用；不存在的文件
    （@ 后被删除）丢弃。无效条目记日志后跳过，不阻断本轮。
    """
    if not file_selections:
        return []
    valid: list[str] = []
    for relative_path in file_selections:
        try:
            logical = WORKSPACE_PATH_POLICY.parse(f"{USER_DATA_VIRTUAL_ROOT}/{relative_path}")
            target = resolve_workspace_path(workspace_root, f"{USER_DATA_VIRTUAL_ROOT}/{logical.normalized}")
            if is_system_result_workspace_path(workspace_root, target):
                raise ValueError("系统结果区文件不可引用")
            if not target.is_file():
                raise FileNotFoundError(relative_path)
        except (ValueError, OSError) as err:
            logger.warning("忽略无效的引用文件 %r: %s", relative_path, err)
            continue
        if logical.normalized not in valid:
            valid.append(logical.normalized)
    return valid


# 子智能体 custom channel 的入站接受集：这里的值来自子智能体进程内构造的
# event_type（属不信任输入），未知类型记 WARNING 后丢弃，不参与帧名派生。
_SUBAGENT_TOOL_EVENT_TYPES = {
    SSE_EVENT_TOOL_STARTED,
    SSE_EVENT_TOOL_COMPLETED,
    SSE_EVENT_TOOL_FAILED,
    SSE_EVENT_SUBAGENT_MESSAGE,
    SSE_EVENT_SUBAGENT_MESSAGE_DELTA,
    SSE_EVENT_SUBAGENT_THINKING_DELTA,
    SSE_EVENT_SUBAGENT_CONTEXT_USAGE,
}


@dataclass
class _MessageChunkFlags:
    """message_chunk 的载荷形态标记，供 _handle_message_chunk 分发。"""

    has_content: bool
    has_tool_calls: bool
    has_tool_call_chunks: bool
    is_ai_message: bool
    is_tool_message: bool
    thinking_event: str | None

    @classmethod
    def of(cls, message_chunk: Any) -> "_MessageChunkFlags":
        return cls(
            has_content=bool(getattr(message_chunk, "content", None)),
            has_tool_calls=bool(getattr(message_chunk, "tool_calls", None)),
            has_tool_call_chunks=bool(getattr(message_chunk, "tool_call_chunks", None)),
            is_ai_message=isinstance(message_chunk, (AIMessage, AIMessageChunk)),
            is_tool_message=isinstance(message_chunk, (ToolMessage, ToolMessageChunk)),
            thinking_event=_format_thinking_event(message_chunk),
        )

    @property
    def has_visible_payload(self) -> bool:
        return self.has_content or self.has_tool_calls or self.has_tool_call_chunks or bool(self.thinking_event)

    @property
    def thinking_only(self) -> bool:
        return (
            not self.has_content
            and not self.has_tool_calls
            and not self.has_tool_call_chunks
            and not self.is_tool_message
        )


@dataclass(frozen=True)
class _DurationRelayHooks:
    """打断接力的耗时回调束（见 _resolve_task_durations）：计时起点墙钟 + Run 行读/写回调。"""

    stream_started_wall_ms: int | None = None
    resolve_inherited_duration: Callable[[], Awaitable[dict[str, int]]] | None = None
    on_interrupt_finalize: Callable[[dict[str, int]], Awaitable[None]] | None = None


@dataclass(frozen=True)
class _DisconnectLog:
    """客户端断开时的日志束：告警文案与标识。"""

    message: str = "客户端已断开，停止流式输出: %s"
    id: str = ""


class _AgentSseStreamAdapter:
    """统一 create_agent 的 SSE 流处理，保留各 Agent 的可见消息过滤钩子。"""

    def __init__(
        self,
        *,
        agent: Any,
        initial_state: dict[str, Any],
        config: dict[str, Any],
        request: Request | None = None,
        visible_message_filter: Callable[[Any, dict[str, Any]], bool] | None = None,
        disconnect_log: _DisconnectLog | None = None,
        include_empty_tool_messages: bool = False,
        on_first_message: Callable[[], None] | None = None,
        on_stream_started: Callable[[], Awaitable[None]] | None = None,
        debug_label: str | None = None,
        user_id: str | None = None,
        reasoning_turn_id: str | None = None,
        duration_relay: _DurationRelayHooks | None = None,
    ) -> None:
        self.agent = agent
        self.initial_state = initial_state
        self.config = config
        self.request = request
        self.visible_message_filter = visible_message_filter
        log = disconnect_log or _DisconnectLog()
        self.disconnect_log_message = log.message
        self.disconnect_log_id = log.id
        self.include_empty_tool_messages = include_empty_tool_messages
        self.on_first_message = on_first_message
        self.on_stream_started = on_stream_started
        self.debug_label = debug_label
        self.user_id = user_id
        self.reasoning_turn_id = reasoning_turn_id
        self.tool_stream_state = _ToolStreamState()
        self.reasoning_segments: list[dict[str, Any]] = []
        self.archived_turn_messages: list[Any] = []
        self.latest_token_usage: dict[str, Any] | None = None
        # 最后一次模型调用的模型名（token_usage 事件 sanitize 前提取），finish 时进 stats
        self.latest_model_name: str = ""
        self.state: Any | None = None
        self.disconnected = False
        self._first_visible_payload_seen = False
        self._first_token_timeout_alarm_emitted = False
        self._queued_events: list[str] = []
        self.graph_started = False
        # 生成统计锚点（服务端口径）：首 token 墙钟与流结束墙钟（解码窗口用），
        # 计时起点墙钟（任务用时起点：优先客户端点击时刻，兜底服务端接收时刻；
        # 打断恢复段由 Run 行历史累计接力，见 _resolve_task_durations），finish 时汇总。
        self._first_token_wall_ms: int | None = None
        self.first_token_ms: int | None = None
        relay = duration_relay or _DurationRelayHooks()
        self._stream_started_wall_ms = relay.stream_started_wall_ms
        self._stream_ended_wall_ms: int | None = None
        # 打断接力（任务耗时累计）：恢复段惰性读取 Run 行历史累计；interrupt
        # 段结束时把累计值写回 Run 行，供恢复段接力。回调由装配层提供。
        self._resolve_inherited_duration = relay.resolve_inherited_duration
        self._on_interrupt_finalize = relay.on_interrupt_finalize
        self._inherited_durations: dict[str, int] | None = None
        # 实时生成速率（generation_speed）：滑窗 token 样本 (墙钟ms, 粗估token)，
        # 仅本轮流内有效；节流推送，窗口静默不推送。
        self._speed_samples: deque[tuple[int, int]] = deque()
        self._last_speed_emit_ms = 0

    def set_input_state(self, input_state: dict[str, Any]) -> None:
        """设置下一次 graph 调用的增量输入。"""
        self.initial_state = input_state

    def queue_event(self, event: str) -> None:
        """登记下一次 graph 调用前必须先发送的 SSE 事件。"""
        self._queued_events.append(event)

    async def stream_events(self) -> AsyncIterator[dict[str, Any] | str]:
        """输出 token_usage、thinking、tool、message 事件。"""
        while self._queued_events:
            yield self._queued_events.pop(0)
        started_at = time.perf_counter()
        if self._stream_started_wall_ms is None:
            self._stream_started_wall_ms = int(time.time() * 1000)
        timeout_task = asyncio.create_task(self._first_token_timeout_watchdog(started_at))
        try:
            async for sse_event in self._iter_agent_stream_events(started_at):
                yield sse_event
        except Exception as exc:
            if not self._first_visible_payload_seen:
                self._emit_first_token_timeout_if_needed(
                    started_at, ext2={"error_type": exc.__class__.__name__, "error": str(exc)}
                )
            emit_llm_connection_alarm_if_needed(
                exc,
                ext1=self._alarm_context(),
                ext2={"error_type": exc.__class__.__name__, "error": str(exc)},
            )
            raise
        finally:
            timeout_task.cancel()
            with suppress(asyncio.CancelledError):
                await timeout_task
            # 解码窗口终点：流退出（正常结束/异常/客户端断开）即盖章，避免 finish()
            # 里的 aget_state 等收尾耗时被算进生成时长。
            self._stream_ended_wall_ms = int(time.time() * 1000)

    async def _iter_agent_stream_events(self, started_at: float) -> AsyncIterator[dict[str, Any] | str]:
        """消费 agent.astream 原始事件流并转成 SSE 事件；客户端断开时提前结束。"""
        stream_started = False
        async for event in self.agent.astream(
            self.initial_state,
            config=self.config,
            stream_mode=["messages", "custom"],
        ):
            if not stream_started:
                stream_started = True
                await self._mark_stream_started()
            if await self._handle_disconnect():
                return

            event_kind, payload = self._parse_stream_event(event)
            if event_kind == "custom":
                for sse_event in self._handle_custom_payload(payload):
                    yield sse_event
                continue
            if event_kind != "messages":
                continue

            message_chunk, metadata = payload
            async for chunk_event in self._iter_message_chunk_events(message_chunk, metadata, started_at):
                yield chunk_event

    async def _mark_stream_started(self) -> None:
        """流首个事件到达：置位标记并触发外部回调（仅一次）。"""
        self.graph_started = True
        if self.on_stream_started:
            await self.on_stream_started()

    async def _iter_message_chunk_events(
        self, message_chunk: Any, metadata: Any, started_at: float
    ) -> AsyncIterator[str]:
        """按可见性过滤并处理单个消息 chunk，产出 SSE 事件。"""
        if self.visible_message_filter and not self.visible_message_filter(message_chunk, metadata):
            return
        # 首 token 锚定「用户看到的第一个字」：只认正文/thinking 流式字。工具调用
        # 参数（tool_call_chunks）虽是模型产出但对用户不可见（界面只有工具行动画），
        # 不能抢盖首 token 章，否则 TTFT 被低估（正文前的工具调用阶段全部漏计）。
        if self._is_user_visible_text_chunk(message_chunk):
            self._record_first_visible_payload(started_at)
        async for sse_event in self._handle_message_chunk(message_chunk, metadata):
            yield sse_event
        speed_event = self._maybe_emit_generation_speed()
        if speed_event:
            yield speed_event

    async def finish(
        self, *, persist_latest_ai_timestamp: bool = False, prefer_latest_usage: bool = False
    ) -> dict[str, Any]:
        """读取最终状态并构造 message_end payload。"""
        self.state = await self.agent.aget_state(self.config) if hasattr(self.agent, "aget_state") else None
        usage = self._resolve_end_usage(prefer_latest_usage)
        # 生成统计（服务端口径）随最后一条 AIMessage 持久化（历史回放可见），并随 message_end 下发。
        stats = _generation_stats_from_usage(
            first_token_ms=self.first_token_ms,
            first_token_wall_ms=self._first_token_wall_ms,
            stream_ended_wall_ms=self._stream_ended_wall_ms,
            usage=usage,
            messages=(getattr(self.state, "values", None) or {}).get("messages"),
            model_name=self.latest_model_name or None,
        )
        # 任务耗时累计（总墙钟，含打断段接力）独立于解码 stats：
        # 缺 token 数据时总耗时应照常产出；interrupt 段结束时累计值写回 Run 行接力。
        durations = await self._resolve_task_durations()
        if durations:
            stats = dict(stats or {})
            stats.update(durations)
        if persist_latest_ai_timestamp:
            self.state = await _persist_latest_ai_message_timestamp(
                self.agent,
                self.config,
                self.state,
                generation_stats=stats,
                completed_at_ms=self._stream_ended_wall_ms,
            )
        payload = _message_end_payload_from_state(self.state, usage)
        payload["generation_stats"] = stats
        return payload

    async def _load_inherited_durations(self) -> dict[str, int]:
        """惰性读取 Run 行历史打断段累计耗时（毫秒），仅读一次；失败降级为空 dict。"""
        if self._resolve_inherited_duration is None or self._inherited_durations is not None:
            return self._inherited_durations or {}
        try:
            stored = await self._resolve_inherited_duration()
            self._inherited_durations = dict(stored) if isinstance(stored, dict) else {}
        except Exception:
            logger.warning("读取打断段累计耗时失败，按 0 续算: run_id=%s", self.disconnect_log_id, exc_info=True)
            self._inherited_durations = {}
        return self._inherited_durations

    async def _finalize_interrupt_durations(self, durations: dict[str, int]) -> None:
        """interrupt 段结束时把累计耗时写回 Run 行，供恢复段接力；失败仅记日志。"""
        if self._on_interrupt_finalize is None:
            return
        try:
            await self._on_interrupt_finalize(durations)
        except Exception:
            logger.warning("打断段累计耗时写回失败: run_id=%s", self.disconnect_log_id, exc_info=True)

    async def _resolve_task_durations(self) -> dict[str, int]:
        """任务总耗时累计（毫秒）：本段 + Run 行历史打断段累计。

        打断（interrupt）段结束写回 Run 行（_finalize_interrupt_durations），恢复
        段惰性读取历史累计（_load_inherited_durations）接力相加——任务总用时只累计
        后端真实执行时间，不含用户应答等待。请求到达锚点缺失返回空 dict（不估算）。
        历史 Run 行可能残留 model_duration_ms 键，读取端忽略。
        """
        if self._stream_started_wall_ms is None:
            return {}
        ended_wall_ms = int(self._stream_ended_wall_ms or time.time() * 1000)
        segment_ms = ended_wall_ms - int(self._stream_started_wall_ms)
        if segment_ms < 0:
            return {}
        inherited = await self._load_inherited_durations()
        durations = {
            "total_duration_ms": segment_ms + max(int(inherited.get("total_duration_ms") or 0), 0),
        }
        if getattr(self.state, "interrupts", None):
            await self._finalize_interrupt_durations(durations)
        return {key: value for key, value in durations.items() if value > 0}

    def pending_interrupt(self) -> dict[str, Any] | None:
        """Return the single durable LangGraph interrupt payload, if present."""
        interrupts = getattr(self.state, "interrupts", None) or ()
        if not interrupts:
            return None
        value = getattr(interrupts[0], "value", None)
        if not isinstance(value, dict):
            logger.warning("忽略非结构化 Agent interrupt: %r", value)
            return None
        return dict(value)

    async def _handle_disconnect(self) -> bool:
        if not self.request or not await self.request.is_disconnected():
            return False
        logger.info(self.disconnect_log_message, self.disconnect_log_id)
        self.disconnected = True
        return True

    def _handle_custom_payload(self, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        handlers = {
            "token_usage": self._handle_token_usage_payload,
            "context_compression": self._handle_context_compression_payload,
            "inbox_admitted": self._handle_inbox_admitted_payload,
            "tool_progress": self._handle_tool_progress_payload,
            "subagent_tool": self._handle_subagent_tool_payload,
        }
        handler = handlers.get(str(payload.get("type") or ""))
        if handler is None:
            return []
        return handler(payload)

    def _handle_token_usage_payload(self, payload: dict[str, Any]) -> list[str]:
        if payload.get("usage"):
            raw_usage = payload.get("usage")
            # 模型名由发射端在 sanitize（剥 llm_calls）前提取，放 payload["model"]。
            # 非空才覆盖：subagent 等其他来源的 token_usage 事件可能无 model，
            # 不能清掉主 agent 已记录的名字。
            usage_model = str(payload.get("model") or "").strip()
            if usage_model:
                self.latest_model_name = usage_model
            self.latest_token_usage = sanitize_context_usage_for_response(raw_usage)
            return [_format_sse(SSE_EVENT_TOKEN_USAGE, self.latest_token_usage)]
        return []

    def _handle_context_compression_payload(self, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload.get("event"), dict):
            return []
        archived_messages = payload.get("archived_turn_messages")
        if isinstance(archived_messages, list):
            self.archived_turn_messages = _merge_message_snapshots(
                self.archived_turn_messages,
                archived_messages,
            )
        return [_format_sse(SSE_EVENT_CONTEXT_COMPRESSION, payload["event"])]

    def _handle_inbox_admitted_payload(self, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload.get("message_ids"), list):
            return []
        message_ids = [str(message_id) for message_id in payload["message_ids"] if str(message_id)]
        return [_format_sse(SSE_EVENT_INBOX_ADMITTED, {"message_ids": message_ids})] if message_ids else []

    def _handle_tool_progress_payload(self, payload: dict[str, Any]) -> list[str]:
        tool_call_id = str(payload.get("tool_call_id") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not tool_call_id or not tool_name or not message:
            logger.warning("忽略无效工具进度事件: %s", payload)
            return []
        return [
            _format_tool_lifecycle_event(
                SSE_EVENT_TOOL_PROGRESS,
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "status": "running",
                    "message": message,
                    "progress": payload.get("progress"),
                },
            )
        ]

    def _handle_subagent_tool_payload(self, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload.get("event"), dict):
            return []
        event = payload["event"]
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in _SUBAGENT_TOOL_EVENT_TYPES:
            logger.warning("忽略未知的子智能体工具事件类型: %s", event_type)
            return []
        # streamed 完整事件不带 content（前端已拼出全文），只补截断标记
        if (
            event_type == SSE_EVENT_SUBAGENT_MESSAGE
            and not event.get("streamed")
            and not str(event.get("content") or "").strip()
        ):
            return []
        delta_types = {SSE_EVENT_SUBAGENT_MESSAGE_DELTA, SSE_EVENT_SUBAGENT_THINKING_DELTA}
        if event_type in delta_types and not str(event.get("delta") or ""):
            return []
        data = {key: value for key, value in event.items() if key != "event_type"}
        return [_format_tool_lifecycle_event(event_type, data)]

    def _alarm_context(self) -> dict[str, Any]:
        configurable = self.config.get("configurable") if isinstance(self.config, dict) else {}
        return {
            "thread_id": (configurable or {}).get("thread_id") or "",
            "checkpoint_ns": (configurable or {}).get("checkpoint_ns") or "",
            "debug_label": self.debug_label or "",
            "user_id": self.user_id or "",
        }

    def _is_user_visible_text_chunk(self, message_chunk: Any) -> bool:
        """chunk 是否包含用户肉眼可见的流式文字（正文或 thinking）。

        首 token 锚定语义：用户看到的第一个字。工具调用参数虽经模型产出并被
        SSE 下发，但界面只渲染工具行动画，不流式展示文字，不计入首 token。
        """
        return bool(getattr(message_chunk, "content", None)) or bool(_format_thinking_event(message_chunk))

    def _record_first_visible_payload(self, started_at: float) -> None:
        if self._first_visible_payload_seen:
            return
        self._first_visible_payload_seen = True
        self._first_token_wall_ms = int(time.time() * 1000)
        # 首 token 起点对齐任务总耗时（点击/接收时刻）：用户视角「点发送 → 看到第一个字」
        # 的完整等待，含前置装配。起点锚点缺失（直连调用等）回退流启动 perf 差。
        if self._stream_started_wall_ms is not None:
            self.first_token_ms = max(self._first_token_wall_ms - int(self._stream_started_wall_ms), 0)
        else:
            self.first_token_ms = int((time.perf_counter() - started_at) * 1000)
        self._emit_first_token_timeout_if_needed(started_at)
        if self.on_first_message:
            self.on_first_message()

    async def _first_token_timeout_watchdog(self, started_at: float) -> None:
        threshold_seconds = int(LLM_FIRST_TOKEN_TIMEOUT_SECONDS or 0)
        if threshold_seconds <= 0:
            return
        await asyncio.sleep(threshold_seconds)
        if not self._first_visible_payload_seen and not self.disconnected:
            self._emit_first_token_timeout_if_needed(started_at)

    def _emit_first_token_timeout_if_needed(self, started_at: float, *, ext2: Any = None) -> None:
        threshold_seconds = int(LLM_FIRST_TOKEN_TIMEOUT_SECONDS or 0)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        if threshold_seconds <= 0 or elapsed_ms < threshold_seconds * 1000 or self._first_token_timeout_alarm_emitted:
            return
        self._first_token_timeout_alarm_emitted = True
        emit_llm_first_token_timeout_alarm(
            elapsed_ms=elapsed_ms,
            threshold_seconds=threshold_seconds,
            ext1=self._alarm_context(),
            ext2=ext2 or {"elapsed_ms": elapsed_ms, "threshold_seconds": threshold_seconds},
        )

    async def _handle_message_chunk(self, message_chunk: Any, metadata: dict[str, Any]) -> AsyncIterator[str]:
        flags = _MessageChunkFlags.of(message_chunk)
        if not flags.has_visible_payload and not (self.include_empty_tool_messages and flags.is_tool_message):
            return
        self._sample_chunk_generation_speed(message_chunk, flags)
        if flags.thinking_event:
            reasoning_content = _extract_reasoning_content(message_chunk)
            _append_reasoning_segment(
                self.reasoning_segments,
                reasoning_content,
                self.tool_stream_state.visible_tool_count(),
                self.reasoning_turn_id,
            )
            yield flags.thinking_event
            if flags.thinking_only:
                return
        if flags.has_tool_calls:
            for tool_event in self.tool_stream_state.handle_tool_calls(getattr(message_chunk, "tool_calls", []) or []):
                yield tool_event
        if flags.has_tool_call_chunks:
            for tool_event in self.tool_stream_state.handle_tool_call_chunks(
                getattr(message_chunk, "tool_call_chunks", []) or []
            ):
                yield tool_event
        if flags.is_tool_message:
            tool_payload = self.tool_stream_state.build_tool_message_payload(message_chunk)
            yield self.tool_stream_state.build_completion_event(tool_payload)
            yield _format_sse(SSE_EVENT_TOOL, tool_payload)
            return
        msg_data = message_chunk.model_dump()
        msg_data["timestamp"] = _get_timestamp()
        yield _format_sse(SSE_EVENT_MESSAGE, msg_data)

    def _sample_chunk_generation_speed(self, message_chunk: Any, flags: _MessageChunkFlags) -> None:
        """按 chunk 载荷形态采样实时速率：只统计用户可见文本（thinking/正文）。

        工具调用参数虽是模型产出，但界面只渲染工具行动画、不流式展示文字，且
        JSON 片段批量到达会显著抬高滑窗速率（与 TTFT 的「用户可见」口径对齐）；
        工具结果是工具执行产物不是模型输出，同样不计。
        """
        if flags.thinking_event:
            self._sample_generation_speed(_extract_reasoning_content(message_chunk))
        if flags.has_tool_call_chunks or flags.is_tool_message:
            return
        content = getattr(message_chunk, "content", None)
        if isinstance(content, str):
            self._sample_generation_speed(content)

    def _sample_generation_speed(self, text: str) -> None:
        """把一段流式模型输出按 token 粗估记入滑窗样本（墙钟毫秒戳）。"""
        tokens = _estimate_stream_tokens(text)
        if tokens > 0:
            self._speed_samples.append((int(time.time() * 1000), tokens))

    def _maybe_emit_generation_speed(self) -> str | None:
        """节流推送实时生成速率（generation_speed）；窗口静默返回 None，前端保持上次值。"""
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_speed_emit_ms < _SPEED_EMIT_INTERVAL_MS:
            return None
        window_start_ms = now_ms - _SPEED_WINDOW_MS
        while self._speed_samples and self._speed_samples[0][0] <= window_start_ms:
            self._speed_samples.popleft()
        if not self._speed_samples:
            return None
        window_tokens = sum(tokens for _, tokens in self._speed_samples)
        earliest_ms = min(timestamp for timestamp, _ in self._speed_samples)
        span_ms = max(now_ms - earliest_ms, _SPEED_MIN_SPAN_MS)
        self._last_speed_emit_ms = now_ms
        return _format_sse(
            SSE_EVENT_GENERATION_SPEED,
            {"tokens_per_second": round(window_tokens / (span_ms / 1000), 1), "window_ms": _SPEED_WINDOW_MS},
        )

    def _resolve_end_usage(self, prefer_latest_usage: bool) -> Any | None:
        state_usage = None
        if self.state and getattr(self.state, "values", None):
            state_usage = self.state.values.get("token_usage")
        if prefer_latest_usage:
            return self.latest_token_usage or state_usage
        # Keep legacy full checkpoints authoritative. New checkpoints contain
        # only compact counters, so use the in-memory full payload when present.
        if isinstance(state_usage, dict) and {"llm_calls", "breakdown"} & state_usage.keys():
            return state_usage
        return self.latest_token_usage or state_usage

    @staticmethod
    def _parse_stream_event(event: Any) -> tuple[str, Any]:
        if isinstance(event, tuple) and len(event) == 2:
            stream_mode, payload = event
            if stream_mode == "custom":
                return "custom", payload
            if stream_mode == "messages":
                return "messages", payload
            message_chunk, metadata = event
            return "messages", (message_chunk, metadata)
        return "unknown", event


def _is_chat_data_visible_stream_message(message_chunk: Any, metadata: dict[str, Any]) -> bool:
    """过滤智能问数 create_agent 流，避免工具内部模型调用进入主对话。"""
    node_name = str(metadata.get("langgraph_node") or "")
    if isinstance(message_chunk, (AIMessage, AIMessageChunk)):
        if node_name != "model":
            logger.debug("[过滤] 跳过来自 %s 节点的 AI 消息", node_name)
            return False
        return True
    if isinstance(message_chunk, (ToolMessage, ToolMessageChunk)):
        return node_name == "tools"
    # 增量输入的 HumanMessage 由前端用户气泡展示，不能再次作为助手正文输出。
    return False


async def _generate_chat_data_stream(
    req: ChatDataStreamRequest,
) -> AsyncIterator[str]:
    """流式执行单一 ChatData Agent。"""
    # 入参对象在此拆成局部变量：函数体与拆分前保持一致，避免大范围表达式改写造成的
    # 语义漂移；签名的收窄已经让「一次执行的完整入参」成为可复用的一等公民。
    thread_id = req.thread_id
    user_query = req.user_query
    datasource_id = req.datasource_id
    user_id = req.user_id
    username = req.username
    member_datasource_ids = req.member_datasource_ids
    semantic_config_id = req.semantic_config_id
    request = req.request
    model_id = req.model_id
    file_selections = req.file_selections
    quoted_snippets = req.quoted_snippets
    enable_thinking = req.enable_thinking
    thinking_effort = req.thinking_effort
    skill_names = req.skill_names
    options = req.options
    file_ids = req.file_ids
    run_id = req.run_id
    adopted_messages = req.adopted_messages
    resume_answer = req.resume_answer
    resume_turn_id = req.resume_turn_id
    turn_id = req.turn_id
    owner_token = req.owner_token
    stream_event_id_provider = req.stream_event_id_provider
    stream_started_wall_ms = req.stream_started_wall_ms
    started_at = time.perf_counter()
    last_phase_at = started_at

    def log_phase(phase: str) -> None:
        nonlocal last_phase_at
        now = time.perf_counter()
        logger.debug(
            "ChatData 阶段耗时: conversation_id=%s phase=%s elapsed_ms=%d total_ms=%d",
            thread_id,
            phase,
            int((now - last_phase_at) * 1000),
            int((now - started_at) * 1000),
        )
        last_phase_at = now

    datasource_id = str(datasource_id or "").strip() or None
    semantic_config_id = str(semantic_config_id or "").strip() or None
    normalized_skill_names = []
    for item in skill_names or []:
        skill_name = str(item or "").strip()
        if skill_name and skill_name not in normalized_skill_names:
            normalized_skill_names.append(skill_name)
    logger.info(f"开始执行智能问数，thread_id: {thread_id}, semantic_config_id: {semantic_config_id}")

    owner_task = asyncio.current_task()
    heartbeat_task = asyncio.create_task(_heartbeat_agent_run(run_id, owner_task, owner_token)) if run_id else None
    # 生命周期契约：本生成器只产出执行事实（业务事件 / error / question_requested），
    # 不发 done、不做收口。Run 的终态判定、complete_run/finish_run 调用与结构化
    # done 发布统一由 AgentRunRuntime（唯一生命周期 owner）完成。
    try:
        async for event in _run_chat_data_agent_stream(
            req=ChatDataStreamRequest(
                thread_id=thread_id,
                user_query=user_query,
                datasource_id=datasource_id,
                user_id=user_id,
                username=username,
                member_datasource_ids=member_datasource_ids,
                semantic_config_id=semantic_config_id,
                request=request,
                model_id=model_id,
                file_selections=file_selections,
                quoted_snippets=quoted_snippets,
                enable_thinking=enable_thinking,
                thinking_effort=thinking_effort,
                skill_names=normalized_skill_names,
                options=options,
                file_ids=file_ids,
                run_id=run_id,
                adopted_messages=adopted_messages,
                stream_started_wall_ms=stream_started_wall_ms,
                resume_answer=resume_answer,
                resume_turn_id=resume_turn_id,
                turn_id=turn_id,
                owner_token=owner_token,
                stream_event_id_provider=stream_event_id_provider,
                # 无人值守标记必须随转发重建透传，否则在内层被默认值覆盖
                disable_user_interaction=req.disable_user_interaction,
            ),
            log_phase=log_phase,
        ):
            yield event
    except AgentRunStreamFailedError:
        # 失败事实（error 事件）已由内层产出；异常必须继续抛给 Runtime，
        # 由唯一生命周期 owner 执行 finish_run("failed") 并发布 done {"status":"failed"}。
        # 若在此吞掉，Runtime 会把失败流当作正常结束走 complete_run，误标为 finished。
        logger.info("ChatData Agent 流式执行失败结束: conversation_id=%s", thread_id)
        raise
    except Exception as exc:
        # 进入内层守卫段之前（准备阶段）的失败：内层来不及产出 error 事实，
        # 这里补发一条；终态收口与 done 发布同样交给 Runtime。
        logger.error("智能问数执行准备失败: conversation_id=%s", thread_id, exc_info=True)
        yield _format_sse(SSE_EVENT_ERROR, {"error": _model_failure_user_message(exc)})
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


def _cancel_agent_owner_task(owner_task: asyncio.Task | None, reason: str) -> None:
    """owner 任务尚未结束时协作取消本地执行。"""
    if owner_task and not owner_task.done():
        owner_task.cancel(reason)


def _should_renew_heartbeat(heartbeat_status: str, owner_token: str | None) -> bool:
    """心跳为 active 且持有 owner token 时才续约。"""
    return heartbeat_status == "active" and bool(owner_token)


async def _heartbeat_agent_run(
    run_id: str,
    owner_task: asyncio.Task | None,
    owner_token: str | None = None,
) -> None:
    """保持运行租约；停止请求落到其他 worker 时协作取消本地执行。"""
    while True:
        # 租约 120s，5s 心跳已足够及时感知跨 worker 的取消/接管，
        # 同时把满载时的 inbox UPDATE 频率控制在个位数 QPS。
        await asyncio.sleep(5)
        try:
            heartbeat_status = await agent_inbox_service.heartbeat_run(run_id, owner_token)
            if _should_renew_heartbeat(heartbeat_status, owner_token) and owner_token is not None:
                await agent_run_event_service.renew_owner(run_id, owner_token)
        except AgentRunLeaseLostError:
            _cancel_agent_owner_task(owner_task, "agent_run_lease_lost")
            return
        except Exception:
            logger.warning("ChatData Agent Run 心跳续约失败，下一周期重试: run_id=%s", run_id, exc_info=True)
            continue
        if heartbeat_status == "cancelling":
            _cancel_agent_owner_task(owner_task, "agent_run_cancelled")
            return
        if heartbeat_status != "active":
            # waiting_user/finished are normal transitions performed by this
            # execution. Stop renewing without cancelling the stream, which
            # still needs to publish question_requested/done. A true takeover
            # keeps status active and fails the owner-token assertion above.
            return


def _create_agent_stream_adapter(
    callbacks: _AgentRunStreamCallbacks,
    *,
    agent: Any,
    initial_state: Any,
    config: dict[str, Any],
    request: Request | None,
    stream_started_wall_ms: int | None = None,
) -> "_AgentSseStreamAdapter":
    """装配本轮 Agent SSE 流适配器，挂到回调上下文并补发恢复重启事件。"""
    on_stream_started = (
        partial(agent_inbox_service.mark_question_resume_started, callbacks.run_id, callbacks.owner_token)
        if callbacks.resume_answer is not None and callbacks.run_id
        else None
    )
    # 打断接力回调：interrupt 段结束写回累计耗时、恢复段惰性读取接力（Run 行存储，
    # 绕开 interrupt 期间禁止写 checkpoint 的红线）。run_id 缺失时跳过（无接力）。
    inherited_duration_reader = (
        partial(agent_inbox_service.get_executed_durations, callbacks.run_id) if callbacks.run_id else None
    )
    interrupt_duration_writer = (
        partial(agent_inbox_service.add_executed_durations, callbacks.run_id) if callbacks.run_id else None
    )
    adapter = _AgentSseStreamAdapter(
        agent=agent,
        initial_state=initial_state,
        config=config,
        request=request,
        visible_message_filter=_is_chat_data_visible_stream_message,
        disconnect_log=_DisconnectLog(id=callbacks.thread_id),
        debug_label="AI Content from model",
        user_id=callbacks.user_id,
        include_empty_tool_messages=True,
        on_first_message=partial(callbacks.log_phase, "first_model_chunk"),
        on_stream_started=on_stream_started,
        reasoning_turn_id=callbacks.resume_turn_id
        or callbacks.turn_id
        or (
            str(callbacks.user_message.additional_kwargs.get("reasoning_turn_id") or "")
            if callbacks.user_message
            else None
        ),
        duration_relay=_DurationRelayHooks(
            stream_started_wall_ms=stream_started_wall_ms,
            resolve_inherited_duration=inherited_duration_reader,
            on_interrupt_finalize=interrupt_duration_writer,
        ),
    )
    callbacks.stream_adapter = adapter
    return adapter


async def _resolve_inbox_message_references(
    message: HumanMessage,
    *,
    workspace_root: str,
    user_id: str | None,
    thread_id: str,
) -> HumanMessage:
    """把排队消息 additional_kwargs 里的引用/附件解析为与普通发送同构的形态。

    「存引用、晚解析」的消费端：@ 引用在校验工作区白名单后追加引用短句，
    附件 file_id 解析为附件占位行与 vision 标记——与首条用户消息
    （_build_user_message + 附件文本）同一条代码语义。必须在 workspace
    就绪之后、进入 initial_state 之前调用（图输入与持久化共享解析结果）；
    失效条目记日志跳过，不阻断消息。
    """
    kwargs = getattr(message, "additional_kwargs", None) or {}
    selections = kwargs.get("file_selections") or []
    file_ids = kwargs.get("file_ids") or []
    quoted_snippets = parse_quoted_snippets(kwargs.get("quoted_snippets"))
    if not selections and not file_ids and not quoted_snippets:
        return message
    valid_selections = _validate_workspace_file_selections(selections, workspace_root) if selections else []
    file_attachments = (
        await _resolve_file_attachments(
            file_ids,
            user_id=user_id,
            thread_id=thread_id,
        )
        if file_ids
        else []
    )
    new_kwargs = dict(kwargs)
    if selections:
        new_kwargs["file_selections"] = valid_selections
    if quoted_snippets:
        # 片段为纯文本，无外部资源可校验；重解析去重截断后留痕，供事件表持久化
        new_kwargs["quoted_snippets"] = quoted_snippets
    _, max_image_size = await get_image_size_limit()
    vision_attachments = select_vision_attachments(file_attachments, max_image_size=max_image_size)
    if vision_attachments:
        new_kwargs["vision_attachments"] = vision_attachments
    new_content = (
        f"{message.content}"
        f"{_file_selection_mention_suffix(valid_selections)}"
        f"{_quote_snippets_suffix(quoted_snippets)}"
        f"{build_file_attachment_text(file_attachments)}"
    )
    return message.model_copy(update={"content": new_content, "additional_kwargs": new_kwargs})


async def _persist_admitted_inbox_messages(
    callbacks: _AgentRunStreamCallbacks,
    messages: list[HumanMessage],
) -> None:
    """把已解析的排队消息持久化为 admitted 用户历史（上屏与回放的依据）。"""
    for message in messages:
        await conversation_event_service.persist_user_message(
            conversation_id=callbacks.thread_id,
            user_id=callbacks.user_id or "system",
            operator_name=callbacks.username or callbacks.user_id or "system",
            message=message,
        )


async def _admit_agent_inbox_messages(
    callbacks: _AgentRunStreamCallbacks,
    messages: list[HumanMessage],
) -> None:
    """steering 中间件认领路径：解析随行引用/附件后持久化。

    就地替换列表元素：中间件随后把同一列表注入图 state，替换后图输入
    拿到的即是解析后的消息（与持久化内容一致）。
    """
    for index, message in enumerate(messages):
        messages[index] = await _resolve_inbox_message_references(
            message,
            workspace_root=callbacks.workspace_root,
            user_id=callbacks.user_id,
            thread_id=callbacks.thread_id,
        )
    await _persist_admitted_inbox_messages(callbacks, messages)


async def _log_agent_stream_done(callbacks: _AgentRunStreamCallbacks) -> None:
    callbacks.log_phase("agent_stream_done")


async def _mark_agent_waiting_for_user(
    callbacks: _AgentRunStreamCallbacks,
    question: dict[str, Any],
) -> None:
    if not callbacks.run_id:
        return
    question["turn_id"] = callbacks.effective_turn_id
    await agent_inbox_service.mark_waiting_for_user(
        callbacks.run_id,
        question,
        callbacks.user_id or "system",
        callbacks.username or callbacks.user_id or "system",
        callbacks.owner_token,
    )


async def _confirm_agent_consumed_messages(callbacks: _AgentRunStreamCallbacks) -> None:
    if callbacks.run_id:
        await agent_inbox_service.mark_consumed(
            callbacks.run_id,
            callbacks.steering_tracker.drain(),
            callbacks.owner_token,
        )


async def _before_agent_stream(callbacks: _AgentRunStreamCallbacks) -> bool:
    if callbacks.request and await callbacks.request.is_disconnected():
        logger.info("客户端已断开，停止流式输出: %s", callbacks.thread_id)
        if callbacks.resume_answer is not None and callbacks.run_id:
            await agent_inbox_service.restore_question_answer(
                callbacks.run_id,
                callbacks.user_id or "system",
                callbacks.username or callbacks.user_id or "system",
                callbacks.owner_token,
            )
        return False
    return True


async def _prepare_agent_prompt_bundle(
    semantic_materialize_note: str,
    user_id: str | None,
    thread_id: str,
    workspace_runtime: ConversationWorkspaceRuntime,
) -> tuple[Any, str]:
    """构建数据上下文与会话技能提示词清单。

    静态注入下线后，数据上下文区唯一有效字段是物化说明；恒传 dict 以兼容
    build_unified_system_prompt 的既有口径（无说明时为空 dict → 空文本）。
    """
    data_context: Any = {"semantic_materialize_note": semantic_materialize_note} if semantic_materialize_note else {}
    available_skills_xml = await _load_runtime_skills_xml(
        user_id or "system",
        thread_id,
        workspace_runtime,
    )
    return data_context, available_skills_xml


@dataclass(frozen=True)
class TurnUserInput:
    """本轮用户侧输入：消息素材与上一轮遗留的排队消息。"""

    user_query: str
    message_content: str
    file_selections: list[str]
    vision_attachments: list[str]
    skill_names: list[str]
    quoted_snippets: list[dict[str, str]]
    adopted_messages: list[HumanMessage] | None = None


@dataclass(frozen=True)
class TurnResumeContext:
    """恢复相关的上下文（恢复答案、所属 turn）。"""

    resume_answer: dict[str, Any] | None
    turn_id: str | None


@dataclass(frozen=True)
class TurnRuntimeContext:
    """本轮执行所需的不变上下文：操作人、会话、数据源、语义、运行时与提示素材。"""

    user_id: str | None
    username: str | None
    thread_id: str
    datasource_id: str | None
    semantic_context: Any
    runtime_options: Any
    workspace_root: str
    data_context: Any
    available_skills_xml: str
    memory_block: str


async def _resolve_agent_turn_inputs(
    *,
    user_input: TurnUserInput,
    resume: TurnResumeContext,
    runtime: TurnRuntimeContext,
    receipt_timestamp_ms: int | None = None,
) -> tuple[HumanMessage | None, Any, str, list[HumanMessage] | None]:
    """构建用户消息、initial_state 与系统提示词。

    receipt_timestamp_ms：计时起点墙钟（Run 行章，优先客户端点击时刻）。传入时
    把它格式化后覆盖用户消息时间戳——气泡显示、历史回放、harness 首 token/总耗时
    三者同锚；缺失回退消息构建时刻（_build_user_message 默认章）。
    """
    # 三个入参对象在此还原为局部变量：函数体与拆分前保持一致，避免表达式级改写。
    user_query = user_input.user_query
    message_content = user_input.message_content
    file_selections = user_input.file_selections
    vision_attachments = user_input.vision_attachments
    skill_names = user_input.skill_names
    quoted_snippets = user_input.quoted_snippets
    adopted_messages = user_input.adopted_messages
    resume_answer = resume.resume_answer
    turn_id = resume.turn_id
    user_id = runtime.user_id
    username = runtime.username
    thread_id = runtime.thread_id
    datasource_id = runtime.datasource_id
    semantic_context = runtime.semantic_context
    runtime_options = runtime.runtime_options
    workspace_root = runtime.workspace_root
    data_context = runtime.data_context
    available_skills_xml = runtime.available_skills_xml
    memory_block = runtime.memory_block
    message_kwargs: dict[str, Any] = {}
    if skill_names:
        message_kwargs["skill_names"] = skill_names
    if receipt_timestamp_ms:
        # _build_user_message 先盖构建时刻章、后 merge extra_kwargs，这里传即覆盖
        message_kwargs["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(receipt_timestamp_ms / 1000))
    user_message = None
    if resume_answer is None and (user_query.strip() or not adopted_messages):
        user_message = _build_user_message(
            message_content,
            file_selections,
            message_id=turn_id,
            vision_attachments=vision_attachments,
            quoted_snippets=quoted_snippets,
            **message_kwargs,
        )
    initial_state = _resolve_agent_initial_state(
        resume_answer=resume_answer,
        user_id=user_id,
        operator_name=username,
        conversation_id=thread_id,
        adopted_messages=adopted_messages,
        user_message=user_message,
        datasource_id=datasource_id,
        semantic_context=semantic_context,
        runtime_options=runtime_options,
        workspace_root=workspace_root,
    )
    system_prompt = build_unified_system_prompt(
        runtime_options,
        data_context=data_context,
        workspace_root=workspace_root,
        available_skills_xml=available_skills_xml,
        memory_block=memory_block,
    )
    return user_message, initial_state, system_prompt, adopted_messages


async def _admit_adopted_messages(
    callbacks: "_AgentRunStreamCallbacks",
    stream_adapter: "_AgentSseStreamAdapter",
    steering_tracker: SteeringMessageTracker,
    adopted_messages: list[HumanMessage] | None,
) -> None:
    """上一轮异常遗留的排队消息由本轮接管，作为新一轮的首批输入：
    先持久化并通知前端上屏，再随 initial_state 进入本轮对话。
    消息的随行引用/附件已在 workspace 就绪后统一解析（见主流程），此处只持久化。"""
    await _persist_admitted_inbox_messages(callbacks, adopted_messages or [])
    if adopted_messages:
        adopted_ids = [str(message.id or "") for message in adopted_messages]
        steering_tracker.record(adopted_ids)
        stream_adapter.queue_event(
            _format_sse(
                SSE_EVENT_INBOX_ADMITTED,
                {
                    "message_ids": adopted_ids,
                },
            )
        )


async def _run_chat_data_agent_stream(
    *,
    req: ChatDataStreamRequest,
    log_phase: Callable[[str], None],
) -> AsyncIterator[str]:
    """执行已注册 active run 的 Agent 流，生命周期由外层统一收口。"""
    # 与 _generate_chat_data_stream 同一口径：入参对象在此还原为局部变量，函数体保持不变。
    thread_id = req.thread_id
    user_query = req.user_query
    datasource_id = req.datasource_id
    user_id = req.user_id
    username = req.username
    member_datasource_ids = req.member_datasource_ids
    semantic_config_id = req.semantic_config_id
    request = req.request
    model_id = req.model_id
    file_selections = req.file_selections
    quoted_snippets = parse_quoted_snippets(req.quoted_snippets)
    enable_thinking = req.enable_thinking
    thinking_effort = req.thinking_effort
    skill_names = req.skill_names
    file_ids = req.file_ids
    run_id = req.run_id
    adopted_messages = req.adopted_messages
    resume_answer = req.resume_answer
    resume_turn_id = req.resume_turn_id
    turn_id = req.turn_id
    owner_token = req.owner_token
    stream_event_id_provider = req.stream_event_id_provider
    stream_started_wall_ms = req.stream_started_wall_ms
    # 无人值守运行（定时轮派发）：工具装配剔除 ask_user_question
    disable_user_interaction = req.disable_user_interaction
    selected_files = parse_file_selections(file_selections)
    try:
        # 无依赖的准备项一次并行：语义上下文（含数据源校验）/ 子智能体热默认 /
        # 会话创建日期 / 附件解析。语义上下文失败仍按 _AgentRunPreparationError
        # 终止本轮；其余任务异常与串行版相同直接冒泡，不改变错误处理边界。
        semantic_context, hot_defaults, created_date, file_attachments = await asyncio.gather(
            _prepare_agent_semantic_context(
                thread_id=thread_id,
                user_query=user_query,
                datasource_id=datasource_id,
                user_id=user_id,
                username=username,
                member_datasource_ids=member_datasource_ids,
                semantic_config_id=semantic_config_id,
            ),
            get_subagent_hot_defaults(),
            _resolve_workspace_created_date(thread_id, user_id),
            _resolve_file_attachments(file_ids, user_id=user_id, thread_id=thread_id),
        )
        datasource_id = semantic_context.datasource_id
        # 部署能力（隔离后端/Bash）只由服务端配置决定：不再接受请求级 runtime_options 覆盖
        runtime_options = resolve_agent_runtime_options(
            user_id=user_id,
            **hot_defaults,
        )
        log_phase("prepare_inputs")
        workspace_paths, workspace_runtime, semantic_materialize_note = await _prepare_agent_workspace_runtime(
            thread_id=thread_id,
            datasource_id=datasource_id,
            user_id=user_id,
            semantic_context=semantic_context,
            runtime_options=runtime_options,
            created_date=created_date,
        )
        log_phase("workspace_runtime")
    except _AgentRunPreparationError as exc:
        yield _format_sse(SSE_EVENT_ERROR, {"error": exc.message})
        raise AgentRunStreamFailedError from None
    # 引用文件校验必须在 workspace 就绪之后：@ 菜单可引用的文件以工作区实际内容为准。
    valid_file_selections = _validate_workspace_file_selections(selected_files, workspace_paths["root"])

    # 排队消息（续跑接管）随行引用/附件的「晚解析」：workspace 就绪后、进入
    # initial_state 前完成，图输入与后续持久化共享同一份解析结果。
    if adopted_messages:
        for adopted_index, adopted_message in enumerate(adopted_messages):
            adopted_messages[adopted_index] = await _resolve_inbox_message_references(
                adopted_message,
                workspace_root=workspace_paths["root"],
                user_id=user_id,
                thread_id=thread_id,
            )

    data_context, available_skills_xml = await _prepare_agent_prompt_bundle(
        semantic_materialize_note,
        user_id,
        thread_id,
        workspace_runtime,
    )
    # 会话记忆注入段（每轮构建时读取）：由系统自动维护，主图 system prompt
    # 与子图只读段共用同一份渲染产物；空串时各消费方自动省略整段。
    memory_block = render_session_memory_block(
        workspace_paths["root"],
        conversation_id=thread_id,
    )

    config = {
        # 子智能体（subagents.py::build_subagent_invocation_config）读同一配置，
        # 主/子图口径同源；运行期经后台配置变量表读取（新会话装配时生效），
        # 不设置时 langgraph 默认仅 25 个 super-step。
        "recursion_limit": await get_runtime_setting("CHAT_DATA_AGENT_RECURSION_LIMIT"),
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
    }

    steering_tracker = SteeringMessageTracker()

    # 用户输入超长时自动转为文件写入 workspace outputs
    user_message_content = user_query
    if len(user_query) > USER_INPUT_OFFLOAD_THRESHOLD:
        user_message_content, offload_attachment = await _offload_oversized_user_input(
            user_query,
            workspace_runtime,
            thread_id,
        )
        file_attachments.append(offload_attachment)

    try:
        async with get_checkpointer() as checkpointer:
            log_phase("open_checkpointer")
            # 用户消息保持纯文本（正文 + 附件占位行）；合规图片附件只打
            # vision_attachments 路径标记，由 ImageInjectionMiddleware 在模型
            # 请求层按当轮 vlm 能力位装载——base64 不进入 state/checkpoint/事件表。
            vision_attachments = select_vision_attachments(
                file_attachments, max_image_size=(await get_image_size_limit())[1]
            )
            attachment_text = build_file_attachment_text(file_attachments)
            message_content = f"{user_message_content}{attachment_text}"

            # 插件启停快照刷新与 turn 输入解析无依赖，并行后一并计入 build_system_prompt 段；
            # refresh 内部 fail-closed 不抛异常，create_data_agent 读到的快照必然已刷新。
            (user_message, initial_state, system_prompt, adopted_messages), _ = await asyncio.gather(
                _resolve_agent_turn_inputs(
                    user_input=TurnUserInput(
                        user_query=user_query,
                        message_content=message_content,
                        file_selections=valid_file_selections,
                        vision_attachments=vision_attachments,
                        skill_names=skill_names or [],
                        quoted_snippets=quoted_snippets,
                        adopted_messages=adopted_messages,
                    ),
                    resume=TurnResumeContext(
                        resume_answer=resume_answer,
                        turn_id=turn_id,
                    ),
                    runtime=TurnRuntimeContext(
                        user_id=user_id,
                        username=username,
                        thread_id=thread_id,
                        datasource_id=datasource_id,
                        semantic_context=semantic_context,
                        runtime_options=runtime_options,
                        workspace_root=workspace_paths["root"],
                        data_context=data_context,
                        available_skills_xml=available_skills_xml,
                        memory_block=memory_block,
                    ),
                    receipt_timestamp_ms=stream_started_wall_ms,
                ),
                refresh_plugin_state(),
            )
            log_phase("build_system_prompt")

            callbacks = _AgentRunStreamCallbacks(
                thread_id=thread_id,
                user_id=user_id,
                username=username,
                run_id=run_id,
                owner_token=owner_token,
                request=request,
                resume_answer=resume_answer,
                resume_turn_id=resume_turn_id,
                turn_id=turn_id,
                stream_event_id_provider=stream_event_id_provider,
                log_phase=log_phase,
                steering_tracker=steering_tracker,
                workspace_root=workspace_paths["root"],
                adopted_messages=adopted_messages,
                user_message=user_message,
            )
            model_snapshot_writer = _LatestModelSnapshotWriter(
                partial(_write_agent_model_snapshot, callbacks),
                task_name=f"chatdata-model-snapshot-{thread_id}",
            )
            callbacks.model_snapshot_writer = model_snapshot_writer

            agent = create_data_agent(
                runtime_options,
                checkpointer=checkpointer,
                conversation_id=thread_id,
                identity_context=IdentityContext(
                    user_id=user_id,
                    operator_name=username,
                ),
                datasource_context=DatasourceContext(
                    datasource_id=datasource_id,
                    source_type=str((semantic_context.datasource_meta or {}).get("source_type") or ""),
                ),
                model_invocation=ModelInvocationConfig(
                    system_prompt=system_prompt,
                    model_id=model_id,
                    enable_thinking=enable_thinking,
                    thinking_effort=thinking_effort,
                    available_skills_xml=available_skills_xml,
                    memory_block=memory_block,
                ),
                workspace_runtime=workspace_runtime,
                run_controls=AgentRunControls(
                    run_id=run_id,
                    owner_token=owner_token,
                    inbox_service=agent_inbox_service,
                    admit_inbox_messages=partial(_admit_agent_inbox_messages, callbacks),
                    steering_tracker=steering_tracker,
                    disable_user_interaction=disable_user_interaction,
                ),
                persist_model_snapshot=partial(_persist_agent_model_snapshot, callbacks),
            )
            log_phase("create_agent")
            stream_adapter = _create_agent_stream_adapter(
                callbacks,
                agent=agent,
                initial_state=initial_state,
                config=config,
                request=request,
                stream_started_wall_ms=stream_started_wall_ms,
            )

            await _admit_adopted_messages(
                callbacks,
                stream_adapter,
                steering_tracker,
                adopted_messages,
            )

            async for sse_event in stream_agent_turn(
                stream_adapter=stream_adapter,
                format_sse=_format_sse,
                persistence=TurnPersistenceHooks(
                    persist_user_message=partial(_persist_agent_user_message, callbacks),
                    persist_turn_history=partial(_persist_agent_turn_history_snapshot, callbacks),
                    persist_partial_history=partial(_persist_agent_partial_history, callbacks),
                ),
                hooks=TurnLifecycleHooks(
                    before_stream=partial(_before_agent_stream, callbacks),
                    after_finish=partial(_mark_agent_resume_checkpointed, callbacks),
                    # 会话记忆收尾钩子下沉到服务层：先同步打点，再调度防抖抽取
                    # （仅整轮正常结束时触发；interrupt/disconnected 不抽取）。
                    after_turn=partial(
                        after_agent_turn_with_memory,
                        partial(_log_agent_stream_done, callbacks),
                        stream_adapter,
                        conversation_id=thread_id,
                        workspace_root=workspace_paths["root"],
                        user_id=user_id,
                        username=username,
                        model_id=model_id,
                    ),
                    after_stream=partial(_confirm_agent_consumed_messages, callbacks),
                    on_interrupt=partial(_mark_agent_waiting_for_user, callbacks),
                ),
                # finish 时把完成时间与生成统计回写最后一条 AIMessage（随 checkpoint
                # 进入 turn 历史持久化），message_end 的时间戳与统计也取持久化值。
                resources=TurnResourceOptions(finalize_stream_kwargs={"persist_latest_ai_timestamp": True}),
            ):
                yield sse_event

    except ContextInputLimitExceededError as exc:
        logger.info("智能问数上下文超出输入容量: conversation_id=%s", thread_id)
        yield _format_sse(SSE_EVENT_ERROR, {"error": str(exc)})
        raise AgentRunStreamFailedError from None
    except Exception as exc:
        logger.error("智能问数执行失败: conversation_id=%s", thread_id, exc_info=True)
        yield _format_sse(SSE_EVENT_ERROR, {"error": _model_failure_user_message(exc)})
        raise AgentRunStreamFailedError from None


def _resolve_client_timestamp_ms(raw: int | None, *, now_ms: int, max_skew_ms: int = 3_600_000) -> int:
    """校验前端点击发送时刻（epoch 毫秒），非法或钟偏超界回退服务端接收章。

    与用户气泡显示同源同锚「点击时刻」：气泡时间是浏览器本地钟，harness 首
    token/总耗时起点跟随它，用户才能拿「气泡 → 首字 / AI 气泡」自行对账。
    防护：非正数回退；客户端钟快于服务端时钳到 now（发送不可能在未来，避免
    负等待）；钟偏超过 max_skew_ms（默认 1 小时，容忍传输延迟/排队重放）视为
    客户端钟失真，回退服务端接收章。
    """
    if not raw or raw <= 0:
        return now_ms
    if now_ms < raw:  # 客户端钟偏快：钳到服务端当前时刻
        return now_ms
    if now_ms - raw > max_skew_ms:
        return now_ms
    return int(raw)


async def sql_completion_handler(
    conversation_id: str,
    query: str,
    options: dict[str, Any] | None = None,
    user_id: str = "system",
    username: str | None = None,
    request: Request | None = None,
    file_ids: list[str] | None = None,
    queued_message_id: str | None = None,
    preserve_queued_messages: bool = False,
    client_timestamp_ms: int | None = None,
) -> StreamingResponse:
    """
    智能问数 completion 处理器。
    """
    # TTFT 观测：本函数是「HTTP 入口 → 后台 run 启动」盲区的主要耗时段
    # （start_run 的 adopt/落库 + 提交调度），此前零日志、波动 2~6s 无法归因。
    # 与 graph 内 log_phase 共用「ChatData 阶段耗时」前缀，一条 grep 串起全链路。
    handler_started_at = time.perf_counter()
    # 请求到达墙钟（毫秒）：用户气泡对齐口径的 harness 首 token / 任务总耗时
    # 起点，随 Run 行透传到流工厂；后续落库/调度/prepare 盲区全部计入等待。
    stream_started_wall_ms = int(time.time() * 1000)
    # 前端点击发送时刻优先（与用户气泡显示同源同锚），校验后覆盖服务端接收章
    stream_started_wall_ms = _resolve_client_timestamp_ms(client_timestamp_ms, now_ms=stream_started_wall_ms)
    run_options = parse_completion_run_options(options)

    # 保持懒创建会话；数据源组在生成流开始时写入业务会话表并校验。
    await conversation.get_or_create_conversation(AGENT_ID, conversation_id, user_id)
    runtime_context = {
        # 明确执行队列消息时，模型输入来自 adopted_messages，不能再把
        # completion 的展示文本作为第二条当前用户消息注入。
        "query": "" if queued_message_id else query,
        "datasource_id": run_options.datasource_id,
        "member_datasource_ids": run_options.member_datasource_ids,
        "semantic_config_id": run_options.semantic_config_id,
        "model_id": run_options.model_id,
        "file_selections": run_options.file_selections,
        "quoted_snippets": run_options.quoted_snippets,
        "enable_thinking": run_options.enable_thinking,
        "thinking_effort": run_options.thinking_effort,
        "skill_names": run_options.skill_names,
        "operator_name": username or user_id,
        "options": options or {},
        "file_ids": file_ids or [],
        # 无人值守标记（评测/批量等内部调用方经 options 传入）：装配剔除
        # ask_user_question；chat 入口恒 False
        "disable_user_interaction": run_options.disable_user_interaction,
    }
    run_id, adopted_messages = await agent_inbox_service.start_run(
        conversation_id,
        user_id,
        username or user_id,
        runtime_context,
        adopt_pending_messages=not queued_message_id and not preserve_queued_messages,
        selected_message_id=queued_message_id,
        stream_started_wall_ms=stream_started_wall_ms,
    )
    await _persist_early_user_message_if_needed(
        conversation_id, user_id, username or user_id, run_id, query, queued_message_id, preserve_queued_messages
    )

    logger.info(
        "ChatData 阶段耗时: conversation_id=%s phase=completion.start_run elapsed_ms=%d total_ms=%d",
        conversation_id,
        int((time.perf_counter() - handler_started_at) * 1000),
        int((time.perf_counter() - handler_started_at) * 1000),
    )

    agent_run_runtime.submit(
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=user_id,
        operator_name=username or user_id,
    )
    logger.info(
        "ChatData 阶段耗时: conversation_id=%s phase=completion.handler_done elapsed_ms=%d total_ms=%d",
        conversation_id,
        int((time.perf_counter() - handler_started_at) * 1000),
        int((time.perf_counter() - handler_started_at) * 1000),
    )
    return StreamingResponse(
        _stream_run_events(run_id, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-ChatData-Run-Id": run_id,
        },
    )


async def _stream_run_events(
    run_id: str,
    last_event_id: str = "0-0",
    request: Request | None = None,
) -> AsyncIterator[str]:
    """订阅后台 Run 的 Redis Stream 事件。"""
    async for event in agent_run_event_service.stream(
        run_id=run_id,
        last_event_id=last_event_id,
        is_disconnected=request.is_disconnected if request else None,
    ):
        yield f"id: {event['stream_id']}\n{event['payload']}"


async def resume_chat_data_question_handler(
    *,
    conversation_id: str,
    user_id: str,
    username: str,
    run_id: str,
    runtime_context: dict[str, Any],
    answer: dict[str, Any],
    turn_id: str,
    last_event_id: str,
    request: Request | None,
) -> StreamingResponse:
    """Queue a checkpoint resume and subscribe to its durable Run events."""
    _ = runtime_context, answer, turn_id

    async def resumed_event_stream() -> AsyncIterator[str]:
        agent_run_runtime.submit(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=username,
        )
        async for event in _stream_run_events(
            run_id,
            last_event_id=last_event_id,
            request=request,
        ):
            yield event

    return StreamingResponse(
        resumed_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-ChatData-Run-Id": run_id,
        },
    )


# 启动装配：AgentRunRuntime 通过注入获得 Run 流生成器，服务层不反向 import 本模块。
agent_run_runtime.set_stream_generator(_generate_chat_data_stream)
