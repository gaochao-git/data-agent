"""
SQL Generator 工具函数

提供表选择、DDL 生成等辅助功能，用于 Graph 执行前的预处理阶段
"""

import asyncio
import json
import os
import re
from typing import Any, Optional, Annotated

from langchain_core.messages import SystemMessage
from langgraph.prebuilt import InjectedState

from utils.logger import get_logger
from ..services.llm_config import get_chat_data_llm
from .prompts import TABLE_SELECTOR_PROMPT, SEMANTIC_TOOL_PARAMS_PROMPT
from .skills import CHART_GENERATORS
from .state import load_query_result

logger = get_logger(__name__)

# ============ SQL 执行审批开关 ============
# true = 需要用户确认才能执行 SQL，false = 直接执行（默认关闭）
SQL_APPROVAL_ENABLED = os.getenv("SQL_APPROVAL_ENABLED", "false").lower() == "true"
logger.info(f"[ChatData] SQL 执行审批: {'开启' if SQL_APPROVAL_ENABLED else '关闭'}")


def get_all_tables(datasource_id: str | None) -> list[dict[str, Any]]:
    """
    获取指定数据源的所有表元数据（表名、描述、字段结构）

    从数据源表结构缓存中读取表信息，包含完整的表结构定义。
    这是表选择流程的第一步，获取候选表的完整信息。

    Args:
        datasource_id: 数据源ID
            - 指定ID: 只获取该数据源下的表
            - None: 获取所有数据源的表（合并）

    Returns:
        表信息列表，每个元素结构如下:
        [
            {
                "table_name": "ods_order",
                "description": "订单主表",
                "columns": [
                    {"name": "order_id", "type": "INT", "description": "订单ID", "primary_key": True},
                    {"name": "customer_id", "type": "INT", "description": "客户ID"},
                    ...
                ]
            },
            ...
        ]
    """
    from ..services.datasource_manager import get_datasource_manager
    datasource_manager = get_datasource_manager()

    if datasource_id:
        datasource = datasource_manager.get_datasource(datasource_id)
        tables = datasource.get("ds_tables", []) if datasource else []
    else:
        all_datasources = datasource_manager.get_all_datasources()
        tables = []
        for ds in all_datasources:
            tables.extend(ds.get("ds_tables", []))

    return tables


