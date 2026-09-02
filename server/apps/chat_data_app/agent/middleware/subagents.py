"""ChatData 子智能体：单一 ``task`` 工具 + 上下文隔离。

参考 deepagents ``SubAgentMiddleware`` 的三个核心机制（单 task 工具、状态裁剪
透传、Command 回传结论），并按 ChatData 的事件流与状态设计做了裁剪：

- 子智能体是无状态的一次性调用：只看到任务书（HumanMessage），不继承主对话历史。
- 仅白名单状态键进入子智能体（身份/数据域/工作区），压缩账本与 token 用量
  不透传，子图消息永远不回流——主对话上下文只增加一条 ToolMessage 结论。
- 子图调用显式切断回调继承（``callbacks=[]``），子图内的模型 token 不进入
  主 SSE 流；内层事件通过 ``_run_subagent_with_events`` 挑选后经父图 custom
  流转发（``parent_tool_call_id`` 挂到所属 task 调用下），前端在 task 卡片内
  展示调用过程。文本与思考以 ``messages`` 模式做 token 级增量转发
  （``subagent_message_delta`` / ``subagent_thinking_delta``），工具生命周期
  仍以 ``updates`` 模式为准。
- 子智能体不注册 ``ask_user_question``，第一版不支持嵌套人工中断。
"""

from dataclasses import dataclass, field
from itertools import count
from typing import Any, Callable

from langchain.agents import create_agent
from langchain.agents.middleware import AgentState, AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.types import Command
from pydantic import BaseModel, Field

from utils.logger import get_logger
from utils.tool_call_args import coerce_tool_call_args
from .tool_call_normalizer import create_tool_call_name_normalizer_middleware
from .tool_result import create_tool_result_middleware

logger = get_logger(__name__)

SUBAGENT_RESULT_PREVIEW_MAX_CHARS = 2000
"""子智能体内层工具结果随 SSE 外发的最大长度，超出截断并打 truncated 标记。

子图不经过主对话的 32k tool_result offload（中间件只挂在主 agent 上），而
SSE custom 通道没有卸载机制，为避免巨包必须在此处主动截断。"""

SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS = 4000
"""子智能体中间文本输出（AIMessage.content）随 SSE 外发的最大长度。

与工具结果分开配置：文本是给人读的过程叙述，截断太狠会失去上下文；但仍然
必须截断——子智能体可能一次性吐出远超 SSE 单帧合理体积的分析长文。"""

SUBAGENT_THINKING_PREVIEW_MAX_CHARS = 8000
"""子智能体思考过程（reasoning_content）随 SSE 外发的最大长度。

独立于正文配置：思考普遍比正文长得多（同一轮里可能是正文的数倍），复用正文
上限会让思考几乎必被截断；但思考只在用户主动展开时可见，故允许更大的量。"""

SUBAGENT_STATE_PASSTHROUGH_KEYS = frozenset({
    "user_id",
    "operator_name",
    "conversation_id",
    "execution_context",
    "workspace",
})
"""允许从主对话复制进子智能体的状态键白名单（身份/数据域/工作区上下文）。

压缩账本（context_compression_events / context_summary）、token_usage 等一律
不透传：前者带自定义 reducer，回流会污染主对话的流重试去重；后者是主对话
的记账口径，子图有自己的生命周期。"""

TASK_TOOL_NAME = "task"

_TASK_TOOL_DESCRIPTION_TEMPLATE = """派发一个隔离上下文的子智能体处理复杂多步任务，只把最终结论带回主对话。

可用子智能体类型：
{available_agents}

使用要求：
- 每次调用相互独立：子智能体只能看到 description，写清背景、要做什么、交付什么格式。
- 只有子智能体的最终回复会回到主对话，中间过程用户不可见；把结论、关键数字和依据写完整。
- 相互独立的子任务可以在一条消息里并行发起多个 task 调用。
- 需要向用户提问或展示中间过程的工作不要派发，直接自己做。"""


