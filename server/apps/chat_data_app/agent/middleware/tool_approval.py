"""工具审批中间件：注册表标记 / MCP 动态工具在执行前中断询问用户。

设计要点（B 端固定策略：每工具 ``ask/allow``，无全局审批模式）：

- B 端固定策略：审批中间件在交互会话始终装配；无人值守不装配且 ask 工具
  在装配层 fail-closed 剔除；
- 需要审批的工具：内置注册表 ``ToolSpec.approval_required``（当前：文件删除/移动）
  + MCP 动态工具（不在注册表、第三方默认严格，按 ``mcp_`` 前缀统一 ask；
  ``mcp_tool_search`` 只读检索除外）；
- 每工具审批档位（``plugin.json`` 的 ``mcp_servers[].tool_approvals``，按工具
  metadata ``approval`` 透传到本中间件）：``allow`` 免审批；缺省 ``ask``。
  不使用 ``deny/force_ask/auto``；不需要的工具在装配层不挂载；
- **一轮模型响应内的审批工具调用批量收集、单次中断**（``after_model`` 钩子）：
  并行工具调用不会产生多个 interrupt，也就不会只展示第一个、卡住其余；
- 复用既有 LangGraph interrupt + pending-question/answer 通道：审批载荷是
  question 形状，单工具/多工具都只提供「允许 / 拒绝」；
- 用户只能对当前调用选择允许/拒绝；不写入本轮/本会话持久授权；
- 无人值守运行（定时任务/评测）不装配本中间件，ask 工具在装配层
  fail-closed 剔除（不悬挂）；审批通道不可用/取消时一律**拒绝**，而不是放行。
"""

from __future__ import annotations

import json
import time
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
_MCP_TOOL_CALL_TOOL_NAME = "mcp_tool_call"

# 审批选项文案：与前端询问卡片渲染一致；答案解析按 label 匹配（严格白名单）
APPROVAL_ALLOW_LABEL = "允许"
APPROVAL_RUN_ALWAYS_ALLOW_LABEL = "本轮内始终允许"
APPROVAL_ALWAYS_ALLOW_LABEL = "本会话内始终允许"
_LEGACY_ALWAYS_ALLOW_LABELS = (
    "本次会话始终允许",
    "本对话内始终允许",
    "本窗口内始终允许",
)  # 兼容升级前已下发的 pending question
APPROVAL_DENY_LABEL = "拒绝"
_APPROVAL_QUESTION_ID = "approval"
_ARGS_PREVIEW_MAX_CHARS = 500


