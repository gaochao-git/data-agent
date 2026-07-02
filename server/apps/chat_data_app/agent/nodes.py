"""智能问数主图节点定义。"""

from datetime import datetime
from typing import Any, Callable

from langchain_core.messages import SystemMessage

from utils.logger import get_logger
from ..services.llm_config import get_chat_data_llm, get_default_chat_data_model
from .middleware.token_usage_middleware import build_request_breakdown, build_token_usage_payload, count_messages_tokens, count_tools_tokens, get_context_window
from .prompts.chat_data import SQL_GENERATOR_PROMPT_SEMANTIC_QUERY
from .state import ChatDataAgentState
from .tools import TOOLS

logger = get_logger(__name__)


def format_current_time_for_prompt(current_time: datetime | None = None) -> str:
    """格式化提示词中的当前时间，附带周几信息。"""
    current = current_time or datetime.now()
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekdays[current.weekday()]
    return f"{current.strftime('%Y-%m-%d %H:%M:%S')}（{weekday}）"


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


def _get_prompt_context_value(state: ChatDataAgentState, key: str) -> str:
    """读取提示词上下文，兼容旧 checkpoint 的顶层字段。"""
    prompt_context = state.get("prompt_context")
    if isinstance(prompt_context, dict):
        value = prompt_context.get(key)
        if value:
            return str(value)
    return str(state.get(key) or "")


def timestamp_message(message: Any, timestamp: str | None = None) -> Any:
    """给 LangChain 消息补充服务端时间戳，随 checkpoint 一起持久化。"""
    if not message:
        return message
    additional_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
    additional_kwargs["timestamp"] = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        message.additional_kwargs = additional_kwargs
    except Exception:
        logger.warning("消息时间戳写入失败: message=%r", message, exc_info=True)
    return message


def _get_available_tool_names(tools: list) -> set[str]:
    """提取当前可用工具名称。"""
    return {
        str(name)
        for item in tools or []
        if (name := getattr(item, "name", None) or (item if isinstance(item, str) else None))
    }


def _normalize_repeated_tool_name(tool_name: Any, available_tool_names: set[str]) -> str:
    """修复 OpenAI-compatible 流式工具调用中偶发的重复工具名。"""
    raw_name = str(tool_name or "").strip()
    if not raw_name or raw_name in available_tool_names:
        return raw_name

    for name in available_tool_names:
        repeat_count, remainder = divmod(len(raw_name), len(name))
        if repeat_count > 1 and remainder == 0 and raw_name == name * repeat_count:
            return name

    return raw_name


def normalize_response_tool_call_names(message: Any, tools: list) -> Any:
    """在 ToolNode 执行前归一化明显重复的已注册工具名。"""
    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        return message

    available_tool_names = _get_available_tool_names(tools)
    if not available_tool_names:
        return message

    changed = False
    normalized_tool_calls = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            normalized_tool_calls.append(tool_call)
            continue
        raw_name = tool_call.get("name")
        normalized_name = _normalize_repeated_tool_name(raw_name, available_tool_names)
        if normalized_name != raw_name:
            tool_call = {**tool_call, "name": normalized_name}
            changed = True
        normalized_tool_calls.append(tool_call)

    if not changed:
        return message

    logger.warning("已归一化重复工具调用名称，避免 OpenAI-compatible 流式 chunk 合并异常")
    try:
        message.tool_calls = normalized_tool_calls
        return message
    except Exception:
        if hasattr(message, "model_copy"):
            return message.model_copy(update={"tool_calls": normalized_tool_calls})
        logger.warning("工具调用名称归一化写入失败: message=%r", message, exc_info=True)
        return message


def build_semantic_layer_context(semantic_config: dict | None) -> str:
    """构建统一语义层上下文。"""
    if not semantic_config:
        return (
            "## 当前指标集\n"
            "本轮未提供完整指标集。业务数据查询或具体指标/维度询问先搜索当前数据源组中的真实指标集；指标集目录或业务范围概览可直接基于“当前可见指标集目录”回答。搜索后仍未找到、候选不唯一或口径不清时再澄清。\n"
        )
    from .tools import build_semantic_prompt
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


