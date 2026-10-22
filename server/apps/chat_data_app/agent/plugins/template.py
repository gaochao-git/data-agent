"""插件模板下载：把内置示例目录打成可安装的 zip。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

TEMPLATE_ROOT = Path(__file__).with_name("template")
TEMPLATE_PLUGIN_NAME = "example-plugin"


def build_plugin_template_zip() -> bytes:
    """返回含 ``example-plugin/`` 顶层目录的插件模板 zip 字节。"""
    source = TEMPLATE_ROOT / TEMPLATE_PLUGIN_NAME
    if not source.is_dir():
        raise FileNotFoundError(f"插件模板目录不存在: {source}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            archive.write(path, arcname=f"{TEMPLATE_PLUGIN_NAME}/{path.relative_to(source).as_posix()}")
    return buffer.getvalue()


__all__ = ["TEMPLATE_PLUGIN_NAME", "build_plugin_template_zip"]
