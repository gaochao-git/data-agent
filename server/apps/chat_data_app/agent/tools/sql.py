"""
SQL Generator 工具函数

提供表选择、DDL 生成等辅助功能，用于 Graph 执行前的预处理阶段
"""

import asyncio
import inspect
import json
import os
import re
from datetime import datetime
from typing import Any, Annotated

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.types import interrupt

from utils.logger import get_logger
from ...schema import ExecuteSqlInput
from ...services.datasource_manager import get_datasource_manager
from ...services.llm_config import get_chat_data_llm
from ...services.runtime_config import DEFAULT_ROW_LIMIT, TOOL_RESULT_OFFLOAD_SIZE
from ...services.sql_concurrency_limiter import run_with_sql_user_datasource_limit
from ...services.sql_executor import get_executor
from ..state import save_query_result
from ..prompts.nl2sql import TABLE_SELECTOR_PROMPT

logger = get_logger(__name__)

# ============ SQL 执行审批开关 ============
# true = 需要用户确认才能执行 SQL，false = 直接执行（默认关闭）
SQL_APPROVAL_ENABLED = os.getenv("SQL_APPROVAL_ENABLED", "false").lower() == "true"
logger.info(f"[ChatData] SQL 执行审批: {'开启' if SQL_APPROVAL_ENABLED else '关闭'}")


async def get_all_tables_async(datasource_id: str | None) -> list[dict[str, Any]]:
    """
    异步获取指定数据源的所有表元数据（表名、描述、字段结构）。

    从数据源表结构缓存中读取表信息，包含完整的表结构定义。
    这是表选择流程的第一步，获取候选表的完整信息。
    """
    datasource_manager = get_datasource_manager()

    if datasource_id:
        datasource = await datasource_manager.get_datasource_async(datasource_id)
        return datasource.get("ds_tables", []) if datasource else []

    all_datasources = await datasource_manager.get_all_datasources_async()
    tables: list[dict[str, Any]] = []
    for ds in all_datasources:
        tables.extend(ds.get("ds_tables", []))
    return tables


def get_tables_name_comment(tables: list[dict[str, Any]]) -> str:
    """
    生成表名和备注的摘要文本（用于 LLM 表选择）

    只提取表名和备注，不包含字段详情，用于第一轮 LLM 调用。
    这样可以用很少的 token 让 LLM 判断需要哪些表。

    Args:
        tables: 表信息列表（来自 get_all_tables_async）

    Returns:
        格式化的表名+备注字符串，示例:
        ```
        - ods_order: 订单主表
        - ods_customer: 客户信息表
        - ods_product: 商品表
        ```
    """
    if not tables:
        return "暂无可用数据表"

    lines = []
    for table in tables:
        table_name = table.get("table_name", "")
        description = table.get("description", "")
        lines.append(f"- {table_name}: {description}")

    return "\n".join(lines)


def get_tables_create_format(tables: list[dict[str, Any]], selected_table_names: list[str]) -> str:
    """
    获取指定表的 CREATE TABLE 格式文本（用于 SQL 生成）

    根据 LLM 选择的表名，生成这些表的 CREATE TABLE 语句。
    只加载选中的表，避免传入所有表的结构信息浪费 token。

    Args:
        tables: 全部表信息列表（来自 get_all_tables_async）
        selected_table_names: LLM 选中的表名列表

    Returns:
        格式化的 CREATE TABLE 文本，示例:
        ```
        ### ods_order
        描述: 订单主表
        ```sql
        CREATE TABLE ods_order (
            order_id INT PRIMARY KEY  -- 订单ID,
            customer_id INT  -- 客户ID,
            order_date TIMESTAMP  -- 下单时间,
            amount DECIMAL(10,2)  -- 订单金额
        );
        ```
        ```
    """
    if not tables or not selected_table_names:
        return ""

    # 过滤出选中的表
    selected_tables = [t for t in tables if t.get("table_name") in selected_table_names]

    if not selected_tables:
        return ""

    lines = []
    for table in selected_tables:
        table_name = table.get("table_name", "")
        description = table.get("description", "")
        columns = table.get("columns", [])

        lines.append(f"### {table_name}")
        lines.append(f"描述: {description}")
        lines.append("```sql")
        lines.append(f"CREATE TABLE {table_name} (")

        col_defs = []
        for col in columns:
            col_def = f"    {col.get('name', '')} {col.get('type', 'TEXT')}"
            if col.get("primary_key"):
                col_def += " PRIMARY KEY"
            if col.get("description"):
                col_def += f"  -- {col.get('description')}"
            col_defs.append(col_def)

        lines.append(",\n".join(col_defs))
        lines.append(");")
        lines.append("```\n")

    return "\n".join(lines)


