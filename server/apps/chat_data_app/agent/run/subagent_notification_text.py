"""子智能体生命周期通知文案。

通知是主智能体感知后台任务状态的唯一业务入口，文案必须明确区分终态类型
与取消来源；单独成模块，避免任务账本服务继续膨胀。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..contracts.subagent_task import CANCEL_ORIGIN_AGENT, CANCEL_ORIGIN_USER, TASK_STATUS_LABELS

if TYPE_CHECKING:
    from .subagent_task_service import SubagentTaskRecord

NOTIFICATION_REPORT_PREVIEW_CHARS = 1600


def _preview(text: str, max_chars: int = NOTIFICATION_REPORT_PREVIEW_CHARS) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars] + (
        f"\n…[报告共 {len(normalized)} 字符，当前只展示预览；"
        '完整内容请调用 subagent({"action":"check","task_id":"..."}) 查看；'
        "超长报告会落盘并返回 result_path]"
    )


def suspension_notification_text(task: SubagentTaskRecord) -> str:
    """等待审批的冒泡文本：只陈述待审批操作，不代替用户做决定。"""
    payload = task.meta.get("pending_interrupt")
    items = payload.get("approval_items") if isinstance(payload, dict) else None
    lines: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("tool_name") or "工具调用")
        intent = str(item.get("intent") or "").strip()
        lines.append(f"- {label}" + (f"：{intent}" if intent else ""))
    detail = "\n".join(lines) if lines else "- 子智能体请求执行需要审批的工具（详情见任务详情）"
    return (
        "## 后台子智能体等待审批（内部消息）\n"
        f"task_id={task.task_id}\n"
        f"类型={task.subagent_type}；任务={task.label}\n\n"
        f"待审批操作：\n{detail}\n\n"
        "子智能体不支持审批参数，该状态只会出现在历史或非标准装配任务中。"
        f'请用 subagent({{"action":"cancel","task_id":"{task.task_id}"}}) '
        "取消后重新派发；不要重复派发同一任务。"
    )


def resumed_notification_text(task: SubagentTaskRecord) -> str:
    return (
        "## 后台子智能体审批完成并恢复执行（内部消息）\n"
        f"task_id={task.task_id}\n"
        f"类型={task.subagent_type}；任务={task.label}\n\n"
        "后续终态仍会另行通知，不要因为恢复而重复派发同一任务。"
    )


def user_message_notification_text(task: SubagentTaskRecord, excerpt: str) -> str:
    return (
        "## 用户直接联系了子智能体（内部消息）\n"
        f"task_id={task.task_id}\n"
        f"类型={task.subagent_type}；任务={task.label}\n\n"
        f"用户消息：{excerpt}\n\n"
        "这是用户与子智能体的独立对话，不要把它当成新的派发指令；"
        "如需继续管理该子会话，先调用 "
        'subagent({"action":"check","task_id":"..."}) 获取最新状态。'
    )


def _terminal_header(cancelled: bool, user_cancelled: bool) -> str:
    if user_cancelled:
        return "用户手动停止后台子智能体"
    if cancelled:
        return "后台子智能体任务状态通知"
    return "后台子智能体完成通知"


def _cancel_source_line(task: SubagentTaskRecord, cancelled: bool, user_cancelled: bool) -> str:
    if user_cancelled:
        return "取消来源=用户主动停止\n"
    if cancelled and task.meta.get("cancel_origin") == CANCEL_ORIGIN_AGENT:
        return "取消来源=主智能体\n"
    return ""


def _terminal_guidance(task: SubagentTaskRecord, cancelled: bool, user_cancelled: bool) -> str:
    if user_cancelled:
        return (
            "这是用户主动停止，不是任务失败，也不是让你继续推进的信号；不需要汇报完成结论。"
            "不要继续执行该任务，不要重新派发同类任务，也不要调用 subagent 的 send/check/wait 续写。"
            "如中断前已有部分结论，可简要告知用户“已停止”及已有结果；"
            "除非用户在下一条消息中明确要求继续或重新执行，否则保持停止。"
        )
    if not cancelled:
        return "请根据报告向用户简要汇报结论（不要重复执行任务）；"
    return (
        "任务已被取消，不需要汇报完成结论；如中断前已有结论可简要告知用户。"
        "需要基于同一子会话继续时调用 "
        f'subagent({{"action":"send","task_id":"{task.task_id}","message":"..."}})，'
        "或重新派发新任务。"
    )


def terminal_notification_text(task: SubagentTaskRecord) -> str:
    cancelled = task.status == "cancelled"
    user_cancelled = cancelled and task.meta.get("cancel_origin") == CANCEL_ORIGIN_USER
    outcome = TASK_STATUS_LABELS.get(task.status, task.status)
    report = _preview(task.report or task.error or "无报告正文")
    path_line = (
        f"完整报告已落盘：result_path={task.report_path}（{len(task.report)} 字符，可用 file_read 读取）。\n"
        if task.report_path
        else ""
    )
    guidance = _terminal_guidance(task, cancelled, user_cancelled)
    if cancelled:
        return (
            f"## {_terminal_header(cancelled, user_cancelled)}（内部消息）\n"
            f"task_id={task.task_id}\n"
            f"类型={task.subagent_type}；状态={outcome}\n"
            f"{_cancel_source_line(task, cancelled, user_cancelled)}"
            f"任务={task.label}\n\n"
            f"中断前内容预览：\n{report}\n\n{path_line}{guidance}"
        )
    return (
        f"## {_terminal_header(cancelled, user_cancelled)}（内部消息）\n"
        f"task_id={task.task_id}\n"
        f"类型={task.subagent_type}；状态={outcome}\n"
        f"任务={task.label}\n\n"
        f"报告预览：\n{report}\n\n{path_line}{guidance}"
        f'需要完整报告或最新状态时调用 subagent({{"action":"check","task_id":"{task.task_id}"}})；'
        + (
            "完整报告已落盘，请优先用 file_read 读取 result_path，不要只凭预览下结论。"
            if task.report_path
            else 'subagent({"action":"check","task_id":"..."}) 会返回完整正文，不要只凭预览下结论。'
        )
        + "如报告中提到产物文件，用 present_artifacts 展示。"
        + "需要基于同一子会话继续分析/修改结论时，用 "
        + f'subagent({{"action":"send","task_id":"{task.task_id}","message":"..."}})。'
    )


__all__ = [
    "NOTIFICATION_REPORT_PREVIEW_CHARS",
    "resumed_notification_text",
    "suspension_notification_text",
    "terminal_notification_text",
    "user_message_notification_text",
]
