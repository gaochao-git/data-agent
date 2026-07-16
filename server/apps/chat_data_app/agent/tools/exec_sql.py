"""DataAnalyze exec SQL tool."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.dashboard_spec_service import get_dashboard_spec_semantic_config, remove_mixed_raw_metrics_for_aggregate_dsl
from ...services.workspace_execution import SandboxExecutionSessionRef, sandbox_write_text_file
from ...services.query_result import query_result_status
from ...services.semantic_query_service import compile_semantic_query_dsl
from ...services.semantic_sql_generator import SemanticSQLGenerator
from .common import json_dumps, normalize_json_like_payload
from .sql import _execute_sql_real, _normalize_member_datasource_ids

logger = get_logger(__name__)


def create_exec_sql_tool(
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    user_id: str = "system",
    operator_name: str | None = None,
    workspace_root: str | None = None,
    execution_session_ref: SandboxExecutionSessionRef | None = None,
    conversation_id: str | None = None,
):
    bound_datasource_id = str(datasource_id or "").strip()
    bound_semantic_config_id = str(semantic_config_id or "").strip() or None
    bound_conversation_id = str(conversation_id or "").strip() or None
    bound_member_datasource_ids = _normalize_member_datasource_ids(member_datasource_ids)
    bound_user_id = str(user_id or "system").strip() or "system"
    bound_operator_name = str(operator_name or "").strip() or None
    bound_workspace_root = str(workspace_root or "").strip()

    @tool("semantic_query_tool")
    async def semantic_query_tool(
        dsl: Any,
        semantic_config_id: str,
        member_datasource_ids: list[str],
        save_as: str | None = None,
        user_query: str = "",
    ) -> str:
        """
        根据语义 DSL 生成只读 SQL 并返回真实数据结果。

        不传 save_as 时返回完整查询结果；传入 save_as 时直接把完整结果写入 workspace/ 或 outputs/ 下的 JSON 文件，
        并仅返回路径和摘要，适合后续 Python 脚本读取。
        """
        try:
            if not bound_datasource_id:
                return "semantic_query_tool 执行失败: 当前会话未绑定数据源"
            effective_semantic_config_id = str(semantic_config_id or "").strip() or None
            if not effective_semantic_config_id:
                return "semantic_query_tool 执行失败: 缺少 semantic_config_id，请先调用 select_semantic_config_tool 后显式传入指标集ID"
            if bound_semantic_config_id and effective_semantic_config_id != bound_semantic_config_id:
                return f"semantic_query_tool 执行失败: 当前会话已选择指标集 {bound_semantic_config_id}，请传入该 semantic_config_id"
            requested_member_datasource_ids = _normalize_member_datasource_ids(member_datasource_ids)
            if not requested_member_datasource_ids:
                return "semantic_query_tool 执行失败: 缺少 member_datasource_ids，请从当前可用执行对象中明确选择本次查询范围"
            if not bound_member_datasource_ids:
                return "semantic_query_tool 执行失败: 当前会话没有可用执行对象，请先在页面选择数据源"
            invalid_member_ids = [
                member_id
                for member_id in requested_member_datasource_ids
                if member_id not in bound_member_datasource_ids
            ]
            if invalid_member_ids:
                return f"semantic_query_tool 执行失败: 本次查询包含不可用的执行对象 {', '.join(invalid_member_ids)}"
            dsl_payload = normalize_json_like_payload(dsl)
            if not isinstance(dsl_payload, dict) or isinstance(dsl_payload.get("cards"), list):
                return "semantic_query_tool 执行失败: dsl 必须是单个查询对象"
            if "sql" in dsl_payload:
                return "semantic_query_tool 执行失败: dsl 中禁止包含 sql，请传入语义 DSL"
            save_path = str(save_as or "").strip()
            if save_path:
                if not bound_workspace_root:
                    return "semantic_query_tool 执行失败: 当前会话未初始化工作目录，无法保存结果"
                if not save_path.lower().endswith(".json"):
                    return "semantic_query_tool 执行失败: save_as 只支持 .json 文件"
            semantic_config = await get_dashboard_spec_semantic_config(bound_datasource_id, effective_semantic_config_id, user_id=bound_user_id)
            payload, warnings = remove_mixed_raw_metrics_for_aggregate_dsl(dsl_payload or {}, semantic_config)
            if payload.get("compare"):
                return "semantic_query_tool 执行失败: DSL2SQL 不支持 compare，请拆成基础 DSL 查询"
            compile_result = compile_semantic_query_dsl(
                semantic_config,
                payload,
                user_query=user_query or None,
                generator_cls=SemanticSQLGenerator,
            )
            if not compile_result.get("success"):
                return f"semantic_query_tool 执行失败: {compile_result.get('error') or 'DSL2SQL 编译失败'}"
            sql = str(compile_result.get("sql") or "").strip()
            if not sql:
                return "semantic_query_tool 执行失败: DSL2SQL 未生成 SQL"
            query_result = await _execute_sql_real(
                sql=sql,
                datasource_id=bound_datasource_id,
                member_datasource_ids=requested_member_datasource_ids,
                user_id=bound_user_id,
                semantic_config_id=effective_semantic_config_id,
                query_text=user_query or None,
                conversation_id=bound_conversation_id,
                operator_name=bound_operator_name,
            )
            if save_path:
                write_result = await sandbox_write_text_file(
                    session_ref=execution_session_ref,
                    file_path=save_path,
                    content=json_dumps(query_result),
                    mode="overwrite",
                )
                result_summary = {
                    "status": query_result_status(query_result),
                    "result_path": write_result.get("file_path") or save_path,
                    "total_count": query_result.get("total_count"),
                    "execution_time_ms": query_result.get("execution_time_ms"),
                    "error": query_result.get("error"),
                    "sql": query_result.get("sql") or sql,
                    "warnings": warnings,
                    "next_required_action": "使用 file_read 抽查文件，或用 sandbox_run 读取 result_path 生成后续产物。",
                }
                return json_dumps(result_summary)
            result_payload = dict(query_result)
            result_payload.setdefault("sql", sql)
            if warnings:
                result_payload["warnings"] = warnings
            return json_dumps(result_payload)
        except Exception as exc:
            logger.error("semantic_query_tool 执行失败: %s", exc, exc_info=True)
            return f"semantic_query_tool 执行失败: {exc}"

    return semantic_query_tool
