"""子图调用配置：切断父图回调/检查点继承。

独立成模块是因为 ``subagents.py`` 已接近单文件行数上限；配置构造不依赖
子图执行体，放在这里也不会形成循环导入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.constants import CONFIG_KEY_CHECKPOINTER

if TYPE_CHECKING:
    from .subagents import SubagentSpec

__all__ = ["build_subagent_invocation_config"]


def build_subagent_invocation_config(
    spec: SubagentSpec,
    *,
    default_recursion_limit: int | None = None,
    background_task_id: str | None = None,
    agent_session_id: str | None = None,
    checkpointer: Any = None,
) -> dict[str, Any]:
    """构造子图调用配置：切断回调继承，避免子图 token 进入主 SSE 流。

    ``background_task_id`` 用于后台子智能体：显式写入独立 thread_id 后，
    langgraph 会丢弃父图环境里的 ``__pregel_checkpointer``/checkpoint_ns，
    否则后台任务会继续借用父 Run 的 MySQL checkpointer——父 Run 收口关闭
    连接后，子图收尾写 checkpoint 就会报 ``(0, 'Not connected')``。
    后台任务本身有持久账本，不需要也不应继承父 Run 的检查点。

    default_recursion_limit 由调用方（异步装配链路）运行期经配置服务解析后传入
    （get_runtime_setting 对注册 key 恒返回 int，不会为 None）；两者都缺省时
    langgraph 默认 25 个 super-step，不低于 1 的下限由 max 保证。
    """
    configurable: dict[str, Any] = {"chatdata_subagent": spec.name}
    if checkpointer is not None:
        # 子图独立检查点：线程按智能体会话稳定，命名空间按任务隔离。
        # 追问复用同一会话线程，但每轮独立命名空间，避免把上一轮已从
        # 会话快照重建的历史重复追加进 messages。
        session_key = agent_session_id or background_task_id
        configurable["thread_id"] = f"chatdata-agent-session-{session_key}"
        configurable["checkpoint_ns"] = f"task-{background_task_id}" if background_task_id else ""
        configurable[CONFIG_KEY_CHECKPOINTER] = checkpointer
    elif background_task_id:
        configurable["thread_id"] = f"chatdata-subagent-task-{background_task_id}"
    return {
        "callbacks": [],
        "recursion_limit": max(int(spec.recursion_limit or default_recursion_limit or 0), 1),
        "configurable": configurable,
    }
