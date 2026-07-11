"""智能问数主图状态定义。"""

from typing import Annotated, Any
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from utils.artifact_store import save_artifact, ArtifactType


def save_query_result(data: dict, conversation_id: str | None = None) -> str:
    """保存查询结果，返回结果ID（使用公共 artifact_store）"""
    return save_artifact(ArtifactType.QUERY_RESULT, data, conversation_id=conversation_id)


def merge_reasoning_segments(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """追加模型原生思考分段，供历史会话恢复。"""
    return list(existing or []) + list(new or [])


class MessageState(TypedDict):
    """对话消息状态。"""
    messages: Annotated[list[BaseMessage], add_messages]


class ExecutionContext(TypedDict, total=False):
    """工具执行所需的数据源和执行对象范围。"""

    # 数据源ID（用于真实SQL执行时获取数据源配置）
    datasource_id: str | None
    # 当前请求可用的成员数据源ID列表
    available_member_datasource_ids: list[str]
    # 用户原文已精确匹配到的成员数据源ID列表，用于防止执行错机构
    matched_member_datasource_ids: list[str]


class PromptContext(TypedDict, total=False):
    """注入 system prompt 的只读上下文。"""

    # 当前数据源组的智能问数说明，供大模型理解数据源组级规则和示例
    datasource_group_context: str
    # 当前可见指标集轻量目录说明，供大模型回答指标集范围类问题
    semantic_config_directory_info: str


class ChatDataAgentState(MessageState, total=False):
    """智能问数主图状态。

    这里同时包含三类信息：
    - 持久对话状态：messages、reasoning_segments、token_usage
    - 工具运行上下文：执行对象、指标集和用户信息
    - 提示词上下文：数据源组说明、指标集目录
    """
    # 当前登录用户ID（用于 HTTP 执行器透传）
    user_id: str | None
    # 当前登录用户展示名称（用于查询历史执行人员展示）
    operator_name: str | None
    # 来源会话ID（用于查询历史学习回溯对话上下文）
    conversation_id: str | None
    # 工具执行上下文
    execution_context: ExecutionContext
    # 提示词注入上下文
    prompt_context: PromptContext
    # 当前语义配置ID（用于查询历史学习按指标集归属过滤）
    semantic_config_id: str | None
    # 当前轮可直接使用的语义配置（临时配置或已预加载配置）
    semantic_config: dict[str, Any] | None
    # 当前轮模型上下文 token 占用估算
    token_usage: dict[str, Any] | None
    # 模型原生思考分段
    reasoning_segments: Annotated[list[dict[str, Any]], merge_reasoning_segments]
