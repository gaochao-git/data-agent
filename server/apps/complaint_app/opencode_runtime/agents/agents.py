from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..config import RuntimeConfig
from apps.complaint_app.core.model_config import get_summary_model
from ..permission import PermissionDecision, PermissionEngine, PermissionRule
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AgentModel:
    """模型配置（provider/model）。"""

    provider_id: str
    model_id: str


@dataclass
class AgentInfo:
    """Agent 元信息（对齐 opencode 的字段集合）。"""

    name: str
    description: str = ""
    mode: str = "all"  # subagent | primary | all
    native: bool = False
    hidden: bool = False
    top_p: float | None = None
    temperature: float | None = None
    color: str | None = None
    permission: List[PermissionRule] = field(default_factory=list)
    allowed_tools: List[str] | None = None
    model: AgentModel | None = None
    prompt: str | None = None
    options: Dict[str, Any] = field(default_factory=dict)
    steps: int | None = None


def _read_text(path: Path) -> str:
    """读取文本文件内容，不存在则返回空字符串。"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("[agents] read prompt file failed path=%s err=%s", path, exc)
        return ""


def _default_permissions() -> List[PermissionRule]:
    """构建默认权限规则（当前为全允许）。"""
    return [PermissionRule(tool_name="*", decision=PermissionDecision.ALLOW)]


def _prompt_dir() -> Path:
    """返回 prompt 目录路径。"""
    return Path(__file__).resolve().parent / "prompt"


def _build_defaults() -> Dict[str, AgentInfo]:
    """构建内置 Agent 列表。"""
    prompt_root = _prompt_dir()
    compaction_prompt = _read_text(prompt_root / "compaction.txt")
    complaint_summary = _read_text(prompt_root / "complaint_summary.txt")

    defaults = _default_permissions()

    result: Dict[str, AgentInfo] = {
        "build": AgentInfo(
            name="build",
            mode="primary",
            native=True,
            permission=list(defaults),
            options={},
        ),
        "plan": AgentInfo(
            name="plan",
            mode="primary",
            native=True,
            permission=list(defaults),
            options={},
        ),
        "compaction": AgentInfo(
            name="compaction",
            mode="subagent",
            native=True,
            hidden=True,
            prompt=compaction_prompt,
            permission=list(defaults),
            options={},
        ),
        "complaint_summary": AgentInfo(
            name="complaint_summary",
            description="举报件摘要智能体，根据举报件原文生成举报件摘要，包括举报人、被举报人、事情经过、举报事项等信息",
            mode="subagent",
            native=True,
            hidden=False,
            allowed_tools=[],
            model=AgentModel(provider_id="", model_id=get_summary_model()),
            prompt=complaint_summary,
            permission=list(defaults),
            options={},
        ),
    }
    return result


class AgentRegistry:
    """Agent 注册表（按需构建并缓存）。"""

    _cache: Dict[str, AgentInfo] | None = None

    @classmethod
    def registry(cls) -> Dict[str, AgentInfo]:
        """返回当前 Agent 字典（首次调用时构建默认值）。"""
        if cls._cache is None:
            cls._cache = _build_defaults()
        return dict(cls._cache)

    @classmethod
    def register(cls, agent: AgentInfo) -> None:
        """注册/覆盖一个 Agent。"""
        if not agent or not agent.name:
            raise ValueError("Agent name is required")
        if cls._cache is None:
            cls._cache = _build_defaults()
        cls._cache[agent.name] = agent

    @classmethod
    def get(cls, name: str) -> AgentInfo | None:
        """按名称获取 Agent。"""
        return cls.registry().get(name)

    @classmethod
    def list(cls) -> List[AgentInfo]:
        """返回 Agent 列表（默认 agent 排在最前）。"""
        cfg = RuntimeConfig.from_env()
        agents = list(cls.registry().values())
        default_name = (cfg.default_agent or "build").strip() or "build"
        agents.sort(key=lambda item: 0 if item.name == default_name else 1)
        return agents

    @classmethod
    def default_agent(cls) -> str:
        """返回默认主 Agent 的名称。"""
        cfg = RuntimeConfig.from_env()
        agents = cls.registry()

        if cfg.default_agent:
            agent = agents.get(cfg.default_agent)
            if not agent:
                raise ValueError(f'Default agent "{cfg.default_agent}" not found')
            if agent.mode == "subagent":
                raise ValueError(f'Default agent "{cfg.default_agent}" is a subagent')
            if agent.hidden:
                raise ValueError(f'Default agent "{cfg.default_agent}" is hidden')
            return agent.name

        for agent in agents.values():
            if agent.mode != "subagent" and not agent.hidden:
                return agent.name
        raise ValueError("No primary visible agent found")

    @classmethod
    def subagents(cls) -> List[AgentInfo]:
        """返回可用的子 Agent 列表（排除 primary）。"""
        return [agent for agent in cls.list() if agent.mode != "primary" and not agent.hidden]

    @classmethod
    def clear_cache(cls) -> None:
        """清空缓存（下次调用会重新构建）。"""
        cls._cache = None


def check_permission(agent: AgentInfo | None, tool_name: str) -> PermissionDecision:
    """检查 agent 是否允许调用指定工具。"""
    if not agent:
        return PermissionDecision.ALLOW
    engine = PermissionEngine(agent.permission)
    return engine.check(tool_name)


def subagent_lines() -> List[str]:
    """生成子 Agent 列表的描述行。"""
    lines: List[str] = []
    for item in AgentRegistry.subagents():
        desc = item.description or "This subagent should only be called manually by the user."
        lines.append(f"- {item.name}: {desc}")
    return lines
