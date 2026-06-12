"""
ChatData API 路由定义

提供 ChatData 查询与配置管理相关接口
"""

import os
import asyncio
import inspect
import json
import re
import tempfile
from typing import Optional, Dict, List, Set, Any
from datetime import datetime
from fastapi import APIRouter, HTTPException, UploadFile, File, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import SystemMessage, HumanMessage
from sqlalchemy import select

from .schema import (
    NL2SQLRequest,
    QuickSqlRequest,
    ExecuteSqlRequest,
    QueryTaskCreateRequest,
    CompileBuilderSqlRequest,
    QueryFavoriteCreateRequest,
    QueryFavoriteUpdateRequest,
    TableListResponse,
    TableInfoRequest,
    TableInfoResponse,
    TableInfoResult,
    DbConnectionRequest,
    DatasourceRefreshRequest,
    DatasourceGroupCreateRequest,
    DatasourceGroupUpdateRequest,
    DatasourceUpdateRequest,
    SemanticConfigCreateRequest,
    SemanticConfigUpdateRequest,
    SemanticAutoPrefillRequest,
    SemanticPrefillRequest,
    PanelExecuteRequest,
    PanelBatchExecuteRequest,
)
from .services.datasource_manager import get_datasource_manager
from .services.datasource_credentials import resolve_hive_credentials, resolve_mysql_credentials, sanitize_source_config
from .services.mysql_sync import MySQLSyncService
from .services.hive_sync import HiveSyncService
from .services.sql_executor import get_executor, resolve_query_timeout, fetch_http_query_task_result
from .services.builder_sql_compiler import compile_builder_sql
from .services.query_metadata import generate_query_metadata
from .services.llm_config import get_chat_data_llm
from .handlers import _stream_sql_generation, _build_execution_target_info
from .agent import get_all_tables, get_tables_create_format
from .agent.prompts import SQL_GENERATOR_PROMPT_GENERATE
from .agent.tools import execute_sql_direct as execute_sql_tool_direct
from .semantic.utils import parse_llm_json
from utils.lang_util.database import get_session
from utils.lang_util.models import SqlQueryFavorite, SqlQueryHistory, ChatDataQueryTask
from utils.response import business_response as _base_business_response
from utils.logger import get_logger
from auth import authenticate_request, AuthenticationError
import uuid
import time

logger = get_logger(__name__)
QUERY_TASK_POLL_INTERVAL_MS = 1500
_query_task_background_tasks: set[asyncio.Task] = set()

