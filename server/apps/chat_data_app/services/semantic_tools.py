"""
语义查询工具

提供基于语义配置的 SQL 生成和验证工具，
供 LangGraph Agent 调用。
"""

import json
from typing import List, Dict, Any, Optional, Annotated

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import InjectedState

from utils.logger import get_logger
from ..semantic.compiler import SemanticSQLGenerator
from ..schema import ExecuteSemanticInput, SelectSemanticConfigInput, ValidateSQLInput
from .datasource_manager import get_datasource_manager
from .semantic_config_selector import _summarize_semantic_config, select_semantic_config_for_query

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
        offset: Optional[int] = None,
        user_query: Optional[str] = None
    ) -> str:
        """
        生成 SQL 查询

        Returns:
            格式化的输出字符串（包含 SQL 和参数信息）
        """
        logger.debug(f"[生成SQL] metrics={metrics}, dimensions={dimensions}")
        logger.debug(f"[参数详情] filters={filters}, having={having}, order_by={order_by}, limit={limit}, offset={offset}")

        # 调用生成器
        result = self.generator.generate_sql(
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
            user_query=user_query,
        )

        if not result["success"]:
            return result["error"]

        # 如果有 user_message（config_not_support 情况）
        if result.get("user_message"):
            return result['user_message']

        # 格式化输出（semantic_params 已在思考区域展示，不重复输出）
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
        logger.debug("[验证SQL] Mock验证，直接通过: datasource_id=%s, sql=%s...", datasource_id or "-", sql[:100])

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


def extract_semantic_config_id(state: Optional[Dict]) -> Optional[str]:
    """从 state 中提取当前指标集ID。"""
    if not state:
        return None
    semantic_config_id = str(state.get("semantic_config_id") or "").strip()
    return semantic_config_id or None


