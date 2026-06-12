from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from apps.complaint_app.core.llm.llm_client import AsyncLLMClient
from utils.logger import get_logger

from .config import RuntimeConfig
from .tools.base_tool import Tool
from .tools.registry import ToolRegistry

logger = get_logger(__name__)


def _trace_enabled() -> bool:
    return os.getenv("OPENCODE_RUNTIME_TRACE", "").strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class PlanStep:
    """计划步骤定义。"""
    """计划步骤定义。

    - step_id: 步骤唯一标识
    - tool: 使用的工具名称
    - inputs: 工具输入参数
    - depends_on: 依赖的前置步骤 ID 列表
    - kind: 步骤类型（tool/skill/agent）
    - description: 可选的人类可读描述
    """
    step_id: str
    tool: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    kind: str = "tool"
    description: str = ""


@dataclass
class PlanResult:
    """计划结果（执行模式 + 步骤列表）。"""
    """计划结果（执行模式 + 步骤列表）。

    - mode: 执行模式（single/sequential/parallel）
    - steps: 计划步骤列表
    - reason: 生成原因或错误原因标记
    - max_parallel: 并行执行上限
    """
    mode: str  # single | sequential | parallel
    steps: List[PlanStep] = field(default_factory=list)
    reason: str = ""
    max_parallel: int = 3


