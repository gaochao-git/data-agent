"""Agent 运行支撑层：从 agent_stream.py（原 handlers.py）拆出的运行准备与历史持久化。

- 运行准备：语义会话上下文、会话工作区、技能快照、附件解析、超长输入转文件、checkpoint 恢复对齐与初始状态解析
- 历史持久化：轮次历史落盘、模型快照串行写入器、checkpoint 消息对齐工具
- 回调持久化：_AgentRunStreamCallbacks 及 persist/flush/mark 系列回调

依赖方向：本模块只依赖 services 层与 agent 域服务，不反向依赖 handlers
（SSE 渲染层）；对 _AgentSseStreamAdapter 的引用仅为字符串注解（TYPE_CHECKING）。
"""

import asyncio
import time

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Request
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from utils.logger import get_logger

from .agent import AgentRuntimeOptions
from .agent.context.context_budget import sanitize_context_usage_for_response
from .agent.middleware.steering import SteeringMessageTracker
from .agent.run.agent_inbox_service import agent_inbox_service
from .agent.run.conversation_event_service import conversation_event_service
from .agent.skills import format_skills_prompt
from .agent.workspace.runtime_skill_snapshot import (
    build_runtime_skill_snapshot_async,
    list_snapshot_skill_metadata,
)
from .services import conversation_service as conversation
from .services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    ensure_conversation_workspace,
    get_conversation_created_date,
)
from .services.agent_run.uploaded_file_service import get_file_info
from .services.semantic.run.semantic_conversation_context import (
    AgentPromptContext,
    SemanticConversationContext,
    build_agent_prompt_context,
    prepare_semantic_conversation_context,
)
from .services.semantic.run.semantic_materializer import prematerialize_enabled_semantic_models

if TYPE_CHECKING:
    from .agent_stream import _AgentSseStreamAdapter

logger = get_logger(__name__)

# 用户输入超长时自动转为文件，阈值单位：字符数
USER_INPUT_OFFLOAD_THRESHOLD = 4000
USER_INPUT_PREVIEW_CHARS = 200


def _get_timestamp() -> str:
    """获取当前时间戳字符串，格式：YYYY-MM-DD HH:MM:SS"""
    return time.strftime('%Y-%m-%d %H:%M:%S')


async def prepare_conversation_workspace(
    conversation_id: str,
    user_id: str = "system",
    *,
    created_date: str | None = None,
) -> dict[str, str]:
    """异步准备 ChatData 会话工作目录（``created_date`` 为会话创建日期 YYYY-MM-DD）。"""
    return await asyncio.to_thread(
        _prepare_conversation_workspace_sync, conversation_id, user_id, created_date=created_date
    )


def _prepare_conversation_workspace_sync(
    conversation_id: str,
    user_id: str = "system",
    *,
    created_date: str | None = None,
) -> dict[str, str]:
    """同步创建 ChatData 会话工作目录。"""
    return ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)


def _build_agent_context_state(
    *,
    user_id: str | None,
    operator_name: str | None,
    conversation_id: str,
    **extra_state: Any,
) -> dict[str, Any]:
    """构造单一 Agent 的状态字段。"""
    state = {
        "user_id": user_id,
        "operator_name": operator_name or "",
        "conversation_id": conversation_id,
    }
    state.update(extra_state)
    return state


async def _persist_latest_ai_message_timestamp(agent: Any, config: dict[str, Any], state: Any | None) -> Any | None:
    """为 create_agent 产生的最新 AIMessage 补充完成时间，保持历史消息时间口径一致。"""
    values = getattr(state, "values", None) if state is not None else None
    messages = values.get("messages") if isinstance(values, dict) else []
    latest_ai_message = None
    for message in reversed(messages or []):
        if isinstance(message, AIMessage):
            latest_ai_message = message
            break
    if latest_ai_message is None:
        return state
    additional_kwargs = getattr(latest_ai_message, "additional_kwargs", None) or {}
    if additional_kwargs.get("timestamp") or not hasattr(agent, "aupdate_state"):
        return state
    try:
        patched_message = latest_ai_message.model_copy(deep=True) if hasattr(latest_ai_message, "model_copy") else latest_ai_message.copy(deep=True)
        patched_message.additional_kwargs = dict(additional_kwargs)
        patched_message.additional_kwargs["timestamp"] = _get_timestamp()
        await agent.aupdate_state(config, {"messages": [patched_message]}, as_node="model")
        return await agent.aget_state(config) if hasattr(agent, "aget_state") else state
    except Exception as exc:
        logger.warning("保存 DataAnalyze 助手消息时间失败: %s", exc)
        return state


