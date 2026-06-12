"""
Agent 模式配置

统一管理不同模式下的提示词和工具选择逻辑
"""

from dataclasses import dataclass

from .prompts.chat_data import SQL_GENERATOR_PROMPT_SEMANTIC_QUERY
from .prompts.nl2sql import SQL_GENERATOR_PROMPT_GENERATE


@dataclass
class ModeConfig:
    """
    模式配置

    Attributes:
        mode: 模式名称 ("generate" 或 "query")
        base_prompt: 基础提示词模板
        uses_tools: 是否使用工具
    """

    mode: str
    base_prompt: str
    uses_tools: bool


# 模式配置映射表
# 键：(是否语义模式, mode)
# 值：ModeConfig
MODE_CONFIGS = {
    # 普通 NL2SQL 模式
    (False, "generate"): ModeConfig(
        mode="generate",
        base_prompt=SQL_GENERATOR_PROMPT_GENERATE,
        uses_tools=False,
    ),
    # 语义模式
    (False, "query"): ModeConfig(
        mode="query",
        base_prompt=SQL_GENERATOR_PROMPT_SEMANTIC_QUERY,
        uses_tools=True,
    ),
    (True, "generate"): ModeConfig(
        mode="generate",
        base_prompt=SQL_GENERATOR_PROMPT_GENERATE,
        uses_tools=False,
    ),
    (True, "query"): ModeConfig(
        mode="query",
        base_prompt=SQL_GENERATOR_PROMPT_SEMANTIC_QUERY,
        uses_tools=True,
    ),
}


def get_mode_config(has_semantic: bool, mode: str) -> ModeConfig:
    """
    获取模式配置

    Args:
        has_semantic: 是否启用语义模式
        mode: 模式名称 ("generate" 或 "query")

    Returns:
        ModeConfig 实例

    Raises:
        KeyError: 如果模式不存在
    """
    key = (has_semantic, mode)
    if key not in MODE_CONFIGS:
        raise ValueError(f"不支持的模式配置: has_semantic={has_semantic}, mode={mode}")
    return MODE_CONFIGS[key]


def filter_tools_by_config(tools: list, config: ModeConfig) -> list:
    """根据配置决定是否启用工具。"""
    if not config.uses_tools:
        return []
    return tools