def get_tables_name_comment(tables: list[dict[str, Any]]) -> str:
    """
    生成表名和备注的摘要文本（用于 LLM 表选择）

    只提取表名和备注，不包含字段详情，用于第一轮 LLM 调用。
    这样可以用很少的 token 让 LLM 判断需要哪些表。

    Args:
        tables: 表信息列表（来自 get_all_tables）

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
        tables: 全部表信息列表（来自 get_all_tables）
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
        tables: 可用表信息列表（来自 get_all_tables）

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
    response = await llm.ainvoke([SystemMessage(content=system_prompt)])

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

from ..schema import ExecuteSqlInput, GenerateChartInput, LoadSkillInput


# ============ 执行 SQL 工具 ============

@tool(args_schema=ExecuteSqlInput)
async def execute_sql_tool(
    sql: str,
    state: Annotated[dict, InjectedState]
) -> str:
    """
    执行 SQL 查询并返回结果。

    当用户需要查询数据时，调用此工具执行 SQL。
    返回的 result_id 可用于后续生成图表。
    """
    logger.info(f"执行 SQL: {sql[:100]}...")

    # Human-in-the-Loop: 根据开关决定是否需要用户确认
    if SQL_APPROVAL_ENABLED:
        from langgraph.types import interrupt

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
    member_datasource_ids = _normalize_member_datasource_ids(state.get("member_datasource_ids"))
    logger.info("chatdata conversation execute sql: user_id=%s datasource_id=%s member_datasource_ids=%s", user_id, datasource_id, member_datasource_ids)
    query_result = await _execute_sql_real(sql, datasource_id, member_datasource_ids, user_id)

    # 直接返回查询结果（简化流程，BI 分析数据量不大，无需存储中转）
    if query_result["success"]:
        import json
        # 直接嵌入 JSON 数据，前端可直接渲染
        result_json = json.dumps(query_result, ensure_ascii=False)
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


async def _execute_sql_on_target(sql: str, target_datasource: dict, user_id: str | None = None) -> dict:
    """在单个执行对象上执行 SQL。"""
    from ..services.sql_executor import get_executor

    executor = get_executor(target_datasource)
    try:
        return await executor.execute(sql, user_id=user_id)
    finally:
        await executor.close()


async def _execute_sql_for_member(sql: str, target_datasource: dict, user_id: str | None = None) -> dict:
    """执行单个成员查询，并将异常转换为成员级失败结果。"""
    member_datasource_id = target_datasource.get("member_datasource_id")
    member_name = target_datasource.get("member_name")
    try:
        target_result = await _execute_sql_on_target(sql, target_datasource, user_id)
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


async def _execute_sql_real(sql: str, datasource_id: str | None, member_datasource_ids: list[str] | None = None, user_id: str | None = None) -> dict:
    """
    真实执行模式：执行实际的数据库查询

    Args:
        sql: SQL 查询语句
        datasource_id: 数据源ID
        member_datasource_ids: 指定执行的成员数据源ID列表

    Returns:
        查询结果字典
    """
    from ..services.datasource_manager import get_datasource_manager

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
    datasource = ds_manager.get_datasource(datasource_id)

    if not datasource:
        logger.error(f"数据源不存在: {datasource_id}")
        return {
            "success": False,
            "error": f"数据源不存在: {datasource_id}",
            "columns": [],
            "data": [],
            "total_count": 0,
        }

    source_type = datasource.get("source_type")

    try:
        execution_targets = ds_manager.get_execution_datasources(datasource_id, member_datasource_ids)
        member_results = await asyncio.gather(*[_execute_sql_for_member(sql, target, user_id) for target in execution_targets])
        if len(member_results) == 1:
            final_result = member_results[0]
        else:
            final_result = _build_multi_member_query_result(sql, member_results)

        await _record_agent_sql_query_history(sql, datasource_id, member_datasource_ids, user_id, final_result)
        return final_result
    except ValueError as e:
        # 不支持的数据源类型
        logger.error(f"不支持的数据源类型: {e}")
        return {
            "success": False,
            "sql": sql,
            "error": str(e),
            "columns": [],
            "data": [],
            "total_count": 0,
        }
    except Exception as e:
        logger.error(f"执行 SQL 异常: {e}")
        return {
            "success": False,
            "sql": sql,
            "error": f"执行失败: {str(e)}",
            "columns": [],
            "data": [],
            "total_count": 0,
        }


async def _record_agent_sql_query_history(
    sql: str,
    datasource_id: str,
    member_datasource_ids: list[str] | None,
    user_id: str | None,
    result: dict,
) -> None:
    """补记智能体执行的 SQL 历史。"""
    try:
        from ..routes import _record_sql_query_history
        from ..schema import ExecuteSqlRequest

        await _record_sql_query_history(
            ExecuteSqlRequest(
                sql=sql,
                datasource_id=datasource_id,
                member_datasource_ids=member_datasource_ids or [],
                user_id=user_id,
                query_type="agent",
            ),
            result,
        )
    except Exception as err:
        logger.error("记录智能体 SQL 查询历史失败: %s", err, exc_info=True)


# ============ 生成图表工具 ============

# 图表类型及其数据要求
CHART_REQUIREMENTS = {
    "bar": {
        "name": "柱状图",
        "description": "适合展示分类数据的对比",
        "x_type": "category",
        "y_type": "number",
    },
    "line": {
        "name": "折线图",
        "description": "适合展示数据随时间/顺序的变化趋势",
        "x_type": "category",
        "y_type": "number",
    },
    "pie": {
        "name": "饼图",
        "description": "适合展示占比分布，分类数建议不超过10个",
        "x_type": "category",
        "y_type": "number",
        "max_categories": 10,
    },
}


@tool(args_schema=GenerateChartInput)
def generate_chart_tool(
    result_id: str,
    chart_type: Optional[str] = None,
    x_field: Optional[str] = None,
    y_field: Optional[str] = None,
    series_field: Optional[str] = None
) -> str:
    """
    分析数据或生成图表。

    使用方式：
    1. 只传 result_id → 返回数据分析（字段类型、样例数据），由你判断是否适合图表并推荐类型
    2. 传完整参数 → 生成图表
    3. multi_line 需要额外传 series_field 参数
    """
    logger.info(f"生成图表: result_id={result_id}, type={chart_type}, x={x_field}, y={y_field}, series={series_field}")

    # 加载查询结果
    query_result = load_query_result(result_id)
    if not query_result:
        return f"找不到查询结果（ID: {result_id[:8]}...），请先执行查询"

    columns = query_result.get("columns", [])
    data = query_result.get("data", [])
    row_count = len(data)

    # 分析字段类型
    numeric_cols = []
    category_cols = []
    for col in columns:
        sample_values = [row.get(col) for row in data[:10] if row.get(col) is not None]
        if sample_values and _is_numeric_column(sample_values):
            numeric_cols.append(col)
        else:
            category_cols.append(col)

    # 如果没传完整参数，返回数据分析让 LLM 结合 skill 判断
    if not chart_type or not x_field or not y_field:
        sample_rows = data[:3]
        sample_text = "\n".join([
            " | ".join([f"{k}={v}" for k, v in row.items()])
            for row in sample_rows
        ])

        return f"""**数据信息**

