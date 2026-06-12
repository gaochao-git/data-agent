#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, Iterable, Optional

import requests


def _build_payload(agent_id: str, conversation_id: str, text: str) -> Dict:
    return {
        "client_meta": {
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "last_message_index": 0,
        },
        "messages": [
            {
                "content_block": [
                    {
                        "block_type": 10001,
                        "content": {
                            "text_block": {
                                "text": text,
                                "icon_url": "",
                                "icon_url_dark": "",
                                "summary": "",
                            }
                        },
                        "block_id": "cli-block-1",
                        "meta_info": [],
                    }
                ],
                "model_id": "DeepSeek-R1",
                "model_name": "DeepSeek-R1",
                "need_deep_think": 1,
            }
        ],
        "option": {"answer_with_suggest": False},
        "chat_ability": {"ability_type": 6, "ability_param": "{\"action\":3}"},
        "ext": {},
    }


JWT_COOKIE_NAME = os.getenv("JWT_COOKIE_NAME", "inner_token")


def _iter_sse_lines(response: requests.Response) -> Iterable[str]:
    buffer = ""
    for chunk in response.iter_lines(decode_unicode=True):
        if chunk is None:
            continue
        buffer += chunk + "\n"
        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            if raw_event.strip():
                yield raw_event


def _print_event(raw_event: str) -> None:
    lines = [line for line in raw_event.splitlines() if line.strip()]
    event = ""
    data = ""
    event_id = ""
    for line in lines:
        if line.startswith("event:"):
            event = line.split("event:", 1)[1].strip()
        elif line.startswith("data:"):
            data = line.split("data:", 1)[1].strip()
        elif line.startswith("id:"):
            event_id = line.split("id:", 1)[1].strip()
    output = {"id": event_id, "event": event, "data": data}
    print(json.dumps(output, ensure_ascii=False))


def _obtain_token(
    session: requests.Session,
    base_url: str,
    token: Optional[str],
    user_id: str,
    user_name: str,
) -> str:
    if token:
        return token
    login_url = f"{base_url.rstrip('/')}/api/inner_login"
    resp = session.get(login_url, params={"userid": user_id, "username": user_name}, timeout=30, verify=session.verify)
    resp.raise_for_status()
    cookie_token = resp.cookies.get(JWT_COOKIE_NAME)
    if not cookie_token:
        raise RuntimeError(f"inner_login succeeded but cookie {JWT_COOKIE_NAME!r} was not found.")
    return cookie_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Call /api/chat/completion and print SSE events.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--agent", default="expert_agent", choices=["complaint_agent", "expert_agent"])
    parser.add_argument("--conversation")
    parser.add_argument("--text", required=True)
    parser.add_argument("--token", default="", help="Optional JWT token (overrides inner_login).")
    parser.add_argument("--userid", default="demo")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification.")
    args = parser.parse_args()

    payload = _build_payload(args.agent, args.conversation, args.text)
    session = requests.Session()
    if args.insecure:
        session.verify = False
    token = _obtain_token(session, args.base_url, args.token, args.userid, args.username)
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    url = f"{args.base_url.rstrip('/')}/api/chat/completion"
    resp = session.post(url, headers=headers, json=payload, stream=True, timeout=args.timeout, verify=session.verify)
    resp.raise_for_status()
    for raw_event in _iter_sse_lines(resp):
        _print_event(raw_event)

    return 0


if __name__ == "__main__":
    sys.exit(main())
