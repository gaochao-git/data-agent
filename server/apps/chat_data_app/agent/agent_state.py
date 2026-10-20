"""ChatData 单一 Agent 的运行态定义。"""

from typing import Annotated, Any, Required

from langchain.agents import AgentState
from langchain_core.messages import AnyMessage
from langgraph.channels import DeltaChannel
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def merge_context_compression_events(
    existing: list[dict] | None,
    new: list[dict] | None,
) -> list[dict]:
    """追加压缩分界事件，并按稳定 ID 去重以支持流重试。"""
    merged: list[dict] = []
    event_ids: set[str] = set()
    for event in [*(existing or []), *(new or [])]:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if not event_id or event_id in event_ids:
            continue
        event_ids.add(event_id)
        merged.append(event)
    return merged


def merge_approval_grants(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """合并会话级审批许可（``tool:<name>`` / ``mcp_server:<id>``），去重保序。"""
    merged: list[str] = []
    seen: set[str] = set()
    for grant in [*(existing or []), *(new or [])]:
        normalized = str(grant or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def replace_approval_run_grants(
    existing: dict[str, list[str]] | None,
    new: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """按 run 替换"本轮内始终允许"许可。

    ``after_model`` 每轮只写当前 run 的许可集合，旧 run 的键随之被淘汰，
    既保证同一 run 内跨工具轮生效，又不会让 checkpoint 随对话无限增长。
    """
    _ = existing
    return {
        str(run_id): [str(item) for item in keys if str(item).strip()] for run_id, keys in (new or {}).items() if keys
    }


def replace_approval_decisions(
    existing: dict[str, str] | None,
    new: dict[str, str] | None,
) -> dict[str, str]:
    """按轮替换审批决策（tool_call_id → allow/deny）。

    每次模型响应后 ``after_model`` 都会重写本字段：本轮无待审批调用时写空字典，
    避免长会话把历史 tool_call_id 累积进 checkpoint。
    """
    _ = existing
    return {str(key): str(value) for key, value in (new or {}).items() if str(key or "").strip()}


def merge_discovered_tool_names(
    existing: list[str] | None,
    new: list[str] | None,
) -> list[str]:
    """追加按需发现的 MCP 工具名，保持首次发现顺序并去重。"""
    merged: list[str] = []
    seen: set[str] = set()
    for name in [*(existing or []), *(new or [])]:
        normalized = str(name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


class ExecutionContext(TypedDict, total=False):
    """工具执行所需的数据源和执行对象范围。"""

    datasource_id: str | None
    available_member_datasource_ids: list[str]
    matched_member_datasource_ids: list[str]


class ConversationWorkspaceContext(TypedDict, total=False):
    """当前会话工作区；只保存可序列化的配置。"""

    workspace_root: str
    workspace_backend: str


def delta_messages_reducer(
    state: list[AnyMessage] | None,
    writes: list[Any],
) -> list[AnyMessage]:
    """DeltaChannel 批量 reducer 适配层。

    DeltaChannel 契约：reducer(state, [w1, w2, ...])，wi 是单次写入的值——
    对 messages 即节点返回的整批消息（list 或单条）。add_messages 只接受
    一批消息，不能直接作为 DeltaChannel reducer（会把整批列表再包一层）。
    这里按写入顺序逐次 fold，等价于原生 LastValue 通道的行为，满足
    DeltaChannel 要求的批量结合律。
    """
    # add_messages 标注返回 Messages 联合（运行时恒为 list），以 Any 承接
    # 避免 list 不变性引发的类型摩擦，边界仍由函数签名约束为 list[AnyMessage]。
    result: Any = list(state or [])
    for write in writes:
        result = add_messages(result, write)
    return list(result)


class ChatDataAgentState(AgentState, total=False):
    """唯一 ChatData Agent 状态。

    messages 覆盖为 DeltaChannel（beta）：checkpoint 每步只写增量、每 25 步
    落一次全量快照，避免长会话全量快照 O(N²) 膨胀。add_messages 满足
    DeltaChannel 要求的批量结合律；压缩/子代理的 RemoveMessage(REMOVE_ALL)
    整体替换按哨兵清空、不依赖消息 id，重放语义保持一致。代价：恢复重建的
    消息对象 id 可能与 live 不同（无 id 消息由 add_messages 随机补 id），
    业务无按消息 id 消费 checkpoint state 的场景，可接受。

    已知偏差：langgraph-checkpoint-mysql 3.0.0 的 pending_writes 子查询无
    ORDER BY 且反序列化丢弃 idx，单 checkpoint 内多任务写 messages 时重放
    顺序不受上游 writes_sort_key 契约保护；当前图每超步仅一个任务写
    messages，现实无影响，未来引入并行写 messages 的任务前需先自定义
    saver 补排序。

    红线：1) 永远不要新增 checkpoint 祖先行清理任务——祖先链断裂时
    DeltaChannel 恢复会静默按空重建（无报错）；2) 上线 delta 后不可降级
    回旧版本续聊 delta 格式写入的会话。
    """

    messages: Required[
        Annotated[
            list[AnyMessage],
            DeltaChannel(reducer=delta_messages_reducer, snapshot_frequency=25),
        ]
    ]
    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    execution_context: ExecutionContext
    token_usage: dict[str, Any] | None
    context_compression: dict[str, Any] | None
    context_summary: str | None
    context_compression_events: Annotated[list[dict[str, Any]], merge_context_compression_events]
    discovered_tool_names: Annotated[list[str], merge_discovered_tool_names]
    # 会话级"始终允许"：仅存于当前会话 checkpoint，随会话结束自然失效。
    approval_grants: Annotated[list[str], merge_approval_grants]
    # 本轮审批决策：after_model 批量中断后写入，wrap_tool_call 执行前消费。
    approval_decisions: Annotated[dict[str, str], replace_approval_decisions]
    # 本轮内始终允许：run_id → [tool:<name>]，仅当前 run 生效，换 run 自动淘汰。
    approval_run_grants: Annotated[dict[str, list[str]], replace_approval_run_grants]
    workspace: ConversationWorkspaceContext
