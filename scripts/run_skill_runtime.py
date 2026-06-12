from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid


def _ensure_pythonpath() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    server_path = os.path.join(root, "server")
    if server_path not in sys.path:
        sys.path.insert(0, server_path)


def _load_env() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    server_env = os.path.join(root, "server", ".env")
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    if os.path.exists(server_env):
        try:
            with open(server_env, "r", encoding="utf-8") as f:
                content = f.read()
            has_openai_key = any(
                line.strip().startswith("OPENAI_API_KEY=") for line in content.splitlines() if line.strip() and not line.strip().startswith("#")
            )
            if has_openai_key and "OPENAI_API_KEY" in os.environ:
                os.environ.pop("OPENAI_API_KEY", None)
        except Exception:
            pass
        load_dotenv(server_env, override=True)


def _read_source_text(text: str | None, path: str | None) -> str:
    if text and path:
        raise ValueError("Only one of --source-text or --source-text-file can be provided.")
    if path:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return text or ""


def _redact_step_outputs(step_outputs: object) -> object:
    if not isinstance(step_outputs, dict):
        return step_outputs
    redacted: dict[str, object] = {}
    for step_id, payload in step_outputs.items():
        if isinstance(payload, dict) and payload.get("title") == "complaint_text_extract":
            masked = dict(payload)
            output = masked.get("output") or ""
            if isinstance(output, str):
                masked["output"] = f"[omitted len={len(output)}]"
            else:
                masked["output"] = "[omitted]"
            redacted[step_id] = masked
        else:
            redacted[step_id] = payload
    return redacted


async def _run_skill_once(
    *,
    skill_name: str,
    prompt: str,
    source_text: str,
    show_plan: bool,
    show_steps: bool,
    agent_name: str | None,
) -> None:
    from apps.complaint_app.opencode_runtime.main_agent import MainAgent
    from apps.complaint_app.opencode_runtime.planner import PlanResult, PlanStep
    from apps.complaint_app.opencode_runtime.session import Session

    agent = MainAgent()
    session = Session(session_id=str(uuid.uuid4()))

    inputs = {"name": skill_name}
    if source_text:
        inputs["source_text"] = source_text

    plan = PlanResult(
        mode="sequential",
        steps=[
            PlanStep(
                step_id="run_skill",
                tool="skill",
                kind="skill",
                inputs=inputs,
                description=f"run skill {skill_name}",
            )
        ],
        reason="manual_skill",
        max_parallel=1,
    )

    if show_plan:
        print("[plan]", plan)

    outputs = await agent._execute_plan(plan, session, agent_name=agent_name, user_input=prompt)
    if show_steps:
        print("[steps]", _redact_step_outputs(outputs))
    final_text = (outputs.get("run_skill") or {}).get("output") or ""
    print(final_text)


def main() -> None:
    _ensure_pythonpath()
    _load_env()

    parser = argparse.ArgumentParser(description="Run a single opencode skill via CLI.")
    parser.add_argument("--skill", "-s", type=str, required=True, help="Skill name to execute")
    parser.add_argument("--prompt", "-p", type=str, default="", help="User prompt for the skill")
    parser.add_argument("--source-text", type=str, default="", help="Inline source text passed to the skill")
    parser.add_argument("--source-text-file", type=str, default="", help="Path to a file containing source text")
    parser.add_argument("--show-plan", action="store_true", help="Print the generated plan")
    parser.add_argument("--show-steps", action="store_true", help="Print per-step outputs")
    parser.add_argument("--agent-name", type=str, default="", help="Agent name for tool context (optional)")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("[warn] OPENAI_API_KEY 未配置，LLM 可能无法运行")

    source_text = _read_source_text(args.source_text.strip() or None, args.source_text_file.strip() or None)
    prompt = args.prompt.strip()
    if not prompt and not source_text:
        raise ValueError("prompt or source_text is required")
    if not prompt:
        prompt = "请基于提供的材料执行该技能。"

    asyncio.run(
        _run_skill_once(
            skill_name=args.skill.strip(),
            prompt=prompt,
            source_text=source_text,
            show_plan=bool(args.show_plan),
            show_steps=bool(args.show_steps),
            agent_name=args.agent_name.strip() or None,
        )
    )


if __name__ == "__main__":
    main()
