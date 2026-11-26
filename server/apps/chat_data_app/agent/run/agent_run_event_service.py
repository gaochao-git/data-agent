"""后台 Run 事件服务，提供可重放的 SSE 事件读取。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

from apps.chat_data_app.daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao
from apps.chat_data_app.sse_protocol import SSE_EVENT_DONE, format_sse
from utils.database import get_session
from utils.logger import get_logger

from .agent_inbox_service import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_FAILED,
    RUN_STATUS_FINISHED,
    RUN_STATUS_WAITING_USER,
    AgentRunLeaseLostError,
    load_run_content,
)
from .redis_event_bus import RedisEventBus, RedisRunOwnerMismatchError, build_redis_event_bus

_EVENT_NAME_RE = re.compile(r"(?:^|\n)event:\s*([^\n]+)")
logger = get_logger(__name__)
QUESTION_DONE_WAIT_SECONDS = 10.0
QUESTION_DONE_DURABLE_POLL_SECONDS = 0.5
# done 发布失败后等待 MySQL 兜底终态的宽限期。后端先 finish_run（MySQL 终态落库）
# 再发布 done，兜底事件的状态永远准确；缩短只影响「done 只是慢而非失败」时直播
# 流的提前收口（未读尾部事件靠刷新历史回放补全）。公网/劣化链路下 10s 白等体感
# 过差，取 3s 平衡。
TERMINAL_PERSISTENCE_GRACE_SECONDS = 3.0
# Redis 读已判定不可用时，done 不可能再经流到达，兜底宽限可大幅缩短；
# 保留 0.5s 吸收「读失败是瞬时抖动、done 其实已在失败前发布」的窗口。
TERMINAL_UNAVAILABLE_GRACE_SECONDS = 0.5
# Run 终态集合；"succeeded" 是防御性保留（当前收口只写 finished，历史数据可能存在）。
TERMINAL_STATUSES = {
    RUN_STATUS_FINISHED,
    "succeeded",
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_WAITING_USER,
}


def _batch_reached_done(events: list) -> bool:
    """事件批次中是否包含 done 终止事件。"""
    return any(event.get("event_type") == "done" for event in events)


def format_terminal_done(status: str, next_run_id: str | None = None) -> str:
    """把 Run 权威状态编码为 SSE done；done 只在后端收口完成后产生。"""
    status = str(status or "failed")
    if status == "succeeded":
        status = "finished"
    if status not in {"finished", "failed", "cancelled", "interrupted", "waiting_user"}:
        status = "failed"
    data = {"status": status}
    next_run_id = str(next_run_id or "").strip()
    if data["status"] == "finished" and next_run_id:
        data["next_run_id"] = next_run_id
    return format_sse(SSE_EVENT_DONE, data)


class AgentRunEventService:
    """追加运行事件，并为 SSE 订阅者提供断线续传。"""

    def __init__(
        self,
        inbox_dao: AgentInboxDao = agent_inbox_dao,
        bus: RedisEventBus | None = None,
    ) -> None:
        self._inbox_dao = inbox_dao
        self._bus = bus or build_redis_event_bus()

    @property
    def bus(self) -> RedisEventBus:
        return self._bus

    async def claim_owner(self, run_id: str, owner_token: str) -> None:
        try:
            await self._bus.claim_run_owner(run_id, owner_token)
        except RedisRunOwnerMismatchError as exc:
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc

    async def release_owner(self, run_id: str, owner_token: str) -> None:
        await self._bus.release_run_owner(run_id, owner_token)

    async def renew_owner(self, run_id: str, owner_token: str) -> None:
        try:
            await self._bus.renew_run_owner(run_id, owner_token)
        except RedisRunOwnerMismatchError as exc:
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc

    async def append(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        event_id: str,
        payload: str,
        owner_token: str,
    ) -> str:
        """发布事件到 Redis 实时 Stream，返回其 stream_id。

        Run 事件不落 MySQL。Redis 丢失后序列从 1 重新开始也无碍：
        sequence_no 只在单个 Stream 生命周期内排序，没有跨段消费方。
        """
        event_type = self._event_type(payload)
        try:
            return await self._bus.publish(
                run_id=run_id,
                conversation_id=conversation_id,
                user_id=user_id,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                owner_token=owner_token,
            )
        except RedisRunOwnerMismatchError as exc:
            raise AgentRunLeaseLostError(f"Run 所有权已变更: {run_id}") from exc

    async def latest_event_id(self, run_id: str) -> str:
        """Get a resume cursor before a checkpoint run is resumed.

        A waiting-user Run has already emitted its terminal SSE event. The
        answer endpoint must start after that event, otherwise the old
        question card and ``done`` event are replayed as if they were part of
        the resumed answer. Redis holds the live tail; the inbox row's
        ``terminal_event`` (written after the done publish succeeded) is the
        durable fallback when the Redis stream has expired.
        """
        try:
            event_id = await self._bus.latest_run_event_id(run_id)
            if event_id:
                return event_id
        except Exception:
            logger.warning("读取 Redis Run 尾游标失败，回退 inbox 终止游标: run_id=%s", run_id, exc_info=True)

        terminal_event = await self._get_terminal_event(run_id)
        return str((terminal_event or {}).get("stream_id") or "") or "0-0"

    async def _wait_for_done_durable_check(self, run_id: str, now: float) -> tuple[bool, float]:
        """到达 durable 轮询阈值时查 inbox 终止记录，返回 (是否命中, 下一轮阈值)。"""
        try:
            terminal_event = await self._get_terminal_event(run_id)
            if terminal_event is not None:
                return True, now + QUESTION_DONE_DURABLE_POLL_SECONDS
        except Exception:
            logger.warning("等待问题 Run 终止事件时 inbox 查询失败: run_id=%s", run_id, exc_info=True)
        return False, now + QUESTION_DONE_DURABLE_POLL_SECONDS

    async def wait_for_done(self, run_id: str, timeout: float = QUESTION_DONE_WAIT_SECONDS) -> bool:
        """Wait briefly for the interrupt segment's terminal SSE event."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.0)
        next_durable_check = 0.0
        while True:
            try:
                event = await self._bus.latest_run_event(run_id)
            except Exception:
                logger.warning(
                    "等待问题 Run 终止事件时 Redis 不可用，检查 inbox 终止记录: run_id=%s", run_id, exc_info=True
                )
                event = None
            if event and event.get("event_type") == "done":
                return True
            now = loop.time()
            if now >= next_durable_check:
                found, next_durable_check = await self._wait_for_done_durable_check(run_id, now)
                if found:
                    return True
            if now >= deadline:
                return False
            await asyncio.sleep(0.05)

    async def _read_run_events(
        self, run_id: str, cursor: str, *, was_unavailable: bool
    ) -> tuple[list[dict[str, str]], bool, bool]:
        """读取 Redis Run 事件，返回 (events, stream_exists, redis_unavailable)。"""
        try:
            events = await self._bus.read_run(run_id, cursor)
            if was_unavailable:
                logger.info("Redis Run 事件读取已恢复: run_id=%s", run_id)
            return events, True, False
        except Exception:
            if not was_unavailable:
                logger.warning("Redis Run 事件读取失败，等待恢复: run_id=%s", run_id, exc_info=True)
            return [], False, True

    async def _recheck_stream_exists(self, run_id: str) -> bool:
        try:
            return bool(await self._bus.run_stream_exists(run_id))
        except Exception:
            return False

    @staticmethod
    def _terminal_fallback_event(run_id: str, cursor: str, snapshot: dict) -> dict:
        """inbox 终态先于 done 事件提交时的兜底 done 事件。"""
        terminal_status = "finished" if snapshot["status"] == "succeeded" else snapshot["status"]
        return {
            "stream_id": cursor,
            "run_id": run_id,
            "conversation_id": str(snapshot.get("conversation_id") or ""),
            "user_id": str(snapshot.get("user_id") or ""),
            "event_id": f"{run_id}:terminal:fallback",
            "event_type": "done",
            "sequence_no": "0",
            "payload": format_terminal_done(terminal_status, snapshot.get("next_run_id")),
        }

    async def _wait_between_polls(self, run_id: str, stream_exists: bool) -> None:
        """无新事件时的等待节奏：流不存在直接长等，否则复查后再等。"""
        if not stream_exists:
            await asyncio.sleep(0.5)
            return
        if not await self._recheck_stream_exists(run_id):
            await asyncio.sleep(0.5)

    async def _load_snapshot_with_retry(self, run_id: str) -> tuple[dict | None, bool]:
        """读取 Run 快照；失败时长等后重试（返回 loaded=False）。"""
        try:
            return await self._get_run_snapshot(run_id), True
        except Exception:
            logger.warning("读取 Run 状态失败，保持 SSE 订阅等待重试: run_id=%s", run_id, exc_info=True)
            await asyncio.sleep(0.5)
            return None, False

    async def stream(
        self,
        *,
        run_id: str,
        last_event_id: str = "0-0",
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        """订阅 Redis Stream；断开后结束订阅，不影响后台 Run。

        Redis Stream 是唯一的可重放事件源。流已过期（超过 TTL）时不补偿
        中间过程：订阅在 Run 终态后结束，刷新页面走会话历史回放最终结果。
        """
        cursor = self._normalize_cursor(last_event_id)
        redis_unavailable = False
        terminal_detected_at: float | None = None
        while True:
            if await self._client_disconnected(is_disconnected):
                return
            events, stream_exists, redis_unavailable = await self._read_run_events(
                run_id, cursor, was_unavailable=redis_unavailable
            )
            if events:
                terminal_detected_at = None
                done_reached = _batch_reached_done(events)
                for event in events:
                    cursor = event["stream_id"]
                    yield event
                if done_reached:
                    return
                continue
            await self._wait_between_polls(run_id, stream_exists)
            snapshot, loaded = await self._load_snapshot_with_retry(run_id)
            stop, terminal_detected_at, fallback = await self._terminal_grace_action(
                run_id,
                cursor,
                snapshot,
                loaded,
                terminal_detected_at,
                redis_unavailable=redis_unavailable,
            )
            for terminal_event in self._terminal_events(stop, fallback):
                yield terminal_event
            if stop:
                return

    @staticmethod
    def _normalize_cursor(last_event_id: str) -> str:
        return last_event_id or "0-0"

    @staticmethod
    async def _client_disconnected(is_disconnected: Callable[[], Awaitable[bool]] | None) -> bool:
        """与原内联写法一致：未传回调时不短路触发额外逻辑。"""
        if is_disconnected is None:
            return False
        return bool(await is_disconnected())

    @staticmethod
    def _terminal_events(stop: bool, fallback: dict[str, str] | None) -> Iterator[dict[str, str]]:
        """终态兜底事件迭代器：非停止或无兜底事件时不产出。"""
        if stop and fallback is not None:
            yield fallback

    async def _terminal_grace_action(
        self,
        run_id: str,
        cursor: str,
        snapshot: dict | None,
        loaded: bool,
        terminal_detected_at: float | None,
        *,
        redis_unavailable: bool = False,
    ) -> tuple[bool, float | None, dict | None]:
        """终态宽限判定，返回 (是否结束订阅, 新的 terminal_detected_at, 兜底事件)。

        inbox 终态先于 done 事件发布提交；宽限期内继续读流（内部 sleep 0.2），
        等待终止事件到达后再结束订阅。快照非终态时重置检测时间戳。
        Redis 读已不可用时 done 无法经流到达：单次短等（0.5s，吸收瞬时抖动）
        后直接兜底，宽限期内不再重读——黑洞链路上每次重读要先付 ~5s 连接
        超时，会把失败反馈拖回 15s+。状态取自 MySQL 权威终态，永远准确。
        """
        if not loaded:
            return False, terminal_detected_at, None
        if snapshot is None or snapshot["status"] in TERMINAL_STATUSES:
            now = asyncio.get_running_loop().time()
            if terminal_detected_at is None:
                terminal_detected_at = now
            if redis_unavailable:
                await asyncio.sleep(TERMINAL_UNAVAILABLE_GRACE_SECONDS)
                fallback = self._terminal_fallback_event(run_id, cursor, snapshot) if snapshot is not None else None
                return True, terminal_detected_at, fallback
            if now - terminal_detected_at >= TERMINAL_PERSISTENCE_GRACE_SECONDS:
                fallback = self._terminal_fallback_event(run_id, cursor, snapshot) if snapshot is not None else None
                return True, terminal_detected_at, fallback
            await asyncio.sleep(0.2)
            return False, terminal_detected_at, None
        return False, None, None

    async def _get_terminal_event(self, run_id: str) -> dict | None:
        """读取 inbox Run 行记录的终止事件游标（done 发布成功后写入）。"""
        async with get_session() as session:
            row = await self._inbox_dao.get_run_by_id(session, run_id)
        content = load_run_content(row.record_content if row else None)
        if not content:
            return None
        terminal_event = content.get("terminal_event")
        return terminal_event if isinstance(terminal_event, dict) else None

    async def _get_run_snapshot(self, run_id: str) -> dict | None:
        async with get_session() as session:
            row = await self._inbox_dao.get_run_by_id(session, run_id)
            if row is None:
                return None
            content = load_run_content(row.record_content)
            next_run_id = content.get("next_run_id") or None
            return {
                "status": row.record_status,
                "conversation_id": row.conversation_id,
                "user_id": row.user_id,
                "next_run_id": str(next_run_id).strip() if next_run_id else None,
            }

    @staticmethod
    def _event_type(payload: str) -> str:
        match = _EVENT_NAME_RE.search(payload or "")
        return match.group(1).strip() if match else "message"


agent_run_event_service = AgentRunEventService()
