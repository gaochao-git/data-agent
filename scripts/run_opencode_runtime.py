from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from urllib.parse import urlparse


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


async def _run_once(prompt: str, *, show_plan: bool, planner_model: str | None, attachment: str | None) -> None:
    from apps.complaint_app.opencode_runtime.main_agent import MainAgent, MainAgentRequest
    from apps.complaint_app.opencode_runtime.planner import Planner
    from apps.complaint_app.opencode_runtime.session import Session

    planner = Planner(model=planner_model) if planner_model else Planner()
    agent = MainAgent(planner=planner)
    session = Session(session_id=str(uuid.uuid4()))
    context = {}
    if attachment:
        context["attachment_path"] = attachment
    result = await agent.run(MainAgentRequest(user_input=prompt, session=session, context=context))

    if show_plan:
        print("[plan]", result.plan)
    print(result.text)


async def _repl(show_plan: bool, planner_model: str | None, attachment: str | None) -> None:
    from apps.complaint_app.opencode_runtime.main_agent import MainAgent, MainAgentRequest
    from apps.complaint_app.opencode_runtime.planner import Planner
    from apps.complaint_app.opencode_runtime.session import Session

    planner = Planner(model=planner_model) if planner_model else Planner()
    agent = MainAgent(planner=planner)
    session = Session(session_id=str(uuid.uuid4()))

    print("opencode_runtime CLI. 输入 :exit 退出, :reset 重置会话")
    while True:
        try:
            line = input(">> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line in {":exit", ":quit", ":q"}:
            break
        if line == ":reset":
            session = Session(session_id=str(uuid.uuid4()))
            print("[session] reset")
            continue

        context = {}
        if attachment:
            context["attachment_path"] = attachment
        result = await agent.run(MainAgentRequest(user_input=line, session=session, context=context))
        if show_plan:
            print("[plan]", result.plan)
        print(result.text)


def _build_api_payload(
    prompt: str,
    *,
    conversation_id: str | None,
    show_plan: bool,
    show_steps: bool,
    model_name: str,
    need_deep_think: bool,
    engine: str,
) -> dict:
    content_block = {
        "block_type": 10001,
        "content": {
            "text_block": {
                "text": prompt,
                "icon_url": "",
                "icon_url_dark": "",
                "summary": "",
            }
        },
        "block_id": str(uuid.uuid4()),
        "meta_info": [],
    }
    payload = {
        "client_meta": {
            "agent_id": "complaint_agent",
            "conversation_id": conversation_id or str(uuid.uuid4()),
            "last_message_index": 0,
            "engine": engine,
            "show_plan": bool(show_plan),
            "show_steps": bool(show_steps),
        },
        "messages": [
            {
                "content_block": [content_block],
                "model_id": model_name,
                "model_name": model_name,
                "need_deep_think": 1 if need_deep_think else 0,
            }
        ],
        "option": {"answer_with_suggest": False},
        "chat_ability": {"ability_type": 6, "ability_param": "{\"action\":3}"},
    }
    return payload


def _api_base_from_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return api_url.rsplit("/api/chat/completion", 1)[0].rstrip("/")


def _create_api_session(
    api_url: str,
    *,
    auth_token: str | None,
    user_id: str | None,
    user_name: str | None,
):
    import requests

    session = requests.Session()
    if auth_token:
        session.headers.update({"Authorization": f"Bearer {auth_token}"})
    if user_id and user_name:
        base = _api_base_from_url(api_url)
        login_url = f"{base}/api/inner_login"
        resp = session.get(login_url, params={"userid": user_id, "username": user_name}, timeout=30)
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(payload.get("error"))
    return session


def _post_api(
    session,
    api_url: str,
    payload: dict,
    *,
    attachment: str | None,
) -> dict:
    if attachment:
        with open(attachment, "rb") as handle:
            files = {"file": (os.path.basename(attachment), handle)}
            data = {"payload": json.dumps(payload, ensure_ascii=False)}
            resp = session.post(api_url, data=data, files=files, timeout=3600)
    else:
        resp = session.post(api_url, json=payload, timeout=3600)
    resp.raise_for_status()
    return resp.json()


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


def _run_api_once(
    prompt: str,
    *,
    show_plan: bool,
    show_steps: bool,
    attachment: str | None,
    api_url: str,
    conversation_id: str | None,
    session,
    model_name: str,
    need_deep_think: bool,
    engine: str,
) -> str | None:
    effective_prompt = prompt.strip()
    if not effective_prompt and attachment:
        effective_prompt = "帮我分析举报件"
    if not effective_prompt:
        raise ValueError("prompt is required")
    payload = _build_api_payload(
        effective_prompt,
        conversation_id=conversation_id,
        show_plan=show_plan,
        show_steps=show_steps,
        model_name=model_name,
        need_deep_think=need_deep_think,
        engine=engine,
    )
    response = _post_api(session, api_url, payload, attachment=attachment)
    if show_plan and response.get("plan"):
        print("[plan]", response.get("plan"))
    if show_steps and response.get("step_outputs"):
        print("[steps]", _redact_step_outputs(response.get("step_outputs")))
    print(response.get("answer") or "")
    return response.get("conversation_id") or conversation_id


def _repl_api(
    *,
    show_plan: bool,
    show_steps: bool,
    attachment: str | None,
    api_url: str,
    conversation_id: str | None,
    session,
    model_name: str,
    need_deep_think: bool,
    engine: str,
) -> None:
    current_conv = conversation_id or ""
    print("opencode_runtime API CLI. 输入 :exit 退出, :reset 重置会话")
    while True:
        try:
            line = input(">> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line in {":exit", ":quit", ":q"}:
            break
        if line == ":reset":
            current_conv = ""
            print("[session] reset")
            continue
        current_conv = _run_api_once(
            line,
            show_plan=show_plan,
            show_steps=show_steps,
            attachment=attachment,
            api_url=api_url,
            conversation_id=current_conv or None,
            session=session,
            model_name=model_name,
            need_deep_think=need_deep_think,
            engine=engine,
        )


def main() -> None:
    _ensure_pythonpath()
    _load_env()

    parser = argparse.ArgumentParser(description="Run opencode_runtime MainAgent via CLI.")
    parser.add_argument("--prompt", "-p", type=str, default="", help="Run once with a single prompt")
    parser.add_argument("--show-plan", action="store_true", help="Print planner output")
    parser.add_argument("--planner-model", type=str, default="", help="Model name for planner LLM")
    parser.add_argument("--attachment", type=str, default="", help="Attachment file path for text extraction")
    parser.add_argument("--api-url", type=str, default="", help="Use HTTP API instead of local agent")
    parser.add_argument("--api-base", type=str, default="", help="API base url, e.g. http://127.0.0.1:5001")
    parser.add_argument("--use-api", action="store_true", help="Use HTTP API with default url")
    parser.add_argument("--conversation-id", type=str, default="", help="Conversation id for API mode")
    parser.add_argument("--auth-token", type=str, default="", help="Bearer token for API mode")
    parser.add_argument("--user-id", type=str, default="", help="Use /api/inner_login to get cookie")
    parser.add_argument("--user-name", type=str, default="", help="Use /api/inner_login to get cookie")
    parser.add_argument("--model-name", type=str, default="DeepSeek-R1", help="Model name for payload")
    parser.add_argument("--need-deep-think", action="store_true", help="Set need_deep_think=1 in payload")
    parser.add_argument("--engine", type=str, default="opencode", help="Engine flag in client_meta")
    parser.add_argument("--show-steps", action="store_true", help="Print per-step outputs")
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("[warn] OPENAI_API_KEY 未配置，LLM 规划器/工具可能无法运行")

    planner_model = args.planner_model.strip() or os.getenv("OPENCODE_PLANNER_MODEL")

    attachment = args.attachment.strip() or None
    api_url = args.api_url.strip() or ""
    if not api_url and args.api_base.strip():
        api_url = args.api_base.strip().rstrip("/") + "/api/chat/completion"
    if not api_url and args.use_api:
        port = os.getenv("PORT", "5001")
        api_url = f"http://127.0.0.1:{port}/api/chat/completion"
    conversation_id = args.conversation_id.strip() or None
    auth_token = args.auth_token.strip() or None
    user_id = args.user_id.strip() or None
    user_name = args.user_name.strip() or None
    model_name = args.model_name.strip() or "DeepSeek-R1"
    need_deep_think = bool(args.need_deep_think)
    engine = args.engine.strip() or "opencode"
    show_steps = bool(args.show_steps)

    if api_url:
        session = _create_api_session(
            api_url,
            auth_token=auth_token,
            user_id=user_id,
            user_name=user_name,
        )
        if args.prompt:
            _run_api_once(
                args.prompt,
                show_plan=args.show_plan,
                show_steps=show_steps,
                attachment=attachment,
                api_url=api_url,
                conversation_id=conversation_id,
                session=session,
                model_name=model_name,
                need_deep_think=need_deep_think,
                engine=engine,
            )
        else:
            _repl_api(
                show_plan=args.show_plan,
                show_steps=show_steps,
                attachment=attachment,
                api_url=api_url,
                conversation_id=conversation_id,
                session=session,
                model_name=model_name,
                need_deep_think=need_deep_think,
                engine=engine,
            )
        return

    if args.prompt:
        asyncio.run(_run_once(args.prompt, show_plan=args.show_plan, planner_model=planner_model, attachment=attachment))
    else:
        asyncio.run(_repl(show_plan=args.show_plan, planner_model=planner_model, attachment=attachment))


if __name__ == "__main__":
    main()
