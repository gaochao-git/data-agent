"""后台子智能体的运行期控制：steering 注入。

控制命令由主智能体通过 ``subagent`` 工具写入任务账本；本中间件在每个模型
调用前读取一次（模型步粒度，不是 token 粒度），因此插话不会被正在生成的
那一轮 token 撕裂。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from utils.logger import get_logger

from ..contracts.subagent_task import get_subagent_task_service

logger = get_logger(__name__)

__all__ = ["SubagentControlMiddleware", "create_subagent_control_middleware"]


class SubagentControlMiddleware(AgentMiddleware):
    """在子图模型调用前消费插话队列。"""

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not isinstance(state, dict):
            return None
        task_id = str(state.get("subagent_task_id") or "")
        owner_token = str(state.get("subagent_owner_token") or "")
        service = get_subagent_task_service()
        if not task_id or not owner_token or service is None:
            return None
        try:
            steers = await service.claim_steer_messages(task_id, owner_token)
        except Exception:
            logger.warning("[tools] 子智能体控制通道读取失败，本轮跳过: task_id=%s", task_id, exc_info=True)
            return None
        if not steers:
            return None
        return {"messages": [HumanMessage(content=message) for message in steers]}


def create_subagent_control_middleware() -> AgentMiddleware:
    return SubagentControlMiddleware()
