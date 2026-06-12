from __future__ import annotations

import os


MATCH_STRATEGY_TWO_STAGE = "two_stage"
MATCH_STRATEGY_LEGACY = "legacy"
MATCH_STRATEGY_LAW_SKILL = "law_skill"
VALID_MATCH_STRATEGIES = {MATCH_STRATEGY_TWO_STAGE, MATCH_STRATEGY_LEGACY, MATCH_STRATEGY_LAW_SKILL}


def _get_match_strategy_default(default: str = MATCH_STRATEGY_TWO_STAGE) -> str:
    value = str(os.getenv("COMPLAINT_MATCH_STRATEGY_DEFAULT") or "").strip().lower()
    if value in VALID_MATCH_STRATEGIES:
        return value
    return default


def normalize_match_strategy(value: str | None, default_value: str | None = None) -> str:
    raw = str(value or "").strip().lower()
    if raw:
        if raw in VALID_MATCH_STRATEGIES:
            return raw
        raise ValueError(f"invalid match_strategy: {value}")
    default_raw = str(default_value or _get_match_strategy_default()).strip().lower()
    if default_raw in VALID_MATCH_STRATEGIES:
        return default_raw
    return MATCH_STRATEGY_TWO_STAGE


__all__ = [
    "MATCH_STRATEGY_TWO_STAGE",
    "MATCH_STRATEGY_LEGACY",
    "MATCH_STRATEGY_LAW_SKILL",
    "VALID_MATCH_STRATEGIES",
    "normalize_match_strategy",
]