class ToolApprovalMiddleware(AgentMiddleware):
    """工具审批：``after_model`` 批量中断 + ``wrap_tool_call`` 按决策放行/拒绝。"""

    def __init__(
        self,
        tool_labels: dict[str, str] | None = None,
        run_id: str | None = None,
        *,
        allowed_tools: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        super().__init__()
        # 工具名 → 用户可见名（MCP 的 display_name；内置工具用原始名）
        self._tool_labels = dict(tool_labels or {})
        # 当前 run 标识（保留给调用方，审批决策不跨 run 复用）
        self._run_id = str(run_id or "")
        # 每工具 allow 策略：仅该工具免审批；未知 MCP/注册表标记一律 ask。
        self._allowed_tools = frozenset(allowed_tools)

    def _needs_approval(self, tool_name: str) -> bool:
        """固定工具策略：allow/未挂载 MCP 工具直行；其余需审批工具一律中断。"""
        if tool_name in self._allowed_tools:
            return False
        # 直连幻觉 MCP 名（不在本次装配工具表里）不弹审批：先让框架返回工具不存在，
        # 避免用户白批一次；代理到不存在目标同理。
        if tool_name.startswith(_MCP_TOOL_PREFIX) and tool_name not in self._tool_labels:
            return False
        return requires_approval(tool_name)

    # ---- after_model：批量收集本轮到待审批调用，仅中断一次 ----

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        calls = self._pending_calls(state)
        if not calls:
            # 本轮无待审批调用：清空上一轮决策。
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
            call = calls[0]
            update: dict[str, Any] = {
                "approval_decisions": {call["call_id"]: ("allow" if decision != "deny" else "deny")}
            }
            # B 端策略：只记录本次允许/拒绝，不产生任何持久授权。
            return update
        return {"approval_decisions": _resolve_batch_answers(answer, calls)}

    def _pending_calls(self, state: Any) -> list[dict[str, Any]]:
        latest_ai = _latest_ai_message(state)
        if latest_ai is None:
            return []
        decided = _string_set(state, "approval_decisions")
        calls: list[dict[str, Any]] = []
        for index, tool_call in enumerate(latest_ai.tool_calls or []):
            call = self._pending_call(tool_call, index, decided)
            if call is not None:
                calls.append(call)
        return calls

    def _pending_call(self, tool_call: Any, index: int, decided: set[str]) -> dict[str, Any] | None:
        tool_name = str(tool_call.get("name") or "")
        if tool_name == _MCP_TOOL_CALL_TOOL_NAME and not _has_proxy_target(tool_call):
            return None
        target_name, effective_call = _approval_target(tool_name, tool_call)
        if not self._needs_approval(target_name):
            return None
        call_id = str(tool_call.get("id") or f"{tool_name}:{index}")
        if call_id in decided:
            return None
        return {
            "call_id": call_id,
            "tool_name": target_name,
            "tool_call": effective_call,
        }

    # ---- wrap_tool_call：执行前消费决策；无决策时兜底单次询问 ----

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        tool_name = str(tool_call.get("name") or "")
        if tool_name == _MCP_TOOL_CALL_TOOL_NAME and not _has_proxy_target(tool_call):
            return await handler(request)
        target_name, effective_call = _approval_target(tool_name, tool_call)
        if not self._needs_approval(target_name):
            return await handler(request)
        call_id = str(tool_call.get("id") or "")
        state = getattr(request, "state", None)
        decision = ""
        if isinstance(state, dict):
            decision = str((state.get("approval_decisions") or {}).get(call_id) or "")
        if decision == "allow":
            return await handler(request)
        if decision == "deny":
            return _deny_message(request, "用户拒绝了本次操作")
        return await _run_with_single_approval(request, target_name, effective_call, handler)


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
    是只读检索工具，不打断。交互会话始终装配本中间件。
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


def _has_proxy_target(tool_call: Any) -> bool:
    if not isinstance(tool_call, dict):
        return False
    args = tool_call.get("args")
    target = str((args or {}).get("name") or "").strip() if isinstance(args, dict) else ""
    return target.startswith(_MCP_TOOL_PREFIX) and target not in {
        _MCP_TOOL_SEARCH_TOOL_NAME,
        _MCP_TOOL_CALL_TOOL_NAME,
    }


def _approval_target(tool_name: str, tool_call: Any) -> tuple[str, Any]:
    """代理调用把审批对象落到目标 MCP 工具，避免 grant 坍缩到 mcp_tool_call。"""
    if tool_name != _MCP_TOOL_CALL_TOOL_NAME or not isinstance(tool_call, dict):
        return tool_name, tool_call
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return tool_name, tool_call
    target = str(args.get("name") or "").strip()
    if not target.startswith(_MCP_TOOL_PREFIX) or target in {
        _MCP_TOOL_SEARCH_TOOL_NAME,
        _MCP_TOOL_CALL_TOOL_NAME,
    }:
        return tool_name, tool_call
    effective = dict(tool_call)
    target_input = dict(args["input"]) if isinstance(args.get("input"), dict) else {}
    intent = str(args.get("intent") or "").strip()
    if intent:
        target_input["intent"] = intent
    effective["args"] = target_input
    return target, effective


def _tool_label(tool_name: str, tool: Any = None) -> str:
    info = _mcp_display_info(tool)
    return info.get("display_name") or info.get("mcp_tool_name") or tool_name


def build_approval_payload(tool_name: str, tool_call: Any, tool: Any = None) -> dict[str, Any]:
    """单工具审批载荷（兜底路径）：question 形状，额外带 kind/tool 元信息。"""
    preview = _tool_args_preview(tool_call)
    mcp_info = _mcp_display_info(tool)
    label = _tool_label(tool_name, tool)
    question = f"是否允许执行 MCP 工具「{label}」？" if mcp_info else f"是否允许执行「{tool_name}」？"
    payload = _approval_payload(
        question,
        [APPROVAL_ALLOW_LABEL, APPROVAL_DENY_LABEL],
        preview,
        tool_name,
        mcp_info,
    )
    payload["approval_items"] = [_approval_item({"tool_name": tool_name, "label": label, "tool_call": tool_call})]
    payload["asked_at_ms"] = int(time.time() * 1000)
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

    B 端审批只允许单次允许/拒绝；多工具时每个操作一个 question（逐条允许/拒绝）。载荷附带结构化 ``approval_items``（工具名/展示名/参数摘要），
    前端据此渲染操作清单，而不是把原始 JSON 拼进问题文本。
    """
    if len(calls) == 1:
        call = calls[0]
        preview = str(call.get("tool_args_preview") or "")
        label = str(call.get("label") or call.get("tool_name") or "")
        question = f"是否允许执行「{label}」？"
        payload = _approval_payload(
            question,
            [APPROVAL_ALLOW_LABEL, APPROVAL_DENY_LABEL],
            preview,
            str(call.get("tool_name") or ""),
            {},
        )
        payload["approval_items"] = [_approval_item({**call, "label": label})]
        payload["asked_at_ms"] = int(time.time() * 1000)
        return payload
    questions = [
        {
            "id": str(call["call_id"]),
            "header": "需要你的确认",
            "question": str(call.get("label") or call.get("tool_name") or ""),
            "options": [
                {"label": APPROVAL_ALLOW_LABEL, "description": "允许执行该操作"},
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
        "asked_at_ms": int(time.time() * 1000),
    }
    return payload


def _approval_payload(
    question: str,
    labels: list[str],
    preview: str,
    tool_name: str,
    mcp_info: dict[str, str],
) -> dict[str, Any]:
    descriptions = {
        APPROVAL_ALLOW_LABEL: "执行本次操作",
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
    """「本会话内始终允许」标签判定（兼容升级前下发的旧文案）。"""
    return label == APPROVAL_ALWAYS_ALLOW_LABEL or label in _LEGACY_ALWAYS_ALLOW_LABELS


def _is_run_always_label(label: str | None) -> bool:
    return label == APPROVAL_RUN_ALWAYS_ALLOW_LABEL


def _resolve_batch_answers(answer: Any, calls: list[dict[str, Any]]) -> dict[str, str]:
    """逐项解析批量审批；历史授权 label 只按本次 allow 处理，不产生 grant。"""
    labels = _selected_labels(answer)
    decisions: dict[str, str] = {}
    for call in calls:
        call_id = str(call["call_id"])
        label = labels.get(call_id, "")
        allowed = label == APPROVAL_ALLOW_LABEL or _is_always_label(label) or _is_run_always_label(label)
        decisions[call_id] = "allow" if allowed else "deny"
    return decisions


def _resolve_decision(answer: Any) -> str:
    """把用户选择归一化为 allow / deny（未知/取消一律 deny）。"""
    label = _selected_label(answer)
    # 兼容旧 pending 文案：历史「始终允许」只按本次 allow 处理，不再产生授权。
    if _is_run_always_label(label) or _is_always_label(label):
        return "allow"
    if label == APPROVAL_ALLOW_LABEL:
        return "allow"
    return "deny"


def _deny_message(request: ToolCallRequest, reason: str) -> ToolMessage:
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = str(tool_call.get("name") or "")
    return ToolMessage(
        content=f"操作未获用户批准，已拒绝执行工具「{tool_name}」：{reason}",
        name=tool_name,
        tool_call_id=str(tool_call.get("id") or ""),
        status="error",
    )


async def _run_with_single_approval(
    request: ToolCallRequest,
    tool_name: str,
    tool_call: Any,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]:
    """兜底：未经过 after_model 的审批工具（异常路径）单次询问。"""
    tool = getattr(request, "tool", None)
    try:
        answer = interrupt(build_approval_payload(tool_name, tool_call, tool))
    except GraphInterrupt:
        raise
    except Exception:
        logger.warning("工具审批通道异常，按拒绝处理: tool=%s", tool_name, exc_info=True)
        return _deny_message(request, "审批通道不可用")
    if _resolve_decision(answer) != "deny":
        return await handler(request)
    return _deny_message(request, "用户拒绝了本次操作")


def create_tool_approval_middleware(
    tools: list[Any] | None = None,
    run_id: str | None = None,
) -> AgentMiddleware:
    """装配审批中间件；工具策略来自 metadata 的 ``approval=ask|allow``。"""
    labels: dict[str, str] = {}
    allowed_tools: set[str] = set()
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "")
        if not name:
            continue
        metadata = getattr(tool, "metadata", None)
        display = str(metadata.get("display_name") or "").strip() if isinstance(metadata, dict) else ""
        labels[name] = display or name
        approval = str(metadata.get("approval") or "").strip() if isinstance(metadata, dict) else ""
        if approval == "allow":
            allowed_tools.add(name)
    return ToolApprovalMiddleware(labels, run_id=run_id, allowed_tools=allowed_tools)