SOURCE_DEFAULT_PORTS = {
    "mysql": 3306,
    "hive": 10000,
}


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = str(os.getenv(name) or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，回退默认值 %s", name, raw_value, default)
        return default
    if value < 1:
        logger.warning("环境变量 %s=%s 小于 1，回退默认值 %s", name, value, default)
        return default
    return value


DATASOURCE_SYNC_TIMEOUT_SECONDS = _read_positive_int_env("CHAT_DATA_DATASOURCE_SYNC_TIMEOUT_SECONDS", 30)
DASHBOARD_PANEL_MAX_CONCURRENCY = _read_positive_int_env("CHAT_DATA_DASHBOARD_PANEL_MAX_CONCURRENCY", 8)

# 代理标识
AGENT_ID = "chat_data"

# 创建路由器
router = APIRouter(
    prefix="/api/chatdata",
    tags=["ChatData NL2SQL"]
)


def business_response(data=None, msg: str = "操作成功", code: int = 2000, status: str = "success"):
    """chatdata 路由统一响应为 success/error。"""
    if status not in ("success", "error"):
        raise ValueError(f"status 仅支持 success/error，当前值: {status}")
    return _base_business_response(data=data, msg=msg, code=code, status=status)


def _get_current_user_id(request: Request) -> str:
    """解析当前登录用户ID。"""
    try:
        user = authenticate_request(request)
    except AuthenticationError as err:
        raise HTTPException(status_code=err.status_code, detail=err.message) from err
    user_id = str(user.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing user_id in token")
    return user_id


async def _call_datasource_manager(ds_manager: Any, method_name: str, *args, **kwargs):
    """优先调用 manager 的 async 方法，兼容测试桩中的同步实现。"""
    async_method = getattr(ds_manager, f"{method_name}_async", None)
    method = async_method or getattr(ds_manager, method_name)
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _normalize_source_item(
    source_type: str,
    host: str,
    port: Optional[int],
    database: str,
    datasource_name: Optional[str] = None,
    query_executor_type: Optional[str] = None,
) -> Dict[str, Any]:
    """标准化数据源连接项。"""
    normalized_host = str(host or "").strip()
    normalized_database = str(database or "").strip()
    normalized_port = int(port or SOURCE_DEFAULT_PORTS[source_type])
    normalized_name = str(datasource_name or "").strip()
    normalized_executor_type = str(query_executor_type or "direct").strip().lower() or "direct"
    if not normalized_name:
        raise ValueError("数据源名称不能为空")
    if not normalized_host or not normalized_database:
        raise ValueError("数据源连接信息不完整")
    if normalized_executor_type not in {"direct", "http"}:
        raise ValueError(f"不支持的查询执行方式: {normalized_executor_type}")
    result = {
        "host": normalized_host,
        "port": normalized_port,
        "database": normalized_database,
        "query_executor_type": normalized_executor_type,
    }
    if normalized_name:
        result["datasource_name"] = normalized_name
    return result


async def _load_tables_from_source(source_type: str, source_item: Dict[str, Any], table_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """从模板数据源拉取表结构。"""
    if source_type == "hive":
        hive_credentials = resolve_hive_credentials({})
        service = HiveSyncService(
            host=source_item["host"],
            port=source_item["port"],
            database=source_item["database"],
            username=hive_credentials["username"],
            password=hive_credentials.get("password"),
        )
        return await asyncio.wait_for(
            service.get_full_schema_async(table_names),
            timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
        )

    mysql_credentials = resolve_mysql_credentials({})
    service = MySQLSyncService(
        host=source_item["host"],
        port=source_item["port"],
        database=source_item["database"],
        user=mysql_credentials["user"],
        password=mysql_credentials["password"],
    )
    return await asyncio.wait_for(
        service.get_full_schema_async(table_names),
        timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
    )


def _build_table_columns_map(datasource: Dict) -> Dict[str, Set[str]]:
    """构建数据源表字段索引。"""
    table_columns: Dict[str, Set[str]] = {}
    for table in datasource.get("ds_tables", []) or []:
        table_name = table.get("table_name")
        if not table_name:
            continue
        columns = table.get("columns", []) or []
        column_names = {col.get("name") for col in columns if col.get("name")}
        table_columns[table_name] = column_names
    return table_columns


def _expression_uses_physical_column(expression: str, table_columns: Set[str]) -> bool:
    """判断表达式是否引用了给定表中的任一物理字段。"""
    if not expression:
        return False
    expr = expression.replace("`", "")
    for column in table_columns:
        escaped_col = re.escape(column)
        pattern = rf"(^|[^A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped_col}([^A-Za-z0-9_]|$)"
        if re.search(pattern, expr, flags=re.IGNORECASE):
            return True
    return False


def _validate_semantic_fields(datasource: Dict, metrics: List[Dict], dimensions: List[Dict]) -> Optional[str]:
    """校验语义字段必须基于数据源物理表字段。"""
    table_columns_map = _build_table_columns_map(datasource)
    if not table_columns_map:
        return "当前数据源缺少表结构信息，请先完成数据源同步"

    for dim in dimensions:
        table_name = dim.get("table")
        column_name = dim.get("column")
        if not table_name or table_name not in table_columns_map:
            return f"维度 {dim.get('name', '')} 所属表不存在: {table_name}"
        if not column_name or column_name not in table_columns_map[table_name]:
            return f"维度 {dim.get('name', '')} 字段不存在: {table_name}.{column_name}"

    for metric in metrics:
        table_name = metric.get("table")
        expression = (metric.get("expression") or "").strip()
        if not table_name or table_name not in table_columns_map:
            return f"指标 {metric.get('name', '')} 所属表不存在: {table_name}"
        if not _expression_uses_physical_column(expression, table_columns_map[table_name]):
            return f"指标 {metric.get('name', '')} 表达式必须引用表 {table_name} 的物理字段"

    return None


def _quote_identifier(name: str) -> str:
    safe = (name or "").replace("`", "``")
    return f"`{safe}`"


def _normalize_prefill_item(item: Dict) -> Optional[Dict]:
    """标准化 LLM 预填项。"""
    table_name = (item.get("table") or "").strip()
    column_name = (item.get("column") or "").strip()
    if not table_name or not column_name:
        return None
    role = str(item.get("role") or "").strip().lower()
    if role not in ("metric", "dimension"):
        if item.get("is_metric") is True:
            role = "metric"
        elif item.get("is_dimension") is True:
            role = "dimension"
        else:
            return None

    is_metric = role == "metric"
    return {
        "table": table_name,
        "column": column_name,
        "is_dimension": not is_metric,
        "is_metric": is_metric,
        "expression": _quote_identifier(column_name)
    }


async def _prefill_by_llm(tables: List[Dict]) -> Optional[List[Dict]]:
    """调用系统大模型进行字段角色预判。"""
    if not tables:
        return []

    table_payload = []
    for table in tables:
        table_name = table.get("table_name")
        if not table_name:
            continue
        columns = []
        for col in table.get("columns", []) or []:
            col_name = col.get("name")
            if not col_name:
                continue
            columns.append({
                "name": col_name,
                "type": col.get("mysql_type") or col.get("hive_type") or col.get("type") or "",
                "description": col.get("description") or ""
            })
        table_payload.append({
            "table": table_name,
            "columns": columns
        })

    if not table_payload:
        return []

    ordered_keys = [
        f"{table.get('table')}.{col.get('name')}"
        for table in table_payload
        for col in table.get("columns", [])
    ]
    all_keys = set(ordered_keys)

    def _parse_fields(content: str) -> Dict[str, Dict]:
        parsed = parse_llm_json(str(content))
        raw_fields = parsed.get("fields", []) if isinstance(parsed, dict) else []
        if not isinstance(raw_fields, list):
            return {}
        normalized_map = {}
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_prefill_item(item)
            if not normalized:
                continue
            key = f"{normalized['table']}.{normalized['column']}"
            normalized_map[key] = normalized
        return normalized_map

    async def _call_prefill(prompt: str) -> Dict[str, Dict]:
        llm = get_chat_data_llm(streaming=False, temperature=0, max_tokens=4000)
        response = await llm.ainvoke([SystemMessage(content=prompt)])
        content = response.content if hasattr(response, "content") else str(response)
        return _parse_fields(str(content))

    try:
        base_prompt = (
            "你是数据建模助手。请根据字段语义判断每个字段更适合作为维度还是指标。\n"
            "要求：\n"
            "1) 每个字段只能二选一：metric 或 dimension；不要双选。\n"
            "2) 输出必须是 JSON，不要解释文本。\n"
            "3) 输出格式：{\"fields\":[{\"table\":\"...\",\"column\":\"...\",\"role\":\"metric|dimension\"}]}\n"
            "4) 必须覆盖输入中的所有字段，每个字段都返回一条。\n"
            "输入数据：\n"
            f"{json.dumps(table_payload, ensure_ascii=False)}"
        )

        normalized_map = await _call_prefill(base_prompt)
        missing_keys = [key for key in ordered_keys if key not in normalized_map]
        if missing_keys:
            missing_payload = []
            for key in missing_keys:
                table_name, col_name = key.split(".", 1)
                table_item = next((t for t in table_payload if t.get("table") == table_name), None)
                col_item = next((c for c in (table_item.get("columns", []) if table_item else []) if c.get("name") == col_name), None)
                if col_item:
                    missing_payload.append({
                        "table": table_name,
                        "name": col_name,
                        "type": col_item.get("type", ""),
                        "description": col_item.get("description", "")
                    })

            retry_prompt = (
                "你是数据建模助手。请只判断以下缺失字段的角色。\n"
                "要求：\n"
                "1) 每个字段只能二选一：metric 或 dimension。\n"
                "2) 只输出 JSON，不要解释。\n"
                "3) 输出格式：{\"fields\":[{\"table\":\"...\",\"column\":\"...\",\"role\":\"metric|dimension\"}]}\n"
                "4) 必须覆盖输入中的全部字段。\n"
                "输入数据：\n"
                f"{json.dumps(missing_payload, ensure_ascii=False)}"
            )
            retry_map = await _call_prefill(retry_prompt)
            normalized_map.update(retry_map)

        if not all_keys.issubset(set(normalized_map.keys())):
            return None
        return [normalized_map[key] for key in ordered_keys]
    except Exception as e:
        logger.warning("自动预填调用大模型失败: %s", e)
        return None


def _resolve_prefill_tables(datasource: Dict, selected_tables: List[str]) -> List[Dict]:
    """根据勾选的表名过滤待预填表结构。"""
    selected = set(selected_tables or [])
    target_tables = []
    for table in datasource.get("ds_tables", []) or []:
        table_name = table.get("table_name")
        if not table_name:
            continue
        if selected and table_name not in selected:
            continue
        target_tables.append(table)
    return target_tables


@router.get("/query-favorites")
async def get_query_favorites(
    request: Request,
    datasource_id: str,
    query_type: Optional[str] = None,
    limit: int = 50
):
    """获取收藏查询卡片。"""
    try:
        user_id = _get_current_user_id(request)
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


@router.post("/query-favorites")
async def create_query_favorite(payload: QueryFavoriteCreateRequest, request: Request):
    """创建查询收藏。"""
    try:
        user_id = _get_current_user_id(request)
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
        user_id = _get_current_user_id(request)
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
            await db.flush()
            return business_response(data=item.to_dict(), msg="更新成功")
    except Exception as e:
        logger.error("更新收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="更新收藏查询失败，请稍后重试", code=50014, status="error")


@router.delete("/query-favorites/{favorite_id}")
async def delete_query_favorite(favorite_id: int, request: Request):
    """删除查询收藏（软删除）。"""
    try:
        user_id = _get_current_user_id(request)
        async with get_session() as db:
            stmt = select(SqlQueryFavorite).filter_by(id=favorite_id, user_id=user_id, delete_flag=0)
            result = await db.execute(stmt)
            item = result.scalar_one_or_none()
            if item is None:
                return business_response(msg="收藏不存在", code=40412, status="error")
            item.delete_flag = 1
            item.update_time = datetime.now()
            await db.flush()
            return business_response(data={"id": favorite_id}, msg="删除成功")
    except Exception as e:
        logger.error("删除收藏查询失败: %s", e, exc_info=True)
        return business_response(msg="删除收藏查询失败，请稍后重试", code=50015, status="error")


@router.post("/nl2sql")
async def quick_sql_endpoint(request: QuickSqlRequest):
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
            logger.info(f"收到快速SQL生成请求: {request.query[:100]}...")

            # 获取表结构
            tables = get_all_tables(request.datasource_id)
            if not tables:
                yield f"data: {json.dumps({'type': 'error', 'message': '数据源暂无表结构信息'}, ensure_ascii=False)}\n\n"
                return

            # 获取所有表的 DDL 格式
            table_names = [t.get("table_name") for t in tables]
            tables_ddl_info = get_tables_create_format(tables, table_names)

            # 构建 prompt
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            execution_target_info = _build_execution_target_info(request.datasource_id, request.member_datasource_ids)
            system_prompt = SQL_GENERATOR_PROMPT_GENERATE.format(
                current_time=current_time,
                execution_target_info=execution_target_info,
                tables_ddl_info=tables_ddl_info
            )

            # 调用 LLM（流式）
            llm = get_chat_data_llm()
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=request.query)
            ]

            # 流式输出
            full_content = ""
            async for chunk in llm.astream(messages):
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
async def get_datasources(include_tables: bool = True):
    """
    获取所有数据源列表

    Returns:
        数据源列表
    """
    try:
        ds_manager = get_datasource_manager()

        datasources = await _call_datasource_manager(ds_manager, "get_all_datasources", include_tables)

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


@router.post("/datasource-groups")
async def create_datasource_group(request: DatasourceGroupCreateRequest):
    """创建数据源组。"""
    try:
        group_name = (request.group_name or "").strip()
        if not group_name:
            return business_response(
                msg="数据源组名称不能为空",
                code=40001,
                status="error"
            )

        ds_manager = get_datasource_manager()
        exists = await _call_datasource_manager(ds_manager, "get_datasource_by_name", group_name)
        if exists:
            return business_response(
                msg=f"数据源组名称已存在: {group_name}",
                code=40001,
                status="error"
            )

        group_desc = (request.group_desc or "").strip()
        template_source = _normalize_source_item(
            request.source_type,
            request.template_source.host,
            request.template_source.port,
            request.template_source.database,
            request.template_source.datasource_name,
            request.template_source.query_executor_type,
        )
        member_sources = [
            _normalize_source_item(
                request.source_type,
                item.host,
                item.port,
                item.database,
                item.datasource_name,
                item.query_executor_type,
            )
            for item in (request.member_sources or [])
        ]
        selected_table_names = []
        for table_name in request.template_table_names or []:
            normalized_table_name = str(table_name or "").strip()
            if normalized_table_name and normalized_table_name not in selected_table_names:
                selected_table_names.append(normalized_table_name)
        tables = await _load_tables_from_source(
            request.source_type,
            template_source,
            selected_table_names or None,
        )
        datasource = await _call_datasource_manager(
            ds_manager,
            "create_datasource_group_from_sources",
            group_name=group_name,
            group_desc=group_desc,
            source_type=request.source_type,
            template_source_config=template_source,
            member_source_configs=member_sources,
            tables=tables,
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
async def update_datasource_meta(datasource_id: str, body: DatasourceUpdateRequest):
    """
    更新数据源元信息（名称、描述）

    Args:
        datasource_id: 数据源ID
        body: 更新请求

    Returns:
        更新后的数据源
    """
    try:
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", datasource_id)
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
            exists = await _call_datasource_manager(ds_manager, "get_datasource_by_name", name)
            if exists and exists.get("id") != datasource_id:
                return business_response(
                    msg=f"数据源名称已存在: {name}",
                    code=40001,
                    status="error"
                )

        updated = await _call_datasource_manager(
            ds_manager,
            "update_datasource",
            datasource_id,
            name=name,
            description=body.description
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
async def update_datasource_group_config(datasource_id: str, body: DatasourceGroupUpdateRequest):
    """更新数据源组配置。"""
    try:
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", datasource_id)
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

        exists = await _call_datasource_manager(ds_manager, "get_datasource_by_name", group_name)
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
            body.template_source.datasource_name,
            body.template_source.query_executor_type,
        )
        member_sources = [
            _normalize_source_item(
                body.source_type,
                item.host,
                item.port,
                item.database,
                item.datasource_name,
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
        )
        updated = await _call_datasource_manager(
            ds_manager,
            "update_datasource_group_from_sources",
            datasource_id=datasource_id,
            group_name=group_name,
            group_desc=(body.group_desc or "").strip(),
            source_type=body.source_type,
            template_source_config=template_source,
            member_source_configs=member_sources,
            tables=tables,
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
async def update_mapped_modules(datasource_id: str, body: dict):
    """
    更新数据源的模块映射

    Args:
        datasource_id: 数据源ID
        body: { "mapped_modules": ["举报管理", "统计分析"] }
    """
    try:
        ds_manager = get_datasource_manager()
        mapped_modules = body.get("mapped_modules", [])
        updated = await _call_datasource_manager(
            ds_manager,
            "update_datasource",
            datasource_id,
            mapped_modules=mapped_modules
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


@router.post("/datasources/{datasource_id}/set-default")
async def set_default_datasource(datasource_id: str):
    """
    设置默认数据源

    Args:
        datasource_id: 数据源ID

    Returns:
        操作结果
    """
    try:
        ds_manager = get_datasource_manager()
        success = await _call_datasource_manager(ds_manager, "set_default_datasource", datasource_id)

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
async def clear_default_datasource(datasource_id: str):
    """
    取消默认数据源

    Args:
        datasource_id: 数据源ID

    Returns:
        操作结果
    """
    try:
        ds_manager = get_datasource_manager()
        success = await _call_datasource_manager(ds_manager, "clear_default_datasource", datasource_id)

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


@router.post("/datasources/{datasource_id}/refresh")
async def refresh_single_datasource(
    datasource_id: str,
    request: DatasourceRefreshRequest = None
):
    """
    刷新单个数据源（异步获取表结构）。

    Args:
        datasource_id: 数据源ID
        request: 刷新请求（包含表名列表等信息）

    Returns:
        刷新结果
    """
    logger.info(f"刷新数据源: {datasource_id}")

    try:
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", datasource_id)

        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )

        source_type = datasource.get("source_type", "")
        source_config = datasource.get("source_config", {})

        if source_type not in ("mysql", "hive"):
            return business_response(
                msg=f"不支持刷新该类型的数据源: {source_type or '未知'}",
                code=40001,
                status="error"
            )

        table_names = request.table_names if request and request.table_names else None

        # 异步获取表结构（一次性查询）
        if source_type == "mysql":
            mysql_credentials = resolve_mysql_credentials(source_config)
            service = MySQLSyncService(
                host=source_config.get("host", "localhost"),
                port=source_config.get("port", 3306),
                database=source_config.get("database", ""),
                user=mysql_credentials["user"],
                password=mysql_credentials["password"]
            )
            tables = await asyncio.wait_for(
                service.get_full_schema_async(table_names),
                timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
            )
        else:  # hive
            hive_credentials = resolve_hive_credentials(source_config)
            service = HiveSyncService(
                host=source_config.get("host", "localhost"),
                port=source_config.get("port", 10000),
                database=source_config.get("database", "default"),
                username=hive_credentials["username"],
                password=hive_credentials.get("password"),
            )
            tables = await asyncio.wait_for(
                service.get_full_schema_async(table_names),
                timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
            )

        # 保存同步的表名列表
        synced_table_names = [t["table_name"] for t in tables]
        new_source_config = {**source_config, "table_names": synced_table_names}
        updated_datasource = await _call_datasource_manager(
            ds_manager,
            "update_datasource",
            datasource_id,
            tables=tables,
            source_config=new_source_config
        )

        return business_response(
            data={
                "datasource_id": updated_datasource["id"],
                "name": updated_datasource["ds_name"],
                "table_count": len(updated_datasource.get("ds_tables", []))
            },
            msg="刷新成功"
        )

    except asyncio.TimeoutError:
        logger.error(
            "刷新数据源超时: datasource_id=%s source_type=%s timeout=%ss",
            datasource_id,
            source_type,
            DATASOURCE_SYNC_TIMEOUT_SECONDS,
        )
        return business_response(
            msg=f"刷新失败: 同步超时（{DATASOURCE_SYNC_TIMEOUT_SECONDS}秒），请检查数据源类型、网络或权限配置",
            code=50008,
            status="error"
        )
    except Exception as e:
        logger.error(f"刷新数据源失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"刷新失败: {str(e)}",
            code=50008,
            status="error"
        )


@router.delete("/datasources/{datasource_id}")
async def delete_datasource(datasource_id: str):
    """
    删除数据源组

    Args:
        datasource_id: 数据源ID

    Returns:
        删除结果
    """
    try:
        ds_manager = get_datasource_manager()

        # 获取数据源信息
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", datasource_id)
        if not datasource:
            return business_response(
                msg="数据源不存在",
                code=40404,
                status="error"
            )

        # 删除数据源
        success = await _call_datasource_manager(ds_manager, "delete_datasource", datasource_id)

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
    tables: str = None
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
            table_names=table_names
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
async def get_db_tables(request: DbConnectionRequest):
    """
    获取数据库中的表列表（用于选择要同步的表）

    Args:
        request: 数据库连接请求

    Returns:
        表名列表
    """
    try:
        source_type = request.source_type

        # 设置默认端口
        port = request.port
        if port is None:
            port = 10000 if source_type == "hive" else 3306

        logger.info(f"获取 {source_type.upper()} 表列表: {request.host}:{port}/{request.database}")

        if source_type == "hive":
            hive_credentials = resolve_hive_credentials({})
            service = HiveSyncService(
                host=request.host,
                port=port,
                database=request.database,
                username=hive_credentials["username"],
                password=hive_credentials.get("password"),
            )
            tables = await asyncio.wait_for(
                service.get_tables_async(),
                timeout=DATASOURCE_SYNC_TIMEOUT_SECONDS,
            )
        else:
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
    query_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行单个成员数据源 SQL。"""
    executor = get_executor(execution_target)
    member_datasource_id = execution_target.get("member_datasource_id")
    member_name = execution_target.get("member_name") or execution_target.get("ds_name") or ""

    async def on_remote_task_created(payload: Dict[str, Any]) -> None:
        if not query_task_id:
            return
        remote_task_id = str((payload or {}).get("remote_task_id") or "").strip()
        if not remote_task_id:
            return
        await _append_query_task_remote_task(
            query_task_id,
            {
                "member_datasource_id": member_datasource_id,
                "member_name": member_name,
                "remote_task_id": remote_task_id,
                "source_type": str((payload or {}).get("source_type") or "").strip().lower() or "hive",
            },
        )

    execute_kwargs = {
        "sql": sql,
        "limit": limit,
        "timeout": timeout,
        "user_id": user_id,
    }
    if "remote_task_callback" in inspect.signature(executor.execute).parameters:
        execute_kwargs["remote_task_callback"] = on_remote_task_created

    try:
        result = await executor.execute(**execute_kwargs)
    finally:
        await executor.close()
    return {
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
        "sql": sql,
        "success": bool(result.get("success")),
        "columns": result.get("columns") or [],
        "data": result.get("data") or [],
        "total_count": int(result.get("total_count") or 0),
        "execution_time_ms": result.get("execution_time_ms"),
        "error": result.get("error"),
    }


async def _execute_dashboard_panel_sql(panel: Dict[str, Any], member_datasource_id: Optional[str] = None) -> Dict[str, Any]:
    """执行看板面板 SQL，仅针对一个成员数据源。"""
    datasource_id = panel.get("datasource_id")
    sql = panel.get("sql") or ""

    if not datasource_id:
        result = await execute_sql_tool_direct(sql, None)
        return {
            "member_datasource_id": None,
            "member_name": None,
            "sql": sql,
            "success": bool(result.get("success")),
            "columns": result.get("columns") or [],
            "data": result.get("data") or [],
            "total_count": int(result.get("total_count") or 0),
            "execution_time_ms": result.get("execution_time_ms"),
            "error": result.get("error"),
        }

    ds_manager = get_datasource_manager()
    selected_member_ids = [member_datasource_id] if member_datasource_id else None
    execution_targets = await _call_datasource_manager(ds_manager, "get_execution_datasources", datasource_id, selected_member_ids)
    execution_target = execution_targets[0]
    return await _execute_sql_for_member(
        execution_target=execution_target,
        sql=sql,
        limit=100,
        timeout=resolve_query_timeout(execution_target.get("source_type")),
    )


async def _execute_dashboard_panel_sql_with_target(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str] = None,
    execution_target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """执行看板面板 SQL，支持复用预解析执行对象。"""
    if execution_target is None:
        return await _execute_dashboard_panel_sql(panel, member_datasource_id)
    sql = panel.get("sql") or ""
    return await _execute_sql_for_member(
        execution_target=execution_target,
        sql=sql,
        limit=100,
        timeout=resolve_query_timeout(execution_target.get("source_type")),
    )


async def _build_dashboard_panel_execution_result(
    panel: Dict[str, Any],
    member_datasource_id: Optional[str] = None,
    execution_target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造看板面板执行返回。"""
    result = await _execute_dashboard_panel_sql_with_target(panel, member_datasource_id, execution_target)
    return {
        "panel_id": panel["id"],
        "name": panel["name"],
        "chart_type": panel["chart_type"],
        "chart_config": panel["chart_config"],
        "member_datasource_id": result.get("member_datasource_id"),
        "member_name": result.get("member_name"),
        "result": result,
    }


def _raise_if_dashboard_panel_execution_failed(payload: Dict[str, Any]) -> None:
    """校验面板执行结果，失败时抛错，统一单面板/批量/同步接口口径。"""
    result = (payload or {}).get("result") or {}
    if result.get("success"):
        return
    error_message = (
        result.get("error")
        or payload.get("error")
        or f"面板执行失败: {(payload or {}).get('panel_id') or 'unknown'}"
    )
    raise RuntimeError(str(error_message))


async def _record_sql_query_history(request: ExecuteSqlRequest, result_data: Dict[str, Any]) -> None:
    """记录 SQL / Builder 查询历史。"""
    query_type = getattr(request, 'query_type', 'sql') or 'sql'
    builder_config_json = None
    normalized_user_id = str(request.user_id or "").strip() or None
    operator = normalized_user_id or ""
    now = datetime.now()
    success_flag = 1 if (result_data or {}).get("success") else 0
    execution_time_ms = (result_data or {}).get("execution_time_ms")
    result_row_count = len((result_data or {}).get("data") or [])
    error_message = None if success_flag else str((result_data or {}).get("error") or "SQL 执行失败")
    if request.builder_config is not None:
        builder_config_json = json.dumps(request.builder_config, ensure_ascii=False)

    try:
        logger.info(f"开始记录查询历史: datasource_id={request.datasource_id}, query_type={query_type}, sql={request.sql[:50]}...")
        async with get_session() as db:
            db.add(SqlQueryHistory(
                datasource_id=request.datasource_id,
                user_id=normalized_user_id,
                sql_text=request.sql,
                query_type=query_type,
                builder_config=builder_config_json,
                query_title="SQL查询",
                last_used=now,
                execution_time_ms=execution_time_ms,
                result_row_count=result_row_count,
                success_flag=success_flag,
                error_message=error_message,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=operator,
                update_by=operator
            ))
            await db.commit()
    except Exception as e:
        logger.error(f"记录查询历史失败: {str(e)}", exc_info=True)


async def _build_execute_sql_result_data(request: ExecuteSqlRequest, query_task_id: Optional[str] = None) -> Dict[str, Any]:
    """执行 SQL 并返回统一结果数据。"""
    start_time = time.time()
    ds_manager = get_datasource_manager()
    datasource = await _call_datasource_manager(ds_manager, "get_datasource", request.datasource_id)

    if not datasource:
        raise ValueError(f"数据源不存在: {request.datasource_id}")

    execution_targets = await _call_datasource_manager(
        ds_manager,
        "get_execution_datasources",
        request.datasource_id,
        request.member_datasource_ids,
    )
    results = await asyncio.gather(*[
        _execute_sql_for_member(
            execution_target=target,
            sql=request.sql,
            limit=request.limit or 100,
            timeout=request.timeout,
            user_id=request.user_id,
            query_task_id=query_task_id,
        )
        for target in execution_targets
    ])

    execution_time_ms = int((time.time() - start_time) * 1000)
    primary_result = next((item for item in results if item.get("success")), results[0] if results else {})
    successful_results = [item for item in results if item.get("success")]
    overall_success = bool(successful_results)
    if overall_success:
        error_message = None
    else:
        error_messages = [
            str(item.get("error") or "").strip()
            for item in results
            if str(item.get("error") or "").strip()
        ]
        error_message = "；".join(error_messages) if error_messages else "所有执行对象均执行失败"
    return {
        "success": overall_success,
        "sql": primary_result.get("sql") or request.sql,
        "columns": primary_result.get("columns") or [],
        "data": primary_result.get("data") or [],
        "total_count": primary_result.get("total_count") or 0,
        "execution_time_ms": primary_result.get("execution_time_ms") or execution_time_ms,
        "member_datasource_id": primary_result.get("member_datasource_id"),
        "member_name": primary_result.get("member_name"),
        "error": error_message,
        "results": results,
    }


def _build_query_task_runtime_data(task: ChatDataQueryTask) -> Dict[str, Any]:
    """将任务模型转换为可脱离 session 使用的字典。"""
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "datasource_id": task.datasource_id,
        "query_type": task.query_type,
        "status": task.task_status,
        "progress_text": task.progress_text,
        "request_payload": task.request_payload,
        "result_payload": task.result_payload,
        "remote_task_payload": task.remote_task_payload,
        "error_message": task.error_message,
        "started_time": task.started_time.isoformat() if task.started_time else None,
        "finished_time": task.finished_time.isoformat() if task.finished_time else None,
        "create_time": task.create_time.isoformat() if task.create_time else None,
        "update_time": task.update_time.isoformat() if task.update_time else None,
    }


def _load_remote_tasks(raw_payload: Any) -> List[Dict[str, Any]]:
    """解析远端任务映射列表。"""
    if not raw_payload:
        return []
    if isinstance(raw_payload, list):
        payload = raw_payload
    else:
        try:
            payload = json.loads(raw_payload)
        except Exception:
            return []
    if not isinstance(payload, list):
        return []
    result: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        remote_task_id = str(item.get("remote_task_id") or "").strip()
        member_datasource_id = str(item.get("member_datasource_id") or "").strip()
        member_name = str(item.get("member_name") or "").strip()
        source_type = str(item.get("source_type") or "").strip().lower() or "hive"
        if not remote_task_id or not member_datasource_id:
            continue
        result.append({
            "member_datasource_id": member_datasource_id,
            "member_name": member_name,
            "remote_task_id": remote_task_id,
            "source_type": source_type,
        })
    return result


async def _are_all_sql_execution_targets_http(request: ExecuteSqlRequest) -> bool:
    """判断 SQL 任务选中的执行对象是否全部为 HTTP 执行。"""
    ds_manager = get_datasource_manager()
    execution_targets = await _call_datasource_manager(
        ds_manager,
        "get_execution_datasources",
        request.datasource_id,
        request.member_datasource_ids,
    )
    return bool(execution_targets) and all(
        str((target or {}).get("query_executor_type") or "").strip().lower() == "http"
        for target in execution_targets
    )


async def _build_live_remote_sql_result(response_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """基于远端 HTTP 任务实时拉取 SQL 结果。"""
    remote_tasks = list(response_data.get("remote_tasks") or [])
    if not remote_tasks:
        return None

    remote_member_results = await asyncio.gather(*[
        fetch_http_query_task_result(
            str(remote_task.get("remote_task_id") or ""),
            str(remote_task.get("source_type") or "hive"),
        )
        for remote_task in remote_tasks
    ])

    remote_results: List[Dict[str, Any]] = []
    for remote_task, remote_result in zip(remote_tasks, remote_member_results):
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

    return {
        "status": task_status,
        "progress_text": progress_text,
        "error_message": error_message,
        "result_payload": {
            "success": bool(successful_results),
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
    result_payload = None
    if task_data.get("result_payload"):
        try:
            result_payload = json.loads(task_data["result_payload"])
        except Exception:
            result_payload = None
    request_payload = None
    if task_data.get("request_payload"):
        try:
            request_payload = json.loads(task_data["request_payload"])
        except Exception:
            request_payload = None
    remote_tasks = _load_remote_tasks(task_data.get("remote_task_payload"))
    return {
        "task_id": task_data.get("task_id"),
        "task_type": task_data.get("task_type"),
        "datasource_id": task_data.get("datasource_id"),
        "query_type": task_data.get("query_type"),
        "status": task_data.get("status"),
        "progress_text": task_data.get("progress_text"),
        "request_payload": request_payload,
        "result_payload": result_payload,
        "remote_tasks": remote_tasks,
        "error_message": task_data.get("error_message"),
        "started_time": task_data.get("started_time"),
        "finished_time": task_data.get("finished_time"),
        "create_time": task_data.get("create_time"),
        "update_time": task_data.get("update_time"),
    }


async def _create_task_record(
    *,
    task_type: str,
    datasource_id: str,
    query_type: str,
    request_payload: Dict[str, Any],
    progress_text: str = "任务已创建，等待执行",
) -> Dict[str, Any]:
    """创建异步任务记录。"""
    task = ChatDataQueryTask(
        task_id=uuid.uuid4().hex,
        task_type=task_type,
        datasource_id=datasource_id,
        query_type=query_type or "sql",
        task_status="pending",
        progress_text=progress_text,
        request_payload=json.dumps(request_payload, ensure_ascii=False),
        delete_flag=0,
        create_time=datetime.now(),
    )
    async with get_session() as db:
        db.add(task)
        await db.flush()
        await db.refresh(task)
        return _build_query_task_response_data(_build_query_task_runtime_data(task))


async def _create_query_task(payload: QueryTaskCreateRequest) -> Dict[str, Any]:
    """创建 SQL 异步查询任务。"""
    return await _create_task_record(
        task_type=payload.task_type,
        datasource_id=payload.datasource_id,
        query_type=payload.query_type or "sql",
        request_payload=payload.model_dump(),
    )


async def _get_query_task(task_id: str) -> Optional[Dict[str, Any]]:
    """读取异步查询任务。"""
    async with get_session() as db:
        result = await db.execute(select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0))
        task = result.scalar_one_or_none()
        if not task:
            return None
        return _build_query_task_runtime_data(task)


async def _append_query_task_remote_task(task_id: str, remote_task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """向异步任务中追加远端 HTTP 任务映射。"""
    normalized_remote_task = {
        "member_datasource_id": str((remote_task or {}).get("member_datasource_id") or "").strip(),
        "member_name": str((remote_task or {}).get("member_name") or "").strip(),
        "remote_task_id": str((remote_task or {}).get("remote_task_id") or "").strip(),
        "source_type": str((remote_task or {}).get("source_type") or "").strip().lower() or "hive",
    }
    if not normalized_remote_task["member_datasource_id"] or not normalized_remote_task["remote_task_id"]:
        return None

    async with get_session() as db:
        stmt = select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0).with_for_update()
        result = await db.execute(stmt)
        task = result.scalar_one_or_none()
        if not task:
            return None
        remote_tasks = _load_remote_tasks(task.remote_task_payload)
        exists = any(
            item.get("member_datasource_id") == normalized_remote_task["member_datasource_id"]
            and item.get("remote_task_id") == normalized_remote_task["remote_task_id"]
            for item in remote_tasks
        )
        if not exists:
            remote_tasks.append(normalized_remote_task)
            task.remote_task_payload = json.dumps(remote_tasks, ensure_ascii=False)
            task.update_time = datetime.now()
            await db.commit()
            await db.refresh(task)
        return _build_query_task_response_data(_build_query_task_runtime_data(task))


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
    async with get_session() as db:
        result = await db.execute(select(ChatDataQueryTask).filter_by(task_id=task_id, delete_flag=0))
        task = result.scalar_one_or_none()
        if not task:
            return None
        if status is not None:
            task.task_status = status
        if progress_text is not None:
            task.progress_text = progress_text
        if result_payload is not None:
            task.result_payload = json.dumps(result_payload, ensure_ascii=False)
        if error_message is not None:
            task.error_message = error_message
        if started_time is not None:
            task.started_time = started_time
        if finished_time is not None:
            task.finished_time = finished_time
        task.update_time = datetime.now()
        await db.commit()
        await db.refresh(task)
        return _build_query_task_response_data(_build_query_task_runtime_data(task))


async def _run_sql_query_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行 SQL 查询任务。"""
    await _update_query_task(
        task_id,
        status="running",
        progress_text="SQL 执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    request_payload = json.loads(task.get("request_payload") or "{}")
    execute_request = ExecuteSqlRequest(**request_payload)
    try:
        all_http_targets = await _are_all_sql_execution_targets_http(execute_request)
    except Exception:
        all_http_targets = False
    try:
        result_data = await _build_execute_sql_result_data(execute_request, query_task_id=task_id)
    except TypeError as exc:
        if "query_task_id" not in str(exc):
            raise
        result_data = await _build_execute_sql_result_data(execute_request)
    if not result_data.get("success"):
        await _record_sql_query_history(execute_request, result_data)
        await _update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            result_payload=(None if all_http_targets else result_data),
            error_message=result_data.get("error") or "SQL 执行失败",
            finished_time=datetime.now(),
        )
        return

    await _record_sql_query_history(execute_request, result_data)
    await _update_query_task(
        task_id,
        status="succeeded",
        progress_text="执行完成",
        result_payload=(None if all_http_targets else result_data),
        error_message=None,
        finished_time=datetime.now(),
    )


async def _load_dashboard_panel_or_raise(panel_id: str) -> Dict[str, Any]:
    """读取面板，不存在则抛错。"""
    service = get_dashboard_service()
    panel = await service.get_panel(panel_id)
    if not panel:
        raise ValueError(f"面板不存在: {panel_id}")
    return panel


def _build_dashboard_execution_target_cache_key(datasource_id: str, member_datasource_id: Optional[str]) -> tuple[str, str]:
    """构造看板执行对象缓存键。"""
    return str(datasource_id or "").strip(), str(member_datasource_id or "").strip()


def _build_dashboard_panel_sql_preview(sql: Any, max_length: int = 160) -> str:
    """构造面板 SQL 日志预览。"""
    preview = " ".join(str(sql or "").split())
    if len(preview) <= max_length:
        return preview
    return f"{preview[:max_length]}..."


def _log_dashboard_panel_timing(
    panel: Optional[Dict[str, Any]],
    elapsed_ms: int,
    status: str,
    member_datasource_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """记录看板面板执行耗时。"""
    panel_data = panel or {}
    logger.info(
        "看板面板执行耗时: panel_id=%s name=%s datasource_id=%s member_datasource_id=%s status=%s elapsed_ms=%s sql=%s error=%s",
        str(panel_data.get("id") or "").strip() or "unknown",
        str(panel_data.get("name") or "").strip() or "unknown",
        str(panel_data.get("datasource_id") or "").strip() or "",
        str(member_datasource_id or "").strip(),
        status,
        elapsed_ms,
        _build_dashboard_panel_sql_preview(panel_data.get("sql")),
        str(error or "").strip(),
    )


async def _prepare_dashboard_batch_context(
    dashboard_id: str,
    panel_ids: List[str],
    panel_member_datasource_ids: Dict[str, str],
    preloaded_dashboard: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Dict[str, Any]], Dict[tuple[str, str], Dict[str, Any]]]:
    """预加载批量执行需要的面板与执行对象，减少每个卡片的重复查库。"""
    service = get_dashboard_service()
    dashboard = preloaded_dashboard
    if dashboard is None:
        dashboard = await service.get_dashboard(dashboard_id)
    if not dashboard:
        raise ValueError(f"大屏不存在: {dashboard_id}")

    panel_map = {
        str(panel.get("id") or "").strip(): panel
        for panel in (dashboard.get("panels") or [])
        if str(panel.get("id") or "").strip()
    }
    missing_panel_ids = [panel_id for panel_id in panel_ids if panel_id not in panel_map]
    if missing_panel_ids:
        raise ValueError(f"面板不存在: {missing_panel_ids[0]}")

    ds_manager = get_datasource_manager()
    execution_target_map: Dict[tuple[str, str], Dict[str, Any]] = {}
    for panel_id in panel_ids:
        panel = panel_map[panel_id]
        datasource_id = str(panel.get("datasource_id") or "").strip()
        if not datasource_id:
            continue
        member_datasource_id = panel_member_datasource_ids.get(panel_id)
        cache_key = _build_dashboard_execution_target_cache_key(datasource_id, member_datasource_id)
        if cache_key in execution_target_map:
            continue
        selected_member_ids = [member_datasource_id] if member_datasource_id else None
        execution_targets = await _call_datasource_manager(ds_manager, "get_execution_datasources", datasource_id, selected_member_ids)
        execution_target_map[cache_key] = execution_targets[0]

    return panel_map, execution_target_map


async def _run_dashboard_panel_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行单面板任务。"""
    await _update_query_task(
        task_id,
        status="running",
        progress_text="面板执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    request_payload = json.loads(task.get("request_payload") or "{}")
    panel = await _load_dashboard_panel_or_raise(str(request_payload.get("panel_id") or ""))
    result_data = await _build_dashboard_panel_execution_result(
        panel=panel,
        member_datasource_id=request_payload.get("member_datasource_id"),
    )
    if not (result_data.get("result") or {}).get("success"):
        await _update_query_task(
            task_id,
            status="failed",
            progress_text="执行失败",
            result_payload=result_data,
            error_message=(result_data.get("result") or {}).get("error") or "面板执行失败",
            finished_time=datetime.now(),
        )
        return
    await _update_query_task(
        task_id,
        status="succeeded",
        progress_text="执行完成",
        result_payload=result_data,
        error_message=None,
        finished_time=datetime.now(),
    )


def _build_dashboard_batch_result(dashboard_id: str, panel_ids: List[str]) -> Dict[str, Any]:
    """构造看板批量任务初始结果。"""
    return {
        "dashboard_id": dashboard_id,
        "total": len(panel_ids),
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "results": {
            panel_id: {"status": "pending"}
            for panel_id in panel_ids
        },
    }


async def _execute_dashboard_panel_task_item(
    panel_id: str,
    member_datasource_id: Optional[str] = None,
    semaphore: Optional[asyncio.Semaphore] = None,
    panel: Optional[Dict[str, Any]] = None,
    execution_target: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    """执行批量任务中的单个面板。"""
    if semaphore is not None:
        async with semaphore:
            return await _execute_dashboard_panel_task_item(panel_id, member_datasource_id, panel=panel, execution_target=execution_target)
    started_at = time.perf_counter()
    try:
        panel = panel or await _load_dashboard_panel_or_raise(panel_id)
        payload = await _build_dashboard_panel_execution_result(
            panel=panel,
            member_datasource_id=member_datasource_id,
            execution_target=execution_target,
        )
        _raise_if_dashboard_panel_execution_failed(payload)
        _log_dashboard_panel_timing(panel, int((time.perf_counter() - started_at) * 1000), "succeeded", member_datasource_id)
        return panel_id, {"status": "succeeded", "payload": payload}
    except Exception as e:
        _log_dashboard_panel_timing(panel, int((time.perf_counter() - started_at) * 1000), "failed", member_datasource_id, str(e))
        logger.error("执行看板面板任务失败: panel_id=%s err=%s", panel_id, e)
        return panel_id, {"status": "failed", "error": str(e)}


async def _run_dashboard_batch_task(task_id: str, task: Dict[str, Any]) -> None:
    """后台执行看板批量任务。"""
    await _update_query_task(
        task_id,
        status="running",
        progress_text="看板执行中",
        started_time=datetime.now(),
        error_message=None,
    )
    request_payload = json.loads(task.get("request_payload") or "{}")
    dashboard_id = str(request_payload.get("dashboard_id") or "")
    panel_ids = [str(panel_id).strip() for panel_id in (request_payload.get("panel_ids") or []) if str(panel_id).strip()]
    panel_member_datasource_ids = {
        str(panel_id): str(member_id).strip()
        for panel_id, member_id in (request_payload.get("panel_member_datasource_ids") or {}).items()
        if str(panel_id).strip() and str(member_id).strip()
    }

    service = get_dashboard_service()
    dashboard: Optional[Dict[str, Any]] = None
    if not panel_ids:
        dashboard = await service.get_dashboard(dashboard_id)
        if not dashboard:
            raise ValueError(f"大屏不存在: {dashboard_id}")
        panel_ids = [str(panel.get("id") or "").strip() for panel in (dashboard.get("panels") or []) if str(panel.get("id") or "").strip()]

    batch_result = _build_dashboard_batch_result(dashboard_id, panel_ids)
    await _update_query_task(task_id, result_payload=batch_result, progress_text="看板执行中")
    if not panel_ids:
        await _update_query_task(
            task_id,
            status="succeeded",
            progress_text="无面板需要执行",
            result_payload=batch_result,
            error_message=None,
            finished_time=datetime.now(),
        )
        return

    panel_map, execution_target_map = await _prepare_dashboard_batch_context(
        dashboard_id,
        panel_ids,
        panel_member_datasource_ids,
        preloaded_dashboard=dashboard,
    )
    semaphore = asyncio.Semaphore(DASHBOARD_PANEL_MAX_CONCURRENCY)
    tasks = [
        asyncio.create_task(
            _execute_dashboard_panel_task_item(
                panel_id,
                panel_member_datasource_ids.get(panel_id),
                semaphore,
                panel=panel_map.get(panel_id),
                execution_target=execution_target_map.get(
                    _build_dashboard_execution_target_cache_key(
                        str((panel_map.get(panel_id) or {}).get("datasource_id") or ""),
                        panel_member_datasource_ids.get(panel_id),
                    )
                ),
            )
        )
        for panel_id in panel_ids
    ]
    for completed_task in asyncio.as_completed(tasks):
        panel_id, entry = await completed_task
        batch_result["results"][panel_id] = entry
        batch_result["completed"] += 1
        if entry["status"] == "succeeded":
            batch_result["succeeded"] += 1
        else:
            batch_result["failed"] += 1
        await _update_query_task(
            task_id,
            progress_text=f"已完成 {batch_result['completed']}/{batch_result['total']}",
            result_payload=batch_result,
        )

    final_status = "succeeded"
    final_progress_text = "执行完成"
    final_error_message = None
    if batch_result["failed"] >= batch_result["total"] and batch_result["total"] > 0:
        final_status = "failed"
        final_progress_text = "执行失败"
        final_error_message = "所有面板执行失败"
    elif batch_result["failed"] > 0:
        final_status = "partial_success"
        final_progress_text = f"部分完成，失败 {batch_result['failed']} 个面板"
        final_error_message = f"{batch_result['failed']} 个面板执行失败"

    await _update_query_task(
        task_id,
        status=final_status,
        progress_text=final_progress_text,
        result_payload=batch_result,
        error_message=final_error_message,
        finished_time=datetime.now(),
    )


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


@router.post("/query-tasks")
async def create_query_task(request: Request, body: QueryTaskCreateRequest):
    """创建异步查询任务并返回任务 ID。"""
    try:
        current_user = authenticate_request(request)
        body = body.model_copy(update={"user_id": current_user.user_id})
        logger.info(
            "query-tasks current user: user_id=%s username=%s datasource_id=%s",
            current_user.user_id,
            current_user.username,
            body.datasource_id,
        )
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", body.datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {body.datasource_id}",
                code=40404,
                status="error"
            )
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
    except Exception as e:
        logger.error(f"创建异步查询任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="创建异步查询任务失败，请稍后重试",
            code=50010,
            status="error"
        )


@router.get("/query-tasks/{task_id}")
async def get_query_task(task_id: str):
    """查询异步任务状态与结果。"""
    try:
        task_data = await _get_query_task(task_id)
        if not task_data:
            return business_response(
                msg=f"任务不存在: {task_id}",
                code=40404,
                status="error"
            )
        response_data = _build_query_task_response_data(task_data)
        if response_data["task_type"] == "sql" and response_data["remote_tasks"]:
            live_remote_payload = await _build_live_remote_sql_result(response_data)
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
    except Exception as e:
        logger.error(f"查询异步任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="查询异步任务失败，请稍后重试",
            code=50010,
            status="error"
        )


@router.post("/execute-sql")
async def execute_sql_direct(request: Request, body: ExecuteSqlRequest):
    """直接执行 SQL（用于 SQL 编辑器模式）。"""
    try:
        current_user = authenticate_request(request)
        body = body.model_copy(update={"user_id": current_user.user_id})
        logger.info(
            "execute-sql current user: user_id=%s username=%s datasource_id=%s",
            current_user.user_id,
            current_user.username,
            body.datasource_id,
        )
        result_data = await _build_execute_sql_result_data(body)
        if not result_data.get("success"):
            asyncio.create_task(_record_sql_query_history(body, result_data))
            return business_response(
                data=result_data,
                msg=result_data.get("error") or "SQL 执行失败，请稍后重试或联系管理员",
                code=50010,
                status="error"
            )
        asyncio.create_task(_record_sql_query_history(body, result_data))
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


@router.post("/compile-builder-sql")
async def compile_builder_sql_endpoint(request: CompileBuilderSqlRequest):
    """编译 Builder DSL 为 SQL（仅预览，不执行）。"""
    try:
        dialect = "mysql"
        if request.datasource_id:
            ds_manager = get_datasource_manager()
            datasource = await _call_datasource_manager(ds_manager, "get_datasource", request.datasource_id)
            if not datasource:
                return business_response(msg=f"数据源不存在: {request.datasource_id}", code=40404, status="error")
            dialect = datasource.get("source_type", "mysql")
        sql = compile_builder_sql(request.builder_config, dialect=dialect)
        return business_response(data={"sql": sql}, msg="编译成功")
    except ValueError as err:
        return business_response(msg=str(err), code=40001, status="error")
    except Exception as err:
        logger.error(f"Builder DSL 编译失败: {err}", exc_info=True)
        return business_response(msg="Builder DSL 编译失败，请检查配置", code=50010, status="error")


# ============ 多语义配置接口（新版） ============

@router.get("/datasources/{datasource_id}/semantic-configs")
async def list_semantic_configs(datasource_id: str):
    """
    获取数据源的所有语义配置

    Args:
        datasource_id: 数据源ID

    Returns:
        语义配置列表
    """
    try:
        ds_manager = get_datasource_manager()
        if not await _call_datasource_manager(ds_manager, "has_datasource", datasource_id):
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )

        configs = await _call_datasource_manager(ds_manager, "get_semantic_configs", datasource_id)

        return business_response(
            data={"configs": configs, "total": len(configs)},
            msg="获取成功"
        )

    except Exception as e:
        logger.error(f"获取语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50010,
            status="error"
        )


@router.post("/datasources/{datasource_id}/semantic-configs")
async def create_semantic_config_new(datasource_id: str, request: SemanticConfigCreateRequest):
    """
    创建新的语义配置

    Args:
        datasource_id: 数据源ID
        request: 语义配置内容

    Returns:
        创建的语义配置
    """
    try:
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", datasource_id)

        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )

        metrics = [m.model_dump() for m in request.metrics]
        dimensions = [d.model_dump() for d in request.dimensions]
        validation_error = _validate_semantic_fields(datasource, metrics, dimensions)
        if validation_error:
            return business_response(msg=validation_error, code=40010, status="error")

        config = await _call_datasource_manager(
            ds_manager,
            "create_semantic_config",
            datasource_id=datasource_id,
            name=request.name,
            description=request.description,
            enabled=request.enabled,
            metrics=metrics,
            dimensions=dimensions,
            relationships=[r.model_dump() for r in request.relationships]
        )

        if not config:
            return business_response(
                msg="创建失败",
                code=50010,
                status="error"
            )

        logger.info(f"创建语义配置: {config['id']} - {config['name']}")

        return business_response(
            data=config,
            msg="创建成功"
        )

    except Exception as e:
        logger.error(f"创建语义配置失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"创建失败: {str(e)}",
            code=50010,
            status="error"
        )


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/auto-prefill")
async def auto_prefill_semantic_fields(datasource_id: str, config_id: str, request: SemanticAutoPrefillRequest):
    """按规则自动预填维度/指标/表达式。"""
    try:
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )
        config = await _call_datasource_manager(ds_manager, "get_semantic_config", datasource_id, config_id)
        if not config:
            return business_response(
                msg=f"语义配置不存在: {config_id}",
                code=40404,
                status="error"
            )
        target_tables = _resolve_prefill_tables(datasource, request.selected_tables)

        fields = await _prefill_by_llm(target_tables)
        if fields is None:
            return business_response(
                msg="自动预填失败：大模型返回结果不完整或不可解析，请重试",
                code=50010,
                status="error"
            )

        return business_response(
            data={"fields": fields, "total": len(fields)},
            msg="自动预填完成"
        )

    except Exception as e:
        logger.error(f"自动预填字段失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"自动预填失败: {str(e)}",
            code=50010,
            status="error"
        )


@router.post("/semantic/prefill")
async def semantic_prefill(request: SemanticPrefillRequest):
    """语义字段预填短路由，前端直接传待预填表结构。"""
    try:
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", request.datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {request.datasource_id}",
                code=40404,
                status="error"
            )

        config = await _call_datasource_manager(ds_manager, "get_semantic_config", request.datasource_id, request.config_id)
        if not config:
            return business_response(
                msg=f"语义配置不存在: {request.config_id}",
                code=40404,
                status="error"
            )

        target_tables = [table.model_dump() for table in request.tables]
        fields = await _prefill_by_llm(target_tables)
        if fields is None:
            return business_response(
                msg="自动预填失败：大模型返回结果不完整或不可解析，请重试",
                code=50010,
                status="error"
            )

        return business_response(
            data={"fields": fields, "total": len(fields)},
            msg="自动预填完成"
        )

    except Exception as e:
        logger.error(f"语义字段预填失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"自动预填失败: {str(e)}",
            code=50010,
            status="error"
        )


@router.put("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def update_semantic_config_new(
    datasource_id: str,
    config_id: str,
    request: SemanticConfigUpdateRequest
):
    """
    更新语义配置

    Args:
        datasource_id: 数据源ID
        config_id: 配置ID
        request: 更新内容

    Returns:
        更新后的配置
    """
    try:
        ds_manager = get_datasource_manager()
        datasource = await _call_datasource_manager(ds_manager, "get_datasource", datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}",
                code=40404,
                status="error"
            )
        current_config = await _call_datasource_manager(ds_manager, "get_semantic_config", datasource_id, config_id)
        if not current_config:
            return business_response(
                msg=f"语义配置不存在: {config_id}",
                code=40404,
                status="error"
            )

        # 准备更新参数
        update_kwargs = {}
        if request.name is not None:
            update_kwargs["name"] = request.name
        if request.description is not None:
            update_kwargs["description"] = request.description
        if request.enabled is not None:
            update_kwargs["enabled"] = request.enabled
        if request.metrics is not None:
            update_kwargs["metrics"] = [m.model_dump() for m in request.metrics]
        if request.dimensions is not None:
            update_kwargs["dimensions"] = [d.model_dump() for d in request.dimensions]
        if request.relationships is not None:
            update_kwargs["relationships"] = [r.model_dump() for r in request.relationships]

        merged_metrics = update_kwargs.get("metrics", current_config.get("metrics", []))
        merged_dimensions = update_kwargs.get("dimensions", current_config.get("dimensions", []))
        validation_error = _validate_semantic_fields(datasource, merged_metrics, merged_dimensions)
        if validation_error:
            return business_response(msg=validation_error, code=40010, status="error")

        config = await _call_datasource_manager(
            ds_manager,
            "update_semantic_config",
            datasource_id=datasource_id,
            config_id=config_id,
            **update_kwargs
        )

        if not config:
            return business_response(
                msg=f"语义配置不存在: {config_id}",
                code=40404,
                status="error"
            )

        logger.info(f"更新语义配置: {config_id}")

        return business_response(
            data=config,
            msg="更新成功"
        )

    except Exception as e:
        logger.error(f"更新语义配置失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50010,
            status="error"
        )


@router.delete("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def delete_semantic_config_new(datasource_id: str, config_id: str):
    """
    删除语义配置

    Args:
        datasource_id: 数据源ID
        config_id: 配置ID

    Returns:
        删除结果
    """
    try:
        ds_manager = get_datasource_manager()
        success = await _call_datasource_manager(ds_manager, "delete_semantic_config", datasource_id, config_id)

        if not success:
            return business_response(
                msg=f"语义配置不存在: {config_id}",
                code=40404,
                status="error"
            )

        logger.info(f"删除语义配置: {config_id}")

        return business_response(
            data={"config_id": config_id},
            msg="删除成功"
        )

    except Exception as e:
        logger.error(f"删除语义配置失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"删除失败: {str(e)}",
            code=50010,
            status="error"
        )


@router.patch("/datasources/{datasource_id}/semantic-configs/{config_id}/toggle")
async def toggle_semantic_config_new(datasource_id: str, config_id: str, enabled: bool):
    """
    切换单个语义配置的启用状态

    Args:
        datasource_id: 数据源ID
        config_id: 配置ID
        enabled: 是否启用

    Returns:
        更新后的状态
    """
    try:
        ds_manager = get_datasource_manager()
        config = await _call_datasource_manager(
            ds_manager,
            "update_semantic_config",
            datasource_id=datasource_id,
            config_id=config_id,
            enabled=enabled
        )

        if not config:
            return business_response(
                msg=f"语义配置不存在: {config_id}",
                code=40404,
                status="error"
            )

        logger.info(f"切换语义配置: {config_id}, enabled={enabled}")

        return business_response(
            data={"config_id": config_id, "enabled": enabled},
            msg=f"语义配置已{'启用' if enabled else '禁用'}"
        )

    except Exception as e:
        logger.error(f"切换语义配置失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"操作失败: {str(e)}",
            code=50010,
            status="error"
        )


# ============ 对话接口 ============
# 会话管理（创建、列表、删除、详情、completion）统一由公共接口 /api/conversations 处理
# sql_generator 的 completion handler 已在 __init__.py 中注册


# ============ 分析大屏接口 ============

from .schema import (
    DashboardCreateRequest,
    DashboardUpdateRequest,
    PanelCreateRequest,
    PanelNameGenerateRequest,
    PanelUpdateRequest,
    LayoutUpdateRequest,
)
from .services.dashboard_service import get_dashboard_service


def _truncate_text(value: str, max_length: int) -> str:
    """裁剪文本长度并去除首尾空白。"""
    text = (value or "").strip()
    if not text:
        return ""
    return text[:max_length].strip()


def _chart_type_label(chart_type: str) -> str:
    """将图表类型转换为中文标签。"""
    chart_labels = {
        "table": "表格",
        "bar": "柱状图",
        "horizontal_bar": "条形图",
        "line": "折线图",
        "pie": "饼图",
        "map": "地图",
        "kpi": "指标卡",
    }
    return chart_labels.get(chart_type, "图表")


def _build_panel_name_fallback(chart_type: str, max_length: int = 24) -> str:
    """构建兜底面板标题。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback = f"{_chart_type_label(chart_type)}_{timestamp}"
    return _truncate_text(fallback, max_length)


async def _resolve_panel_name(sql: str, chart_type: str, provided_name: Optional[str], max_length: int = 24) -> str:
    """生成面板标题：优先显式名称，其次LLM，最后本地兜底。"""
    explicit_name = _truncate_text(provided_name or "", max_length)
    if explicit_name:
        return explicit_name
    try:
        metadata = await generate_query_metadata(sql)
        llm_title = _truncate_text(str(metadata.get("title", "")), max_length)
        if llm_title and llm_title != "SQL查询":
            return llm_title
    except Exception as e:
        logger.warning("保存面板时生成标题失败: %s", e)
    return _build_panel_name_fallback(chart_type, max_length)


@router.get("/dashboards")
async def list_dashboards(user_id: str = "default"):
    """
    获取大屏列表

    Args:
        user_id: 用户ID（可选，默认 default）

    Returns:
        大屏列表
    """
    try:
        service = get_dashboard_service()
        dashboards = await service.list_dashboards(user_id=user_id)

        return business_response(
            data={"dashboards": dashboards, "total": len(dashboards)},
            msg="获取成功"
        )

    except Exception as e:
        logger.error(f"获取大屏列表失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50020,
            status="error"
        )


@router.post("/dashboards")
async def create_dashboard(request: DashboardCreateRequest, user_id: str = "default"):
    """
    创建新的分析大屏

    Args:
        request: 创建请求
        user_id: 用户ID

    Returns:
        创建的大屏信息
    """
    try:
        service = get_dashboard_service()
        dashboard = await service.create_dashboard(
            name=request.name,
            user_id=user_id,
            description=request.description,
            datasource_id=request.datasource_id,
            theme=request.theme
        )

        return business_response(
            data=dashboard,
            msg="创建成功"
        )

    except Exception as e:
        logger.error(f"创建大屏失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"创建失败: {str(e)}",
            code=50021,
            status="error"
        )


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str):
    """
    获取大屏详情（含所有面板）

    Args:
        dashboard_id: 大屏ID

    Returns:
        大屏详情及面板列表
    """
    try:
        service = get_dashboard_service()
        dashboard = await service.get_dashboard(dashboard_id)

        if not dashboard:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data=dashboard,
            msg="获取成功"
        )

    except Exception as e:
        logger.error(f"获取大屏详情失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"获取失败: {str(e)}",
            code=50022,
            status="error"
        )


@router.put("/dashboards/{dashboard_id}")
async def update_dashboard(dashboard_id: str, request: DashboardUpdateRequest):
    """
    更新大屏信息

    Args:
        dashboard_id: 大屏ID
        request: 更新请求

    Returns:
        更新后的大屏信息
    """
    try:
        service = get_dashboard_service()
        dashboard = await service.update_dashboard(
            dashboard_id=dashboard_id,
            name=request.name,
            description=request.description,
            theme=request.theme,
            background_id=request.background_id
        )

        if not dashboard:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data=dashboard,
            msg="更新成功"
        )

    except Exception as e:
        logger.error(f"更新大屏失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50023,
            status="error"
        )


@router.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str):
    """
    删除大屏（同时删除所有面板）

    Args:
        dashboard_id: 大屏ID

    Returns:
        删除结果
    """
    try:
        service = get_dashboard_service()
        success = await service.delete_dashboard(dashboard_id)

        if not success:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data={"dashboard_id": dashboard_id},
            msg="删除成功"
        )

    except Exception as e:
        logger.error(f"删除大屏失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"删除失败: {str(e)}",
            code=50024,
            status="error"
        )


# ============ 面板接口 ============

@router.post("/dashboards/{dashboard_id}/panels")
async def add_panel(dashboard_id: str, request: PanelCreateRequest):
    """
    添加面板到大屏

    Args:
        dashboard_id: 大屏ID
        request: 面板创建请求

    Returns:
        创建的面板信息
    """
    try:
        service = get_dashboard_service()
        panel_name = _truncate_text(request.name or "", 24) or _build_panel_name_fallback(request.chart_type, 24)
        panel = await service.add_panel(
            dashboard_id=dashboard_id,
            name=panel_name,
            sql=request.sql,
            chart_type=request.chart_type,
            chart_config=request.chart_config,
            layout_x=request.layout_x,
            layout_y=request.layout_y,
            layout_w=request.layout_w,
            layout_h=request.layout_h,
            refresh_interval=request.refresh_interval,
            source_thread_id=request.source_thread_id,
            datasource_id=request.datasource_id
        )

        if not panel:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data=panel,
            msg="添加成功"
        )

    except Exception as e:
        logger.error(f"添加面板失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"添加失败: {str(e)}",
            code=50025,
            status="error"
        )


@router.post("/panel-name-generate")
async def generate_panel_name(request: PanelNameGenerateRequest):
    """生成面板标题。"""
    try:
        panel_name = await _resolve_panel_name(
            sql=request.sql,
            chart_type=request.chart_type,
            provided_name=request.name,
            max_length=24
        )
        return business_response(
            data={"name": panel_name},
            msg="生成成功"
        )
    except Exception as e:
        logger.error(f"生成面板标题失败: {str(e)}", exc_info=True)
        return business_response(
            msg="生成面板标题失败，请稍后重试",
            code=50031,
            status="error"
        )


@router.put("/dashboards/{dashboard_id}/panels/{panel_id}")
async def update_panel(dashboard_id: str, panel_id: str, request: PanelUpdateRequest):
    """
    更新面板

    Args:
        dashboard_id: 大屏ID
        panel_id: 面板ID
        request: 更新请求

    Returns:
        更新后的面板信息
    """
    try:
        service = get_dashboard_service()
        panel = await service.update_panel(
            panel_id=panel_id,
            name=request.name,
            sql=request.sql,
            chart_type=request.chart_type,
            chart_config=request.chart_config,
            layout_x=request.layout_x,
            layout_y=request.layout_y,
            layout_w=request.layout_w,
            layout_h=request.layout_h,
            refresh_interval=request.refresh_interval
        )

        if not panel:
            return business_response(
                msg=f"面板不存在: {panel_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data=panel,
            msg="更新成功"
        )

    except Exception as e:
        logger.error(f"更新面板失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50026,
            status="error"
        )


@router.delete("/dashboards/{dashboard_id}/panels/{panel_id}")
async def delete_panel(dashboard_id: str, panel_id: str):
    """
    删除面板

    Args:
        dashboard_id: 大屏ID
        panel_id: 面板ID

    Returns:
        删除结果
    """
    try:
        service = get_dashboard_service()
        success = await service.delete_panel(panel_id)

        if not success:
            return business_response(
                msg=f"面板不存在: {panel_id}",
                code=40404,
                status="error"
            )

        return business_response(
            data={"panel_id": panel_id},
            msg="删除成功"
        )

    except Exception as e:
        logger.error(f"删除面板失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"删除失败: {str(e)}",
            code=50027,
            status="error"
        )


@router.put("/dashboards/{dashboard_id}/layout")
async def update_layout(dashboard_id: str, request: LayoutUpdateRequest):
    """
    批量更新面板布局

    用于拖拽调整面板位置和大小后保存

    Args:
        dashboard_id: 大屏ID
        request: 布局更新请求

    Returns:
        更新结果
    """
    try:
        service = get_dashboard_service()
        success = await service.update_panels_layout(
            dashboard_id=dashboard_id,
            layouts=request.layouts
        )

        return business_response(
            data={"dashboard_id": dashboard_id, "updated": len(request.layouts)},
            msg="布局更新成功"
        )

    except Exception as e:
        logger.error(f"更新布局失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"更新失败: {str(e)}",
            code=50028,
            status="error"
        )


@router.post("/dashboards/{dashboard_id}/panels/{panel_id}/execute")
async def execute_panel(dashboard_id: str, panel_id: str, request: Optional[PanelExecuteRequest] = None):
    """
    执行面板 SQL 并返回数据

    用于大屏查看时刷新单个面板数据

    Args:
        dashboard_id: 大屏ID
        panel_id: 面板ID

    Returns:
        查询结果数据
    """
    try:
        service = get_dashboard_service()
        panel = await service.get_panel(panel_id)

        if not panel:
            return business_response(
                msg=f"面板不存在: {panel_id}",
                code=40404,
                status="error"
            )

        payload = await _build_dashboard_panel_execution_result(
            panel=panel,
            member_datasource_id=(request.member_datasource_id if request else None),
        )
        _raise_if_dashboard_panel_execution_failed(payload)

        return business_response(data=payload, msg="执行成功")

    except ValueError as e:
        return business_response(
            msg=str(e),
            code=40001,
            status="error"
        )
    except RuntimeError as e:
        return business_response(
            msg=str(e),
            code=50029,
            status="error"
        )

    except Exception as e:
        logger.error(f"执行面板SQL失败: {str(e)}", exc_info=True)
        return business_response(
            msg=f"执行失败: {str(e)}",
            code=50029,
            status="error"
        )


@router.post("/dashboards/{dashboard_id}/panels/{panel_id}/query-tasks")
async def create_panel_query_task(dashboard_id: str, panel_id: str, request: Optional[PanelExecuteRequest] = None):
    """创建单面板异步执行任务，兼容代理到批量任务接口。"""
    batch_request = PanelBatchExecuteRequest(
        panel_ids=[panel_id],
        panel_member_datasource_ids=(
            {panel_id: request.member_datasource_id}
            if request and request.member_datasource_id
            else {}
        ),
    )
    return await create_dashboard_query_task(dashboard_id, batch_request)


@router.post("/dashboards/{dashboard_id}/panels/execute-batch")
async def execute_panels_batch(
    dashboard_id: str,
    request: Optional[PanelBatchExecuteRequest | List[str]] = None,
):
    """
    批量执行面板 SQL 并流式返回数据

    使用 SSE 流式返回，每完成一个面板就立即返回结果

    Args:
        dashboard_id: 大屏ID
        panel_ids: 面板ID列表，为空时执行所有面板

    Returns:
        SSE 流，每个事件包含一个面板的执行结果
    """
    service = get_dashboard_service()
    dashboard: Optional[Dict[str, Any]] = None

    # 如果没有指定 panel_ids，获取该大屏下所有面板
    panel_ids: Optional[List[str]] = None
    panel_member_datasource_ids: Dict[str, str] = {}
    if isinstance(request, list):
        panel_ids = request
    elif request is not None:
        panel_ids = request.panel_ids or None
        panel_member_datasource_ids = {
            str(panel_id): str(member_id).strip()
            for panel_id, member_id in (request.panel_member_datasource_ids or {}).items()
            if str(panel_id).strip() and str(member_id).strip()
        }

    if not panel_ids:
        dashboard = await service.get_dashboard(dashboard_id)
        if not dashboard:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )
        panel_ids = [p["id"] for p in dashboard.get("panels", [])]

    if not panel_ids:
        return business_response(
            data={"results": {}},
            msg="无面板需要执行"
        )

    panel_map, execution_target_map = await _prepare_dashboard_batch_context(
        dashboard_id,
        panel_ids,
        panel_member_datasource_ids,
        preloaded_dashboard=dashboard,
    )

    async def generate():
        """流式生成器，每完成一个面板就 yield 结果"""
        semaphore = asyncio.Semaphore(DASHBOARD_PANEL_MAX_CONCURRENCY)

        async def execute_single_panel(panel_id: str):
            """执行单个面板并返回结果"""
            async with semaphore:
                panel = panel_map.get(panel_id)
                started_at = time.perf_counter()
                try:
                    if not panel:
                        return {
                            "panel_id": panel_id,
                            "error": f"面板不存在: {panel_id}"
                        }
                    execution_target = execution_target_map.get(
                        _build_dashboard_execution_target_cache_key(
                            str(panel.get("datasource_id") or ""),
                            panel_member_datasource_ids.get(panel_id),
                        )
                    )
                    payload = await _build_dashboard_panel_execution_result(
                        panel=panel,
                        member_datasource_id=panel_member_datasource_ids.get(panel_id),
                        execution_target=execution_target,
                    )
                    _raise_if_dashboard_panel_execution_failed(payload)
                    _log_dashboard_panel_timing(
                        panel,
                        int((time.perf_counter() - started_at) * 1000),
                        "succeeded",
                        panel_member_datasource_ids.get(panel_id),
                    )
                    return payload
                except ValueError as e:
                    _log_dashboard_panel_timing(
                        panel,
                        int((time.perf_counter() - started_at) * 1000),
                        "failed",
                        panel_member_datasource_ids.get(panel_id),
                        str(e),
                    )
                    return {
                        "panel_id": panel_id,
                        "error": str(e)
                    }
                except Exception as e:
                    _log_dashboard_panel_timing(
                        panel,
                        int((time.perf_counter() - started_at) * 1000),
                        "failed",
                        panel_member_datasource_ids.get(panel_id),
                        str(e),
                    )
                    logger.error(f"执行面板 {panel_id} 失败: {str(e)}")
                    return {
                        "panel_id": panel_id,
                        "error": str(e)
                    }

        # 创建所有任务，记录 panel_id 映射
        tasks = {asyncio.create_task(execute_single_panel(pid)): pid for pid in panel_ids}
        pending = set(tasks.keys())

        # 使用 wait + FIRST_COMPLETED 实现先完成先返回
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                panel_id = tasks[task]
                try:
                    result = task.result()
                    yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                except Exception as e:
                    logger.error(f"面板 {panel_id} 执行异常: {str(e)}")
                    yield f"data: {json.dumps({'panel_id': panel_id, 'error': str(e)}, ensure_ascii=False)}\n\n"

        # 发送结束标记
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/dashboards/{dashboard_id}/query-tasks")
async def create_dashboard_query_task(
    dashboard_id: str,
    request: Optional[PanelBatchExecuteRequest | List[str]] = None,
):
    """创建看板批量异步执行任务。"""
    try:
        service = get_dashboard_service()
        dashboard = await service.get_dashboard(dashboard_id)
        if not dashboard:
            return business_response(
                msg=f"大屏不存在: {dashboard_id}",
                code=40404,
                status="error"
            )

        panel_ids: Optional[List[str]] = None
        panel_member_datasource_ids: Dict[str, str] = {}
        if isinstance(request, list):
            panel_ids = request
        elif request is not None:
            panel_ids = request.panel_ids or None
            panel_member_datasource_ids = {
                str(panel_id): str(member_id).strip()
                for panel_id, member_id in (request.panel_member_datasource_ids or {}).items()
                if str(panel_id).strip() and str(member_id).strip()
            }

        task_info = await _create_task_record(
            task_type="dashboard_batch",
            datasource_id=str(dashboard.get("datasource_id") or ""),
            query_type="dashboard",
            request_payload={
                "dashboard_id": dashboard_id,
                "panel_ids": panel_ids or [],
                "panel_member_datasource_ids": panel_member_datasource_ids,
            },
            progress_text="看板任务已创建，等待执行",
        )
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
    except Exception as e:
        logger.error(f"创建看板异步任务失败: {str(e)}", exc_info=True)
        return business_response(
            msg="创建看板异步任务失败，请稍后重试",
            code=50029,
            status="error"
        )
