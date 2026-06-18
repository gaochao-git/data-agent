"""DataAnalyze 通用分析智能体。"""

from datetime import datetime
import json
from typing import Annotated, Any

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import ModelFallbackMiddleware
from langgraph.config import get_stream_writer
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict

from ..chart_types import get_chart_type_prompt_text
from ..services.data_analyze_workspace import ensure_data_analyze_workspace
from ..services.llm_config import get_chat_data_llm, get_default_chat_data_model
from ..services.workspace_execution import (
    SandboxExecutionSessionRef,
    attach_workspace_execution_session,
    cleanup_workspace_execution_session,
    get_workspace_execution_session,
)
from .prompts.analyze_data import (
    DATA_ANALYZE_DEFAULT_PLAN_STEPS,
    DATA_ANALYZE_DONE_PROMPT,
    DATA_ANALYZE_GENERIC_DIALECT_CONTEXT,
    DATA_ANALYZE_GENERIC_DIALECT_FEWSHOT_PROMPT,
    DATA_ANALYZE_HIVE_DIALECT_CONTEXT,
    DATA_ANALYZE_HIVE_DIALECT_FEWSHOT_PROMPT,
    DATA_ANALYZE_MYSQL_DIALECT_CONTEXT,
    DATA_ANALYZE_MYSQL_DIALECT_FEWSHOT_PROMPT,
    DATA_ANALYZE_MULTI_STEP_FEWSHOT_PROMPT,
    DATA_ANALYZE_PLAN_PROMPT,
    DATA_ANALYZE_SEMANTIC_FEWSHOT_PROMPT,
    DATA_ANALYZE_SYSTEM_PROMPT_TEMPLATE,
    DATA_ANALYZE_THINKING_PROMPT,
)
from .middleware.token_usage_middleware import bind_token_usage_middleware, create_request_token_usage_middleware, invoke_with_token_usage
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
    {"name": "semantic_query_tool", "status": "available", "description": "执行只读 SQL 查询，返回真实数据结果，用于数据分析、抽样验证和结论支撑。"},
]
MAX_STAGE_HISTORY_MESSAGES = 10
MAX_STAGE_HISTORY_CHARS = 4000
MAX_STAGE_HISTORY_MESSAGE_CHARS = 800


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


class DataAnalyzeFlowState(TypedDict):
    """DataAnalyze 外层任务流状态。"""
    messages: Annotated[list[BaseMessage], add_messages]
    task_stages: Annotated[list[dict[str, Any]], merge_task_stages]
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
    task_run_id: str
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


def _latest_user_query(messages: list[BaseMessage]) -> str:
    """提取最近一条用户消息。"""
    for message in reversed(messages or []):
        if getattr(message, "type", None) == "human":
            return str(getattr(message, "content", "") or "")
    return ""


def _message_text(content: Any) -> str:
    """提取消息中的可读文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content or "")


def _truncate_text(text: str, max_chars: int) -> str:
    """截断过长文本，控制阶段节点上下文大小。"""
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars]}..."


def _format_stage_conversation_context(messages: list[BaseMessage], *, max_messages: int = MAX_STAGE_HISTORY_MESSAGES, max_chars: int = MAX_STAGE_HISTORY_CHARS) -> str:
    """格式化最近对话，供理解和规划节点感知上下文。"""
    items: list[str] = []
    for message in messages or []:
        if isinstance(message, HumanMessage):
            role = "用户"
        elif isinstance(message, AIMessage):
            role = "助手"
        else:
            continue
        text = _truncate_text(_message_text(getattr(message, "content", "")), MAX_STAGE_HISTORY_MESSAGE_CHARS)
        if text:
            items.append(f"{role}：{text}")
    if not items:
        return ""
    content = "\n".join(items[-max_messages:])
    return _truncate_text(content, max_chars)


def _build_stage_user_prompt(messages: list[BaseMessage], current_query: str, extra: str = "") -> str:
    """构造阶段节点用户输入，包含最近历史和当前目标。"""
    history = _format_stage_conversation_context(messages)
    sections: list[str] = []
    if history:
        sections.append(f"最近历史会话：\n{history}")
    sections.append(f"当前用户目标：\n{current_query}")
    if extra:
        sections.append(extra)
    return "\n\n".join(sections)


def _task_run_id(state: DataAnalyzeFlowState) -> str:
    """获取当前任务阶段运行批次。"""
    return str(state.get("task_run_id") or "").strip()


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
) -> list[Any]:
    """在 DataAnalyzeAgent 内组装当前允许模型调用的工具。"""
    root = validate_workspace_root(workspace_root)
    tools = [
        create_skill_load_tool(root, user_id=user_id, execution_session_ref=execution_session_ref),
        create_file_read_tool(root, execution_session_ref),
        create_file_edit_tool(root, execution_session_ref),
        create_present_artifacts_tool(execution_session_ref),
        create_sandbox_run_tool(root, execution_session_ref),
        *create_semantic_config_selection_tools(model_id=model_id, include_query_tools=False, datasource_id=datasource_id),
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
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        workspace_root=workspace_root or "未初始化",
        data_context_text=data_context_text,
        available_skills_xml=available_skills_xml,
        chart_types_text=get_chart_type_prompt_text(),
        dialect_context_text=dialect_context_text,
        semantic_fewshot=DATA_ANALYZE_SEMANTIC_FEWSHOT_PROMPT,
        dialect_fewshot=dialect_fewshot,
        multi_step_fewshot=DATA_ANALYZE_MULTI_STEP_FEWSHOT_PROMPT,
    )


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
        temperature=0.2,
        enable_thinking=enable_thinking,
    )
    token_usage_middleware = create_request_token_usage_middleware(model_id)
    middleware = [token_usage_middleware]
    fallback_model = get_default_chat_data_model()
    if model_id and fallback_model and fallback_model != model_id:
        fallback_llm = get_chat_data_llm(
            model=fallback_model,
            streaming=True,
            stream_usage=True,
            temperature=0.2,
            enable_thinking=enable_thinking,
        )
        middleware.append(ModelFallbackMiddleware(fallback_llm))
    agent = create_agent(
        model=llm,
        tools=create_data_analyze_tools(
            workspace_root,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            conversation_id=conversation_id,
            member_datasource_ids=member_datasource_ids,
            user_id=user_id,
            operator_name=operator_name,
            execution_session_ref=execution_session_ref,
            model_id=model_id,
        ),
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
        return dict(result)
    return {"messages": [result]}


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
    """创建 thinking -> plan -> execute -> done 的 DataAnalyze 外层 LangGraph。"""
    flow_llm: BaseChatModel = get_chat_data_llm(
        model=model_id,
        streaming=True,
        stream_usage=True,
        temperature=0.1,
        enable_thinking=enable_thinking,
    )
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

    async def thinking_node(state: DataAnalyzeFlowState) -> dict[str, Any]:
        messages = state.get("messages", [])
        query = _latest_user_query(messages)
        response = await flow_llm.ainvoke([
            SystemMessage(content=DATA_ANALYZE_THINKING_PROMPT),
            HumanMessage(content=_build_stage_user_prompt(messages, query)),
        ])
        return {
            "task_stages": [{
                "stage": "thinking",
                "run_id": _task_run_id(state),
                "status": "completed",
                "title": "理解任务",
                "content": str(response.content or "").strip(),
            }],
        }

    async def plan_node(state: DataAnalyzeFlowState) -> dict[str, Any]:
        messages = state.get("messages", [])
        query = _latest_user_query(messages)
        run_id = _task_run_id(state)
        thinking = next((
            item
            for item in state.get("task_stages", [])
            if item.get("stage") == "thinking" and str(item.get("run_id") or "").strip() == run_id
        ), {})
        response = await flow_llm.ainvoke([
            SystemMessage(content=DATA_ANALYZE_PLAN_PROMPT),
            HumanMessage(content=_build_stage_user_prompt(messages, query, f"理解摘要：\n{thinking.get('content') or ''}")),
        ])
        steps = [line.strip(" -0123456789.、\t") for line in str(response.content or "").splitlines()]
        steps = [line for line in steps if line][:5]
        return {
            "task_stages": [{
                "stage": "plan",
                "run_id": run_id,
                "status": "completed",
                "title": "规划步骤",
                "content": "",
                "steps": steps or DATA_ANALYZE_DEFAULT_PLAN_STEPS,
            }],
        }

    async def execute_start_node(state: DataAnalyzeFlowState) -> dict[str, Any]:
        if execution_session_ref.current is None:
            execution_session_ref.current = await get_workspace_execution_session(workspace_root, user_id)
        return {
            "task_stages": [{
                "stage": "execute",
                "run_id": _task_run_id(state),
                "status": "running",
                "title": "任务执行",
                "content": "",
            }]
        }

    async def execute_node(state: DataAnalyzeFlowState) -> dict[str, Any]:
        try:
            return await execute_data_analyze_agent(
                agent=execute_agent,
                state=state,
                system_prompt=system_prompt,
                model_id=model_id,
            )
        except Exception:
            await cleanup_workspace_execution_session(execution_session_ref.current)
            execution_session_ref.current = None
            raise

    async def done_node(state: DataAnalyzeFlowState) -> dict[str, Any]:
        query = _latest_user_query(state.get("messages", []))
        response = await flow_llm.ainvoke([
            SystemMessage(content=DATA_ANALYZE_DONE_PROMPT),
            HumanMessage(content=query),
        ])
        try:
            return {
                "task_stages": [
                    {
                        "stage": "execute",
                        "run_id": _task_run_id(state),
                        "status": "completed",
                        "title": "任务执行",
                        "content": "",
                    },
                    {
                        "stage": "done",
                        "run_id": _task_run_id(state),
                        "status": "completed",
                        "title": "完成",
                        "content": str(response.content or "").strip() or "已完成本次智能分析任务。",
                    },
                ],
            }
        finally:
            await attach_workspace_execution_session(execution_session_ref.current, user_id)
            execution_session_ref.current = None

    builder = StateGraph(DataAnalyzeFlowState)
    builder.add_node("thinking", thinking_node)
    builder.add_node("plan", plan_node)
    builder.add_node("execute_start", execute_start_node)
    builder.add_node("execute", execute_node)
    builder.add_node("done", done_node)
    builder.add_edge(START, "thinking")
    builder.add_edge("thinking", "plan")
    builder.add_edge("plan", "execute_start")
    builder.add_edge("execute_start", "execute")
    builder.add_edge("execute", "done")
    builder.add_edge("done", END)
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
