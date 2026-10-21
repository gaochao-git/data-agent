"""跨进程 Agent 运行协调与运行中消息收件箱。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from apps.chat_data_app.daos.agent_inbox_dao import AgentInboxDao, agent_inbox_dao
from utils.database import get_session
from utils.logger import get_logger
from utils.models import ChatDataAgentInbox

logger = get_logger(__name__)

MESSAGE_LEASE_SECONDS = 60
RUN_LEASE_SECONDS = 120

# next-step 在下一次模型调用前注入；next-turn 每个新轮次按 FIFO 领取一条。
INBOX_QUEUE_TYPES = {"next-step", "next-turn"}
REDIS_STREAM_ID_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")


def load_run_content(content: str | None) -> dict[str, Any]:
    """解析 Run 行 content JSON；损坏或非对象一律降级为空字典。

    agent/run 域内唯一的 Run content 解析入口，事件服务与本服务共用。
    """
    try:
        value = json.loads(content or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


# Run 生命周期状态（chat_data_agent_inbox 表 record_type='run' 行的 record_status 列）。
# 同表的 message 行（record_type='message'）另有 pending/claimed/consumed/cancelled
# 状态体系，两者共用 status 列但取值域不同，勿混用常量。
RUN_STATUS_PENDING = "pending"
RUN_STATUS_ACTIVE = "active"
RUN_STATUS_CANCELLING = "cancelling"
RUN_STATUS_WAITING_USER = "waiting_user"
RUN_STATUS_FINISHED = "finished"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"


def is_valid_redis_stream_id(value: str | None) -> bool:
    """判断值是否为已发布事件的 Redis Stream ID。"""
    normalized = str(value or "").strip()
    return bool(REDIS_STREAM_ID_PATTERN.fullmatch(normalized)) and normalized != "0-0"


def normalize_history_checkpoint(content: dict[str, Any], run_id: str | None) -> dict[str, Any] | None:
    """只向外暴露归属于当前 Run 且格式有效的历史恢复水位。"""
    checkpoint = content.get("history_checkpoint")
    if not isinstance(checkpoint, dict) or str(checkpoint.get("run_id") or "") != str(run_id or ""):
        return None
    stream_id = str(checkpoint.get("stream_id") or "").strip()
    if not is_valid_redis_stream_id(stream_id):
        return None
    try:
        history_sequence_no = max(int(checkpoint.get("history_sequence_no") or 0), 0)
    except (TypeError, ValueError):
        history_sequence_no = 0
    return {
        "run_id": str(run_id),
        "turn_id": str(checkpoint.get("turn_id") or ""),
        "history_sequence_no": history_sequence_no,
        "stream_id": stream_id,
    }


class AgentRunConflictError(RuntimeError):
    pass


class AgentRunNotActiveError(RuntimeError):
    pass


class SteerMessageNotFoundError(RuntimeError):
    pass


class SteerMessageNotPendingError(RuntimeError):
    pass


class AgentQuestionNotFoundError(RuntimeError):
    pass


class AgentQuestionNotWaitingError(RuntimeError):
    pass


class AgentRunLeaseLostError(RuntimeError):
    """当前执行者已失去 Run 所有权，禁止继续写入运行状态。"""


@dataclass(frozen=True)
class ClaimedAgentMessages:
    run_id: str
    messages: list[HumanMessage]


@dataclass(frozen=True)
class AgentRunCancellationResult:
    """取消请求的事务结果；terminal_owner_token 非空表示 Run 已在事务内收口。"""

    cancelled: bool
    status: str | None = None
    terminal_owner_token: str | None = None


@dataclass(frozen=True)
class AgentRunCompletionResult:
    """执行结束时由 MySQL 事务定稿的权威终态。"""

    status: str
    next_run_id: str | None


@dataclass(frozen=True)
class AgentRunReapResult:
    """后台回收过期 Run 后，发布终态事件所需的信息。"""

    run_id: str
    conversation_id: str
    user_id: str
    status: str
    owner_token: str


class AgentInboxService:
    def __init__(self, dao: AgentInboxDao = agent_inbox_dao) -> None:
        self._dao = dao

    async def start_run(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        runtime_context: dict[str, Any] | None = None,
        *,
        adopt_pending_messages: bool = True,
        selected_message_id: str | None = None,
        stream_started_wall_ms: int | None = None,
    ) -> tuple[str, list[HumanMessage]]:
        """开启新运行，返回 (run_id, 遗留排队消息)。

        默认接管上一次运行遗留的排队消息（未消费的立即插入/下一轮）作为
        本轮首批输入，并在本事务内领取，避免被中间件重复注入。

        ``stream_started_wall_ms``：请求到达墙钟（毫秒），由 completion 入口
        盖章写入 Run 行 content，供流启动后作为 harness 首 token 与任务总
        耗时的起点（覆盖落库/调度/prepare 的盲区）；不传则由流工厂按派发
        时刻兜底盖章（定时任务/continuation 现状）。

        停止后发消息语义下有两种例外：
        - adopt_pending_messages=False（preserve）：新输入单独成 Run，
          旧队列留在 pending，由当前 Run 结束后的 continuation 按 FIFO 接走；
        - selected_message_id：单条队列执行，只迁移指定消息，其余继续排队。
        """
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            active_run = await self._dao.get_active_run(session, conversation_id, user_id)
            active_run = await self._reap_expired_run(
                session,
                active_run,
                now,
                user_id,
                operator_name,
            )
            if active_run:
                raise AgentRunConflictError("当前会话已有运行中的任务")
            run_id = uuid4().hex
            run_content: dict[str, Any] = {"runtime_context": runtime_context or {}, "turn_id": run_id}
            if stream_started_wall_ms:
                run_content["stream_started_wall_ms"] = int(stream_started_wall_ms)
            session.add(
                ChatDataAgentInbox(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    run_id=run_id,
                    message_id=run_id,
                    record_type="run",
                    record_content=json.dumps(run_content, ensure_ascii=False),
                    record_status=RUN_STATUS_PENDING,
                    lease_expire_time=None,
                    delete_flag=0,
                    create_time=now,
                    update_time=now,
                    create_by=user_id,
                    create_by_name=operator_name,
                    update_by=user_id,
                    update_by_name=operator_name,
                )
            )
            if selected_message_id:
                selected = await self._dao.get_message_by_id(
                    session,
                    conversation_id,
                    user_id,
                    selected_message_id,
                )
                if selected is None:
                    raise SteerMessageNotFoundError("队列消息不存在")
                if selected.record_status != "pending":
                    raise SteerMessageNotPendingError("队列消息已进入处理流程")
                selected.run_id = run_id
                selected.update_time = now
            elif adopt_pending_messages:
                await self._dao.adopt_pending_messages(session, conversation_id, user_id, run_id, now)
            rows = await self._claim_step_and_turn(session, run_id, now)
            return run_id, [self._to_human_message(row) for row in rows]

    async def prepare_pending_run(self, run_id: str) -> dict[str, Any] | None:
        """领取 pending Run 并返回重建上下文。

        只认领 pending：租约过期的 active Run 视为执行者已死亡，任务停止，
        由后台回收器或用户交互经 _reap_expired_run 收口，不自动接管续跑。
        """
        async with get_session() as session:
            now = await self._dao.now(session)
            run_snapshot = await self._dao.get_run_by_id(session, run_id)
            if run_snapshot is None:
                return None
            thread = await self._dao.lock_thread(session, run_snapshot.conversation_id, run_snapshot.user_id)
            if thread is None:
                return None
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None:
                return None
            if run.record_status != RUN_STATUS_PENDING:
                return None
            active = await self._dao.get_active_run(session, run.conversation_id, run.user_id)
            if active is not None and active.run_id != run_id:
                return None
            run.record_status = RUN_STATUS_ACTIVE
            content = self._load_run_content(run.record_content)
            owner_generation = int(content.get("owner_generation") or 0) + 1
            content["owner_generation"] = owner_generation
            # 新执行段开始后，上一段的终止游标即失效；保留会让下一次
            # 问答恢复把旧段的 done 误判为当前段已收口。
            content.pop("terminal_event", None)
            content.pop("next_run_id", None)
            run.record_content = json.dumps(content, ensure_ascii=False)
            owner_token = f"{owner_generation}:{uuid4().hex}"
            run.owner_token = owner_token
            run.lease_expire_time = now + timedelta(seconds=RUN_LEASE_SECONDS)
            run.update_time = now
            rows = await self._dao.list_claimed_messages(session, run_id)
            resume_question = content.get("resuming_question")
            pending_answer = content.get("pending_answer")
            return {
                "run_id": run_id,
                "owner_token": owner_token,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "operator_name": content.get("runtime_context", {}).get("operator_name") or run.user_id,
                "runtime_context": content.get("runtime_context") or {},
                "turn_id": str(content.get("turn_id") or run_id),
                "stream_started_wall_ms": content.get("stream_started_wall_ms"),
                "messages": [self._to_human_message(row) for row in rows],
                "resume_answer": pending_answer if isinstance(pending_answer, dict) else None,
                "resume_turn_id": (
                    str(resume_question.get("turn_id") or "") if isinstance(resume_question, dict) else None
                ),
            }

    async def reap_expired_runs(self, limit: int = 20) -> list[AgentRunReapResult]:
        """在后台收口租约过期的 Run，不自动重放执行段。"""
        results: list[AgentRunReapResult] = []
        async with get_session() as session:
            now = await self._dao.now(session)
            runs = await self._dao.list_expired_runs(session, now, max(limit, 1))
            run_ids = [run.run_id for run in runs]
        for run_id in run_ids:
            # 扫描不持有锁；每个候选 Run 单独开事务并重新检查状态。
            # 多个 Worker 可能同时看到同一候选，最终只有拿到 Run 行锁且
            # 仍处于过期状态的 Worker 可以收口，其他 Worker 会安全跳过。
            try:
                async with get_session() as session:
                    now = await self._dao.now(session)
                    run = await self._dao.get_run_by_id_for_update(session, run_id)
                    if run is None:
                        continue
                    result = await self._reap_expired_run_state(
                        session,
                        run,
                        now,
                        run.user_id,
                        run.create_by_name,
                    )
                    if result is not None:
                        results.append(result)
            except Exception:
                # 单个候选收口失败（DB 抖动/锁冲突等）只记日志，下一个
                # 调度 tick 会重新扫到它；不能中断本轮其余候选的收口。
                logger.exception("回收过期 Run 失败，等待下一周期重试: run_id=%s", run_id)
        return results

    async def heartbeat_run(self, run_id: str, owner_token: str | None = None) -> str:
        """只用一条带 owner fencing 的 UPDATE 续 Run 租约。

        claimed 消息的租约由领取事务设置，结束/接管时由 Run 锁保护的收口流程
        处理；高频心跳不再扫描消息行，避免与收口流程形成锁竞争。
        """
        async with get_session() as session:
            now = await self._dao.now(session)
            renewed = await self._dao.renew_run_lease(
                session,
                run_id,
                owner_token,
                now + timedelta(seconds=RUN_LEASE_SECONDS),
                now,
            )
            if renewed:
                return RUN_STATUS_ACTIVE

            # 0 行既可能是取消/完成，也可能是 owner 已被接管。只在这个
            # 竞争路径做一次普通读取，正常心跳始终只有一条 UPDATE。
            run = await self._dao.get_run_by_id(session, run_id)
            if run is None or run.record_status not in {RUN_STATUS_ACTIVE, RUN_STATUS_CANCELLING}:
                return "inactive"
            if run.record_status == RUN_STATUS_CANCELLING:
                return RUN_STATUS_CANCELLING
            self._assert_owner(run, owner_token)
            return RUN_STATUS_ACTIVE

    async def assert_run_owner(self, run_id: str, owner_token: str | None = None) -> None:
        """Fence a new tool side effect against a superseded Worker."""
        async with get_session() as session:
            run = await self._dao.get_run_by_id(session, run_id)
            if run is None or run.record_status != RUN_STATUS_ACTIVE:
                raise AgentRunLeaseLostError("当前运行已结束或已暂停")
            self._assert_owner(run, owner_token)

    async def mark_waiting_for_user(
        self,
        run_id: str,
        question: dict[str, Any],
        user_id: str,
        operator_name: str,
        owner_token: str | None = None,
    ) -> None:
        async with get_session() as session:
            now = await self._dao.now(session)
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.record_status != RUN_STATUS_ACTIVE:
                raise AgentRunNotActiveError("当前运行已结束")
            self._assert_owner(run, owner_token)
            content = self._load_run_content(run.record_content)
            content["pending_question"] = question
            content.pop("resuming_question", None)
            content.pop("pending_answer", None)
            content.pop("next_run_id", None)
            run.record_content = json.dumps(content, ensure_ascii=False)
            run.record_status = RUN_STATUS_WAITING_USER
            run.owner_token = None
            run.lease_expire_time = None
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name

    async def has_active_run(self, conversation_id: str, user_id: str) -> bool:
        """当前会话是否还有进行中的 Run（含排队/运行/取消中/等待回答）。"""
        async with get_session() as session:
            run = await self._dao.get_active_run(session, conversation_id, user_id)
        return run is not None

    async def get_pending_question(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            run = await self._reap_expired_run(session, run, now, user_id, user_id)
            if run is None or run.record_status != RUN_STATUS_WAITING_USER:
                return None
            return self._question_response(run)

    async def claim_question_answer(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        question_id: str,
        answer: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentQuestionNotFoundError("待回答问题不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None:
                raise AgentQuestionNotFoundError("待回答问题不存在")
            if run.record_status != RUN_STATUS_WAITING_USER:
                raise AgentQuestionNotWaitingError("问题已回答或运行已恢复")
            content = self._load_run_content(run.record_content)
            question = content.get("pending_question") or {}
            if str(question.get("question_id") or "") != question_id:
                raise AgentQuestionNotFoundError("待回答问题不存在")
            self._validate_question_answer(question, answer)
            # Keep the question separately while the checkpoint is being
            # resumed. It must not be exposed as pending after the answer has
            # been claimed, otherwise a later stream failure can resurrect an
            # interrupt that LangGraph has already consumed.
            content["resuming_question"] = content.pop("pending_question")
            content["pending_answer"] = answer
            content["resume_started"] = False
            # 恢复段首 token/总耗时起点 = 回答到达时刻，覆盖原 Run 行的旧盖章。
            content["stream_started_wall_ms"] = int(time.time() * 1000)
            run.record_content = json.dumps(content, ensure_ascii=False)
            # The answer is durable before dispatch. Runtime will atomically
            # claim the pending run, including after a worker restart.
            run.record_status = RUN_STATUS_PENDING
            run.lease_expire_time = None
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name
            return run.run_id, content.get("runtime_context") or {}

    async def record_terminal_event(self, run_id: str, stream_id: str) -> None:
        """终止 SSE 事件发布成功后，把 Redis 游标持久化到 Run 行。

        此时 Run 已进入终态（waiting_user/finished 等），owner_token 可能已被
        mark_waiting_for_user 清空，因此不做 owner 断言——终止事件本身已被
        Redis owner 脚本围栏，同一 Run 只有一个段能发布成功。该记录供问答
        恢复在 Redis Stream 失效时做 done 检测与游标兜底。
        """
        async with get_session() as session:
            now = await self._dao.now(session)
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None:
                return
            content = self._load_run_content(run.record_content)
            content["terminal_event"] = {"stream_id": stream_id, "event_type": "done"}
            run.record_content = json.dumps(content, ensure_ascii=False)
            run.update_time = now

    async def add_executed_durations(self, run_id: str, durations: dict[str, int]) -> None:
        """累加本段执行耗时到 Run 行，供打断恢复后接力累计任务总用时。

        interrupt 段结束（SSE 流退出）时调用，durations 形如
        {"total_duration_ms": x}；此时 Run 可能已处于
        waiting_user 且 owner_token 已被 mark_waiting_for_user 清空，因此不筛
        状态、不做 owner 断言。耗时仅服务展示统计，写失败不影响流程。
        行不存在时静默忽略。
        """
        async with get_session() as session:
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None:
                return
            content = self._load_run_content(run.record_content)
            stored = content.get("executed_durations")
            merged = dict(stored) if isinstance(stored, dict) else {}
            for key, value in durations.items():
                merged[key] = int(merged.get(key) or 0) + max(int(value or 0), 0)
            content["executed_durations"] = merged
            run.record_content = json.dumps(content, ensure_ascii=False)
            run.update_time = await self._dao.now(session)

    async def get_executed_durations(self, run_id: str) -> dict[str, int]:
        """读取 Run 行累计执行耗时；行不存在或字段缺失返回空 dict。"""
        async with get_session() as session:
            run = await self._dao.get_run_by_id(session, run_id)
            if run is None:
                return {}
            content = self._load_run_content(run.record_content)
            stored = content.get("executed_durations")
            if not isinstance(stored, dict):
                return {}
            return {str(key): int(value or 0) for key, value in stored.items()}

    async def mark_question_resume_started(self, run_id: str, owner_token: str | None = None) -> None:
        """Record that the checkpoint has advanced past the claimed answer."""
        async with get_session() as session:
            now = await self._dao.now(session)
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.record_status not in {RUN_STATUS_ACTIVE, RUN_STATUS_PENDING}:
                return
            self._assert_owner(run, owner_token)
            content = self._load_run_content(run.record_content)
            if not isinstance(content.get("resuming_question"), dict):
                return
            content["resume_started"] = True
            run.record_content = json.dumps(content, ensure_ascii=False)
            run.update_time = now

    async def restore_question_answer(
        self,
        run_id: str,
        user_id: str,
        operator_name: str,
        owner_token: str | None = None,
    ) -> None:
        """Roll back a claim when the resume stream could not be created."""
        async with get_session() as session:
            now = await self._dao.now(session)
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.record_status not in {RUN_STATUS_ACTIVE, RUN_STATUS_PENDING}:
                return
            self._assert_owner(run, owner_token)
            content = self._load_run_content(run.record_content)
            question = content.pop("resuming_question", None)
            if not isinstance(question, dict) or "pending_answer" not in content:
                return
            content.pop("pending_answer", None)
            content.pop("resume_started", None)
            content["pending_question"] = question
            run.record_content = json.dumps(content, ensure_ascii=False)
            run.record_status = RUN_STATUS_WAITING_USER
            run.lease_expire_time = None
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name

    async def enqueue_message(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        content: str,
        queue_type: str = "next-turn",
        meta: dict[str, Any] | None = None,
    ) -> str:
        if queue_type not in INBOX_QUEUE_TYPES:
            raise ValueError(f"非法队列类型: {queue_type}")
        normalized_content = (content or "").strip()
        if not normalized_content:
            raise ValueError("消息内容不能为空")
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            run = await self._reap_expired_run(session, run, now, user_id, operator_name)
            if run is None or run.record_status not in {RUN_STATUS_PENDING, RUN_STATUS_ACTIVE}:
                raise AgentRunNotActiveError("当前会话没有运行中的任务")
            message_id = uuid4().hex
            session.add(
                ChatDataAgentInbox(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    run_id=run.run_id,
                    message_id=message_id,
                    record_type="message",
                    queue_type=queue_type,
                    record_content=normalized_content,
                    # 随行元数据（@ 引用 / 附件 file_id）以 JSON 引用形式落列，
                    # 消费时才由 agent_stream 采纳点校验解析——存引用、晚解析。
                    record_meta=json.dumps(meta, ensure_ascii=False) if meta else None,
                    record_status="pending",
                    delete_flag=0,
                    create_time=now,
                    update_time=now,
                    create_by=user_id,
                    create_by_name=operator_name,
                    update_by=user_id,
                    update_by_name=operator_name,
                )
            )
            return message_id

    async def claim_pending_messages(
        self,
        run_id: str,
        *,
        queue_type: str | None = "next-step",
        owner_token: str | None = None,
    ) -> ClaimedAgentMessages:
        async with get_session() as session:
            now = await self._dao.now(session)
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.record_status != RUN_STATUS_ACTIVE:
                return ClaimedAgentMessages(run_id, [])
            self._assert_owner(run, owner_token)
            rows = await self._dao.claim_messages(
                session,
                run_id,
                now,
                now + timedelta(seconds=MESSAGE_LEASE_SECONDS),
                queue_type=queue_type,
            )
            return ClaimedAgentMessages(run_id, [self._to_human_message(row) for row in rows])

    async def mark_consumed(self, run_id: str, message_ids: list[str], owner_token: str | None = None) -> None:
        if not message_ids:
            return
        async with get_session() as session:
            now = await self._dao.now(session)
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None:
                return
            self._assert_owner(run, owner_token)
            rows = await self._dao.get_claimed_messages(session, run_id, message_ids)
            for row in rows:
                row.record_status = "consumed"
                row.consumed_time = now
                row.lease_expire_time = None
                row.update_time = now

    async def finish_run(
        self,
        run_id: str,
        status: str,
        user_id: str,
        operator_name: str,
        owner_token: str | None = None,
    ) -> str | None:
        async with get_session() as session:
            now = await self._dao.now(session)
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run and run.record_status == RUN_STATUS_WAITING_USER:
                # 问题已落库、Run 已处于合法等待态（异常/取消发生在
                # mark_waiting_for_user 之后）：不重复收口、不回退队列，
                # 回报权威状态让调用方发布与之一致的 done。
                return RUN_STATUS_WAITING_USER
            if run and run.record_status in {RUN_STATUS_PENDING, RUN_STATUS_ACTIVE, RUN_STATUS_CANCELLING}:
                self._assert_owner(run, owner_token)
                if status in {RUN_STATUS_FAILED, RUN_STATUS_CANCELLED}:
                    await self._dao.requeue_messages(session, run_id, now)
                content = self._load_run_content(run.record_content)
                self._restore_unstarted_question(content)
                content.pop("next_run_id", None)
                # A claimed answer is already in the resume lifecycle. If the
                # resumed stream fails, do not expose the old question again;
                # the checkpoint may already have consumed its interrupt.
                run.record_status = RUN_STATUS_WAITING_USER if content.get("pending_question") else status
                run.record_content = json.dumps(content, ensure_ascii=False)
                run.lease_expire_time = None
                run.owner_token = None
                run.update_time = now
                run.update_by = user_id
                run.update_by_name = operator_name
                return run.record_status
            return None

    async def complete_run(
        self,
        run_id: str,
        user_id: str,
        operator_name: str,
        owner_token: str | None = None,
    ) -> AgentRunCompletionResult:
        """收口自然结束的 Run，并在同一事务内决定是否调度下一轮。"""
        async with get_session() as session:
            now = await self._dao.now(session)
            run_snapshot = await self._dao.get_run_by_id(session, run_id)
            if run_snapshot is None:
                raise AgentRunLeaseLostError("当前运行不存在")
            thread = await self._dao.lock_thread(session, run_snapshot.conversation_id, run_snapshot.user_id)
            if thread is None:
                raise AgentRunLeaseLostError("当前会话不存在")
            run = await self._dao.get_run_by_id_for_update(session, run_id)
            if run is None or run.record_status not in {RUN_STATUS_ACTIVE, RUN_STATUS_CANCELLING}:
                raise AgentRunLeaseLostError("当前运行已结束或已由其他 Worker 接管")
            self._assert_owner(run, owner_token)
            content = self._load_run_content(run.record_content)
            if run.record_status == RUN_STATUS_CANCELLING:
                await self._dao.requeue_messages(session, run_id, now)
                self._restore_unstarted_question(content)
                terminal_status = RUN_STATUS_WAITING_USER if content.get("pending_question") else RUN_STATUS_CANCELLED
                content.pop("next_run_id", None)
                run.record_content = json.dumps(content, ensure_ascii=False)
                run.owner_token = None
                run.record_status = terminal_status
                run.lease_expire_time = None
                run.update_time = now
                run.update_by = user_id
                run.update_by_name = operator_name
                return AgentRunCompletionResult(
                    status=terminal_status,
                    next_run_id=None,
                )

            for key in ("pending_question", "resuming_question", "pending_answer", "resume_started"):
                content.pop(key, None)
            run.owner_token = None
            run.record_status = RUN_STATUS_FINISHED
            run.lease_expire_time = None
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name

            next_run_id = None
            if await self._dao.has_pending_messages(session, run.conversation_id, run.user_id):
                next_run_id = uuid4().hex
                runtime_context = content.get("runtime_context")
                if not isinstance(runtime_context, dict):
                    runtime_context = {}
                # The original user query was already persisted in this Run. The
                # next Run must execute only its inbox messages, otherwise a
                # refresh/queued turn would submit the original query twice.
                runtime_context = {**runtime_context, "query": ""}
                await self._dao.adopt_pending_messages(
                    session,
                    run.conversation_id,
                    run.user_id,
                    next_run_id,
                    now,
                )
                claimed_rows = await self._claim_step_and_turn(session, next_run_id, now)
                next_turn_id = str(claimed_rows[-1].message_id) if claimed_rows else next_run_id
                session.add(
                    ChatDataAgentInbox(
                        conversation_id=run.conversation_id,
                        user_id=run.user_id,
                        run_id=next_run_id,
                        message_id=next_run_id,
                        record_type="run",
                        record_content=json.dumps(
                            {
                                "runtime_context": runtime_context,
                                "run_kind": "continuation",
                                "turn_id": next_turn_id,
                            },
                            ensure_ascii=False,
                        ),
                        record_status=RUN_STATUS_PENDING,
                        lease_expire_time=None,
                        delete_flag=0,
                        create_time=now,
                        update_time=now,
                        create_by=user_id,
                        create_by_name=operator_name,
                        update_by=user_id,
                        update_by_name=operator_name,
                    )
                )
            if next_run_id:
                content["next_run_id"] = next_run_id
            else:
                content.pop("next_run_id", None)
            run.record_content = json.dumps(content, ensure_ascii=False)
            return AgentRunCompletionResult(
                status=RUN_STATUS_FINISHED,
                next_run_id=next_run_id,
            )

    async def cancel_active_run(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        run_id: str,
    ) -> AgentRunCancellationResult:
        """显式停止当前执行；保留 pending，并把 claimed 恢复为 pending 供下一轮接管。"""
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            run = await self._dao.get_active_run(session, conversation_id, user_id)
            if run is None or run.run_id != run_id or run.record_status not in {RUN_STATUS_PENDING, RUN_STATUS_ACTIVE}:
                return AgentRunCancellationResult(cancelled=False)
            if run.record_status == RUN_STATUS_PENDING:
                terminal_owner_token = await self._cancel_run(session, run, now, user_id, operator_name)
                return AgentRunCancellationResult(
                    cancelled=True,
                    status=run.record_status,
                    terminal_owner_token=terminal_owner_token,
                )
            run.record_status = RUN_STATUS_CANCELLING
            run.lease_expire_time = now + timedelta(seconds=5)
            run.update_time = now
            run.update_by = user_id
            run.update_by_name = operator_name
            return AgentRunCancellationResult(cancelled=True, status=RUN_STATUS_CANCELLING)

    async def update_pending_message(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        message_id: str,
        *,
        content: str | None = None,
        queue_type: str | None = None,
    ) -> dict[str, str]:
        """编辑仍在等待注入的消息；Run 结束后遗留的 pending 消息也可修改。"""
        if content is not None and not content.strip():
            raise ValueError("消息内容不能为空")
        if queue_type is not None and queue_type not in INBOX_QUEUE_TYPES:
            raise ValueError(f"非法队列类型: {queue_type}")
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            message = await self._dao.get_message_by_id(
                session,
                conversation_id,
                user_id,
                message_id,
            )
            if message is None:
                raise SteerMessageNotFoundError("队列消息不存在")
            if message.record_status != "pending":
                raise SteerMessageNotPendingError("消息已进入注入流程，无法修改")
            if content is not None:
                message.record_content = content.strip()
            if queue_type is not None:
                message.queue_type = queue_type
            message.update_time = now
            message.update_by = user_id
            message.update_by_name = operator_name
            return {
                "message_id": message.message_id,
                "content": message.record_content,
                "queue_type": message.queue_type,
            }

    async def cancel_pending_message(
        self,
        conversation_id: str,
        user_id: str,
        operator_name: str,
        message_id: str,
    ) -> None:
        """删除仍在等待注入的消息；Run 结束后遗留的 pending 消息也可删除。"""
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            message = await self._dao.get_message_by_id(
                session,
                conversation_id,
                user_id,
                message_id,
            )
            if message is None:
                raise SteerMessageNotFoundError("队列消息不存在")
            if message.record_status != "pending":
                raise SteerMessageNotPendingError("消息已进入注入流程，无法删除")
            message.record_status = "cancelled"
            message.delete_flag = 1
            message.update_time = now
            message.update_by = user_id
            message.update_by_name = operator_name

    async def list_message_states(
        self,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """读取当前 active run 的完整队列状态，供刷新和断线重连恢复。"""
        async with get_session() as session:
            now = await self._dao.now(session)
            thread = await self._dao.lock_thread(session, conversation_id, user_id)
            if thread is None:
                raise AgentRunNotActiveError("会话不存在")
            active_run = await self._dao.get_active_run(session, conversation_id, user_id)
            active_run = await self._reap_expired_run(
                session,
                active_run,
                now,
                user_id,
                user_id,
            )
            latest_run = active_run or await self._dao.get_latest_run(session, conversation_id, user_id)
            rows = await self._dao.list_run_messages(session, latest_run.run_id) if latest_run else []
            latest_content = self._load_run_content(latest_run.record_content) if latest_run else {}
            return {
                "has_active_run": active_run is not None,
                "run_id": latest_run.run_id if latest_run else None,
                "run_status": latest_run.record_status if latest_run else None,
                "next_run_id": self._next_run_id(latest_content),
                "turn_id": str(latest_content.get("turn_id") or "") or None,
                "is_continuation": latest_content.get("run_kind") == "continuation",
                "history_checkpoint": self._history_checkpoint(
                    latest_content, latest_run.run_id if latest_run else None
                ),
                "messages": [
                    {
                        "message_id": row.message_id,
                        "content": row.record_content,
                        "queue_type": row.queue_type,
                        "status": row.record_status,
                        # 随行引用/附件元数据：前端刷新恢复队列项快照用
                        "meta": _load_inbox_meta(row.record_meta) or None,
                        # ISO 格式：前端 new Date() 跨浏览器（含 Safari）均可解析
                        "create_time": row.create_time.isoformat(),
                    }
                    for row in rows
                ],
            }

    async def get_run_snapshot(
        self,
        conversation_id: str,
        user_id: str,
        run_id: str,
    ) -> dict[str, Any] | None:
        """读取指定 Run，并校验它属于当前用户和会话。"""
        async with get_session() as session:
            run = await self._dao.get_run_by_id(session, run_id)
            if run is None or run.conversation_id != conversation_id or run.user_id != user_id:
                return None
            return {
                "run_id": run.run_id,
                "conversation_id": run.conversation_id,
                "user_id": run.user_id,
                "status": run.record_status,
                "next_run_id": self._next_run_id(self._load_run_content(run.record_content)),
                "history_checkpoint": self._history_checkpoint(
                    self._load_run_content(run.record_content),
                    run.run_id,
                ),
            }

    async def _cancel_run(
        self,
        session: AsyncSession,
        run: ChatDataAgentInbox,
        now: datetime,
        user_id: str,
        operator_name: str,
    ) -> str:
        """在持有 Run 行锁时完成取消收口。"""
        await self._dao.requeue_messages(session, run.run_id, now)
        content = self._load_run_content(run.record_content)
        self._restore_unstarted_question(content)
        owner_generation = int(content.get("owner_generation") or 0) + 1
        content["owner_generation"] = owner_generation
        terminal_owner_token = f"{owner_generation}:{uuid4().hex}"
        terminal_status = RUN_STATUS_WAITING_USER if content.get("pending_question") else RUN_STATUS_CANCELLED
        content.pop("next_run_id", None)
        run.record_content = json.dumps(content, ensure_ascii=False)
        run.record_status = terminal_status
        run.owner_token = None
        run.lease_expire_time = None
        run.update_time = now
        run.update_by = user_id
        run.update_by_name = operator_name
        return terminal_owner_token

    async def _reap_expired_run(
        self,
        session: AsyncSession,
        run: ChatDataAgentInbox | None,
        now: datetime,
        user_id: str,
        operator_name: str,
    ) -> ChatDataAgentInbox | None:
        """收口租约过期的 Run：执行者已死亡，任务停止，不自动续跑。

        排队消息退回 pending 由下一个新 Run 接管；有未开始的待答问题则还原为
        waiting_user，否则 active 收口为 failed、cancelling 收口为 cancelled。
        """
        if run is None or run.record_status not in {RUN_STATUS_ACTIVE, RUN_STATUS_CANCELLING}:
            return run
        if run.lease_expire_time is None or run.lease_expire_time > now:
            return run
        result = await self._reap_expired_run_state(session, run, now, user_id, operator_name)
        return run if result and result.status == RUN_STATUS_WAITING_USER else None

    async def _reap_expired_run_state(
        self,
        session: AsyncSession,
        run: ChatDataAgentInbox,
        now: datetime,
        user_id: str,
        operator_name: str,
    ) -> AgentRunReapResult | None:
        """收口单个过期 Run，并生成新的终态发布 fencing token。"""
        if run.record_status not in {RUN_STATUS_ACTIVE, RUN_STATUS_CANCELLING}:
            return None
        if run.lease_expire_time is None or run.lease_expire_time > now:
            return None
        dead_status = RUN_STATUS_FAILED if run.record_status == RUN_STATUS_ACTIVE else RUN_STATUS_CANCELLED
        await self._dao.requeue_messages(session, run.run_id, now)
        content = self._load_run_content(run.record_content)
        self._restore_unstarted_question(content)
        terminal_status = RUN_STATUS_WAITING_USER if content.get("pending_question") else dead_status
        owner_generation = int(content.get("owner_generation") or 0) + 1
        content["owner_generation"] = owner_generation
        content.pop("next_run_id", None)
        run.record_content = json.dumps(content, ensure_ascii=False)
        run.record_status = terminal_status
        run.owner_token = None
        run.lease_expire_time = None
        run.update_time = now
        run.update_by = user_id
        run.update_by_name = operator_name
        return AgentRunReapResult(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            user_id=run.user_id,
            status=terminal_status,
            owner_token=f"{owner_generation}:{uuid4().hex}",
        )

    _load_run_content = staticmethod(load_run_content)

    @staticmethod
    def _next_run_id(content: dict[str, Any]) -> str | None:
        next_run_id = content.get("next_run_id")
        return str(next_run_id).strip() if next_run_id else None

    @staticmethod
    def _assert_owner(run: ChatDataAgentInbox, owner_token: str | None) -> None:
        if not owner_token or not run.owner_token or owner_token != run.owner_token:
            raise AgentRunLeaseLostError("当前运行已由其他 Worker 接管")

    def _question_response(self, run: ChatDataAgentInbox) -> dict[str, Any] | None:
        question = self._load_run_content(run.record_content).get("pending_question")
        if not isinstance(question, dict):
            return None
        return {"run_id": run.run_id, **question}

    @staticmethod
    def _history_checkpoint(content: dict[str, Any], run_id: str | None) -> dict[str, Any] | None:
        return normalize_history_checkpoint(content, run_id)

    @staticmethod
    def _restore_unstarted_question(content: dict[str, Any]) -> None:
        question = content.get("resuming_question")
        if not isinstance(question, dict) or content.get("resume_started") is True:
            return
        content["pending_question"] = content.pop("resuming_question")
        content.pop("pending_answer", None)
        content.pop("resume_started", None)

    @staticmethod
    def _validate_question_answer(question: dict[str, Any], answer: dict[str, Any]) -> None:
        # "cancelled" 是回答协议的 key 名，与 Run 状态值无关，保持字面量
        if answer.get("cancelled") is True:
            return
        questions = question.get("questions") or []
        submitted_by_id = AgentInboxService._submitted_answer_by_id(answer, questions)
        for item in questions:
            current = submitted_by_id[str(item.get("id") or "")]
            AgentInboxService._validate_answer_item(current, item)

    @staticmethod
    def _submitted_answer_by_id(answer: dict[str, Any], questions: list[dict[str, Any]]) -> dict[str, Any]:
        """校验 answers 顶层结构，返回按问题 ID 索引的回答映射。"""
        submitted = answer.get("answers") if isinstance(answer, dict) else None
        if not isinstance(submitted, list):
            raise ValueError("answers 必须是数组")
        submitted_by_id = {str(item.get("id") or ""): item for item in submitted if isinstance(item, dict)}
        if len(submitted_by_id) != len(submitted):
            raise ValueError("回答的问题 ID 不能重复且必须是对象")
        if set(submitted_by_id) != {str(item.get("id") or "") for item in questions}:
            raise ValueError("回答的问题 ID 与待回答问题不一致")
        return submitted_by_id

    @staticmethod
    def _validate_answer_item(current: dict[str, Any], item: dict[str, Any]) -> None:
        if current.get("skipped") is True:
            return
        selected = current.get("selected") or []
        custom = str(current.get("custom") or "").strip()
        AgentInboxService._validate_selected_options(selected)
        allowed = {str(option.get("label") or "") for option in item.get("options") or []}
        if any(value not in allowed for value in selected):
            raise ValueError("回答包含无效选项")
        if not item.get("multi_select") and len(selected) > 1:
            raise ValueError("单选问题只能选择一个选项")
        if not selected and not custom:
            raise ValueError("每个问题都必须选择选项或填写自定义回答")

    @staticmethod
    def _validate_selected_options(selected: Any) -> None:
        if not isinstance(selected, list) or not all(isinstance(value, str) for value in selected):
            raise ValueError("selected 必须是字符串数组")

    async def _claim_step_and_turn(self, session: AsyncSession, run_id: str, now: datetime) -> list[ChatDataAgentInbox]:
        """领取一个模型轮次的输入：全部 next-step，加一条 FIFO next-turn。"""
        lease_expire_time = now + timedelta(seconds=MESSAGE_LEASE_SECONDS)
        step_rows = await self._dao.claim_messages(
            session,
            run_id,
            now,
            lease_expire_time,
            queue_type="next-step",
        )
        turn_rows = await self._dao.claim_messages(
            session,
            run_id,
            now,
            lease_expire_time,
            queue_type="next-turn",
            limit=1,
        )
        return [*step_rows, *turn_rows]

    @staticmethod
    def _to_human_message(row: ChatDataAgentInbox) -> HumanMessage:
        additional_kwargs: dict[str, Any] = {
            "timestamp": row.create_time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        meta = _load_inbox_meta(row.record_meta)
        # 随行引用/附件还原进 additional_kwargs；校验解析（白名单、引用短句、
        # 片段归一、附件占位与视觉装载）由 agent_stream 采纳点统一完成——本层只透传。
        for key in ("file_selections", "quoted_snippets", "file_ids"):
            if meta.get(key):
                additional_kwargs[key] = meta[key]
        return HumanMessage(
            id=row.message_id,
            content=row.record_content,
            additional_kwargs=additional_kwargs,
        )


def _load_inbox_meta(meta_text: str | None) -> dict[str, Any]:
    """解析排队消息随行元数据列（JSON 文本）；空/坏数据一律回退空 dict。"""
    if not meta_text:
        return {}
    try:
        loaded = json.loads(meta_text)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


agent_inbox_service = AgentInboxService()
