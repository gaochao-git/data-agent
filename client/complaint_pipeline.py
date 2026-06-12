#!/usr/bin/env python3
"""
独立调用四个接口的 CLI（原样打印返回）：
  text             -> /api/complaint/text         （auto:user）
  summary          -> /api/complaint/{base_id}/summary  （auto:user）
  extraction       -> /api/complaint/{base_id}/extraction（auto:user）
  classification   -> /api/complaint/{base_id}/classification（auto:user）

示例：
- text 自动上传：
python client/complaint_pipeline.py text --file /Users/fengyingsheng/Downloads/mock_举报信_5图片_交易流水.pdf --userid demo --username demo
- text 自动复跑：
python client/complaint_pipeline.py text --text-mode auto --base-id 0a13eae701fe44f78276ae52cd4acf45 --userid demo --username demo
- text 用户保存：
python client/complaint_pipeline.py text --text-mode user --base-id 0a13eae701fe44f78276ae52cd4acf45 --document-text "用户全文" --userid demo --username demo

- summary 自动：
python client/complaint_pipeline.py summary --base-id 0a13eae701fe44f78276ae52cd4acf45 --summary-mode auto --userid demo --username demo
- summary 用户：
python client/complaint_pipeline.py summary --base-id 0a13eae701fe44f78276ae52cd4acf45 --summary-mode user --summary-text "用户摘要" --userid demo --username demo


- extraction 自动：
python client/complaint_pipeline.py extraction --base-id 0a13eae701fe44f78276ae52cd4acf45 --extraction-mode auto --userid demo --username demo

- extraction 自动自定义文本：
python client/complaint_pipeline.py extraction --base-id 0a13eae701fe44f78276ae52cd4acf45 --extraction-mode auto --extraction-text "覆盖输入文本" --userid demo --username demo

- extraction 用户 all：
python client/complaint_pipeline.py extraction --base-id 0a13eae701fe44f78276ae52cd4acf45 --extraction-mode user --items-json /Users/fengyingsheng/complaints.json --appeals-json appeals.json   --userid demo --username demo


- classification 自动：
python client/complaint_pipeline.py classification --base-id 0a13eae701fe44f78276ae52cd4acf45 --classification-mode auto --userid demo --username demo
- classification 用户结果：
python client/complaint_pipeline.py classification --base-id 0a13eae701fe44f78276ae52cd4acf45 --classification-mode user --items-json /path/to/patch.json --userid demo --username demo

"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable, Optional

import requests

DEFAULT_BASE_URL = os.getenv("COMPLAINT_API_BASE_URL", "http://127.0.0.1:5001")
JWT_COOKIE_NAME = os.getenv("JWT_COOKIE_NAME", "inner_token")


def _iter_sse_data_lines(response: requests.Response) -> Iterable[str]:
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line.split("data:", 1)[1].strip()
        if payload:
            yield payload


def _obtain_token(session: requests.Session, base_url: str, token: Optional[str], user_id: str, user_name: str) -> str:
    if token:
        return token
    login_url = f"{base_url.rstrip('/')}/api/inner_login"
    resp = session.get(login_url, params={"userid": user_id, "username": user_name}, timeout=30, verify=session.verify)
    resp.raise_for_status()
    cookie_token = resp.cookies.get(JWT_COOKIE_NAME)
    if not cookie_token:
        raise RuntimeError(f"inner_login succeeded but cookie {JWT_COOKIE_NAME!r} was not found.")
    return cookie_token


def _load_json_arg(value: Optional[str]) -> Optional[Any]:
    if not value:
        return None
    if os.path.isfile(value):
        with open(value, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(value)


def cmd_text(args: argparse.Namespace) -> None:
    base_url = args.base_url
    session = requests.Session()
    if args.insecure:
        session.verify = False
    token = _obtain_token(session, base_url, args.token, args.userid, args.username)

    url = f"{base_url.rstrip('/')}/api/complaint/text"
    if args.text_mode == "auto":
        if not args.file and not args.base_id:
            raise RuntimeError("auto text mode requires --file or --base-id")
        headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
        data = {}
        files = None
        if args.base_id:
            data["base_id"] = args.base_id
        if args.file:
            files = {"file": open(args.file, "rb")}
        print(f"url={url}")
        resp = session.post(url, data=data, files=files, headers=headers, stream=True, timeout=args.timeout, verify=session.verify)
        if files:
            files["file"].close()
        resp.raise_for_status()
        for payload in _iter_sse_data_lines(resp):
            print(payload)
    else:
        if not args.base_id or not args.document_text:
            raise RuntimeError("user text mode requires --base-id and --document-text")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"base_id": args.base_id, "document_text": args.document_text}
        resp = session.post(url, json=body, headers=headers, timeout=args.timeout, verify=session.verify)
        resp.raise_for_status()
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


def cmd_summary(args: argparse.Namespace) -> None:
    base_url = args.base_url
    session = requests.Session()
    if args.insecure:
        session.verify = False
    token = _obtain_token(session, base_url, args.token, args.userid, args.username)

    url = f"{base_url.rstrip('/')}/api/complaint/{args.base_id}/summary"
    if args.summary_mode == "auto":
        headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
        resp = session.post(url, headers=headers, stream=True, timeout=args.timeout, verify=session.verify)
        resp.raise_for_status()
        for payload in _iter_sse_data_lines(resp):
            print(payload)
    else:
        if not args.summary_text:
            raise RuntimeError("user summary mode requires --summary-text")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"summary_text": args.summary_text}
        resp = session.post(url, json=body, headers=headers, timeout=args.timeout, verify=session.verify)
        resp.raise_for_status()
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


def cmd_extraction(args: argparse.Namespace) -> None:
    base_url = args.base_url
    session = requests.Session()
    if args.insecure:
        session.verify = False
    token = _obtain_token(session, base_url, args.token, args.userid, args.username)

    url = f"{base_url.rstrip('/')}/api/complaint/{args.base_id}/extraction"
    if args.extraction_mode == "auto":
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream"}
        body: dict = {}
        if args.extraction_text:
            body["input_text"] = args.extraction_text
        resp = session.post(url, json=body, headers=headers, stream=True, timeout=args.timeout, verify=session.verify)
        resp.raise_for_status()
        for payload in _iter_sse_data_lines(resp):
            print(payload)
    else:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body: dict = {}
        items_user = _load_json_arg(args.items_json)
        appeals_user = _load_json_arg(args.appeals_json)

        # 支持单个 JSON 同时包含 complaints/appeals
        if isinstance(items_user, dict):
            if "complaints" in items_user:
                body["complaints"] = items_user.get("complaints")
            if "appeals" in items_user and "appeals" not in body:
                body["appeals"] = items_user.get("appeals")
        elif items_user is not None:
            body["complaints"] = items_user

        if isinstance(appeals_user, dict):
            if "complaints" in appeals_user and "complaints" not in body:
                body["complaints"] = appeals_user.get("complaints")
            if "appeals" in appeals_user:
                body["appeals"] = appeals_user.get("appeals")
        elif appeals_user is not None:
            body["appeals"] = appeals_user

        if not body:
            raise RuntimeError("user extraction mode requires --items-json or --appeals-json")
        resp = session.post(url, json=body, headers=headers, timeout=args.timeout, verify=session.verify)
        resp.raise_for_status()
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


def cmd_classification(args: argparse.Namespace) -> None:
    base_url = args.base_url
    session = requests.Session()
    if args.insecure:
        session.verify = False
    token = _obtain_token(session, base_url, args.token, args.userid, args.username)

    url = f"{base_url.rstrip('/')}/api/complaint/{args.base_id}/classification"
    if args.classification_mode == "auto":
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "text/event-stream"}
        body = {"source": "auto"}
        resp = session.post(url, json=body, headers=headers, stream=True, timeout=args.timeout, verify=session.verify)
        resp.raise_for_status()
        for payload in _iter_sse_data_lines(resp):
            print(payload)
    else:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        complaints_user = _load_json_arg(args.items_json)
        appeals_user = _load_json_arg(args.appeals_json)

        body: dict = {"source": "user", "story": args.story or ""}
        # 支持单个 JSON 同时包含 complaints/appeals
        if isinstance(complaints_user, dict):
            if "complaints" in complaints_user:
                body["complaints"] = complaints_user.get("complaints")
            if "appeals" in complaints_user and "appeals" not in body:
                body["appeals"] = complaints_user.get("appeals")
        elif complaints_user is not None:
            body["complaints"] = complaints_user

        if isinstance(appeals_user, dict):
            if "complaints" in appeals_user and "complaints" not in body:
                body["complaints"] = appeals_user.get("complaints")
            if "appeals" in appeals_user:
                body["appeals"] = appeals_user.get("appeals")
        elif appeals_user is not None:
            body["appeals"] = appeals_user

        if "complaints" not in body and "appeals" not in body:
            raise RuntimeError("user classification mode requires complaints and/or appeals data")
        resp = session.post(url, json=body, headers=headers, timeout=args.timeout, verify=session.verify)
        resp.raise_for_status()
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    # 公共参数，挂载在顶层和各子命令，支持出现在子命令前或后
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL (default {DEFAULT_BASE_URL}).")
    common.add_argument("--userid", default=os.getenv("COMPLAINT_USERID", "demo"), help="User ID for inner_login.")
    common.add_argument("--username", default=os.getenv("COMPLAINT_USERNAME", "demo"), help="Username for inner_login.")
    common.add_argument("--token", help="Optional JWT; if absent script will call /api/inner_login.")
    common.add_argument("--insecure", action="store_true", help="Skip TLS verification.")
    common.add_argument("--timeout", type=int, default=600, help="HTTP timeout seconds.")

    parser = argparse.ArgumentParser(description="Complaint endpoints CLI (per-interface, auto/user modes, raw output).", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    # text
    p_text = sub.add_parser("text", help="Call /complaint/text", parents=[common], add_help=False)
    p_text.add_argument("--text-mode", choices=["auto", "user"], default="auto")
    p_text.add_argument("--file", help="Path to PDF/image for auto mode.")
    p_text.add_argument("--base-id", help="Existing base_id (resume or user mode required).")
    p_text.add_argument("--document-text", help="Full text for user mode.")

    # summary
    p_summary = sub.add_parser("summary", help="Call /complaint/{base_id}/summary", parents=[common], add_help=False)
    p_summary.add_argument("--base-id", required=True, help="Complaint base_id.")
    p_summary.add_argument("--summary-mode", choices=["auto", "user"], default="auto")
    p_summary.add_argument("--summary-text", help="Summary text for user mode.")

    # extraction
    p_extraction = sub.add_parser("extraction", help="Call /complaint/{base_id}/extraction", parents=[common], add_help=False)
    p_extraction.add_argument("--base-id", required=True, help="Complaint base_id.")
    p_extraction.add_argument("--extraction-mode", choices=["auto", "user"], default="auto")
    p_extraction.add_argument("--extraction-text", help="Input text override for auto mode.")
    p_extraction.add_argument("--items-json", help="Items JSON (string or file) for user mode.")
    p_extraction.add_argument("--appeals-json", help="Appeals JSON (string or file) for user mode.")

    # classification
    p_class = sub.add_parser("classification", help="Call /complaint/{base_id}/classification", parents=[common], add_help=False)
    p_class.add_argument("--base-id", required=True, help="Complaint base_id.")
    p_class.add_argument("--classification-mode", choices=["auto", "user"], default="auto")
    p_class.add_argument("--items-json", help="Complaints JSON (string or file) for user mode.")
    p_class.add_argument("--appeals-json", help="Appeals JSON (string or file) for user mode.")
    p_class.add_argument("--classification-json", help="(Deprecated) Classification JSON (ignored).")
    p_class.add_argument("--story", help="Story/summary text for classification.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "text":
            cmd_text(args)
        elif args.command == "summary":
            cmd_summary(args)
        elif args.command == "extraction":
            cmd_extraction(args)
        elif args.command == "classification":
            cmd_classification(args)
        else:
            parser.error("unknown command")
    except Exception as exc:  # pragma: no cover
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
