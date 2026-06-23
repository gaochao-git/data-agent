"""
ChatData API 路由定义

提供 ChatData 查询与配置管理相关接口
"""

import os
import asyncio
import json
import re
from typing import Optional, Dict, List, Set, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import HumanMessage
from sqlalchemy import and_, func, or_, select

from auth import AuthenticationError
from .schema import (
    NL2SQLRequest,
    QuickSqlRequest,
    ExecuteSqlRequest,
    QueryTaskCreateRequest,
    CompileSemanticBuilderSqlRequest,
    QueryFavoriteCreateRequest,
    QueryFavoriteUpdateRequest,
    TableListResponse,
    TableInfoRequest,
    TableInfoResponse,
    TableInfoResult,
    DbConnectionRequest,
    DatasourceColumnDictGenerateRequest,
    DatasourceColumnDictUpdateRequest,
    DatasourceGroupCreateRequest,
    DatasourceGroupUpdateRequest,
    DatasourceTableBusinessDescUpdateRequest,
    DatasourceTableGroupsUpdateRequest,
    DatasourceUpdateRequest,
)
from .services.datasource_manager import get_datasource_manager
from .services.datasource_credentials import resolve_hive_credentials, resolve_mysql_credentials, sanitize_source_config
from .services.mysql_sync import MySQLSyncService
from .services.hive_sync import HiveSyncService
from .services.hive_meta_http import get_hive_meta_tables_async, get_hive_meta_schema_async
from .services.sql_executor import fetch_http_query_task_result, stop_http_query_task, kill_mysql_query
from .services import query_runtime
from .services.semantic_builder_compiler import compile_semantic_builder_sql
from .semantic.utils import parse_llm_json
from .services.llm_config import get_chat_data_llm
from .handlers import _stream_sql_generation, _build_execution_target_info_async
from .agent import get_all_tables_async, get_tables_create_format
from .agent.sql_assist_graph import get_sql_assist_graph
from .agent.prompts.nl2sql import SQL_GENERATOR_PROMPT_GENERATE
from utils.lang_util.database import get_session
from utils.lang_util import conversation
from utils.lang_util.models import (
    SqlQueryFavorite,
    ChatDataQueryTask,
    ChatDataQueryExecution,
)
from utils.logger import get_logger
from .route_modules.common import _build_table_columns_map, _get_current_user, _optional_current_user, business_response
from .services.runtime_config import read_positive_int_env
from .route_modules.config import router as config_router
from .route_modules.evaluation import router as evaluation_router
from .route_modules.data_analyze_workspace import router as data_analyze_workspace_router
from .route_modules.skills import router as skills_router
from .route_modules.dashboard import DashboardRuntime, configure_dashboard_runtime, router as dashboard_router
from .route_modules.semantic_config import router as semantic_config_router
from .route_modules.config import (
    list_chat_data_models,
    get_chat_data_config,
)
from .route_modules.evaluation import (
    run_accuracy_evaluation,
    run_accuracy_evaluation_stream,
    list_accuracy_evaluation_runs,
    get_accuracy_evaluation_run_detail,
    get_scene_evaluation_cases,
    save_scene_evaluation_cases,
    generate_scene_evaluation_cases,
    draft_scene_evaluation_case,
)
from .route_modules.data_analyze_workspace import (
    list_data_analyze_workspace_files,
    read_data_analyze_workspace_file,
    import_data_analyze_workspace_skill,
)
from .route_modules.skills import (
    list_skills,
    list_my_skills,
    list_favorite_skills,
    create_user_skill,
    update_user_skill,
    delete_user_skill,
    copy_user_skill,
    publish_user_skill,
    list_skill_versions,
    get_skill_version,
    list_skill_version_files,
    read_skill_version_file,
    save_skill_version_file,
    delete_skill_version_file,
    rollback_skill_version,
    favorite_skill,
    unfavorite_skill,
    sync_system_skills,
    reload_skills,
    get_skill,
)
from .route_modules.dashboard import (
    _run_dashboard_batch_task,
    _run_dashboard_panel_task,
)
import time

logger = get_logger(__name__)
SQL_ASSIST_AGENT_ID = "chat_data_sql_assist"
DATA_ANALYZE_AGENT_ID = "chat_data_data_analyze"
QUERY_TASK_POLL_INTERVAL_MS = 1500
CANCEL_TARGET_WAIT_SECONDS = 3
CANCEL_TARGET_POLL_INTERVAL_SECONDS = 0.1
_query_task_background_tasks: set[asyncio.Task] = set()
QUERY_TASK_FINAL_STATUSES = {"succeeded", "failed", "cancelled", "partial_success"}

SOURCE_DEFAULT_PORTS = {
    "mysql": 3306,
    "hive": 10000,
}


DATASOURCE_SYNC_TIMEOUT_SECONDS = read_positive_int_env("CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS", 30)


def _is_http_query_executor(source_config: Optional[Dict[str, Any]]) -> bool:
    """判断当前数据源连接项是否走 HTTP 执行。"""
    return str((source_config or {}).get("query_executor_type") or "direct").strip().lower() == "http"


def _get_optional_request_user_http_params(request: Optional[Request]) -> Dict[str, str]:
    """尽量从请求中提取用户参数，不改变原接口鉴权行为。"""
    user = _optional_current_user(request) if request is not None else None
    if user is None:
        return {}
    user_id = str(getattr(user, "user_id", "") or "").strip()
    username = str(getattr(user, "username", "") or "").strip()
    params: Dict[str, str] = {}
    if user_id:
        params["user_id"] = user_id
    if username:
        params["username"] = username
    return params


# 代理标识
AGENT_ID = "chat_data"

# 创建路由器
router = APIRouter(
    prefix="/api/chatdata",
    tags=["ChatData NL2SQL"]
)
router.include_router(config_router)
router.include_router(evaluation_router)
router.include_router(data_analyze_workspace_router)
router.include_router(skills_router)
router.include_router(dashboard_router)
router.include_router(semantic_config_router)


def _normalize_query_history_source_filter(source: Optional[str]) -> Optional[List[str]]:
    """规整查询历史来源筛选参数。"""
    normalized = str(source or "").strip().lower()
    if not normalized or normalized == "all":
        return None
    if normalized == "sql":
        return ["sql", ""]
    if normalized == "builder":
        return ["builder"]
    if normalized in {"agent", "conversation"}:
        return ["agent", "conversation"]
    if normalized == "dashboard":
        return ["dashboard"]
    return None


def _normalize_query_history_status_filter(status: Optional[str]) -> Optional[str]:
    """规整查询历史执行状态筛选参数。"""
    normalized = str(status or "").strip().lower()
    if normalized in {"pending", "running", "succeeded", "failed", "cancelled", "timeout", "kill_failed"}:
        return normalized
    return None


def _get_query_history_source(query_type: Optional[str]) -> str:
    """将查询类型映射为前端展示来源。"""
    normalized_type = str(query_type or "").strip().lower()
    if normalized_type == "builder":
        return "可视化查询"
    if normalized_type in {"agent", "conversation"}:
        return "智能问数"
    if normalized_type == "dashboard":
        return "看板"
    return "SQL编辑器"


def _is_http_query_execution(execution: ChatDataQueryExecution) -> bool:
    """判断执行明细是否为 HTTP 远端查询。"""
    return bool(str(getattr(execution, "remote_task_id", "") or "").strip())


def _is_http_execution_target(execution_target: Dict[str, Any]) -> bool:
    """判断执行目标是否走 HTTP 查询。"""
    return str((execution_target or {}).get("query_executor_type") or "").strip().lower() == "http"


def _resolve_remote_history_source_type(execution: ChatDataQueryExecution, task: Optional[ChatDataQueryTask]) -> str:
    """从任务远端映射里解析历史 HTTP 查询的数据源类型。"""
    member_datasource_id = str(execution.member_datasource_id or "").strip()
    for remote_task in _load_remote_tasks(task.remote_task_payload if task else None):
        if str(remote_task.get("member_datasource_id") or "").strip() == member_datasource_id:
            return str(remote_task.get("source_type") or "hive").strip().lower() or "hive"
    return "hive"


async def _build_remote_history_result_payload(execution: ChatDataQueryExecution, task: Optional[ChatDataQueryTask] = None) -> Optional[Dict[str, Any]]:
    """按远端任务 ID 实时读取历史查询结果。"""
    remote_task_id = str(execution.remote_task_id or "").strip()
    if not remote_task_id:
        return None
    operator_name = str(execution.create_by_name or execution.update_by_name or "").strip()
    if not operator_name and task:
        operator_name = _get_task_operator_name(_build_query_task_runtime_data(task))
    try:
        remote_result = await fetch_http_query_task_result(
            remote_task_id,
            _resolve_remote_history_source_type(execution, task),
            user_id=str(execution.user_id or "").strip() or None,
            username=operator_name or None,
        )
    except Exception as exc:
        logger.warning("获取远端历史查询结果失败: execution_id=%s remote_task_id=%s error=%s", execution.execution_id, remote_task_id, exc)
        return {
            "success": False,
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": execution.execution_time_ms,
            "error": "历史结果已不可用或获取失败",
            "remote_result_status": "unavailable",
        }
    status = str((remote_result or {}).get("status") or "").strip().lower()
    if status != "succeeded":
        error_message = str((remote_result or {}).get("error") or "").strip() or "历史结果已不可用或仍在处理中"
        return {
            "success": False,
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": (remote_result or {}).get("execution_time_ms"),
            "error": error_message,
            "remote_result_status": status or "unknown",
        }
    return {
        "success": True,
        "columns": (remote_result or {}).get("columns") or [],
        "data": (remote_result or {}).get("data") or [],
        "total_count": int((remote_result or {}).get("total_count") or 0),
        "execution_time_ms": (remote_result or {}).get("execution_time_ms"),
        "error": None,
        "remote_result_status": status,
    }


def _get_sql_dialect_label(source_type: Optional[str]) -> str:
    """将数据源类型映射为提示词使用的 SQL 方言标签。"""
    normalized_type = str(source_type or "").strip().lower()
    if normalized_type == "hive":
        return "Hive SQL"
    return "MySQL"


def _normalize_source_item(
    source_type: str,
    host: str,
    port: Optional[int],
    database: str,
    org_name: Optional[str] = None,
    org_code: Optional[str] = None,
    query_executor_type: Optional[str] = None,
) -> Dict[str, Any]:
    """标准化数据源连接项。"""
    normalized_host = str(host or "").strip()
    normalized_database = str(database or "").strip()
    normalized_org_name = str(org_name or "").strip()
    normalized_org_code = str(org_code or "").strip()
    normalized_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    if not normalized_org_name:
        raise ValueError("机构名称不能为空")
    if not normalized_org_code:
        raise ValueError("机构编码不能为空")
    if normalized_executor_type not in {"direct", "http"}:
        raise ValueError(f"不支持的查询执行方式: {normalized_executor_type}")
    if not normalized_database:
        raise ValueError("数据库名不能为空")
    if normalized_executor_type == "direct" and not normalized_host:
        raise ValueError("数据源连接信息不完整")
    if normalized_executor_type == "http" and (normalized_host or port is not None):
        raise ValueError("HTTP 模式只填写数据库名，不填写 IP 和端口")
    if normalized_executor_type == "direct":
        result = {
            "host": normalized_host,
            "port": int(port or SOURCE_DEFAULT_PORTS[source_type]),
            "database": normalized_database,
            "query_executor_type": normalized_executor_type,
        }
    else:
        result = {
            "database": normalized_database,
            "query_executor_type": normalized_executor_type,
        }
    result["org_name"] = normalized_org_name
    result["org_code"] = normalized_org_code
    return result


