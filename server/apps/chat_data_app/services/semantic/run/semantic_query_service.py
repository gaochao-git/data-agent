"""AI/BI 语义查询的统一应用服务（门面）。

查询计划组装与编译已分层：
- 输入适配器：semantic_ai_query_adapter / semantic_bi_query_adapter
- 共享组装原语：semantic_query_plan_builder
- 编译内核：core/semantic_sql（唯一 SQL 生成权威入口）
本文件只保留对外编译/执行门面与结果装配，所有存量调用方继续从这里导入。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...datasource.manager import get_datasource_manager
from ...setting_service import get_runtime_setting
from ...sql_run.result import query_result_status
from .semantic_ai_query_adapter import SemanticAIQueryAdapter  # noqa: F401
from .semantic_bi_query_adapter import (  # noqa: F401  历史兼容 re-export，存量调用方与测试从本模块导入
    FORBIDDEN_SEMANTIC_BUILDER_KEYS,
    SemanticBIQueryAdapter,
    _as_list,
    _field_keys,
    _normalize_alias,
    _parse_aggregate_expression,
)
from .semantic_config_service import get_semantic_config_service
from .semantic_model_repository import load_semantic_model
from .semantic_query_plan_builder import normalize_text


async def load_runtime_semantic_config_strict(
    datasource_id: str, semantic_config_id: str, user_id: str | None = None
) -> dict[str, Any]:
    """加载运行时语义配置并做错误映射；与 repository 的静默版(load_runtime_semantic_config)
    不同：未找到/未启用时抛出带用户可读信息的 LookupError/ValueError，供编译流程直出错误。"""
    normalized_datasource_id = normalize_text(datasource_id)
    normalized_config_id = normalize_text(semantic_config_id)
    if not normalized_datasource_id or not normalized_config_id:
        raise ValueError("缺少 datasource_id 或 semantic_config_id")
    try:
        return await load_semantic_model(
            normalized_datasource_id,
            normalized_config_id,
            user_id=normalize_text(user_id),
            mode="runtime",
            manager=get_datasource_manager(),
            semantic_service=get_semantic_config_service(),
        )
    except ValueError as exc:
        message = str(exc)
        if message == "数据源不存在":
            raise LookupError(f"数据源不存在: {normalized_datasource_id}") from exc
        if message == "未找到可用语义配置":
            raise LookupError(f"语义配置不存在: {normalized_config_id}") from exc
        if message == "语义配置未启用":
            raise ValueError(f"语义配置未启用: {normalized_config_id}") from exc
        raise


async def compile_semantic_builder_sql(
    datasource_id: str,
    semantic_config_id: str,
    semantic_builder_config: dict[str, Any],
    user_id: str | None = None,
) -> dict[str, Any]:
    semantic_config = await load_runtime_semantic_config_strict(datasource_id, semantic_config_id, user_id=user_id)
    result = compile_semantic_bi_dsl(
        semantic_config,
        semantic_builder_config,
        max_rows=await get_runtime_setting("CHATDATA_ROW_LIMIT"),
    )
    if not result.get("success"):
        raise ValueError(str(result.get("error") or "BI 语义 DSL 编译失败"))
    return result


@dataclass(frozen=True)
class SemanticCompileOptions:
    """语义 DSL 编译选项；应用层负责注入，不由编译器读取环境变量。"""

    max_rows: int | None = None
    offset: int | None = None


def compile_semantic_ai_dsl(
    semantic_config: dict[str, Any],
    dsl: dict[str, Any],
    *,
    options: SemanticCompileOptions | None = None,
    generator_cls: Callable[[dict[str, Any]], Any] | None = None,
    generator: Any = None,
) -> dict[str, Any]:
    """统一语义 DSL 编译门面，所有应用入口应通过本函数编译。"""
    if not isinstance(semantic_config, dict):
        return {"success": False, "sql": "", "error": "语义模型必须是对象"}
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "语义 DSL 必须是对象"}

    compile_options = options or SemanticCompileOptions()
    compiler_factory = generator_cls or SemanticAIQueryAdapter
    compiler = generator or compiler_factory(semantic_config)
    compile_result = compiler.generate_sql(
        metrics=dsl.get("metrics") or [],
        dimensions=dsl.get("dimensions") or [],
        filters=dsl.get("filters") or [],
        having=dsl.get("having") or [],
        order_by=dsl.get("order_by") or [],
        limit=dsl.get("limit", compile_options.max_rows),
        offset=dsl.get("offset", compile_options.offset),
    )
    return {
        "success": bool(compile_result.get("success")),
        "sql": str(compile_result.get("sql") or "").strip(),
        "error": compile_result.get("error"),
        "warnings": compile_result.get("warnings") or [],
    }


def compile_semantic_bi_dsl(
    semantic_config: dict[str, Any],
    dsl: dict[str, Any],
    *,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """编译 BI 可视化语义 DSL，返回 SQL 和内部查询计划。

    max_rows 为行数上限热值（async 调用方从配置变量表解析后传入；未传走适配器兜底）。
    """
    if not isinstance(semantic_config, dict):
        return {"success": False, "sql": "", "error": "语义模型必须是对象"}
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "BI 语义 DSL 必须是对象"}
    try:
        result = SemanticBIQueryAdapter(semantic_config, max_rows=max_rows).compile(dsl)
    except (TypeError, ValueError) as exc:
        return {"success": False, "sql": "", "error": str(exc)}
    return {
        "success": True,
        "sql": str(result.get("sql") or "").strip(),
        "query_plan": result.get("builder_config") or {},
        # 保留已有可视化响应字段，新的公共门面内部使用 query_plan。
        "builder_config": result.get("builder_config") or {},
        "error": None,
    }


def normalize_member_datasource_ids(member_datasource_ids: Any) -> list[str]:
    """Normalize member datasource ids while preserving the first-seen order."""
    normalized_ids: list[str] = []
    for item in member_datasource_ids or []:
        member_id = str(item or "").strip()
        if member_id and member_id not in normalized_ids:
            normalized_ids.append(member_id)
    return normalized_ids


async def execute_semantic_query_sql(
    *,
    sql: str,
    datasource_id: str | None,
    member_datasource_ids: list[str] | None = None,
    user_id: str | None = None,
    semantic_config_id: str | None = None,
    query_text: str | None = None,
    conversation_id: str | None = None,
    operator_name: str | None = None,
) -> dict[str, Any]:
    """Execute semantic query SQL through the unified query runtime."""
    # 延迟导入避免 query_runtime 的 BI 编译入口与本服务形成模块循环。
    from ...sql_run import runtime as query_runtime

    return await query_runtime.execute_agent_sql(
        sql=sql,
        datasource_id=datasource_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        semantic_config_id=semantic_config_id,
        query_text=query_text,
        conversation_id=conversation_id,
        operator_name=operator_name,
    )


def build_member_query_result(
    base_result: dict[str, Any],
    sql: str,
    member_datasource_id: str | None = None,
    member_name: str | None = None,
) -> dict[str, Any]:
    """Build a normalized member query result."""
    return {
        "status": query_result_status(base_result),
        "sql": sql,
        "columns": base_result.get("columns", []),
        "data": base_result.get("data", []),
        "total_count": base_result.get("total_count", 0),
        "execution_time_ms": base_result.get("execution_time_ms"),
        "error": base_result.get("error"),
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
    }
