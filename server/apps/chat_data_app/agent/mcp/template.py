"""MCP Server 模板下载：把内置 hello-world 服务打成 zip。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).with_name("template")
TEMPLATE_SERVER_NAME = "hello-mcp-server"


def build_mcp_server_template_zip() -> bytes:
    """返回含 ``hello-mcp-server/`` 顶层目录的 MCP Server 模板 zip 字节。"""
    source = TEMPLATE_ROOT / TEMPLATE_SERVER_NAME
    if not source.is_dir():
        raise FileNotFoundError(f"MCP Server 模板目录不存在: {source}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            archive.write(path, arcname=f"{TEMPLATE_SERVER_NAME}/{path.relative_to(source).as_posix()}")
    return buffer.getvalue()


__all__ = ["TEMPLATE_SERVER_NAME", "build_mcp_server_template_zip"]
