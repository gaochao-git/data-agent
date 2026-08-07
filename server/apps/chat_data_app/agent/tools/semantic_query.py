"""Semantic query agent tool."""

from dataclasses import dataclass
from typing import Any, Annotated

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from utils.logger import get_logger
from ...schema import SemanticQueryInput
from ...services.semantic_tools import load_runtime_semantic_config
from ...services.semantic_query_service import (
    compile_semantic_query_dsl,
    execute_semantic_query_sql,
    normalize_member_datasource_ids,
)
from ...services.semantic_sql_generator import SemanticSQLGenerator
from ..tool_results import build_internal_query_result
from .common import normalize_json_like_payload

logger = get_logger(__name__)
TOOL_ERROR_PREFIX = "semantic_query_tool 执行失败"


@dataclass(frozen=True)
class _PreparedToolRequest:
    dsl: dict[str, Any] | None
    sql: str
    has_dsl: bool
    save_path: str


@dataclass(frozen=True)
class _ResolvedToolContext:
    state: dict[str, Any]
    datasource_id: str | None
    semantic_config_id: str
    query_text: str
    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    member_datasource_ids: list[str]
    available_member_datasource_ids: list[str]
    matched_member_datasource_ids: list[str]


@dataclass(frozen=True)
class _CompiledToolQuery:
    sql: str
    query_mode: str
    confidence: float


def _tool_error(message: str) -> str:
    return f"{TOOL_ERROR_PREFIX}: {message}"


def _latest_human_query(messages: list[Any] | None) -> str:
    """提取最近一条用户消息，保持 DSL 校验和执行阶段的意图判定一致。"""
    for message in reversed(messages or []):
        if isinstance(message, HumanMessage):
            return str(getattr(message, "content", "") or "").strip()
        if getattr(message, "type", None) == "human":
            return str(getattr(message, "content", "") or "").strip()
    return ""


async def _resolve_semantic_query_config(
    state: dict,
    datasource_id: str | None,
    semantic_config_id: str | None,
) -> dict[str, Any] | None:
    """按模型显式传入的指标集 ID读取当前用户可用的运行时配置。"""
    user_id = str(state.get("user_id") or "").strip() or None
    return await load_runtime_semantic_config(datasource_id, semantic_config_id, user_id=user_id)


def _get_execution_context(state: dict | None) -> dict[str, Any]:
    """读取统一的工具执行上下文。"""
    context = (state or {}).get("execution_context")
    return context if isinstance(context, dict) else {}


def _get_datasource_id_from_state(state: dict | None) -> str | None:
    context = _get_execution_context(state)
    datasource_id = str(context.get("datasource_id") or "").strip()
    return datasource_id or None


def _get_member_scope_from_state(state: dict | None) -> tuple[list[str], list[str]]:
    context = _get_execution_context(state)
    available_member_ids = _normalize_member_datasource_ids(
        context.get("available_member_datasource_ids")
    )
    matched_member_ids = _normalize_member_datasource_ids(
        context.get("matched_member_datasource_ids")
    )
    return available_member_ids, matched_member_ids


def _get_workspace_root_from_state(state: dict | None) -> str | None:
    """读取当前会话工作区。"""
    workspace_context = (state or {}).get("workspace")
    workspace_root = (
        workspace_context.get("workspace_root")
        if isinstance(workspace_context, dict)
        else None
    )
    if not isinstance(workspace_root, str):
        return None
    workspace_root = workspace_root.strip()
    return workspace_root or None


def _prepare_tool_request(
    *,
    dsl: Any,
    sql: str | None,
    save_as: str | None,
    workspace_root: str | None,
    has_workspace: bool,
) -> _PreparedToolRequest | str:
    has_dsl = dsl is not None
    normalized_sql = str(sql or "").strip()
    has_sql = bool(normalized_sql)
    if has_dsl == has_sql:
        return _tool_error("必须且只能传入 dsl 或 sql 其中一个")
    if has_dsl:
        dsl = normalize_json_like_payload(dsl)
    if has_dsl and not isinstance(dsl, dict):
        return _tool_error("dsl 必须是对象")
    if isinstance(dsl, dict) and "sql" in dsl:
        return _tool_error("sql 必须放在顶层 sql 参数中，不要放入 dsl")
    if isinstance(dsl, dict) and isinstance(dsl.get("cards"), list):
        return _tool_error("dsl 必须是单个查询对象")

    save_path = str(save_as or "").strip()
    if save_path:
        if not has_workspace:
            return _tool_error("当前会话未初始化工作目录，无法保存结果")
        if not str(workspace_root or "").strip():
            return _tool_error("当前会话未初始化工作目录，无法保存结果")
        if not save_path.lower().endswith(".json"):
            return _tool_error("save_as 只支持 .json 文件")
    return _PreparedToolRequest(
        dsl=dsl if isinstance(dsl, dict) else None,
        sql=normalized_sql,
        has_dsl=has_dsl,
        save_path=save_path,
    )