async def load_runtime_semantic_config(datasource_id: str | None, semantic_config_id: str | None) -> Dict[str, Any] | None:
    """读取运行时语义配置，并补齐方言和 joins。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_config_id = str(semantic_config_id or "").strip()
    if not normalized_datasource_id or not normalized_config_id:
        return None
    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(normalized_datasource_id)
    if not datasource:
        return None
    if hasattr(ds_manager, "get_runtime_semantic_config_async"):
        config = await ds_manager.get_runtime_semantic_config_async(normalized_datasource_id, normalized_config_id)
    else:
        config = await ds_manager.get_semantic_config_async(normalized_datasource_id, normalized_config_id)
    if not config or not config.get("enabled", True):
        return None
    result = dict(config)
    if result.get("relationships") and not result.get("joins"):
        result["joins"] = result.get("relationships")
    if datasource.get("source_type"):
        result["source_type"] = datasource.get("source_type")
    if not result.get("id"):
        result["id"] = normalized_config_id
    return result


def _selection_to_tool_payload(selection: Optional[Dict[str, Any]], selected_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """将指标集选择结果转为工具返回结构。"""
    if not selection:
        return {
            "success": False,
            "selected": False,
            "selected_config_id": "",
            "selected_config_name": "",
            "coverage": "none",
            "message": "当前数据源组没有可用于智能问数的指标集，请先维护并启用指标集",
            "candidates": [],
        }
    selected_config_id = str(selection.get("selected_config_id") or "").strip()
    coverage = str(selection.get("coverage") or "").strip()
    can_select = bool(
        selected_config_id
        and coverage == "full"
        and not selection.get("ambiguous")
        and not selection.get("cross_config_required")
    )
    candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
    payload = {
        "success": True,
        "selected": can_select,
        "selected_config_id": selected_config_id if can_select else "",
        "selected_config_name": (selection.get("selected_config_name") or selected_config_id) if can_select else "",
        "coverage": coverage,
        "confidence": selection.get("confidence") or "",
        "reason": selection.get("reason") or selection.get("ambiguity_reason") or selection.get("cross_config_reason") or "",
        "matched_metrics": selection.get("matched_metrics") or [],
        "matched_dimensions": selection.get("matched_dimensions") or [],
        "ambiguous": bool(selection.get("ambiguous")),
        "cross_config_required": bool(selection.get("cross_config_required")),
        "candidates": candidates,
    }
    if can_select:
        payload["message"] = "已选择指标集，请后续调用 dsl2sql_tool 时将 selected_config_id 作为 semantic_config_id 传入"
        if selected_config:
            payload["semantic_config"] = _summarize_semantic_config(selected_config)
    elif payload["ambiguous"] or payload["cross_config_required"]:
        payload["message"] = "存在多个可用指标集或需要跨指标集，请让用户从 candidates 中选择一个指标集后再查询"
    else:
        payload["message"] = "当前指标体系不支持该问题，请说明缺少的指标、维度或业务口径"
    return payload


def create_semantic_config_selection_tools(
    model_id: str | None = None,
    include_query_tools: bool = True,
    datasource_id: str | None = None,
    semantic_config: Dict[str, Any] | None = None,
):
    """创建统一语义查询工具。"""
    bound_datasource_id = str(datasource_id or "").strip() or None
    bound_semantic_config = dict(semantic_config or {}) if semantic_config else None
    bound_semantic_config_id = str((bound_semantic_config or {}).get("id") or "").strip()

    async def resolve_semantic_config(state: Optional[Dict], semantic_config_id: str | None) -> tuple[str | None, Dict[str, Any] | None]:
        """解析本次工具调用使用的指标集配置。"""
        effective_semantic_config_id = str(semantic_config_id or extract_semantic_config_id(state) or "").strip()
        if bound_semantic_config and (not effective_semantic_config_id or effective_semantic_config_id == bound_semantic_config_id):
            return bound_semantic_config_id or effective_semantic_config_id, dict(bound_semantic_config)
        datasource_id = bound_datasource_id or extract_datasource_id(state)
        return effective_semantic_config_id, await load_runtime_semantic_config(datasource_id, effective_semantic_config_id)

    @tool(args_schema=SelectSemanticConfigInput)
    async def select_semantic_config_tool(
        query: str | None = None,
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """
        从当前数据源组的指标集中选择最适合回答用户问题的指标集。

        仅在用户提出业务数据查询、且当前会话没有选择指标集时调用。
        普通对话、概念解释、使用帮助不要调用。
        """
        datasource_id = bound_datasource_id or extract_datasource_id(state)
        user_query = str(query or extract_user_query(state) or "").strip()
        if not datasource_id:
            return "select_semantic_config_tool 执行失败: 当前会话未绑定数据源"
        if not user_query:
            return "select_semantic_config_tool 执行失败: 缺少用户问题"
        try:
            selection = await select_semantic_config_for_query(
                datasource_id=datasource_id,
                user_query=user_query,
                model_id=model_id,
            )
            selected_config_id = str((selection or {}).get("selected_config_id") or "").strip()
            selected_config = await load_runtime_semantic_config(datasource_id, selected_config_id) if selected_config_id else None
            return "[select_semantic_config_tool]\n" + json.dumps(_selection_to_tool_payload(selection, selected_config), ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("select_semantic_config_tool 执行失败: %s", exc, exc_info=True)
            return f"select_semantic_config_tool 执行失败: {exc}"

    @tool(args_schema=ExecuteSemanticInput)
    async def dsl2sql_tool(
        semantic_config_id: str = None,
        metrics: List[str] = [],
        dimensions: List[str] = [],
        filters: List[Dict] = [],
        having: List[Dict] = [],
        order_by: List[Dict] = [],
        limit: int = None,
        offset: int = None,
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """
        使用当前或指定指标集生成 SQL 查询语句（仅生成，不执行）。

        已手动选择指标集时可不传 semantic_config_id；否则必须来自 select_semantic_config_tool 返回的 selected_config_id，或来自用户明确选择的候选指标集。
        """
        effective_semantic_config_id, config = await resolve_semantic_config(state, semantic_config_id)
        if not config:
            return f"dsl2sql_tool 执行失败: 未找到可用指标集 {effective_semantic_config_id or '-'}"
        user_query = extract_user_query(state)
        return SemanticToolHandler(config).generate_sql(
            metrics, dimensions, filters, having, order_by, limit, offset, user_query
        )

    @tool(args_schema=ValidateSQLInput)
    async def validate_sql_tool(
        sql: str,
        datasource_id: str = None,
        semantic_config_id: str = None,
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """
        验证 SQL（当前为 Mock，占位保留 SQL + 数据源 + 指标集入参）。
        """
        resolved_datasource_id = str(datasource_id or extract_datasource_id(state) or "").strip() or None
        _, config = await resolve_semantic_config(state, semantic_config_id)
        handler = SemanticToolHandler(config or {"metrics": [], "dimensions": [], "relationships": []})
        return handler.validate_sql(sql, extract_user_query(state), resolved_datasource_id)

    if not include_query_tools:
        return [select_semantic_config_tool]
    return [select_semantic_config_tool, dsl2sql_tool, validate_sql_tool]
