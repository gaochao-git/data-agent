"""DataAnalyze exec SQL tool."""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.workspace_execution import SandboxExecutionSessionRef, sandbox_write_text_file
from .common import json_dumps
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
    async def semantic_query_tool(sql: str, save_as: str | None = None, semantic_config_id: str | None = None) -> str:
        """
        执行只读 SQL 查询并返回真实数据结果。

        不传 save_as 时返回完整查询结果；传入 save_as 时直接把完整结果写入 workspace/ 或 outputs/ 下的 JSON 文件，
        并仅返回路径和摘要，适合后续 Python 脚本读取。
        """
        try:
            if not bound_datasource_id:
                return "semantic_query_tool 执行失败: 当前会话未绑定数据源"
            effective_semantic_config_id = str(semantic_config_id or bound_semantic_config_id or "").strip() or None
            query_result = await _execute_sql_real(
                sql=sql,
                datasource_id=bound_datasource_id,
                member_datasource_ids=bound_member_datasource_ids,
                user_id=bound_user_id,
                semantic_config_id=effective_semantic_config_id,
                conversation_id=bound_conversation_id,
                operator_name=bound_operator_name,
            )
            if save_as:
                if not bound_workspace_root:
                    return "semantic_query_tool 执行失败: 当前会话未初始化工作目录，无法保存结果"
                save_path = str(save_as or "").strip()
                if not save_path.lower().endswith(".json"):
                    return "semantic_query_tool 执行失败: save_as 只支持 .json 文件"
                write_result = await sandbox_write_text_file(
                    session_ref=execution_session_ref,
                    file_path=save_path,
                    content=json_dumps(query_result),
                    mode="overwrite",
                )
                payload = {
                    "success": bool(query_result.get("success")),
                    "result_path": write_result.get("file_path") or save_path,
                    "columns": query_result.get("columns", []),
                    "row_count": len(query_result.get("data", []) or []),
                    "total_count": query_result.get("total_count"),
                    "execution_time_ms": query_result.get("execution_time_ms"),
                    "error": query_result.get("error"),
                    "sql": query_result.get("sql") or sql,
                    "next_required_action": "使用 file_read 抽查文件，或用 sandbox_run 读取 result_path 生成后续产物。",
                }
                return "[semantic_query_tool]\n" + json_dumps(payload)
            return "[semantic_query_tool]\n" + json_dumps(query_result)
        except Exception as exc:
            logger.error("semantic_query_tool 执行失败: %s", exc, exc_info=True)
            return f"semantic_query_tool 执行失败: {exc}"

    return semantic_query_tool