数据行数：{row_count}
样例数据（前3行）：
{sample_text}
...

请根据技能中的判断标准，判断数据是否符合图表要求。"""

    # 传了完整参数，生成图表
    # 验证字段
    if x_field not in columns:
        return f"X轴字段 '{x_field}' 不存在，可用字段：{', '.join(columns)}"
    if y_field not in columns:
        return f"Y轴字段 '{y_field}' 不存在，可用字段：{', '.join(columns)}"
    if y_field not in numeric_cols:
        return f"Y轴字段 '{y_field}' 不是数值类型，数值字段有：{', '.join(numeric_cols)}"

    # 如果传了 series_field，校验字段存在
    if series_field and series_field not in columns:
        return f"分组字段 '{series_field}' 不存在，可用字段：{', '.join(columns)}"

    # 生成图表配置
    chart_result = generate_chart_from_data(
        query_result=query_result,
        chart_type=chart_type,
        x_field=x_field,
        y_field=y_field,
        series_field=series_field,
        title=""
    )

    if chart_result.get("success"):
        # 使用 artifact_store 存储图表配置，避免大数据进入 LLM 上下文
        from utils.lang_util.artifact_store import save_artifact, ArtifactType
        chart_id = save_artifact(ArtifactType.CHART_CONFIG, chart_result["chart_config"])
        return f"<chart_config>{chart_id}</chart_config>"
    else:
        return f"图表生成失败：{chart_result.get('error', '未知错误')}"


def _is_numeric_value(v) -> bool:
    """判断单个值是否为数值"""
    if isinstance(v, (int, float)):
        return True
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def _is_numeric_column(values: list) -> bool:
    """判断列是否为数值类型"""
    for v in values[:20]:
        if v is None:
            continue
        if not isinstance(v, (int, float)):
            try:
                float(v)
            except (ValueError, TypeError):
                return False
    return True


def generate_chart_from_data(
    query_result: dict,
    chart_type: str,
    x_field: str,
    y_field: str,
    series_field: str = None,
    title: str = ""
) -> dict:
    """
    根据查询结果生成 ECharts 图表配置（确定性逻辑，无 LLM 推理）

    Args:
        query_result: 查询结果 {"columns": [...], "data": [{...}, ...], "success": bool}
        chart_type: 图表类型 bar/line/pie/multi_line
        x_field: X轴字段名
        y_field: Y轴字段名
        series_field: 分组字段名（仅 multi_line 需要）
        title: 图表标题

    Returns:
        成功: {"success": True, "chart_config": {...}}
        失败: {"success": False, "error": "错误信息"}
    """
    logger.info(f"生成图表: type={chart_type}, x={x_field}, y={y_field}, series={series_field}")

    # 1. 校验查询结果
    if not query_result:
        return {"success": False, "error": "没有可用的查询数据，请先执行查询"}

    if not query_result.get("success"):
        return {"success": False, "error": "查询结果无效"}

    columns = query_result.get("columns", [])
    data = query_result.get("data", [])

    if not columns or not data:
        return {"success": False, "error": "查询结果为空"}

    # 2. 校验字段是否存在
    if x_field not in columns:
        return {"success": False, "error": f"字段 '{x_field}' 不存在，可用字段: {columns}"}

    if y_field not in columns:
        return {"success": False, "error": f"字段 '{y_field}' 不存在，可用字段: {columns}"}

    # 3. 带 series_field 的图表 → 分组图表
    if series_field:
        if series_field not in columns:
            return {"success": False, "error": f"分组字段 '{series_field}' 不存在，可用字段: {columns}"}

        if chart_type == "line":
            from .skills import generate_multi_line_config
            chart_config = generate_multi_line_config(
                data=data,
                x_field=x_field,
                y_field=y_field,
                series_field=series_field,
                title=title
            )
            logger.info("多折线图配置生成成功")
            return {"success": True, "chart_config": chart_config}

        elif chart_type == "bar":
            from .skills import generate_multi_bar_config
            chart_config = generate_multi_bar_config(
                data=data,
                x_field=x_field,
                y_field=y_field,
                series_field=series_field,
                title=title
            )
            logger.info("分组柱状图配置生成成功")
            return {"success": True, "chart_config": chart_config}

    # 4. 从 JSON 对象数组提取数据（普通图表）
    x_data = [row.get(x_field) for row in data]
    y_data = [row.get(y_field) for row in data]

    # 4. 校验 Y 轴数据类型
    if not _is_numeric_column(y_data):
        return {"success": False, "error": f"字段 '{y_field}' 不是数值类型，无法作为Y轴数据"}

    # 转换 y_data 为数值
    y_data = [float(v) if v is not None else 0 for v in y_data]

    # 5. X 轴转为字符串
    x_data = [str(v) if v is not None else "" for v in x_data]

    # 6. 饼图基本检查（只做最基础的校验，复杂判断交给 LLM）
    if chart_type == "pie":
        unique_count = len(set(x_data))
        if unique_count > 20:
            return {
                "success": False,
                "error": f"饼图分类数({unique_count})过多，无法有效展示"
            }

    # 7. 生成图表配置
    generator = CHART_GENERATORS.get(chart_type)
    if not generator:
        return {"success": False, "error": f"不支持的图表类型: {chart_type}，支持: bar/line/pie"}

    chart_config = generator(x_data, y_data, title, x_field, y_field)
    logger.info(f"图表配置生成成功: {chart_type}")

    return {"success": True, "chart_config": chart_config}


# ============ 加载 Skill 工具 ============

@tool(args_schema=LoadSkillInput)
def load_skill_tool(skill_name: str) -> str:
    """
    加载技能的详细说明。

    当你需要了解图表类型的详细要求（适用场景、数据要求、调用方式）时，
    调用此工具加载 skill 内容。

    可用的 skill：chart-processing（包含柱状图、折线图、饼图的完整说明）
    """
    from .skills import activate_skill

    skill = activate_skill(skill_name)
    if skill:
        return skill["content"]
    else:
        return f"未找到技能: {skill_name}。可用: chart-processing"


# ============ 语义查询工具 ============

# 工具名称常量
class SemanticToolNames:
    """语义工具名称常量（避免硬编码字符串）"""
    GENERATE = "dsl2sql_tool"
    VALIDATE = "validate_sql_tool"


# 从独立模块导入
from ..services.semantic_tools import create_semantic_tools

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

    def format_prompt_line(name: str, desc: str, field_type: str, expression: str) -> str:
        parts = [f"**{name}**"]
        if desc and desc != name:
            parts.append(desc)
        if field_type:
            parts.append(f"类型: {field_type}")
        parts.append(f"表达式: {expression}")
        return "，".join(parts)

    # 配置描述（可选）
    description_section = ""
    if semantic_config.get('description'):
        description_section = f"**配置说明**: {semantic_config['description']}\n\n"

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
            metrics_lines.append(f"- {format_prompt_line(name, desc, get_metric_type(m), expression)}")
        metrics_section = "### 可用指标（可用于统计，也可用于明细字段选择）\n" + "\n".join(metrics_lines) + "\n\n"

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
                    column = d.get('column', '')
                    expression = f"{table}.{column}" if table and column else column or '-'
            field_type = get_dimension_type(d)

            # ✨ 如果有预定义的可选值，展示给大模型（避免枚举值幻觉）
            allowed_values = d.get('allowed_values')
            if allowed_values:
                values_str = "、".join(str(v) for v in allowed_values)
                dimensions_lines.append(f"- {format_prompt_line(name, desc, field_type, expression)} - 可选值: [{values_str}]")
            else:
                dimensions_lines.append(f"- {format_prompt_line(name, desc, field_type, expression)}")

        dimensions_section = "### 可用维度（用于分组/筛选，也可用于明细字段选择）\n" + "\n".join(dimensions_lines) + "\n\n"

    # 构建表关系列表
    relationships = semantic_config.get('relationships', [])
    relationships_section = ""
    if relationships:
        relationships_lines = []
        for r in relationships:
            left_table = r.get('left_table', '')
            right_table = r.get('right_table', '')
            join_type = r.get('join_type', 'left').upper()
            rel_type = r.get('relationship_type', 'one_to_many')
            join_conditions = r.get('join_conditions') or []
            condition_parts = []
            for cond in join_conditions:
                left_col = (cond or {}).get('left_column')
                right_col = (cond or {}).get('right_column')
                if left_col and right_col:
                    condition_parts.append(f"{left_table}.{left_col} = {right_table}.{right_col}")
            if not condition_parts:
                continue
            relationships_lines.append(
                f"- **{left_table}** → **{right_table}**: {' AND '.join(condition_parts)} ({join_type} JOIN, {rel_type})"
            )
        relationships_section = "### 表关系（用于跨表查询）\n" + "\n".join(relationships_lines) + "\n\n"

    # 组合配置信息（工具参数说明从 prompts.py 导入）
    return f"""{description_section}{dialect_section}{metrics_section}{dimensions_section}{relationships_section}{SEMANTIC_TOOL_PARAMS_PROMPT}"""


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


# ============ 工具列表 ============
TOOLS = [execute_sql_tool]
