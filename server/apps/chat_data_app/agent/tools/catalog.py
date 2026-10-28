"""工具能力目录：内置、中间件、MCP 工具的统一元数据视图。

目录只描述“工具是什么、来自哪里、风险/审批/可见性如何”，不负责构造工具；
``assemble_tools``、审批策略、装配诊断和管理端都从这里读取同一份元数据，
避免再维护多张分散的 name map。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from utils.logger import get_logger

from .registry import (
    MIDDLEWARE_INJECTED_APPROVAL_TOOLS,
    SUBAGENT_BASE_EXCLUDED_TOOL_NAMES,
    TOOL_SPECS,
    system_tool_approval,
    tool_risk,
)

logger = get_logger(__name__)

ToolSource = Literal["builtin", "middleware", "mcp", "runtime"]
ToolRisk = Literal["low", "medium", "high"]
ToolApproval = Literal["allow", "ask", "deny"]

DEFAULT_TOOL_APPROVAL: ToolApproval = "ask"
VALID_TOOL_APPROVALS = frozenset({"allow", "ask", "deny"})


def normalize_tool_approval(value: Any, *, default: ToolApproval = DEFAULT_TOOL_APPROVAL) -> ToolApproval:
    """归一化 allow/ask/deny；未知值 fail-closed 回落默认值。"""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_TOOL_APPROVALS else default  # type: ignore[return-value]


@dataclass(frozen=True)
class ToolDescriptor:
    """单个工具的管理元数据。"""

    name: str
    source: ToolSource
    risk: ToolRisk
    permission: str
    owner: str
    approval: ToolApproval
    description: str = ""
    data_scope: frozenset[str] = frozenset()
    subagent_visible: bool = True
    searchable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "risk": self.risk,
            "permission": self.permission,
            "owner": self.owner,
            "approval": self.approval,
            "description": self.description,
            "data_scope": sorted(self.data_scope),
            "subagent_visible": self.subagent_visible,
            "searchable": self.searchable,
        }


@dataclass(frozen=True)
class ToolCatalog:
    """一次装配的工具目录；同名工具以先登记/后动态覆盖为准。"""

    descriptors: tuple[ToolDescriptor, ...]

    @property
    def by_name(self) -> dict[str, ToolDescriptor]:
        return {descriptor.name: descriptor for descriptor in self.descriptors}

    def names(self) -> tuple[str, ...]:
        return tuple(descriptor.name for descriptor in self.descriptors)

    def approval_policies(self) -> dict[str, str]:
        return {descriptor.name: descriptor.approval for descriptor in self.descriptors}

    def to_dicts(self) -> list[dict[str, Any]]:
        return [descriptor.to_dict() for descriptor in self.descriptors]


def _metadata(tool: Any) -> Mapping[str, Any]:
    metadata = getattr(tool, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _is_mcp_tool(name: str, metadata: Mapping[str, Any]) -> bool:
    return str(metadata.get("origin") or "") == "mcp" or name.startswith("mcp_")


def _builtin_descriptors() -> list[ToolDescriptor]:
    descriptors: list[ToolDescriptor] = []
    for spec in TOOL_SPECS:
        for name in spec.provides:
            descriptors.append(
                ToolDescriptor(
                    name=name,
                    source="builtin",
                    risk=tool_risk(name),  # type: ignore[arg-type]
                    permission=f"tool:{name}",
                    owner="platform",
                    approval=normalize_tool_approval(system_tool_approval(name)),
                    description=spec.note,
                    subagent_visible=name not in SUBAGENT_BASE_EXCLUDED_TOOL_NAMES,
                )
            )
    for name in MIDDLEWARE_INJECTED_APPROVAL_TOOLS:
        descriptors.append(
            ToolDescriptor(
                name=name,
                source="middleware",
                risk=tool_risk(name),  # type: ignore[arg-type]
                permission=f"tool:{name}",
                owner="platform",
                approval=normalize_tool_approval(system_tool_approval(name)),
                description="middleware injected",
            )
        )
    return descriptors


def _dynamic_descriptor(tool: Any) -> ToolDescriptor | None:
    name = str(getattr(tool, "name", "") or "").strip()
    if not name:
        return None
    metadata = _metadata(tool)
    if not _is_mcp_tool(name, metadata):
        return ToolDescriptor(
            name=name,
            source="runtime",
            risk="medium",
            permission=f"tool:{name}",
            owner="platform",
            approval=normalize_tool_approval(system_tool_approval(name)),
            description=str(getattr(tool, "description", "") or ""),
        )
    raw_approval = metadata.get("approval") or DEFAULT_TOOL_APPROVAL
    approval = normalize_tool_approval(raw_approval)
    if approval != str(raw_approval).strip().lower():
        logger.warning("[tool_catalog] 非法 MCP 审批档位，按 ask 处理: tool=%s value=%r", name, raw_approval)
    return ToolDescriptor(
        name=name,
        source="mcp",
        risk="high" if approval == "ask" else "medium",
        permission=f"tool:{name}",
        owner=str(metadata.get("mcp_server_id") or ""),
        approval=approval,
        description=str(getattr(tool, "description", "") or ""),
        subagent_visible=False,
        searchable=True,
    )


def build_tool_catalog(tools: Iterable[Any] = ()) -> ToolCatalog:
    """合并内置/中间件/动态工具，返回统一目录。

    ``tools`` 传本次实际挂载的工具（含 MCP）；不在注册表里的普通工具按 runtime
    处理，保持与旧审批策略一致的 fail-closed 行为。
    """
    descriptors = _builtin_descriptors()
    known = {descriptor.name for descriptor in descriptors}
    for tool in tools:
        descriptor = _dynamic_descriptor(tool)
        if descriptor is None or descriptor.name in known:
            continue
        descriptors.append(descriptor)
        known.add(descriptor.name)
    return ToolCatalog(tuple(descriptors))


__all__ = [
    "DEFAULT_TOOL_APPROVAL",
    "ToolApproval",
    "ToolCatalog",
    "ToolDescriptor",
    "ToolRisk",
    "ToolSource",
    "VALID_TOOL_APPROVALS",
    "build_tool_catalog",
    "normalize_tool_approval",
]
