"""
SQL Generator 状态定义
"""

from typing import Annotated, Literal, Any
from typing_extensions import NotRequired, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from utils.lang_util.artifact_store import save_artifact, ArtifactType


def save_query_result(data: dict, conversation_id: str | None = None) -> str:
    """保存查询结果，返回结果ID（使用公共 artifact_store）"""
    return save_artifact(ArtifactType.QUERY_RESULT, data, conversation_id=conversation_id)


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


def merge_reasoning_segments(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """追加模型原生思考分段，供历史会话恢复。"""
    return list(existing or []) + list(new or [])


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
    # 数据源ID（用于真实SQL执行时获取数据源配置）
    datasource_id: str | None
    # 当前语义配置ID（用于查询历史学习按指标集归属过滤）
    semantic_config_id: str | None
    # 当前轮可直接使用的语义配置（临时配置或已预加载配置）
    semantic_config: NotRequired[dict[str, Any] | None]
    # 当前登录用户ID（用于 HTTP 执行器透传）
    user_id: str | None
    # 当前登录用户展示名称（用于查询历史执行人员展示）
    operator_name: str | None
    # 来源会话ID（用于查询历史学习回溯对话上下文）
    conversation_id: str | None
    # 指定执行的成员数据源ID列表
    member_datasource_ids: list[str]
    # 当前可用执行对象的成员数据源ID列表，仅用于工具层校验可选范围
    available_member_datasource_ids: NotRequired[list[str]]
    # 用户原文已精确匹配到的成员数据源ID列表，仅用于工具层防止执行错机构
    matched_member_datasource_ids: NotRequired[list[str]]
    # 是否要求 semantic_query_tool 显式传入本次执行成员，避免把可用范围误当默认执行范围
    require_explicit_member_selection: NotRequired[bool]
    # 当前数据源组的智能问数说明，供大模型理解数据源组级规则和示例
    datasource_group_context: NotRequired[str]
    # 当前可见指标集轻量目录说明，供大模型回答指标集范围类问题
    semantic_config_directory_info: str
    # SQL 生成方言，仅用于普通 NL2SQL 生成链路
    sql_dialect: NotRequired[str]
    # 当前轮模型上下文 token 占用估算
    token_usage: NotRequired[dict[str, Any] | None]
    # 模型原生思考分段
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
