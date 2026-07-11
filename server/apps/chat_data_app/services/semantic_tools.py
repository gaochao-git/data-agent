"""
语义查询工具

提供基于语义配置的 SQL 生成工具，
供 LangGraph Agent 调用。
"""

import json
from typing import List, Dict, Any, Optional, Annotated

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import InjectedState

from utils.logger import get_logger
from .semantic_sql_generator import SemanticSQLGenerator
from ..schema import ExecuteSemanticInput, SelectSemanticConfigInput
from .datasource_manager import get_datasource_manager
from .semantic_config_selector import select_semantic_config_for_query
from .context_budget import ToolResultBudget, estimate_text_tokens

logger = get_logger(__name__)


# ============ 工具处理器 ============

class SemanticToolHandler:
    """
    语义工具处理器

    封装 SQL 生成核心逻辑，供工具函数调用。
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
    execution_context = state.get("execution_context")
    if isinstance(execution_context, dict):
        datasource_id = str(execution_context.get("datasource_id") or "").strip()
        if datasource_id:
            return datasource_id
    datasource_id = str(state.get("datasource_id") or "").strip()
    return datasource_id or None


def extract_datasource_group_context(state: Optional[Dict]) -> str:
    """从 state 中提取当前数据源组智能问数说明。"""
    if not state:
        return ""
    prompt_context = state.get("prompt_context")
    if isinstance(prompt_context, dict):
        context = str(prompt_context.get("datasource_group_context") or "").strip()
        if context:
            return context
    return str(state.get("datasource_group_context") or "").strip()


def _is_semantic_config_directory_query(query: str) -> bool:
    """判断用户是否在询问当前可见指标集目录，而不是要选择一个指标集查数。"""
    text = str(query or "").strip()
    if not text:
        return False
    dataset_terms = ("指标集", "数据集", "语义配置", "可查范围", "查询范围")
    view_terms = ("有哪些", "有那些", "能看到", "可以看到", "可用", "当前", "列表", "列出", "都有什么", "都有哪些")
    if not any(term in text for term in dataset_terms):
        return False
    return any(term in text for term in view_terms)


def _semantic_config_directory_item(config: Dict[str, Any]) -> Dict[str, Any]:
    """构造面向模型的指标集目录项，只包含名称、描述和规模。"""
    config_id = str(config.get("id") or config.get("semantic_config_id") or "").strip()
    metrics = config.get("metrics") if isinstance(config.get("metrics"), list) else []
    dimensions = config.get("dimensions") if isinstance(config.get("dimensions"), list) else []
    return {
        "id": config_id,
        "name": str(config.get("name") or config.get("semantic_config_name") or config_id).strip(),
        "description": str(config.get("description") or config.get("semantic_config_desc") or "").strip(),
        "metrics_total": len(metrics),
        "dimensions_total": len(dimensions),
    }


def _prompt_text(value: Any) -> str:
    """标准化注入提示词的目录文本，不裁剪后台维护内容。"""
    return str(value or "").strip()


def _safe_count(value: Any) -> int:
    """读取目录数量字段，避免脏数据影响提示词构造。"""
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(count, 0)


def format_semantic_config_directory_for_prompt(directory: List[Dict[str, Any]]) -> str:
    """将当前可见指标集轻量目录格式化为 system prompt 文本。"""
    normalized_items = []
    for item in directory or []:
        if not isinstance(item, dict):
            continue
        name = _prompt_text(item.get("name"))
        if not name:
            continue
        normalized_items.append({
            "name": name,
            "description": _prompt_text(item.get("description")) or "未填写",
            "metrics_total": _safe_count(item.get("metrics_total")),
            "dimensions_total": _safe_count(item.get("dimensions_total")),
        })
    if not normalized_items:
        return ""

    lines = [
        "## 当前可见指标集目录",
        "以下是当前数据源组下可见的启用指标集轻量目录，只包含名称、描述、指标数量和维度数量；用于回答可见范围或业务域介绍，不代表已加载完整指标/维度口径。",
    ]
    for index, item in enumerate(normalized_items, start=1):
        lines.append(f"{index}. 名称：{item['name']}")
        lines.append(f"   描述：{item['description']}")
        lines.append(f"   指标数：{item['metrics_total']}；维度数：{item['dimensions_total']}")
    return "\n".join(lines)


async def list_runtime_semantic_config_directory(datasource_id: str | None, user_id: str | None = None) -> List[Dict[str, Any]]:
    """读取当前数据源组下所有启用指标集的轻量目录。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_datasource_id:
        return []
    ds_manager = get_datasource_manager()
    if hasattr(ds_manager, "get_runtime_semantic_configs_async"):
        configs = await ds_manager.get_runtime_semantic_configs_async(normalized_datasource_id, normalized_user_id)
    else:
        configs = await ds_manager.get_semantic_configs_async(normalized_datasource_id, normalized_user_id)
    directory = [
        _semantic_config_directory_item(config)
        for config in configs or []
        if str((config or {}).get("id") or (config or {}).get("semantic_config_id") or "").strip()
        and bool((config or {}).get("enabled", True))
    ]
    return directory


