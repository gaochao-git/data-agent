"""会话记忆服务：存储、并发合并、自动抽取调度与注入渲染（单一权威入口）。

职责与边界：
- 存储：会话记忆文件固定为 ``<会话 user-data>/memory/memory.md``（模型可见、
  用户可删的人工治理通道），锁文件固定为 ``<会话 user-data>/.memory.lock``
  （user-data 层，不出现在用户可见的 workspace/outputs/uploads/memory 文件列表中）。
- 并发：跨进程写合并用 ``fcntl.flock`` 包住「读-去重-淘汰-写」临界区，
  写入用 tmp 文件 + ``os.replace`` 原子替换。
- 自动抽取：两个触发点调度同一后台抽取——
  1) 整轮正常结束（``after_agent_turn_with_memory``，30s 防抖合并连续轮次）；
  2) 上下文压缩即将丢弃消息（``schedule_memory_extraction_now`` 立即抽取，
     此时调度快照是压缩回调交付的将被丢弃消息）。
  抽取用当轮摘要同源 LLM 输出 JSON，服务端解析校验，任何失败只告警降级，
  不影响对话主流程。
- 注入渲染：``render_session_memory_block`` 读文件→解析→预算裁剪→逐条转义，
  供 system prompt 构建方拼接；开关关闭或无内容时返回空串。

会话维度按 user_id/datasource 的授权不适用：路径由服务端从会话工作区定位，
本服务不接收任何外部传入路径，无横向越权面。
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from apps.chat_data_app.agent.prompts.memory_extraction import build_memory_extraction_prompt
from apps.chat_data_app.services.llm_config import get_chat_data_llm
from apps.chat_data_app.services.runtime_config import read_bool_env, read_positive_int_env
from utils.llm_json import parse_llm_json
from utils.logger import get_logger

from .memory_parser import (
    MAX_MEMORY_ITEMS,
    MemoryItem,
    enforce_memory_item_limit,
    escape_memory_for_prompt,
    normalize_memory_items,
    parse_memory_document,
    render_memory_document,
    select_items_within_budget,
)

logger = get_logger(__name__)

MEMORY_FILE_NAME = "memory.md"
MEMORY_LOCK_FILE_NAME = ".memory.lock"

# 整轮结束后的防抖窗口：连续快速多轮时只抽最后一轮，避免每轮一次 LLM 调用
EXTRACTION_DEBOUNCE_SECONDS = 30.0
# 抽取快照消息条数上限：只看最近若干条，足够覆盖最近 1-2 轮的口径与纠错
EXTRACTION_SNAPSHOT_MESSAGE_LIMIT = 40
# 抽取 LLM 调用输出上限：条目本身限长，2000 tokens 富余
EXTRACTION_LLM_MAX_TOKENS = 2000

# 会话级防抖任务表：conversation_id -> 最新调度任务；新调度取消旧任务
_pending_extraction_tasks: dict[str, asyncio.Task] = {}
# 立即抽取后台任务的强引用集：防止任务被 GC 静默吞掉
_background_extraction_tasks: set[asyncio.Task] = set()


def session_memory_enabled() -> bool:
    """会话记忆自动抽取与注入总开关（默认开启）。"""
    return read_bool_env("CHAT_DATA_SESSION_MEMORY_ENABLED", True)


def session_memory_max_items() -> int:
    """单会话记忆条目上限。"""
    return read_positive_int_env("CHAT_DATA_SESSION_MEMORY_MAX_ITEMS", MAX_MEMORY_ITEMS)


def session_memory_injection_max_tokens() -> int:
    """会话记忆注入 system prompt 的 token 预算（1 token≈1 字符）。"""
    return read_positive_int_env("CHAT_DATA_SESSION_MEMORY_INJECTION_MAX_TOKENS", 1500)


# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------


def memory_file_path(workspace_root: str) -> Path:
    """会话记忆文件：``<user-data>/memory/memory.md``。"""
    return Path(workspace_root) / "memory" / MEMORY_FILE_NAME


def memory_lock_path(workspace_root: str) -> Path:
    """跨进程合并锁文件：``<user-data>/.memory.lock``（非用户可见区）。"""
    return Path(workspace_root) / MEMORY_LOCK_FILE_NAME


# ---------------------------------------------------------------------------
# 读取、合并与原子写入
# ---------------------------------------------------------------------------


def load_memory_items(workspace_root: str) -> list[MemoryItem]:
    """读取并解析会话记忆条目；文件不存在或异常时回退空列表（不抛出）。"""
    path = memory_file_path(workspace_root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        logger.warning("[memory] 会话记忆文件读取失败: %s", path, exc_info=True)
        return []
    return parse_memory_document(text)


def _atomic_write_text(path: Path, text: str) -> None:
    """tmp 文件 + ``os.replace`` 原子写入；tmp 落在 user-data 层避免污染 workspace 列表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent.parent), prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


