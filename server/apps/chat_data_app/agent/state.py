"""
SQL Generator 状态定义
"""

from typing import Annotated, Literal, Any
from typing_extensions import NotRequired, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from utils.lang_util.artifact_store import save_artifact, load_artifact, ArtifactType


def save_query_result(data: dict, conversation_id: str | None = None) -> str:
    """保存查询结果，返回结果ID（使用公共 artifact_store）"""
    return save_artifact(ArtifactType.QUERY_RESULT, data, conversation_id=conversation_id)


def load_query_result(result_id: str) -> dict | None:
    """根据ID加载查询结果（使用公共 artifact_store）"""
    return load_artifact(ArtifactType.QUERY_RESULT, result_id)


def merge_tables_ddl(existing: str | None, new: str | None) -> str:
    """
    合并表 DDL 信息的 reducer

    - 如果 new 为空，保持 existing
    - 如果 existing 为空，使用 new
    - 如果都有值，追加合并
    """
    if not new:
        return existing or ""
    if not existing:
        return new
    # 合并：existing + 新增的部分
    return existing + "\n\n" + new


def merge_table_names(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """
    合并已加载表名列表的 reducer

    - 使用集合去重后转为列表
    """
    existing_set = set(existing or [])
    new_set = set(new or [])
    return list(existing_set | new_set)


def merge_query_results(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """
    合并查询结果索引的 reducer

    - 追加新结果到列表
    - 按 ID 去重，保留最新的
    - 最多保留 10 条历史结果
    """
    existing = existing or []
    new = new or []

    # 用字典按 ID 去重，新的覆盖旧的
    result_map = {item["id"]: item for item in existing}
    for item in new:
        result_map[item["id"]] = item

    # 转回列表，保留最近 10 条
    merged = list(result_map.values())
    return merged[-10:]


def merge_reasoning_segments(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """追加模型原生思考分段，供历史会话恢复。"""
    return list(existing or []) + list(new or [])


class QueryResultIndex(TypedDict):
    """查询结果索引，用于 LLM 选择"""
    id: str           # 结果文件 ID
    sql: str          # 执行的 SQL（便于 LLM 识别）
    columns: list[str]  # 列名列表
    row_count: int    # 行数


class SqlGeneratorState(TypedDict):
    """SQL生成器状态"""
    # 消息列表，使用 add_messages reducer 自动追加
    messages: Annotated[list[BaseMessage], add_messages]
    # 已加载的表 DDL 信息（给 LLM 看），使用自定义 reducer 合并
    tables_ddl_info: Annotated[str, merge_tables_ddl]
    # 已加载的表名列表（给程序用），用于判断增量加载
    loaded_table_names: Annotated[list[str], merge_table_names]
    # 模式：generate=仅生成SQL，query=生成并执行
    mode: Literal["generate", "query"]
    # 查询结果索引列表（ID + 摘要，便于 LLM 选择，实际数据存文件）
    query_results: Annotated[list[QueryResultIndex], merge_query_results]
    # 数据源ID（用于真实SQL执行时获取数据源配置）
    datasource_id: str | None
    # 当前语义配置ID（用于查询历史学习按指标集归属过滤）
    semantic_config_id: str | None
    # 当前登录用户ID（用于 HTTP 执行器透传）
    user_id: str | None
    # 当前登录用户展示名称（用于查询历史执行人员展示）
    operator_name: str | None
    # 来源会话ID（用于查询历史学习回溯对话上下文）
    conversation_id: str | None
    # 指定执行的成员数据源ID列表
    member_datasource_ids: list[str]
    # 当前选中的执行对象说明，供大模型理解执行范围
    execution_target_info: str
    # 当前轮模型上下文 token 占用估算
    token_usage: NotRequired[dict[str, Any] | None]
    # 模型原生思考分段
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
