"""后台子智能体的运行期控制：暂停与 steering 注入。

控制命令由主智能体通过 ``task`` 工具写入任务账本；本中间件在每个模型
调用前读取一次（模型步粒度，不是 token 粒度），因此暂停落在安全点、
插话不会被正在生成的那一轮 token 撕裂。暂停用 LangGraph ``interrupt``
实现，必须配合子图独立 checkpointer 才能恢复。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from utils.logger import get_logger

from ..contracts.subagent_task import get_subagent_task_service

logger = get_logger(__name__)

__all__ = ["SubagentControlMiddleware", "create_subagent_control_middleware"]


class SubagentControlMiddleware(AgentMiddleware):
    """在子图模型调用前消费暂停标记与插话队列。"""

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not isinstance(state, dict):
            return None
        task_id = str(state.get("subagent_task_id") or "")
        owner_token = str(state.get("subagent_owner_token") or "")
        service = get_subagent_task_service()
        if not task_id or not owner_token or service is None:
            return None
        try:
            pause_requested = await service.is_pause_requested(task_id, owner_token)
        except Exception:
            logger.warning("[tools] 子智能体暂停标记读取失败，本轮跳过: task_id=%s", task_id, exc_info=True)
            return None
        if pause_requested:
            # GraphInterrupt 不是业务异常，必须原样上抛；被下面的兜底吞掉会
            # 让任务看起来正常结束，检查点却停在半途。
            interrupt({"kind": "subagent_pause", "task_id": task_id})
        try:
            steers = await service.claim_steer_messages(task_id, owner_token)
        except GraphInterrupt:
            raise
        except Exception:
            logger.warning("[tools] 子智能体控制通道读取失败，本轮跳过: task_id=%s", task_id, exc_info=True)
            return None
        if not steers:
            return None
        return {"messages": [HumanMessage(content=message) for message in steers]}


def create_subagent_control_middleware() -> AgentMiddleware:
    return SubagentControlMiddleware()