class Planner:
    """任务规划器（LLM 生成执行计划）。"""

    def __init__(self, *, llm_client: AsyncLLMClient | None = None, model: str | None = None, max_parallel: int | None = None) -> None:
        cfg = RuntimeConfig.from_env()
        self._max_parallel = max(1, int(max_parallel or cfg.max_parallel_tasks or 3))
        self._llm = llm_client or AsyncLLMClient(name="planner_llm")
        self._model = model

    def _resolve_model(self) -> str | None:
        """获取规划器模型名称（优先构造时传入，其次环境变量）。"""
        if self._model:
            return self._model
        return (
            os.getenv("OPENCODE_PLANNER_MODEL")
            or os.getenv("OPENAI_ANALYSIS_MODEL")
            or os.getenv("COMPLAINT_FC_MODEL")
            or os.getenv("OPENAI_SUGGEST_MODEL")
        )

    def _tool_kind(self, tool: Tool) -> str:
        if tool.name == "task":
            return "agent"
        if tool.name == "skill":
            return "skill"
        return "tool"

    def _tool_catalog(self, tools: Iterable[Tool]) -> List[Dict[str, Any]]:
        catalog: List[Dict[str, Any]] = []
        for tool in tools:
            catalog.append(
                {
                    "name": tool.name,
                    "kind": self._tool_kind(tool),
                    "description": tool.description or "",
                    "parameters": tool.metadata.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        return catalog

    def _system_prompt(self, tool_catalog: List[Dict[str, Any]]) -> str:
        return (
            "你是一个计划编排器。根据用户任务与可用工具生成执行计划。"
            "输出必须是 JSON 对象，禁止附加解释文字或代码块。\n\n"
            "规则：\n"
            f"- 可并行执行的步骤最多 {self._max_parallel} 个。\n"
            "- 如果步骤依赖前一步输出，必须在 depends_on 中声明依赖。\n"
            "- 若需要引用上一步输出，请在 inputs 中使用：{\"from\": \"<step_id>\", \"path\": \"output\"}。\n"
            "- complaint_analysis 的 source_text 必须来自摘要或用户已提供的文本；若无摘要，必须先生成摘要。\n"
            "- 生成摘要前若无可用文本，必须先使用 complaint_text_extract 提取文本。\n"
            "- 生成摘要的 task 步骤必须提供 source_text，并引用提取步骤输出。\n"
            "- 只能使用下方 tools 列表中的工具名称。\n\n"
            "JSON 结构：\n"
            "{\n"
            '  \"mode\": \"single|sequential|parallel\",\n'
            '  \"steps\": [\n'
            "    {\n"
            '      \"step_id\": \"unique_id\",\n'
            '      \"tool\": \"tool_name\",\n'
            '      \"kind\": \"tool|skill|agent\",\n'
            '      \"inputs\": {\"key\": \"value\"},\n'
            '      \"depends_on\": [\"step_id\"],\n'
            '      \"description\": \"optional\"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            f"tools: {json.dumps(tool_catalog, ensure_ascii=False)}"
        )

    def _extract_json(self, text: str) -> str | None:
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.split("\n", 1)[-1]
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return cleaned[start: end + 1]

    def _parse_plan(self, content: str) -> Dict[str, Any] | None:
        raw = self._extract_json(content)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception as exc:
            logger.warning("[planner] parse_plan_failed err=%s raw=%s", exc, raw)
            return None

    def _normalize_plan(self, raw: Dict[str, Any], tools: Iterable[Tool]) -> PlanResult:
        tool_names = {tool.name for tool in tools}
        mode = str(raw.get("mode") or "single").strip().lower()
        if mode not in {"single", "sequential", "parallel"}:
            mode = "single"
        raw_steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
        steps: List[PlanStep] = []
        for idx, item in enumerate(raw_steps):
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if not tool or tool not in tool_names:
                continue
            step_id = str(item.get("step_id") or f"step_{idx + 1}")
            inputs = item.get("inputs") if isinstance(item.get("inputs"), dict) else {}
            depends_on = item.get("depends_on") if isinstance(item.get("depends_on"), list) else []
            depends = [str(dep) for dep in depends_on if isinstance(dep, str)]
            kind = str(item.get("kind") or "tool")
            description = str(item.get("description") or "")
            steps.append(
                PlanStep(
                    step_id=step_id,
                    tool=tool,
                    inputs=inputs,
                    depends_on=depends,
                    kind=kind,
                    description=description,
                )
            )

        if not steps:
            return PlanResult(mode="single", reason="empty_plan", max_parallel=self._max_parallel)

        has_dependency = any(step.depends_on for step in steps)
        if mode == "single" and len(steps) > 1:
            mode = "sequential" if has_dependency else "parallel"

        return PlanResult(mode=mode, steps=steps, reason="llm_plan", max_parallel=self._max_parallel)

    def _ensure_dependency_rules(self, plan: PlanResult, *, context: Dict[str, Any], tools: Iterable[Tool], ) -> PlanResult:
        tool_names = {tool.name for tool in tools}
        steps = list(plan.steps)
        step_index = {step.step_id: step for step in steps}

        def _ensure_step(step: PlanStep) -> None:
            if step.step_id not in step_index:
                steps.insert(0, step)
                step_index[step.step_id] = step

        def _find_summary_step() -> PlanStep | None:
            for step in steps:
                if step.tool == "task" and str(step.inputs.get("subagent_type") or "") == "complaint_summary":
                    return step
            return None

        def _find_extract_step() -> PlanStep | None:
            for step in steps:
                if step.tool == "complaint_text_extract":
                    return step
            return None

        summary_text = context.get("summary") or context.get("source_text")
        attachment_path = context.get("attachment_path")
        attachments = context.get("attachments") if isinstance(context.get("attachments"), list) else []
        base_id = context.get("base_id")
        user_id = context.get("user_id") or "cli"

        for step in steps:
            if step.tool != "complaint_analysis":
                continue
            source_text = step.inputs.get("source_text")
            if isinstance(source_text, dict) and source_text.get("from"):
                continue
            if isinstance(source_text, str) and source_text.strip():
                continue

            if summary_text:
                step.inputs["source_text"] = summary_text
                continue

            summary_step = _find_summary_step()
            if not summary_step and "task" in tool_names:
                summary_step = PlanStep(
                    step_id="summary",
                    tool="task",
                    kind="agent",
                    inputs={
                        "description": "举报摘要",
                        "prompt": "根据提供的举报原文生成结构化摘要，包含举报人、被举报人、事情经过、举报事项等信息。",
                        "subagent_type": "complaint_summary",
                    },
                    description="生成举报摘要",
                )
                _ensure_step(summary_step)

            if summary_step:
                if user_id and not summary_step.inputs.get("user_id"):
                    summary_step.inputs["user_id"] = user_id
                if base_id and not summary_step.inputs.get("base_id"):
                    summary_step.inputs["base_id"] = base_id
                if "summary" not in step.depends_on:
                    step.depends_on.append(summary_step.step_id)
                step.inputs["source_text"] = {"from": summary_step.step_id, "path": "output"}
                if plan.mode != "sequential":
                    plan.mode = "sequential"

                has_text = bool(summary_text)
                extract_step = _find_extract_step()
                if not has_text and not extract_step and "complaint_text_extract" in tool_names:
                    file_path = attachment_path or (attachments[0] if attachments else "")
                    extract_step = PlanStep(
                        step_id="text_extract",
                        tool="complaint_text_extract",
                        kind="tool",
                        inputs={
                            "user_id": user_id,
                            "input_file_path": file_path,
                            "base_id": base_id,
                        },
                        description="提取举报件原文文本",
                    )
                    _ensure_step(extract_step)
                if extract_step:
                    if extract_step.step_id not in summary_step.depends_on:
                        summary_step.depends_on.append(extract_step.step_id)
                    if not summary_step.inputs.get("source_text"):
                        summary_step.inputs["source_text"] = {"from": extract_step.step_id, "path": "output"}

        return PlanResult(
            mode=plan.mode,
            steps=steps,
            reason=plan.reason,
            max_parallel=plan.max_parallel,
        )

    async def plan(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> PlanResult:
        """通过 LLM 生成执行计划（工具编排）。"""
        text = (user_input or "").strip()
        if not text:
            return PlanResult(mode="single", reason="empty_input", max_parallel=self._max_parallel)

        tools = ToolRegistry.registry()
        tool_catalog = self._tool_catalog(tools)
        system_prompt = self._system_prompt(tool_catalog)
        payload = {"task": text, "context": context or {}, "max_parallel": self._max_parallel}
        if _trace_enabled():
            logger.info("[planner] payload=%s", json.dumps(payload, ensure_ascii=False))
            logger.info("[planner] tools=%s", [tool.name for tool in tools])
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        logger.info("=====planner messages begins=======")
        logger.info(json.dumps(messages, ensure_ascii=False))
        logger.info("=====planner messages end=======")
        model = self._resolve_model()
        if not model:
            return PlanResult(mode="single", reason="missing_model", max_parallel=self._max_parallel)
        response = await self._llm.chat(messages=messages, model=model)
        choices = response.choices if hasattr(response, "choices") else []
        content = ""
        if choices:
            message = choices[0].message
            content = getattr(message, "content", "") or ""
        if _trace_enabled():
            logger.info("[planner] raw_response=%s", content)
        raw_plan = self._parse_plan(content)
        if not raw_plan:
            return PlanResult(mode="single", reason="parse_error", max_parallel=self._max_parallel)
        if _trace_enabled():
            logger.info("[planner] parsed_plan=%s", json.dumps(raw_plan, ensure_ascii=False))
        plan = self._normalize_plan(raw_plan, tools)
        return self._ensure_dependency_rules(plan, context=context or {}, tools=tools)


__all__ = ["Planner", "PlanResult", "PlanStep"]
