"""ChatData 单一智能体的运行选项。

工作区始终存在，差别只在其后端及 Bash 是否注册：

- ``workspace_backend``: ``local`` 使用受限的本地会话目录；``sandbox`` 使用 OpenSandbox。
- ``bash_enabled``: 是否注册 ``bash_run``。
- ``explore_subagent_enabled``: 注册 ``data-explore`` 子智能体（只读事实收集）。
- ``plan_subagent_enabled``: 注册 ``plan`` 子智能体（统一规划，产出计划文件）。
  两者任一启用即注册 ``task`` 工具，允许主智能体把长任务派发给隔离上下文的
  子智能体（见 ``middleware/subagents.py`` 与 ``data_agent.build_subagent_middleware``）。

本地 Bash 不提供隔离，只能在受信任的开发或测试部署显式开启。
"""

from dataclasses import dataclass
from typing import Literal

WorkspaceBackend = Literal["local", "sandbox"]


@dataclass(frozen=True)
class AgentRuntimeOptions:
    """单一 ``chat_data`` Agent 的每轮运行选项。"""

    workspace_backend: WorkspaceBackend = "local"
    bash_enabled: bool = False
    explore_subagent_enabled: bool = False
    plan_subagent_enabled: bool = False

    def __post_init__(self) -> None:
        if self.workspace_backend not in ("local", "sandbox"):
            object.__setattr__(self, "workspace_backend", "local")


def get_chat_data_default_runtime_options() -> AgentRuntimeOptions:
    """从环境变量读取系统默认运行选项。"""
    from ..services.runtime_config import (
        CHAT_DATA_DEFAULT_BASH_ENABLED,
        CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED,
        CHAT_DATA_DEFAULT_WORKSPACE_BACKEND,
        CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED,
    )

    return AgentRuntimeOptions(
        workspace_backend=CHAT_DATA_DEFAULT_WORKSPACE_BACKEND,
        bash_enabled=CHAT_DATA_DEFAULT_BASH_ENABLED,
        explore_subagent_enabled=CHAT_DATA_DEFAULT_EXPLORE_SUBAGENT_ENABLED,
        plan_subagent_enabled=CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED,
    )


def resolve_agent_runtime_options(
    *,
    user_id: str | None = None,
    override: dict | None = None,
) -> AgentRuntimeOptions:
    """解析最终运行选项。

    仅在 ``CHAT_DATA_CAPABILITY_USER_OVERRIDABLE=true`` 时接受请求覆盖。生产环境
    关闭该变量后，前端传入的任何值都不会改变服务端默认配置。
    """
    _ = user_id
    base = get_chat_data_default_runtime_options()
    if override and _is_runtime_option_user_overridable():
        backend = str(override.get("workspace_backend", base.workspace_backend) or base.workspace_backend)
        if backend not in ("local", "sandbox"):
            backend = base.workspace_backend
        return AgentRuntimeOptions(
            workspace_backend=backend,
            bash_enabled=_coerce_bool(override.get("bash_enabled"), base.bash_enabled),
            explore_subagent_enabled=_coerce_bool(
                override.get("explore_subagent_enabled"), base.explore_subagent_enabled
            ),
            plan_subagent_enabled=_coerce_bool(
                override.get("plan_subagent_enabled"), base.plan_subagent_enabled
            ),
        )
    return base


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _is_runtime_option_user_overridable() -> bool:
    from ..services.runtime_config import is_chat_data_runtime_option_user_overridable

    return is_chat_data_runtime_option_user_overridable()
