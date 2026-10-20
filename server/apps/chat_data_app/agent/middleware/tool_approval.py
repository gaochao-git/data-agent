"""按需审批中间件：注册表标记为 ``approval_required`` 的工具在执行前中断询问用户。

设计要点（对应需求「按需审批 / 自动审批」两档）：

- 仅在 ``AgentRunControls.approval_mode == "ask"`` 时装配；``auto`` 直接执行；
- 需要审批的工具：内置注册表 ``ToolSpec.approval_required``（当前：文件删除/移动）
  + MCP 动态工具（不在注册表、第三方默认严格，按 ``mcp_`` 前缀统一 ask；
  ``mcp_tool_search`` 只读检索除外）；单点执行前裁决，不给每个工具写重复代码；
- 复用既有 LangGraph interrupt + pending-question/answer 通道：审批载荷是
  question 形状（允许 / 本次会话始终允许 / 拒绝），前端无需新增审批组件即可复用
  询问卡片；答案由 ``answer`` 路由原样 resume 回 ``interrupt``；
- "本次会话始终允许"写入会话 checkpoint（``approval_grants``），后续同类调用直接放行；
- 无人值守运行（定时任务/评测）由调用方强制 ``auto``，本中间件不装配——不会
  出现无人应答的悬挂；若未来被错误装配，审批通道不可用/取消时一律**拒绝**
  （fail-closed），而不是放行。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest, wrap_tool_call
from langchain_core.messages import ToolMessage
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
APPROVAL_ALWAYS_ALLOW_LABEL = "本次会话始终允许"
APPROVAL_DENY_LABEL = "拒绝"
_APPROVAL_QUESTION_ID = "approval"
_ARGS_PREVIEW_MAX_CHARS = 500


def _tool_args_preview(tool_call: Any) -> str:
    """工具参数摘要（脱敏不在此层做：当前仅文件工具，参数是工作区路径）：截断防超长。"""
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


def _grant_key(tool_name: str, tool: Any) -> str:
    """会话级许可的键：MCP 按 server 粒度（一个 server 的工具通常同源可信），
    内置工具按工具名粒度。键随会话 checkpoint 持久化，会话结束自然失效。"""
    server_id = _mcp_display_info(tool).get("mcp_server_id")
    if server_id:
        return f"mcp_server:{server_id}"
    return f"tool:{tool_name}"


def _always_allow_description(tool_name: str, tool: Any) -> str:
    server_id = _mcp_display_info(tool).get("mcp_server_id")
    if server_id:
        return f"本会话内 MCP server「{server_id}」的所有工具不再询问"
    return f"本会话内「{tool_name}」不再询问"


def build_approval_payload(tool_name: str, tool_call: Any, tool: Any = None) -> dict[str, Any]:
    """审批载荷：question 形状（复用询问卡片与 answer 路由），额外带 kind/tool 元信息。

    MCP 工具额外带 ``mcp_server_id`` / ``mcp_tool_name`` / ``display_name``，前端可
    展示「MCP server X · 工具 Y」而不是 ``mcp_<server>_<tool>`` 原始名。
    选项三态：允许（本次）/ 本次会话始终允许 / 拒绝。
    """
    preview = _tool_args_preview(tool_call)
    mcp_info = _mcp_display_info(tool)
    label = mcp_info.get("display_name") or mcp_info.get("mcp_tool_name") or tool_name
    question = f"是否允许执行 MCP 工具「{label}」？" if mcp_info else f"是否允许执行「{tool_name}」？"
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
                "options": [
                    {"label": APPROVAL_ALLOW_LABEL, "description": "执行本次操作"},
                    {
                        "label": APPROVAL_ALWAYS_ALLOW_LABEL,
                        "description": _always_allow_description(tool_name, tool),
                    },
                    {
                        "label": APPROVAL_DENY_LABEL,
                        "description": "不执行；模型会收到拒绝原因并调整方案",
                    },
                ],
                "multi_select": False,
            }
        ],
    }


def is_always_allowed(answer: Any) -> bool:
    """解析 resume 答案：显式选择「本次会话始终允许」时返回 True（含允许本次执行）。"""
    if not isinstance(answer, dict) or answer.get("cancelled") is True:
        return False
    answers = answer.get("answers")
    if not isinstance(answers, list):
        return False
    for item in answers:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != _APPROVAL_QUESTION_ID:
            continue
        selected = item.get("selected")
        return isinstance(selected, list) and APPROVAL_ALWAYS_ALLOW_LABEL in selected
    return False


def is_approval_allowed(answer: Any) -> bool:
    """解析 resume 回来的答案：只有显式选择「允许」才放行，其余一律拒绝。"""
    if not isinstance(answer, dict) or answer.get("cancelled") is True:
        return False
    answers = answer.get("answers")
    if not isinstance(answers, list):
        return False
    for item in answers:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") != _APPROVAL_QUESTION_ID:
            continue
        selected = item.get("selected")
        return isinstance(selected, list) and APPROVAL_ALLOW_LABEL in selected
    return False


def _append_grant(result: ToolMessage | Command[Any], grant_key: str) -> ToolMessage | Command[Any]:
    """在保留工具结果的同时，把"本次会话始终允许"写回会话 state。"""
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
    """当前会话是否已授予该工具/MCP server 的"始终允许"。"""
    grants = state.get("approval_grants") if isinstance(state, dict) else None
    return isinstance(grants, list) and grant_key in {str(grant) for grant in grants}


async def _run_with_approval(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
) -> ToolMessage | Command[Any]:
    """执行一次需审批的工具调用：会话许可 → 中断询问 → 放行/拒绝。"""
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = str(tool_call.get("name") or "")
    tool = getattr(request, "tool", None)
    grant_key = _grant_key(tool_name, tool)
    if _has_session_grant(getattr(request, "state", None), grant_key):
        return await handler(request)
    try:
        answer = interrupt(build_approval_payload(tool_name, tool_call, tool))
    except GraphInterrupt:
        # 正常中断路径：交给 LangGraph 挂起/恢复，绝不能吞掉
        raise
    except Exception:
        # 审批通道异常时必须 fail-closed，不能因为拿不到审批而放行高危操作
        logger.warning("工具审批通道异常，按拒绝处理: tool=%s", tool_name, exc_info=True)
        return _deny_message(request, "审批通道不可用")
    if is_always_allowed(answer):
        return _append_grant(await handler(request), grant_key)
    if is_approval_allowed(answer):
        return await handler(request)
    return _deny_message(request, "用户拒绝了本次操作")


def create_tool_approval_middleware() -> AgentMiddleware:
    """装配 ask 模式的审批拦截：命中审批工具先中断询问（带会话级始终允许）。"""

    @wrap_tool_call(name="ToolApprovalMiddleware")
    async def tool_approval_middleware(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        tool_name = str(tool_call.get("name") or "")
        if not requires_approval(tool_name):
            return await handler(request)
        return await _run_with_approval(request, handler)

    return tool_approval_middleware
