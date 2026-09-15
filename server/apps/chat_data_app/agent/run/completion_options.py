"""SQL completion 链路的运行参数解析（从 handlers.py 归位到 agent/run）。

供 sql_completion_handler 与 agent_schedule_runtime 派发链路共用，
保证两条入口的 options 字段解析口径一致；模块仅依赖标准库，
避免派发链路反向依赖 handlers 的重装配。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class CompletionRunOptions:
    """sql_completion_handler 从 options 中解析出的本轮运行参数。"""

    datasource_id: str | None
    semantic_config_id: str | None
    model_id: Any
    enable_thinking: Any
    thinking_effort: Any
    member_datasource_ids: list
    semantic_config_selection: Any
    skill_names: list[str]


def normalize_enable_thinking(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def normalize_thinking_effort(value: Any) -> str | None:
    """归一化思考档位；非法值返回 None（由 resolve_thinking 回退 default_effort）。"""
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized or None
    return None


def parse_completion_run_options(options: Optional[Dict[str, Any]]) -> CompletionRunOptions:
    options_data = options or {}
    raw_skill_names = options_data.get("skill_names") or []
    if isinstance(raw_skill_names, str):
        skill_names = [raw_skill_names]
    elif isinstance(raw_skill_names, list):
        skill_names = [str(item or "").strip() for item in raw_skill_names if str(item or "").strip()]
    else:
        skill_names = []
    return CompletionRunOptions(
        datasource_id=str(options_data.get("datasource_id") or "").strip() or None,
        semantic_config_id=str(options_data.get("semantic_config_id") or "").strip() or None,
        model_id=options_data.get("model_id"),
        enable_thinking=normalize_enable_thinking(options_data.get("enable_thinking")),
        thinking_effort=normalize_thinking_effort(options_data.get("thinking_effort")),
        member_datasource_ids=options_data.get("member_datasource_ids") or [],
        semantic_config_selection=options_data.get("semantic_config_selection"),
        skill_names=skill_names,
    )
