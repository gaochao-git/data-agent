"""中间件组装顺序守卫。

``agent_runtime.MIDDLEWARE_CANONICAL_ORDER`` 是唯一权威顺序表；本测试枚举
全部可选中间件的启用/禁用组合（2^11 = 2048 种），断言实际组装结果与
"规范顺序表过滤后"逐一致。任何新增中间件插错位置、或顺序表与组装逻辑
脱节，都会在这里失败——顺序约束不靠 review 肉眼保证。
"""

from itertools import product
from types import SimpleNamespace

from apps.chat_data_app.agent.agent_runtime import (
    MIDDLEWARE_CANONICAL_ORDER,
    build_standard_agent_middleware,
)

# build_standard_agent_middleware 的可选注入槽位 -> 对应中间件注册名
_OPTIONAL_SLOTS = {
    "tool_execution_fence_middleware": "ToolExecutionFenceMiddleware",
    "tool_approval_middleware": "ToolApprovalMiddleware",
    "image_injection_middleware": "ImageInjectionMiddleware",
    "mcp_tool_discovery_middleware": "MCPToolDiscoveryMiddleware",
    "tool_call_history_repair_middleware": "ToolCallHistoryRepairMiddleware",
    "steering_middleware": "SteeringMiddleware",
    "subagent_middleware": "SubagentMiddleware",
    "after_model_history_middleware": "AfterModelHistoryMiddleware",
    "context_compression_middleware": "ContextCompressionMiddleware",
    "context_usage_middleware": "ContextUsageMiddleware",
    "tool_result_middleware": "ToolResultMiddleware",
}

# 无条件必备项（组装点内部创建）
_MANDATORY_NAMES = [
    "ToolTimingMiddleware",
    "ToolCallRateAlertMiddleware",
    "TodoListMiddleware",
    "ToolErrorMiddleware",
]

# include_flags 取值 -> 对应中间件注册名
_FLAG_SLOTS = {
    "response_timestamp": "ResponseTimestampMiddleware",
    "tool_call_normalizer": "ToolCallNameNormalizerMiddleware",
}


def _expected_names(enabled: dict[str, str]) -> list[str]:
    """按规范顺序表过滤出本次应出现的中间件名。"""
    present = set(enabled.values()) | set(_MANDATORY_NAMES)
    return [name for name in MIDDLEWARE_CANONICAL_ORDER if name in present]


def test_canonical_order_covers_every_registered_middleware():
    """顺序表必须覆盖所有槽位与必备项，防止新增中间件忘记登记。"""
    covered = set(_OPTIONAL_SLOTS.values()) | set(_FLAG_SLOTS.values()) | set(_MANDATORY_NAMES)
    assert covered == set(MIDDLEWARE_CANONICAL_ORDER), (
        "MIDDLEWARE_CANONICAL_ORDER 与实际槽位不一致："
        f"缺失={covered - set(MIDDLEWARE_CANONICAL_ORDER)}, 多余={set(MIDDLEWARE_CANONICAL_ORDER) - covered}"
    )


def test_assembled_order_matches_canonical_order_for_all_combinations():
    """枚举全部启用组合，组装结果必须等于规范顺序表过滤后的序列。"""
    slot_names = list(_OPTIONAL_SLOTS)
    flag_names = list(_FLAG_SLOTS)
    for bits in product([False, True], repeat=len(slot_names) + len(flag_names)):
        kwargs: dict = {}
        enabled: dict[str, str] = {}
        for slot, on in zip(slot_names, bits[: len(slot_names)], strict=True):
            if on:
                name = _OPTIONAL_SLOTS[slot]
                kwargs[slot] = SimpleNamespace(name=name)
                enabled[slot] = name
        enabled_flags: set[str] = set()
        for flag, on in zip(flag_names, bits[len(slot_names) :], strict=True):
            if on:
                enabled_flags.add(flag)
                enabled[flag] = _FLAG_SLOTS[flag]
        kwargs["include_flags"] = frozenset(enabled_flags)

        middleware = build_standard_agent_middleware([], **kwargs)
        actual = [getattr(item, "name", "") for item in middleware]
        assert actual == _expected_names(enabled), f"组合 {sorted(enabled.values())} 的组装顺序与规范表不符: {actual}"
