from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable


class PermissionDecision(str, Enum):
    """权限决策枚举。"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionRule:
    """工具权限规则。"""
    tool_name: str
    decision: PermissionDecision = PermissionDecision.ALLOW
    metadata: Dict[str, Any] = field(default_factory=dict)

    def matches(self, tool_name: str) -> bool:
        if self.tool_name == "*":
            return True
        return self.tool_name == tool_name


class PermissionEngine:
    """权限引擎：按规则顺序匹配并返回决策。"""
    def __init__(self, rules: Iterable[PermissionRule] | None = None) -> None:
        self._rules = list(rules) if rules else []

    def add_rule(self, rule: PermissionRule) -> None:
        self._rules.append(rule)

    def check(self, tool_name: str, context: Any | None = None) -> PermissionDecision:
        for rule in self._rules:
            if rule.matches(tool_name):
                return rule.decision
        return PermissionDecision.ALLOW