async def llm_select_tables(user_query: str, tables: list[dict[str, Any]]) -> list[str]:
    """
    根据用户问题智能选择相关的表（LLM 调用）

    这是两步表选择策略的核心：
    1. 只传表名+备注给 LLM（token 少）
    2. LLM 返回需要的表名列表
    3. 后续只加载选中表的完整 DDL

    Args:
        user_query: 用户的自然语言问题，如 "查询最近7天的订单数量"
        tables: 可用表信息列表（来自 get_all_tables_async）

    Returns:
        LLM 选中的表名列表，示例: ["ods_order"]

    Note:
        - 使用 TABLE_SELECTOR_PROMPT 提示词
        - LLM 返回 JSON 格式: {"tables": [...], "reasoning": "..."}
        - 如果解析失败，降级为返回所有表名
    """
    if not tables:
        return []

    # 生成表名+备注摘要
    tables_name_comment = get_tables_name_comment(tables)

    # 构建表选择提示词
    system_prompt = TABLE_SELECTOR_PROMPT.format(
        tables_summary=tables_name_comment,
        user_query=user_query
    )

    # 调用 LLM 选择表（非流式，快速返回）
    llm = get_chat_data_llm(streaming=False, temperature=0)
    response = await llm.ainvoke([HumanMessage(content=system_prompt)])

    # 解析返回的表名列表
    content = response.content.strip()
    logger.info(f"表选择结果: {content}")

    try:
        # 尝试解析 JSON 格式（可能被 ```json 包裹）
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
            selected = result.get("tables", [])
        else:
            # 尝试直接解析 JSON
            result = json.loads(content)
            selected = result.get("tables", [])

        # 验证表名有效性（防止 LLM 幻觉）
        valid_table_names = {t.get("table_name") for t in tables}
        selected = [t for t in selected if t in valid_table_names]

        logger.info(f"选中的表: {selected}")
        return selected

    except Exception as e:
        # 解析失败时降级：返回所有表（保证功能可用）
        logger.warning(f"解析表选择结果失败: {e}，降级使用所有表")
        return [t.get("table_name") for t in tables]


# ============ Agent 工具定义 ============

from langchain_core.tools import tool

from ...schema import ExecuteSqlInput


# ============ 执行 SQL 工具 ============

