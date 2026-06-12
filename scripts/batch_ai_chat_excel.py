#!/usr/bin/env python3
"""
Batch-call /api/ai_chat using rows from an Excel file and write results back.

Requirements:
  pip install pandas requests openpyxl
Example:
  python scripts/batch_ai_chat_excel.py \\
    --input input.xlsx --output output.xlsx --question-column question \\
    --base-url http://127.0.0.1:5000 --userid demo --username demo
  python scripts/batch_ai_chat_excel.py -i scripts/ai_chat_sample.xlsx -o scripts/ai_chat_output.xlsx --question-column 问题 --base-url http://127.0.0.1:5001 --userid demo --username demo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Iterable, Optional
import re
from pathlib import Path

import pandas as pd
import requests

DEFAULT_BASE_URL = os.getenv("AI_CHAT_BASE_URL", "http://127.0.0.1:5000")
DEFAULT_TIMEOUT = int(os.getenv("AI_CHAT_TIMEOUT", "3600"))
JWT_COOKIE_NAME = os.getenv("JWT_COOKIE_NAME", "inner_token")


@dataclass
class ChatResult:
    answer: Optional[str]
    error: Optional[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run /api/ai_chat for each row in an Excel file.")
    parser.add_argument("-i", "--input", required=True, help="Input Excel file path.")
    parser.add_argument("-o", "--output", default="ai_chat_output.xlsx", help="Output Excel file path.")
    parser.add_argument(
        "--question-column",
        default="question",
        help="Column name that holds the user question for /api/ai_chat.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument("--userid", default=os.getenv("AI_CHAT_USERID", "demo"), help="User ID for inner_login.")
    parser.add_argument("--username", default=os.getenv("AI_CHAT_USERNAME", "demo"), help="Username for inner_login.")
    parser.add_argument(
        "--token",
        default=os.getenv("AI_CHAT_TOKEN"),
        help="Optional existing JWT; if absent the script will call /api/inner_login.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS certificate verification (not recommended for production).",
    )
    return parser.parse_args()


def obtain_token(session: requests.Session, base_url: str, token: Optional[str], user_id: str, user_name: str) -> str:
    if token:
        return token

    login_url = f"{base_url.rstrip('/')}/api/inner_login"
    resp = session.get(login_url, params={"userid": user_id, "username": user_name}, timeout=30, verify=session.verify)
    resp.raise_for_status()
    cookie_token = resp.cookies.get(JWT_COOKIE_NAME)
    if not cookie_token:
        raise RuntimeError(f"inner_login succeeded but cookie {JWT_COOKIE_NAME!r} was not found.")
    return cookie_token


def _iter_sse_data_lines(response: requests.Response) -> Iterable[dict]:
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line.split("data:", 1)[1].strip()
        if not payload:
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def call_ai_chat(session: requests.Session, base_url: str, token: str, question: str, timeout: int) -> ChatResult:
    url = f"{base_url.rstrip('/')}/api/ai_chat"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {"question": question}
    response = session.post(url, json=payload, headers=headers, stream=True, timeout=timeout, verify=session.verify)
    if not response.ok:
        return ChatResult(
            answer=None,
            error=f"HTTP {response.status_code}: {response.text[:200]}",
        )

    deltas: list[str] = []
    final_answer: Optional[str] = None
    error_msg: Optional[str] = None

    for data in _iter_sse_data_lines(response):
        event_name = str(data.get("event") or "").strip()
        if not event_name:
            continue

        if event_name == "content_delta":
            delta = data.get("delta")
            if isinstance(delta, str):
                deltas.append(delta)
        elif event_name == "message_end":
            display = data.get("display") or {}
            display_content = display.get("content")
            if isinstance(display_content, str) and display_content.strip():
                final_answer = display_content.strip()
            else:
                final_answer = "".join(deltas).strip()
        elif event_name == "error":
            message = data.get("message") or data
            error_msg = f"stream error: {message}"
        elif event_name == "done":
            break

    if final_answer is None:
        final_answer = "".join(deltas).strip() or None

    return ChatResult(
        answer=final_answer,
        error=error_msg,
    )


def split_answer_and_links(answer: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Split answer into plain text and link code blocks.
    Returns (plain_answer, link_blocks).
    """
    if not answer:
        return None, None
    pattern = re.compile(r"```link(.*?)```", re.DOTALL | re.IGNORECASE)
    link_parts = []

    def _collect(match: re.Match) -> str:
        content = match.group(1).strip()
        if content:
            link_parts.append(content)
        return ""

    plain = pattern.sub(_collect, answer)
    plain_clean = plain.strip() or None
    links_joined = "\n\n".join(link_parts) if link_parts else None
    return plain_clean, links_joined


def _normalize_cell(value: object) -> Optional[str]:
    """Normalize Excel cell content to None or stripped string."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text if text else None


def main() -> int:
    args = parse_args()
    session = requests.Session()
    if args.insecure:
        session.verify = False
    try:
        token = obtain_token(session, args.base_url, args.token, args.userid, args.username)
    except Exception as exc:  # pragma: no cover - runtime convenience
        print(f"Failed to obtain token: {exc}", file=sys.stderr)
        return 1

    try:
        df = pd.read_excel(args.input)
    except Exception as exc:  # pragma: no cover
        print(f"Failed to read Excel: {exc}", file=sys.stderr)
        return 1

    if args.question_column not in df.columns:
        print(f"Column {args.question_column!r} not found in {args.input}.", file=sys.stderr)
        return 1

    answers: list[Optional[str]] = [None] * len(df)
    link_blocks: list[Optional[str]] = [None] * len(df)
    output_path = Path(args.output)
    if output_path.exists():
        try:
            prev_df = pd.read_excel(output_path)
            for idx in range(min(len(df), len(prev_df))):
                if "ai_chat_answer" in prev_df.columns:
                    answers[idx] = _normalize_cell(prev_df.at[idx, "ai_chat_answer"])
                if "ai_chat_links" in prev_df.columns:
                    link_blocks[idx] = _normalize_cell(prev_df.at[idx, "ai_chat_links"])
        except Exception as exc:  # pragma: no cover
            print(f"Warning: failed to read existing output {output_path}: {exc}", file=sys.stderr)

    def save_progress() -> None:
        out_df = df.copy()
        out_df["ai_chat_answer"] = answers
        out_df["ai_chat_links"] = link_blocks
        out_df.to_excel(output_path, index=False)

    total = len(df)
    for idx, question in enumerate(df[args.question_column], start=0):
        human_idx = idx + 1
        q_text = "" if pd.isna(question) else str(question).strip()
        if not q_text:
            answers[idx] = None
            link_blocks[idx] = None
            print(f"[{human_idx}/{total}] skipped (empty question)")
            save_progress()
            continue

        if answers[idx]:
            print(f"[{human_idx}/{total}] already processed, skip.")
            continue

        print(f"[{human_idx}/{total}] sending question: {q_text[:80]!r}")
        try:
            result = call_ai_chat(session, args.base_url, token, q_text, args.timeout)
        except Exception as exc:  # pragma: no cover
            answers[idx] = None
            link_blocks[idx] = None
            print(f"  error: {exc}")
            save_progress()
            continue

        if result.error:
            print(f"  error: {result.error}")
            answers[idx] = result.answer or None
            link_blocks[idx] = None
        else:
            plain_answer, links = split_answer_and_links(result.answer)
            answers[idx] = plain_answer
            link_blocks[idx] = links
            print("  done")

        save_progress()

    print(f"Saved results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
