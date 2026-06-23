"""
SQL Generator 节点定义
"""

from datetime import datetime
from typing import Any, Callable

from langchain_core.messages import SystemMessage

from utils.logger import get_logger
from ..services.llm_config import get_chat_data_llm, get_default_chat_data_model
from .middleware.token_usage_middleware import build_request_breakdown, build_token_usage_payload, count_messages_tokens, count_tools_tokens, get_context_window
from .config import get_mode_config, filter_tools_by_config
from .state import SqlGeneratorState, QueryResultIndex
from .tools import TOOLS

logger = get_logger(__name__)


def format_query_results_for_llm(query_results: list[QueryResultIndex]) -> str:
    """将查询结果索引格式化为 LLM 可读的文本"""
    if not query_results:
        return ""

    lines = ["\n## 可用的查询结果\n"]
    for i, qr in enumerate(query_results, 1):
        lines.append(f"### 结果 {i}")
        lines.append(f"result_id: `{qr['id']}`")
        lines.append(f"SQL: `{qr['sql'][:100]}{'...' if len(qr['sql']) > 100 else ''}`")
        lines.append(f"列: {', '.join(qr['columns'])}")
        lines.append(f"行数: {qr['row_count']}\n")

    return "\n".join(lines)


def format_execution_target_info_for_llm(execution_target_info: str) -> str:
    """格式化执行对象信息，供 system prompt 注入。"""
    text = str(execution_target_info or "").strip()
    if not text:
        return ""
    return f"\n{text}\n"


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
    """在工具执行前归一化明显重复的已注册工具名。"""
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


def build_semantic_layer_context(semantic_config: dict | None, mode: str) -> str:
    """构建统一语义层上下文。"""
    if mode != "query":
        return ""
    if not semantic_config:
        return "## 当前指标集\n当前未选择指标集。业务数据查询必须先调用 `select_semantic_config_tool`。\n"
    from .tools import build_semantic_prompt
    config_id = str(semantic_config.get("id") or "").strip()
    config_name = str(semantic_config.get("name") or semantic_config.get("config_name") or config_id or "").strip()
    header = "## 当前指标集\n当前已选择指标集"
    if config_name:
        header += f"：{config_name}"
    if config_id:
        header += f"（semantic_config_id: {config_id}）"
    header += "。业务数据查询不要调用 `select_semantic_config_tool`。\n\n"
    return header + build_semantic_prompt(semantic_config, mode)


def create_agent_node(
    tools: list,
    semantic_config: dict | None = None,
    model_id: str | None = None,
    enable_thinking: bool | None = None,
) -> Callable:
    """
    创建 agent 节点的工厂函数

    Args:
        tools: 可用工具列表
        semantic_config: 语义配置（可选，语义模式时根据 mode 动态生成提示词）

    Returns:
        agent 节点函数
    """
    async def agent_node(state: SqlGeneratorState) -> dict[str, Any]:
        """
        Agent 节点 - 调用 LLM 生成响应（可能包含工具调用）
        """
        logger.info("Agent 处理用户请求")

        messages = list(state["messages"])
        tables_ddl_info = state.get("tables_ddl_info") or ""
        query_results = state.get("query_results") or []
        mode = state.get("mode", "generate")  # 默认为 generate 模式

        # 获取模式配置
        config = get_mode_config(has_semantic=bool(semantic_config), mode=mode)

        # 根据配置过滤工具
        active_tools = filter_tools_by_config(tools, config)

        # 当前时间
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 构建提示词参数
        prompt_params = {
            "tables_ddl_info": tables_ddl_info,
            "current_time": current_time,
            "execution_target_info": format_execution_target_info_for_llm(state.get("execution_target_info") or ""),
        }

        prompt_params["semantic_layer_config"] = build_semantic_layer_context(semantic_config, mode)

        # 格式化提示词
        system_prompt = config.base_prompt.format(**prompt_params)

        # 如果有查询结果，追加到提示词（仅 query 模式有效）
        if query_results and mode == "query":
            system_prompt += format_query_results_for_llm(query_results)

        prompt_length = len(system_prompt)
        logger.info(f"[agent] system_prompt 长度: {prompt_length} 字符, mode={mode}, semantic={bool(semantic_config)}")
        # logger.info(f"[agent] runtime system_prompt (mode={mode}, semantic={bool(semantic_config)}):\n{system_prompt}")

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

        return {"messages": [response], "token_usage": token_usage}

    return agent_node


# 默认 agent 节点（向后兼容）
agent_node = create_agent_node(TOOLS)