async def load_runtime_semantic_config(datasource_id: str | None, semantic_config_id: str | None, user_id: str | None = None) -> Dict[str, Any] | None:
    """读取运行时语义配置，并补齐方言和 joins。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_config_id = str(semantic_config_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_datasource_id or not normalized_config_id:
        return None
    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(normalized_datasource_id)
    if not datasource:
        return None
    if hasattr(ds_manager, "get_runtime_semantic_config_async"):
        config = (
            await ds_manager.get_runtime_semantic_config_async(normalized_datasource_id, normalized_config_id, normalized_user_id)
            if normalized_user_id
            else await ds_manager.get_runtime_semantic_config_async(normalized_datasource_id, normalized_config_id)
        )
    else:
        config = (
            await ds_manager.get_semantic_config_async(normalized_datasource_id, normalized_config_id, normalized_user_id)
            if normalized_user_id
            else await ds_manager.get_semantic_config_async(normalized_datasource_id, normalized_config_id)
        )
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


async def _load_runtime_semantic_config_for_user(
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_id: str | None,
) -> Dict[str, Any] | None:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return await load_runtime_semantic_config(datasource_id, semantic_config_id)
    try:
        return await load_runtime_semantic_config(datasource_id, semantic_config_id, user_id=normalized_user_id)
    except TypeError as exc:
        if "user_id" not in str(exc):
            raise
        return await load_runtime_semantic_config(datasource_id, semantic_config_id)


def _selection_to_tool_payload(selection: Optional[Dict[str, Any]], semantic_configs: Dict[str, Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """将指标集检索结果转为主模型可消费的工具返回结构。"""
    if not selection:
        return {
            "success": False,
            "semantic_config_ids": [],
            "semantic_configs": {},
            "message": "当前数据源组没有可用于智能问数的指标集，请先维护并启用指标集",
        }
    semantic_config_ids = [
        str(config_id).strip()
        for config_id in selection.get("semantic_config_ids") or []
        if str(config_id).strip()
    ]
    summaries = semantic_configs or {}
    returned_ids = [config_id for config_id in semantic_config_ids if config_id in summaries]
    return {
        "success": True,
        "semantic_config_ids": returned_ids,
        "semantic_configs": {config_id: summaries[config_id] for config_id in returned_ids},
        "message": (
            "已返回相关指标集及完整运行时语义配置。请根据用户问题自行决定后续查询、澄清或回答；"
            "每次 dsl2sql_tool 或 semantic_query_tool 只能传入一个 semantic_config_id。"
            if returned_ids else
            "未检索到相关指标集。请根据用户问题说明当前指标体系的限制，或向用户澄清业务范围。"
        ),
    }


def _serialize_selection_tool_payload(payload: Dict[str, Any]) -> str:
    """序列化最终工具结果，作为上下文预算的唯一计量对象。"""
    return "[select_semantic_config_tool]\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _semantic_config_directory_to_tool_payload(directory: List[Dict[str, Any]]) -> Dict[str, Any]:
    """将指标集目录转为工具返回结构。"""
    return {
        "success": True,
        "directory": True,
        "semantic_config_ids": [],
        "semantic_config_directory": directory,
        "message": "当前可见指标集目录已返回；请基于 semantic_config_directory 用自然语言列出名称、描述和指标/维度数量，不要编造不存在的指标集。",
    }


def create_semantic_config_selection_tools(
    model_id: str | None = None,
    include_query_tools: bool = True,
    datasource_id: str | None = None,
    semantic_config: Dict[str, Any] | None = None,
    user_id: str | None = None,
    tool_result_budget: ToolResultBudget | None = None,
):
    """创建统一语义查询工具。"""
    bound_datasource_id = str(datasource_id or "").strip() or None
    bound_user_id = str(user_id or "").strip()
    bound_semantic_config = dict(semantic_config or {}) if semantic_config else None
    bound_semantic_config_id = str((bound_semantic_config or {}).get("id") or "").strip()

    async def resolve_semantic_config(semantic_config_id: str | None) -> tuple[str | None, Dict[str, Any] | None]:
        """按工具调用中明确指定的指标集 ID 读取运行时配置。"""
        effective_semantic_config_id = str(semantic_config_id or "").strip() or None
        if not effective_semantic_config_id:
            return None, None
        if bound_semantic_config:
            if effective_semantic_config_id != bound_semantic_config_id:
                return None, None
            return effective_semantic_config_id, dict(bound_semantic_config)
        datasource_id = bound_datasource_id
        return effective_semantic_config_id, await _load_runtime_semantic_config_for_user(datasource_id, effective_semantic_config_id, bound_user_id)

    @tool(args_schema=SelectSemanticConfigInput)
    async def select_semantic_config_tool(
        query: str | None = None,
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """
        从当前数据源组的指标集中选择最适合回答用户问题的指标集。

        仅在用户提出业务数据查询、且当前会话没有选择指标集时调用。
        如已加载只读业务知识技能的路由 reference，query 可包含原问题和简短业务限定。
        普通对话、概念解释、使用帮助不要调用。
        """
        datasource_id = bound_datasource_id or extract_datasource_id(state)
        user_query = str(query or extract_user_query(state) or "").strip()
        if not datasource_id:
            return "select_semantic_config_tool 执行失败: 当前会话未绑定数据源"
        if not user_query:
            return "select_semantic_config_tool 执行失败: 缺少用户问题"
        try:
            if _is_semantic_config_directory_query(user_query):
                directory = await list_runtime_semantic_config_directory(datasource_id, bound_user_id)
                payload = _semantic_config_directory_to_tool_payload(directory)
                return _serialize_selection_tool_payload(payload)
            selection = await select_semantic_config_for_query(
                datasource_id=datasource_id,
                user_query=user_query,
                model_id=model_id,
                user_id=bound_user_id,
                datasource_group_context=extract_datasource_group_context(state),
            )
            selected_config_ids = [
                str(config_id).strip()
                for config_id in (selection or {}).get("semantic_config_ids") or []
                if str(config_id).strip()
            ]
            semantic_configs: Dict[str, Dict[str, Any]] = {}
            omitted_config_ids: list[str] = []
            first_config_exceeds_budget = False
            remaining_tokens = (
                tool_result_budget.remaining_for_tool_result(list((state or {}).get("messages") or []))
                if tool_result_budget is not None
                else None
            )
            for config_id in selected_config_ids:
                config = await _load_runtime_semantic_config_for_user(datasource_id, config_id, bound_user_id)
                if not config:
                    omitted_config_ids.append(config_id)
                    continue
                candidate_configs = {**semantic_configs, config_id: config}
                candidate_payload = _selection_to_tool_payload(selection, candidate_configs)
                candidate_content = _serialize_selection_tool_payload(candidate_payload)
                if remaining_tokens is not None and estimate_text_tokens(candidate_content) > remaining_tokens:
                    omitted_config_ids.append(config_id)
                    # 相关性最高的完整指标集都不能装入时，后续低相关配置不能替代它。
                    first_config_exceeds_budget = not semantic_configs
                    if first_config_exceeds_budget:
                        break
                    continue
                # 运行时指标集必须完整返回，不能为了上下文预算截断指标、维度或关系。
                semantic_configs[config_id] = config
            payload = _selection_to_tool_payload(selection, semantic_configs)
            content = _serialize_selection_tool_payload(payload)
            if first_config_exceeds_budget or (selected_config_ids and not semantic_configs and remaining_tokens is not None):
                payload = {
                    "success": False,
                    "semantic_config_ids": [],
                    "semantic_configs": {},
                    "message": "相关指标集的运行时配置超过当前模型可用上下文，请缩小指标集或手动选择指标集后重试。",
                }
                content = _serialize_selection_tool_payload(payload)
            if remaining_tokens is not None:
                logger.info(
                    "指标集运行时配置上下文预算: model=%s input_budget=%s static_tokens=%s message_tokens=%s available_tokens=%s candidates=%s kept=%s omitted=%s",
                    model_id or "-",
                    tool_result_budget.budget.input_budget_tokens,
                    tool_result_budget.static_input_tokens,
                    tool_result_budget.budget.input_budget_tokens - tool_result_budget.static_input_tokens - remaining_tokens,
                    remaining_tokens,
                    len(selected_config_ids),
                    len(semantic_configs),
                    omitted_config_ids,
                )
            return content
        except Exception as exc:
            logger.error("select_semantic_config_tool 执行失败: %s", exc, exc_info=True)
            return f"select_semantic_config_tool 执行失败: {exc}"

    @tool(args_schema=ExecuteSemanticInput)
    async def dsl2sql_tool(
        semantic_config_id: str,
        dsl: Dict[str, Any] = None,
        state: Annotated[dict, InjectedState] = None,
    ) -> str:
        """
        使用当前或指定指标集生成 SQL 查询语句（仅生成，不执行）。

        每次调用都必须显式传入本次使用的 semantic_config_id。
        """
        _ = state
        effective_semantic_config_id, config = await resolve_semantic_config(semantic_config_id)
        if not config:
            return f"dsl2sql_tool 执行失败: 未找到可用指标集 {effective_semantic_config_id or '-'}"
        if not isinstance(dsl, dict):
            return "dsl2sql_tool 执行失败: dsl 必须是对象"
        user_query = extract_user_query(state)
        return SemanticToolHandler(config).generate_sql(
            dsl.get("metrics") or [],
            dsl.get("dimensions") or [],
            dsl.get("filters") or [],
            dsl.get("having") or [],
            dsl.get("order_by") or [],
            dsl.get("limit"),
            dsl.get("offset"),
            user_query,
        )

    if bound_semantic_config:
        return [dsl2sql_tool] if include_query_tools else []
    if not include_query_tools:
        return [select_semantic_config_tool]
    return [select_semantic_config_tool, dsl2sql_tool]
