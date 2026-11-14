"""后台子智能体：task 工具 background=true 的注册表、派生与管理工具。

与 ``middleware/subagents.py`` 的分工：本模块只管「后台任务」的簿记与工具
（注册表、派生、完成通知收集、check/cancel/list 三件套），子图执行体
（``_run_background_task``）留在 subagents.py——它依赖 ``_build_task_tool_impl``
的子图派发与报告抽取，经 ``runner_factory`` 参数注入，保持单向依赖。

任务注册表为模块级按 conversation_id 分桶：后台任务的生命周期可以跨越单次
Run——父 Run 结束前未取回的报告，由同一会话下一次 Run 的模型调用前注入兜底
（对齐 deepagents「空闲入队、忙碌拉取」双通道）。进程重启后任务丢失（与
Octop inbox 同级取舍），文档已注明。
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from utils.logger import get_logger

logger = get_logger(__name__)

# 同时执行中的后台任务上限，防主 agent 在一轮里无节制派发拖垮子图资源；
# 已完成未上报的条目不计入上限，但会在下一次模型调用前随完成通知注入并出册。
MAX_BACKGROUND_TASKS = 20
# 完成通知注入主上下文时的单条报告截断：完整报告已落盘（offload），通知只带结论。
BACKGROUND_NOTIFICATION_REPORT_CHARS = 1200


def _truncate_text(text: str, max_chars: int, label: str = "结果") -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[{label}过长已截断，完整内容共 {len(text)} 字符]"


@dataclass
class _BackgroundTaskEntry:
    """后台子智能体任务条目。"""

    task_id: str
    conversation_id: str
    user_id: str | None
    subagent_type: str
    description: str
    status: str = "running"  # running / completed / failed / cancelled
    report: str = ""
    tool_status: str = ""
    reported: bool = False
    started_at: datetime = field(default_factory=datetime.now)
    asyncio_task: asyncio.Task | None = None


# 后台子智能体任务注册表：conversation_id → (task_id → 条目)。
_BACKGROUND_TASKS: dict[str, dict[str, _BackgroundTaskEntry]] = {}


def _read_conversation_context(runtime: ToolRuntime) -> tuple[str, str | None]:
    """从当轮 state 读会话身份：conversation_id + user_id（白名单透传键）。

    state 缺 conversation_id（理论外路径，如测试直接构造）时用随机桶隔离，
    保证不串会话；该桶无注入通道，任务结果等同丢弃。
    """
    state = getattr(runtime, "state", None) or {}
    conversation_id = str(state.get("conversation_id") or "").strip()
    user_id = str(state.get("user_id") or "").strip() or None
    if not conversation_id:
        conversation_id = f"unknown-{uuid4().hex[:8]}"
    return conversation_id, user_id


def _spawn_background_task(
    *,
    subagent_type: str,
    description: str,
    runtime: ToolRuntime,
    runner_factory: Callable[[_BackgroundTaskEntry], Coroutine[Any, Any, None]],
) -> str:
    """把子智能体转入后台执行：立即返回确认文本，不阻塞主 agent。

    子图在独立 asyncio task 中静默运行（不往父流转发过程事件——父级 task
    卡片已随本调用的确认文本关闭）；完成后报告写入注册表，由
    ``_collect_background_notifications`` 在主 agent 下一次模型调用前注入。
    执行体经 ``runner_factory`` 注入（见 subagents.py ``_run_background_task``）。
    """
    conversation_id, user_id = _read_conversation_context(runtime)
    bucket = _BACKGROUND_TASKS.setdefault(conversation_id, {})
    running = [item for item in bucket.values() if item.status == "running"]
    if len(running) >= MAX_BACKGROUND_TASKS:
        return f"后台子智能体任务已达上限（{MAX_BACKGROUND_TASKS} 个执行中），请等当前任务完成或用 list_async_tasks 查询后再派发。"
    task_id = runtime.tool_call_id or f"bg-{uuid4().hex[:8]}"
    entry = _BackgroundTaskEntry(
        task_id=task_id,
        conversation_id=conversation_id,
        user_id=user_id,
        subagent_type=subagent_type,
        description=description,
    )
    bucket[task_id] = entry
    entry.asyncio_task = asyncio.create_task(runner_factory(entry))
    logger.info(
        "[tools] 子智能体已转入后台执行: task_id=%s type=%s conversation_id=%s", task_id, subagent_type, conversation_id
    )
    return (
        f"子智能体已在后台启动（task_id={task_id}，类型={subagent_type}）。"
        "它不会阻塞当前对话：完成后的报告会自动注入你的上下文；"
        "期间可用 list_async_tasks / check_async_task 查询进度。请继续你的其他工作，或直接结束本轮等待完成通知。"
    )


def _collect_background_notifications(conversation_id: str) -> str | None:
    """收集该会话已完成未上报的后台任务报告并出册；无则返回 None（不触碰请求）。"""
    bucket = _BACKGROUND_TASKS.get(conversation_id)
    if not bucket:
        return None
    done = [item for item in bucket.values() if item.status in ("completed", "failed") and not item.reported]
    if not done:
        return None
    blocks = []
    for item in done:
        item.reported = True
        bucket.pop(item.task_id, None)
        blocks.append(
            f"- task_id={item.task_id} 类型={item.subagent_type} 状态={item.status}\n"
            f"  报告：{_truncate_text(item.report, BACKGROUND_NOTIFICATION_REPORT_CHARS)}"
        )
    if conversation_id in _BACKGROUND_TASKS and not _BACKGROUND_TASKS[conversation_id]:
        # 空桶出册，防长期运行下的键泄漏。
        _BACKGROUND_TASKS.pop(conversation_id, None)
    return (
        "## 后台子智能体完成通知\n"
        "以下后台任务的最终报告已就绪，结论可直接引用；完整产物按报告内路径处理：\n" + "\n".join(blocks)
    )


class CheckAsyncTaskArgs(BaseModel):
    """``check_async_task`` 工具入参。"""

    task_id: str = Field(min_length=1, max_length=64, description="要查询的后台任务 ID。")


class CancelAsyncTaskArgs(BaseModel):
    """``cancel_async_task`` 工具入参。"""

    task_id: str = Field(min_length=1, max_length=64, description="要取消的后台任务 ID。")


def build_check_async_task_tool() -> StructuredTool:
    """构建 ``check_async_task``：按 task_id 查单个后台任务的状态与报告。"""

    async def check_async_task(task_id: str, runtime: ToolRuntime) -> str:
        conversation_id, _ = _read_conversation_context(runtime)
        entry = _BACKGROUND_TASKS.get(conversation_id, {}).get(task_id)
        if entry is None:
            return f"后台任务 {task_id} 不存在（或已交付出册）。可用 list_async_tasks 查看当前任务。"
        if entry.status == "running":
            return (
                f"[执行中] task_id={task_id} 类型={entry.subagent_type} "
                f"任务书：{_truncate_text(entry.description, 200)}"
            )
        return (
            f"[{entry.status}] task_id={task_id} 类型={entry.subagent_type}\n报告：{_truncate_text(entry.report, 2000)}"
        )

    return StructuredTool.from_function(
        name="check_async_task",
        coroutine=check_async_task,
        description="按 task_id 查询单个后台子智能体任务的状态与最终报告。",
        args_schema=CheckAsyncTaskArgs,
    )


def build_cancel_async_task_tool() -> StructuredTool:
    """构建 ``cancel_async_task``：取消仍在执行的后台子智能体任务。"""

    async def cancel_async_task(task_id: str, runtime: ToolRuntime) -> str:
        conversation_id, _ = _read_conversation_context(runtime)
        entry = _BACKGROUND_TASKS.get(conversation_id, {}).get(task_id)
        if entry is None:
            return f"后台任务 {task_id} 不存在（或已交付出册）。"
        if entry.status != "running":
            return f"后台任务 {task_id} 已是 {entry.status} 状态，无需取消。"
        if entry.asyncio_task is not None:
            entry.asyncio_task.cancel()
        entry.status = "cancelled"
        entry.report = "任务已被主智能体取消。"
        logger.info("[tools] 后台子智能体已取消: task_id=%s", task_id)
        return f"后台任务 {task_id} 已取消。"

    return StructuredTool.from_function(
        name="cancel_async_task",
        coroutine=cancel_async_task,
        description="取消一个仍在执行的后台子智能体任务。",
        args_schema=CancelAsyncTaskArgs,
    )


def build_list_async_tasks_tool() -> StructuredTool:
    """构建 ``list_async_tasks``：列出当前会话全部后台任务（含已完成报告）。"""

    async def list_async_tasks(runtime: ToolRuntime) -> str:
        conversation_id, _ = _read_conversation_context(runtime)
        bucket = _BACKGROUND_TASKS.get(conversation_id) or {}
        if not bucket:
            return "当前会话没有后台子智能体任务。"
        blocks = []
        for item in bucket.values():
            if item.status == "running":
                blocks.append(
                    f"- [执行中] task_id={item.task_id} 类型={item.subagent_type} "
                    f"任务书：{_truncate_text(item.description, 120)}"
                )
            else:
                blocks.append(
                    f"- [{item.status}] task_id={item.task_id} 类型={item.subagent_type}\n"
                    f"  报告：{_truncate_text(item.report, 800)}"
                )
        return "后台子智能体任务：\n" + "\n".join(blocks)

    return StructuredTool.from_function(
        name="list_async_tasks",
        coroutine=list_async_tasks,
        description="列出当前会话全部后台子智能体任务：执行中的看进度，已完成/失败的直接读报告。",
    )
