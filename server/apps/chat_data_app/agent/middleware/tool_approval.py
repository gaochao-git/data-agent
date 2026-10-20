"""按需审批中间件：注册表标记 / MCP 动态工具在执行前中断询问用户。

设计要点（对应需求「按需审批 / 自动审批」两档）：

- 仅在 ``AgentRunControls.approval_mode == "ask"`` 时装配；``auto`` 直接执行；
- 需要审批的工具：内置注册表 ``ToolSpec.approval_required``（当前：文件删除/移动）
  + MCP 动态工具（不在注册表、第三方默认严格，按 ``mcp_`` 前缀统一 ask；
  ``mcp_tool_search`` 只读检索除外）；
- **一轮模型响应内的审批工具调用批量收集、单次中断**（``after_model`` 钩子）：
  并行工具调用不会产生多个 interrupt，也就不会只展示第一个、卡住其余；
- 复用既有 LangGraph interrupt + pending-question/answer 通道：审批载荷是
  question 形状（单工具：允许 / 本对话内始终允许 / 拒绝；多工具：每个操作一个
  question，逐条允许/本对话内始终允许/拒绝），前端无需新增审批组件即可复用询问卡片；
- "本对话内始终允许"按**工具**粒度写入会话 checkpoint（``approval_grants``），
  后续同类调用直接放行；会话结束/新建会话自然失效；
- 无人值守运行（定时任务/评测）由调用方强制 ``auto``，本中间件不装配——不会
  出现无人应答的悬挂；若未来被错误装配，审批通道不可用/取消时一律**拒绝**
  （fail-closed），而不是放行。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command, interrupt

from utils.logger import get_logger

from ..tools.registry import APPROVAL_REQUIRED_TOOL_NAMES

logger = get_logger(__name__)

# MCP 工具命名与检索工具的单一事实源在 agent/mcp（manager / catalog）；此处用常量
# 保持轻量依赖，不把 mcp SDK 拉进审批中间件模块。
_MCP_TOOL_PREFIX = "mcp_"
_MCP_TOOL_SEARCH_TOOL_NAME = "mcp_tool_search"

# 审批选项文案：与前端询问卡片渲染一致；答案解析按 label 匹配（严格白名单）
APPROVAL_ALLOW_LABEL = "允许"
APPROVAL_ALWAYS_ALLOW_LABEL = "本对话内始终允许"
_LEGACY_ALWAYS_ALLOW_LABEL = "本次会话始终允许"  # 兼容升级前已下发的 pending question
APPROVAL_DENY_LABEL = "拒绝"
_APPROVAL_QUESTION_ID = "approval"
_ARGS_PREVIEW_MAX_CHARS = 500


class ToolApprovalMiddleware(AgentMiddleware):
    """按需审批：``after_model`` 批量中断 + ``wrap_tool_call`` 按决策放行/拒绝。"""

    def __init__(self, tool_labels: dict[str, str] | None = None) -> None:
        super().__init__()
        # 工具名 → 用户可见名（MCP 的 display_name；内置工具用原始名）
        self._tool_labels = dict(tool_labels or {})

    # ---- after_model：批量收集本轮到待审批调用，仅中断一次 ----

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        calls = self._pending_calls(state)
        if not calls:
            # 本轮无待审批调用：清空上一轮决策，避免 checkpoint 随会话累积
            return {"approval_decisions": {}}
        payload = build_batch_approval_payload(
            [
                {
                    "call_id": call["call_id"],
                    "tool_name": call["tool_name"],
                    "label": self._tool_labels.get(call["tool_name"], call["tool_name"]),
                    "tool_args_preview": _tool_args_preview(call["tool_call"]),
                    "tool_call": call["tool_call"],
                }
                for call in calls
            ]
        )
        try:
            answer = interrupt(payload)
        except GraphInterrupt:
            raise
        except Exception:
            logger.warning(
                "工具审批通道异常，按拒绝处理: tools=%s", [call["tool_name"] for call in calls], exc_info=True
            )
            return {"approval_decisions": {call["call_id"]: "deny" for call in calls}}
        if len(calls) == 1:
            decision = _resolve_decision(answer)
            decisions = {calls[0]["call_id"]: ("allow" if decision in ("allow", "always") else "deny")}
            update: dict[str, Any] = {"approval_decisions": decisions}
            if decision == "always":
                update["approval_grants"] = [calls[0]["grant_key"]]
            return update
        decisions, grants = _resolve_batch_answers(answer, calls)
        batch_update: dict[str, Any] = {"approval_decisions": decisions}
        if grants:
            batch_update["approval_grants"] = grants
        return batch_update

    def _pending_calls(self, state: Any) -> list[dict[str, Any]]:
        latest_ai = _latest_ai_message(state)
        if latest_ai is None:
            return []
        granted = _string_set(state, "approval_grants")
        decided = _string_set(state, "approval_decisions")
        calls: list[dict[str, Any]] = []
        for index, tool_call in enumerate(latest_ai.tool_calls or []):
            call = self._pending_call(tool_call, index, granted, decided)
            if call is not None:
                calls.append(call)
        return calls

    def _pending_call(
        self,
        tool_call: Any,
        index: int,
        granted: set[str],
        decided: set[str],
    ) -> dict[str, Any] | None:
        tool_name = str(tool_call.get("name") or "")
        if not requires_approval(tool_name):
            return None
        call_id = str(tool_call.get("id") or f"{tool_name}:{index}")
        if call_id in decided:
            return None
        grant_key = _grant_key(tool_name)
        if grant_key in granted:
            return None
        return {"call_id": call_id, "tool_name": tool_name, "tool_call": tool_call, "grant_key": grant_key}

    # ---- wrap_tool_call：执行前消费决策；无决策时兜底单次询问 ----

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        tool_name = str(tool_call.get("name") or "")
        if not requires_approval(tool_name):
            return await handler(request)
        call_id = str(tool_call.get("id") or "")
        state = getattr(request, "state", None)
        decision = ""
        if isinstance(state, dict):
            decision = str((state.get("approval_decisions") or {}).get(call_id) or "")
            if not decision and _has_session_grant(state, _grant_key(tool_name)):
                return await handler(request)
        if decision == "allow":
            return await handler(request)
        if decision == "deny":
            return _deny_message(request, "用户拒绝了本次操作")
        return await _run_with_single_approval(request, tool_name, tool_call, handler)


def _latest_ai_message(state: Any) -> AIMessage | None:
    messages = state.get("messages") if isinstance(state, dict) else None
    return next((message for message in reversed(messages or []) if isinstance(message, AIMessage)), None)


def _string_set(state: Any, field: str) -> set[str]:
    raw = state.get(field) if isinstance(state, dict) else None
    if isinstance(raw, dict):
        return {str(key) for key in raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    return set()


def _tool_args_preview(tool_call: Any) -> str:
    """工具参数摘要（当前仅文件工具/ MCP 参数）：截断防超长。"""
    args = tool_call.get("args") or {} if isinstance(tool_call, dict) else {}
    try:
        raw = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        raw = str(args)
    return raw[:_ARGS_PREVIEW_MAX_CHARS]


def requires_approval(tool_name: str) -> bool:
    """工具是否需要人工审批：内置注册表标记 + MCP 动态工具（第三方默认严格）。

    MCP 工具不在内置注册表里（运行时枚举），默认按 ask 处理；``mcp_tool_search``
    是只读检索工具，不打断。语义与 AUTO 模式一致：只在 ask 模式装配本中间件。
    """
    if tool_name in APPROVAL_REQUIRED_TOOL_NAMES:
        return True
    if tool_name == _MCP_TOOL_SEARCH_TOOL_NAME:
        return False
    return tool_name.startswith(_MCP_TOOL_PREFIX)


def _mcp_display_info(tool: Any) -> dict[str, str]:
    """从 StructuredTool.metadata 取 MCP 来源信息；非 MCP 或未带 metadata 返回空。"""
    metadata = getattr(tool, "metadata", None)
    if not isinstance(metadata, dict) or metadata.get("origin") != "mcp":
        return {}
    info = {
        "mcp_server_id": str(metadata.get("mcp_server_id") or ""),
        "mcp_tool_name": str(metadata.get("mcp_tool_name") or ""),
    }
    display_name = str(metadata.get("display_name") or "").strip()
    if display_name:
        info["display_name"] = display_name
    return {key: value for key, value in info.items() if value}


def _grant_key(tool_name: str) -> str:
    """会话级许可的键：按**工具**粒度（MCP 也是单个工具），避免"同意只读工具=放行同 server 删除工具"。"""
    return f"tool:{tool_name}"


def _tool_label(tool_name: str, tool: Any = None) -> str:
    info = _mcp_display_info(tool)
    return info.get("display_name") or info.get("mcp_tool_name") or tool_name


def _always_allow_description(tool_name: str, tool: Any = None) -> str:
    return f"本对话内「{_tool_label(tool_name, tool)}」不再询问"


def build_approval_payload(tool_name: str, tool_call: Any, tool: Any = None) -> dict[str, Any]:
    """单工具审批载荷（兜底路径）：question 形状，额外带 kind/tool 元信息。"""
    preview = _tool_args_preview(tool_call)
    mcp_info = _mcp_display_info(tool)
    label = _tool_label(tool_name, tool)
    question = f"是否允许执行 MCP 工具「{label}」？" if mcp_info else f"是否允许执行「{tool_name}」？"
    payload = _approval_payload(
        question,
        [APPROVAL_ALLOW_LABEL, APPROVAL_ALWAYS_ALLOW_LABEL, APPROVAL_DENY_LABEL],
        preview,
        tool_name,
        mcp_info,
        always_desc=_always_allow_description(tool_name, tool),
    )
    payload["approval_items"] = [_approval_item({"tool_name": tool_name, "label": label, "tool_call": tool_call})]
    return payload


def _args_summary(tool_call: Any) -> dict[str, str]:
    """参数摘要：只保留标量值，截断每项长度，供审批卡结构化展示。"""
    args = tool_call.get("args") if isinstance(tool_call, dict) else None
    if not isinstance(args, dict):
        return {}
    summary: dict[str, str] = {}
    for key, value in args.items():
        if len(summary) >= 6:
            break
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[str(key)] = str(value)[:160]
        elif isinstance(value, (dict, list)):
            try:
                summary[str(key)] = json.dumps(value, ensure_ascii=False)[:160]
            except (TypeError, ValueError):
                summary[str(key)] = str(value)[:160]
    return summary


def _approval_item(call: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(call.get("tool_name") or "")
    tool_call = call.get("tool_call") or {}
    summary = _args_summary(tool_call)
    # intent 是模型对本次操作的自述（例如"删除第一个测试文件"），单独展示给用户辅助判断
    intent = str(summary.pop("intent", "") or "")
    call_id = str(call.get("call_id") or (tool_call.get("id") if isinstance(tool_call, dict) else "") or "")
    return {
        "call_id": call_id,
        "tool_name": tool_name,
        "label": str(call.get("label") or tool_name),
        "intent": intent,
        "args_summary": summary,
    }


def build_batch_approval_payload(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """一轮内多个审批工具的合并载荷：一次中断解决并行工具调用。

    单工具时保持原「允许/始终允许/拒绝」文案；多工具时每个操作一个 question
    （逐条允许/本对话内始终允许/拒绝）。载荷附带结构化 ``approval_items``（工具名/展示名/参数摘要），
    前端据此渲染操作清单，而不是把原始 JSON 拼进问题文本。
    """
    if len(calls) == 1:
        call = calls[0]
        preview = str(call.get("tool_args_preview") or "")
        label = str(call.get("label") or call.get("tool_name") or "")
        question = f"是否允许执行「{label}」？"
        payload = _approval_payload(
            question,
            [APPROVAL_ALLOW_LABEL, APPROVAL_ALWAYS_ALLOW_LABEL, APPROVAL_DENY_LABEL],
            preview,
            str(call.get("tool_name") or ""),
            {},
            always_desc=f"本对话内「{label}」不再询问",
        )
        payload["approval_items"] = [_approval_item({**call, "label": label})]
        return payload
    questions = [
        {
            "id": str(call["call_id"]),
            "header": "需要你的确认",
            "question": str(call.get("label") or call.get("tool_name") or ""),
            "options": [
                {"label": APPROVAL_ALLOW_LABEL, "description": "允许执行该操作"},
                {"label": APPROVAL_ALWAYS_ALLOW_LABEL, "description": "本对话内不再询问该工具"},
                {"label": APPROVAL_DENY_LABEL, "description": "拒绝该操作"},
            ],
            "multi_select": False,
        }
        for call in calls
    ]
    payload = {
        "question_id": uuid4().hex,
        "kind": "approval",
        "tool_name": "",
        "questions": questions,
        "approval_items": [_approval_item(call) for call in calls],
    }
    return payload


def _approval_payload(
    question: str,
    labels: list[str],
    preview: str,
    tool_name: str,
    mcp_info: dict[str, str],
    always_desc: str = "本对话内这些工具不再询问",
) -> dict[str, Any]:
    descriptions = {
        APPROVAL_ALLOW_LABEL: "执行本次操作",
        APPROVAL_ALWAYS_ALLOW_LABEL: always_desc,
        APPROVAL_DENY_LABEL: "不执行；模型会收到拒绝原因并调整方案",
    }
    return {
        "question_id": uuid4().hex,
        "kind": "approval",
        "tool_name": tool_name,
        **mcp_info,
        "tool_args_preview": preview,
        "questions": [
            {
                "id": _APPROVAL_QUESTION_ID,
                "header": "需要你的确认",
                "question": question,
                "options": [{"label": label, "description": descriptions[label]} for label in labels],
                "multi_select": False,
            }
        ],
    }


def _selected_label(answer: Any) -> str:
    if not isinstance(answer, dict) or answer.get("cancelled") is True:
        return ""
    answers = answer.get("answers")
    if not isinstance(answers, list):
        return ""
    for item in answers:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != _APPROVAL_QUESTION_ID:
            continue
        selected = item.get("selected")
        if isinstance(selected, list) and selected:
            return str(selected[0])
        return ""
    return ""


def _selected_labels(answer: Any) -> dict[str, str]:
    """从答案中提取 question id → 选中 label（结构非法返回空）。"""
    labels: dict[str, str] = {}
    answers = answer.get("answers") if isinstance(answer, dict) else None
    if not isinstance(answers, list):
        return labels
    for item in answers:
        if not isinstance(item, dict):
            continue
        selected = item.get("selected")
        if isinstance(selected, list) and selected:
            labels[str(item.get("id") or "")] = str(selected[0])
    return labels


def _is_always_label(label: str | None) -> bool:
    """「始终允许」标签判定（兼容升级前下发的旧文案）。"""
    return label in (APPROVAL_ALWAYS_ALLOW_LABEL, _LEGACY_ALWAYS_ALLOW_LABEL)


def _resolve_call_answer(label: str | None) -> tuple[str, bool]:
    """单条选择 → (allow/deny, 是否写会话许可)；未知/缺失 fail-closed 为拒绝。"""
    if _is_always_label(label):
        return "allow", True
    if label == APPROVAL_ALLOW_LABEL:
        return "allow", False
    return "deny", False


def _resolve_batch_answers(answer: Any, calls: list[dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """批量审批：按 question id（= tool_call_id）逐条解析，缺失/未知一律拒绝。

    单条选择「本对话内始终允许」时：本条放行并写入该工具的会话许可（grants）。
    """
    labels = _selected_labels(answer)
    decisions: dict[str, str] = {}
    grants: list[str] = []
    for call in calls:
        call_id = str(call["call_id"])
        decision, grant = _resolve_call_answer(labels.get(call_id))
        decisions[call_id] = decision
        if grant:
            grants.append(str(call["grant_key"]))
    return decisions, grants


def _resolve_decision(answer: Any) -> str:
    """把用户选择归一化为 allow / always / deny（未知/取消一律 deny）。"""
    label = _selected_label(answer)
    if _is_always_label(label):
        return "always"
    if label == APPROVAL_ALLOW_LABEL:
        return "allow"
    return "deny"


def is_always_allowed(answer: Any) -> bool:
    """解析 resume 答案：显式选择「本对话内始终允许」时返回 True（含允许本次执行）。"""
    return _is_always_label(_selected_label(answer))


def is_approval_allowed(answer: Any) -> bool:
    """解析 resume 回来的答案：只有显式选择「允许」才放行，其余一律拒绝。"""
    return _selected_label(answer) == APPROVAL_ALLOW_LABEL


def _append_grant(result: ToolMessage | Command[Any], grant_key: str) -> ToolMessage | Command[Any]:
    """在保留工具结果的同时，把"本对话内始终允许"写回会话 state。"""
    if isinstance(result, Command):
        update = dict(result.update or {})
        update["approval_grants"] = [grant_key]
        return Command(update=update, goto=result.goto, resume=result.resume)
    if isinstance(result, ToolMessage):
        return Command(update={"messages": [result], "approval_grants": [grant_key]})
    return result


def _deny_message(request: ToolCallRequest, reason: str) -> ToolMessage:
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = str(tool_call.get("name") or "")
    return ToolMessage(
        content=f"操作未获用户批准，已拒绝执行工具「{tool_name}」：{reason}",
        name=tool_name,
        tool_call_id=str(tool_call.get("id") or ""),
        status="error",
    )


def _has_session_grant(state: Any, grant_key: str) -> bool:
    """当前会话是否已授予该工具的"始终允许"。"""
    grants = state.get("approval_grants") if isinstance(state, dict) else None
    return isinstance(grants, list) and grant_key in {str(grant) for grant in grants}


async def _run_with_single_approval(
    request: ToolCallRequest,
    tool_name: str,
    tool_call: Any,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]:
    """兜底：未经过 after_model 的审批工具（异常路径）单次询问。"""
    tool = getattr(request, "tool", None)
    grant_key = _grant_key(tool_name)
    try:
        answer = interrupt(build_approval_payload(tool_name, tool_call, tool))
    except GraphInterrupt:
        raise
    except Exception:
        logger.warning("工具审批通道异常，按拒绝处理: tool=%s", tool_name, exc_info=True)
        return _deny_message(request, "审批通道不可用")
    if is_always_allowed(answer):
        return _append_grant(await handler(request), grant_key)
    if is_approval_allowed(answer):
        return await handler(request)
    return _deny_message(request, "用户拒绝了本次操作")


def create_tool_approval_middleware(tools: list[Any] | None = None) -> AgentMiddleware:
    """装配 ask 模式的审批中间件（批量中断 + 决策执行）。"""
    labels: dict[str, str] = {}
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "")
        metadata = getattr(tool, "metadata", None)
        display = str(metadata.get("display_name") or "").strip() if isinstance(metadata, dict) else ""
        if name:
            labels[name] = display or name
    return ToolApprovalMiddleware(labels)
