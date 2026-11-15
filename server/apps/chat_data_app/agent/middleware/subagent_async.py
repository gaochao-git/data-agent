"""后台子智能体：task 工具 background=true 的持久任务账本与配套工具。

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
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Literal

from langchain.tools import ToolRuntime

from utils.logger import get_logger

from ..contracts.subagent_task import (
    ACTIVE_TASK_STATUSES,
    TASK_HEARTBEAT_SECONDS,
    TASK_STATUS_CANCELLING,
    TASK_STATUS_LABELS,
    TASK_STATUS_PAUSED,
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_APPROVAL,
    TERMINAL_TASK_STATUSES,
    SubagentTaskCapacityError,
    SubagentTaskPort,
    SubagentTaskView,
    get_subagent_task_service,
    parse_task_status_filter,
)

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


def _format_task_status(record: SubagentTaskView) -> str:
    label = TASK_STATUS_LABELS.get(record.status, record.status)
    turn_index = int(getattr(record, "turn_index", 1) or 1)
    turn = f" 轮次={turn_index}" if turn_index > 1 else ""
    prefix = f"[{label}] task_id={record.task_id}{turn} 类型={record.subagent_type} 任务={record.label}"
    if record.is_terminal:
        return prefix
    return f"{prefix}\n  {_task_progress_text(record)}"


def _task_session_id(record: SubagentTaskView) -> str:
    """任务对应的子会话 ID；兼容改造前没有 agent_session_id 的记录。"""
    return str(getattr(record, "agent_session_id", "") or record.task_id)


def format_active_subagent_summary(records: Sequence[SubagentTaskView]) -> str:
    """把活跃任务压成主智能体可读的一行摘要，按子会话去重。"""
    session_statuses = {_task_session_id(record): record.status for record in records}
    if not session_statuses:
        return "当前会话没有活跃的后台子智能体。"
    executing = sum(
        1
        for status in session_statuses.values()
        if status in {TASK_STATUS_PENDING, TASK_STATUS_RUNNING, TASK_STATUS_CANCELLING}
    )
    waiting = sum(1 for status in session_statuses.values() if status == TASK_STATUS_WAITING_APPROVAL)
    paused = sum(1 for status in session_statuses.values() if status == TASK_STATUS_PAUSED)
    parts = [f"执行中 {executing}"] if executing else []
    if waiting:
        parts.append(f"等待审批 {waiting}")
    if paused:
        parts.append(f"已暂停 {paused}")
    return f"当前会话有 {len(session_statuses)} 个活跃子智能体（{'、'.join(parts)}）。"


def format_subagent_task_list_summary(records: Sequence[SubagentTaskView]) -> str:
    """任务列表抬头：同时给出任务条数、子智能体数和活跃条数。"""
    if not records:
        return "当前会话没有匹配的后台子智能体任务。"
    session_ids = {_task_session_id(record) for record in records}
    active = sum(1 for record in records if record.status in ACTIVE_TASK_STATUSES)
    return f"后台子智能体任务（共 {len(records)} 条，涉及 {len(session_ids)} 个子智能体，活跃 {active} 条）："


def _format_task_report(record: SubagentTaskView) -> str:
    """终态任务报告：有落盘路径返回路径+预览，否则直接返回完整正文。"""
    body = record.report or record.error or "（无报告正文）"
    session_id = str(getattr(record, "agent_session_id", "") or getattr(record, "root_task_id", "") or record.task_id)
    turn_index = int(getattr(record, "turn_index", 1) or 1)
    lines = [
        f"[{TASK_STATUS_LABELS.get(record.status, record.status)}] task_id={record.task_id} "
        f"类型={record.subagent_type} 子会话={session_id} 轮次={turn_index}",
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
    if record.is_terminal:
        lines.append(f'可基于同一子会话继续追问：task(action="follow_up", task_id="{record.task_id}", message="...")。')
    return "\n".join(lines)


async def _spawn_background_task(
    *,
    subagent_type: str,
    description: str,
    label: str | None,
    timeout_seconds: int | None,
    runtime: ToolRuntime,
    run_controls: Any,
    runner_factory: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]],
) -> str:
    """登记持久任务并立即返回；执行体在独立 asyncio task 中运行。"""
    conversation_id, user_id, operator_name, parent_run_id = _read_conversation_context(runtime, run_controls)
    if not user_id:
        return "无法启动后台子智能体：当前会话缺少用户身份。"
    service = _task_service()
    if service is None:
        return "后台子智能体启动失败：任务账本服务未初始化。"
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
            timeout_seconds=timeout_seconds,
            operator_name=operator_name,
        )
    except SubagentTaskCapacityError as exc:
        return str(exc)
    except Exception:
        logger.error(
            "[tools] 后台任务登记失败: conversation_id=%s type=%s", conversation_id, subagent_type, exc_info=True
        )
        return "后台子智能体启动失败：任务登记未成功，请稍后重试或改为同步执行。"

    _launch_task_runner(service, record, runtime, runner_factory)
    logger.info(
        "[tools] 子智能体已转入后台执行: task_id=%s type=%s conversation_id=%s",
        record.task_id,
        subagent_type,
        conversation_id,
    )
    return (
        f"子智能体已在后台启动（task_id={record.task_id}，类型={subagent_type}）。"
        f"超时={record.timeout_seconds} 秒。任务不会阻塞当前对话：完成/失败/超时后系统会自动唤醒"
        "一条隐藏通知（不需要等用户先发消息）；期间可继续其他工作，也可用 "
        'task 查询：action="list" 列任务，action="check" 查单个，action="wait" 等结果。'
    )


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


async def _spawn_follow_up_task(
    *,
    task_id: str | None,
    message: str | None,
    available_types: set[str],
    runtime: ToolRuntime,
    run_controls: Any,
    runner_factory: Callable[[SubagentTaskView, Any], Coroutine[Any, Any, None]],
) -> str:
    """对已结束的子智能体发起下一轮；沿用同一子会话历史。"""
    normalized = _clean_task_id(task_id)
    content = str(message or "").strip()
    if not normalized:
        return "action=follow_up 需要提供 task_id（要追问的子智能体任务）。"
    if not content:
        return "action=follow_up 需要提供 message（追问内容）。"
    conversation_id, user_id, operator_name, parent_run_id = _read_conversation_context(runtime, run_controls)
    if not user_id:
        return "无法追问子智能体：当前会话缺少用户身份。"
    service = _task_service()
    if service is None:
        return "子智能体追问失败：任务账本服务未初始化。"
    target = await service.get_task(
        normalized,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if target is None:
        return f'子智能体任务 {normalized} 不存在。可用 task(action="list") 查看当前会话任务。'
    if not target.is_terminal:
        return (
            f"子智能体任务 {normalized} 仍在执行（{TASK_STATUS_LABELS.get(target.status, target.status)}），"
            '不能追问；可先用 task(action="wait") 等结果，或 task(action="cancel") 取消后再开始新一轮。'
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
    try:
        record = await service.create_task(
            conversation_id=conversation_id,
            user_id=user_id,
            parent_run_id=parent_run_id,
            subagent_type=target.subagent_type,
            description=content,
            label=None,
            parent_tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
            parent_owner_token=str(getattr(run_controls, "owner_token", None) or ""),
            timeout_seconds=None,
            operator_name=operator_name,
            parent_task_id=base.task_id,
            agent_session_id=base.agent_session_id,
        )
    except SubagentTaskCapacityError as exc:
        return str(exc)
    except Exception:
        logger.error(
            "[tools] 子智能体追问任务登记失败: task_id=%s parent=%s",
            normalized,
            target.task_id,
            exc_info=True,
        )
        return "子智能体追问失败：任务登记未成功，请稍后重试。"
    _launch_task_runner(service, record, runtime, runner_factory)
    logger.info(
        "[tools] 子智能体追问已转入后台: task_id=%s parent=%s session=%s",
        record.task_id,
        base.task_id,
        base.agent_session_id,
    )
    return (
        f"已向子智能体 {target.subagent_type} 发起第 {record.turn_index} 轮追问"
        f"（task_id={record.task_id}，子会话={record.agent_session_id}，上一轮={base.task_id}）。"
        "任务在后台执行，会沿用该子会话的完整消息历史；完成后自动唤醒并注入通知，"
        f'也可用 task(action="check", task_id="{record.task_id}") 查询。'
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
        return "action=check 需要提供 task_id。"
    if not user_id:
        return "当前会话缺少用户身份。"
    record = await service.get_task(
        normalized,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if record is None:
        return f'后台任务 {normalized} 不存在。可用 task(action="list") 查看当前会话任务。'
    if record.is_terminal:
        await service.mark_viewed(record)
        return _format_task_report(record)
    return _format_task_status(record)


async def _cancel_async_task(
    service: SubagentTaskPort,
    *,
    conversation_id: str,
    user_id: str | None,
    operator_name: str,
    task_id: str | None,
) -> str:
    """跨 worker 请求取消后台任务。"""
    normalized = _clean_task_id(task_id)
    if not normalized:
        return "action=cancel 需要提供 task_id。"
    if not user_id:
        return "无法取消：当前会话缺少用户身份。"
    record = await service.cancel_task(conversation_id, user_id, normalized, operator_name)
    if record is None:
        return f"后台任务 {normalized} 不存在。"
    if record.status in TERMINAL_TASK_STATUSES:
        return f"后台任务 {normalized} 已是 {TASK_STATUS_LABELS.get(record.status, record.status)}，无需取消。"
    return (
        f"已提交取消请求（task_id={normalized}）。执行节点会在下一次心跳时停止任务，"
        f'最终状态可稍后 task(action="check", task_id="{normalized}") 查询。'
    )


async def _list_async_tasks(
    service: SubagentTaskPort,
    *,
    conversation_id: str,
    user_id: str | None,
    status: str | None,
    limit: int,
) -> str:
    """列出当前会话后台任务。"""
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
    return format_subagent_task_list_summary(records) + "\n" + "\n".join(_format_task_status(item) for item in records)


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
        return "action=wait 需要提供 task_ids（至少一个任务 ID）。"
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
    status: str | None = None,
    limit: int = 20,
    wait_seconds: int = DEFAULT_WAIT_TIMEOUT_SECONDS,
) -> str:
    """执行 ``task`` 工具的后台任务管理动作。"""
    conversation_id, user_id, operator_name, _parent_run_id = _read_conversation_context(runtime)
    service = _task_service()
    if service is None:
        return "后台任务服务未初始化。"
    if action == "list":
        return await _list_async_tasks(
            service,
            conversation_id=conversation_id,
            user_id=user_id,
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
            return f"后台任务 {missing} 不存在。"
        if all(item.is_terminal for item in records):
            for item in records:
                await service.mark_viewed(item)
            return "\n\n".join(_format_task_report(item) for item in records)
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
    """只有前台 task 需要父流 writer；拿不到时降级为静默过程。"""
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
