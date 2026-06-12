"""
Agent 模式配置

统一管理不同模式下的提示词和工具选择逻辑
"""

from dataclasses import dataclass
from .prompts import (
    SQL_GENERATOR_PROMPT_GENERATE,
    SQL_GENERATOR_PROMPT_SEMANTIC_QUERY
)
from .tools import SemanticToolNames


@dataclass
class ModeConfig:
    """
    模式配置

    Attributes:
        mode: 模式名称 ("generate" 或 "query")
        base_prompt: 基础提示词模板
        uses_tools: 是否使用工具
        semantic_tool_filter: 语义工具过滤策略
            - "generate_only": 仅保留 dsl2sql_tool
            - "execute_only": 排除 dsl2sql_tool，保留其他工具
            - None: 不过滤
    """
    mode: str
    base_prompt: str
    uses_tools: bool
    semantic_tool_filter: str | None = None


# 模式配置映射表
# 键：(是否语义模式, mode)
# 值：ModeConfig
MODE_CONFIGS = {
    # 普通 NL2SQL 模式
    (False, "generate"): ModeConfig(
        mode="generate",
        base_prompt=SQL_GENERATOR_PROMPT_GENERATE,
        uses_tools=False,
        semantic_tool_filter=None
    ),

    # 语义模式
    (True, "generate"): ModeConfig(
        mode="generate",
        base_prompt=SQL_GENERATOR_PROMPT_GENERATE,
        uses_tools=False,
        semantic_tool_filter=None
    ),
    (True, "query"): ModeConfig(
        mode="query",
        base_prompt=SQL_GENERATOR_PROMPT_SEMANTIC_QUERY,
        uses_tools=True,
        semantic_tool_filter=None  # 查询模式：需要 dsl2sql_tool + execute_sql_tool 等所有工具
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
    """
    根据配置过滤工具列表

    Args:
        tools: 所有可用工具
        config: 模式配置

    Returns:
        过滤后的工具列表
    """
    if not config.uses_tools:
        return []

    if config.semantic_tool_filter == "semantic_only":
        # 仅保留语义层工具：dsl2sql_tool + validate_sql_tool
        semantic_tool_names = {SemanticToolNames.GENERATE, SemanticToolNames.VALIDATE}
        return [t for t in tools if t.name in semantic_tool_names]
    else:
        # 使用所有工具
        return tools
