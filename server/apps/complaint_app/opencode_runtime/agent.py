from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional


AgentHandler = Callable[..., Any]


@dataclass
class Agent:
    """Agent 元信息与执行入口。"""
    name: str
    description: str = ""
    handler: AgentHandler | None = None
    allowed_tools: list[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def run(self, *args, **kwargs) -> Any:
        if not self.handler:
            raise RuntimeError(f"Agent handler not set: {self.name}")
        return self.handler(*args, **kwargs)


class AgentRegistry:
    """Agent 注册表。"""
    def __init__(self) -> None:
        self._agents: Dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        if not agent or not agent.name:
            raise ValueError("Agent name is required")
        self._agents[agent.name] = agent

    def get(self, name: str) -> Optional[Agent]:
        return self._agents.get(name)

    def list(self) -> Iterable[Agent]:
        return list(self._agents.values())

    def remove(self, name: str) -> None:
        if name in self._agents:
            del self._agents[name]