def create_agent_node(
    tools: list,
    semantic_config: dict | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
    available_skills_context: str | None = None,
) -> Callable:
    """
    创建 agent 节点的工厂函数

    Args:
        tools: 可用工具列表
        semantic_config: 语义配置（可选，传入时在 system prompt 中注入当前指标集信息）

    Returns:
        agent 节点函数
    """
    async def agent_node(state: ChatDataAgentState) -> dict[str, Any]:
        """
        Agent 节点 - 调用 LLM 生成响应（可能包含工具调用）
        """
        logger.info("Agent 处理用户请求")

        messages = list(state["messages"])
        active_tools = tools

        # 当前时间
        current_time = format_current_time_for_prompt()

        # 构建提示词参数
        prompt_params = {
            "current_time": current_time,
            "datasource_group_context": format_datasource_group_context_for_llm(_get_prompt_context_value(state, "datasource_group_context")),
            "available_skills_context": available_skills_context or "",
            "semantic_config_directory_info": format_semantic_config_directory_info_for_llm(_get_prompt_context_value(state, "semantic_config_directory_info")),
        }

        prompt_params["semantic_layer_config"] = build_semantic_layer_context(semantic_config)

        # 格式化提示词
        system_prompt = SQL_GENERATOR_PROMPT_SEMANTIC_QUERY.format(**prompt_params)

        prompt_length = len(system_prompt)
        logger.info(f"[agent] system_prompt 长度: {prompt_length} 字符, semantic={bool(semantic_config)}")
        # logger.info(f"[agent] runtime system_prompt (semantic={bool(semantic_config)}):\n{system_prompt}")

        # 构建消息列表
        llm_messages = [SystemMessage(content=system_prompt)] + messages

        # 调用 LLM（指定模型失败时回退默认模型）
        llm = get_chat_data_llm(model=model_id, streaming=True, enable_thinking=enable_thinking)

        # 统计上下文 tokens
        message_tokens = count_messages_tokens(llm_messages)
        tool_tokens = count_tools_tokens(active_tools)
        input_tokens = message_tokens + tool_tokens
        context_window = get_context_window(model_id)
        token_usage = build_token_usage_payload(used=input_tokens, total=context_window)
        token_usage.update({
            "input_tokens": input_tokens,
            "current_input_tokens": input_tokens,
            "output_tokens": 0,
            "total_tokens": input_tokens,
            "context_window": context_window,
            "estimated": True,
            "breakdown": build_request_breakdown(
                messages=llm_messages,
                message_tokens=message_tokens,
                tools=active_tools,
                tools_tokens=tool_tokens,
                overhead_tokens=0,
            ),
        })
        try:
            if active_tools:
                response = await llm.bind_tools(active_tools).ainvoke(llm_messages)
            else:
                response = await llm.ainvoke(llm_messages)
            response = normalize_response_tool_call_names(response, active_tools)
        except Exception as err:
            # 仅在显式传入模型时做回退，避免掩盖其他异常
            if not model_id:
                raise
            fallback_model = get_default_chat_data_model()
            logger.warning(f"[agent] 指定模型调用失败，回退默认模型: model_id={model_id}, fallback={fallback_model}, err={err}")
            llm = get_chat_data_llm(model=fallback_model, streaming=True, enable_thinking=enable_thinking)
            if active_tools:
                response = await llm.bind_tools(active_tools).ainvoke(llm_messages)
            else:
                response = await llm.ainvoke(llm_messages)
            response = normalize_response_tool_call_names(response, active_tools)

        return {"messages": [timestamp_message(response)], "token_usage": token_usage}

    return agent_node


# 默认 agent 节点（向后兼容）
agent_node = create_agent_node(TOOLS)
