"""System prompt builder for the ChatData query agent."""

from typing import Any

from .chat_data import SQL_GENERATOR_PROMPT_SEMANTIC_QUERY
from .semantic_layer import build_semantic_prompt


def format_semantic_config_directory_info_for_llm(directory_info: str) -> str:
    """格式化当前可见指标集轻量目录，供 system prompt 注入。"""
    text = str(directory_info or "").strip()
    if not text:
        return ""
    return f"\n{text}\n"


def format_datasource_group_context_for_llm(context: str) -> str:
    """格式化数据源组智能问数说明，供 system prompt 注入。"""
    text = str(context or "").strip()
    if not text:
        return ""
    return f"\n## 数据源组智能问数说明\n{text}\n"


def build_semantic_layer_context(semantic_config: dict | None) -> str:
    """构建统一语义层上下文。"""
    if not semantic_config:
        return (
            "## 当前指标集\n"
            "本轮未提供完整指标集。业务数据查询或具体指标/维度询问先搜索当前数据源组中的真实指标集；指标集目录或业务范围概览可直接基于“当前可见指标集目录”回答。未选中完整指标集前，不要推荐具体查数样例，只引导用户先查看或选择指标集。搜索后仍未找到、候选不唯一或口径不清时再澄清。\n"
        )

    config_id = str(semantic_config.get("id") or "").strip()
    config_name = str(semantic_config.get("name") or semantic_config.get("config_name") or config_id or "").strip()
    header = "## 当前指标集\n当前已选择指标集"
    if config_name:
        header += f"：{config_name}"
    if config_id:
        header += f"（semantic_config_id: {config_id}）"
    header += (
        "。业务数据查询不要调用 `select_semantic_config_tool`。\n"
        "优先使用数据源组说明和当前指标集理解用户问题；只有用户本轮显式指定业务知识技能时，才读取对应技能补充查询判断；最终回答不要提及技能加载过程。\n\n"
    )
    return header + build_semantic_prompt(semantic_config, "query")


def build_chat_data_system_prompt(
    prompt_context: dict[str, Any],
    *,
    semantic_config: dict | None = None,
    available_skills_context: str | None = None,
) -> str:
    """按共享提示词上下文构造智能问数 system prompt。"""
    prompt_params = {
        "datasource_group_context": format_datasource_group_context_for_llm(prompt_context.get("datasource_group_context")),
        "available_skills_context": available_skills_context or "",
        "semantic_config_directory_info": format_semantic_config_directory_info_for_llm(prompt_context.get("semantic_config_directory_info")),
        "semantic_layer_config": build_semantic_layer_context(semantic_config),
    }
    return SQL_GENERATOR_PROMPT_SEMANTIC_QUERY.format(**prompt_params)
