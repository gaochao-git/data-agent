"""DataAnalyze 通用分析智能体。"""

import json
from typing import Annotated, Any

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ModelFallbackMiddleware, wrap_model_call
from langgraph.config import get_stream_writer
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict

from ..chart_types import get_chart_type_prompt_text
from ..services.data_analyze_workspace import ensure_data_analyze_workspace
from ..services.llm_config import get_chat_data_llm, get_default_chat_data_model
from ..services.context_budget import ToolResultBudget
from ..services.workspace_execution import (
    SandboxExecutionSessionRef,
    attach_workspace_execution_session,
    cleanup_workspace_execution_session,
    get_workspace_execution_session,
)
from .prompts.analyze_data import (
    DATA_ANALYZE_GENERIC_DIALECT_CONTEXT,
    DATA_ANALYZE_GENERIC_DIALECT_FEWSHOT_PROMPT,
    DATA_ANALYZE_HIVE_DIALECT_CONTEXT,
    DATA_ANALYZE_HIVE_DIALECT_FEWSHOT_PROMPT,
    DATA_ANALYZE_MYSQL_DIALECT_CONTEXT,
    DATA_ANALYZE_MYSQL_DIALECT_FEWSHOT_PROMPT,
    DATA_ANALYZE_MULTI_STEP_FEWSHOT_PROMPT,
    DATA_ANALYZE_SEMANTIC_FEWSHOT_PROMPT,
    DATA_ANALYZE_SYSTEM_PROMPT_TEMPLATE,
)
from .middleware.token_usage_middleware import bind_token_usage_middleware, create_request_token_usage_middleware, invoke_with_token_usage
from .nodes import format_current_time_for_prompt, normalize_response_tool_call_names, timestamp_message
from .skills import build_builtin_skills_prompt
from .tools.common import validate_workspace_root
from .tools.dsl2sql import create_dsl2sql_tool
from .tools.exec_sql import create_exec_sql_tool
from .tools.file_edit import create_file_edit_tool
from .tools.file_read import create_file_read_tool
from .tools.present_artifacts import create_present_artifacts_tool
from .tools.sandbox_run import create_sandbox_run_tool
from .tools.retry_guard import create_tool_retry_guard
from .tools.skill_load import create_skill_load_tool
from ..services.semantic_tools import create_semantic_config_selection_tools

DATA_ANALYZE_TOOL_DEFINITIONS = [
    {"name": "skill_load", "status": "available", "description": "加载技能说明，并将技能目录复制到当前会话 runtime-skills。"},
    {"name": "file_read", "status": "available", "description": "读取 workspace、outputs、uploads、runtime-skills 中的文本文件。"},
    {"name": "file_edit", "status": "available", "description": "向 workspace 或 outputs 写入中间文件和最终产物。"},
    {"name": "present_artifacts", "status": "available", "description": "将 outputs 中已生成且已校验的最终产物显式呈现给用户。"},
    {"name": "sandbox_run", "status": "available", "description": "在当前会话沙箱中执行 ls/find/python3/node/bash 等非交互命令。"},
    {"name": "select_semantic_config_tool", "status": "available", "description": "当前没有手动选择指标集时，从数据源组指标集中选择可回答问题的指标集。"},
    {"name": "dsl2sql_tool", "status": "available", "description": "按智能问数同一套语义层规则校验单个 DSL 或批量卡片 DSL，并生成 SQL。"},
    {"name": "semantic_query_tool", "status": "available", "description": "接收语义 DSL，内部生成只读 SQL 并返回真实数据结果，用于数据分析、抽样验证和结论支撑。"},
]