class ChatDataSubAgentState(AgentState, total=False):
    """子智能体状态：仅 messages 加白名单透传的业务上下文。"""

    user_id: str | None
    operator_name: str | None
    conversation_id: str | None
    execution_context: dict[str, Any]
    workspace: dict[str, Any]


class TaskToolArgs(BaseModel):
    """``task`` 工具入参。"""

    description: str = Field(
        min_length=1,
        max_length=8000,
        description=(
            "给子智能体的完整任务书：背景、要做什么、期望的交付格式。"
            "子智能体看不到主对话，所有必要上下文都必须写在里面。"
        ),
    )
    subagent_type: str = Field(
        min_length=1,
        max_length=64,
        description="子智能体类型，必须是工具描述中列出的类型之一。",
    )


@dataclass(frozen=True)
class SubagentSpec:
    """子智能体定义。"""

    name: str
    description: str
    system_prompt: str
    tools: list = field(default_factory=list)
    model: Any = None
    recursion_limit: int = 40


def validate_subagent_specs(specs: list[SubagentSpec]) -> None:
    """校验子智能体定义：至少一个、名称非空且唯一。"""
    if not specs:
        raise ValueError("至少需要定义一个子智能体")
    names = [str(spec.name or "").strip() for spec in specs]
    if any(not name for name in names):
        raise ValueError("子智能体名称不能为空")
    duplicated = {name for name in names if names.count(name) > 1}
    if duplicated:
        raise ValueError(f"子智能体名称重复: {', '.join(sorted(duplicated))}")


def _extract_final_report(messages: list) -> str:
    """取子图最后一个非空 AIMessage 文本作为回传结论。"""
    for message in reversed(messages or []):
        if not isinstance(message, AIMessage):
            continue
        text = (getattr(message, "text", "") or "").rstrip()
        if text:
            return text
    return ""


def _compile_subagent(spec: SubagentSpec) -> Any:
    """把 spec 编译成独立 langgraph runnable。"""
    tools = list(spec.tools or [])
    return create_agent(
        spec.model,
        tools=tools,
        system_prompt=spec.system_prompt,
        state_schema=ChatDataSubAgentState,
        name=f"subagent_{spec.name}",
        # 内层模型与主智能体同一 vLLM，同样可能返回重复工具名/字符串 args，
        # 必须挂同一归一化 middleware，否则内层工具会以原始形态执行。
        # ToolResultMiddleware 必须同样挂载：查询工具返回的是内部协议 JSON，
        # 靠它在出口转成模型可读的小信封并外置全量数据；不挂则子图内
        # 全量查询数据内联进上下文（顺序与主图 MIDDLEWARE_CANONICAL_ORDER
        # 一致：先归一化入参，再处理出口）。白名单已透传
        # user_id/conversation_id/workspace，卸载副本写入所需的字段齐备。
        middleware=[
            create_tool_call_name_normalizer_middleware(tools),
            create_tool_result_middleware(),
        ],
    )


def build_subagent_invocation_config(spec: SubagentSpec) -> dict[str, Any]:
    """构造子图调用配置：切断回调继承，避免子图 token 进入主 SSE 流。"""
    return {
        "callbacks": [],
        "recursion_limit": max(int(spec.recursion_limit or 40), 1),
        "configurable": {"chatdata_subagent": spec.name},
    }


def _plain_text(content: Any) -> str:
    """把消息 content 归一化成纯文本（多模态 list 按行拼接）。"""
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else str(part)
            for part in content
        )
    return content if isinstance(content, str) else str(content or "")


def _truncate_text(text: str, max_chars: int, label: str = "结果") -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[{label}过长已截断，完整内容共 {len(text)} 字符]"


def _truncate_subagent_result(content: Any) -> str:
    """把内层工具结果压成 SSE 可安全外发的文本预览。"""
    return _truncate_text(_plain_text(content), SUBAGENT_RESULT_PREVIEW_MAX_CHARS)


