"""按需审批中间件：注册表标记为 ``approval_required`` 的工具在执行前中断询问用户。

设计要点（对应需求「按需审批 / 自动审批」两档）：

- 仅在 ``AgentRunControls.approval_mode == "ask"`` 时装配；``auto`` 直接执行；
- 需要审批的工具来自注册表 ``ToolSpec.approval_required``（当前：文件删除/移动），
  中间件按工具名匹配，单点执行前裁决，不给每个工具写重复代码；
- 复用既有 LangGraph interrupt + pending-question/answer 通道：审批载荷是
  question 形状（一个二元问题「允许/拒绝」），前端无需新增审批组件即可复用
  询问卡片；答案由 ``answer`` 路由原样 resume 回 ``interrupt``；
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

# 审批选项文案：与前端询问卡片渲染一致；答案解析按 label 匹配（严格白名单）
APPROVAL_ALLOW_LABEL = "允许"
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


def build_approval_payload(tool_name: str, tool_call: Any) -> dict[str, Any]:
    """审批载荷：question 形状（复用询问卡片与 answer 路由），额外带 kind/tool 元信息。"""
    preview = _tool_args_preview(tool_call)
    return {
        "question_id": uuid4().hex,
        "kind": "approval",
        "tool_name": tool_name,
        "tool_args_preview": preview,
        "questions": [
            {
                "id": _APPROVAL_QUESTION_ID,
                "header": "需要你的确认",
                "question": f"是否允许执行「{tool_name}」？",
                "options": [
                    {"label": APPROVAL_ALLOW_LABEL, "description": "执行本次操作"},
                    {
                        "label": APPROVAL_DENY_LABEL,
                        "description": "不执行；模型会收到拒绝原因并调整方案",
                    },
                ],
                "multi_select": False,
            }
        ],
    }


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


def _deny_message(request: ToolCallRequest, reason: str) -> ToolMessage:
    tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
    tool_name = str(tool_call.get("name") or "")
    return ToolMessage(
        content=f"操作未获用户批准，已拒绝执行工具「{tool_name}」：{reason}",
        name=tool_name,
        tool_call_id=str(tool_call.get("id") or ""),
        status="error",
    )


def create_tool_approval_middleware() -> AgentMiddleware:
    """装配 ask 模式的审批拦截：命中注册表标记的工具先中断询问。"""

    @wrap_tool_call(name="ToolApprovalMiddleware")
    async def tool_approval_middleware(
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call if isinstance(request.tool_call, dict) else {}
        tool_name = str(tool_call.get("name") or "")
        if tool_name not in APPROVAL_REQUIRED_TOOL_NAMES:
            return await handler(request)
        try:
            answer = interrupt(build_approval_payload(tool_name, tool_call))
        except GraphInterrupt:
            # 正常中断路径：交给 LangGraph 挂起/恢复，绝不能吞掉
            raise
        except Exception:
            # 审批通道异常时必须 fail-closed，不能因为拿不到审批而放行高危操作
            logger.warning("工具审批通道异常，按拒绝处理: tool=%s", tool_name, exc_info=True)
            return _deny_message(request, "审批通道不可用")
        if is_approval_allowed(answer):
            return await handler(request)
        return _deny_message(request, "用户拒绝了本次操作")

    return tool_approval_middleware
