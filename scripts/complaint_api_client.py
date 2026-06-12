#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import requests


def stream_sse(url: str, json_payload: dict[str, Any] | None = None, form_data: dict[str, Any] | None = None, files: dict[str, Any] | None = None, timeout: int = 600) -> None:
    request_kwargs: dict[str, Any] = {"stream": True, "timeout": timeout}
    if json_payload is not None:
        request_kwargs["json"] = json_payload
    else:
        request_kwargs["data"] = form_data or {}
    if files:
        request_kwargs["files"] = files
    with requests.post(url, **request_kwargs) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str:
                continue
            try:
                parsed = json.loads(data_str)
                print(json.dumps(parsed, ensure_ascii=False))
            except Exception:
                print(data_str)


def run_analysis(base_url: str, user_id: str, file_id: str | None, force: bool, use_uploaded_pdf: bool, pdf_file: str | None) -> None:
    url = f"{base_url.rstrip('/')}/api/complaint/complaint-analysis"
    if use_uploaded_pdf:
        if not pdf_file:
            raise ValueError("use_uploaded_pdf=true 时必须提供 --pdf-file")
        pdf_path = Path(pdf_file).expanduser()
        if not pdf_path.is_file():
            raise ValueError(f"PDF 文件不存在: {pdf_path}")
        with pdf_path.open("rb") as handle:
            files = {"file": (pdf_path.name, handle, "application/pdf")}
            data: dict[str, Any] = {"user_id": user_id, "force": str(bool(force)).lower(), "use_uploaded_pdf": "true"}
            if file_id:
                data["file_id"] = file_id
            stream_sse(url, form_data=data, files=files)
        return
    if not file_id:
        raise ValueError("use_uploaded_pdf=false 时必须提供 --file-id")
    stream_sse(url, json_payload={"user_id": user_id, "file_id": file_id, "force": force, "use_uploaded_pdf": False})


def run_reply(base_url: str, user_id: str, file_id: str, force: bool) -> None:
    url = f"{base_url.rstrip('/')}/api/complaint/reply-draft"
    stream_sse(url, json_payload={"user_id": user_id, "file_id": file_id, "force": force})


def run_save(base_url: str, user_id: str, file_id: str, title: str, content: str) -> None:
    url = f"{base_url.rstrip('/')}/api/complaint/reply-draft/save"
    payload = {"user_id": user_id, "file_id": file_id, "title": title, "content": content, "source_versions": {}}
    response = requests.post(url, json=payload, timeout=60)
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


def run_download(base_url: str, user_id: str, file_id: str, version: str, output: str) -> None:
    url = f"{base_url.rstrip('/')}/api/complaint/reply-draft/download"
    with requests.get(url, params={"file_id": file_id, "user_id": user_id, "version": version}, stream=True, timeout=120) as response:
        response.raise_for_status()
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)
    print(f"saved: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Complaint API 调试客户端")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001", help="服务地址")
    parser.add_argument("--user-id", required=True, help="用户标识")

    sub = parser.add_subparsers(dest="command", required=True)

    p_analysis = sub.add_parser("analysis", help="调用举报事项分析 SSE")
    p_analysis.add_argument("--file-id", default=None)
    p_analysis.add_argument("--force", action="store_true")
    p_analysis.add_argument("--use-uploaded-pdf", action="store_true")
    p_analysis.add_argument("--pdf-file", default=None, help="上传模式时的本地 PDF 路径")

    p_reply = sub.add_parser("reply", help="调用辅助文书撰写 SSE")
    p_reply.add_argument("--file-id", required=True)
    p_reply.add_argument("--force", action="store_true")

    p_save = sub.add_parser("save", help="保存文书草稿")
    p_save.add_argument("--file-id", required=True)
    p_save.add_argument("--title", required=True)
    p_save.add_argument("--content", required=True)

    p_download = sub.add_parser("download", help="下载文书 docx")
    p_download.add_argument("--file-id", required=True)
    p_download.add_argument("--version", default="latest")
    p_download.add_argument("--output", default="./reply_draft.docx")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "analysis":
        if args.use_uploaded_pdf and not args.pdf_file:
            parser.error("analysis 命令启用 --use-uploaded-pdf 时必须提供 --pdf-file")
        if not args.use_uploaded_pdf and not args.file_id:
            parser.error("analysis 命令未启用 --use-uploaded-pdf 时必须提供 --file-id")
        run_analysis(args.base_url, args.user_id, args.file_id, bool(args.force), bool(args.use_uploaded_pdf), args.pdf_file)
        return
    if args.command == "reply":
        run_reply(args.base_url, args.user_id, args.file_id, bool(args.force))
        return
    if args.command == "save":
        run_save(args.base_url, args.user_id, args.file_id, args.title, args.content)
        return
    if args.command == "download":
        run_download(args.base_url, args.user_id, args.file_id, args.version, args.output)
        return


if __name__ == "__main__":
    main()
