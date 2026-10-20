"""ChatData 单一智能体的运行选项。

工作区始终存在，差别只在其后端及 Bash 是否注册：

- ``workspace_backend``: ``local`` 使用受限的本地会话目录；``sandbox`` 使用 OpenSandbox。
- ``bash_enabled``: 是否注册 ``bash_run``。
- ``explore_subagent_enabled``: 注册 ``data-explore`` 子智能体（只读事实收集）。
- ``plan_subagent_enabled``: 注册 ``plan`` 子智能体（统一规划，产出计划文件）。
- ``general_purpose_subagent_enabled``: 注册 ``general-purpose`` 子智能体（通用执行，
  产出交付物）。任一启用即注册 ``task`` 工具，允许主智能体把长任务派发给隔离上下文的
  子智能体（见 ``middleware/subagents.py`` 与 ``data_agent.build_subagent_middleware``）。

本地 Bash 不提供隔离，只能在受信任的开发或测试部署显式开启。
"""

from dataclasses import dataclass, replace
from typing import Literal

WorkspaceBackend = Literal["local", "sandbox"]


@dataclass(frozen=True)
class AgentRuntimeOptions:
    """单一 ``chat_data`` Agent 的每轮运行选项。"""

    workspace_backend: WorkspaceBackend = "local"
    bash_enabled: bool = False
    explore_subagent_enabled: bool = False
    plan_subagent_enabled: bool = False
    general_purpose_subagent_enabled: bool = False

    def __post_init__(self) -> None:
        if self.workspace_backend not in ("local", "sandbox"):
            object.__setattr__(self, "workspace_backend", "local")


def get_chat_data_default_runtime_options() -> AgentRuntimeOptions:
    """从环境变量读取系统默认运行选项。"""
    from ..services.runtime_config import (
        CHAT_DATA_DEFAULT_BASH_ENABLED,
        CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED,
        CHAT_DATA_DEFAULT_GENERAL_PURPOSE_SUBAGENT_ENABLED,
        CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED,
        CHAT_DATA_DEFAULT_WORKSPACE_BACKEND,
    )

    return AgentRuntimeOptions(
        workspace_backend=CHAT_DATA_DEFAULT_WORKSPACE_BACKEND,
        bash_enabled=CHAT_DATA_DEFAULT_BASH_ENABLED,
        explore_subagent_enabled=CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED,
        plan_subagent_enabled=CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED,
        general_purpose_subagent_enabled=CHAT_DATA_DEFAULT_GENERAL_PURPOSE_SUBAGENT_ENABLED,
    )


def resolve_agent_runtime_options(
    *,
    user_id: str | None = None,
    explore_subagent_enabled: bool | None = None,
    plan_subagent_enabled: bool | None = None,
    general_purpose_subagent_enabled: bool | None = None,
) -> AgentRuntimeOptions:
    """解析最终运行选项。

    **部署能力只由服务端配置决定**：``workspace_backend`` / ``bash_enabled`` /
    子智能体开关都来自 env 或配置表热值，不接受前端请求动态传参（前端仅保留
    「权限选择」，审批模式单独走权限链路）。

    三个子智能体开关的默认值（``CHAT_DATA_DEFAULT_*_SUBAGENT_ENABLED``）支持
    运行时热值：async 调用方经 ``get_runtime_setting`` 解析后经参数传入，未传
    （None）则回落 env 快照（sync 兜底路径，如 ``create_data_agent`` 默认装配）。
    """
    _ = user_id
    base = get_chat_data_default_runtime_options()
    if explore_subagent_enabled is not None:
        base = replace(base, explore_subagent_enabled=explore_subagent_enabled)
    if plan_subagent_enabled is not None:
        base = replace(base, plan_subagent_enabled=plan_subagent_enabled)
    if general_purpose_subagent_enabled is not None:
        base = replace(base, general_purpose_subagent_enabled=general_purpose_subagent_enabled)
    return replace(
        base,
        explore_subagent_enabled=explore_subagent_enabled
        if explore_subagent_enabled is not None
        else base.explore_subagent_enabled,
        plan_subagent_enabled=plan_subagent_enabled
        if plan_subagent_enabled is not None
        else base.plan_subagent_enabled,
        general_purpose_subagent_enabled=general_purpose_subagent_enabled
        if general_purpose_subagent_enabled is not None
        else base.general_purpose_subagent_enabled,
    )


async def get_subagent_hot_defaults() -> dict:
    """读取子智能体开关的运行时热值（async 调用方路径）。

    键名与 ``resolve_agent_runtime_options`` 的同名参数一一对应，经 ``**`` 解包
    透传；三个开关的读取单一入口在此，新增子智能体开关只改本函数与解析参数。
    """
    from ..services.setting_service import get_runtime_setting

    return {
        "explore_subagent_enabled": bool(await get_runtime_setting("CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED")),
        "plan_subagent_enabled": bool(await get_runtime_setting("CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED")),
        "general_purpose_subagent_enabled": bool(
            await get_runtime_setting("CHAT_DATA_DEFAULT_GENERAL_PURPOSE_SUBAGENT_ENABLED")
        ),
    }
