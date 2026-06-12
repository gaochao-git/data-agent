"""
Prompt 层

提供语义层使用的 Prompt 模板。
"""

from .match_prompt import (
    MatchPromptBuilder,
    build_match_prompt,
    build_concise_match_prompt,
    MATCH_PROMPT_TEMPLATE,
    MATCH_PROMPT_CONCISE_TEMPLATE,
)
from .validate_prompt import (
    ValidatePromptBuilder,
    build_intent_check_prompt,
    build_sql_check_prompt,
    build_ambiguity_check_prompt,
    INTENT_CHECK_PROMPT,
    SQL_CHECK_PROMPT,
    AMBIGUITY_CHECK_PROMPT,
)

__all__ = [
    # 匹配 Prompt
    "MatchPromptBuilder",
    "build_match_prompt",
    "build_concise_match_prompt",
    "MATCH_PROMPT_TEMPLATE",
    "MATCH_PROMPT_CONCISE_TEMPLATE",
    # 验证 Prompt
    "ValidatePromptBuilder",
    "build_intent_check_prompt",
    "build_sql_check_prompt",
    "build_ambiguity_check_prompt",
    "INTENT_CHECK_PROMPT",
    "SQL_CHECK_PROMPT",
    "AMBIGUITY_CHECK_PROMPT",
]
