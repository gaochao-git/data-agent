from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

from .agents.agents import AgentRegistry
from .context_manager import ContextManager
from .planner import PlanResult, PlanStep, Planner
from .session import Message, Session
from .session_prompt import PromptResult, SessionPrompt
from .tools.base_tool import ToolContext, ToolExecutor
from .tools.registry import ToolRegistry
from apps.complaint_app.core.model_config import get_chat_model, get_skill_model
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MainAgentRequest:
    """主智能体请求参数。"""

    user_input: str
    session: Session | None = None
    system_prompt: str | None = None
    model: str | None = None
    agent_name: str | None = None
    context: Dict[str, Any] | None = None


@dataclass
class MainAgentResult:
    """主智能体执行结果。"""

    session: Session
    plan: PlanResult
    message: Message | None
    text: str
    metadata: Dict[str, Any]


class MainAgent:
    """主智能体：负责规划、压缩与单轮执行。"""

    def __init__(self, *, planner: Planner | None = None, context_manager: ContextManager | None = None,
                 session_prompt: SessionPrompt | None = None, tool_executor: ToolExecutor | None = None, ) -> None:
        self._planner = planner or Planner()
        self._context = context_manager or ContextManager()
        self._prompt = session_prompt or SessionPrompt()
        self._executor = tool_executor or ToolExecutor()
        if tool_executor is None:
            for tool in ToolRegistry.registry():
                self._executor.register(tool)
        self._logger = get_logger(__name__)

    def _ensure_session(self, session: Session | None) -> Session:
        """确保 session 存在。"""
        if session:
            return session
        return Session(session_id=str(uuid.uuid4()))

    def _resolve_agent(self, agent_name: str | None) -> str:
        """确定执行使用的 agent 名称。"""
        if agent_name:
            return agent_name
        return AgentRegistry.default_agent()

    def _resolve_system_prompt(self, agent_name: str | None, override: str | None) -> str | None:
        """获取 system prompt（优先使用外部传入）。"""
        if override:
            return override
        if not agent_name:
            return None
        agent = AgentRegistry.get(agent_name)
        return agent.prompt if agent else None

    def _trace_enabled(self) -> bool:
        return os.getenv("OPENCODE_RUNTIME_TRACE", "").strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _contains_cjk(text: str | None) -> bool:
        if not text:
            return False
        for char in text:
            if "\u4e00" <= char <= "\u9fff":
                return True
        return False

    def _resolve_input_value(self, value: Any, outputs: Dict[str, Dict[str, Any]]) -> Any:
        """解析输入引用。"""
        if isinstance(value, dict) and "from" in value:
            source = value.get("from")
            path = value.get("path", "output")
            if source in outputs:
                return outputs[source].get(path)
            return None
        return value

    def _resolve_inputs(self, inputs: Dict[str, Any], outputs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """解析 step 输入。"""
        resolved: Dict[str, Any] = {}
        for key, value in (inputs or {}).items():
            resolved[key] = self._resolve_input_value(value, outputs)
        return resolved

    async def _execute_step(self, step: PlanStep, session: Session, outputs: Dict[str, Dict[str, Any]], agent_name: str | None, user_input: str, ) -> Dict[str, Any]:
        """执行单个计划步骤。"""
        inputs = self._resolve_inputs(step.inputs, outputs)
        if self._trace_enabled():
            self._logger.info("[main_agent] execute_step=%s tool=%s inputs=%s", step.step_id, step.tool, inputs)
        if step.tool == "skill":
            output_payload = await self._execute_skill_step(step, session, inputs, outputs, agent_name, user_input)
        else:
            ctx = ToolContext(session_id=session.session_id, message_id=str(uuid.uuid4()), agent_name=agent_name)
            result = self._executor.execute(step.tool, context=ctx, payload=inputs)
            if hasattr(result, "__await__"):
                result = await result
            output_payload = result if isinstance(result, dict) else {"output": result}
        outputs[step.step_id] = output_payload
        output_text = output_payload.get("output") if isinstance(output_payload, dict) else str(result)
        if output_text is None:
            output_text = json.dumps(output_payload, ensure_ascii=False, default=str)
        if step.tool != "skill":
            self._context.append_tool_message(session, tool_name=step.tool, output=str(output_text), tool_call_id=ctx.message_id)
        if self._trace_enabled():
            self._logger.info("[main_agent] step_result=%s output=%s", step.step_id, _safe_string(output_payload))
        return output_payload

    async def _execute_skill_step(self, step: PlanStep, session: Session, inputs: Dict[str, Any], outputs: Dict[str, Dict[str, Any]], agent_name: str | None, user_input: str, ) -> Dict[str, Any]:
        """执行 skill 步骤：加载技能后进入工具循环。"""
        ctx = ToolContext(session_id=session.session_id, message_id=str(uuid.uuid4()), agent_name=agent_name)
        result = self._executor.execute("skill", context=ctx, payload=inputs)
        if hasattr(result, "__await__"):
            result = await result
        output_payload = result if isinstance(result, dict) else {"output": result}
        skill_output = str(output_payload.get("output") or "")
        metadata = output_payload.get("metadata") if isinstance(output_payload, dict) else {}
        allowed_tools = _normalize_allowed_tools(metadata)
        if allowed_tools:
            allowed_tools = [name for name in allowed_tools if name != "skill"]

        source_text = str(inputs.get("source_text") or "")
        if not source_text and step.depends_on:
            last_dep = step.depends_on[-1]
            source_text = str((outputs.get(last_dep) or {}).get("output") or "")
        skill_user_input = (user_input or "").strip()
        if source_text:
            skill_user_input = "\n\n".join(
                [
                    skill_user_input,
                    "<source_text>",
                    source_text,
                    "</source_text>",
                ]
            ).strip()
        if self._contains_cjk(user_input) or self._contains_cjk(source_text) or self._contains_cjk(skill_output):
            skill_user_input = "\n\n".join(
                [
                    skill_user_input,
                    "【输出语言要求】请仅使用简体中文输出最终结果；不要输出英文段落（法规名称保留中文原文即可）。",
                ]
            ).strip()

        model_name = get_skill_model()
        skill_session = Session(session_id=str(uuid.uuid4()))
        prompt_result = await self._prompt.prompt(
            session=skill_session,
            user_input=skill_user_input,
            system_prompt=skill_output or None,
            model=model_name,
            agent_name=None,
            allowed_tools=allowed_tools,
            max_tool_rounds=50,
            record_messages=False,
        )
        output_text = ""
        if prompt_result.message and prompt_result.message.parts:
            output_text = prompt_result.message.parts[-1].content
        shell_commands = _extract_shell_commands(prompt_result.tool_results)
        prompt_stats = prompt_result.stats if isinstance(prompt_result.stats, dict) else {}
        merged_metadata = metadata if isinstance(metadata, dict) else {}
        if prompt_stats:
            merged_metadata = dict(merged_metadata)
            for key in ("tool_rounds", "parallel_rounds", "shell_call_count"):
                if key in prompt_stats:
                    merged_metadata[key] = prompt_stats.get(key)
        if shell_commands:
            merged_metadata = dict(merged_metadata)
            merged_metadata["tool_commands"] = shell_commands
            if "shell_call_count" not in merged_metadata:
                merged_metadata["shell_call_count"] = len(shell_commands)
        return {
            "title": output_payload.get("title") if isinstance(output_payload, dict) else "skill",
            "output": output_text,
            "metadata": merged_metadata or {},
        }

    async def _execute_plan(self, plan: PlanResult, session: Session, agent_name: str | None, user_input: str, ) -> Dict[str, Dict[str, Any]]:
        """按计划执行工具步骤（支持并行）。"""
        outputs: Dict[str, Dict[str, Any]] = {}
        pending = {step.step_id: step for step in plan.steps}
        order = {step.step_id: idx for idx, step in enumerate(plan.steps)}
        if self._trace_enabled():
            self._logger.info("[main_agent] plan_mode=%s max_parallel=%s", plan.mode, plan.max_parallel)

        while pending:
            ready: List[PlanStep] = []
            for step in pending.values():
                if all(dep in outputs for dep in step.depends_on):
                    ready.append(step)
            if not ready:
                break
            ready.sort(key=lambda item: order.get(item.step_id, 0))

            if plan.mode == "sequential":
                ready = ready[:1]
            else:
                ready = ready[: max(1, plan.max_parallel)]
            if self._trace_enabled():
                self._logger.info("[main_agent] ready_steps=%s", [step.step_id for step in ready])

            tasks = [self._execute_step(step, session, outputs, agent_name, user_input) for step in ready]
            if len(tasks) == 1:
                await tasks[0]
            else:
                await asyncio.gather(*tasks)

            for step in ready:
                pending.pop(step.step_id, None)

        return outputs

    async def run(self, request: MainAgentRequest) -> MainAgentResult:
        """执行主流程（计划编排 + 工具调用）。"""
        # 1) 确保有可用会话（用于承载 message history / compact / 追加新消息等）。
        session = self._ensure_session(request.session)
        if self._trace_enabled():
            self._logger.info("[main_agent] request=%s", _safe_string(_preview_request(request)))

        # 2) 组装 planner 所需上下文；若调用方未显式传入 messages，则回填会话历史。
        context = dict(request.context or {})
        if "messages" not in context:
            context["messages"] = [msg.to_dict() for msg in session.messages]
        if self._trace_enabled():
            messages = context.get("messages")
            count = len(messages) if isinstance(messages, list) else 0
            self._logger.info("[main_agent] context_messages_count=%s", count)
            self._logger.info("[main_agent] context_messages_preview=%s", _safe_string(_preview_messages(messages)))

        # 3) 规划阶段：基于 user_input + context 生成执行步骤（可能为空）。
        plan = await self._planner.plan(request.user_input, context)
        if self._trace_enabled():
            self._logger.info("[main_agent] planner_result=%s", plan)

        # 4) 解析 agent 与系统提示词（在非 plan 模式下会直接用于 prompt）。
        agent_name = self._resolve_agent(request.agent_name)
        system_prompt = self._resolve_system_prompt(agent_name, request.system_prompt)
        if self._trace_enabled():
            prompt_source = "override" if (request.system_prompt and str(request.system_prompt).strip()) else "agent_registry"
            self._logger.info("[main_agent] resolved_agent=%s request_agent=%s system_prompt_source=%s system_prompt_preview=%s", agent_name, request.agent_name, prompt_source, _preview_text(system_prompt))

        # 5) 执行前先做一次 compact，尽量避免长对话导致上下文溢出。
        model_name = request.model or get_chat_model()
        self._context.compact_if_needed(session, model=model_name)
        result: PromptResult | None = None

        # step_id -> {title, output, metadata}
        outputs: Dict[str, Dict[str, Any]] = {}

        if plan.steps:
            # 6a) Plan 模式：先记录用户消息，再按计划执行每一步（支持并行/串行）。
            self._context.append_user_message(session, request.user_input)
            outputs = await self._execute_plan(plan, session, agent_name, request.user_input)

            # 计划约定：最后一步的 output 作为最终答复内容。
            final_text = outputs.get(plan.steps[-1].step_id, {}).get("output", "")
            result = PromptResult(message=None, tool_results=[])
            result.message = self._context.append_assistant_message(session, str(final_text))
        else:
            # 6b) 直出模式：不走 planner，直接调用大模型生成回答并记录消息。
            result = await self._prompt.prompt(
                session=session,
                user_input=request.user_input,
                system_prompt=system_prompt,
                model=request.model,
                agent_name=agent_name,
                record_messages=True,
            )

        # 7) 执行结束后再 compact 一次，控制会话体积，便于后续轮次继续使用。
        self._context.compact_if_needed(session, model=model_name)

        # 8) 从 message parts 中提取最终展示文本（默认取最后一个 part）。
        text = ""
        if result.message and result.message.parts:
            text = result.message.parts[-1].content

        # 9) 返回结构化结果：plan/消息/文本/以及每步 outputs 的调试信息。
        return MainAgentResult(
            session=session,
            plan=plan,
            message=result.message,
            text=text,
            metadata={"executed": True, "agent": agent_name, "outputs": outputs},
        )


def _safe_string(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning("[main_agent] safe_string_failed type=%s err=%s", type(value).__name__, exc)
        return str(value)


def _normalize_allowed_tools(metadata: Any) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    allowed = metadata.get("allowed_tools") or metadata.get("allowed-tools")
    if isinstance(allowed, list):
        return [str(item).strip() for item in allowed if str(item).strip()]
    if isinstance(allowed, str):
        return [part.strip() for part in allowed.split(",") if part.strip()]
    return []


def _extract_shell_commands(tool_results: Any) -> list[str]:
    if not isinstance(tool_results, list):
        return []
    commands: list[str] = []
    for item in tool_results:
        name = str(getattr(item, "name", "") or "")
        if name != "shell":
            continue
        output = getattr(item, "output", None)
        command = _extract_command_from_tool_output(output)
        if command:
            commands.append(command)
    return commands


def _extract_command_from_tool_output(output: Any) -> str:
    if isinstance(output, dict):
        metadata = output.get("metadata")
        if isinstance(metadata, dict):
            command = metadata.get("command")
            if command is not None:
                return str(command).strip()
        command = output.get("command")
        if command is not None:
            return str(command).strip()
    return ""


def _preview_messages(messages: Any, *, limit: int = 20, part_limit: int = 3, part_chars: int = 160) -> Any:
    """将 messages 截断为可打印预览，避免 trace 日志过大。"""
    if not isinstance(messages, list):
        return messages
    sliced = messages[-limit:] if limit > 0 else messages
    preview: list[dict[str, Any]] = []
    for item in sliced:
        if not isinstance(item, dict):
            preview.append({"raw": str(item)})
            continue
        parts = item.get("parts") if isinstance(item.get("parts"), list) else []
        part_preview: list[dict[str, Any]] = []
        for part in parts[: max(0, part_limit)]:
            if not isinstance(part, dict):
                continue
            content = str(part.get("content") or "")
            if part_chars > 0 and len(content) > part_chars:
                content = content[:part_chars] + "..."
            part_preview.append({"kind": part.get("kind"), "content": content})
        preview.append({"id": item.get("id"), "role": item.get("role"), "parts": part_preview})
    return preview


def _preview_request(request: MainAgentRequest) -> Dict[str, Any]:
    """构造 request 的日志预览，避免直接打印超大上下文。"""
    context = request.context if isinstance(request.context, dict) else {}
    context_preview: Dict[str, Any] = {}
    for key, value in context.items():
        if key == "messages":
            context_preview[key] = _preview_messages(value)
            continue
        context_preview[key] = _preview_text(_safe_string(value), chars=240)
    return {
        "agent_name": request.agent_name,
        "model": request.model,
        "system_prompt": _preview_text(request.system_prompt, chars=240),
        "user_input": _preview_text(request.user_input, chars=600),
        "session_id": request.session.session_id if request.session else None,
        "context": context_preview,
    }


def _preview_text(text: Any, *, chars: int = 240) -> str:
    """截断文本预览，避免 trace 日志过大。"""
    if text is None:
        return ""
    value = str(text)
    if chars > 0 and len(value) > chars:
        return value[:chars] + "..."
    return value


__all__ = ["MainAgent", "MainAgentRequest", "MainAgentResult"]
