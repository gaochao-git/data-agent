"""子图中间件组装顺序守卫。

``SUBAGENT_MIDDLEWARE_CANONICAL_ORDER`` 是子图唯一权威顺序表；本测试枚举
控制/审批/沙箱续期/租约围栏的启用组合，断言装配结果与规范顺序表过滤后
逐一致。位置约束不再靠 ``_compile_subagent`` 里的 insert/append 保证。
"""

from itertools import product
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.middleware.assembly import assemble_middleware
from apps.chat_data_app.agent.middleware.subagent_assembly import (
    SUBAGENT_MIDDLEWARE_CANONICAL_ORDER,
    assemble_subagent_middleware,
)

_MANDATORY_NAMES = [
    "ContextCompressionMiddleware",
    "ContextUsageMiddleware",
    "SkillPrerequisiteMiddleware",
    "ToolTimingMiddleware",
    "ToolCallNameNormalizerMiddleware",
    "ToolResultMiddleware",
    "ToolErrorMiddleware",
]

_OPTIONAL_NAMES = {
    "control": "SubagentControlMiddleware",
    "approval": "ToolApprovalMiddleware",
    "sandbox": "SandboxRenewalMiddleware",
    "fence": "ToolExecutionFenceMiddleware",
}


def _assemble(
    *,
    control: bool = False,
    approval: bool = False,
    sandbox: bool = False,
    fence: bool = False,
) -> list:
    dynamic = {
        "control": control,
        "approval": approval,
        "sandbox": sandbox,
        "fence": fence,
    }
    slots = {name: (SimpleNamespace(name=name) if dynamic[key] else None) for key, name in _OPTIONAL_NAMES.items()}
    return assemble_subagent_middleware(
        [],
        context_compression_middleware=SimpleNamespace(name="ContextCompressionMiddleware"),
        context_usage_middleware=SimpleNamespace(name="ContextUsageMiddleware"),
        control_middleware=slots["SubagentControlMiddleware"],
        approval_middleware=slots["ToolApprovalMiddleware"],
        sandbox_renewal_middleware=slots["SandboxRenewalMiddleware"],
        execution_fence_middleware=slots["ToolExecutionFenceMiddleware"],
    )


def _expected_names(enabled: set[str]) -> list[str]:
    present = enabled | set(_MANDATORY_NAMES)
    return [name for name in SUBAGENT_MIDDLEWARE_CANONICAL_ORDER if name in present]


def test_canonical_order_covers_every_subagent_slot():
    covered = set(_OPTIONAL_NAMES.values()) | set(_MANDATORY_NAMES)
    assert covered == set(SUBAGENT_MIDDLEWARE_CANONICAL_ORDER), (
        "SUBAGENT_MIDDLEWARE_CANONICAL_ORDER 与实际槽位不一致："
        f"缺失={covered - set(SUBAGENT_MIDDLEWARE_CANONICAL_ORDER)}, "
        f"多余={set(SUBAGENT_MIDDLEWARE_CANONICAL_ORDER) - covered}"
    )


def test_assembled_order_matches_canonical_order_for_all_combinations():
    for control, approval, sandbox, fence in product([False, True], repeat=4):
        if approval and not control:
            # 审批只在控制中间件启用（可中断后台任务）时才有意义。
            continue
        flags = {"control": control, "approval": approval, "sandbox": sandbox, "fence": fence}
        enabled = {name for key, name in _OPTIONAL_NAMES.items() if flags[key]}
        middleware = _assemble(control=control, approval=approval, sandbox=sandbox, fence=fence)
        actual = [getattr(item, "name", "") for item in middleware]
        assert actual == _expected_names(enabled), (
            f"组合 control={control} approval={approval} sandbox={sandbox} fence={fence} "
            f"的组装顺序与规范表不符: {actual}"
        )


def test_execution_fence_is_outermost_when_enabled():
    middleware = _assemble(fence=True)
    assert getattr(middleware[0], "name", "") == "ToolExecutionFenceMiddleware"


def test_shared_assembler_rejects_unregistered_or_missing_slots():
    with pytest.raises(ValueError, match="未登记"):
        assemble_middleware(("A",), {"B": object()})
    with pytest.raises(ValueError, match="缺槽位"):
        assemble_middleware(("A", "B"), {"A": object()})
