"""
SQL Generator 工具函数

提供表选择、DDL 生成等辅助功能，用于 Graph 执行前的预处理阶段
"""

import json
import re
from typing import Any, Annotated

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from utils.logger import get_logger
from ...schema import SemanticQueryInput
from ...services.datasource_manager import get_datasource_manager
from ...services.llm_config import get_chat_data_llm
from ...services.semantic_tools import load_runtime_semantic_config
from ...services import query_runtime
from ...services.runtime_config import TOOL_RESULT_OFFLOAD_SIZE
from ...semantic.compiler import SemanticSQLGenerator
from ..state import save_query_result
from ..prompts.nl2sql import TABLE_SELECTOR_PROMPT

logger = get_logger(__name__)


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
    llm = get_chat_data_llm(streaming=False)
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

# ============ 语义查询工具 ============

def _state_semantic_config_matches(config: dict[str, Any], semantic_config_id: str | None) -> bool:
    """判断 state 中预加载的语义配置是否可用于本次工具调用。"""
    if not config:
        return False
    if not semantic_config_id:
        return True
    config_id = str(config.get("id") or "").strip()
    return not config_id or config_id == semantic_config_id


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
    """优先复用图状态里的临时配置，否则按数据源和指标集 ID 读取运行时配置。"""
    state_config = state.get("semantic_config")
    if isinstance(state_config, dict) and _state_semantic_config_matches(state_config, semantic_config_id):
        config = dict(state_config)
        if semantic_config_id and not config.get("id"):
            config["id"] = semantic_config_id
        return config
    return await load_runtime_semantic_config(datasource_id, semantic_config_id)


