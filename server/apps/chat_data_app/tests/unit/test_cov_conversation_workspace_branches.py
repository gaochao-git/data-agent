"""services/agent_run/conversation_workspace.py 剩余分支。"""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

from apps.chat_data_app.services.agent_run import conversation_workspace as cw


def _prepare_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for name in cw.WORKSPACE_DIR_NAMES:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def test_delete_workspace_file_rejects_system_result_path():
    """系统结果目录（查询结果/工具缓存）不允许删除。"""
    with pytest.raises(ValueError, match="查询结果需要留存审计"):
        cw.delete_workspace_file("conv_a", "/mnt/user-data/outputs/query_result/r1.json")


def test_list_workspace_files_skips_missing_directory(tmp_path, monkeypatch):
    """工作区缺少某个可见目录时跳过，不影响其他目录列举。"""
    root = tmp_path / "root"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "note.txt").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(
        cw, "ensure_conversation_workspace", lambda *_args, **_kwargs: {"root": str(root), "workspace": ""}
    )

    files = cw.list_workspace_files("conv_a", "u1")

    assert [item["relative_path"] for item in files] == ["workspace/note.txt"]


def test_archive_workspace_directory_uses_default_limit_when_not_passed(tmp_path, monkeypatch):
    """未传 archive_max_bytes 时回退模块兜底常量。"""
    root = _prepare_root(tmp_path)
    (root / "outputs" / "a.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(
        cw, "ensure_conversation_workspace", lambda *_args, **_kwargs: {"root": str(root), "outputs": ""}
    )
    monkeypatch.setattr(cw, "WORKSPACE_ARCHIVE_MAX_BYTES", 1)

    with pytest.raises(cw.WorkspaceArchiveTooLargeError):
        cw._archive_workspace_directory("conv_a", "/outputs", "u1", archive_max_bytes=None)

    archive = cw._archive_workspace_directory("conv_a", "/outputs", "u1", archive_max_bytes=10 * 1024 * 1024)
    assert archive["file_count"] == 1
    with zipfile.ZipFile(archive["temp_path"]) as zf:
        assert zf.namelist() == ["outputs/a.txt"]
    Path(archive["temp_path"]).unlink()


def test_archive_workspace_directory_fails_when_all_files_disappeared(tmp_path, monkeypatch):
    """列表命中但文件在打包前被删除时按空目录报错。"""
    root = _prepare_root(tmp_path)
    monkeypatch.setattr(
        cw, "ensure_conversation_workspace", lambda *_args, **_kwargs: {"root": str(root), "outputs": ""}
    )
    monkeypatch.setattr(cw, "list_workspace_files", lambda *_args, **_kwargs: [{"relative_path": "outputs/gone.txt"}])

    with pytest.raises(FileNotFoundError, match="没有可打包的文件"):
        cw._archive_workspace_directory("conv_a", "/outputs", "u1", archive_max_bytes=10 * 1024 * 1024)


def test_validate_office_preview_source_uses_default_limit(tmp_path, monkeypatch):
    """未传 max_bytes 时回退 Office 预览兜底上限。"""
    root = _prepare_root(tmp_path)
    target = root / "outputs" / "report.docx"
    target.write_bytes(b"x")
    monkeypatch.setattr(cw, "WORKSPACE_OFFICE_SOURCE_MAX_BYTES", 0)

    with pytest.raises(ValueError, match="文件过大"):
        cw._validate_office_preview_source(str(root), "/mnt/user-data/outputs/report.docx")

    cw._validate_office_preview_source(str(root), "/mnt/user-data/outputs/report.docx", max_bytes=1024)


def test_archive_max_bytes_reads_runtime_setting(monkeypatch):
    """打包上限按运行期配置读取（MB 转字节）。"""

    async def fake_setting(_key: str, _default: object = None) -> int:
        return 3

    monkeypatch.setattr(cw, "get_runtime_setting", fake_setting)

    assert asyncio.run(cw._archive_max_bytes()) == 3 * 1024 * 1024