def _chunk_text_delta(content: Any) -> str:
    """从流式 chunk 的 content 里取纯文本增量。

    ``str`` 直接返回；content blocks 形式只取 ``text`` 块，其余块（工具调用
    等结构化载荷）不参与文本增量，避免 dict 被 ``str()`` 成调试噪音。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _chunk_reasoning_text(chunk: Any) -> str:
    """从流式 chunk 提取思考增量。

    口径与 handlers ``_extract_reasoning_content`` 一致（OpenAI compatible
    的 ``reasoning_content`` / ``reasoning`` / ``reasoning_text``），但此处
    面向 middleware 层、不依赖 handlers；展示名替换只在 handlers 出口做。
    """
    for attr in ("additional_kwargs", "response_metadata"):
        value = getattr(chunk, attr, None)
        if not isinstance(value, dict):
            continue
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            reasoning = value.get(key)
            if isinstance(reasoning, str) and reasoning:
                return reasoning
    return ""


class _StreamCapState:
    """单个流式键（正文或思考）的累计字符数与封顶标记。"""

    __slots__ = ("chars", "capped")

    def __init__(self) -> None:
        self.chars = 0
        self.capped = False

    def take(self, delta: str, max_chars: int) -> tuple[str | None, bool]:
        """返回 ``(允许外发的增量片段, 本次是否触发封顶截断)``；封顶后为 ``(None, False)``。"""
        if self.capped or not delta:
            return None, False
        remaining = max_chars - self.chars
        if remaining <= 0:
            self.capped = True
            return None, False
        if len(delta) <= remaining:
            self.chars += len(delta)
            return delta, False
        self.chars = max_chars
        self.capped = True
        return delta[:remaining] + "\n…[内容过长已截断]", True


def _iter_chunk_stream_events(
    chunk: Any,
    parent_tool_call_id: str | None,
    text_caps: dict[str, _StreamCapState],
    think_caps: dict[str, _StreamCapState],
) -> list[dict[str, Any]]:
    """把 ``messages`` 模式的 ``AIMessageChunk`` 翻译成 token 级增量事件。

    - 正文增量 → ``subagent_message_delta``
    - 思考增量 → ``subagent_thinking_delta``
    - 无 ``message_id`` 的 chunk 不增量转发（无法与 updates 的完整消息收敛，
      由完整消息事件兜底）

    带 ``tool_call_chunks`` 的 chunk **不能整块跳过**：OpenAI 兼容实现里正文与
    工具调用是两个独立字段，同一个 chunk 完全可以「文本 + 工具增量」同时到达。
    一旦跳过，这段文本永远补不回来——该消息已因先前的 delta 进入
    ``streamed_text_ids``，完整消息事件按收敛策略只补标记、不再携带 content。
    工具参数不会漏进正文：``_chunk_text_delta`` 只取 ``str`` 与 ``type=="text"``
    的内容块，结构化块（工具调用等）不参与文本增量。
    """
    if not isinstance(chunk, AIMessageChunk):
        return []
    message_id = getattr(chunk, "id", None)
    if not message_id:
        return []

    events: list[dict[str, Any]] = []
    text = _chunk_text_delta(getattr(chunk, "content", ""))
    if text:
        cap = text_caps.setdefault(message_id, _StreamCapState())
        allowed, was_capped = cap.take(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS)
        if allowed:
            events.append({
                "event_type": "subagent_message_delta",
                "message_id": message_id,
                "delta": allowed,
                "truncated": was_capped,
                "parent_tool_call_id": parent_tool_call_id,
            })
    reasoning = _chunk_reasoning_text(chunk)
    if reasoning:
        cap = think_caps.setdefault(message_id, _StreamCapState())
        allowed, was_capped = cap.take(reasoning, SUBAGENT_THINKING_PREVIEW_MAX_CHARS)
        if allowed:
            events.append({
                "event_type": "subagent_thinking_delta",
                "message_id": message_id,
                "delta": allowed,
                "truncated": was_capped,
                "parent_tool_call_id": parent_tool_call_id,
            })
    return events


def _emit_subagent_tool_event(writer: Any, event: dict[str, Any]) -> None:
    """把子智能体内层工具事件写入父图 custom 流（writer 不可用时静默跳过）。"""
    if writer is None:
        return
    try:
        writer({"type": "subagent_tool", "event": event})
    except Exception:
        logger.warning("转发子智能体工具事件失败", exc_info=True)


def _iter_subagent_stream_events(
    messages: list,
    parent_tool_call_id: str | None = None,
    streamed_text_ids: set[str] | None = None,
    streamed_think_ids: set[str] | None = None,
    id_resolver: Callable[[Any], str] | None = None,
) -> list[dict[str, Any]]:
    """把子图节点更新里的消息翻译成子智能体内部过程事件序列。

    - AIMessage.content（非空文本）→ ``subagent_message``，即子智能体的中间
      叙述/分析结论，用 ``message_id`` 去重
    - AIMessage.tool_calls → ``tool_started``（args 尽力规整为 dict）
    - ToolMessage → ``tool_completed`` / ``tool_failed``（结果截断为预览）
    - 每个事件都挂 ``parent_tool_call_id`` 指向所属外层 task 调用，便于前端
      把内部过程挂在 task 卡片下、而不是当作顶层调用渲染。

    收敛策略（只补标记不覆盖）：``messages`` 模式已增量转发过文本/思考的
    消息（命中 ``streamed_text_ids`` / ``streamed_think_ids``），完整消息事件
    不再携带 content（前端已拼出全文），只补 ``truncated`` 标记。

    同一条 AIMessage 既有文本又有工具调用时按「先说后做」输出，保证前端拿到的
    顺序与子智能体真实执行顺序一致。

    ``id_resolver`` 为缺失 ``id`` 的消息生成稳定兜底 id：前端以 message_id 作
    为 trail 条目主键，空 id 会被整条丢弃——不能把「中间文本是否可见」押在
    provider 一定返回 chunk id 上。
    """
    streamed_text_ids = streamed_text_ids or set()
    streamed_think_ids = streamed_think_ids or set()
    events: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AIMessage):
            message_id = getattr(message, "id", None) or (id_resolver(message) if id_resolver else None)
            text = _plain_text(getattr(message, "content", ""))
            if text.strip():
                if message_id and message_id in streamed_text_ids:
                    events.append({
                        "event_type": "subagent_message",
                        "message_id": message_id,
                        "content": "",
                        "streamed": True,
                        "truncated": len(text) > SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
                        "parent_tool_call_id": parent_tool_call_id,
                    })
                else:
                    events.append({
                        "event_type": "subagent_message",
                        "message_id": message_id,
                        "content": _truncate_text(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS, "内容"),
                        # 两个分支都显式带 streamed：前端按布尔值判断收敛，
                        # 不区分「字段缺失」与「未流式」
                        "streamed": False,
                        "truncated": len(text) > SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
                        "parent_tool_call_id": parent_tool_call_id,
                    })
            for tool_call in getattr(message, "tool_calls", None) or []:
                if not isinstance(tool_call, dict):
                    continue
                events.append({
                    "event_type": "tool_started",
                    "tool_call_id": tool_call.get("id"),
                    "tool_name": tool_call.get("name"),
                    "args": coerce_tool_call_args(tool_call.get("args")) or {},
                    "parent_tool_call_id": parent_tool_call_id,
                })
        elif isinstance(message, ToolMessage):
            raw = _plain_text(getattr(message, "content", ""))
            status = str(getattr(message, "status", "") or "")
            events.append({
                "event_type": "tool_failed" if status == "error" else "tool_completed",
                "tool_call_id": getattr(message, "tool_call_id", None),
                "tool_name": getattr(message, "name", None),
                "content": _truncate_text(raw, SUBAGENT_RESULT_PREVIEW_MAX_CHARS),
                "truncated": len(raw) > SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
                "parent_tool_call_id": parent_tool_call_id,
            })
    return events


async def _run_subagent_with_events(
    runnable: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    writer: Any,
    parent_tool_call_id: str | None = None,
    collected: list | None = None,
) -> list:
    """流式运行子图：向父图 custom 流转发内层事件，返回完整消息列表。

    ``collected`` 可由调用方传入并持有：子图中途抛异常（如 recursion_limit、
    模型网络错误）时，调用方仍能用已收集的消息为失败路径构建 trail 快照。

    双模式并用：

    - ``messages``：token 级 ``AIMessageChunk``，正文/思考增量经
      ``subagent_message_delta`` / ``subagent_thinking_delta`` 转发，
      前端即时渲染；``callbacks=[]`` 不受影响（增量走 langgraph 流通道，
      不经过回调）。
    - ``updates``：节点完成后的完整消息，负责工具生命周期事件
      （tool_started / tool_completed / tool_failed）与最终报告提取；
      完整 ``subagent_message`` 事件对已增量转发的消息只补截断标记
      （收敛策略见 ``_iter_subagent_stream_events``）。
    """
    collected = [] if collected is None else collected
    seen_ids: set[str] = set()
    text_caps: dict[str, _StreamCapState] = {}
    think_caps: dict[str, _StreamCapState] = {}
    streamed_text_ids: set[str] = set()
    streamed_think_ids: set[str] = set()
    anonymous_seq = count(1)
    anonymous_ids: dict[int, str] = {}

    def resolve_anonymous_id(message: Any) -> str:
        """给缺失 id 的消息生成稳定 id。

        按对象身份缓存：同一条消息（无 id）可能出现在多个节点的 updates 里，
        必须收敛到同一个 id，否则 seen_ids 去重失效、前端会渲染出重复条目。
        消息对象在 collected 中持有强引用，``id()`` 作为 key 不会被复用。
        """
        key = id(message)
        generated = anonymous_ids.get(key)
        if generated is None:
            generated = f"{parent_tool_call_id or 'subagent'}:anon-{next(anonymous_seq)}"
            anonymous_ids[key] = generated
        return generated

    async for chunk in runnable.astream(state, config, stream_mode=["updates", "messages"]):
        if not (isinstance(chunk, tuple) and len(chunk) == 2):
            continue
        mode, payload = chunk
        if mode == "messages":
            if isinstance(payload, tuple) and len(payload) == 2:
                chunk_obj = payload[0]
                for event in _iter_chunk_stream_events(chunk_obj, parent_tool_call_id, text_caps, think_caps):
                    if event["event_type"] == "subagent_message_delta":
                        streamed_text_ids.add(event["message_id"])
                    else:
                        streamed_think_ids.add(event["message_id"])
                    _emit_subagent_tool_event(writer, event)
            continue
        if mode != "updates":
            continue
        update = payload
        if not isinstance(update, dict):
            continue
        new_messages: list = []
        for node_result in update.values():
            if not isinstance(node_result, dict):
                continue
            node_messages = node_result.get("messages") or []
            if isinstance(node_messages, dict):
                # 带 reducer 指令的消息更新（如 REMOVE_ALL_MESSAGES），跳过
                continue
            for message in node_messages:
                if isinstance(message, dict):
                    continue
                message_id = getattr(message, "id", None) or resolve_anonymous_id(message)
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
                collected.append(message)
                new_messages.append(message)
        for event in _iter_subagent_stream_events(
            new_messages, parent_tool_call_id, streamed_text_ids, streamed_think_ids, resolve_anonymous_id
        ):
            _emit_subagent_tool_event(writer, event)
    return collected


def _build_subagent_state(runtime: ToolRuntime, description: str) -> dict[str, Any]:
    state = {
        key: value
        for key, value in dict(runtime.state or {}).items()
        if key in SUBAGENT_STATE_PASSTHROUGH_KEYS
    }
    state["messages"] = [HumanMessage(content=description)]
    return state


def _build_trail_snapshot(messages: list) -> list[dict[str, Any]]:
    """把子图消息序列化成与前端 ``SubagentTrailItem`` 同构的过程快照。

    用于历史回放：直播时过程经 SSE custom 流逐条转发，刷新/看历史后由
    task ToolMessage 的 ``subagent_trail`` 还原。截断口径与 SSE 预览共用
    同一套常量，保证用户看到的直播内容与历史内容一致。

    - AIMessage → ``message`` 条目（content + reasoning 合并为一条，text 与
      tool_calls 按「先说后做」输出，顺序与 ``_iter_subagent_stream_events``
      一致）
    - ToolMessage → 回填对应内层工具的终态 status 与结果预览
    """
    tool_results: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_call_id = str(getattr(message, "tool_call_id", "") or "")
        if not tool_call_id:
            continue
        raw = _plain_text(getattr(message, "content", ""))
        tool_results[tool_call_id] = {
            "status": "error" if str(getattr(message, "status", "") or "") == "error" else "completed",
            "result": _truncate_text(raw, SUBAGENT_RESULT_PREVIEW_MAX_CHARS),
        }

    trail: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    anonymous_seq = count(1)
    for message in messages:
        if isinstance(message, AIMessage):
            message_id = str(getattr(message, "id", "") or "") or f"trail-anon-{next(anonymous_seq)}"
            text = _plain_text(getattr(message, "content", ""))
            thinking = _chunk_reasoning_text(message)
            if (text.strip() or thinking.strip()) and message_id not in seen_ids:
                seen_ids.add(message_id)
                trail.append({
                    "kind": "message",
                    "id": message_id,
                    "content": _truncate_text(text, SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS, "内容") if text.strip() else "",
                    "truncated": len(text) > SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS,
                    "thinking": _truncate_text(thinking, SUBAGENT_THINKING_PREVIEW_MAX_CHARS, "思考") if thinking.strip() else "",
                    "thinkingTruncated": len(thinking) > SUBAGENT_THINKING_PREVIEW_MAX_CHARS,
                })
            for tool_call in getattr(message, "tool_calls", None) or []:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = str(tool_call.get("id") or "")
                if not tool_call_id or tool_call_id in seen_ids:
                    continue
                seen_ids.add(tool_call_id)
                outcome = tool_results.get(tool_call_id) or {}
                trail.append({
                    "kind": "tool",
                    "id": tool_call_id,
                    "tool_name": str(tool_call.get("name") or ""),
                    "args": coerce_tool_call_args(tool_call.get("args")) or {},
                    "result": outcome.get("result", ""),
                    "status": outcome.get("status", "completed"),
                })
        elif isinstance(message, ToolMessage):
            # 内层工具先产生 ToolMessage 后才出现对应 tool_calls 的情形不
            # 应发生（OpenAI 协议要求先声明后调用）；此处只为容忍乱序输入。
            tool_call_id = str(getattr(message, "tool_call_id", "") or "")
            if tool_call_id and tool_call_id not in seen_ids:
                outcome = tool_results[tool_call_id]
                seen_ids.add(tool_call_id)
                trail.append({
                    "kind": "tool",
                    "id": tool_call_id,
                    "tool_name": str(getattr(message, "name", "") or ""),
                    "args": {},
                    "result": outcome["result"],
                    "status": outcome["status"],
                })
    return trail


def build_task_tool(specs: list[SubagentSpec]) -> StructuredTool:
    """构建单一 ``task`` 工具：按 subagent_type 派发到隔离上下文的子图。"""
    validate_subagent_specs(specs)
    compiled = {spec.name: _compile_subagent(spec) for spec in specs}
    available = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)

    async def atask(description: str, subagent_type: str, runtime: ToolRuntime):
        runnable = compiled.get(subagent_type)
        if runnable is None:
            allowed = ", ".join(f"`{name}`" for name in compiled)
            return f"无法调用子智能体 {subagent_type}：类型不存在，可用类型为 {allowed}"
        spec = next(item for item in specs if item.name == subagent_type)
        # writer 必须在子图调用前获取：调用后当前上下文会切进子图运行
        try:
            writer = get_stream_writer()
        except Exception:
            writer = None
        collected: list = []
        try:
            messages = await _run_subagent_with_events(
                runnable,
                _build_subagent_state(runtime, description),
                build_subagent_invocation_config(spec),
                writer,
                runtime.tool_call_id,
                collected=collected,
            )
        except Exception as exc:
            # 失败路径同样盖 trail 快照：直播时内层过程已通过 SSE 转发给用户，
            # 若异常直接抛给 tool_error 中间件（无 additional_kwargs），历史里
            # trail 会丢失，造成「直播看得到、历史看不到」的不一致。这里就地
            # 转成 error ToolMessage，主智能体可据失败原因降级接手。
            logger.error("[tools] 子智能体执行异常: %s", exc, exc_info=True)
            report = f"子智能体执行失败：{_truncate_text(str(exc) or exc.__class__.__name__, 200)}"
            messages = collected
            tool_status = "error"
        else:
            report = _extract_final_report(messages)
            if not report:
                report = "子智能体未产出有效结论，请检查任务书后重试或自行完成该任务。"
            tool_status = "success"
        # 过程快照盖在 ToolMessage 上随消息持久化：直播过程走 SSE，历史回放
        # 走 conversation_event 的 tool_result payload（见
        # conversation_event_service._serialize_tool_message）。
        additional_kwargs: dict[str, Any] = {}
        trail = _build_trail_snapshot(messages)
        if trail:
            additional_kwargs["subagent_trail"] = trail
        return Command(update={
            "messages": [ToolMessage(
                content=report,
                tool_call_id=runtime.tool_call_id or "",
                status=tool_status,
                additional_kwargs=additional_kwargs,
            )],
        })

    return StructuredTool.from_function(
        name=TASK_TOOL_NAME,
        coroutine=atask,
        description=_TASK_TOOL_DESCRIPTION_TEMPLATE.format(available_agents=available),
        args_schema=TaskToolArgs,
    )


class SubagentMiddleware(AgentMiddleware):
    """向主智能体注册 ``task`` 工具，并在系统提示追加子智能体使用说明。"""

    def __init__(self, specs: list[SubagentSpec], *, system_prompt: str | None = None):
        validate_subagent_specs(specs)
        self.specs = list(specs)
        self.subagent_names = frozenset(spec.name for spec in specs)
        self.tools = [build_task_tool(specs)]
        agents_desc = "\n".join(f"- {spec.name}: {spec.description}" for spec in specs)
        self.system_prompt = (system_prompt or _default_system_prompt()).format(
            available_agents=agents_desc,
        )

    def wrap_model_call(self, request, handler):
        return handler(_append_system_prompt(request, self.system_prompt))

    async def awrap_model_call(self, request, handler):
        return await handler(_append_system_prompt(request, self.system_prompt))


def _append_system_prompt(request, extra: str):
    """向请求的系统提示追加子智能体使用说明，保持 SystemMessage 类型。"""
    system_message = getattr(request, "system_message", None)
    if system_message is None:
        return request.override(system_message=SystemMessage(content=extra))
    merged = str(getattr(system_message, "content", "") or "").rstrip()
    return request.override(system_message=SystemMessage(
        content=f"{merged}\n\n{extra}",
        additional_kwargs=dict(getattr(system_message, "additional_kwargs", {}) or {}),
    ))


def _default_system_prompt() -> str:
    return (
        "## 子智能体委派\n\n"
        "你可以通过 task 工具把复杂多步任务派发给隔离上下文的子智能体，"
        "只有子智能体的最终结论会回到对话。可用类型：\n\n{available_agents}\n\n"
        "派发时写清任务书；需要用户确认或展示中间过程的工作自己完成。"
    )