def _resolve_history_turn_ids(messages: list[Any], turn_id: str) -> list[str]:
    turn_ids = [
        message_id
        for message in messages
        if isinstance(message, HumanMessage)
        and (message_id := _stream_message_id(message))
    ]
    if turn_id and turn_id not in turn_ids:
        turn_ids.insert(0, turn_id)
    return turn_ids


def _resolve_history_usage(values: dict[str, Any] | None, stream_adapter: "_AgentSseStreamAdapter") -> Any:
    usage = (values or {}).get("token_usage")
    latest_usage = getattr(stream_adapter, "latest_token_usage", None)
    if isinstance(latest_usage, dict) and (latest_usage.get("breakdown") or latest_usage.get("llm_calls")):
        usage = latest_usage
    return usage


def _resolve_history_stream_id(
    snapshot_stream_id: str | None,
    stream_event_id_provider: Callable[[], str | None] | None,
) -> str | None:
    if snapshot_stream_id is not None:
        return snapshot_stream_id
    return stream_event_id_provider() if stream_event_id_provider else None


async def _persist_agent_turn_history(
    stream_adapter: "_AgentSseStreamAdapter",
    *,
    conversation_id: str,
    user_id: str,
    username: str | None,
    turn_id: str,
    state_override: dict[str, Any] | None = None,
    run_id: str | None = None,
    owner_token: str | None = None,
    stream_event_id_provider: Callable[[], str | None] | None = None,
    snapshot_stream_id: str | None = None,
) -> None:
    """将本轮完整消息、工具结果和思考分段追加到独立历史。"""
    values = state_override
    if values is None:
        values = getattr(getattr(stream_adapter, "state", None), "values", None)
    state_messages = values.get("messages") if isinstance(values, dict) else []
    if not isinstance(state_messages, list):
        return
    messages = _merge_archived_turn_messages(
        getattr(stream_adapter, "archived_turn_messages", []),
        state_messages,
        turn_id=turn_id,
    )
    turn_ids = _resolve_history_turn_ids(messages, turn_id)
    usage = _resolve_history_usage(values, stream_adapter)
    for current_turn_id in turn_ids:
        # token_usage 是会话级单行快照（event_key 固定），只随最后一轮写一次即可。
        is_last_turn = current_turn_id == turn_ids[-1]
        persist_kwargs = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "operator_name": username or user_id,
            "turn_id": current_turn_id,
            "messages": messages,
            "reasoning_segments": stream_adapter.reasoning_segments,
            "context_compression_events": (values or {}).get("context_compression_events") or [],
            "token_usage": sanitize_context_usage_for_response(usage) if is_last_turn else None,
        }
        if run_id and stream_event_id_provider is not None:
            await conversation_event_service.persist_turn_with_checkpoint(
                **persist_kwargs,
                run_id=run_id,
                owner_token=owner_token,
                stream_id=_resolve_history_stream_id(snapshot_stream_id, stream_event_id_provider),
            )
        else:
            await conversation_event_service.persist_turn(**persist_kwargs)


class _LatestModelSnapshotWriter:
    """Serialize snapshot writes while coalescing an obsolete pending snapshot."""

    def __init__(
        self,
        write_snapshot: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        task_name: str,
    ):
        self._write_snapshot = write_snapshot
        self._task_name = task_name
        self._pending: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None

    def submit(self, state: dict[str, Any]) -> None:
        snapshot = dict(state or {})
        if isinstance(snapshot.get("messages"), list):
            snapshot["messages"] = list(snapshot["messages"])
        if isinstance(snapshot.get("context_compression_events"), list):
            snapshot["context_compression_events"] = list(snapshot["context_compression_events"])
        self._pending = snapshot
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name=self._task_name)

    async def flush(self) -> None:
        while self._task is not None:
            task = self._task
            await task
            if self._task is task and self._pending is None:
                return

    async def _run(self) -> None:
        while self._pending is not None:
            snapshot = self._pending
            self._pending = None
            try:
                await self._write_snapshot(snapshot)
            except Exception:
                logger.warning("模型调用完成后的会话历史快照写入失败", exc_info=True)


