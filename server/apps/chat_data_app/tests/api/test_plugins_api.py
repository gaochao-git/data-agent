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
from apps.chat_data_app.services import plugin_service
from utils.error_codes import ErrorCode


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    monkeypatch.setattr(plugin_loader, "plugins_root", lambda chat_data_home=None: root)
    monkeypatch.setattr(plugin_installer, "plugin_dir", lambda name, chat_data_home=None: root / name)
    clear_metadata_cache()
    yield root
    clear_metadata_cache()


@pytest.fixture(autouse=True)
def _fake_plugin_db(monkeypatch):
    """内存假 DB：以 dict 模拟 chat_data_plugin，路由生命周期测试不触真实库。"""
    store: dict[str, dict] = {}

    class _FakeDao:
        async def list_enabled_names(self):
            return {n for n, v in store.items() if v["enabled"] and not v["deleted"]}

        async def upsert_installed(self, *, plugin_name, **_kw):
            store[plugin_name] = {"enabled": True, "deleted": False}

        async def set_enabled(self, *, plugin_name, enabled, **_kw):
            row = store.get(plugin_name)
            if not row or row["deleted"]:
                return False
            row["enabled"] = bool(enabled)
            return True

        async def mark_deleted(self, *, plugin_name, **_kw):
            row = store.get(plugin_name)
            if not row or row["deleted"]:
                return False
            row["deleted"] = True
            row["enabled"] = False
            return True

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _FakeDao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _FakeDao())
    yield store


def _plugin_zip(name="ops-readonly", skill="fault-analysis"):
    manifest = {"name": name, "version": "1.0.0", "display_name": name, "skills": [skill]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr(f"skills/{skill}/SKILL.md", f"---\nname: {skill}\n---\n# {skill}")
    return buffer.getvalue()


def test_install_plugin_rejects_oversized_upload(client, auth_headers, monkeypatch):
    from apps.chat_data_app.routers import plugins as plugins_router

    monkeypatch.setattr(plugins_router, "_PLUGIN_UPLOAD_MAX_BYTES", 1)
    response = client.post(
        "/api/chatdata/admin/plugins/install",
        files={"file": ("big.zip", b"xx", "application/zip")},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "error"


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
    after_disable = client.get("/api/chatdata/admin/plugins", headers=auth_headers).json()["data"]["plugins"]
    assert after_disable[0]["enabled"] is False

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
    from apps.chat_data_app.services import plugin_service as plugin_service_mod

    reset_calls = []
    monkeypatch.setattr(plugin_service_mod, "reset_mcp_tool_manager", lambda: reset_calls.append("reset") or None)

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


def test_plugin_template_download_returns_installable_zip(client, auth_headers):
    from apps.chat_data_app.agent.plugins.manifest import parse_plugin_manifest

    response = client.get("/api/chatdata/admin/plugins/template", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "example-plugin/plugin.json" in names
        assert "example-plugin/skills/example-skill/SKILL.md" in names
        assert "example-plugin/skills/example-skill/references/README.md" in names
        assert "example-plugin/skills/example-skill/assets/README.md" in names
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        manifest = parse_plugin_manifest(json.loads(archive.read("example-plugin/plugin.json")))
    assert manifest.name == "example-plugin"
    assert manifest.skills == ("example-skill",)
    assert len(manifest.mcp_servers) == 1
    assert manifest.mcp_servers[0]["transport"] == "streamable_http"
    assert manifest.mcp_servers[0]["enabled"] is False


def test_plugin_install_rejects_nonstandard_skill_directory(client, auth_headers, plugin_root):
    manifest = {
        "name": "bad-layout",
        "version": "1.0.0",
        "skills": ["bad-skill"],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr("skills/bad-skill/SKILL.md", "---\nname: bad-skill\n---\n")
        archive.writestr("skills/bad-skill/scripts/run.py", "print('bad')")
    response = client.post(
        "/api/chatdata/admin/plugins/install",
        files={"file": ("bad.zip", buffer.getvalue(), "application/zip")},
        headers=auth_headers,
    )
    assert response.json()["status"] == "error"
    assert "非标准路径" in response.json()["msg"]


def test_plugin_template_installs_with_standard_skill_layout(client, auth_headers, plugin_root: Path):
    """完整走一遍：下载模板 → 安装 → 校验标准技能目录 → 卸载。"""
    template = client.get("/api/chatdata/admin/plugins/template", headers=auth_headers)
    assert template.status_code == 200

    installed = client.post(
        "/api/chatdata/admin/plugins/install",
        files={"file": ("template.zip", template.content, "application/zip")},
        headers=auth_headers,
    )
    assert installed.json()["status"] == "success"
    assert installed.json()["data"]["plugin"]["name"] == "example-plugin"

    root = plugin_root / "example-plugin"
    skill_root = root / "skills" / "example-skill"
    assert (skill_root / "SKILL.md").is_file()
    assert (skill_root / "references" / "README.md").is_file()
    assert (skill_root / "assets" / "README.md").is_file()
    assert not (skill_root / "scripts").exists()

    listed = client.get("/api/chatdata/admin/plugins", headers=auth_headers)
    names = [item["name"] for item in listed.json()["data"]["plugins"]]
    assert "example-plugin" in names

    removed = client.delete("/api/chatdata/admin/plugins/example-plugin", headers=auth_headers)
    assert removed.json()["status"] == "success"


def test_probe_plugin_mcp_tools(client, auth_headers, plugin_root: Path, monkeypatch):
    template = client.get("/api/chatdata/admin/plugins/template", headers=auth_headers)
    installed = client.post(
        "/api/chatdata/admin/plugins/install",
        files={"file": ("template.zip", template.content, "application/zip")},
        headers=auth_headers,
    )
    assert installed.json()["status"] == "success"

    async def fake_probe(_plugin_name, _server_id):
        return {
            "server_id": "demo-http",
            "count": 2,
            "tools": [
                {"name": "read_data", "description": "读取数据", "parameters": [], "approval": "allow"},
                {"name": "write_data", "description": "写入数据", "parameters": [], "approval": "ask"},
            ],
        }

    monkeypatch.setattr(plugin_service.installer, "probe_mcp_server_tools", fake_probe)
    response = client.get(
        "/api/chatdata/admin/plugins/example-plugin/mcp-servers/demo-http/tools",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["server_id"] == "demo-http"
    assert data["count"] == 2
    assert [tool["name"] for tool in data["tools"]] == ["read_data", "write_data"]
    assert data["tools"][0]["approval"] == "allow"
    assert data["tools"][1]["approval"] == "ask"


def test_mcp_server_template_download_contains_hello_server(client, auth_headers):
    response = client.get("/api/chatdata/admin/plugins/mcp-template", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert "hello-mcp-server/server.py" in names
    assert "hello-mcp-server/requirements.txt" in names
    assert "hello-mcp-server/README.md" in names
    assert "hello-mcp-server/mcp_auth.py" in names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        server_source = archive.read("hello-mcp-server/server.py").decode()
        auth_source = archive.read("hello-mcp-server/mcp_auth.py").decode()
        requirements = archive.read("hello-mcp-server/requirements.txt").decode()
    assert "MCPServer" in server_source
    assert 'transport="streamable-http"' in server_source
    assert 'streamable_http_path="/mcp"' in server_source
    assert "build_bearer_auth" in server_source
    assert "TokenVerifier" in auth_source
    assert "AuthSettings" in auth_source
    assert "MCP_AUTH_TOKEN" in server_source
    assert "def hello" in server_source
    assert "def add" in server_source
    assert "mcp>=2.2.0" in requirements
    assert "uvicorn" not in requirements