def merge_memory_items(
    workspace_root: str,
    new_items: Iterable[MemoryItem],
    *,
    max_items: int = MAX_MEMORY_ITEMS,
) -> list[MemoryItem]:
    """跨进程安全的记忆合并：flock 临界区内「读-去重-淘汰-写」。

    新条目按空白折叠键去重后追加在已有条目之后（文件内顺序即新旧，
    越靠后越新）；超出上限时淘汰最早的条目。
    """
    normalized_new = normalize_memory_items(new_items)
    lock_path = memory_lock_path(workspace_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        try:
            existing = load_memory_items(workspace_root)
            merged: list[MemoryItem] = []
            seen_keys: set[str] = set()
            for item in [*existing, *normalized_new]:
                key = " ".join(f"{item.category} {item.content} {item.scope}".split())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(item)
            merged = enforce_memory_item_limit(merged, max_items=max_items)
            _atomic_write_text(
                memory_file_path(workspace_root),
                render_memory_document(merged),
            )
            return merged
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# 抽取（LLM 后台调用）
# ---------------------------------------------------------------------------


def _extract_llm_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") if isinstance(item, dict) else item) for item in content)
    return str(content or "").strip()


async def extract_memory_items(messages: list[Any], *, model_id: str | None) -> list[MemoryItem]:
    """调用 LLM 从消息快照抽取记忆条目；无候选或失败时返回空列表（不抛出）。"""
    prompt = build_memory_extraction_prompt(messages)
    try:
        llm = get_chat_data_llm(
            model=model_id,
            streaming=False,
            enable_thinking=False,
            max_tokens=EXTRACTION_LLM_MAX_TOKENS,
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception:
        logger.warning("[memory] 会话记忆抽取 LLM 调用失败: model_id=%s", model_id, exc_info=True)
        return []
    try:
        payload = parse_llm_json(_extract_llm_text(response))
    except Exception:
        logger.warning("[memory] 会话记忆抽取输出解析失败: model_id=%s", model_id, exc_info=True)
        return []
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return []
    candidates = [
        MemoryItem(
            category=str(raw.get("category") or ""),
            content=str(raw.get("content") or ""),
            scope=str(raw.get("scope") or ""),
        )
        for raw in raw_items
        if isinstance(raw, dict)
    ]
    return normalize_memory_items(candidates)


async def extract_and_save_memory(
    *,
    conversation_id: str,
    workspace_root: str,
    model_id: str | None,
    messages: list[Any],
) -> None:
    """抽取并合并落盘；抽取为空或写入失败只告警降级，不抛出。"""
    items = await extract_memory_items(messages, model_id=model_id)
    if not items:
        return
    try:
        merged = await asyncio.to_thread(
            merge_memory_items,
            workspace_root,
            items,
            max_items=session_memory_max_items(),
        )
    except Exception:
        logger.warning("[memory] 会话记忆写入失败: conversation_id=%s", conversation_id, exc_info=True)
        return
    logger.info(
        "[memory] 会话记忆已更新: conversation_id=%s items=%s total=%s",
        conversation_id,
        len(items),
        len(merged),
    )


# ---------------------------------------------------------------------------
# 调度（防抖 / 立即）
# ---------------------------------------------------------------------------


def _prepare_scheduled_extraction(*, conversation_id: str, messages: list[Any]) -> bool:
    """抽取调度守卫：开关关闭、会话为空或快照为空时不调度。"""
    if not session_memory_enabled():
        return False
    if not str(conversation_id or "").strip():
        return False
    return bool(messages)


def snapshot_messages_for_extraction(messages: Any) -> list[Any]:
    """复制最近若干条消息作为抽取快照（非列表输入回退空列表，不抛出）。"""
    try:
        items = list(messages or [])
    except TypeError:
        return []
    return items[-EXTRACTION_SNAPSHOT_MESSAGE_LIMIT:]


def _discard_pending_task(task: asyncio.Task, conversation_id: str) -> None:
    """任务完成后清理防抖表；仅当表内仍是本任务时移除，避免误删新任务。"""
    if _pending_extraction_tasks.get(conversation_id) is task:
        _pending_extraction_tasks.pop(conversation_id, None)


async def _debounced_extract(**kwargs: Any) -> None:
    """防抖等待后执行抽取；被新调度取消时静默终止。"""
    await asyncio.sleep(EXTRACTION_DEBOUNCE_SECONDS)
    await extract_and_save_memory(**kwargs)


def schedule_memory_extraction_after_turn(
    *,
    conversation_id: str,
    workspace_root: str,
    user_id: str | None,
    username: str | None,
    model_id: str | None,
    messages: Any,
) -> None:
    """整轮结束后的防抖抽取调度：同一会话连续调度只保留最新一次。"""
    snapshot = snapshot_messages_for_extraction(messages)
    if not _prepare_scheduled_extraction(conversation_id=conversation_id, messages=snapshot):
        return
    previous = _pending_extraction_tasks.pop(conversation_id, None)
    if previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(
        _debounced_extract(
            conversation_id=conversation_id,
            workspace_root=workspace_root,
            model_id=model_id,
            messages=snapshot,
        )
    )
    _pending_extraction_tasks[conversation_id] = task
    task.add_done_callback(partial(_discard_pending_task, conversation_id=conversation_id))
    logger.debug(
        "[memory] 已调度整轮结束抽取: conversation_id=%s snapshot=%s",
        conversation_id,
        len(snapshot),
    )


def schedule_memory_extraction_now(
    *,
    conversation_id: str,
    workspace_root: str,
    model_id: str | None,
    messages: Any,
) -> None:
    """上下文压缩丢弃消息前的立即抽取：后台任务持强引用防 GC。"""
    dropped = list(messages or [])
    if not _prepare_scheduled_extraction(conversation_id=conversation_id, messages=dropped):
        return
    task = asyncio.create_task(
        extract_and_save_memory(
            conversation_id=conversation_id,
            workspace_root=workspace_root,
            model_id=model_id,
            messages=dropped,
        )
    )
    _background_extraction_tasks.add(task)
    task.add_done_callback(_background_extraction_tasks.discard)


async def after_agent_turn_with_memory(
    log_done: Any,
    stream_adapter: Any,
    *,
    conversation_id: str,
    workspace_root: str,
    user_id: str | None,
    username: str | None,
    model_id: str | None,
) -> None:
    """TurnLifecycleHooks.after_turn 的记忆收尾钩子（仅整轮正常结束时触发）。

    先同步执行原有打点（``log_done``），再从流适配器的最终 state 取消息快照
    调度防抖抽取；快照不可用（state 缺失）时静默跳过，不影响收尾。
    """
    await log_done()
    try:
        values = getattr(getattr(stream_adapter, "state", None), "values", None) or {}
        messages = values.get("messages")
    except Exception:
        logger.debug("[memory] 读取最终消息快照失败，本轮跳过抽取: conversation_id=%s", conversation_id)
        return
    schedule_memory_extraction_after_turn(
        conversation_id=conversation_id,
        workspace_root=workspace_root,
        user_id=user_id,
        username=username,
        model_id=model_id,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# 注入渲染
# ---------------------------------------------------------------------------


def render_session_memory_block(workspace_root: str, *, conversation_id: str) -> str:
    """渲染注入 system prompt 的会话记忆内容段（逐条转义，不含段落标题）。

    开关关闭、文件不存在或无有效条目时返回空串，由调用方省略整段。
    """
    if not session_memory_enabled():
        return ""
    if not str(conversation_id or "").strip():
        return ""
    items = load_memory_items(workspace_root)
    budgeted = select_items_within_budget(
        items,
        max_tokens=session_memory_injection_max_tokens(),
    )
    if not budgeted:
        return ""
    return "\n".join(escape_memory_for_prompt(item.to_line()) for item in budgeted)