def _merge_archived_turn_messages(
    archived_messages: list[BaseMessage],
    state_messages: list[BaseMessage],
    *,
    turn_id: str,
) -> list[BaseMessage]:
    """Merge pre-compression snapshots with the final checkpoint turn."""
    current_state_turn = _messages_from_turn(state_messages, turn_id)
    if not archived_messages:
        return current_state_turn or state_messages
    return _merge_message_snapshots(archived_messages, current_state_turn)


def _messages_from_turn(messages: list[BaseMessage], turn_id: str) -> list[BaseMessage]:
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage) and _stream_message_id(message) == turn_id:
            return list(messages[index:])
    return []


def _merge_message_snapshots(
    existing: list[BaseMessage],
    incoming: list[BaseMessage],
) -> list[BaseMessage]:
    """Merge monotonic turn snapshots while retaining original full payloads."""
    merged = list(existing)
    seen = {
        key
        for index, message in enumerate(merged)
        if (key := _stream_message_key(message, index)) is not None
    }
    for index, message in enumerate(incoming):
        key = _stream_message_key(message, index)
        if key is not None and key in seen:
            continue
        merged.append(message)
        if key is not None:
            seen.add(key)
    return merged


def _tool_call_ids_of(message: AIMessage) -> tuple[str, ...]:
    tool_call_ids = tuple(
        str((tool_call or {}).get("id") or "").strip()
        for tool_call in getattr(message, "tool_calls", None) or []
        if str((tool_call or {}).get("id") or "").strip()
    )
    return tool_call_ids


def _tool_message_key(message: ToolMessage) -> tuple[Any, ...] | None:
    tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
    return ("tool", tool_call_id) if tool_call_id else None


def _stream_message_key(message: BaseMessage, index: int) -> tuple[Any, ...] | None:
    message_id = _stream_message_id(message)
    if message_id:
        return ("id", message_id)
    if isinstance(message, ToolMessage):
        return _tool_message_key(message)
    if isinstance(message, AIMessage):
        tool_call_ids = _tool_call_ids_of(message)
        if tool_call_ids:
            return ("ai_tools", *tool_call_ids)
        return ("ai", str(message.content or ""))
    if isinstance(message, HumanMessage):
        return ("human", str(message.content or ""))
    return (message.__class__.__name__, str(getattr(message, "content", "") or ""), index)


def _stream_message_id(message: BaseMessage) -> str:
    return str(getattr(message, "id", "") or "").strip()


def _checkpoint_message_ids(checkpoint_tuple: Any | None) -> set[str]:
    """Read stable message IDs from a LangGraph checkpoint tuple."""
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
    channel_values = checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
    messages = channel_values.get("messages") if isinstance(channel_values, dict) else None
    if not isinstance(messages, list):
        return set()
    return {
        message_id
        for message in messages
        if (message_id := _stream_message_id(message))
    }


def _checkpoint_resume_status(checkpoint_tuple: Any | None) -> bool | None:
    """Return whether a resume interrupt is still pending in the checkpoint.

    ``None`` means the checkpointer implementation did not expose pending
    writes, so callers can fall back to the durable Run marker used by older
    checkpoints. An explicit interrupt write is the authoritative indication
    that ``Command(resume=...)`` still needs to be submitted.
    """
    pending_writes = getattr(checkpoint_tuple, "pending_writes", None)
    if pending_writes is None:
        return None
    has_pending_interrupt = any(
        isinstance(write, (tuple, list))
        and len(write) >= 2
        and str(write[1]) == "__interrupt__"
        for write in pending_writes
    )
    return not has_pending_interrupt


async def _persist_partial_agent_turn_history(
    stream_adapter: "_AgentSseStreamAdapter | None",
    *,
    conversation_id: str,
    user_id: str,
    username: str | None,
    turn_id: str,
    run_id: str | None = None,
    owner_token: str | None = None,
    stream_event_id_provider: Callable[[], str | None] | None = None,
) -> None:
    """流中断时尽力保存已写入 checkpoint 的可见历史，不影响原始异常。"""
    if stream_adapter is None:
        return
    try:
        if stream_adapter.state is None and hasattr(stream_adapter.agent, "aget_state"):
            stream_adapter.state = await stream_adapter.agent.aget_state(stream_adapter.config)
        await _persist_agent_turn_history(
            stream_adapter,
            conversation_id=conversation_id,
            user_id=user_id,
            username=username,
            turn_id=turn_id,
            run_id=run_id,
            owner_token=owner_token,
            stream_event_id_provider=stream_event_id_provider,
        )
    except Exception:
        logger.warning("流异常后的会话历史补写失败: conversation_id=%s", conversation_id, exc_info=True)