async def _load_tables_from_source(source_type: str, source_item: Dict[str, Any], table_names: Optional[List[str]] = None, user_id: Optional[str] = None, username: Optional[str] = None) -> List[Dict[str, Any]]:
    """从模板数据源拉取表结构。"""
    if _is_http_query_executor(source_item) and source_type != "hive":
        raise ValueError("HTTP 元数据模式当前仅支持 Hive 数据源")
    if source_type == "hive":
        if _is_http_query_executor(source_item):
            return await get_hive_meta_schema_async(source_item, table_names, user_id=user_id, username=username)
        hive_credentials = resolve_hive_credentials(source_item)
        service = HiveSyncService(
            host=source_item.get("host", "localhost"),
            port=source_item.get("port", 10000),
            database=source_item.get("database", "default"),
            username=hive_credentials.get("username"),
            password=hive_credentials.get("password"),
        )
        return await asyncio.wait_for(
            service.get_full_schema_async(table_names),
            timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
        )

    mysql_credentials = resolve_mysql_credentials(source_item)
    service = MySQLSyncService(
        host=source_item.get("host", "localhost"),
        port=source_item.get("port", 3306),
        database=source_item.get("database", ""),
        user=mysql_credentials["user"],
        password=mysql_credentials["password"],
    )
    return await asyncio.wait_for(
        service.get_full_schema_async(table_names),
        timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
    )


def _get_datasource_meta_source_config(datasource: Dict[str, Any]) -> Dict[str, Any]:
    """构建元数据接口使用的模板源配置。"""
    source_config = dict(datasource.get("source_config") or {})
    source_config.pop("namespace", None)
    source_config.pop("orgCode", None)
    source_config.pop("orgName", None)
    template_member_id = str(datasource.get("template_datasource_id") or "").strip()
    template_member = None
    for member in datasource.get("member_sources") or []:
        member_id = str((member or {}).get("member_datasource_id") or "").strip()
        if template_member_id and member_id != template_member_id:
            continue
        template_member = member
        break
    if not template_member and datasource.get("member_sources"):
        template_member = (datasource.get("member_sources") or [])[0]
    org_code = str((template_member or {}).get("org_code") or "").strip()
    if org_code:
        source_config["org_code"] = org_code
    org_name = str((template_member or {}).get("org_name") or "").strip()
    if org_name:
        source_config["org_name"] = org_name
    query_executor_type = str((template_member or {}).get("query_executor_type") or datasource.get("query_executor_type") or "direct").strip().lower()
    source_config["query_executor_type"] = query_executor_type if query_executor_type in {"direct", "http"} else "direct"
    return source_config


def _get_datasource_physical_source_config(datasource: Dict[str, Any]) -> Dict[str, Any]:
    """构建元数据实时读取与校验表结构共用的物理源配置。"""
    source_type = datasource.get("source_type")
    if source_type == "hive":
        return _get_datasource_meta_source_config(datasource)
    return dict(datasource.get("source_config") or {})


def _column_type_label(column: Dict[str, Any]) -> str:
    """提取字段类型用于表结构差异比较。"""
    return str(column.get("mysql_type") or column.get("hive_type") or column.get("type") or "").strip().lower()


