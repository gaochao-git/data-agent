"""运行中子智能体的过程快照。

详情页打开时若从 Redis Stream 的 ``0-0`` 全量重放，token 级增量动辄上千条，
首屏会明显变慢。子图在每个 super-step 都会写 LangGraph 检查点，这里直接读
最新检查点里的 messages，复用收口快照的 ``_build_trail_snapshot`` 生成 trail；
调用方再附带当前事件流尾游标，前端先渲染快照、再从游标之后订阅增量。

快照与游标不是原子读取，两个方向都有窗口，前端各自兜底：
- 事件泵领先（检查点滞后）：游标已越过检查点尚未包含的消息，续传不会重放；
  这类缺口在任务收口后由 ``result_meta.subagent_trail`` 持久快照补齐，运行中
  可能短暂少一两条过程条目。
- 检查点领先（事件泵滞后）：游标之后会重放已完结消息的 token 增量。前端
  ``useSubagentTaskStream`` 把快照里的 message id 记为已闭合，丢弃这些增量，
  避免无条件拼接导致正文/思考翻倍。
因此这里不需要额外加锁：加锁也串不起 LangGraph 检查点与 Redis 事件流两套存储。
"""

from __future__ import annotations

from typing import Any

from utils.checkpoint import get_checkpointer
from utils.logger import get_logger

from ..middleware.subagent_trail import _build_trail_snapshot

logger = get_logger(__name__)


def _checkpoint_config(task_id: str, agent_session_id: str) -> dict[str, Any]:
    """与 ``build_subagent_invocation_config`` 的子图检查点寻址保持一致。"""
    session_key = agent_session_id or task_id
    return {
        "configurable": {
            "thread_id": f"chatdata-agent-session-{session_key}",
            "checkpoint_ns": f"task-{task_id}",
        }
    }


async def _load_checkpoint_values(task_id: str, agent_session_id: str) -> dict[str, Any] | None:
    try:
        async with get_checkpointer() as checkpointer:
            snapshot = await checkpointer.aget_tuple(_checkpoint_config(task_id, agent_session_id))
    except Exception:
        logger.warning("读取子智能体检查点快照失败: task_id=%s", task_id, exc_info=True)
        return None
    checkpoint = getattr(snapshot, "checkpoint", None) if snapshot is not None else None
    if not isinstance(checkpoint, dict):
        return None
    values = checkpoint.get("channel_values")
    return values if isinstance(values, dict) else None


async def load_live_trail_snapshot(*, task_id: str, agent_session_id: str) -> dict[str, Any] | None:
    """返回 ``{trail, compactions}``；没有可用检查点时返回 None，调用方回退全量重放。"""
    values = await _load_checkpoint_values(task_id, agent_session_id)
    if not values:
        return None
    messages = values.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    compactions = values.get("context_compression_events")
    if not isinstance(compactions, list):
        compactions = []
    trail = _build_trail_snapshot(messages, compactions, pending_tool_status="running")
    if not trail:
        return None
    return {"trail": trail, "compactions": compactions}