def merge_task_stages(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """按阶段名合并任务阶段，保留最新状态。"""
    stages: list[dict[str, Any]] = list(existing or [])
    for item in new or []:
        stage_name = str(item.get("stage") or "").strip()
        run_id = str(item.get("run_id") or "").strip()
        if not stage_name:
            stages.append(item)
            continue
        existing_index = next((
            index
            for index, stage in enumerate(stages)
            if stage.get("stage") == stage_name and str(stage.get("run_id") or "").strip() == run_id
        ), -1)
        if existing_index >= 0:
            stages[existing_index] = {**stages[existing_index], **item}
        else:
            stages.append(item)
    return stages


def merge_reasoning_segments(existing: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """追加模型原生思考分段，供历史会话恢复。"""
    return list(existing or []) + list(new or [])


def keep_conversation_datasource_id(existing: str | None, new: str | None) -> str | None:
    """数据源组首次绑定后不可被后续轮次覆盖。"""
    return existing or new


class DataAnalyzeFlowState(TypedDict):
    """DataAnalyze 外层任务流状态。"""
    messages: Annotated[list[BaseMessage], add_messages]
    task_stages: Annotated[list[dict[str, Any]], merge_task_stages]
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
    token_usage: NotRequired[dict[str, Any] | None]
    task_run_id: str
    conversation_datasource_id: Annotated[str | None, keep_conversation_datasource_id]
    datasource_id: NotRequired[str | None]
    semantic_config_id: NotRequired[str | None]
    member_datasource_ids: NotRequired[list[str]]
    user_id: NotRequired[str | None]
    operator_name: NotRequired[str | None]
    conversation_id: NotRequired[str | None]


class DataAnalyzeAgentState(AgentState):
    """DataAnalyze 内层 Agent 状态，供工具读取当前会话上下文。"""
    datasource_id: NotRequired[str | None]
    semantic_config_id: NotRequired[str | None]
    member_datasource_ids: NotRequired[list[str]]
    user_id: NotRequired[str | None]
    operator_name: NotRequired[str | None]
    conversation_id: NotRequired[str | None]


def get_data_analyze_tool_chain() -> list[dict[str, str]]:
    """返回 DataAnalyze 默认可用工具元信息，供提示词和上下文统一引用。"""
    return [dict(item) for item in DATA_ANALYZE_TOOL_DEFINITIONS]


def get_data_analyze_allowed_tool_names_text() -> str:
    """返回提示词中使用的默认可调用工具名列表。"""
    return "、".join(f"`{item['name']}`" for item in DATA_ANALYZE_TOOL_DEFINITIONS)


def _extract_source_type(data_context: dict[str, Any]) -> str:
    """从数据上下文提取当前方言类型。"""
    datasource = data_context.get("datasource") if isinstance(data_context, dict) else {}
    source_type = str((datasource or {}).get("source_type") or "").strip().lower()
    if source_type:
        return source_type
    semantic_configs = data_context.get("semantic_configs") if isinstance(data_context, dict) else []
    if isinstance(semantic_configs, list):
        for config in semantic_configs:
            if not isinstance(config, dict):
                continue
            source_type = str(config.get("source_type") or "").strip().lower()
            if source_type:
                return source_type
    return ""


def build_dialect_prompt_sections(data_context: dict[str, Any]) -> tuple[str, str]:
    """按 source_type 返回方言上下文和方言 few-shot。"""
    source_type = _extract_source_type(data_context)
    if source_type == "hive":
        return DATA_ANALYZE_HIVE_DIALECT_CONTEXT, DATA_ANALYZE_HIVE_DIALECT_FEWSHOT_PROMPT
    if source_type == "mysql":
        return DATA_ANALYZE_MYSQL_DIALECT_CONTEXT, DATA_ANALYZE_MYSQL_DIALECT_FEWSHOT_PROMPT
    return DATA_ANALYZE_GENERIC_DIALECT_CONTEXT, DATA_ANALYZE_GENERIC_DIALECT_FEWSHOT_PROMPT


def create_data_analyze_tools(
    workspace_root: str,
    *,
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    conversation_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    user_id: str = "system",
    operator_name: str | None = None,
    execution_session_ref: SandboxExecutionSessionRef | None = None,
    model_id: str | None = None,
    tool_result_budget: ToolResultBudget | None = None,
) -> list[Any]:
    """在 DataAnalyzeAgent 内组装当前允许模型调用的工具。"""
    root = validate_workspace_root(workspace_root)
    tools = [
        create_skill_load_tool(root, user_id=user_id, execution_session_ref=execution_session_ref),
        create_file_read_tool(root, execution_session_ref),
        create_file_edit_tool(root, execution_session_ref),
        create_present_artifacts_tool(execution_session_ref),
        create_sandbox_run_tool(root, execution_session_ref),
        *create_semantic_config_selection_tools(
            model_id=model_id,
            include_query_tools=False,
            datasource_id=datasource_id,
            user_id=user_id,
            tool_result_budget=tool_result_budget,
        ),
        create_dsl2sql_tool(datasource_id, semantic_config_id, user_id=user_id, operator_name=operator_name),
        create_exec_sql_tool(
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            member_datasource_ids=member_datasource_ids,
            user_id=user_id,
            operator_name=operator_name,
            workspace_root=root,
            execution_session_ref=execution_session_ref,
            conversation_id=conversation_id,
        ),
    ]
    return create_tool_retry_guard(tools)


def build_data_analyze_system_prompt(
    *,
    skill: dict[str, Any] | None,
    data_context: dict[str, Any],
    workspace_root: str,
    data_context_file: str = "",
    available_skills_xml: str | None = None,
) -> str:
    """构造通用分析智能体系统提示词。"""
    data_context_text = json.dumps(data_context or {}, ensure_ascii=False, indent=2)
    if available_skills_xml is None:
        available_skills_xml = build_builtin_skills_prompt()
    dialect_context_text, dialect_fewshot = build_dialect_prompt_sections(data_context or {})

    _ = data_context_file

    return DATA_ANALYZE_SYSTEM_PROMPT_TEMPLATE.format(
        current_time=format_current_time_for_prompt(),
        workspace_root=workspace_root or "未初始化",
        data_context_text=data_context_text,
        available_skills_xml=available_skills_xml,
        chart_types_text=get_chart_type_prompt_text(),
        dialect_context_text=dialect_context_text,
        semantic_fewshot=DATA_ANALYZE_SEMANTIC_FEWSHOT_PROMPT,
        dialect_fewshot=dialect_fewshot,
        multi_step_fewshot=DATA_ANALYZE_MULTI_STEP_FEWSHOT_PROMPT,
    )


def create_tool_call_name_normalizer_middleware(tools: list[Any]):
    """创建模型工具调用名称归一化 middleware。"""
    @wrap_model_call(name="ToolCallNameNormalizerMiddleware")
    async def normalize_tool_call_name_middleware(request, handler):
        response = await handler(request)
        if isinstance(response, AIMessage):
            return normalize_response_tool_call_names(response, tools)
        result = list(getattr(response, "result", []) or [])
        if not result:
            return response
        normalized_result = [
            normalize_response_tool_call_names(message, tools)
            for message in result
        ]
        response.result = normalized_result
        return response

    return normalize_tool_call_name_middleware


def create_data_analyze_agent(
    *,
    workspace_root: str,
    system_prompt: str,
    model_id: str | None = None,
    checkpointer=None,
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    conversation_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    user_id: str = "system",
    operator_name: str | None = None,
    execution_session_ref: SandboxExecutionSessionRef | None = None,
    enable_thinking: bool | None = None,
):
    """创建 LangChain 通用分析 agent。"""
    llm: BaseChatModel = get_chat_data_llm(
        model=model_id,
        streaming=True,
        stream_usage=True,
        enable_thinking=enable_thinking,
    )
    tool_result_budget = ToolResultBudget(model_id)
    tools = create_data_analyze_tools(
        workspace_root,
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        conversation_id=conversation_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        operator_name=operator_name,
        execution_session_ref=execution_session_ref,
        model_id=model_id,
        tool_result_budget=tool_result_budget,
    )
    token_usage_middleware = create_request_token_usage_middleware(model_id, tool_result_budget=tool_result_budget)
    middleware = []
    fallback_model = get_default_chat_data_model()
    if model_id and fallback_model and fallback_model != model_id:
        fallback_llm = get_chat_data_llm(
            model=fallback_model,
            streaming=True,
            stream_usage=True,
            enable_thinking=enable_thinking,
        )
        middleware.append(ModelFallbackMiddleware(fallback_llm))
    middleware.extend([token_usage_middleware, create_tool_call_name_normalizer_middleware(tools)])
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        state_schema=DataAnalyzeAgentState,
        checkpointer=checkpointer,
        name="data_analyze_agent",
    )
    bind_token_usage_middleware(agent, token_usage_middleware)
    return agent


async def execute_data_analyze_agent(
    *,
    agent,
    state: DataAnalyzeFlowState,
    system_prompt: str,
    model_id: str | None,
) -> dict[str, Any]:
    result, token_usage = await invoke_with_token_usage(
        agent=agent,
        state=state,
        system_prompt=system_prompt,
        model_id=model_id,
    )
    get_stream_writer()({"type": "token_usage", "usage": token_usage})
    if isinstance(result, dict):
        payload = dict(result)
        payload["token_usage"] = token_usage
        messages = payload.get("messages")
        if isinstance(messages, list):
            payload["messages"] = [
                timestamp_message(message) if getattr(message, "type", None) == "ai" else message
                for message in messages
            ]
        return payload
    return {"messages": [timestamp_message(result)], "token_usage": token_usage}


def create_data_analyze_flow_graph(
    *,
    workspace_root: str,
    system_prompt: str,
    model_id: str | None = None,
    checkpointer=None,
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    conversation_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    user_id: str = "system",
    operator_name: str | None = None,
    enable_thinking: bool | None = None,
):
    """创建单层 DataAnalyze 执行图，保留外层 state 兼容历史 checkpoint。"""
    execution_session_ref = SandboxExecutionSessionRef()
    execute_agent = create_data_analyze_agent(
        workspace_root=workspace_root,
        system_prompt=system_prompt,
        model_id=model_id,
        checkpointer=None,
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        conversation_id=conversation_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        operator_name=operator_name,
        execution_session_ref=execution_session_ref,
        enable_thinking=enable_thinking,
    )

    async def execute_node(state: DataAnalyzeFlowState) -> dict[str, Any]:
        if execution_session_ref.current is None:
            execution_session_ref.current = await get_workspace_execution_session(workspace_root, user_id)
        try:
            payload = await execute_data_analyze_agent(
                agent=execute_agent,
                state=state,
                system_prompt=system_prompt,
                model_id=model_id,
            )
        except Exception:
            await cleanup_workspace_execution_session(execution_session_ref.current)
            execution_session_ref.current = None
            raise
        finally:
            if execution_session_ref.current is not None:
                await attach_workspace_execution_session(execution_session_ref.current, user_id)
                execution_session_ref.current = None

        return payload

    builder = StateGraph(DataAnalyzeFlowState)
    builder.add_node("execute", execute_node)
    builder.add_edge(START, "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=checkpointer)


def get_data_analyze_graph(checkpointer=None):
    """创建用于读取 DataAnalyze checkpoint 历史的 graph。"""
    workspace_paths = ensure_data_analyze_workspace("_history_reader")
    system_prompt = build_data_analyze_system_prompt(
        skill=None,
        data_context={},
        workspace_root=workspace_paths["root"],
        data_context_file="",
    )
    return create_data_analyze_flow_graph(
        workspace_root=workspace_paths["root"],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