def _build_table_schema_map(tables: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """构建表字段类型索引。"""
    schema: Dict[str, Dict[str, str]] = {}
    for table in tables or []:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            continue
        schema[table_name] = {
            str(column.get("name") or "").strip(): _column_type_label(column)
            for column in table.get("columns", []) or []
            if str(column.get("name") or "").strip()
        }
    return schema


def _schema_health_issue(level: str, issue_type: str, message: str, target: str) -> Dict[str, Any]:
    """构造表结构校验问题项。"""
    return {"level": level, "type": issue_type, "message": message, "target": target}


def _compare_datasource_schema(cached_tables: List[Dict[str, Any]], physical_tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """校验缓存表结构是否仍存在于真实物理表结构中。"""
    cached_schema = _build_table_schema_map(cached_tables)
    physical_schema = _build_table_schema_map(physical_tables)
    issues: List[Dict[str, Any]] = []

    for table_name in sorted(set(cached_schema) - set(physical_schema)):
        issues.append(_schema_health_issue("error", "physical_table_removed", f"物理库已不存在表: {table_name}", f"table:{table_name}"))

    for table_name in sorted(set(cached_schema) & set(physical_schema)):
        cached_columns = cached_schema[table_name]
        physical_columns = physical_schema[table_name]
        for column_name in sorted(set(cached_columns) - set(physical_columns)):
            issues.append(_schema_health_issue("error", "physical_column_removed", f"物理库已不存在字段: {table_name}.{column_name}", f"column:{table_name}.{column_name}"))
        for column_name in sorted(set(cached_columns) & set(physical_columns)):
            cached_type = cached_columns[column_name]
            physical_type = physical_columns[column_name]
            if cached_type and physical_type and cached_type != physical_type:
                issues.append(_schema_health_issue("warning", "physical_column_type_changed", f"字段类型变化: {table_name}.{column_name}，缓存为 {cached_type}，物理库为 {physical_type}", f"column:{table_name}.{column_name}"))

    status = "invalid" if any(item["level"] == "error" for item in issues) else "warning" if issues else "ok"
    return {
        "status": status,
        "summary": {
            "cached_table_count": len(cached_schema),
            "physical_table_count": len(physical_schema),
            "issue_count": len(issues),
            "error_count": sum(1 for issue in issues if issue.get("level") == "error"),
            "warning_count": sum(1 for issue in issues if issue.get("level") == "warning"),
        },
        "issues": issues,
    }


async def _build_datasource_schema_health(datasource: Dict[str, Any]) -> Dict[str, Any]:
    """拉取真实物理表结构并校验当前缓存表字段是否仍存在。"""
    source_type = datasource.get("source_type")
    if source_type not in ("mysql", "hive"):
        return {
            "status": "warning",
            "summary": {"cached_table_count": len(datasource.get("ds_tables") or []), "physical_table_count": 0, "issue_count": 1, "error_count": 0, "warning_count": 1},
            "issues": [_schema_health_issue("warning", "physical_schema_unsupported", f"暂不支持校验该数据源类型的物理表结构: {source_type or '未知'}", "datasource")],
        }
    try:
        physical_tables = await _load_tables_from_source(source_type, _get_datasource_physical_source_config(datasource), None)
        return _compare_datasource_schema(datasource.get("ds_tables") or [], physical_tables)
    except Exception as e:
        logger.error("物理表结构校验失败: datasource_id=%s error=%s", datasource.get("id"), e, exc_info=True)
        return {
            "status": "invalid",
            "summary": {"cached_table_count": len(datasource.get("ds_tables") or []), "physical_table_count": 0, "issue_count": 1, "error_count": 1, "warning_count": 0},
            "issues": [_schema_health_issue("error", "physical_schema_check_failed", f"连接物理库读取表结构失败: {str(e)}", "datasource")],
        }


def _extract_expression_identifiers(expression: str) -> Set[str]:
    """从简单 SQL 表达式中提取可能的字段标识符。"""
    text = str(expression or "")
    if not text:
        return set()
    without_strings = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", text).replace("`", "")
    identifiers = set()
    sql_keywords = {
        "and", "as", "asc", "between", "case", "cast", "count", "date", "date_format",
        "desc", "distinct", "else", "end", "from", "if", "in", "is", "max", "min", "not",
        "null", "or", "sum", "avg", "then", "trim", "upper", "lower", "when", "year",
    }
    pattern = r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)*"
    for match in re.finditer(pattern, without_strings):
        raw = match.group(0)
        candidate = re.split(r"\s*\.\s*", raw)[-1]
        next_char_index = match.end()
        while next_char_index < len(without_strings) and without_strings[next_char_index].isspace():
            next_char_index += 1
        if next_char_index < len(without_strings) and without_strings[next_char_index] == "(":
            continue
        if candidate and candidate.lower() not in sql_keywords:
            identifiers.add(candidate)
    return identifiers


def _is_count_all_expression(expression: str) -> bool:
    """判断是否为不依赖具体字段的总数计数表达式。"""
    normalized = re.sub(r"\s+", "", str(expression or "")).upper()
    return normalized in {"COUNT(*)", "COUNT(1)"}


def _semantic_health_issue(level: str, issue_type: str, message: str, target: str, config: Optional[Dict] = None) -> Dict[str, Any]:
    """构造语义健康检查问题项。"""
    issue = {
        "level": level,
        "type": issue_type,
        "message": message,
        "target": target,
    }
    if config:
        issue["config_id"] = config.get("id") or config.get("semantic_config_id")
        issue["config_name"] = config.get("name") or ""
    return issue


def _validate_semantic_config_health(datasource: Dict, config: Dict) -> Dict[str, Any]:
    """校验单个指标集是否仍匹配当前物理表结构。"""
    table_columns_map = _build_table_columns_map(datasource)
    issues: List[Dict[str, Any]] = []
    if not table_columns_map:
        issues.append(_semantic_health_issue("error", "missing_schema", "当前数据源缺少表结构信息，请在数据源组编辑页拉取表信息并保存", "datasource", config))
        return {"config_id": config.get("id") or config.get("semantic_config_id"), "config_name": config.get("name") or "", "status": "invalid", "issues": issues}

    for dim in config.get("dimensions") or []:
        dim_name = dim.get("name") or dim.get("field_name") or ""
        table_name = dim.get("table")
        column_name = dim.get("column")
        target = f"dimension:{dim_name}"
        if not table_name or table_name not in table_columns_map:
            issues.append(_semantic_health_issue("error", "missing_table", f"维度 {dim_name} 所属表不存在: {table_name}", target, config))
            continue
        if not column_name or column_name not in table_columns_map[table_name]:
            issues.append(_semantic_health_issue("error", "missing_column", f"维度 {dim_name} 字段不存在: {table_name}.{column_name}", target, config))
        expression = str(dim.get("expression") or "").strip()
        if expression:
            for identifier in _extract_expression_identifiers(expression):
                if identifier not in table_columns_map[table_name]:
                    issues.append(_semantic_health_issue("warning", "expression_column_changed", f"维度 {dim_name} 表达式疑似引用已变化字段: {table_name}.{identifier}", target, config))

    for metric in config.get("metrics") or []:
        metric_name = metric.get("name") or metric.get("field_name") or ""
        table_name = metric.get("table")
        expression = str(metric.get("expression") or "").strip()
        target = f"metric:{metric_name}"
        if not table_name or table_name not in table_columns_map:
            issues.append(_semantic_health_issue("error", "missing_table", f"指标 {metric_name} 所属表不存在: {table_name}", target, config))
            continue
        if _is_count_all_expression(expression):
            continue
        identifiers = _extract_expression_identifiers(expression)
        if not identifiers:
            issues.append(_semantic_health_issue("warning", "expression_unparsed", f"指标 {metric_name} 表达式未识别到物理字段: {expression or '-'}", target, config))
            continue
        missing_identifiers = identifiers - table_columns_map[table_name]
        for identifier in sorted(missing_identifiers):
            issues.append(_semantic_health_issue("error", "expression_column_missing", f"指标 {metric_name} 表达式引用不存在字段: {table_name}.{identifier}", target, config))

    for index, rel in enumerate(config.get("relationships") or [], start=1):
        left_table = rel.get("left_table")
        right_table = rel.get("right_table")
        target = f"relationship:{left_table}->{right_table}"
        if not left_table or left_table not in table_columns_map:
            issues.append(_semantic_health_issue("error", "missing_table", f"关系 {index} 左表不存在: {left_table}", target, config))
            continue
        if not right_table or right_table not in table_columns_map:
            issues.append(_semantic_health_issue("error", "missing_table", f"关系 {index} 右表不存在: {right_table}", target, config))
            continue
        conditions = rel.get("join_conditions") or [{"left_column": rel.get("left_column"), "right_column": rel.get("right_column")}]
        for cond_index, cond in enumerate(conditions, start=1):
            left_column = (cond or {}).get("left_column")
            right_column = (cond or {}).get("right_column")
            if not left_column or left_column not in table_columns_map[left_table]:
                issues.append(_semantic_health_issue("error", "missing_join_column", f"关系 {index}.{cond_index} 左字段不存在: {left_table}.{left_column}", target, config))
            if not right_column or right_column not in table_columns_map[right_table]:
                issues.append(_semantic_health_issue("error", "missing_join_column", f"关系 {index}.{cond_index} 右字段不存在: {right_table}.{right_column}", target, config))

    status = "invalid" if any(item["level"] == "error" for item in issues) else "warning" if issues else "ok"
    return {
        "config_id": config.get("id") or config.get("semantic_config_id"),
        "config_name": config.get("name") or "",
        "status": status,
        "issues": issues,
    }


def _build_datasource_semantic_health(datasource: Dict, configs: List[Dict]) -> Dict[str, Any]:
    """构造数据源下所有指标集的健康检查结果。"""
    config_results = [_validate_semantic_config_health(datasource, config) for config in configs or []]
    all_issues = [issue for result in config_results for issue in result.get("issues") or []]
    status = "invalid" if any(issue["level"] == "error" for issue in all_issues) else "warning" if all_issues else "ok"
    return {
        "datasource_id": datasource.get("id"),
        "group_name": datasource.get("group_name") or "",
        "status": status,
        "summary": {
            "config_count": len(config_results),
            "issue_count": len(all_issues),
            "error_count": sum(1 for issue in all_issues if issue.get("level") == "error"),
            "warning_count": sum(1 for issue in all_issues if issue.get("level") == "warning"),
        },
        "configs": config_results,
        "issues": all_issues,
    }


def _merge_health_status(*statuses: str) -> str:
    """合并多层健康状态。"""
    if any(status == "invalid" for status in statuses):
        return "invalid"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "ok"


@router.get("/query-favorites")
async def get_query_favorites(
    request: Request,
    datasource_id: str,
    query_type: Optional[str] = None,
    limit: int = 50
):
    """获取收藏查询卡片。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        async with get_session() as db:
            stmt = select(SqlQueryFavorite).filter_by(datasource_id=datasource_id, delete_flag=0)
            stmt = stmt.filter_by(user_id=user_id)
            if query_type:
                stmt = stmt.filter(SqlQueryFavorite.query_type == query_type)
            stmt = stmt.order_by(SqlQueryFavorite.update_time.desc(), SqlQueryFavorite.create_time.desc()).limit(limit)
            result = await db.execute(stmt)
            favorites = result.scalars().all()
            results = [q.to_dict() for q in favorites]
            return business_response(data={"queries": results, "total": len(results)}, msg="获取成功")
    except Exception as e:
        logger.error("获取收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="获取收藏查询失败，请稍后重试", code=50012, status="error")


@router.get("/query-history")
async def get_query_history(
    request: Request,
    datasource_id: str,
    page: int = 1,
    page_size: int = 10,
    source: Optional[str] = None,
    status: Optional[str] = None,
):
    """获取当前用户的查询历史。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        normalized_page = max(1, page)
        normalized_page_size = max(1, min(page_size, 100))
        source_filter = _normalize_query_history_source_filter(source)
        status_filter = _normalize_query_history_status_filter(status)
        async with get_session() as db:
            base_conditions = [
                ChatDataQueryExecution.datasource_id == datasource_id,
                ChatDataQueryExecution.user_id == user_id,
                ChatDataQueryExecution.delete_flag == 0,
            ]
            if source_filter:
                if "" in source_filter:
                    base_conditions.append(or_(ChatDataQueryTask.query_type.in_([item for item in source_filter if item]), ChatDataQueryTask.query_type.is_(None)))
                else:
                    base_conditions.append(ChatDataQueryTask.query_type.in_(source_filter))
            if status_filter:
                if status_filter == "cancelled":
                    base_conditions.append(or_(ChatDataQueryTask.task_status == "cancelled", ChatDataQueryExecution.sql_status == "cancelled"))
                else:
                    base_conditions.append(and_(or_(ChatDataQueryTask.task_status.is_(None), ChatDataQueryTask.task_status != "cancelled"), ChatDataQueryExecution.sql_status == status_filter))

            count_stmt = (
                select(func.count())
                .select_from(ChatDataQueryExecution)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(*base_conditions)
            )
            total_result = await db.execute(count_stmt)
            total = int(total_result.scalar() or 0)

            stmt = (
                select(ChatDataQueryExecution, ChatDataQueryTask)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(*base_conditions)
                .order_by(ChatDataQueryExecution.create_time.desc(), ChatDataQueryExecution.id.desc())
                .offset((normalized_page - 1) * normalized_page_size)
                .limit(normalized_page_size)
            )
            result = await db.execute(stmt)
            queries = []
            for item, task in result.all():
                query_type = task.query_type if task else "sql"
                task_status = str(task.task_status or "").strip().lower() if task else ""
                task_operator_name = (
                    str(item.create_by_name or item.update_by_name or "").strip()
                    or (str(task.create_by_name or task.update_by_name or "").strip() if task else "")
                    or (_get_task_operator_name(_build_query_task_runtime_data(task)) if task else "")
                )
                display_sql_status = "cancelled" if task_status == "cancelled" else item.sql_status
                success_flag = 1 if display_sql_status == "succeeded" else 0
                payload = item.to_dict()
                payload.pop("result_payload", None)
                payload.update({
                    "query_type": query_type,
                    "task_status": task_status,
                    "sql_status": display_sql_status,
                    "title": "SQL查询",
                    "last_used": payload.get("finished_time") or payload.get("started_time") or payload.get("create_time"),
                    "executed_at": payload.get("finished_time") or payload.get("started_time") or payload.get("create_time"),
                    "source": _get_query_history_source(query_type),
                    "success_flag": success_flag,
                    "operator_name": task_operator_name,
                    "has_result": bool(item.result_payload or item.remote_task_id),
                })
                queries.append(payload)
            return business_response(
                data={
                    "queries": queries,
                    "total": total,
                    "page": normalized_page,
                    "page_size": normalized_page_size,
                },
                msg="获取成功",
            )
    except Exception as e:
        logger.error("获取查询历史失败: %s", e, exc_info=True)
        return business_response(msg="获取查询历史失败，请稍后重试", code=50016, status="error")


@router.get("/query-history/{execution_id}/result")
async def get_query_history_result(execution_id: str, request: Request):
    """按执行ID获取查询历史结果。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        async with get_session() as db:
            result = await db.execute(
                select(ChatDataQueryExecution, ChatDataQueryTask)
                .outerjoin(ChatDataQueryTask, ChatDataQueryTask.task_id == ChatDataQueryExecution.task_id)
                .where(
                    ChatDataQueryExecution.execution_id == execution_id,
                    ChatDataQueryExecution.user_id == user_id,
                    ChatDataQueryExecution.delete_flag == 0,
                )
            )
            row = result.first()
            execution = row[0] if row else None
            task = row[1] if row else None
            if not execution:
                return business_response(msg="查询历史不存在", code=40404, status="error")
            payload = execution.to_dict()
            result_payload = payload.get("result_payload")
            if _is_http_query_execution(execution):
                result_payload = await _build_remote_history_result_payload(execution, task)
            return business_response(
                data={
                    "execution_id": execution.execution_id,
                    "query": execution.sql_text,
                    "member_datasource_id": execution.member_datasource_id,
                    "member_name": execution.member_name,
                    "execution_time_ms": execution.execution_time_ms,
                    "error_message": execution.error_message,
                    "result_payload": result_payload,
                },
                msg="获取成功",
            )
    except Exception as e:
        logger.error("获取查询历史结果失败: execution_id=%s error=%s", execution_id, e, exc_info=True)
        return business_response(msg="获取查询历史结果失败，请稍后重试", code=50016, status="error")


@router.post("/query-favorites")
async def create_query_favorite(payload: QueryFavoriteCreateRequest, request: Request):
    """创建查询收藏。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        if payload.query_type == "builder" and payload.builder_config is None:
            return business_response(msg="builder 类型收藏必须包含 builder_config", code=40012, status="error")

        builder_config_json = None
        if payload.builder_config is not None:
            builder_config_json = json.dumps(payload.builder_config, ensure_ascii=False)

        now = datetime.now()
        async with get_session() as db:
            item = SqlQueryFavorite(
                datasource_id=payload.datasource_id,
                user_id=user_id,
                semantic_config_id=payload.semantic_config_id,
                query_type=payload.query_type,
                favorite_title=payload.favorite_title.strip(),
                query_text=payload.query_text,
                builder_config=builder_config_json,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=username,
                update_by=user_id,
                update_by_name=username,
            )
            db.add(item)
            await db.flush()
            return business_response(data=item.to_dict(), msg="收藏成功")
    except Exception as e:
        logger.error("创建收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="创建收藏查询失败，请稍后重试", code=50013, status="error")


@router.put("/query-favorites/{favorite_id}")
async def update_query_favorite(favorite_id: int, payload: QueryFavoriteUpdateRequest, request: Request):
    """更新查询收藏。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        async with get_session() as db:
            stmt = select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            result = await db.execute(stmt)
            item = result.scalar_one_or_none()
            if item is None:
                return business_response(msg="收藏不存在", code=40412, status="error")

            if payload.favorite_title is not None:
                item.favorite_title = payload.favorite_title.strip()

            if item.query_type == "builder":
                # builder 收藏原始配置不可编辑
                if payload.query_text is not None or payload.builder_config is not None:
                    return business_response(msg="builder 收藏不支持编辑原始内容", code=40013, status="error")
            else:
                if payload.query_text is not None:
                    item.query_text = payload.query_text
                if payload.builder_config is not None:
                    item.builder_config = json.dumps(payload.builder_config, ensure_ascii=False)

            if payload.semantic_config_id is not None:
                item.semantic_config_id = payload.semantic_config_id

            item.update_time = datetime.now()
            item.update_by = user_id
            item.update_by_name = username
            await db.flush()
            return business_response(data=item.to_dict(), msg="更新成功")
    except Exception as e:
        logger.error("更新收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="更新收藏查询失败，请稍后重试", code=50014, status="error")


@router.delete("/query-favorites/{favorite_id}")
async def delete_query_favorite(favorite_id: int, request: Request):
    """删除查询收藏（软删除）。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        async with get_session() as db:
            stmt = select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            result = await db.execute(stmt)
            item = result.scalar_one_or_none()
            if item is None:
                return business_response(msg="收藏不存在", code=40412, status="error")
            item.delete_flag = 1
            item.update_time = datetime.now()
            item.update_by = user_id
            item.update_by_name = username
            await db.flush()
            return business_response(data={"id": favorite_id}, msg="删除成功")
    except Exception as e:
        logger.error("删除收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="删除收藏查询失败，请稍后重试", code=50015, status="error")


@router.post("/nl2sql")
async def quick_sql_endpoint(payload: QuickSqlRequest, request: Request = None):
    """
    快速 SQL 生成接口（流式输出，用于 SQL 编辑器的 AI 辅助）

    简化的 NL2SQL 接口，流式返回生成的 SQL，不走完整的 Agent 流程。

    Args:
        request: 快速 SQL 请求对象
            - query: 自然语言查询
            - datasource_id: 数据源ID

    Returns:
        SSE 流式响应，包含生成的 SQL
    """
    async def generate_stream():
        try:
            logger.info(f"收到快速SQL生成请求: {payload.query[:100]}...")
            ds_manager = get_datasource_manager()
            datasource = await ds_manager.get_datasource_async(payload.datasource_id)
            sql_dialect = _get_sql_dialect_label((datasource or {}).get("source_type"))

            # 获取表结构
            tables = await get_all_tables_async(payload.datasource_id)
            if not tables:
                yield f"data: {json.dumps({'type': 'error', 'message': '数据源暂无表结构信息'}, ensure_ascii=False)}\n\n"
                return

            # 获取所有表的 DDL 格式
            table_names = [t.get("table_name") for t in tables]
            tables_ddl_info = get_tables_create_format(tables, table_names)

            # 构建 prompt
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            execution_target_info = await _build_execution_target_info_async(payload.datasource_id, payload.member_datasource_ids)
            system_prompt = SQL_GENERATOR_PROMPT_GENERATE.format(
                current_time=current_time,
                execution_target_info=execution_target_info,
                tables_ddl_info=tables_ddl_info,
                sql_dialect=sql_dialect,
            )
            if not payload.conversation_id:
                yield f"data: {json.dumps({'type': 'error', 'message': '缺少 SQL 辅助会话ID'}, ensure_ascii=False)}\n\n"
                return

            # 流式输出
            full_content = ""
            from .agent import get_checkpointer
            current_user_id = "system"
            if request is not None:
                current_user = _get_current_user(request)
                current_user_id = getattr(current_user, "user_id", None) or getattr(current_user, "username", None) or "system"
            existing_agent_id = await conversation.get_agent_id("", payload.conversation_id, current_user_id)
            if existing_agent_id and existing_agent_id != SQL_ASSIST_AGENT_ID:
                yield f"data: {json.dumps({'type': 'error', 'message': '对话不存在'}, ensure_ascii=False)}\n\n"
                return
            if not existing_agent_id:
                unowned_agent_id = await conversation.get_agent_id("", payload.conversation_id)
                if unowned_agent_id:
                    yield f"data: {json.dumps({'type': 'error', 'message': '对话不存在'}, ensure_ascii=False)}\n\n"
                    return
            await conversation.get_or_create_conversation("", SQL_ASSIST_AGENT_ID, payload.conversation_id, current_user_id)

            config = {"configurable": {"thread_id": payload.conversation_id, "checkpoint_ns": ""}}
            initial_state = {
                "messages": [HumanMessage(content=payload.query, additional_kwargs={"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})],
                "tables_ddl_info": tables_ddl_info,
                "loaded_table_names": table_names,
                "mode": "generate",
                "datasource_id": payload.datasource_id,
                "user_id": current_user_id,
                "member_datasource_ids": payload.member_datasource_ids or [],
                "execution_target_info": execution_target_info,
            }
            async with get_checkpointer() as checkpointer:
                graph = get_sql_assist_graph(
                    checkpointer,
                    sql_dialect=sql_dialect,
                    execution_target_info=execution_target_info,
                )
                async for chunk, metadata in graph.astream(initial_state, config=config, stream_mode="messages"):
                    if metadata.get("langgraph_node") != "sql_assist":
                        continue
                    if hasattr(chunk, 'content') and chunk.content:
                        full_content += chunk.content
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.content}, ensure_ascii=False)}\n\n"

            # 提取 SQL（从 ```sql ... ``` 代码块中提取）
            sql_match = re.search(r'```sql\s*(.*?)\s*```', full_content, re.DOTALL | re.IGNORECASE)
            if sql_match:
                sql = sql_match.group(1).strip()
            else:
                # 如果没有找到代码块，尝试直接返回内容
                sql = full_content.strip()

            logger.info(f"生成的 SQL: {sql[:200]}...")

            # 发送完成事件（包含提取的纯 SQL）
            yield f"data: {json.dumps({'type': 'complete', 'sql': sql}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"快速SQL生成失败: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream"
    )


@router.get("/datasources")
async def get_datasources(request: Request, include_tables: bool = True):
    """
    获取所有数据源列表

    Returns:
        数据源列表
    """
    try:
        ds_manager = get_datasource_manager()

        datasources = await ds_manager.get_all_datasources_async(include_tables)

        return business_response(
            data={"datasources": datasources},
            msg="获取成功"
        )

    except Exception as e:
        logger.error(f"获取数据源列表失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50005,
            status="error"
        )


@router.post("/datasources/groups")
async def create_datasource_group(body: DatasourceGroupCreateRequest, request: Request):
    """创建数据源组。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        group_name = (body.group_name or "").strip()
        if not group_name:
            return business_response(
                msg="数据源组名称不能为空",
                code=40001,
                status="error"
            )

        ds_manager = get_datasource_manager()
        exists = await ds_manager.get_datasource_by_group_name_async(group_name)
        if exists:
            return business_response(
                msg=f"数据源组名称已存在: {group_name}",
                code=40001,
                status="error"
            )

        group_desc = (body.group_desc or "").strip()
        template_source = _normalize_source_item(
            body.source_type,
            body.template_source.host,
            body.template_source.port,
            body.template_source.database,
            body.template_source.org_name,
            body.template_source.org_code,
            body.template_source.query_executor_type,
        )
        member_sources = [
            _normalize_source_item(
                body.source_type,
                item.host,
                item.port,
                item.database,
                item.org_name,
                item.org_code,
                item.query_executor_type,
            )
            for item in (body.member_sources or [])
        ]
        selected_table_names = []
        for table_name in body.template_table_names or []:
            normalized_table_name = str(table_name or "").strip()
            if normalized_table_name and normalized_table_name not in selected_table_names:
                selected_table_names.append(normalized_table_name)
        tables = await _load_tables_from_source(
            body.source_type,
            template_source,
            selected_table_names or None,
            user_id=current_user.user_id,
            username=current_user.username,
        )
        datasource = await ds_manager.create_datasource_group_from_sources_async(
            group_name=group_name,
            group_desc=group_desc,
            source_type=body.source_type,
            template_source_config=template_source,
            member_source_configs=member_sources,
            tables=tables,
            create_by=user_id,
            create_by_name=username,
        )
        return business_response(
            data=datasource,
            msg="创建成功"
        )
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except Exception as e:
        logger.error(f"创建数据源组失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"创建失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.patch("/datasources/{datasource_id}")
async def update_datasource_meta(datasource_id: str, body: DatasourceUpdateRequest, request: Request):
    """
    更新数据源元信息（名称、描述）

    Args:
        datasource_id: 数据源ID
        body: 更新请求

    Returns:
        更新后的数据源
    """
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )

        name = None
        if body.name is not None:
            name = str(body.name).strip()
            if not name:
                return business_response(
                    msg="数据源名称不能为空",
                    code=40001,
                    status="error"
                )
            exists = await ds_manager.get_datasource_by_group_name_async(name)
            if exists and exists.get("id") != datasource_id:
                return business_response(
                    msg=f"数据源名称已存在: {name}",
                    code=40001,
                    status="error"
                )

        updated = await ds_manager.update_datasource_async(
            datasource_id,
            name=name,
            description=body.description,
            update_by=str(current_user.user_id),
            update_by_name=str(current_user.username or ""),
        )
        if not updated:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data=updated,
            msg="更新成功"
        )
    except Exception as e:
        logger.error(f"更新数据源失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.put("/datasources/{datasource_id}/group-config")
async def update_datasource_group_config(datasource_id: str, body: DatasourceGroupUpdateRequest, request: Request):
    """更新数据源组配置。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(current_user.username or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )

        group_name = str(body.group_name or "").strip()
        if not group_name:
            return business_response(
                msg="数据源组名称不能为空",
                code=40001,
                status="error"
            )

        exists = await ds_manager.get_datasource_by_group_name_async(group_name)
        if exists and exists.get("id") != datasource_id:
            return business_response(
                msg=f"数据源组名称已存在: {group_name}",
                code=40001,
                status="error"
            )

        template_source = _normalize_source_item(
            body.source_type,
            body.template_source.host,
            body.template_source.port,
            body.template_source.database,
            body.template_source.org_name,
            body.template_source.org_code,
            body.template_source.query_executor_type,
        )
        member_sources = [
            _normalize_source_item(
                body.source_type,
                item.host,
                item.port,
                item.database,
                item.org_name,
                item.org_code,
                item.query_executor_type,
            )
            for item in (body.member_sources or [])
        ]
        selected_table_names = []
        for table_name in body.template_table_names or []:
            normalized_table_name = str(table_name or "").strip()
            if normalized_table_name and normalized_table_name not in selected_table_names:
                selected_table_names.append(normalized_table_name)
        tables = await _load_tables_from_source(
            body.source_type,
            template_source,
            selected_table_names or None,
            user_id=current_user.user_id,
            username=current_user.username,
        )
        updated = await ds_manager.update_datasource_group_from_sources_async(
            datasource_id=datasource_id,
            group_name=group_name,
            group_desc=(body.group_desc or "").strip(),
            source_type=body.source_type,
            template_source_config=template_source,
            member_source_configs=member_sources,
            tables=tables,
            update_by=user_id,
            update_by_name=username,
        )
        if not updated:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )
        return business_response(
            data=updated,
            msg="更新成功"
        )
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except Exception as e:
        logger.error(f"更新数据源组失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.patch("/datasources/{datasource_id}/mapped-modules")
async def update_mapped_modules(datasource_id: str, body: dict, request: Request):
    """
    更新数据源的模块映射

    Args:
        datasource_id: 数据源ID
        body: { "mapped_modules": ["举报管理", "统计分析"] }
    """
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        mapped_modules = body.get("mapped_modules", [])
        updated = await ds_manager.update_datasource_async(
            datasource_id,
            mapped_modules=mapped_modules,
            update_by=str(current_user.user_id),
            update_by_name=str(current_user.username or ""),
        )
        if not updated:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )
        return business_response(
            data={"datasource_id": datasource_id, "mapped_modules": mapped_modules},
            msg="更新成功"
        )
    except Exception as e:
        logger.error(f"更新模块映射失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.get("/datasources/{datasource_id}/table-groups")
async def get_datasource_table_groups(datasource_id: str):
    """获取数据源内表分组。"""
    try:
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )
        groups = await ds_manager.get_table_groups_async(datasource_id)
        return business_response(
            data={"datasource_id": datasource_id, "groups": groups},
            msg="获取成功"
        )
    except Exception as e:
        logger.error("获取表分组失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.get("/datasources/{datasource_id}/semantic-health")
async def get_datasource_semantic_health(datasource_id: str):
    """校验数据源物理表结构与指标集配置是否一致。"""
    try:
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )
        configs = await ds_manager.get_semantic_configs_async(datasource_id)
        schema_health = await _build_datasource_schema_health(datasource)
        semantic_health = _build_datasource_semantic_health(datasource, configs)
        return business_response(
            data={
                **semantic_health,
                "status": _merge_health_status(schema_health.get("status"), semantic_health.get("status")),
                "schema": schema_health,
                "semantic": semantic_health,
            },
            msg="校验完成"
        )
    except Exception as e:
        logger.error("指标集健康校验失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(
            msg=f"校验失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.put("/datasources/{datasource_id}/table-groups")
async def update_datasource_table_groups(datasource_id: str, body: DatasourceTableGroupsUpdateRequest, request: Request):
    """覆盖保存数据源内表分组。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        groups = [item.model_dump() for item in body.groups or []]
        success = await ds_manager.replace_table_groups_async(datasource_id, groups, operator=str(current_user.user_id), operator_name=str(current_user.username or ""))
        if not success:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )
        saved_groups = await ds_manager.get_table_groups_async(datasource_id)
        return business_response(
            data={"datasource_id": datasource_id, "groups": saved_groups},
            msg="保存成功"
        )
    except Exception as e:
        logger.error("保存表分组失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(
            msg=f"保存失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.put("/datasources/{datasource_id}/tables/{table_name}/business-desc")
async def update_datasource_table_business_desc(datasource_id: str, table_name: str, body: DatasourceTableBusinessDescUpdateRequest, request: Request):
    """更新数据源内单张表的业务说明。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        table = await ds_manager.update_table_business_desc_async(
            datasource_id,
            table_name,
            body.business_desc,
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        if table is None:
            return business_response(
                msg=f"数据源或表不存在: {datasource_id}/{table_name}",
                code=40404,
                status="error"
            )
        return business_response(
            data={"datasource_id": datasource_id, "table": table},
            msg="保存成功"
        )
    except Exception as e:
        logger.error("保存表业务说明失败: datasource_id=%s table_name=%s error=%s", datasource_id, table_name, e, exc_info=True)
        return business_response(
            msg=f"保存失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.get("/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict")
async def get_datasource_column_dict(datasource_id: str, table_name: str, column_name: str, request: Request):
    """获取数据源字段数据字典。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        column_dict = await ds_manager.get_column_dict_async(datasource_id, table_name, column_name)
        if column_dict is None:
            return business_response(
                msg=f"数据源、表或字段不存在: {datasource_id}/{table_name}/{column_name}",
                code=40404,
                status="error"
            )
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict": column_dict,
            },
            msg="获取成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s", datasource_id, table_name, column_name, e, exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.get("/datasources/{datasource_id}/tables/{table_name}/column-dicts")
async def get_datasource_table_column_dicts(datasource_id: str, table_name: str, request: Request):
    """批量获取数据源单张表已维护字段数据字典。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        column_dicts = await ds_manager.get_table_column_dicts_async(datasource_id, table_name)
        if column_dicts is None:
            return business_response(
                msg=f"数据源或表不存在: {datasource_id}/{table_name}",
                code=40404,
                status="error"
            )
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "dicts": column_dicts,
            },
            msg="获取成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("批量获取字段数据字典失败: datasource_id=%s table_name=%s error=%s", datasource_id, table_name, e, exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.put("/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict")
async def update_datasource_column_dict(datasource_id: str, table_name: str, column_name: str, body: DatasourceColumnDictUpdateRequest, request: Request):
    """覆盖保存数据源字段数据字典。"""
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        saved_dict = await ds_manager.replace_column_dict_async(
            datasource_id,
            table_name,
            column_name,
            body.column_dict.model_dump(),
            operator=str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        if saved_dict is None:
            return business_response(
                msg=f"数据源、表或字段不存在: {datasource_id}/{table_name}/{column_name}",
                code=40404,
                status="error"
            )
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict": saved_dict,
            },
            msg="保存成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("保存字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s", datasource_id, table_name, column_name, e, exc_info=True)
        return business_response(
            msg=f"保存失败: {str(e)}",
            code=50006,
            status="error"
        )


def _build_column_dict_generate_prompt(table_name: str, column_name: str, column: Dict[str, Any], text: str) -> str:
    """构造字段数据字典生成提示词。"""
    payload = {
        "table_name": table_name,
        "column_name": column_name,
        "column_type": column.get("mysql_type") or column.get("hive_type") or column.get("type") or "",
        "column_description": column.get("description") or "",
        "user_text": str(text or "").strip()[:10000],
    }
    return (
        "你是数据字典维护助手。请从用户文本中抽取当前字段的数据字典。\n"
        "要求：\n"
        "1) 只抽取文本中明确出现或能直接对应的信息，不要猜测、不要扩展。\n"
        "2) enum_values 表示字段真实入库值与业务含义，value 必须是真实入库值，例如 0、1、RUNNING；label 是业务含义，例如 下线、在线、运行中。\n"
        "3) unit 表示数值单位，例如 元、万元、秒；如果文本没有明确单位，返回空字符串。\n"
        "4) 不要生成 storage_format，column_type 只是数据库字段类型，不代表字段真实数据存储格式。\n"
        "5) 不要生成同义词。\n"
        "6) 只输出 JSON，不要解释文本。格式：{\"dict\":{\"enum_values\":[{\"value\":\"...\",\"label\":\"...\"}],\"unit\":\"...\"}}\n"
        "输入数据：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def _parse_column_dict_generate_response(content: str) -> Dict[str, Any]:
    """解析字段数据字典生成模型返回。"""
    parsed = parse_llm_json(str(content or ""), strict=False)
    raw_dict = parsed.get("dict") if isinstance(parsed, dict) else {}
    if not isinstance(raw_dict, dict):
        raw_dict = {}
    raw_enums = raw_dict.get("enum_values") or raw_dict.get("enums") or []
    enum_values: List[Dict[str, str]] = []
    seen: Set[str] = set()
    if isinstance(raw_enums, list):
        for item in raw_enums:
            if not isinstance(item, dict):
                continue
            enum_value = str(item.get("value") or item.get("enum_value") or "").strip()[:128]
            enum_label = str(item.get("label") or item.get("enum_label") or "").strip()[:255]
            if not enum_value or enum_value in seen:
                continue
            seen.add(enum_value)
            enum_values.append({"value": enum_value, "label": enum_label})
    return {
        "enum_values": enum_values[:100],
        "synonyms": [],
        "storage_format": "",
        "unit": str(raw_dict.get("unit") or "").strip()[:100],
    }


@router.post("/datasources/{datasource_id}/tables/{table_name}/columns/{column_name}/dict/generate")
async def generate_datasource_column_dict(datasource_id: str, table_name: str, column_name: str, body: DatasourceColumnDictGenerateRequest, request: Request):
    """根据文本生成字段数据字典候选值，不直接保存。"""
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        target_column = None
        for table in (datasource or {}).get("ds_tables") or []:
            if str(table.get("table_name") or "").strip() != table_name:
                continue
            for column in table.get("columns") or []:
                if str(column.get("name") or "").strip() == column_name:
                    target_column = column
                    break
        if not datasource or target_column is None:
            return business_response(
                msg=f"数据源、表或字段不存在: {datasource_id}/{table_name}/{column_name}",
                code=40404,
                status="error"
            )
        prompt = _build_column_dict_generate_prompt(table_name, column_name, target_column, body.text)
        llm = get_chat_data_llm(streaming=False, max_tokens=1200)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if hasattr(response, "content") else str(response)
        column_dict = _parse_column_dict_generate_response(str(content))
        return business_response(
            data={
                "datasource_id": datasource_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict": column_dict,
            },
            msg="生成成功"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("生成字段数据字典失败: datasource_id=%s table_name=%s column_name=%s error=%s", datasource_id, table_name, column_name, e, exc_info=True)
        return business_response(
            msg=f"生成失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.post("/datasources/{datasource_id}/set-default")
async def set_default_datasource(datasource_id: str, request: Request):
    """
    设置默认数据源

    Args:
        datasource_id: 数据源ID

    Returns:
        操作结果
    """
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        success = await ds_manager.set_default_datasource_async(datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or ""))

        if not success:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data={"datasource_id": datasource_id, "is_default": True},
            msg="设置成功"
        )

    except Exception as e:
        logger.error(f"设置默认数据源失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"设置失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.delete("/datasources/{datasource_id}/set-default")
async def clear_default_datasource(datasource_id: str, request: Request):
    """
    取消默认数据源

    Args:
        datasource_id: 数据源ID

    Returns:
        操作结果
    """
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()
        success = await ds_manager.clear_default_datasource_async(datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or ""))

        if not success:
            return business_response(
                msg=f"数据源不存在或非默认数据源: {datasource_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data={"datasource_id": datasource_id, "is_default": False},
            msg="取消成功"
        )

    except Exception as e:
        logger.error(f"取消默认数据源失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"取消失败: {str(e)}",
            code=50006,
            status="error"
        )


@router.delete("/datasources/{datasource_id}")
async def delete_datasource(datasource_id: str, request: Request):
    """
    删除数据源组

    Args:
        datasource_id: 数据源ID

    Returns:
        删除结果
    """
    try:
        current_user = _get_current_user(request)
        ds_manager = get_datasource_manager()

        # 获取数据源信息
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg="数据源不存在",
                code=40404,
                status="error"
            )

        # 删除数据源
        success = await ds_manager.delete_datasource_async(datasource_id, update_by=str(current_user.user_id), update_by_name=str(current_user.username or ""))

        if not success:
            return business_response(
                msg="删除失败，数据源不存在或无法删除",
                code=40001,
                status="error"
            )

        return business_response(
            data={"datasource_id": datasource_id},
            msg="删除成功"
        )

    except Exception as e:
        if isinstance(e, ValueError):
            return business_response(
                msg=str(e),
                code=40001,
                status="error"
            )
        logger.error(f"删除数据源失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"删除失败: {str(e)}",
            code=50007,
            status="error"
        )


# ============ 统一表结构接口 ============

@router.get("/datasources/{datasource_id}/schema")
async def get_schema(
    datasource_id: str,
    source: str = None,
    tables: str = None,
    request: Request = None,
):
    """
    获取数据源的表结构（统一接口）

    支持两种模式：
    - cache: 从本地缓存读取（快）
    - live: 实时从数据库获取（准）

    默认值由环境变量 CHAT_DATA_SCHEMA_SOURCE 控制，未配置时默认 cache

    Args:
        datasource_id: 数据源ID
        source: 数据来源，可选值: cache、live（不传则使用环境变量配置）
        tables: 可选，指定表名（逗号分隔），不传则返回全部

    Returns:
        表结构列表: [{table_name, description, columns: [...]}]

    Examples:
        GET /datasources/ds_xxx/schema                    # 使用环境变量配置，全部表
        GET /datasources/ds_xxx/schema?source=live       # 实时获取，全部表
        GET /datasources/ds_xxx/schema?tables=t1,t2      # 使用环境变量配置，指定表
    """
    try:
        user_params = _get_optional_request_user_http_params(request)
        ds_manager = get_datasource_manager()

        # 从环境变量读取默认值，未配置时默认 cache
        if source is None:
            source = os.getenv("CHAT_DATA_SCHEMA_SOURCE", "cache")

        # 解析表名列表
        table_names = None
        if tables:
            table_names = [t.strip() for t in tables.split(",") if t.strip()]

        # 获取表结构（异步执行，避免阻塞事件循环）
        schema_tables = await ds_manager.get_schema_async(
            datasource_id,
            source=source,
            table_names=table_names,
            **user_params,
        )

        return business_response(
            data={
                "tables": schema_tables,
                "count": len(schema_tables),
                "source": source
            },
            msg="获取成功"
        )

    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except ConnectionError as e:
        return business_response(
            msg=str(e),
            code=50008,
            status="error"
        )
    except Exception as e:
        logger.error(f"获取表结构失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50008,
            status="error"
        )


# ============ 数据库同步接口 ============

@router.post("/datasources/db-tables")
async def get_db_tables(request: DbConnectionRequest, http_request: Request = None):
    """
    获取数据库中的表列表（用于选择要同步的表）

    Args:
        request: 数据库连接请求

    Returns:
        表名列表
    """
    try:
        user_params = _get_optional_request_user_http_params(http_request)
        source_type = request.source_type

        port = request.port
        if request.query_executor_type == "direct" and port is None:
            port = 10000 if source_type == "hive" else 3306

        logger.info(
            "获取 %s 表列表: executor=%s host=%s port=%s database=%s org_code=%s",
            source_type.upper(),
            request.query_executor_type,
            request.host,
            port,
            request.database,
            request.org_code,
        )

        if source_type == "hive":
            source_config = {
                "database": request.database,
                "org_code": request.org_code,
                "org_name": request.org_name,
                "query_executor_type": request.query_executor_type,
            }
            if request.query_executor_type == "http":
                tables = await get_hive_meta_tables_async(source_config, **user_params)
            else:
                source_config["host"] = request.host
                source_config["port"] = port
                hive_credentials = resolve_hive_credentials(source_config)
                service = HiveSyncService(
                    host=request.host,
                    port=port,
                    database=request.database,
                    username=hive_credentials.get("username"),
                    password=hive_credentials.get("password"),
                )
                tables = await asyncio.wait_for(
                    service.get_tables_async(),
                    timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
                )
        else:
            if request.query_executor_type == "http":
                raise ValueError("HTTP 元数据模式当前仅支持 Hive 数据源")
            mysql_credentials = resolve_mysql_credentials({})
            service = MySQLSyncService(
                host=request.host,
                port=port,
                database=request.database,
                user=mysql_credentials["user"],
                password=mysql_credentials["password"]
            )
            tables = await asyncio.wait_for(
                service.get_tables_async(),
                timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
            )

        return business_response(
            data={"tables": tables, "total": len(tables)},
            msg=f"获取成功，共 {len(tables)} 个表"
        )

    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except asyncio.TimeoutError:
        logger.error(
            "获取表列表超时: source_type=%s host=%s port=%s database=%s timeout=%ss",
            request.source_type,
            request.host,
            port,
            request.database,
            DATASOURCE_SYNC_TIMEOUT_SECONDS,
        )
        return business_response(
            msg=f"连接超时（{DATASOURCE_SYNC_TIMEOUT_SECONDS}秒），请检查数据源配置或稍后重试",
            code=50014,
            status="error"
        )
    except Exception as e:
        logger.error(f"获取表列表失败: {str(e)}", exc_info=True)
        return business_response(
            msg="连接失败，请检查数据源配置或稍后重试",
            code=50014,
            status="error"
        )


# ============ SQL 编辑器接口 ============


async def _execute_sql_for_member(
    execution_target: Dict[str, Any],
    sql: str,
    limit: int,
    timeout: Optional[int],
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    query_task_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    skip_query_cache: bool = False,
    datasource_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行单个成员数据源 SQL。"""
    return await query_runtime.execute_sql_for_member(
        execution_target=execution_target,
        sql=sql,
        limit=limit,
        timeout=timeout,
        user_id=user_id,
        username=username,
        query_task_id=query_task_id,
        execution_id=execution_id,
        skip_query_cache=skip_query_cache,
        datasource_id=datasource_id,
    )


def _normalize_sql_cache_text(sql: str) -> str:
    """归一化 SQL 缓存键文本。"""
    return query_runtime.normalize_sql_cache_text(sql)


def _build_sql_result_cache_identity(execution_target: Dict[str, Any], effective_sql: str, datasource_id: Optional[str] = None) -> Dict[str, Any]:
    """构造 SQL 结果缓存身份字段。"""
    return query_runtime.build_sql_result_cache_identity(execution_target, effective_sql, datasource_id=datasource_id)


def _is_sql_result_cache_enabled(skip_query_cache: bool) -> bool:
    """判断本次 SQL 执行是否允许使用结果缓存。"""
    return query_runtime.is_sql_result_cache_enabled(skip_query_cache)


async def _get_sql_result_cache(cache_identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """读取未过期的 SQL 查询结果缓存。"""
    return await query_runtime.get_sql_result_cache(cache_identity)


async def _save_sql_result_cache(cache_identity: Dict[str, Any], result: Dict[str, Any], operator_id: Optional[str] = None, operator_name: Optional[str] = None) -> None:
    """保存成功的 SQL 查询结果缓存。"""
    await query_runtime.save_sql_result_cache(cache_identity, result, operator_id=operator_id, operator_name=operator_name)




def _get_execution_id_by_member(execution_map: Dict[str, str], execution_target: Dict[str, Any]) -> Optional[str]:
    """根据成员数据源获取执行记录ID。"""
    return query_runtime.get_execution_id_by_member(execution_map, execution_target)


async def _create_query_executions_for_task(task_id: str, request: ExecuteSqlRequest, operator_name: Optional[str] = None) -> Dict[str, str]:
    """为任务预创建每个成员数据源的 SQL 执行记录。"""
    return await query_runtime.create_query_executions_for_task(task_id, request, operator_name=operator_name)




async def _update_query_execution(
    execution_id: str,
    *,
    sql_status: Optional[str] = None,
    remote_task_id: Optional[str] = None,
    db_connection_id: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
    result_row_count: Optional[int] = None,
    result_payload: Any = None,
    error_message: Optional[str] = None,
    started_time: Optional[datetime] = None,
    finished_time: Optional[datetime] = None,
) -> None:
    """更新 SQL 执行明细。"""
    await query_runtime.update_query_execution(
        execution_id,
        sql_status=sql_status,
        remote_task_id=remote_task_id,
        db_connection_id=db_connection_id,
        execution_time_ms=execution_time_ms,
        result_row_count=result_row_count,
        result_payload=result_payload,
        error_message=error_message,
        started_time=started_time,
        finished_time=finished_time,
    )


async def _update_query_execution_from_result(execution_id: str, result: Dict[str, Any], store_result_payload: bool = True) -> None:
    """按 SQL 执行结果更新执行明细。"""
    await query_runtime.update_query_execution_from_result(execution_id, result, store_result_payload=store_result_payload)


async def _build_execute_sql_result_data(
    request: ExecuteSqlRequest,
    query_task_id: Optional[str] = None,
    execution_map: Optional[Dict[str, str]] = None,
    username: Optional[str] = None,
) -> Dict[str, Any]:
    """执行 SQL 并返回统一结果数据。"""
    return await query_runtime.build_execute_sql_result_data(
        request,
        query_task_id=query_task_id,
        execution_map=execution_map,
        username=username,
    )


def _build_query_task_runtime_data(task: ChatDataQueryTask) -> Dict[str, Any]:
    """将任务模型转换为可脱离 session 使用的字典。"""
    return query_runtime.build_query_task_runtime_data(task)


def _load_remote_tasks(raw_payload: Any) -> List[Dict[str, Any]]:
    """解析远端任务映射列表。"""
    return query_runtime.load_remote_tasks(raw_payload)


async def _are_all_sql_execution_targets_http(request: ExecuteSqlRequest) -> bool:
    """判断 SQL 任务选中的执行对象是否全部为 HTTP 执行。"""
    return await query_runtime.are_all_sql_execution_targets_http(request)


async def _get_http_execution_member_ids(request: ExecuteSqlRequest) -> Set[str]:
    """读取本次 SQL 任务中走 HTTP 的成员数据源 ID。"""
    return await query_runtime.get_http_execution_member_ids(request)


async def _build_persistent_query_task_result_for_request(request: ExecuteSqlRequest, result_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """按执行配置构建任务落库结果，HTTP 结果不落库。"""
    return await query_runtime.build_persistent_query_task_result_for_request(request, result_data)


def _build_persistent_query_task_result_payload(result_data: Dict[str, Any], http_member_ids: Set[str]) -> Optional[Dict[str, Any]]:
    """构建可落库的任务结果，过滤 HTTP 返回数据。"""
    return query_runtime.build_persistent_query_task_result_payload(result_data, http_member_ids)


def _build_cancelled_execution_map(executions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按成员数据源整理本地已取消的执行记录。"""
    cancelled_map: Dict[str, Dict[str, Any]] = {}
    for execution in executions:
        status = str((execution or {}).get("sql_status") or "").strip().lower()
        member_datasource_id = str((execution or {}).get("member_datasource_id") or "").strip()
        if member_datasource_id and status in {"cancelled", "kill_failed"}:
            cancelled_map[member_datasource_id] = execution
    return cancelled_map


async def _build_live_remote_sql_result(response_data: Dict[str, Any], executions: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """基于远端 HTTP 任务实时拉取 SQL 结果。"""
    remote_tasks = list(response_data.get("remote_tasks") or [])
    if not remote_tasks:
        return None
    cancelled_execution_map = _build_cancelled_execution_map(executions or [])
    request_payload = response_data.get("request_payload") or {}
    remote_user_id = str((request_payload or {}).get("user_id") or "").strip()
    remote_username = str((request_payload or {}).get("operator_name") or "").strip()

    remote_member_results = await asyncio.gather(*[
        fetch_http_query_task_result(
            str(remote_task.get("remote_task_id") or ""),
            str(remote_task.get("source_type") or "hive"),
            user_id=remote_user_id,
            username=remote_username,
        )
        for remote_task in remote_tasks
    ])

    remote_results: List[Dict[str, Any]] = []
    for remote_task, remote_result in zip(remote_tasks, remote_member_results):
        member_datasource_id = str(remote_task.get("member_datasource_id") or "").strip()
        local_cancelled_execution = cancelled_execution_map.get(member_datasource_id)
        if local_cancelled_execution:
            local_status = str(local_cancelled_execution.get("sql_status") or "cancelled").strip().lower()
            remote_results.append({
                "member_datasource_id": remote_task.get("member_datasource_id"),
                "member_name": remote_task.get("member_name"),
                "sql": ((response_data.get("request_payload") or {}).get("sql") or ""),
                "success": False,
                "status": local_status,
                "columns": [],
                "data": [],
                "total_count": 0,
                "execution_time_ms": local_cancelled_execution.get("execution_time_ms"),
                "error": local_cancelled_execution.get("error_message") or ("已人工取消" if local_status == "cancelled" else "停止执行失败"),
            })
            continue
        remote_results.append({
            "member_datasource_id": remote_task.get("member_datasource_id"),
            "member_name": remote_task.get("member_name"),
            "sql": ((response_data.get("request_payload") or {}).get("sql") or ""),
            "success": bool(remote_result.get("success")),
            "status": remote_result.get("status"),
            "columns": remote_result.get("columns") or [],
            "data": remote_result.get("data") or [],
            "total_count": int(remote_result.get("total_count") or 0),
            "execution_time_ms": remote_result.get("execution_time_ms"),
            "error": remote_result.get("error"),
        })

    local_result_payload = response_data.get("result_payload")
    local_results = []
    if isinstance(local_result_payload, dict):
        local_results = list(local_result_payload.get("results") or [])
    remote_member_ids = {
        str(item.get("member_datasource_id") or "").strip()
        for item in remote_tasks
        if str(item.get("member_datasource_id") or "").strip()
    }
    direct_results = [
        item for item in local_results
        if str((item or {}).get("member_datasource_id") or "").strip() not in remote_member_ids
    ]
    merged_results = direct_results + remote_results

    statuses = {str(item.get("status") or "").strip().lower() for item in remote_results}
    if "pending" in statuses or "running" in statuses:
        task_status = "running"
        progress_text = "SQL 执行中"
    elif statuses and statuses <= {"cancelled"}:
        task_status = "cancelled"
        progress_text = "已停止"
    elif any(item.get("success") for item in merged_results):
        task_status = "succeeded"
        progress_text = "执行完成"
    else:
        task_status = "failed"
        progress_text = "执行失败"

    primary_result = next((item for item in merged_results if item.get("success")), merged_results[0] if merged_results else {})
    successful_results = [item for item in merged_results if item.get("success")]
    if task_status == "running":
        error_message = None
    elif successful_results:
        error_message = None
    else:
        error_messages = [
            str(item.get("error") or "").strip()
            for item in merged_results
            if str(item.get("error") or "").strip()
        ]
        error_message = "；".join(error_messages) if error_messages else "所有执行对象均执行失败"

    result_success = bool(successful_results) and task_status != "running"
    return {
        "status": task_status,
        "progress_text": progress_text,
        "error_message": error_message,
        "result_payload": {
            "success": result_success,
            "sql": primary_result.get("sql") or ((response_data.get("request_payload") or {}).get("sql") or ""),
            "columns": primary_result.get("columns") or [],
            "data": primary_result.get("data") or [],
            "total_count": int(primary_result.get("total_count") or 0),
            "execution_time_ms": primary_result.get("execution_time_ms"),
            "member_datasource_id": primary_result.get("member_datasource_id"),
            "member_name": primary_result.get("member_name"),
            "error": error_message,
            "results": merged_results,
        },
    }


def _build_query_task_response_data(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """将任务运行态字典转换为接口返回结构。"""
    return query_runtime.build_query_task_response_data(task_data)


async def _get_query_executions(task_id: str) -> List[Dict[str, Any]]:
    """读取任务下的 SQL 执行明细。"""
    return await query_runtime.get_query_executions(task_id)


async def _get_query_execution_models(task_id: str) -> List[ChatDataQueryExecution]:
    """读取任务下的 SQL 执行模型。"""
    if not task_id:
        return []
    async with get_session() as db:
        result = await db.execute(
            select(ChatDataQueryExecution)
            .filter_by(task_id=task_id, delete_flag=0)
            .order_by(ChatDataQueryExecution.create_time.asc(), ChatDataQueryExecution.id.asc())
        )
        return list(result.scalars().all())


async def _get_query_execution_model(execution_id: str) -> Optional[ChatDataQueryExecution]:
    """按执行ID读取 SQL 执行模型。"""
    if not execution_id:
        return None
    async with get_session() as db:
        result = await db.execute(select(ChatDataQueryExecution).filter_by(execution_id=execution_id, delete_flag=0))
        return result.scalar_one_or_none()


def _get_task_user_id(task_data: Dict[str, Any]) -> str:
    """从任务请求负载中解析发起用户ID。"""
    try:
        payload = json.loads(task_data.get("request_payload") or "{}")
    except Exception:
        payload = {}
    return str((payload or {}).get("user_id") or task_data.get("create_by") or task_data.get("update_by") or "").strip()


def _is_query_task_owned_by_user(task_data: Dict[str, Any], executions: List[Dict[str, Any]], user_id: str) -> bool:
    """判断查询任务是否归属于当前用户。"""
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return False
    owner_ids = {
        str(item.get("user_id") or item.get("create_by") or item.get("update_by") or "").strip()
        for item in executions or []
        if str(item.get("user_id") or item.get("create_by") or item.get("update_by") or "").strip()
    }
    task_user_id = _get_task_user_id(task_data)
    if task_user_id:
        owner_ids.add(task_user_id)
    return not owner_ids or normalized_user_id in owner_ids


def _get_task_operator_name(task_data: Dict[str, Any]) -> str:
    """从任务请求负载中解析发起用户名。"""
    try:
        payload = json.loads(task_data.get("request_payload") or "{}")
    except Exception:
        payload = {}
    return str((payload or {}).get("operator_name") or "").strip()


async def _resolve_execution_target(execution: ChatDataQueryExecution) -> Optional[Dict[str, Any]]:
    """根据执行记录解析实际成员数据源配置。"""
    ds_manager = get_datasource_manager()
    member_ids = [execution.member_datasource_id] if execution.member_datasource_id else None
    targets = await ds_manager.get_execution_datasources_async(
        execution.datasource_id,
        member_ids,
    )
    return targets[0] if targets else None


def _copy_execution_identity(source: ChatDataQueryExecution, target: ChatDataQueryExecution) -> None:
    """复制执行记录中的可停止标识。"""
    source.remote_task_id = str(getattr(target, "remote_task_id", "") or "").strip()
    source.db_connection_id = str(getattr(target, "db_connection_id", "") or "").strip()
    source.sql_status = str(getattr(target, "sql_status", "") or source.sql_status or "").strip()


async def _wait_for_cancel_target(execution: ChatDataQueryExecution) -> ChatDataQueryExecution:
    """短暂等待执行记录写入远端任务ID或数据库连接ID。"""
    if execution.remote_task_id or execution.db_connection_id or str(execution.sql_status or "").strip().lower() != "running":
        return execution
    deadline = time.monotonic() + CANCEL_TARGET_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(CANCEL_TARGET_POLL_INTERVAL_SECONDS)
        latest = await _get_query_execution_model(execution.execution_id)
        if latest is None:
            return execution
        _copy_execution_identity(execution, latest)
        if execution.remote_task_id or execution.db_connection_id or str(execution.sql_status or "").strip().lower() != "running":
            return execution
    return execution


async def _cancel_query_execution(execution: ChatDataQueryExecution, *, cleanup_without_target: bool = False, operator_name: Optional[str] = None) -> Dict[str, Any]:
    """停止一条 SQL 执行记录对应的远端任务或直连查询。"""
    current_status = str(execution.sql_status or "").strip().lower()
    if current_status not in {"pending", "running"}:
        return {
            "execution_id": execution.execution_id,
            "status": current_status,
            "success": True,
            "message": "无需停止",
        }
    target = None
    stop_result: Dict[str, Any]
    execution = await _wait_for_cancel_target(execution)
    current_status = str(execution.sql_status or "").strip().lower()
    if current_status not in {"pending", "running"}:
        return {
            "execution_id": execution.execution_id,
            "status": current_status,
            "success": True,
            "message": "无需停止",
        }
    if execution.remote_task_id:
        try:
            target = await _resolve_execution_target(execution)
        except Exception as err:
            logger.warning("解析 HTTP 停止数据源失败: execution_id=%s error=%s", execution.execution_id, err)
        source_type = str((target or {}).get("source_type") or "hive").strip().lower() or "hive"
        stop_result = await stop_http_query_task(
            execution.remote_task_id,
            source_type,
            user_id=str(execution.user_id or "").strip(),
            username=str(operator_name or "").strip(),
        )
    elif execution.db_connection_id:
        try:
            target = await _resolve_execution_target(execution)
        except Exception as err:
            logger.error("解析 MySQL 停止数据源失败: execution_id=%s error=%s", execution.execution_id, err, exc_info=True)
            stop_result = {"success": False, "error": "解析执行数据源失败"}
        else:
            stop_result = await kill_mysql_query(target or {}, execution.db_connection_id)
    elif current_status == "pending" or cleanup_without_target:
        stop_result = {"success": True, "error": None}
    else:
        stop_result = {"success": False, "error": "缺少可停止的远端任务ID或数据库连接ID"}

    success = bool(stop_result.get("success"))
    await _update_query_execution(
        execution.execution_id,
        sql_status=("cancelled" if success else "kill_failed"),
        error_message=("已人工取消" if success else str(stop_result.get("error") or "停止执行失败")),
        finished_time=datetime.now(),
    )
    return {
        "execution_id": execution.execution_id,
        "status": "cancelled" if success else "kill_failed",
        "success": success,
        "message": None if success else str(stop_result.get("error") or "停止执行失败"),
    }


def _build_cancel_task_status(execution_results: List[Dict[str, Any]]) -> str:
    """根据执行停止结果汇总任务状态。"""
    if not execution_results:
        return "cancelled"
    statuses = {str(item.get("status") or "").strip().lower() for item in execution_results}
    if statuses <= {"cancelled"}:
        return "cancelled"
    if statuses <= {"kill_failed"}:
        return "failed"
    if "kill_failed" in statuses:
        return "partial_success"
    return "cancelled"


async def _create_task_record(
    *,
    task_type: str,
    datasource_id: str,
    query_type: str,
    request_payload: Dict[str, Any],
    progress_text: str = "任务已创建，等待执行",
) -> Dict[str, Any]:
    """创建异步任务记录。"""
    return await query_runtime.create_task_record(
        task_type=task_type,
        datasource_id=datasource_id,
        query_type=query_type,
        request_payload=request_payload,
        progress_text=progress_text,
    )


async def _create_query_task(payload: QueryTaskCreateRequest) -> Dict[str, Any]:
    """创建 SQL 异步查询任务。"""
    return await query_runtime.create_query_task(payload)


async def _prepare_sql_request_for_execution(request: ExecuteSqlRequest | QueryTaskCreateRequest, datasource: Optional[Dict[str, Any]] = None):
    """保留 SQL 原文；安全校验统一在执行器边界完成。"""
    return await query_runtime.prepare_sql_request_for_execution(request, datasource)


async def _create_task_for_execute_request(
    request: ExecuteSqlRequest,
    *,
    query_type: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """为直接执行或智能体执行创建任务主记录。"""
    return await query_runtime.create_task_for_execute_request(
        request,
        query_type=query_type,
        operator_name=operator_name,
    )


async def _get_query_task(task_id: str) -> Optional[Dict[str, Any]]:
    """读取异步查询任务。"""
    return await query_runtime.get_query_task(task_id)


async def _is_query_task_cancelled(task_id: str) -> bool:
    """判断异步查询任务是否已取消。"""
    return await query_runtime.is_query_task_cancelled(task_id)


async def _get_query_execution_status(execution_id: str) -> str:
    """读取 SQL 执行明细当前状态。"""
    return await query_runtime.get_query_execution_status(execution_id)


async def _append_query_task_remote_task(task_id: str, remote_task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """向异步任务中追加远端 HTTP 任务映射。"""
    return await query_runtime.append_query_task_remote_task(task_id, remote_task)


async def _update_query_task(
    task_id: str,
    *,
    status: Optional[str] = None,
    progress_text: Optional[str] = None,
    result_payload: Any = None,
    error_message: Optional[str] = None,
    started_time: Optional[datetime] = None,
    finished_time: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """更新异步查询任务。"""
    return await query_runtime.update_query_task(
        task_id,
        status=status,
        progress_text=progress_text,
        result_payload=result_payload,
        error_message=error_message,
        started_time=started_time,
        finished_time=finished_time,
    )


async def _run_sql_query_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行 SQL 查询任务。"""
    await query_runtime.run_sql_query_task(task_id, task)




async def _run_query_task(task_id: str) -> None:
    """后台执行查询任务。"""
    task = await _get_query_task(task_id)
    if not task:
        logger.warning("异步查询任务不存在: %s", task_id)
        return
    try:
        if task.get("task_type") == "sql":
            await _run_sql_query_task(task_id, task)
            return
        if task.get("task_type") == "dashboard_panel":
            await _run_dashboard_panel_task(task_id, task)
            return
        if task.get("task_type") == "dashboard_batch":
            await _run_dashboard_batch_task(task_id, task)
            return
        raise ValueError(f"不支持的任务类型: {task.get('task_type')}")
    except ValueError as e:
        logger.error("异步查询任务失败: %s", e)
        await _update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            error_message=str(e),
            finished_time=datetime.now(),
        )
    except Exception as e:
        logger.error("异步查询任务异常: %s", e, exc_info=True)
        await _update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            error_message="执行失败，请稍后重试或联系管理员",
            finished_time=datetime.now(),
        )


def _schedule_query_task(task_id: str) -> None:
    """提交后台异步查询任务。"""
    background_task = asyncio.create_task(_run_query_task(task_id))
    _query_task_background_tasks.add(background_task)
    background_task.add_done_callback(_query_task_background_tasks.discard)


configure_dashboard_runtime(DashboardRuntime(
    get_datasource_manager=lambda: get_datasource_manager(),
    execute_sql_for_member=lambda **kwargs: _execute_sql_for_member(**kwargs),
    create_task_record=lambda **kwargs: _create_task_record(**kwargs),
    update_query_task=lambda *args, **kwargs: _update_query_task(*args, **kwargs),
    is_query_task_cancelled=lambda task_id: _is_query_task_cancelled(task_id),
    schedule_query_task=lambda task_id: _schedule_query_task(task_id),
    query_task_poll_interval_ms=QUERY_TASK_POLL_INTERVAL_MS,
))


@router.post("/query-tasks")
async def create_query_task(request: Request, body: QueryTaskCreateRequest):
    """创建异步查询任务并返回任务 ID。"""
    try:
        current_user = _get_current_user(request)
        body = body.model_copy(update={"user_id": current_user.user_id, "operator_name": current_user.username})
        logger.info(
            "query-tasks current user: user_id=%s username=%s datasource_id=%s",
            current_user.user_id,
            current_user.username,
            body.datasource_id,
        )
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(body.datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {body.datasource_id}",
                code=40404,
                status="error"
            )
        body = await _prepare_sql_request_for_execution(body, datasource)
        task_info = await _create_query_task(body)
        _schedule_query_task(task_info["task_id"])
        return business_response(
            data={
                "task_id": task_info["task_id"],
                "task_type": task_info["task_type"],
                "status": task_info["status"],
                "poll_interval_ms": QUERY_TASK_POLL_INTERVAL_MS,
            },
            msg="任务已创建"
        )
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=40101,
            status="error"
        )
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except Exception as e:
        logger.error(f"创建异步查询任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="创建异步查询任务失败，请稍后重试",
            code=50010,
            status="error"
        )


@router.get("/query-tasks/{task_id}")
async def get_query_task(task_id: str, request: Request):
    """查询异步任务状态与结果。"""
    try:
        current_user = _get_current_user(request)
        current_user_id = str(current_user.user_id)
        task_data = await _get_query_task(task_id)
        if not task_data:
            return business_response(
                msg=f"任务不存在: {task_id}",
                code=40404,
                status="error"
            )
        response_data = _build_query_task_response_data(task_data)
        executions = await _get_query_executions(response_data["task_id"])
        if not _is_query_task_owned_by_user(task_data, executions, current_user_id):
            return business_response(msg=f"任务不存在: {task_id}", code=40404, status="error")
        if response_data["task_type"] == "sql" and response_data["remote_tasks"]:
            live_remote_payload = await _build_live_remote_sql_result(response_data, executions)
            if live_remote_payload:
                response_data = {
                    **response_data,
                    "status": live_remote_payload["status"],
                    "progress_text": live_remote_payload["progress_text"],
                    "error_message": live_remote_payload["error_message"],
                    "result_payload": live_remote_payload["result_payload"],
                }
        result_payload = response_data["result_payload"]
        task_type = response_data["task_type"]
        return business_response(
            data={
                "task_id": response_data["task_id"],
                "task_type": task_type,
                "datasource_id": response_data["datasource_id"],
                "query_type": response_data["query_type"],
                "status": response_data["status"],
                "progress_text": response_data["progress_text"],
                "remote_tasks": response_data["remote_tasks"],
                "executions": executions,
                "result": (result_payload if task_type != "sql" else None),
                "results": ((result_payload or {}).get("results") if task_type == "sql" and isinstance(result_payload, dict) else None),
                "error_message": response_data["error_message"],
                "started_time": response_data["started_time"],
                "finished_time": response_data["finished_time"],
                "create_time": response_data["create_time"],
                "update_time": response_data["update_time"],
            },
            msg="查询成功"
        )
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=40101,
            status="error"
        )
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=e.status_code * 100 + 1, status="error")
    except Exception as e:
        logger.error(f"查询异步任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="查询异步任务失败，请稍后重试",
            code=50010,
            status="error"
        )


@router.post("/query-tasks/{task_id}/cancel")
async def cancel_query_task(task_id: str, request: Request):
    """停止异步查询任务及其 SQL 执行。"""
    try:
        current_user = _get_current_user(request)
        current_user_id = str(current_user.user_id)
        task_data = await _get_query_task(task_id)
        if not task_data:
            return business_response(msg=f"任务不存在: {task_id}", code=40404, status="error")
        executions = await _get_query_execution_models(task_id)
        owner_ids = {str(item.user_id or "").strip() for item in executions if str(item.user_id or "").strip()}
        if not owner_ids:
            task_user_id = _get_task_user_id(task_data)
            if task_user_id:
                owner_ids.add(task_user_id)
        if owner_ids and current_user_id not in owner_ids:
            return business_response(msg="无权停止该查询任务", code=40301, status="error")
        operator_name = _get_task_operator_name(task_data) or str(getattr(current_user, "username", "") or "").strip()
        cancellable_executions = [
            item for item in executions
            if str(item.sql_status or "").strip().lower() in {"pending", "running"}
        ]
        task_status = str(task_data.get("status") or "").strip().lower()
        if task_status in QUERY_TASK_FINAL_STATUSES and not cancellable_executions:
            return business_response(
                data={
                    "task_id": task_id,
                    "status": task_data.get("status"),
                    "executions": [item.to_dict() for item in executions],
                },
                msg="任务已结束"
            )
        cleanup_without_target = task_status in QUERY_TASK_FINAL_STATUSES
        execution_results = await asyncio.gather(*[
            _cancel_query_execution(item, cleanup_without_target=cleanup_without_target, operator_name=operator_name)
            for item in (cancellable_executions or executions)
        ])
        cancel_status = _build_cancel_task_status(execution_results)
        if task_status not in QUERY_TASK_FINAL_STATUSES:
            await _update_query_task(
                task_id,
                status=cancel_status,
                progress_text=("已停止" if cancel_status == "cancelled" else "部分停止失败"),
                error_message=(None if cancel_status == "cancelled" else "部分 SQL 停止失败"),
                finished_time=datetime.now(),
            )
        return business_response(
            data={
                "task_id": task_id,
                "status": cancel_status,
                "executions": execution_results,
            },
            msg="停止请求已提交"
        )
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=e.status_code * 100 + 1, status="error")
    except Exception as e:
        logger.error("停止查询任务失败: task_id=%s error=%s", task_id, e, exc_info=True)
        return business_response(msg="停止查询任务失败，请稍后重试", code=50010, status="error")


@router.post("/execute-sql")
async def execute_sql_direct(request: Request, body: ExecuteSqlRequest):
    """直接执行 SQL（用于 SQL 编辑器模式）。"""
    try:
        current_user = _get_current_user(request)
        body = body.model_copy(update={"user_id": current_user.user_id})
        body = await _prepare_sql_request_for_execution(body)
        logger.info(
            "execute-sql current user: user_id=%s username=%s datasource_id=%s",
            current_user.user_id,
            current_user.username,
            body.datasource_id,
        )
        result_data = await query_runtime.execute_sql_request_with_task(
            body,
            query_type=body.query_type or "sql",
            operator_name=current_user.username,
        )
        if not result_data.get("success"):
            return business_response(
                data=result_data,
                msg=result_data.get("error") or "SQL 执行失败，请稍后重试或联系管理员",
                code=50010,
                status="error"
            )
        return business_response(data=result_data, msg="执行完成")
    except AuthenticationError as e:
        return business_response(
            msg=e.message,
            code=40101,
            status="error"
        )
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except Exception as e:
        logger.error(f"执行 SQL 失败: {str(e)}", exc_info=True)
        return business_response(
            msg="SQL 执行失败，请稍后重试或联系管理员",
            code=50010,
            status="error"
        )


@router.post("/compile-semantic-builder-sql")
async def compile_semantic_builder_sql_endpoint(request: CompileSemanticBuilderSqlRequest):
    """编译语义可视化 DSL 为 SQL（仅预览，不执行）。"""
    try:
        result = await compile_semantic_builder_sql(
            datasource_id=request.datasource_id,
            semantic_config_id=request.semantic_config_id,
            semantic_builder_config=request.semantic_builder_config,
        )
        return business_response(data={"sql": result["sql"], "builder_config": result["builder_config"]}, msg="编译成功")
    except LookupError as err:
        return business_response(msg=str(err), code=40404, status="error")
    except ValueError as err:
        return business_response(msg=str(err), code=40001, status="error")
    except Exception as err:
        logger.error(f"Semantic Builder DSL 编译失败: {err}", exc_info=True)
        return business_response(msg="Semantic Builder DSL 编译失败，请检查配置", code=50010, status="error")


# ============ 对话接口 ============
# 会话管理（创建、列表、删除、详情、completion）统一由公共接口 /api/chatdata/conversations 处理
# sql_generator 的 completion handler 已在 __init__.py 中注册