@tool(args_schema=ExecuteSqlInput)
async def execute_sql_tool(
    sql: str,
    state: Annotated[dict, InjectedState],
    semantic_config_id: str | None = None
) -> str:
    """
    执行 SQL 查询并返回结果。

    当用户需要查询数据时，调用此工具执行 SQL。
    返回的 result_id 可用于后续生成图表。
    """
    logger.info(f"执行 SQL: {sql[:100]}...")

    # Human-in-the-Loop: 根据开关决定是否需要用户确认
    if SQL_APPROVAL_ENABLED:
        # interrupt() 会暂停图执行，返回给用户确认
        # 用户确认后通过 Command(resume=...) 恢复执行
        approval = interrupt({
            "args": {"sql": sql},  # 通用参数字段，不同工具有不同的 args
            "message": "请确认是否执行此 SQL 查询"
        })

        # 检查用户响应
        if isinstance(approval, dict):
            action = approval.get("action", "approve")
            if action == "reject":
                return "用户取消了 SQL 执行"
            # 如果用户修改了 SQL，使用修改后的版本
            sql = approval.get("sql", sql)

        logger.info(f"用户确认执行 SQL: {sql[:100]}...")
    else:
        logger.info(f"直接执行 SQL（审批已关闭）: {sql[:100]}...")

    # 获取数据源ID
    datasource_id = state.get("datasource_id")
    user_id = str(state.get("user_id") or "").strip() or None
    operator_name = str(state.get("operator_name") or "").strip() or None
    member_datasource_ids = _normalize_member_datasource_ids(state.get("member_datasource_ids"))
    logger.info("chatdata conversation execute sql: user_id=%s operator_name=%s datasource_id=%s member_datasource_ids=%s", user_id, operator_name, datasource_id, member_datasource_ids)
    semantic_config_id = str(semantic_config_id or state.get("semantic_config_id") or "").strip() or None
    conversation_id = str(state.get("conversation_id") or "").strip() or None
    query_text = ""
    messages = state.get("messages") or []
    if messages:
        query_text = str(getattr(messages[0], "content", "") or "").strip()
    query_result = await _execute_sql_real(sql, datasource_id, member_datasource_ids, user_id, semantic_config_id, query_text, conversation_id, operator_name=operator_name)

    if query_result["success"]:
        result_json = json.dumps(query_result, ensure_ascii=False)
        if len(result_json) > TOOL_RESULT_OFFLOAD_SIZE:
            artifact_id = save_query_result(query_result, conversation_id=conversation_id)
            result_summary = {
                "success": True,
                "artifact_id": artifact_id,
                "columns": query_result.get("columns", []),
                "total_count": query_result.get("total_count", 0),
                "execution_time_ms": query_result.get("execution_time_ms"),
                "result_size_chars": len(result_json),
                "llm_analysis_skipped": True,
                "message": "查询结果已超过限制，仅返回工具结果，不进行分析；如需二次分析请使用智能分析功能。",
            }
            if query_result.get("sql"):
                result_summary["sql"] = query_result.get("sql")
            logger.info(
                "智能问数查询结果过大，已写入 artifact: artifact_id=%s size_chars=%s rows=%s",
                artifact_id,
                len(result_json),
                query_result.get("total_count", 0),
            )
            return f"<query_result>{json.dumps(result_summary, ensure_ascii=False)}</query_result>"
        return f"<query_result>{result_json}</query_result>"
    else:
        return f"查询失败: {query_result.get('error', '未知错误')}"


def _normalize_member_datasource_ids(member_datasource_ids: Any) -> list[str]:
    """标准化成员数据源ID列表。"""
    normalized_ids: list[str] = []
    for item in member_datasource_ids or []:
        member_id = str(item or "").strip()
        if member_id and member_id not in normalized_ids:
            normalized_ids.append(member_id)
    return normalized_ids


def _build_member_query_result(base_result: dict, sql: str, member_datasource_id: str | None = None, member_name: str | None = None) -> dict:
    """构造成员执行结果。"""
    return {
        "success": bool(base_result.get("success")),
        "sql": sql,
        "columns": base_result.get("columns", []),
        "data": base_result.get("data", []),
        "total_count": base_result.get("total_count", 0),
        "execution_time_ms": base_result.get("execution_time_ms"),
        "error": base_result.get("error"),
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
    }


def _build_multi_member_query_result(sql: str, member_results: list[dict]) -> dict:
    """构造多成员查询结果。"""
    primary_result = next((item for item in member_results if item.get("success")), None)
    if primary_result is None:
        primary_result = member_results[0] if member_results else {}
    has_success = any(item.get("success") for item in member_results)
    return {
        "success": has_success,
        "sql": sql,
        "columns": primary_result.get("columns", []),
        "data": primary_result.get("data", []),
        "total_count": primary_result.get("total_count", 0),
        "execution_time_ms": primary_result.get("execution_time_ms"),
        "error": None if has_success else (primary_result.get("error") or "查询失败"),
        "member_datasource_id": primary_result.get("member_datasource_id"),
        "member_name": primary_result.get("member_name"),
        "results": member_results,
    }


async def _execute_sql_on_target(sql: str, target_datasource: dict, user_id: str | None = None, operator_name: str | None = None) -> dict:
    """在单个执行对象上执行 SQL。"""
    async def execute_query() -> dict:
        executor = get_executor(target_datasource)
        try:
            execute_kwargs = {"user_id": user_id}
            if "username" in inspect.signature(executor.execute).parameters:
                execute_kwargs["username"] = operator_name
            return await executor.execute(sql, **execute_kwargs)
        finally:
            await executor.close()

    return await run_with_sql_user_datasource_limit(user_id, target_datasource, execute_query)