@tool(args_schema=SemanticQueryInput)
async def semantic_query_tool(
    dsl: dict | None = None,
    state: Annotated[dict | None, InjectedState] = None,
    sql: str | None = None,
    semantic_config_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
) -> str:
    """
    执行智能问数查询并返回结果。

    优先接收语义 DSL，内部生成 SQL 后执行；DSL 明确无法表达时接收只读 SQL 执行。
    返回的 result_id 可用于后续生成图表。
    """
    has_dsl = dsl is not None
    sql = str(sql or "").strip()
    has_sql = bool(sql)
    if has_dsl == has_sql:
        return "semantic_query_tool 执行失败: 必须且只能传入 dsl 或 sql 其中一个"
    if has_dsl and not isinstance(dsl, dict):
        return "semantic_query_tool 执行失败: dsl 必须是对象"
    if isinstance(dsl, dict) and "sql" in dsl:
        return "semantic_query_tool 执行失败: sql 必须放在顶层 sql 参数中，不要放入 dsl"
    state = state or {}

    datasource_id = state.get("datasource_id")
    state_config = state.get("semantic_config")
    state_config_id = str((state_config or {}).get("id") or "").strip() if isinstance(state_config, dict) else ""
    semantic_config_id = str(semantic_config_id or state.get("semantic_config_id") or state_config_id or "").strip() or None
    if not semantic_config_id and not isinstance(state_config, dict):
        return "semantic_query_tool 执行失败: 缺少指标集ID，请先调用 select_semantic_config_tool 或手动选择指标集"

    config = await _resolve_semantic_query_config(state, datasource_id, semantic_config_id)
    if not config:
        return f"semantic_query_tool 执行失败: 未找到可用指标集 {semantic_config_id or '-'}"

    query_text = _latest_human_query(state.get("messages") or [])

    query_mode = "nl2sql" if has_sql else "dsl2sql"
    confidence = 0.6 if has_sql else 0.9
    if has_dsl:
        compile_result = SemanticSQLGenerator(config).generate_sql(
            metrics=dsl.get("metrics") or [],
            dimensions=dsl.get("dimensions") or [],
            filters=dsl.get("filters") or [],
            having=dsl.get("having") or [],
            order_by=dsl.get("order_by") or [],
            limit=dsl.get("limit"),
            offset=dsl.get("offset"),
            user_query=query_text or None,
        )
        if not compile_result.get("success"):
            return f"semantic_query_tool 执行失败: {compile_result.get('error') or 'DSL2SQL 编译失败'}"
        sql = str(compile_result.get("sql") or "").strip()
        if not sql:
            return "semantic_query_tool 执行失败: DSL2SQL 未生成 SQL"

    logger.info(f"执行语义查询 SQL: {sql[:100]}...")

    # 获取数据源ID
    user_id = str(state.get("user_id") or "").strip() or None
    operator_name = str(state.get("operator_name") or "").strip() or None
    requested_member_datasource_ids = _normalize_member_datasource_ids(member_datasource_ids)
    state_member_datasource_ids = _normalize_member_datasource_ids(state.get("member_datasource_ids"))
    available_member_datasource_ids = _normalize_member_datasource_ids(state.get("available_member_datasource_ids"))
    matched_member_datasource_ids = _normalize_member_datasource_ids(state.get("matched_member_datasource_ids"))
    allowed_member_datasource_ids = state_member_datasource_ids or available_member_datasource_ids
    if state.get("require_explicit_member_selection") and not allowed_member_datasource_ids:
        return "semantic_query_tool 执行失败: 本次请求没有可用执行对象，请先选择有权限的数据源后再查询"
    if requested_member_datasource_ids:
        if matched_member_datasource_ids:
            invalid_matched_member_ids = [
                member_id
                for member_id in requested_member_datasource_ids
                if member_id not in matched_member_datasource_ids
            ]
            if invalid_matched_member_ids:
                return (
                    "semantic_query_tool 执行失败: 本次查询的执行对象与用户问题中指定的机构不一致；"
                    f"请只使用用户问题精确匹配到的 member_datasource_ids: {', '.join(matched_member_datasource_ids)}"
                )
        if allowed_member_datasource_ids:
            invalid_member_ids = [
                member_id
                for member_id in requested_member_datasource_ids
                if member_id not in allowed_member_datasource_ids
            ]
            if invalid_member_ids:
                return f"semantic_query_tool 执行失败: 本次查询包含不可用的执行对象 {', '.join(invalid_member_ids)}；请只使用可用执行对象中的 member_datasource_ids"
        effective_member_datasource_ids = requested_member_datasource_ids
    elif state.get("require_explicit_member_selection") and len(allowed_member_datasource_ids) > 1:
        return "semantic_query_tool 执行失败: 多个可用执行对象时必须传入本次要查询的 member_datasource_ids；如果用户未明确机构范围，请先确认查询哪个机构或是否查询全部"
    else:
        effective_member_datasource_ids = state_member_datasource_ids
    if (
        state.get("require_explicit_member_selection")
        and len(available_member_datasource_ids) > 1
        and len(effective_member_datasource_ids) == 1
        and not matched_member_datasource_ids
        and effective_member_datasource_ids[0] == available_member_datasource_ids[0]
    ):
        query_result_default_member_note = "用户未指定机构，本次默认查询第 1 个机构；最终回答需要说明该默认机构范围。"
    else:
        query_result_default_member_note = ""
    logger.info(
        "chatdata conversation execute sql: user_id=%s operator_name=%s datasource_id=%s member_datasource_ids=%s",
        user_id,
        operator_name,
        datasource_id,
        effective_member_datasource_ids,
    )
    conversation_id = str(state.get("conversation_id") or "").strip() or None
    query_result = await _execute_sql_real(sql, datasource_id, effective_member_datasource_ids, user_id, semantic_config_id, query_text, conversation_id, operator_name=operator_name)
    query_result["sql"] = query_result.get("sql") or sql
    query_result["query_mode"] = query_mode
    query_result["confidence"] = confidence
    if query_result_default_member_note:
        query_result["default_member_notice"] = query_result_default_member_note

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
                "query_mode": query_mode,
                "confidence": confidence,
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
    has_running = any(str(item.get("status") or "").strip().lower() in {"pending", "running"} for item in member_results)
    return {
        "success": has_success and not has_running,
        "sql": sql,
        "columns": primary_result.get("columns", []),
        "data": primary_result.get("data", []),
        "total_count": primary_result.get("total_count", 0),
        "execution_time_ms": primary_result.get("execution_time_ms"),
        "error": "部分执行对象仍在查询中" if has_running else (None if has_success else (primary_result.get("error") or "查询失败")),
        "member_datasource_id": primary_result.get("member_datasource_id"),
        "member_name": primary_result.get("member_name"),
        "results": member_results,
    }


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


