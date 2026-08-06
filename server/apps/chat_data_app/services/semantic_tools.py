"""
语义查询工具

提供基于语义配置的 SQL 生成工具，
供 LangGraph Agent 调用。
"""

from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from .datasource_manager import get_datasource_manager

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
    """从未绑定工具上下文的 state 中提取数据源组说明。"""
    if not state:
        return ""
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
    """构造面向模型的指标集目录项，只包含模型选择信息。"""
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


def _normalize_directory_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a lightweight DAO directory item without adding a YAML path."""
    normalized = dict(item)
    normalized.pop("path", None)
    return normalized


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
            "id": _prompt_text(item.get("id") or item.get("semantic_config_id")),
            "name": name,
            "description": _prompt_text(item.get("description")) or "未填写",
            "metrics_total": _safe_count(item.get("metrics_total")),
            "dimensions_total": _safe_count(item.get("dimensions_total")),
        })
    if not normalized_items:
        return ""

    lines = [
        "## 当前可见语义模型目录",
        "以下是当前数据源组下可见的启用语义模型轻量目录，只包含 ID、名称、描述、指标数量和维度数量；用于选择模型，不代表已加载完整指标/维度口径。需要具体指标、维度或业务口径时，先调用 search_dataset，工具会返回选中模型的实际语义文件路径，再通过 file_read/file_grep 读取。",
    ]
    for index, item in enumerate(normalized_items, start=1):
        lines.append(f"{index}. ID：{item['id'] or '未填写'}")
        lines.append(f"   名称：{item['name']}")
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
    if hasattr(ds_manager, "get_semantic_config_directory_async"):
        directory = await ds_manager.get_semantic_config_directory_async(
            normalized_datasource_id,
            normalized_user_id,
        )
        return [
            _normalize_directory_item(item)
            for item in directory or []
            if isinstance(item, dict)
            and str(item.get("id") or item.get("semantic_config_id") or "").strip()
            and bool(item.get("enabled", True))
        ]
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
            "已返回相关语义模型的轻量引用。必须使用每项 path 对应的语义 YAML，通过 file_grep/file_read 按需读取真实指标、维度、关系和口径；"
            "在完成必要读取前，不得生成 DSL 或猜测字段。每次 dsl2sql_tool 或 semantic_query_tool 只能传入一个 semantic_config_id。"
            if returned_ids else
            "未检索到相关指标集。请根据用户问题说明当前指标体系的限制，或向用户澄清业务范围。"
        ),
    }


def _semantic_config_directory_to_tool_payload(directory: List[Dict[str, Any]]) -> Dict[str, Any]:
    """将指标集目录转为工具返回结构。"""
    normalized_directory = []
    for item in directory or []:
        if not isinstance(item, dict):
            continue
        normalized_item = dict(item)
        normalized_item.pop("path", None)
        normalized_directory.append(normalized_item)
    return {
        "success": True,
        "directory": True,
        "semantic_config_ids": [],
        "semantic_config_directory": normalized_directory,
        "message": "当前可见语义模型目录已返回；请基于 semantic_config_directory 用自然语言列出名称、描述和指标/维度数量，不要编造不存在的语义模型。需要读取具体定义时，先选择模型后再次调用 search_dataset。",
    }
