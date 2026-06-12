"""
语义查询工具

提供基于语义配置的 SQL 生成和验证工具，
供 LangGraph Agent 调用。
"""

from typing import List, Dict, Any, Optional, Annotated

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import InjectedState

from utils.logger import get_logger
from .semantic_sql_generator import SemanticSQLGenerator
from ..schema import ExecuteSemanticInput, ValidateSQLInput

logger = get_logger(__name__)


# ============ 工具处理器 ============

class SemanticToolHandler:
    """
    语义工具处理器

    封装 SQL 生成和验证的核心逻辑，供工具函数调用。
    """

    def __init__(self, semantic_config: Dict[str, Any]):
        """
        初始化处理器

        Args:
            semantic_config: 语义配置，包含 metrics, dimensions, relationships
        """
        self.semantic_config = semantic_config
        self.generator = SemanticSQLGenerator(semantic_config)

    def generate_sql(
        self,
        metrics: List[str],
        dimensions: List[str],
        filters: List[Dict],
        having: List[Dict],
        order_by: List[Dict],
        limit: Optional[int],
        user_query: Optional[str],
        compare: Optional[Dict[str, Any]] = None,
        visualization: Optional[Dict] = None
    ) -> str:
        """
        生成 SQL 查询

        Returns:
            格式化的输出字符串（包含 SQL 和参数信息）
        """
        logger.info(f"[生成SQL] metrics={metrics}, dimensions={dimensions}")
        logger.debug(f"[参数详情] filters={filters}, having={having}, order_by={order_by}, limit={limit}, visualization={visualization}")

        # 调用生成器
        result = self.generator.generate_sql(
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            having=having,
            order_by=order_by,
            limit=limit,
            user_query=user_query,
            compare=compare,
        )

        if not result["success"]:
            return result["error"]

        # 如果有 user_message（config_not_support 情况）
        if result.get("user_message"):
            return result['user_message']

        # 格式化输出（semantic_params 已在思考区域展示，不重复输出）
        # visualization 已在工具参数中，前端直接从 args 获取，无需在结果中重复输出
        output = f"""```sql
{result['sql']}
```"""

        return output

    def validate_sql(self, sql: str, user_query: Optional[str], datasource_id: Optional[str] = None) -> str:
        """
        验证 SQL 是否符合用户意图

        当前为 Mock 实现，仅保留 SQL + 数据源 入参形态，
        后续可基于 datasource_id 扩展为 EXPLAIN / 执行计划校验。

        Returns:
            验证结果字符串
        """
        logger.info("[验证SQL] Mock验证，直接通过: datasource_id=%s, sql=%s...", datasource_id or "-", sql[:100])

        # Mock: 直接返回成功
        # TODO: 后续基于 datasource_id 实现数据库连接验证
        # - EXPLAIN 验证语法
        # - 检查执行计划（全表扫描警告等）
        return "✓ SQL验证通过"


# ============ 工具辅助函数 ============

def extract_user_query(state: Optional[Dict]) -> Optional[str]:
    """从 state 中提取用户原始查询"""
    if not state or 'messages' not in state:
        return None

    for msg in reversed(state['messages']):
        if isinstance(msg, HumanMessage):
            return msg.content
    return None


def extract_datasource_id(state: Optional[Dict]) -> Optional[str]:
    """从 state 中提取当前数据源ID。"""
    if not state:
        return None
    datasource_id = str(state.get("datasource_id") or "").strip()
    return datasource_id or None


# ============ 工具工厂函数 ============

def create_semantic_tools(semantic_config: Dict[str, Any]):
    """
    创建语义查询工具

    Args:
        semantic_config: 语义配置

    Returns:
        [dsl2sql_tool, validate_sql_tool]
    """
    handler = SemanticToolHandler(semantic_config)

    @tool(args_schema=ExecuteSemanticInput)
    def dsl2sql_tool(
        metrics: List[str] = [],
        dimensions: List[str] = [],
        filters: List[Dict] = [],
        having: List[Dict] = [],
        order_by: List[Dict] = [],
        limit: int = None,
        compare: Dict = None,
        visualization: Dict = None,
        state: Annotated[dict, InjectedState] = None
    ) -> str:
        """
        根据语义配置生成SQL查询语句（仅生成，不执行）。

        **重要**：当用户查询统计分析类问题时，优先调用此工具。

        **参数说明：**
        - metrics: 可从可用指标或可用维度（原始字段）中选择（建议必填）
        - dimensions: 分组维度（可选）
        - filters: WHERE 筛选条件
        - having: HAVING 筛选条件
        - order_by: 排序条件
        - limit: 限制返回条数
        - compare: 同比/环比配置
        - visualization: 图表推荐配置
        """
        user_query = extract_user_query(state)
        return handler.generate_sql(
            metrics, dimensions, filters, having, order_by, limit, user_query, compare, visualization
        )

    @tool(args_schema=ValidateSQLInput)
    def validate_sql_tool(
        sql: str,
        datasource_id: str = None,
        state: Annotated[dict, InjectedState] = None
    ) -> str:
        """
        验证 SQL（当前为 Mock，占位保留 SQL + 数据源 入参，后续可扩展为 EXPLAIN）。

        **使用场景：** 在生成SQL后调用，确认SQL可执行。
        """
        user_query = extract_user_query(state)
        resolved_datasource_id = str(datasource_id or extract_datasource_id(state) or "").strip() or None
        return handler.validate_sql(sql, user_query, resolved_datasource_id)

    return [dsl2sql_tool, validate_sql_tool]
