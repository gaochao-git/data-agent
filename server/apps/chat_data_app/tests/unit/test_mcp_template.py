"""MCP Server 模板打包器：目录缺失与目录成员跳过分支。"""

import io
import zipfile

import pytest

from apps.chat_data_app.agent.mcp import template as mcp_template
from apps.chat_data_app.agent.plugins import template as plugin_template


def test_build_mcp_server_template_requires_source_root(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_template, "TEMPLATE_ROOT", tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        mcp_template.build_mcp_server_template_zip()


def test_build_mcp_server_template_skips_directories(monkeypatch, tmp_path):
    source = tmp_path / mcp_template.TEMPLATE_SERVER_NAME
    source.mkdir(parents=True)
    (source / "server.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "nested").mkdir()
    monkeypatch.setattr(mcp_template, "TEMPLATE_ROOT", tmp_path)

    with zipfile.ZipFile(io.BytesIO(mcp_template.build_mcp_server_template_zip())) as archive:
        names = set(archive.namelist())

    assert "hello-mcp-server/server.py" in names
    assert "hello-mcp-server/nested/" not in names


def test_build_plugin_template_requires_source_root(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_template, "TEMPLATE_ROOT", tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        plugin_template.build_plugin_template_zip()
