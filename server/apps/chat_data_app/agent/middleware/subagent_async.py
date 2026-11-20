"""后台子智能体：subagent(dispatch=...) 默认 background=true 的持久任务账本与配套工具。

与 ``middleware/subagents.py`` 的分工：本模块负责任务登记、稳定 task_id、
check/list/wait/cancel 工具；子图执行体（``_run_background_task``）留在
subagents.py，通过 ``runner_factory`` 注入，保持单向依赖。

持久化事实源是 ``chat_data_agent_inbox`` 的 ``record_type='task'`` 行：
进程内注册表只保存 asyncio 句柄，不承担状态真值。这样 worker 重启、多副本
切换后任务仍可查询；取消请求跨 worker 也能被执行节点的心跳观察到。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain.tools import ToolRuntime

from utils.logger import get_logger

from ..contracts.subagent_capabilities import resolve_task_capabilities
from ..contracts.subagent_task import (
    ACTIVE_TASK_STATUSES,
    SESSION_STATUS_CLOSED,
    TASK_HEARTBEAT_SECONDS,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_CANCELLING,
    TASK_STATUS_LABELS,
    TERMINAL_TASK_STATUSES,
    SubagentSessionClosedError,
    SubagentTaskCapacityError,
    SubagentTaskPort,
    SubagentTaskView,
    get_subagent_task_service,
    parse_task_status_filter,
)
from .subagent_config import SUBAGENT_STATE_PASSTHROUGH_KEYS

logger = get_logger(__name__)

DEFAULT_WAIT_TIMEOUT_SECONDS = 30
MAX_WAIT_TASKS = 10
WAIT_POLL_SECONDS = 0.5
# 已落盘报告在工具返回里的预览长度；完整正文由 file_read 读取。
REPORT_PATH_PREVIEW_CHARS = 2000


def _task_service() -> SubagentTaskPort | None:
    return get_subagent_task_service()


def _truncate_text(text: str, max_chars: int, label: str = "结果") -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[{label}过长已截断，完整内容共 {len(text)} 字符]"


def _read_conversation_context(runtime: ToolRuntime, run_controls: Any = None) -> tuple[str, str | None, str, str]:
    """读取会话身份与父 Run；缺 conversation_id 时仍生成隔离桶，避免串会话。"""
    state = getattr(runtime, "state", None) or {}
    conversation_id = str(state.get("conversation_id") or "").strip()
    user_id = str(state.get("user_id") or "").strip() or None
    operator_name = str(state.get("operator_name") or "").strip() or user_id or "系统"
    parent_run_id = str(
        getattr(run_controls, "run_id", None) or state.get("run_id") or state.get("parent_run_id") or ""
    ).strip()
    if not conversation_id:
        conversation_id = f"unknown-{time.time_ns()}"
    if not parent_run_id:
        parent_run_id = f"standalone-{time.time_ns()}"
    return conversation_id, user_id, operator_name, parent_run_id


def _task_progress_text(record: SubagentTaskView) -> str:
    progress = record.progress
    pieces: list[str] = []
    phase = str(progress.get("phase") or "").strip()
    if phase:
        pieces.append(f"阶段={phase}")
    if progress.get("tool_calls") is not None:
        pieces.append(f"工具调用={progress.get('tool_calls')}")
    if progress.get("running_tools"):
        pieces.append(f"执行中工具={progress.get('running_tools')}")
    if progress.get("message_count") is not None:
        pieces.append(f"过程消息={progress.get('message_count')}")
    if progress.get("last_tool"):
        pieces.append(f"最近工具={progress.get('last_tool')}")
    if progress.get("updated_at"):
        pieces.append(f"更新于={progress.get('updated_at')}")
    return "；".join(pieces) or "暂无进度明细"


def _format_task_status(record: SubagentTaskView, *, session_status: str | None = None) -> str:
    label = TASK_STATUS_LABELS.get(record.status, record.status)
    turn_index = int(getattr(record, "turn_index", 1) or 1)
    turn = f" 轮次={turn_index}" if turn_index > 1 else ""
    capabilities = resolve_task_capabilities(record, session_status=session_status)
    prefix = (
        f"[{label}] agent_id={_task_agent_id(record)} task_id={record.task_id}{turn} "
        f"类型={record.subagent_type} 任务={record.label}"
    )
    if record.is_terminal:
        return f"{prefix}\n  {capabilities.describe()}"
    return f"{prefix}\n  {capabilities.describe()}\n  {_task_progress_text(record)}"


def _task_session_id(record: SubagentTaskView) -> str:
    """任务对应的子会话 ID；兼容改造前没有 agent_session_id 的记录。"""
    return str(getattr(record, "agent_session_id", "") or record.task_id)


def _task_agent_id(record: SubagentTaskView) -> str:
    """任务所属子智能体的稳定 ID；兼容旧记录回退到子会话或任务 ID。"""
    return str(getattr(record, "agent_id", "") or _task_session_id(record))


def format_subagent_task_list_summary(records: Sequence[SubagentTaskView]) -> str:
    """子智能体列表抬头：同时给出智能体数、任务条数和活跃条数。"""
    if not records:
        return "当前会话没有匹配的子智能体任务。"
    agent_ids = {_task_agent_id(record) for record in records}
    active = sum(1 for record in records if record.status in ACTIVE_TASK_STATUSES)
    return f"子智能体（共 {len(agent_ids)} 个，任务 {len(records)} 条，活跃 {active} 条）："


def format_subagent_agent_list(
    records: Sequence[SubagentTaskView],
    *,
    status_filtered: bool = False,
    session_statuses: dict[str, str] | None = None,
) -> str:
    """按智能体分组展示最近状态；任务 ID 明细留到二次查询。"""
    grouped: dict[str, list[SubagentTaskView]] = {}
    for record in records:
        grouped.setdefault(_task_agent_id(record), []).append(record)
    lines: list[str] = []
    for agent_id, tasks in grouped.items():
        latest = tasks[0]
        status = TASK_STATUS_LABELS.get(latest.status, latest.status)
        turn_index = int(getattr(latest, "turn_index", 1) or 1)
        latest_label = "最近匹配 task_id" if status_filtered else "最近 task_id"
        lines.append(
            f"- agent_id={agent_id} 类型={latest.subagent_type} 轮次={turn_index} "
            f"{latest_label}={latest.task_id} 状态={status} "
            f"可操作={resolve_task_capabilities(latest, session_status=_session_status(latest, session_statuses)).actions_text()} "
            f"任务={_truncate_text(latest.label, 100, '任务标题')}"
        )
        if _session_closed(latest, session_statuses):
            lines.append("  会话状态：已关闭（不可再追问）")
    lines.append('查看某个子智能体的任务 ID：subagent({"action":"list","agent_id":"<agent_id>"})。')
    return "\n".join(lines)


def format_subagent_agent_tasks(
    agent_id: str,
    records: Sequence[SubagentTaskView],
    *,
    session_statuses: dict[str, str] | None = None,
) -> str:
    """某个智能体名下的任务 ID 明细，供二次下钻查询。"""
    if not records:
        return f"没有找到子智能体 {agent_id} 的任务。"
    subagent_type = records[0].subagent_type
    lines = [f"子智能体 agent_id={agent_id}（类型={subagent_type}）的任务（共 {len(records)} 条）："]
    for record in records:
        status = TASK_STATUS_LABELS.get(record.status, record.status)
        turn_index = int(getattr(record, "turn_index", 1) or 1)
        lines.append(
            f"- task_id={record.task_id} 状态={status} 轮次={turn_index} "
            f"可操作={resolve_task_capabilities(record, session_status=_session_status(record, session_statuses)).actions_text()} "
            f"任务={_truncate_text(record.label, 100, '任务标题')}"
        )
        if _session_closed(record, session_statuses):
            lines.append("  会话状态：已关闭（不可再追问）")
    lines.append("check/cancel/follow_up/steer/approve/reject 使用上面的 task_id；agent_id 只用于识别子智能体。")
    return "\n".join(lines)


def _format_task_report(record: SubagentTaskView, *, session_status: str | None = None) -> str:
    """终态任务报告：有落盘路径返回路径+预览，否则直接返回完整正文。"""
    body = record.report or record.error or "（无报告正文）"
    turn_index = int(getattr(record, "turn_index", 1) or 1)
    lines = [
        f"[{TASK_STATUS_LABELS.get(record.status, record.status)}] "
        f"agent_id={_task_agent_id(record)} task_id={record.task_id} "
        f"类型={record.subagent_type} 轮次={turn_index}",
        f"任务：{record.label}",
    ]
    report_path = str(getattr(record, "report_path", "") or "")
    if report_path:
        lines.extend(
            [
                f"完整报告已落盘：result_path={report_path}（{len(body)} 字符）。",
                f"预览：{_truncate_text(body, REPORT_PATH_PREVIEW_CHARS, '报告预览')}",
                "请用 file_read 读取完整报告，不要只凭预览下结论。",
            ]
        )
    else:
        lines.append(f"报告：{body}")
    if record.is_terminal and session_status != SESSION_STATUS_CLOSED:
        lines.append(
            f'可基于同一子会话继续追问：subagent({{"action":"send","task_id":"{record.task_id}","message":"..."}})。'
        )
    elif session_status == SESSION_STATUS_CLOSED:
        lines.append("该子智能体会话已关闭，不能继续追问；如需同类任务请重新派发新的子智能体。")
    return "\n".join(lines)


def _session_status(record: SubagentTaskView, statuses: dict[str, str] | None) -> str | None:
    if not statuses:
        return None
    return statuses.get(_task_session_id(record))


def _session_closed(record: SubagentTaskView, statuses: dict[str, str] | None) -> bool:
    return _session_status(record, statuses) == SESSION_STATUS_CLOSED


async def _spawn_background_task(
    *,
    subagent_type: str,
    description: str,
    label: str | None,
    runtime: ToolRuntime,
    run_controls: Any,
    runner_factory: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]],
) -> str:
    """登记持久任务并立即返回；执行体在独立 asyncio task 中运行。"""
    conversation_id, user_id, operator_name, parent_run_id = _read_conversation_context(runtime, run_controls)
    if not user_id:
        raise ValueError("无法启动后台子智能体：当前会话缺少用户身份。")
    service = _task_service()
    if service is None:
        raise ValueError("后台子智能体启动失败：任务账本服务未初始化。")
    try:
        record = await service.create_task(
            conversation_id=conversation_id,
            user_id=user_id,
            parent_run_id=parent_run_id,
            subagent_type=subagent_type,
            description=description,
            label=label,
            parent_tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
            parent_owner_token=str(getattr(run_controls, "owner_token", None) or ""),
            operator_name=operator_name,
        )
    except SubagentTaskCapacityError as exc:
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        logger.error(
            "[tools] 后台任务登记失败: conversation_id=%s type=%s", conversation_id, subagent_type, exc_info=True
        )
        raise ValueError("后台子智能体启动失败：任务登记未成功，请稍后重试或改为同步执行。") from exc

    await _launch_task_runner_with_recovery(service, record, runtime, runner_factory)
    logger.info(
        "[tools] 子智能体已转入后台执行: task_id=%s type=%s conversation_id=%s",
        record.task_id,
        subagent_type,
        conversation_id,
    )
    return (
        f"子智能体已在后台启动（agent_id={record.agent_id}，task_id={record.task_id}，类型={subagent_type}）。"
        f"超时={record.timeout_seconds} 秒。"
        "任务不会阻塞当前对话：完成/失败/超时后系统会自动唤醒"
        "一条隐藏通知（不需要等用户先发消息）；期间可继续其他工作，也可用 "
        "subagent 查询：manage 的 action=list 列任务、check 查单个、wait 等结果。"
    )


async def _launch_task_runner_with_recovery(
    service: SubagentTaskPort,
    record: SubagentTaskView,
    runtime: ToolRuntime,
    runner_factory: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]],
) -> None:
    state = {
        key: value
        for key, value in dict(getattr(runtime, "state", None) or {}).items()
        if key in SUBAGENT_STATE_PASSTHROUGH_KEYS
    }
    try:
        persisted = await service.save_task_recovery_runtime(
            record.task_id,
            record.owner_token or "",
            state=state,
            tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
        )
    except Exception as exc:
        logger.error("[tools] 后台任务恢复上下文保存失败: task_id=%s", record.task_id, exc_info=True)
        await service.finish_task(
            record.task_id,
            record.owner_token or "",
            status="failed",
            report="后台子智能体启动失败：恢复上下文未能保存，请重新派发。",
            error="恢复上下文保存失败",
        )
        raise ValueError("后台子智能体启动失败：恢复上下文未能保存，请重新派发。") from exc
    service.register_recovery_runner(runner_factory, task_id=record.task_id)
    _launch_task_runner(service, persisted or record, runtime, runner_factory)


def _launch_task_runner(
    service: SubagentTaskPort,
    record: SubagentTaskView,
    runtime: ToolRuntime,
    runner_factory: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]],
) -> None:
    snapshot = service.snapshot_runtime(runtime)
    task = asyncio.create_task(
        runner_factory(record, snapshot),
        name=f"chatdata-subagent-task-{record.task_id}",
    )
    service.register_local_task(record.task_id, task)

    def _on_done(completed: asyncio.Task) -> None:
        if completed.cancelled():
            return
        exc = completed.exception()
        if exc is not None:
            logger.error("[tools] 后台任务协程异常退出: task_id=%s", record.task_id, exc_info=exc)

    task.add_done_callback(_on_done)


@dataclass(frozen=True)
class _FollowUpTarget:
    service: SubagentTaskPort
    target: SubagentTaskView
    base: SubagentTaskView
    conversation_id: str
    user_id: str
    operator_name: str
    parent_run_id: str


async def _resolve_follow_up_target(
    *,
    task_id: str,
    available_types: set[str],
    runtime: ToolRuntime,
    run_controls: Any,
) -> _FollowUpTarget | str:
    """校验追问目标并定位最新一轮；返回统一绑定信息或可直接回给模型的错误。"""
    conversation_id, user_id, operator_name, parent_run_id = _read_conversation_context(runtime, run_controls)
    if not user_id:
        return "无法追问子智能体：当前会话缺少用户身份。"
    service = _task_service()
    if service is None:
        return "子智能体追问失败：任务账本服务未初始化。"
    target = await service.get_task(task_id, conversation_id=conversation_id, user_id=user_id)
    if target is None:
        return f'子智能体任务 {task_id} 不存在。可用 subagent({{"action":"list"}}) 查看当前会话任务。'
    session_state = await service.get_session_state(
        target.agent_session_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if session_state is not None and session_state.status == SESSION_STATUS_CLOSED:
        return "该子智能体会话已关闭，不能继续追问；如需同类任务请重新派发新的子智能体。"
    if not resolve_task_capabilities(
        target, session_status=session_state.status if session_state else None
    ).can_follow_up:
        return (
            f"子智能体任务 {task_id} 仍在执行（{TASK_STATUS_LABELS.get(target.status, target.status)}），"
            '不能追问；可先用 subagent({"action":"wait","task_ids":["..."]}) 等结果，'
            '或 subagent({"action":"cancel","task_id":"..."}) 取消后再开始新一轮。'
        )
    if target.subagent_type not in available_types:
        return f"子智能体类型 {target.subagent_type} 当前未启用，无法追问。"
    active = await service.find_active_task_in_session(
        target.agent_session_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if active is not None:
        return (
            f"该子智能体已有进行中的追问任务 {active.task_id}（{TASK_STATUS_LABELS.get(active.status, active.status)}），"
            "请等待其结束或先取消，再发起下一轮。"
        )
    # 用户可能从任意历史任务卡发起追问；子会话应始终从最新一轮线性续写，
    # 避免 parent/turn_index 指回旧分支，而上下文却是最新快照。
    base = await service.get_latest_task_in_session(
        target.agent_session_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if base is None or base.turn_index < target.turn_index:
        # 快照更新失败时 latest_task_id 可能仍指向旧轮次；宁可锚定用户指定的
        # 终态任务，也不能把新追问接到更早的 parent 上。
        base = target
    return _FollowUpTarget(
        service=service,
        target=target,
        base=base,
        conversation_id=conversation_id,
        user_id=user_id,
        operator_name=operator_name,
        parent_run_id=parent_run_id,
    )


async def _close_aborted_sync_task(service: SubagentTaskPort, record: SubagentTaskView) -> None:
    """同步任务未真正开始执行时立即收口，避免残留 pending 行被租约回收。"""
    try:
        closed = await service.finish_task(
            record.task_id,
            record.owner_token or "",
            status=TASK_STATUS_CANCELLED,
            report="同步子智能体任务启动失败，已取消。",
        )
        if closed is not None and closed.is_terminal:
            await service.mark_viewed(closed)
    except Exception:
        logger.warning("同步子智能体任务启动失败后收口失败: task_id=%s", record.task_id, exc_info=True)


async def _start_registered_sync_follow_up(
    service: SubagentTaskPort,
    record: SubagentTaskView,
) -> SubagentTaskView | None:
    """启动已登记的同步追问；失败时立即收口，避免留下运行中的孤儿任务。"""
    try:
        configured = await service.configure_task_execution(
            record.task_id,
            record.owner_token or "",
            execution_mode="sync",
        )
        started = await service.start_task(record.task_id, record.owner_token or "")
    except Exception:
        logger.error("同步子智能体追问启动失败: task_id=%s", record.task_id, exc_info=True)
        await _close_aborted_sync_task(service, record)
        return None
    if configured is None or started is None:
        await _close_aborted_sync_task(service, record)
        return None
    return started


async def _run_sync_follow_up(
    service: SubagentTaskPort,
    record: SubagentTaskView,
    runner: Callable[[SubagentTaskView], Awaitable[Any]],
) -> Any:
    """同步追问沿用原会话历史，由调用方注入当前轮执行体。"""
    started = await _start_registered_sync_follow_up(service, record)
    if started is None:
        return "子智能体追问失败：任务状态已变化，请先查询最新状态后再试。"
    return await runner(started)


async def _spawn_follow_up_task(
    *,
    task_id: str | None,
    message: str | None,
    available_types: set[str],
    runtime: ToolRuntime,
    run_controls: Any,
    background: bool,
    runner_factory: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]],
    sync_runner: Callable[[SubagentTaskPort, SubagentTaskView], Coroutine[Any, Any, Any]] | None = None,
) -> Any:
    """对已结束的子智能体发起下一轮；沿用同一子会话，可按同步或后台执行。"""
    normalized = _clean_task_id(task_id)
    content = str(message or "").strip()
    if not normalized:
        return "subagent(send=...) 需要提供 task_id（要追问的子智能体任务）。"
    if not content:
        return "subagent(send=...) 需要提供 message（追问内容）。"
    if not background and sync_runner is None:
        return "子智能体追问失败：同步执行器未初始化。"
    resolved = await _resolve_follow_up_target(
        task_id=normalized,
        available_types=available_types,
        runtime=runtime,
        run_controls=run_controls,
    )
    if isinstance(resolved, str):
        return resolved
    service = resolved.service
    target = resolved.target
    base = resolved.base
    try:
        record = await service.create_task(
            conversation_id=resolved.conversation_id,
            user_id=resolved.user_id,
            parent_run_id=resolved.parent_run_id,
            subagent_type=target.subagent_type,
            description=content,
            label=None,
            parent_tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
            parent_owner_token=str(getattr(run_controls, "owner_token", None) or ""),
            operator_name=resolved.operator_name,
            parent_task_id=base.task_id,
            agent_session_id=base.agent_session_id,
        )
    except (SubagentTaskCapacityError, SubagentSessionClosedError) as exc:
        return str(exc)
    except Exception:
        logger.error(
            "[tools] 子智能体追问任务登记失败: task_id=%s parent=%s",
            normalized,
            target.task_id,
            exc_info=True,
        )
        return "子智能体追问失败：任务登记未成功，请稍后重试。"
    if not background:
        assert sync_runner is not None
        return await sync_runner(service, record)
    await _launch_task_runner_with_recovery(service, record, runtime, runner_factory)
    logger.info(
        "[tools] 子智能体追问已转入后台: task_id=%s parent=%s session=%s",
        record.task_id,
        base.task_id,
        base.agent_session_id,
    )
    return (
        f"已向子智能体 {target.subagent_type} 发起第 {record.turn_index} 轮追问"
        f"（agent_id={record.agent_id}，task_id={record.task_id}，上一轮 task_id={base.task_id}）。"
        "任务在后台执行，会沿用该子会话的完整消息历史；完成后自动唤醒并注入通知，"
        f'也可用 subagent({{"action":"check","task_id":"{record.task_id}"}}) 查询。'
    )


def _clean_task_id(task_id: str | None) -> str:
    return str(task_id or "").strip()


async def _check_async_task(
    service: SubagentTaskPort,
    *,
    conversation_id: str,
    user_id: str | None,
    task_id: str | None,
) -> str:
    """按 task_id 查状态与最终报告。"""
    normalized = _clean_task_id(task_id)
    if not normalized:
        return "manage action=check 需要提供 task_id。"
    if not user_id:
        return "当前会话缺少用户身份。"
    record = await service.get_task(
        normalized,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if record is None:
        return f'子智能体任务 {normalized} 不存在。可用 subagent({{"action":"list"}}) 查看当前会话任务。'
    state = await service.get_session_state(
        _task_session_id(record),
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if record.is_terminal:
        await service.mark_viewed(record)
        return _format_task_report(record, session_status=state.status if state else None)
    return _format_task_status(record, session_status=state.status if state else None)


async def _cancel_async_task(
    service: SubagentTaskPort,
    *,
    conversation_id: str,
    user_id: str | None,
    operator_name: str,
    task_id: str | None,
) -> str:
    """跨 worker 请求取消子智能体任务。"""
    normalized = _clean_task_id(task_id)
    if not normalized:
        return "manage action=cancel 需要提供 task_id。"
    if not user_id:
        return "无法取消：当前会话缺少用户身份。"
    record = await service.cancel_task(conversation_id, user_id, normalized, operator_name)
    if record is None:
        return f"子智能体任务 {normalized} 不存在。"
    if record.status in TERMINAL_TASK_STATUSES:
        return f"子智能体任务 {normalized} 已是 {TASK_STATUS_LABELS.get(record.status, record.status)}，无需取消。"
    return (
        f"已提交取消请求（agent_id={_task_agent_id(record)}，task_id={normalized}）。"
        "执行节点会在下一次心跳时停止任务，"
        f'最终状态可稍后 subagent({{"action":"check","task_id":"{normalized}"}}) 查询。'
    )


async def _list_async_tasks(
    service: SubagentTaskPort,
    *,
    conversation_id: str,
    user_id: str | None,
    agent_id: str | None,
    status: str | None,
    limit: int,
) -> str:
    """先列子智能体；指定 agent_id 时再下钻该智能体的任务 ID。"""
    if not user_id:
        return "当前会话缺少用户身份。"
    records = await service.list_tasks(
        conversation_id,
        user_id,
        statuses=parse_task_status_filter(status),
        limit=limit,
    )
    if not records:
        return "当前会话没有匹配的后台子智能体任务。"
    truncated_note = (
        f"\n注：仅扫描最近 {limit} 条任务；更早记录请加 status 或 agent_id 缩小范围。" if len(records) >= limit else ""
    )
    normalized_agent_id = str(agent_id or "").strip()
    states = await service.list_session_states(
        [_task_session_id(item) for item in records],
        conversation_id=conversation_id,
        user_id=user_id,
    )
    session_statuses = {session_id: state.status for session_id, state in states.items()}
    if normalized_agent_id:
        matched = [item for item in records if _task_agent_id(item) == normalized_agent_id]
        return (
            format_subagent_agent_tasks(normalized_agent_id, matched, session_statuses=session_statuses)
            + truncated_note
        )
    return (
        format_subagent_task_list_summary(records)
        + "\n"
        + format_subagent_agent_list(
            records,
            status_filtered=bool(str(status or "").strip()),
            session_statuses=session_statuses,
        )
        + truncated_note
    )


async def _wait_async_tasks(
    service: SubagentTaskPort,
    *,
    conversation_id: str,
    user_id: str | None,
    task_ids: list[str] | None,
    timeout_seconds: int,
) -> str:
    """等待一个或多个任务进入终态。"""
    if not user_id:
        return "当前会话缺少用户身份。"
    normalized = list(dict.fromkeys(str(item).strip() for item in (task_ids or []) if str(item).strip()))
    if not normalized:
        return "manage action=wait 需要提供 task_ids（至少一个任务 ID）。"
    return await _wait_for_task_views(
        service,
        normalized,
        conversation_id=conversation_id,
        user_id=user_id,
        timeout_seconds=timeout_seconds,
    )


async def manage_async_task(
    action: Literal["list", "check", "wait", "cancel"],
    *,
    runtime: ToolRuntime,
    task_id: str | None = None,
    task_ids: list[str] | None = None,
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    wait_seconds: int = DEFAULT_WAIT_TIMEOUT_SECONDS,
) -> str:
    """执行 ``subagent`` 工具的子智能体任务管理动作。"""
    conversation_id, user_id, operator_name, _parent_run_id = _read_conversation_context(runtime)
    service = _task_service()
    if service is None:
        return "子智能体任务服务未初始化。"
    if action == "list":
        return await _list_async_tasks(
            service,
            conversation_id=conversation_id,
            user_id=user_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )
    if action == "check":
        return await _check_async_task(
            service,
            conversation_id=conversation_id,
            user_id=user_id,
            task_id=task_id,
        )
    if action == "wait":
        return await _wait_async_tasks(
            service,
            conversation_id=conversation_id,
            user_id=user_id,
            task_ids=task_ids,
            timeout_seconds=wait_seconds,
        )
    if action == "cancel":
        return await _cancel_async_task(
            service,
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=operator_name,
            task_id=task_id,
        )
    return f"不支持的操作：{action}"


async def _wait_for_task_views(
    service: SubagentTaskPort,
    task_ids: list[str],
    *,
    conversation_id: str,
    user_id: str,
    timeout_seconds: int,
) -> str:
    """轮询任务直至终态或超时；不存在的任务立即返回明确错误。"""
    deadline = time.monotonic() + timeout_seconds
    records: list[SubagentTaskView] = []
    while True:
        records, missing = await _load_task_views(
            service,
            task_ids,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if missing:
            return f"子智能体任务 {missing} 不存在。"
        if all(item.is_terminal for item in records):
            for item in records:
                await service.mark_viewed(item)
            states = await service.list_session_states(
                [_task_session_id(item) for item in records],
                conversation_id=conversation_id,
                user_id=user_id,
            )
            statuses = {session_id: state.status for session_id, state in states.items()}
            return "\n\n".join(
                _format_task_report(item, session_status=_session_status(item, statuses)) for item in records
            )
        if time.monotonic() >= deadline:
            return "等待超时，任务仍在执行：\n" + "\n".join(_format_task_status(item) for item in records)
        await asyncio.sleep(WAIT_POLL_SECONDS)


async def _load_task_views(
    service: SubagentTaskPort,
    task_ids: list[str],
    *,
    conversation_id: str,
    user_id: str,
) -> tuple[list[SubagentTaskView], str]:
    records: list[SubagentTaskView] = []
    for task_id in task_ids:
        record = await service.get_task(task_id, conversation_id=conversation_id, user_id=user_id)
        if record is None:
            return [], task_id
        records.append(record)
    return records, ""


def _get_subagent_stream_writer(stream_events: bool, writer_factory: Callable[[], Any]) -> Any:
    """只有前台 subagent 需要父流 writer；拿不到时降级为静默过程。"""
    if not stream_events:
        return None
    try:
        return writer_factory()
    except Exception as exc:
        logger.warning("[tools] 获取子图流式 writer 失败,子智能体过程将不做实时下发: %s", exc)
        return None


def _append_partial_report(report: str, partial_report: str) -> str:
    if not partial_report:
        return report
    return f"{report}\n\n中断前已产出的内容：\n{partial_report}"


async def _watch_background_task(
    task_id: str,
    owner_token: str,
    progress: dict[str, Any],
    running_task: asyncio.Task | None,
    service: SubagentTaskPort,
) -> None:
    """心跳续租并观察跨 worker 取消；租约丢失/终态时停止本地执行。"""
    while True:
        await asyncio.sleep(TASK_HEARTBEAT_SECONDS)
        try:
            status = await service.heartbeat_task(task_id, owner_token, dict(progress))
        except Exception:
            logger.warning("[tools] 后台任务心跳失败，下轮重试: task_id=%s", task_id, exc_info=True)
            continue
        if status is None or status == TASK_STATUS_CANCELLING or status in TERMINAL_TASK_STATUSES:
            if running_task is not None and not running_task.done():
                running_task.cancel(
                    "subagent_task_cancelled" if status == TASK_STATUS_CANCELLING else "subagent_task_closed"
                )
            return