class _AgentRunPreparationError(Exception):
    """Agent 流准备阶段致命失败；message 即下发前端的 error 文案。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _resolve_agent_turn_id(
    resume_turn_id: str | None,
    turn_id: str | None,
    user_message: HumanMessage | None,
    adopted_messages: list[HumanMessage] | None,
) -> str:
    """当前轮次 ID：恢复轮 > 请求轮 > 用户消息 ID > 首条接管消息 ID。"""
    return resume_turn_id or turn_id or str(
        user_message.id if user_message else (adopted_messages[0].id if adopted_messages else "")
    ) or ""


async def _prepare_agent_semantic_context(
    *,
    thread_id: str,
    user_query: str,
    datasource_id: str | None,
    user_id: str | None,
    username: str | None,
    member_datasource_ids: list[str] | None,
    semantic_config_id: str | None,
) -> tuple[SemanticConversationContext, AgentPromptContext]:
    """准备语义会话上下文并校验数据源绑定；致命失败抛 _AgentRunPreparationError。"""
    semantic_context = await prepare_semantic_conversation_context(
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        username=username,
        user_query=user_query,
    )
    if semantic_context.semantic_config_error:
        logger.warning("智能问数指标集准备失败: %s", semantic_context.semantic_config_error)
        raise _AgentRunPreparationError(semantic_context.semantic_config_error)
    if semantic_context.datasource_id and semantic_context.datasource_meta is not None:
        bound_datasource_id = await conversation.resolve_datasource_binding(
            thread_id,
            user_id or "system",
            semantic_context.datasource_id,
        )
        if bound_datasource_id != semantic_context.datasource_id:
            raise _AgentRunPreparationError("当前会话已绑定其他数据源组，请新建会话后再切换。")
    if semantic_context.semantic_config:
        logger.info(
            "智能问数使用手选指标集: %s",
            semantic_context.semantic_config.get("name") or semantic_context.semantic_config.get("id"),
        )
    else:
        logger.info("智能问数未手动选择指标集，将由模型按需调用选择工具")
    return semantic_context, build_agent_prompt_context(semantic_context)


async def _prepare_agent_workspace_runtime(
    *,
    thread_id: str,
    datasource_id: str | None,
    user_id: str | None,
    semantic_context: SemanticConversationContext,
    runtime_options: AgentRuntimeOptions,
) -> tuple[dict[str, str], ConversationWorkspaceRuntime, str]:
    """准备会话工作区并预物化语义目录；目录整体失败抛 _AgentRunPreparationError。

    返回（workspace 路径表, workspace 运行时, 语义物化缺失说明）。
    """
    # 工作区按会话创建日期归档；先查 DB 权威解析，避免与 uploads/技能快照等入口分裂出多个日期目录。
    user_id_str = user_id or "system"
    created_date = await get_conversation_created_date(thread_id, user_id=user_id_str)
    workspace_paths = await prepare_conversation_workspace(thread_id, user_id_str, created_date=created_date)
    workspace_runtime = ConversationWorkspaceRuntime(
        workspace_root=workspace_paths["root"],
        user_id=user_id_str,
        backend=runtime_options.workspace_backend,
    )
    # 语义目录预物化：agent 循环前把全部启用模型写好，模型可不经 semantic_search
    # 直接 dir_list/file_grep/file_read 检索 /mnt/user-data/workspace/semantic/。
    # 目录是语义检索的唯一来源，物化异常属基础设施故障：带着空目录继续只会让
    # 模型误判为“没有语义模型”，因此直接终止本轮，不进入 agent 循环。
    # 部分模型物化失败不终止本轮，但把缺失清单注入上下文，让模型能区分
    # “数据源组没有该模型”和“该模型本轮未物化”。
    try:
        prematerialized = await prematerialize_enabled_semantic_models(
            workspace_runtime=workspace_runtime,
            datasource_id=datasource_id,
            user_id=user_id,
            source_type=str((semantic_context.datasource_meta or {}).get("source_type") or ""),
        )
    except Exception as err:
        logger.error(
            "语义模型预物化失败，终止本轮: thread_id=%s datasource_id=%s error=%s",
            thread_id, datasource_id, err,
        )
        raise _AgentRunPreparationError("语义模型目录初始化失败，请稍后重试。") from None
    if prematerialized["total"]:
        logger.info(
            "语义模型预物化完成: thread_id=%s datasource_id=%s files=%s",
            thread_id, datasource_id, prematerialized["total"],
        )
    failed_ids = [str(item) for item in prematerialized.get("failed_ids") or [] if str(item).strip()]
    if not failed_ids:
        return workspace_paths, workspace_runtime, ""
    logger.error(
        "语义模型预物化部分失败: thread_id=%s datasource_id=%s failed_ids=%s",
        thread_id, datasource_id, failed_ids,
    )
    materialize_note = (
        "注意：本轮语义目录物化不完整，以下语义模型未能生成 YAML 文件："
        + "、".join(failed_ids)
        + "。目录中检索不到这些模型不代表数据源组没有该模型；如用户问题命中这些模型，如实说明本轮无法查询它们，不要判定为数据缺失。"
    )
    return workspace_paths, workspace_runtime, materialize_note


async def _load_runtime_skills_xml(
    user_id: str,
    thread_id: str,
    workspace_runtime: ConversationWorkspaceRuntime,
) -> str:
    """加载会话技能快照并生成系统提示词技能清单；失败时本轮不提供技能。"""
    available_skills: list[dict[str, Any]] = []
    available_skills_xml = format_skills_prompt([])
    # runtime-skills 是模型实际读取、file_read 解析及沙箱挂载的唯一技能真相。
    # 先构建/复用会话快照，再从同一目录生成系统提示词技能清单，不能回退管理域。
    try:
        # 快照目录由工作区根推导，保证与实际工作区同一日期分层（不从 uid/cid 反推）。
        snapshot_dir = await build_runtime_skill_snapshot_async(
            user_id, thread_id, target_dir=workspace_runtime.runtime_skills_dir
        )
        available_skills = await asyncio.to_thread(list_snapshot_skill_metadata, snapshot_dir)
        available_skills_xml = format_skills_prompt(available_skills)
    except Exception as err:
        logger.warning("会话技能快照加载失败，本轮不提供技能: %s", err)
    workspace_runtime.available_skill_names = [str(s.get("name") or "") for s in available_skills]
    return available_skills_xml


async def _resolve_file_attachments(
    file_ids: list[str] | None,
    *,
    user_id: str | None,
    thread_id: str,
) -> list[dict[str, Any]]:
    """解析 file_ids 对应的文件信息，用于追加到用户消息。

    上传与解析已解耦：只要求文件存在且归属当前会话，不再要求预解析完成。
    """
    attachments: list[dict[str, Any]] = []
    if not file_ids:
        return attachments
    for fid in file_ids:
        info = await get_file_info(fid, user_id=user_id or "system", thread_id=thread_id)
        if not info:
            logger.warning("关联文件不存在或无权限，已忽略: file_id=%s thread_id=%s", fid, thread_id)
            continue
        if info.get("thread_id") and thread_id and info["thread_id"] != thread_id:
            logger.warning("关联文件会话归属不匹配，已忽略: file_id=%s", fid)
            continue
        attachments.append({
            "file_id": fid,
            "original_filename": info["original_filename"],
            "file_type": info["file_type"],
            "file_size": info["file_size"],
            "virtual_path": f"/mnt/user-data/uploads/{info['storage_dir']}",
        })
    return attachments


async def _offload_oversized_user_input(
    user_query: str,
    workspace_runtime: ConversationWorkspaceRuntime,
    thread_id: str,
) -> tuple[str, dict[str, str]]:
    """用户输入超长时自动转为文件写入 workspace outputs。

    返回（注入消息的正文, 附件描述字典）。
    """
    offload_name = f"input-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    visible_path = f"/mnt/user-data/outputs/{offload_name}"
    await workspace_runtime.write_text_file(
        file_path=visible_path,
        content=user_query,
        mode="overwrite",
    )
    preview = user_query[:USER_INPUT_PREVIEW_CHARS]
    user_message_content = (
        f"{preview}...\n\n"
        f"[用户输入较长（共 {len(user_query)} 字符），完整内容已保存到 {visible_path}，"
        "请用 file_read 分批读取后再回答。]"
    )
    logger.info("用户输入超长已转文件: conversation_id=%s chars=%d path=%s", thread_id, len(user_query), visible_path)
    return user_message_content, {
        "original_filename": offload_name,
        "parsed_path": visible_path,
    }


async def _resolve_checkpointed_turn_state(
    checkpointer: Any,
    config: dict[str, Any],
    *,
    recovering: bool,
    resume_answer: dict[str, Any] | None,
    resume_started: bool,
    turn_id: str | None,
    run_id: str | None,
    owner_token: str | None,
    adopted_messages: list[HumanMessage] | None,
) -> tuple[bool, bool, list[HumanMessage] | None]:
    """恢复场景下对齐 checkpoint 与 inbox，判定本轮的续跑形态。

    返回（当前轮已 checkpoint, checkpoint 续跑生效, 接管消息列表）。
    """
    checkpoint_tuple = await checkpointer.aget_tuple(config) if recovering else None
    checkpoint_message_ids = _checkpoint_message_ids(checkpoint_tuple)
    current_turn_checkpointed = bool(turn_id and turn_id in checkpoint_message_ids)
    checkpoint_resume_status = _checkpoint_resume_status(checkpoint_tuple)
    resume_checkpointed = bool(
        resume_answer is not None
        and recovering
        and checkpoint_tuple is not None
        and (
            checkpoint_resume_status is True
            or (checkpoint_resume_status is None and resume_started)
        )
    )
    if recovering and current_turn_checkpointed and run_id and owner_token:
        await agent_inbox_service.reconcile_recovered_messages(
            run_id,
            checkpoint_message_ids,
            owner_token,
        )
        # Checkpointed messages are already part of graph state; any
        # claimed-but-uncheckpointed messages were returned to pending
        # for the steering middleware to inject exactly once.
        adopted_messages = []
    return current_turn_checkpointed, resume_checkpointed, adopted_messages


def _resolve_agent_initial_state(
    *,
    resume_checkpointed: bool,
    resume_answer: dict[str, Any] | None,
    current_turn_checkpointed: bool,
    user_id: str | None,
    operator_name: str | None,
    conversation_id: str,
    adopted_messages: list[HumanMessage] | None,
    user_message: HumanMessage | None,
    datasource_id: str | None,
    semantic_context: SemanticConversationContext,
    runtime_options: AgentRuntimeOptions,
    workspace_root: str,
) -> Any:
    """按恢复场景决定本轮 agent 的初始状态。"""
    if resume_checkpointed:
        return None
    if resume_answer is not None:
        return Command(resume=resume_answer)
    if current_turn_checkpointed:
        return None
    return _build_agent_context_state(
        user_id=user_id,
        operator_name=operator_name,
        conversation_id=conversation_id,
        messages=[*(adopted_messages or []), *([user_message] if user_message else [])],
        execution_context={
            "datasource_id": datasource_id,
            "available_member_datasource_ids": semantic_context.available_member_datasource_ids,
            "matched_member_datasource_ids": semantic_context.matched_member_datasource_ids,
        },
        workspace={
            "workspace_root": workspace_root,
            "workspace_backend": runtime_options.workspace_backend,
        },
    )


@dataclass
class _AgentRunStreamCallbacks:
    """_run_chat_data_agent_stream 拆出的持久化/收口回调共享的运行期上下文。

    user_message / stream_adapter / model_snapshot_writer / adopted_messages
    在主流程推进过程中被就地赋值，各回调在触发时读取最新值。
    """

    thread_id: str
    user_id: str | None
    username: str | None
    run_id: str | None
    owner_token: str | None
    request: Request | None
    resume_answer: dict[str, Any] | None
    resume_turn_id: str | None
    turn_id: str | None
    stream_event_id_provider: Callable[[], str | None] | None
    log_phase: Callable[[str], Any]
    steering_tracker: SteeringMessageTracker
    adopted_messages: list[HumanMessage] | None = None
    user_message: HumanMessage | None = None
    stream_adapter: "_AgentSseStreamAdapter | None" = None
    model_snapshot_writer: "_LatestModelSnapshotWriter | None" = None
    recovering: bool = False

    @property
    def effective_turn_id(self) -> str:
        return _resolve_agent_turn_id(
            self.resume_turn_id, self.turn_id, self.user_message, self.adopted_messages,
        )


async def _write_agent_model_snapshot(
    callbacks: _AgentRunStreamCallbacks,
    state: dict[str, Any],
) -> None:
    adapter = callbacks.stream_adapter
    if adapter is None:
        return
    snapshot_stream_id = state.get("__history_stream_id") if isinstance(state, dict) else None
    await _persist_agent_turn_history(
        adapter,
        conversation_id=callbacks.thread_id,
        user_id=callbacks.user_id,
        username=callbacks.username,
        turn_id=callbacks.effective_turn_id,
        state_override=state,
        run_id=callbacks.run_id,
        owner_token=callbacks.owner_token,
        stream_event_id_provider=callbacks.stream_event_id_provider,
        snapshot_stream_id=snapshot_stream_id,
    )


async def _persist_agent_model_snapshot(
    callbacks: _AgentRunStreamCallbacks,
    state: dict[str, Any],
) -> None:
    """Queue the latest snapshot so model-to-tool transition stays immediate."""
    snapshot = dict(state or {})
    # after_model 是异步合并写入。游标必须在提交快照时捕获，
    # 不能等后台 writer 真正写库时再读取，否则可能跨过未包含
    # 在该快照内的 Redis 事件，恢复时会跳过内容。
    snapshot["__history_stream_id"] = (
        callbacks.stream_event_id_provider() if callbacks.stream_event_id_provider else None
    )
    callbacks.model_snapshot_writer.submit(snapshot)


async def _flush_agent_model_snapshots(callbacks: _AgentRunStreamCallbacks) -> None:
    """Wait until the newest submitted snapshot is durable."""
    await callbacks.model_snapshot_writer.flush()


async def _persist_agent_user_message(callbacks: _AgentRunStreamCallbacks) -> None:
    if callbacks.user_message is None:
        return
    await conversation_event_service.persist_user_message(
        conversation_id=callbacks.thread_id,
        user_id=callbacks.user_id,
        operator_name=callbacks.username or callbacks.user_id,
        message=callbacks.user_message,
    )
    callbacks.log_phase("persist_user_message")


async def _persist_agent_turn_history_snapshot(callbacks: _AgentRunStreamCallbacks) -> None:
    await _flush_agent_model_snapshots(callbacks)
    await _persist_agent_turn_history(
        callbacks.stream_adapter,
        conversation_id=callbacks.thread_id,
        user_id=callbacks.user_id,
        username=callbacks.username,
        turn_id=callbacks.effective_turn_id,
        run_id=callbacks.run_id,
        owner_token=callbacks.owner_token,
        stream_event_id_provider=callbacks.stream_event_id_provider,
    )


async def _persist_agent_partial_history(callbacks: _AgentRunStreamCallbacks) -> None:
    await _flush_agent_model_snapshots(callbacks)
    await _persist_partial_agent_turn_history(
        callbacks.stream_adapter,
        conversation_id=callbacks.thread_id,
        user_id=callbacks.user_id,
        username=callbacks.username,
        turn_id=callbacks.effective_turn_id,
        run_id=callbacks.run_id,
        owner_token=callbacks.owner_token,
        stream_event_id_provider=callbacks.stream_event_id_provider,
    )
    # A stream can fail after LangGraph has checkpointed the
    # answer but before the normal finish hook. Inspecting the
    # durable state here prevents the old question from being
    # resurrected on the next worker.
    if (
        callbacks.resume_answer is not None
        and callbacks.run_id
        and callbacks.stream_adapter.graph_started
        and callbacks.stream_adapter.state is not None
        and not callbacks.stream_adapter.pending_interrupt()
    ):
        await agent_inbox_service.mark_question_resume_started(callbacks.run_id, callbacks.owner_token)


async def _mark_agent_resume_checkpointed(callbacks: _AgentRunStreamCallbacks) -> None:
    if (
        callbacks.resume_answer is not None
        and callbacks.run_id
        and callbacks.stream_adapter.graph_started
        and not callbacks.stream_adapter.pending_interrupt()
    ):
        await agent_inbox_service.mark_question_resume_started(callbacks.run_id, callbacks.owner_token)