async def _resolve_tool_context(
    *,
    state: dict | None,
    semantic_config_id: str | None,
    member_datasource_ids: list[str] | None,
) -> _ResolvedToolContext | str:
    state = state or {}
    datasource_id = _get_datasource_id_from_state(state)
    normalized_semantic_config_id = str(semantic_config_id or "").strip()
    if not normalized_semantic_config_id:
        return _tool_error("缺少指标集ID，请先调用 semantic_tool 搜索语义模型后显式传入 semantic_config_id")
    if not datasource_id:
        return _tool_error("当前会话未绑定数据源")

    query_text = _latest_human_query(state.get("messages") or [])
    requested_member_datasource_ids = _normalize_member_datasource_ids(member_datasource_ids)
    available_member_datasource_ids, matched_member_datasource_ids = _get_member_scope_from_state(state)

    return _ResolvedToolContext(
        state=state,
        datasource_id=datasource_id,
        semantic_config_id=normalized_semantic_config_id,
        query_text=query_text,
        user_id=str(state.get("user_id") or "").strip() or None,
        operator_name=str(state.get("operator_name") or "").strip() or None,
        conversation_id=str(state.get("conversation_id") or "").strip() or None,
        member_datasource_ids=requested_member_datasource_ids,
        available_member_datasource_ids=available_member_datasource_ids,
        matched_member_datasource_ids=matched_member_datasource_ids,
    )


def _validate_member_scope(
    *,
    requested_member_datasource_ids: list[str],
    available_member_datasource_ids: list[str],
    matched_member_datasource_ids: list[str],
) -> str | None:
    if not available_member_datasource_ids:
        return _tool_error("本次请求没有可用执行对象，请先选择有权限的数据源后再查询")
    if not requested_member_datasource_ids:
        return _tool_error("缺少 member_datasource_ids，请从当前可用执行对象中明确选择本次查询范围")
    if matched_member_datasource_ids:
        invalid_matched_member_ids = [
            member_id
            for member_id in requested_member_datasource_ids
            if member_id not in matched_member_datasource_ids
        ]
        if invalid_matched_member_ids:
            return _tool_error(
                "本次查询的执行对象与用户问题中指定的机构不一致；"
                f"请只使用用户问题精确匹配到的 member_datasource_ids: {', '.join(matched_member_datasource_ids)}"
            )

    invalid_member_ids = [
        member_id
        for member_id in requested_member_datasource_ids
        if member_id not in available_member_datasource_ids
    ]
    if invalid_member_ids:
        return _tool_error(f"本次查询包含不可用的执行对象 {', '.join(invalid_member_ids)}；请只使用可用执行对象中的 member_datasource_ids")
    return None


async def _compile_tool_query(
    *,
    request: _PreparedToolRequest,
    context: _ResolvedToolContext,
) -> _CompiledToolQuery | str:
    query_mode = "nl2sql" if not request.has_dsl else "dsl2sql"
    confidence = 0.6 if not request.has_dsl else 0.9
    config = await _resolve_semantic_query_config(
        context.state,
        context.datasource_id,
        context.semantic_config_id,
    )
    if not config:
        return _tool_error(f"未找到可用指标集 {context.semantic_config_id or '-'}")
    if not request.has_dsl:
        return _CompiledToolQuery(sql=request.sql, query_mode=query_mode, confidence=confidence)

    compile_result = compile_semantic_query_dsl(
        config,
        request.dsl or {},
        user_query=context.query_text or None,
        generator_cls=SemanticSQLGenerator,
    )
    if not compile_result.get("success"):
        return _tool_error(compile_result.get("error") or "DSL2SQL 编译失败")
    sql = str(compile_result.get("sql") or "").strip()
    if not sql:
        return _tool_error("DSL2SQL 未生成 SQL")
    return _CompiledToolQuery(sql=sql, query_mode=query_mode, confidence=confidence)


