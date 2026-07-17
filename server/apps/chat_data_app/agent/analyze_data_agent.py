"""DataAnalyze 通用分析智能体。"""

import json
from typing import Annotated, Any

from langchain.agents import AgentState, create_agent
from typing_extensions import NotRequired

from ..chart_types import get_chart_type_prompt_text
from ..services.data_analyze_workspace import ensure_data_analyze_workspace
from ..services.llm_config import get_chat_data_llm
from ..services.context_budget import ToolResultBudget
from ..services.workspace_execution import SandboxExecutionSessionRef
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
from .agent_runtime import (
    build_standard_agent_middleware,
    create_semantic_selection_tools_bundle,
    create_streaming_chat_model,
    format_current_time_for_prompt,
)
from .middleware.context_compression import create_context_compression_middleware
from .middleware.context_usage import bind_context_usage_middleware, create_request_context_usage_middleware
from .chat_data_agent import keep_conversation_datasource_id, merge_reasoning_segments
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


class DataAnalyzeAgentState(AgentState, total=False):
    """DataAnalyze 单 Agent 状态，供工具、历史和前端恢复读取。"""
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
    token_usage: NotRequired[dict[str, Any] | None]
    context_compression: NotRequired[dict[str, Any] | None]
    conversation_datasource_id: Annotated[str | None, keep_conversation_datasource_id]
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


def _format_data_context_for_prompt(data_context: dict[str, Any]) -> str:
    """格式化注入系统提示词的数据上下文。"""
    if not isinstance(data_context, dict):
        return "{}"
    semantic_config_directory_info = str(data_context.get("semantic_config_directory_info") or "").strip()
    semantic_configs = data_context.get("semantic_configs")
    if semantic_config_directory_info and not semantic_configs:
        return "\n".join([
            semantic_config_directory_info,
            "",
            "## 当前指标集",
            "本轮未提供完整指标集。业务数据分析或具体指标/维度询问先搜索当前数据源组中的真实指标集；指标集目录或业务范围概览可直接基于“当前可见指标集目录”回答。未选中完整指标集前，不要推荐具体查数样例，只引导用户先查看或选择指标集。搜索后仍未找到、候选不唯一或口径不清时再澄清。",
        ])
    return json.dumps(data_context or {}, ensure_ascii=False, indent=2)


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
        *create_semantic_selection_tools_bundle(
            model_id=model_id,
            datasource_id=datasource_id,
            include_query_tools=False,
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
    data_context_text = _format_data_context_for_prompt(data_context or {})
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
    llm = create_streaming_chat_model(
        model_id=model_id,
        stream_usage=True,
        enable_thinking=enable_thinking,
        llm_factory=get_chat_data_llm,
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
    context_usage_middleware = create_request_context_usage_middleware(
        model_id,
        tools=tools,
        tool_result_budget=tool_result_budget,
        state_schema=DataAnalyzeAgentState,
    )
    context_compression_middleware = create_context_compression_middleware(
        system_prompt=system_prompt,
        tools=tools,
        model_id=model_id,
        tool_result_budget=tool_result_budget,
        state_schema=DataAnalyzeAgentState,
    )
    middleware = build_standard_agent_middleware(
        tools,
        context_compression_middleware=context_compression_middleware,
        context_usage_middleware=context_usage_middleware,
        include_response_timestamp=True,
        include_tool_call_normalizer=True,
    )
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        state_schema=DataAnalyzeAgentState,
        checkpointer=checkpointer,
        name="data_analyze_agent",
    )
    bind_context_usage_middleware(agent, context_usage_middleware)
    return agent


def get_data_analyze_graph(checkpointer=None):
    """创建用于读取 DataAnalyze checkpoint 历史的单 Agent。"""
    workspace_paths = ensure_data_analyze_workspace("_history_reader")
    system_prompt = build_data_analyze_system_prompt(
        skill=None,
        data_context={},
        workspace_root=workspace_paths["root"],
        data_context_file="",
    )
    return create_data_analyze_agent(
        workspace_root=workspace_paths["root"],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )
