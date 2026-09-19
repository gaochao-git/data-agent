"""插件管理路由 API 契约测试（`/api/chatdata/admin/plugins`）。

安装/启停/卸载通过 monkeypatch 插件根目录到临时目录真实执行，
不依赖真实 CHAT_DATA_HOME。
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from apps.chat_data_app.agent.plugins import installer as plugin_installer
from apps.chat_data_app.agent.plugins import loader as plugin_loader
from apps.chat_data_app.agent.plugins.loader import clear_metadata_cache
from utils.error_codes import ErrorCode


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    monkeypatch.setattr(plugin_loader, "plugins_root", lambda chat_data_home=None: root)
    monkeypatch.setattr(plugin_installer, "plugin_dir", lambda name, chat_data_home=None: root / name)
    clear_metadata_cache()
    yield root
    clear_metadata_cache()


def _plugin_zip(name="ops-readonly", skill="fault-analysis"):
    manifest = {"name": name, "version": "1.0.0", "display_name": name, "skills": [skill]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr(f"skills/{skill}/SKILL.md", f"---\nname: {skill}\n---\n# {skill}")
    return buffer.getvalue()


def test_plugin_routes_full_lifecycle(client, auth_headers, plugin_root: Path):
    zip_bytes = _plugin_zip()

    install_response = client.post(
        "/api/chatdata/admin/plugins/install",
        files={"file": ("ops-readonly.zip", zip_bytes, "application/zip")},
        headers=auth_headers,
    )
    assert install_response.status_code == 200
    assert install_response.json()["status"] == "success"
    assert install_response.json()["data"]["plugin"]["name"] == "ops-readonly"

    list_response = client.get("/api/chatdata/admin/plugins", headers=auth_headers)
    assert list_response.status_code == 200
    plugins = list_response.json()["data"]["plugins"]
    assert [item["name"] for item in plugins] == ["ops-readonly"]
    assert plugins[0]["enabled"] is True

    disable_response = client.put(
        "/api/chatdata/admin/plugins/ops-readonly/enabled",
        json={"enabled": False},
        headers=auth_headers,
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["data"]["enabled"] is False
    assert (plugin_root / "ops-readonly" / ".disabled").exists()

    enable_response = client.put(
        "/api/chatdata/admin/plugins/ops-readonly/enabled",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert enable_response.status_code == 200

    delete_response = client.delete("/api/chatdata/admin/plugins/ops-readonly", headers=auth_headers)
    assert delete_response.status_code == 200
    assert not (plugin_root / "ops-readonly").exists()

    missing_response = client.delete("/api/chatdata/admin/plugins/ghost", headers=auth_headers)
    assert missing_response.json()["code"] == ErrorCode.CHATDATA_PLUGIN_NOT_FOUND


def test_plugin_install_rejects_duplicate(client, auth_headers, plugin_root):
    files = {"file": ("p.zip", _plugin_zip(), "application/zip")}
    first = client.post("/api/chatdata/admin/plugins/install", files=files, headers=auth_headers)
    assert first.json()["status"] == "success"
    second = client.post("/api/chatdata/admin/plugins/install", files=files, headers=auth_headers)
    assert second.json()["status"] == "error"
    assert second.json()["code"] == ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED


def test_plugin_install_rejects_bad_zip(client, auth_headers, plugin_root):
    response = client.post(
        "/api/chatdata/admin/plugins/install",
        files={"file": ("bad.zip", b"not a zip", "application/zip")},
        headers=auth_headers,
    )
    assert response.json()["status"] == "error"
    assert response.json()["code"] == ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED


def test_plugin_enable_unknown_returns_not_found(client, auth_headers, plugin_root):
    response = client.put(
        "/api/chatdata/admin/plugins/ghost/enabled",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert response.json()["code"] == ErrorCode.CHATDATA_PLUGIN_NOT_FOUND


def test_plugin_enable_invalid_name_returns_manifest_invalid(client, auth_headers, plugin_root):
    response = client.put(
        "/api/chatdata/admin/plugins/NOT%20VALID/enabled",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert response.json()["code"] == ErrorCode.CHATDATA_PLUGIN_MANIFEST_INVALID


def test_plugin_changes_invalidate_mcp_tool_cache(client, auth_headers, plugin_root, monkeypatch):
    """安装/启停/卸载成功后必须重置 MCP 工具管理器单例，避免 TTL 缓存延迟新装配感知。"""
    from apps.chat_data_app.routers import plugins as plugins_routes

    reset_calls = []
    monkeypatch.setattr(
        plugins_routes, "reset_mcp_tool_manager", lambda: reset_calls.append("reset") or None
    )

    installed = client.post(
        "/api/chatdata/admin/plugins/install",
        files={"file": ("p.zip", _plugin_zip(name="mcp-plugin"), "application/zip")},
        headers=auth_headers,
    )
    assert installed.json()["status"] == "success"
    enabled = client.put(
        "/api/chatdata/admin/plugins/mcp-plugin/enabled",
        json={"enabled": False},
        headers=auth_headers,
    )
    assert enabled.json()["status"] == "success"
    removed = client.delete("/api/chatdata/admin/plugins/mcp-plugin", headers=auth_headers)
    assert removed.json()["status"] == "success"
    assert reset_calls == ["reset", "reset", "reset"]

    # 失败的变更（未知插件启停）不应触发重置
    reset_calls.clear()
    missing = client.put(
        "/api/chatdata/admin/plugins/ghost/enabled",
        json={"enabled": True},
        headers=auth_headers,
    )
    assert missing.json()["code"] == ErrorCode.CHATDATA_PLUGIN_NOT_FOUND
    assert reset_calls == []