async def _execute_tool_query(context: _ResolvedToolContext, query: _CompiledToolQuery) -> dict | str:
    logger.info(f"执行语义查询 SQL: {query.sql[:100]}...")
    logger.info(
        "chatdata conversation execute sql: user_id=%s operator_name=%s datasource_id=%s member_datasource_ids=%s",
        context.user_id,
        context.operator_name,
        context.datasource_id,
        context.member_datasource_ids,
    )
    try:
        query_result = await _execute_sql_real(
            sql=query.sql,
            datasource_id=context.datasource_id,
            member_datasource_ids=context.member_datasource_ids,
            user_id=context.user_id,
            semantic_config_id=context.semantic_config_id,
            query_text=context.query_text,
            conversation_id=context.conversation_id,
            operator_name=context.operator_name,
        )
    except Exception as exc:
        logger.error("semantic_query_tool 执行失败: %s", exc, exc_info=True)
        return _tool_error(str(exc))

    query_result["sql"] = query_result.get("sql") or query.sql
    query_result["query_mode"] = query.query_mode
    query_result["confidence"] = query.confidence
    return query_result


@tool(args_schema=SemanticQueryInput)
async def semantic_query_tool(
    dsl: dict | None = None,
    state: Annotated[dict | None, InjectedState] = None,
    sql: str | None = None,
    semantic_config_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    save_as: str | None = None,
) -> str:
    """
    执行数据库查询并返回统一查询结果。

    常规查询传入语义 DSL，服务端先编译为 SQL 再执行；只有 DSL 无法表达查询结构时，
    才传入只读 SQL 直接执行。两种模式都必须指定语义模型和执行对象范围。
    DSL 和 SQL 只能传递一个。常规查询不要传 save_as；仅当用户、技能或后续流程明确要求
    将完整结果保留到磁盘用于二次分析、脚本处理或产物生成时使用 save_as。
    """
    return await _run_semantic_query_tool(
        dsl=dsl,
        sql=sql,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
        state=state,
        save_as=save_as,
    )


async def _run_semantic_query_tool(
    *,
    dsl: Any = None,
    sql: str | None = None,
    semantic_config_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    state: dict | None = None,
    save_as: str | None = None,
) -> str:
    workspace_root = _get_workspace_root_from_state(state)
    workspace_context = (state or {}).get("workspace")
    has_workspace = isinstance(workspace_context, dict)
    prepared_request = _prepare_tool_request(
        dsl=dsl,
        sql=sql,
        save_as=save_as,
        workspace_root=workspace_root,
        has_workspace=has_workspace,
    )
    if isinstance(prepared_request, str):
        return prepared_request

    context = await _resolve_tool_context(
        state=state,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
    )
    if isinstance(context, str):
        return context

    query = await _compile_tool_query(request=prepared_request, context=context)
    if isinstance(query, str):
        return query

    member_scope_error = _validate_member_scope(
        requested_member_datasource_ids=context.member_datasource_ids,
        available_member_datasource_ids=context.available_member_datasource_ids,
        matched_member_datasource_ids=context.matched_member_datasource_ids,
    )
    if member_scope_error:
        return member_scope_error

    query_result = await _execute_tool_query(context, query)
    if isinstance(query_result, str):
        return query_result

    return build_internal_query_result(
        query_result,
        query_mode=query.query_mode,
        confidence=query.confidence,
        save_as=prepared_request.save_path,
    )


def _normalize_member_datasource_ids(member_datasource_ids: Any) -> list[str]:
    """标准化成员数据源ID列表。"""
    return normalize_member_datasource_ids(member_datasource_ids)


async def _execute_sql_real(
    sql: str,
    datasource_id: str | None,
    member_datasource_ids: list[str] | None = None,
    user_id: str | None = None,
    semantic_config_id: str | None = None,
    query_text: str | None = None,
    conversation_id: str | None = None,
    operator_name: str | None = None,
) -> dict:
    """
    真实执行模式：执行实际的数据库查询

    Args:
        sql: SQL 查询语句
        datasource_id: 数据源ID
        member_datasource_ids: 指定执行的成员数据源ID列表

    Returns:
        查询结果字典
    """
    return await execute_semantic_query_sql(
        sql=sql,
        datasource_id=datasource_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        semantic_config_id=semantic_config_id,
        query_text=query_text,
        conversation_id=conversation_id,
        operator_name=operator_name,
    )