async def _execute_sql_for_member(
    sql: str,
    target_datasource: dict,
    user_id: str | None = None,
    operator_name: str | None = None,
    execution_id: str | None = None,
) -> dict:
    """执行单个成员查询，并将异常转换为成员级失败结果。"""
    member_datasource_id = target_datasource.get("member_datasource_id")
    member_name = target_datasource.get("member_name")
    try:
        if execution_id:
            from ...routes import _execute_sql_for_member as _execute_route_sql_for_member
            return await _execute_route_sql_for_member(
                execution_target=target_datasource,
                sql=sql,
                limit=DEFAULT_ROW_LIMIT,
                timeout=None,
                user_id=user_id,
                username=operator_name,
                execution_id=execution_id,
            )
        target_result = await _execute_sql_on_target(sql, target_datasource, user_id, operator_name)
    except Exception as e:
        logger.exception("执行对象 SQL 查询异常: member=%s", member_datasource_id)
        target_result = {
            "success": False,
            "columns": [],
            "data": [],
            "total_count": 0,
            "error": f"执行失败: {str(e) or type(e).__name__}",
        }

    return _build_member_query_result(target_result, sql, member_datasource_id, member_name)


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
    # 检查数据源
    if not datasource_id:
        logger.error("未指定数据源ID，无法执行真实查询")
        return {
            "success": False,
            "sql": sql,
            "error": "未指定数据源ID，无法执行查询",
            "columns": [],
            "data": [],
            "total_count": 0,
        }

    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(datasource_id)

    if not datasource:
        logger.error(f"数据源不存在: {datasource_id}")
        return {
            "success": False,
            "error": f"数据源不存在: {datasource_id}",
            "columns": [],
            "data": [],
            "total_count": 0,
        }

    task_info = None
    update_query_task = None
    try:
        execution_map = {}
        get_execution_id_by_member = None
        try:
            from ...routes import (
                _create_task_for_execute_request,
                _create_query_executions_for_task,
                _get_execution_id_by_member,
                _build_persistent_query_task_result_for_request,
                _update_query_task,
            )
            from ...schema import ExecuteSqlRequest

            execute_request = ExecuteSqlRequest(
                sql=sql,
                datasource_id=datasource_id,
                member_datasource_ids=member_datasource_ids or [],
                user_id=user_id,
                conversation_id=conversation_id,
                query_text=query_text,
                semantic_config_id=semantic_config_id,
                query_type="agent",
            )
            task_info = await _create_task_for_execute_request(execute_request, query_type="agent", operator_name=operator_name)
            execution_map = await _create_query_executions_for_task(task_info["task_id"], execute_request, operator_name=operator_name)
            update_query_task = _update_query_task
            get_execution_id_by_member = _get_execution_id_by_member
            await update_query_task(task_info["task_id"], status="running", progress_text="SQL 执行中", started_time=datetime.now(), error_message=None)
        except Exception as task_err:
            logger.warning("创建智能体 SQL 查询任务失败，将直接执行查询: %s", task_err, exc_info=True)

        execution_targets = await ds_manager.get_execution_datasources_async(datasource_id, member_datasource_ids)
        member_results = await asyncio.gather(*[
            _execute_sql_for_member(
                sql,
                target,
                user_id,
                operator_name,
                get_execution_id_by_member(execution_map, target) if get_execution_id_by_member else None,
            )
            for target in execution_targets
        ])
        if len(member_results) == 1:
            final_result = member_results[0]
        else:
            final_result = _build_multi_member_query_result(sql, member_results)

        if task_info and update_query_task:
            persistent_result_payload = final_result
            try:
                persistent_result_payload = await _build_persistent_query_task_result_for_request(execute_request, final_result)
            except Exception as persist_err:
                logger.warning("构建智能体 SQL 查询任务落库结果失败，跳过结果落库: %s", persist_err, exc_info=True)
                persistent_result_payload = None
            await update_query_task(
                task_info["task_id"],
                status="succeeded" if final_result.get("success") else "failed",
                progress_text="执行完成" if final_result.get("success") else "执行失败",
                result_payload=persistent_result_payload,
                error_message=None if final_result.get("success") else final_result.get("error") or "SQL 执行失败",
                finished_time=datetime.now(),
            )
        return final_result
    except ValueError as e:
        # 不支持的数据源类型
        logger.error(f"不支持的数据源类型: {e}")
        if task_info and update_query_task:
            try:
                await update_query_task(
                    task_info["task_id"],
                    status="failed",
                    progress_text="执行失败",
                    error_message=str(e) or "SQL 执行失败",
                    finished_time=datetime.now(),
                )
            except Exception as update_err:
                logger.error("更新智能体 SQL 查询任务失败状态异常: %s", update_err, exc_info=True)
        return {
            "success": False,
            "sql": sql,
            "error": str(e),
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    except Exception as e:
        logger.error(f"执行 SQL 异常: {e}", exc_info=True)
        if task_info and update_query_task:
            try:
                await update_query_task(
                    task_info["task_id"],
                    status="failed",
                    progress_text="执行失败",
                    error_message=str(e) or "SQL 执行失败",
                    finished_time=datetime.now(),
                )
            except Exception as update_err:
                logger.error("更新智能体 SQL 查询任务失败状态异常: %s", update_err, exc_info=True)
        return {
            "success": False,
            "sql": sql,
            "error": f"执行失败: {str(e)}",
            "columns": [],
            "data": [],
            "total_count": 0,
        }


# ============ 语义查询工具 ============

# 工具名称常量
class SemanticToolNames:
    """语义工具名称常量（避免硬编码字符串）"""
    GENERATE = "dsl2sql_tool"
    VALIDATE = "validate_sql_tool"


# 从独立模块导入
from ...services.semantic_tools import create_semantic_tools

# 保持向后兼容的别名
create_execute_semantic_tool = create_semantic_tools


def build_semantic_prompt(semantic_config: dict, mode: str = "query") -> str:
    """
    构建语义配置的提示词，告诉 LLM 有哪些指标和维度可用

    注意：从独立提示词架构开始，此函数只返回配置信息部分（指标和维度列表）
    工作流程和规则已经包含在 SQL_GENERATOR_PROMPT_SEMANTIC_* 中

    Args:
        semantic_config: 语义配置
        mode: 工作模式 ("generate" 或 "query")

    Returns:
        格式化的配置信息字符串
    """
    def get_metric_type(field: dict) -> str:
        return str(
            field.get('data_type')
            or field.get('mysql_type')
            or field.get('hive_type')
            or field.get('columnType')
            or field.get('type')
            or ''
        ).strip()

    def get_dimension_type(field: dict) -> str:
        return str(
            field.get('mysql_type')
            or field.get('hive_type')
            or field.get('columnType')
            or field.get('data_type')
            or field.get('type')
            or ''
        ).strip()

    def format_prompt_line(name: str, desc: str, field_type: str, expression: str, metadata_supplement: str = "") -> str:
        parts = [f"**{name}**"]
        if desc and desc != name:
            parts.append(desc)
        if field_type:
            parts.append(f"类型: {field_type}")
        parts.append(f"表达式: {expression}")
        supplement = str(metadata_supplement or "").strip()
        if supplement:
            parts.append(f"元数据补充: {supplement}")
        return "，".join(parts)

    def semantic_table_ref(table: str, table_alias: str = "") -> str:
        table_name = str(table or "").strip()
        alias = str(table_alias or table_name).strip()
        if not table_name:
            return alias
        if not alias or alias == table_name:
            return table_name
        return f"{alias}({table_name})"

    # 配置描述（可选）
    description_section = ""
    if semantic_config.get('description'):
        description_section = f"**配置说明**: {semantic_config['description']}\n\n"

    # 业务描述（可选）
    business_description_section = ""
    if semantic_config.get('ontology_rules'):
        business_description_section = f"**业务描述**: {semantic_config['ontology_rules']}\n\n"

    # SQL 方言（帮助模型理解 SQL 语法边界）
    source_type = str(semantic_config.get('source_type') or '').strip()
    dialect_section = f"**SQL方言**: {source_type.upper()}\n\n" if source_type else ""

    # 构建指标列表
    metrics = semantic_config.get('metrics', [])
    metrics_section = ""
    if metrics:
        metrics_lines = []
        for m in metrics:
            name = m.get('name', '')
            desc = m.get('description') or m.get('label') or name
            expression = m.get('expression')
            if not expression:
                aggregation = m.get('aggregation', 'COUNT')
                field = m.get('field', '')
                expression = f"{aggregation}({field})"
            table_ref = semantic_table_ref(m.get('table', ''), m.get('table_alias', ''))
            line = format_prompt_line(name, desc, get_metric_type(m), expression, m.get('metadata_supplement'))
            if table_ref:
                line = f"{line}，表: {table_ref}"
            metrics_lines.append(f"- {line}")
        metrics_section = "### 可用指标（用于聚合统计，明细查询不要放入 metrics）\n" + "\n".join(metrics_lines) + "\n\n"

    # 构建维度列表
    dimensions = semantic_config.get('dimensions', [])
    dimensions_section = ""
    if dimensions:
        dimensions_lines = []
        for d in dimensions:
            name = d.get('name', '')
            desc = d.get('description') or d.get('label') or name
            expression = d.get('expression')
            if not expression:
                field = d.get('field')
                if field:
                    expression = field
                else:
                    table = d.get('table', '')
                    table_alias = d.get('table_alias', '')
                    column = d.get('column', '')
                    table_ref = table_alias or table
                    expression = f"{table_ref}.{column}" if table_ref and column else column or '-'
            field_type = get_dimension_type(d)
            table_ref = semantic_table_ref(d.get('table', ''), d.get('table_alias', ''))
            prompt_line = format_prompt_line(name, desc, field_type, expression, d.get('metadata_supplement'))
            if table_ref:
                prompt_line = f"{prompt_line}，表: {table_ref}"

            # ✨ 如果有预定义的可选值，展示给大模型（避免枚举值幻觉）
            allowed_values = d.get('allowed_values')
            if allowed_values:
                values_str = "、".join(str(v) for v in allowed_values)
                dimensions_lines.append(f"- {prompt_line} - 可选值: [{values_str}]")
            else:
                dimensions_lines.append(f"- {prompt_line}")

        dimensions_section = "### 可用维度（用于分组/筛选，也可用于明细字段选择）\n" + "\n".join(dimensions_lines) + "\n\n"

    # 构建表关系列表
    relationships = semantic_config.get('relationships', [])
    relationships_section = ""
    if relationships:
        relationships_lines = []
        for r in relationships:
            left_table = r.get('left_table', '')
            right_table = r.get('right_table', '')
            left_ref = semantic_table_ref(left_table, r.get('left_alias', ''))
            right_ref = semantic_table_ref(right_table, r.get('right_alias', ''))
            join_type = r.get('join_type', 'left').upper()
            rel_type = r.get('relationship_type', 'one_to_many')
            join_conditions = r.get('join_conditions') or []
            condition_parts = []
            for cond in join_conditions:
                left_col = (cond or {}).get('left_column')
                right_col = (cond or {}).get('right_column')
                if left_col and right_col:
                    condition_parts.append(f"{left_ref}.{left_col} = {right_ref}.{right_col}")
            if not condition_parts:
                continue
            relationships_lines.append(
                f"- **{left_ref}** → **{right_ref}**: {' AND '.join(condition_parts)} ({join_type} JOIN, {rel_type})"
            )
        relationships_section = "### 表关系（用于跨表查询）\n" + "\n".join(relationships_lines) + "\n\n"

    # 只注入当前指标集配置；工具参数规则由智能问数主提示词维护。
    return f"""{description_section}{business_description_section}{dialect_section}{metrics_section}{dimensions_section}{relationships_section}"""


# ============ 直接执行 SQL（无审批，用于 Dashboard 面板刷新） ============

async def execute_sql_direct(sql: str, datasource_id: str = None) -> dict:
    """
    直接执行 SQL 并返回结果（无审批流程）

    用于 Dashboard 面板数据刷新，不经过 Agent 工作流

    Args:
        sql: SQL 查询语句
        datasource_id: 数据源ID

    Returns:
        查询结果 {"success": bool, "columns": [...], "data": [...], "total_count": int}
    """
    logger.info(f"直接执行 SQL: {sql[:100]}... (datasource_id={datasource_id}, mode=真实)")
    return await _execute_sql_real(sql, datasource_id)
