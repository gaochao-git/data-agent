"""data_analyzing_tool：数据分析合并工具。

2026-09-18 起原 ``dsl2sql_tool``（校验）、``semantic_query_tool``（执行）、
``semantic_yaml_check_tool``（治理自检）合并为单个工具，按 ``action`` 分派：

- ``validate_dsl``：校验单个查询 DSL，返回编译后 SQL（不执行）；
- ``validate_dashboard``：批量校验完整 dashboard_spec 的所有卡片；
- ``execute_query``：执行查询（DSL 或只读 SQL 二选一），结果走内部协议由
  ToolResultMiddleware 落盘给前端；
- ``check_yaml``：语义模型 YAML 交付前自检（与导入接口同口径，只读不落库）；
- ``list_execution_targets``：实时获取当前用户可用执行对象清单（只读发现通道，
  为后续语义查询插件化预留的工具内数据源切口）；方言与智能问数说明随本返回
  透出，系统提示词不再静态注入。
- ``locate_object``：按本体对象批量定位语义模型（纯导航，不执行查询）——返回
  绑定模型（含快照文件名）、属性→维度对照与对象关系，供模型直接进入
  execute_query（定位纯函数见 services/ontology/object_locator.py，开关
  ``CHAT_DATA_ONTOLOGY_LOCATE_ENABLED`` 默认关闭）。

入参契约在 ``schema.DataAnalyzingInput``：``action`` + ``action_payload``（结构随
action 变化）+ ``intent``；每个 action 的 payload 在工具实现入口经
``schema.validate_data_analyzing_payload`` 校验（共用 ``DATA_ANALYZING_ACTION_PAYLOAD_MODELS``
映射表）。校验不能放进 args model_validator——langgraph 会把 model_validator 抛出的
校验错误过滤成空白消息，模型与用户都看不到原因；在函数体内抛错则详情完整可见。
新增 action 的步骤：schema.py 加 payload 模型并登记映射 -> 本文件加分派分支。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState

from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.daos.ontology_dao import OntologyDao
from apps.chat_data_app.services.dashboard.spec_service import (
    remove_mixed_raw_metrics_for_aggregate_dsl,
    validate_dashboard_spec_payload,
)
from apps.chat_data_app.services.datasource.manager import get_datasource_manager
from apps.chat_data_app.services.datasource.target_context import (
    get_execution_target_member_id,
    get_execution_target_name,
    get_execution_target_org_code,
    source_type_to_dialect,
)
from apps.chat_data_app.services.ontology.object_locator import (
    build_locator_usage_hint,
    build_object_locator_report,
    object_locator_enabled,
)
from apps.chat_data_app.services.runtime_config import SEMANTIC_MODEL_DISCOVERY_HINT
from apps.chat_data_app.services.semantic.run.chat_data_ossie_importer import (
    build_model_checklist,
)
from apps.chat_data_app.services.semantic.run.semantic_config_service import (
    get_semantic_config_service,
    load_semantic_physical_columns,
    prepare_ossie_import_models,
    quality_findings_for_imports,
)
from apps.chat_data_app.services.semantic.run.semantic_materializer import (
    enabled_candidate_ids,
    model_snapshot_file_name,
)
from apps.chat_data_app.services.semantic.run.semantic_model_repository import (
    load_runtime_semantic_config,
    load_semantic_model,
)
from apps.chat_data_app.services.semantic.run.semantic_query_service import (
    SemanticCompileOptions,
    compile_semantic_ai_dsl,
    execute_semantic_query_sql,
    normalize_member_datasource_ids,
)
from apps.chat_data_app.services.setting_service import get_runtime_setting
from utils.logger import get_logger

from ...schema import (
    DataAnalyzingInput,
    validate_data_analyzing_payload,
)
from ..tool_results import build_internal_query_result
from .common import normalize_json_like_payload, tool_result_payload
from .filesystem import read_text_file_full

logger = get_logger(__name__)

DATA_ANALYZING_TOOL_NAME = "data_analyzing_tool"

# 失败统一走 raise：ToolErrorMiddleware 转 status="error" 的 ToolMessage，content 带详情。


# ---------------------------------------------------------------------------
# execute_query：执行语义查询（原 semantic_query_tool 逻辑平移）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PreparedToolRequest:
    dsl: dict[str, Any] | None
    sql: str
    has_dsl: bool


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


def _latest_human_query(messages: list[Any] | None) -> str:
    """提取最近一条用户消息，保持 DSL 校验和执行阶段的意图判定一致。"""
    for message in reversed(messages or []):
        if isinstance(message, HumanMessage):
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
    available_member_ids = normalize_member_datasource_ids(context.get("available_member_datasource_ids"))
    matched_member_ids = normalize_member_datasource_ids(context.get("matched_member_datasource_ids"))
    return available_member_ids, matched_member_ids


def _prepare_tool_request(
    *,
    dsl: Any,
    sql: str | None,
) -> _PreparedToolRequest:
    has_dsl = dsl is not None
    normalized_sql = str(sql or "").strip()
    has_sql = bool(normalized_sql)
    if has_dsl == has_sql:
        raise ValueError("必须且只能传入 dsl 或 sql 其中一个")
    if has_dsl:
        dsl = normalize_json_like_payload(dsl)
    if has_dsl and not isinstance(dsl, dict):
        raise ValueError("dsl 必须是对象")
    if isinstance(dsl, dict) and "sql" in dsl:
        raise ValueError("sql 必须放在顶层 sql 参数中，不要放入 dsl")
    if isinstance(dsl, dict) and isinstance(dsl.get("cards"), list):
        raise ValueError("dsl 必须是单个查询对象")
    return _PreparedToolRequest(
        dsl=dsl if isinstance(dsl, dict) else None,
        sql=normalized_sql,
        has_dsl=has_dsl,
    )


async def _resolve_tool_context(
    *,
    state: dict | None,
    semantic_config_id: str | None,
    member_datasource_ids: list[str] | None,
) -> _ResolvedToolContext:
    state = state or {}
    datasource_id = _get_datasource_id_from_state(state)
    normalized_semantic_config_id = str(semantic_config_id or "").strip()
    if not normalized_semantic_config_id:
        raise ValueError(f"缺少指标集ID，请先{SEMANTIC_MODEL_DISCOVERY_HINT}后显式传入 semantic_config_id")
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")

    query_text = _latest_human_query(state.get("messages") or [])
    requested_member_datasource_ids = normalize_member_datasource_ids(member_datasource_ids)
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
) -> None:
    if not available_member_datasource_ids:
        raise ValueError("本次请求没有可用执行对象，请先选择有权限的数据源后再查询")
    if not requested_member_datasource_ids:
        raise ValueError(
            "缺少 member_datasource_ids，请从当前可用执行对象中明确选择本次查询范围；"
            f"当前可用执行对象: {', '.join(available_member_datasource_ids)}"
        )
    if matched_member_datasource_ids:
        invalid_matched_member_ids = [
            member_id for member_id in requested_member_datasource_ids if member_id not in matched_member_datasource_ids
        ]
        if invalid_matched_member_ids:
            raise ValueError(
                "本次查询的执行对象与用户问题中指定的机构不一致；"
                f"请只使用用户问题精确匹配到的 member_datasource_ids: {', '.join(matched_member_datasource_ids)}"
            )

    invalid_member_ids = [
        member_id for member_id in requested_member_datasource_ids if member_id not in available_member_datasource_ids
    ]
    if invalid_member_ids:
        raise ValueError(
            f"本次查询包含不可用的执行对象 {', '.join(invalid_member_ids)}；"
            "请只使用可用执行对象中的 member_datasource_ids，"
            f"当前可用执行对象: {', '.join(available_member_datasource_ids)}"
        )
    return None


async def _compile_tool_query(
    *,
    request: _PreparedToolRequest,
    context: _ResolvedToolContext,
) -> _CompiledToolQuery:
    query_mode = "sql" if not request.has_dsl else "dsl"
    config = await _resolve_semantic_query_config(
        context.state,
        context.datasource_id,
        context.semantic_config_id,
    )
    if not config:
        raise ValueError(f"未找到可用指标集 {context.semantic_config_id or '-'}")
    if not request.has_dsl:
        return _CompiledToolQuery(sql=request.sql, query_mode=query_mode)

    compile_result = compile_semantic_ai_dsl(
        config,
        request.dsl or {},
        options=SemanticCompileOptions(max_rows=await get_runtime_setting("CHATDATA_ROW_LIMIT")),
    )
    if not compile_result.get("success"):
        raise ValueError(compile_result.get("error") or "DSL2SQL 编译失败")
    sql = str(compile_result.get("sql") or "").strip()
    if not sql:
        raise ValueError("DSL2SQL 未生成 SQL")
    return _CompiledToolQuery(sql=sql, query_mode=query_mode)


async def _execute_tool_query(context: _ResolvedToolContext, query: _CompiledToolQuery) -> dict:
    logger.info(f"执行语义查询 SQL: {query.sql[:100]}...")
    logger.info(
        "chatdata conversation execute sql: user_id=%s operator_name=%s datasource_id=%s member_datasource_ids=%s",
        context.user_id,
        context.operator_name,
        context.datasource_id,
        context.member_datasource_ids,
    )
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

    query_result["sql"] = query_result.get("sql") or query.sql
    query_result["query_mode"] = query.query_mode
    return query_result


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
    """真实执行模式：执行实际的数据库查询（审计与落库在 query_runtime 内完成）。"""
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


async def _run_execute_query(
    *,
    dsl: Any = None,
    sql: str | None = None,
    semantic_config_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    state: dict | None = None,
) -> str:
    """执行查询动作的主体：编译 -> 执行对象范围校验 -> 执行 -> 内部协议返回。"""
    prepared_request = _prepare_tool_request(dsl=dsl, sql=sql)

    context = await _resolve_tool_context(
        state=state,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
    )

    query = await _compile_tool_query(request=prepared_request, context=context)
    _validate_member_scope(
        requested_member_datasource_ids=context.member_datasource_ids,
        available_member_datasource_ids=context.available_member_datasource_ids,
        matched_member_datasource_ids=context.matched_member_datasource_ids,
    )

    query_result = await _execute_tool_query(context, query)

    return build_internal_query_result(
        query_result,
        query_mode=query.query_mode,
    )


# ---------------------------------------------------------------------------
# validate_dsl / validate_dashboard：DSL 校验（原 dsl2sql_tool 逻辑平移）
# ---------------------------------------------------------------------------


def _batch_result_payload(
    result: dict[str, Any],
    datasource_id: str,
    semantic_config_id: str | None,
) -> dict[str, Any]:
    """dashboard_spec 批量校验的统一结果结构（字段顺序即输出顺序）。"""
    return {
        "success": bool(result.get("valid")),
        "mode": "batch",
        "datasource_id": datasource_id,
        "semantic_config_id": semantic_config_id,
        "semantic_config_ids": result.get("semantic_config_ids") or [],
        "valid_count": result.get("valid_count") or 0,
        "warnings": result.get("warnings") or [],
        "panels": result.get("panels") or [],
    }


def _single_result_payload(
    *,
    datasource_id: str,
    semantic_config_id: str | None,
    dsl: Any,
    sql: Any,
    error: Any,
    warnings: list[Any],
    success: bool,
) -> dict[str, Any]:
    """单条 DSL 校验的统一结果结构（字段顺序即输出顺序）。"""
    return {
        "success": success,
        "mode": "single",
        "datasource_id": datasource_id,
        "semantic_config_id": semantic_config_id,
        "dsl": dsl,
        "sql": sql,
        "error": error,
        "warnings": warnings,
    }


async def _run_validate_dsl(
    *,
    dsl: Any,
    semantic_config_id: str,
    datasource_id: str,
    user_id: str,
    operator_name: str | None,
) -> str:
    """单条 DSL 校验：编译返回 SQL，不执行。"""
    effective_semantic_config_id = str(semantic_config_id or "").strip() or None
    dsl = normalize_json_like_payload(dsl)
    if not isinstance(dsl, dict):
        raise ValueError("dsl 必须是对象")
    if not effective_semantic_config_id:
        raise ValueError(f"缺少 semantic_config_id，请先{SEMANTIC_MODEL_DISCOVERY_HINT}后显式传入语义模型ID")
    semantic_config = await load_semantic_model(
        datasource_id,
        effective_semantic_config_id,
        user_id=user_id,
        mode="full",
    )
    payload, warnings = remove_mixed_raw_metrics_for_aggregate_dsl(dsl or {}, semantic_config)
    if payload.get("compare"):
        return tool_result_payload(
            DATA_ANALYZING_TOOL_NAME,
            _single_result_payload(
                datasource_id=datasource_id,
                semantic_config_id=effective_semantic_config_id,
                dsl=payload,
                sql=None,
                error="错误：DSL2SQL 不再支持 compare 同比/环比；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析",
                warnings=warnings,
                success=False,
            ),
        )
    result = compile_semantic_ai_dsl(
        semantic_config,
        payload,
        options=SemanticCompileOptions(max_rows=await get_runtime_setting("CHATDATA_ROW_LIMIT")),
    )
    return tool_result_payload(
        DATA_ANALYZING_TOOL_NAME,
        _single_result_payload(
            datasource_id=datasource_id,
            semantic_config_id=effective_semantic_config_id,
            dsl=payload,
            sql=result.get("sql"),
            error=result.get("error"),
            warnings=warnings,
            success=bool(result.get("success")),
        ),
    )


async def _run_validate_dashboard(
    *,
    dsl: Any,
    semantic_config_id: str | None,
    datasource_id: str,
    user_id: str,
    operator_name: str | None,
) -> str:
    """dashboard_spec 批量校验：一次传入完整规格，逐卡返回结果。"""
    effective_semantic_config_id = str(semantic_config_id or "").strip() or None
    dsl = normalize_json_like_payload(dsl)
    if isinstance(dsl, dict) and isinstance(dsl.get("cards"), list):
        result = await validate_dashboard_spec_payload(
            dashboard_spec=dsl,
            datasource_id=datasource_id,
            semantic_config_id=effective_semantic_config_id,
            user_id=user_id,
            username=operator_name,
        )
        return tool_result_payload(
            DATA_ANALYZING_TOOL_NAME,
            _batch_result_payload(result, datasource_id, effective_semantic_config_id),
        )
    if isinstance(dsl, list):
        if not effective_semantic_config_id and not any(
            isinstance(card, dict) and str(card.get("semantic_config_id") or "").strip() for card in dsl
        ):
            raise ValueError(f"批量看板卡片缺少 semantic_config_id，请先{SEMANTIC_MODEL_DISCOVERY_HINT}")
        result = await validate_dashboard_spec_payload(
            dashboard_spec={"cards": dsl},
            datasource_id=datasource_id,
            semantic_config_id=effective_semantic_config_id,
            user_id=user_id,
            username=operator_name,
        )
        return tool_result_payload(
            DATA_ANALYZING_TOOL_NAME,
            _batch_result_payload(result, datasource_id, effective_semantic_config_id),
        )
    raise ValueError("dsl 必须是对象或数组")


# ---------------------------------------------------------------------------
# check_yaml：语义模型 YAML 交付前自检（原 semantic_yaml_check_tool 逻辑平移）
# ---------------------------------------------------------------------------

# YAML 全文直读上限（字符）：与导入接口 SemanticOssieImportRequest.yaml_content 的 2MB 口径一致
_CHECK_YAML_MAX_CHARS = 2_000_000


async def _read_workspace_yaml_text(workspace_runtime: ConversationWorkspaceRuntime | None, file_path: str) -> str:
    """从会话工作区拉取 YAML 全文；复用 filesystem.read_text_file_full，失败直接 raise，由 ToolErrorMiddleware 转 error。"""
    if workspace_runtime is None:
        raise ValueError("工作区不可用，无法读取 YAML 文件")
    return await read_text_file_full(workspace_runtime, file_path, max_chars=_CHECK_YAML_MAX_CHARS)


async def _run_check_yaml(
    *,
    file_path: str,
    datasource_id: str,
    user_id: str | None = None,
    workspace_runtime: ConversationWorkspaceRuntime | None = None,
) -> str:
    """与导入接口（ossie-import/preview）共用 prepare_ossie_import_models，保证校验口径一致。"""
    yaml_content = await _read_workspace_yaml_text(workspace_runtime, file_path)
    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(datasource_id)
    if not datasource:
        raise ValueError(f"数据源不存在: {datasource_id}")
    try:
        imports, duplicate_error = await prepare_ossie_import_models(
            ds_manager,
            datasource,
            datasource_id,
            str(yaml_content or ""),
            semantic_service=get_semantic_config_service(),
            user_id=str(user_id or ""),
        )
    except ValueError as exc:
        return tool_result_payload(
            DATA_ANALYZING_TOOL_NAME,
            {
                "success": False,
                "stage": "parse",
                "datasource_id": datasource_id,
                "error": str(exc),
                "models": [],
                "all_importable": False,
            },
        )
    if duplicate_error:
        return tool_result_payload(
            DATA_ANALYZING_TOOL_NAME,
            {
                "success": False,
                "stage": "duplicate_names",
                "datasource_id": datasource_id,
                "error": duplicate_error,
                "models": [],
                "all_importable": False,
            },
        )

    physical_columns = await load_semantic_physical_columns(ds_manager, datasource_id)
    # 质量检查（单模型 5 维 + 跨模型口径）统一编排入口，与导入预览同源
    findings_by_model = await quality_findings_for_imports(
        imports, datasource_id, physical_columns=physical_columns, user_id=str(user_id or "")
    )
    models = []
    for item in imports:
        model_payload: dict[str, Any] = {
            "name": item.name,
            "importable": item.importable,
            "errors": list(item.errors),
            "warnings": list(item.warnings),
            "metric_count": len(item.metrics),
            "dimension_count": len(item.dimensions),
            "relationship_count": len(item.relationships),
            # 同名冲突标注与导入预览同口径：模型自检也能发现「会被替换/会被忽略」的后果
            "existing_config": dict(item.existing_config) if item.existing_config else None,
            "conflict_locked": bool(item.conflict_locked),
        }
        findings = None
        if item.importable:
            # 只透出触发的 findings：评分无法指导动作，自检链路不输出 score/grade
            findings = findings_by_model.get(item.name, [])
            model_payload["quality"] = {"findings": findings}
        model_payload["checklist"] = build_model_checklist(
            item,
            validation_error=item.validation_error,
            quality_findings=findings,
            inventory_available=physical_columns is not None,
        )
        models.append(model_payload)

    return tool_result_payload(
        DATA_ANALYZING_TOOL_NAME,
        {
            "success": True,
            "stage": "checked",
            "datasource_id": datasource_id,
            "all_importable": bool(models) and all(model["importable"] for model in models),
            "models": models,
        },
    )


# ---------------------------------------------------------------------------
# list_execution_targets：实时获取当前用户可用执行对象（只读发现通道）
# ---------------------------------------------------------------------------


def _execution_target_entry(target: dict[str, Any], index: int) -> dict[str, Any]:
    """单条执行对象的对外展示字段：只透出名称/ID/机构编码，不透出连接信息。"""
    return {
        "member_name": get_execution_target_name(target, index),
        "member_datasource_id": get_execution_target_member_id(target),
        "org_code": get_execution_target_org_code(target),
    }


def _datasource_overview(meta: dict) -> dict:
    """数据源组概览字段：类型/方言/智能问数说明，随执行对象清单一起返回。

    系统提示词不再注入方言块与数据源组说明（见 prompts/chat_data.py 开关），
    这些都是数据源组属性，改为模型查数前经本工具按需获取。
    """
    source_type = str(meta.get("source_type") or "").strip().lower()
    return {
        "source_type": source_type,
        "dialect": source_type_to_dialect(source_type),
        "query_context": str(meta.get("query_context") or "").strip(),
    }


async def _run_list_execution_targets(
    *,
    state: dict | None,
    bound_datasource_id: str,
    bound_user_id: str,
    bound_operator_name: str | None,
) -> str:
    """实时查询当前用户在会话数据源组下有权限执行的全部成员数据源。

    范围完全由服务端决定（datasource_id 绑定会话 + user_id 鉴权），模型无参数可指定；
    复用 manager 的全量授权清单入口，与系统提示词注入的执行对象同一套权限口径。
    方言与智能问数说明（query_context）随本返回透出。
    """
    state = state or {}
    datasource_id = _get_datasource_id_from_state(state) or bound_datasource_id
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    operator_name = str(state.get("operator_name") or "").strip() or bound_operator_name
    manager = get_datasource_manager()
    targets = await manager.list_authorized_execution_targets_async(
        datasource_id,
        user_id=user_id,
        username=operator_name,
    )
    meta = await manager.get_datasource_meta_async(datasource_id) or {}
    entries = [_execution_target_entry(target, index) for index, target in enumerate(targets, start=1)]
    return tool_result_payload(
        DATA_ANALYZING_TOOL_NAME,
        {
            "success": True,
            **_datasource_overview(meta),
            "count": len(entries),
            "execution_targets": entries,
        },
    )


# ---------------------------------------------------------------------------
# locate_object：按本体对象批量定位语义模型（纯导航，不执行查询）
# ---------------------------------------------------------------------------


async def _run_locate_object(
    *,
    payload: Any,
    state: dict | None,
    bound_datasource_id: str,
    bound_user_id: str,
    bound_operator_name: str | None,
) -> str:
    """本体对象定位：批量返回对象绑定的语义模型/属性→维度对照/关系。

    可定位范围结构性收敛：注入 bundle 按「本轮启用语义模型」加载（与本体目录
    物化同源），对象不在范围内即列入 not_found 并给出候选，不泄露全量本体。
    定位不执行查询——查询仍由模型依据报告走 execute_query。
    """
    if not object_locator_enabled():
        raise ValueError("对象定位未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED=1")
    state = state or {}
    datasource_id = _get_datasource_id_from_state(state) or bound_datasource_id
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")
    user_id = str(state.get("user_id") or "").strip() or bound_user_id

    service = get_semantic_config_service()
    configs = await service.get_configs_async(datasource_id, str(user_id or ""))
    enabled_id_set = set(enabled_candidate_ids(configs))
    enabled_configs = [config for config in configs if str((config or {}).get("id") or "") in enabled_id_set]
    if not enabled_configs:
        raise ValueError("当前数据源组没有启用语义模型，无法定位对象")
    enabled_ids = [str(config.get("id")) for config in enabled_configs]
    bundle = await OntologyDao().load_injection_bundle_async(enabled_ids)

    report = build_object_locator_report(
        bundle,
        object_names=payload.objects,
        config_names={
            config_id: str(config.get("name") or config.get("semantic_config_name") or "")
            for config_id, config in zip(enabled_ids, enabled_configs, strict=True)
        },
        model_files={
            config_id: model_snapshot_file_name(config)
            for config_id, config in zip(enabled_ids, enabled_configs, strict=True)
        },
        semantic_config_id=payload.semantic_config_id,
    )
    return tool_result_payload(
        DATA_ANALYZING_TOOL_NAME,
        {
            "success": True,
            **report,
            "usage": build_locator_usage_hint(),
        },
    )


# ---------------------------------------------------------------------------
# 合并工具本体
# ---------------------------------------------------------------------------


async def _run_data_analyzing_impl(
    action: str,
    action_payload: dict,
    state: Annotated[dict | None, InjectedState] | None,
    bound_datasource_id: str,
    bound_user_id: str,
    bound_operator_name: str | None,
    workspace_runtime: ConversationWorkspaceRuntime | None,
) -> str:
    """按 action 分发到对应的 DSL 校验 / 查询执行 / YAML 自检实现。"""
    # payload 校验必须在函数体内做（不能用 args_schema 的 model_validator）：
    # langgraph 会把 model_validator 抛出的校验错误过滤成空白消息，模型看不到原因
    payload = validate_data_analyzing_payload(action, action_payload)

    if action == "validate_dsl":
        if not bound_datasource_id:
            raise ValueError("当前会话未绑定数据源")
        return await _run_validate_dsl(
            dsl=payload.dsl,
            semantic_config_id=payload.semantic_config_id,
            datasource_id=bound_datasource_id,
            user_id=bound_user_id,
            operator_name=bound_operator_name,
        )
    if action == "validate_dashboard":
        if not bound_datasource_id:
            raise ValueError("当前会话未绑定数据源")
        return await _run_validate_dashboard(
            dsl=payload.dsl,
            semantic_config_id=payload.semantic_config_id,
            datasource_id=bound_datasource_id,
            user_id=bound_user_id,
            operator_name=bound_operator_name,
        )
    if action == "execute_query":
        return await _run_execute_query(
            dsl=payload.dsl,
            sql=payload.sql,
            semantic_config_id=payload.semantic_config_id,
            member_datasource_ids=payload.member_datasource_ids,
            state=state,
        )
    if action == "list_execution_targets":
        return await _run_list_execution_targets(
            state=state,
            bound_datasource_id=bound_datasource_id,
            bound_user_id=bound_user_id,
            bound_operator_name=bound_operator_name,
        )
    if action == "locate_object":
        return await _run_locate_object(
            payload=payload,
            state=state,
            bound_datasource_id=bound_datasource_id,
            bound_user_id=bound_user_id,
            bound_operator_name=bound_operator_name,
        )
    # check_yaml
    if not bound_datasource_id:
        raise ValueError("当前会话未绑定数据源")
    return await _run_check_yaml(
        file_path=payload.file_path,
        datasource_id=bound_datasource_id,
        user_id=bound_user_id,
        workspace_runtime=workspace_runtime,
    )


def create_data_analyzing_tool(
    datasource_id: str | None = None,
    user_id: str | None = None,
    operator_name: str | None = None,
    workspace_runtime: ConversationWorkspaceRuntime | None = None,
) -> BaseTool:
    bound_datasource_id = str(datasource_id or "").strip()
    bound_user_id = str(user_id or "system").strip() or "system"
    bound_operator_name = str(operator_name or "").strip() or None

    @tool(DATA_ANALYZING_TOOL_NAME, args_schema=DataAnalyzingInput)
    async def data_analyzing_tool(
        action: str,
        action_payload: dict,
        state: Annotated[dict | None, InjectedState] = None,
        intent: str = "",
    ) -> str:
        """
        数据分析合并工具：按 action 执行 DSL 校验、查询执行、语义模型 YAML 自检或本体对象定位。常规查数流程（检索语义模型、确认字段口径、生成 DSL、校验、执行）以 `data-analyzing` 技能为准，本工具不重复定义。

        Args:
            action: 操作类型，枚举：
        - validate_dsl: 校验单个查询 DSL，返回编译后的 SQL（不执行）
        - validate_dashboard: 批量校验 dashboard_spec 的所有卡片
        - execute_query: 执行查询（DSL 或 SQL 二选一），结果落盘到 outputs/query_result/
        - check_yaml: 语义模型 YAML 质量自检
        - list_execution_targets: 实时获取当前用户可用的执行对象清单（execute_query 前用于确认 member_datasource_ids 取值范围）
        - locate_object: 按本体对象批量定位语义模型——返回绑定模型（含快照文件名）、属性→维度对照、对象关系；纯导航不执行查询，可定位对象见本体目录（_ontology_catalog.yaml）
            action_payload: 操作参数，结构随 action 变化：
        - validate_dsl: {"dsl": {...}, "semantic_config_id": "..."}
        - validate_dashboard: {"dsl": <含 dashboard_name 和 cards 的完整 dashboard_spec>, "semantic_config_id": "卡片缺省值，可选"}
        - execute_query: {"dsl": {...} 或 "sql": "...", "semantic_config_id": "...", "member_datasource_ids": ["<ID>"]}（member_datasource_ids 必须是 ID 字符串数组，单个机构也传数组，如 ["ORG_CODE1"]）
        - check_yaml: {"file_path": "/mnt/user-data/workspace/model.yaml"}（工作区 YAML 文件路径，后端直读，不要把 YAML 全文复制进参数）
        - list_execution_targets: {}（无参数，范围由服务端会话上下文决定）
        - locate_object: {"objects": [<对象API名>...]（单次最多10个）, "semantic_config_id": 可选只看该模型绑定}
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        return await _run_data_analyzing_impl(
            action,
            action_payload,
            state,
            bound_datasource_id,
            bound_user_id,
            bound_operator_name,
            workspace_runtime,
        )

    return data_analyzing_tool


__all__ = [
    "DATA_ANALYZING_TOOL_NAME",
    "create_data_analyzing_tool",
]