# ============ 语义查询工具 ============

# 工具名称常量
class SemanticToolNames:
    """语义工具名称常量（避免硬编码字符串）"""
    GENERATE = "dsl2sql_tool"


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

    def collect_semantic_table_names() -> set[str]:
        table_names: set[str] = set()
        for item in [*(semantic_config.get("metrics") or []), *(semantic_config.get("dimensions") or [])]:
            table_name = str((item or {}).get("table") or "").strip()
            if table_name:
                table_names.add(table_name)
        for relationship in semantic_config.get("relationships") or []:
            left_table = str((relationship or {}).get("left_table") or "").strip()
            right_table = str((relationship or {}).get("right_table") or "").strip()
            if left_table:
                table_names.add(left_table)
            if right_table:
                table_names.add(right_table)
        return table_names

    def column_name(column: dict) -> str:
        return str(column.get("name") or column.get("column_name") or "").strip()

    def column_type(column: dict) -> str:
        return str(
            column.get("type")
            or column.get("mysql_type")
            or column.get("hive_type")
            or column.get("data_type")
            or column.get("columnType")
            or ""
        ).strip()

    def column_description(column: dict) -> str:
        return str(column.get("description") or column.get("comment") or "").strip()

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

        dimensions_section = "### 可用维度/字段（可用于返回、筛选、分组）\n" + "\n".join(dimensions_lines) + "\n\n"

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
        metrics_section = "### 可用指标（仅用于明确聚合口径）\n" + "\n".join(metrics_lines) + "\n\n"

    # 构建当前指标集相关表和原始字段列表，供 NL2SQL 回退理解表名、字段名和字段备注。
    tables = semantic_config.get('tables') or []
    table_names = collect_semantic_table_names()
    tables_section = ""
    if tables and table_names:
        table_lines = []
        for table in tables:
            table_name = str((table or {}).get("table_name") or "").strip()
            if not table_name or table_name not in table_names:
                continue
            table_desc = str((table or {}).get("description") or "").strip()
            header = f"- **{table_name}**"
            if table_desc:
                header = f"{header}：{table_desc}"
            table_lines.append(header)
            for column in table.get("columns") or []:
                name = column_name(column)
                if not name:
                    continue
                parts = [name]
                col_type = column_type(column)
                col_desc = column_description(column)
                if col_type:
                    parts.append(f"类型: {col_type}")
                if col_desc:
                    parts.append(f"备注: {col_desc}")
                table_lines.append(f"  - {'，'.join(parts)}")
        if table_lines:
            tables_section = "### 相关表和原始字段（用于补充字段理解和 SQL 回退）\n" + "\n".join(table_lines) + "\n\n"

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
    return f"""{description_section}{business_description_section}{dialect_section}{dimensions_section}{tables_section}{relationships_section}{metrics_section}"""


# ============ 直接执行 SQL（用于 Dashboard 面板刷新） ============

async def execute_sql_direct(sql: str, datasource_id: str = None) -> dict:
    """
    直接执行 SQL 并返回结果。

    用于 Dashboard 面板数据刷新，不经过 Agent 工作流

    Args:
        sql: SQL 查询语句
        datasource_id: 数据源ID

    Returns:
        查询结果 {"success": bool, "columns": [...], "data": [...], "total_count": int}
    """
    logger.info(f"直接执行 SQL: {sql[:100]}... (datasource_id={datasource_id}, mode=真实)")
    return await _execute_sql_real(sql, datasource_id)
