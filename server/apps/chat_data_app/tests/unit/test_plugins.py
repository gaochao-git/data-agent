"""``agent/plugins`` 单测：manifest 校验、zip 安装安全边界、启停与运行期视图。

插件目录通过 monkeypatch ``loader.plugins_root`` 指到临时目录，
不依赖真实 ``CHAT_DATA_HOME``。
"""

import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.agent.plugins import installer as plugin_installer  # noqa: E402
from apps.chat_data_app.agent.plugins import loader as plugin_loader  # noqa: E402
from apps.chat_data_app.agent.plugins.installer import (  # noqa: E402
    PluginInstallError,
    PluginNotFoundError,
    install_plugin_from_zip_bytes,
    uninstall_plugin,
)
from apps.chat_data_app.agent.plugins.loader import (  # noqa: E402
    clear_metadata_cache,
    enabled_plugin_mcp_servers,
    enabled_plugin_skill_sources,
    load_plugins_metadata,
    refresh_plugin_state,
)
from apps.chat_data_app.agent.plugins.manifest import (  # noqa: E402
    PluginManifestError,
    parse_plugin_manifest,
)
from apps.chat_data_app.services import plugin_service  # noqa: E402

VALID_MANIFEST = {
    "name": "ops-readonly",
    "version": "1.0.0",
    "display_name": "智能运维只读插件",
    "description": "提供故障分析技能",
    "skills": ["fault-analysis"],
}


@pytest.fixture(autouse=True)
def _plugin_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    monkeypatch.setattr(plugin_loader, "plugins_root", lambda chat_data_home=None: root)
    monkeypatch.setattr(plugin_installer, "plugin_dir", lambda name, chat_data_home=None: root / name)
    monkeypatch.setattr(plugin_loader, "_enabled_state", None)
    clear_metadata_cache()
    yield root
    clear_metadata_cache()


def _build_zip(manifest: dict, *, files: dict[str, str] | None = None, wrap_dir: str | None = None) -> bytes:
    buffer = io.BytesIO()
    prefix = f"{wrap_dir}/" if wrap_dir else ""
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{prefix}plugin.json", json.dumps(manifest, ensure_ascii=False))
        for skill in manifest.get("skills", []):
            archive.writestr(f"{prefix}skills/{skill}/SKILL.md", f"---\nname: {skill}\n---\n# {skill}")
        for path, content in (files or {}).items():
            archive.writestr(f"{prefix}{path}", content)
    return buffer.getvalue()


# ============ manifest 解析 ============


def test_parse_manifest_ok():
    parsed = parse_plugin_manifest(VALID_MANIFEST)
    assert parsed.name == "ops-readonly"
    assert parsed.skills == ("fault-analysis",)
    assert parsed.display_name == "智能运维只读插件"
    data = parsed.to_dict()
    assert data["skills"] == ["fault-analysis"]


@pytest.mark.parametrize(
    "override",
    [
        {"name": "UPPER"},
        {"name": "a"},
        {"name": ""},
        {"version": ""},
        {"version": "v" * 64},
        {"skills": "not-a-list"},
        {"skills": ["bad name!"]},
        {"skills": ["dup", "dup"]},
        {"mcp_servers": [{"id": "x", "transport": "websocket", "command": "c"}]},
        {"mcp_servers": [{"id": "x", "transport": "stdio"}]},
        {"mcp_servers": [{"id": "x", "transport": "sse", "url": "ftp://x"}]},
        {"display_name": "x" * 100},
        {"description": "x" * 600},
    ],
)
def test_parse_manifest_rejects_invalid(override):
    payload = {**VALID_MANIFEST, **override}
    with pytest.raises(PluginManifestError):
        parse_plugin_manifest(payload)


def test_parse_manifest_allows_mcp_only_and_ignores_unknown_fields():
    parsed = parse_plugin_manifest(
        {
            **VALID_MANIFEST,
            "skills": [],
            "mcp_servers": [{"id": "fs-ro", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}],
            "future_field": {"anything": True},
        }
    )
    assert parsed.skills == ()
    assert parsed.mcp_servers[0]["id"] == "fs-ro"


# ============ zip 安装 ============


def test_install_plugin_from_zip_bytes_happy_path():
    manifest = install_plugin_from_zip_bytes(_build_zip(VALID_MANIFEST))
    root = plugin_loader.plugins_root()
    assert (root / "ops-readonly" / "plugin.json").is_file()
    assert (root / "ops-readonly" / "skills" / "fault-analysis" / "SKILL.md").is_file()
    assert not list(root.glob("*.installing"))
    assert manifest.name == "ops-readonly"


def test_install_plugin_wrapped_in_single_top_dir():
    install_plugin_from_zip_bytes(_build_zip(VALID_MANIFEST, wrap_dir="ops-readonly"))
    assert (plugin_loader.plugins_root() / "ops-readonly" / "plugin.json").is_file()


def test_install_plugin_rejects_duplicate():
    install_plugin_from_zip_bytes(_build_zip(VALID_MANIFEST))
    with pytest.raises(PluginInstallError, match="已存在"):
        install_plugin_from_zip_bytes(_build_zip(VALID_MANIFEST))


def test_install_plugin_rejects_missing_manifest():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("README.md", "no manifest here")
    with pytest.raises(PluginInstallError, match="plugin.json"):
        install_plugin_from_zip_bytes(buffer.getvalue())


def test_install_plugin_rejects_missing_declared_skill():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps({**VALID_MANIFEST, "skills": ["ghost-skill"]}, ensure_ascii=False))
        archive.writestr("skills/fault-analysis/SKILL.md", "---\nname: fault-analysis\n---\n")
    with pytest.raises(PluginInstallError, match="ghost-skill"):
        install_plugin_from_zip_bytes(buffer.getvalue())


def test_install_plugin_rejects_invalid_manifest():
    with pytest.raises(PluginInstallError, match="name 非法"):
        install_plugin_from_zip_bytes(_build_zip({**VALID_MANIFEST, "name": "BAD NAME"}))


def test_install_plugin_rejects_not_zip():
    with pytest.raises(PluginInstallError, match="zip"):
        install_plugin_from_zip_bytes(b"this is not a zip")


def test_install_plugin_rejects_oversize():
    big = b"\0" * (plugin_installer.PLUGIN_ZIP_MAX_BYTES + 1)
    with pytest.raises(PluginInstallError, match="大小上限"):
        install_plugin_from_zip_bytes(big)


def test_install_plugin_rejects_zip_slip():
    files = {"../evil.txt": "outside"}
    with pytest.raises(PluginInstallError, match="路径非法"):
        install_plugin_from_zip_bytes(_build_zip(VALID_MANIFEST, files=files))


def test_safe_extract_enforces_actual_bytes_budget(monkeypatch):
    """伪造 file_size 的压缩炸弹：声明值预检通过后，真实解压字节仍被二次拦截。

    直接对 _safe_extract 单测（预算调小绕过声明值预检），验证按实际字节的第二道闸。
    """
    data = _build_zip(VALID_MANIFEST)
    monkeypatch.setattr(plugin_installer, "PLUGIN_ZIP_MAX_UNCOMPRESSED_BYTES", 32)
    import shutil

    staging = plugin_installer.plugin_dir(VALID_MANIFEST["name"]).with_name("staging-test")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        with pytest.raises(PluginInstallError, match="实际解压总量"):
            plugin_installer._safe_extract(archive, members, staging, "")
    shutil.rmtree(staging, ignore_errors=True)


def test_validate_zip_members_rejects_encrypted_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(VALID_MANIFEST))
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        member = archive.infolist()[0]
        member.flag_bits |= 0x1
        with pytest.raises(PluginInstallError, match="加密成员"):
            plugin_installer._validate_zip_members(archive)


def test_install_plugin_rejects_symlink_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(VALID_MANIFEST, ensure_ascii=False))
        archive.writestr("skills/fault-analysis/SKILL.md", "---\nname: fault-analysis\n---\n")
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(PluginInstallError, match="符号链接"):
        install_plugin_from_zip_bytes(buffer.getvalue())


def test_uninstall_plugin_removes_dir():
    install_plugin_from_zip_bytes(_build_zip(VALID_MANIFEST))
    manifest = uninstall_plugin("ops-readonly")
    assert manifest.name == "ops-readonly"
    assert not (plugin_loader.plugins_root() / "ops-readonly").exists()


def test_uninstall_plugin_missing_raises_not_found():
    with pytest.raises(PluginNotFoundError):
        uninstall_plugin("ghost")


# ============ 启停 / 运行期视图 ============


def _install_default_plugin():
    install_plugin_from_zip_bytes(
        _build_zip(
            {
                **VALID_MANIFEST,
                "mcp_servers": [{"id": "fs-ro", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}],
            }
        )
    )


def _set_enabled_snapshot(names):
    plugin_loader._enabled_state = frozenset(names)


def test_load_plugins_metadata_reflects_db_enabled_snapshot():
    _install_default_plugin()
    _set_enabled_snapshot({"ops-readonly"})
    metadata = load_plugins_metadata()
    assert set(metadata) == {"ops-readonly"}
    assert metadata["ops-readonly"].enabled is True

    _set_enabled_snapshot(set())
    assert load_plugins_metadata()["ops-readonly"].enabled is False


def test_load_plugins_metadata_disabled_when_never_refreshed():
    # 未成功刷新（_enabled_state is None）→ 全部禁用，fail-closed，不读到未确认的启用态
    _install_default_plugin()
    assert load_plugins_metadata()["ops-readonly"].enabled is False


def test_enabled_views_exclude_disabled_plugins():
    _install_default_plugin()
    _set_enabled_snapshot({"ops-readonly"})
    assert enabled_plugin_skill_sources()
    assert enabled_plugin_mcp_servers()
    _set_enabled_snapshot(set())
    assert enabled_plugin_skill_sources() == []
    assert enabled_plugin_mcp_servers() == []


def test_plugin_loader_skips_dir_name_mismatch():
    root = plugin_loader.plugins_root()
    target = root / "other-name"
    target.mkdir(parents=True)
    (target / "plugin.json").write_text(json.dumps(VALID_MANIFEST, ensure_ascii=False), encoding="utf-8")
    clear_metadata_cache()
    assert load_plugins_metadata() == {}


# ============ refresh_plugin_state（DB 快照刷新）============


@pytest.mark.asyncio
async def test_refresh_plugin_state_populates_from_dao(monkeypatch):
    class _Dao:
        async def list_enabled_names(self):
            return {"ops-readonly"}

    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    await refresh_plugin_state()
    assert plugin_loader._enabled_state == frozenset({"ops-readonly"})


@pytest.mark.asyncio
async def test_refresh_plugin_state_fail_closed_on_db_error(monkeypatch):
    class _Dao:
        async def list_enabled_names(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    plugin_loader._enabled_state = frozenset({"stale"})
    await refresh_plugin_state()
    # DB 读失败：清空快照、不兜底旧值
    assert plugin_loader._enabled_state == frozenset()


# ============ service 编排（install / 启停 / 卸载）============


@pytest.mark.asyncio
async def test_service_install_writes_db_and_refreshes(monkeypatch):
    calls = {}

    class _ServiceDao:
        async def upsert_installed(self, **kw):
            calls["upsert"] = kw

    class _RefreshDao:
        async def list_enabled_names(self):
            return {"ops-readonly"}

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _ServiceDao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _RefreshDao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    manifest = await plugin_service.install_plugin(
        _build_zip(VALID_MANIFEST),
        operator_id="u1",
        operator_name="张三",
    )
    assert manifest.name == "ops-readonly"
    assert calls["upsert"]["plugin_name"] == "ops-readonly"
    assert calls["upsert"]["operator_id"] == "u1"
    assert calls["upsert"]["operator_name"] == "张三"
    # 安装后快照刷新：插件已启用
    assert plugin_loader._enabled_state == frozenset({"ops-readonly"})


@pytest.mark.asyncio
async def test_service_install_rolls_back_files_when_db_write_fails(monkeypatch):
    class _FailingDao:
        async def upsert_installed(self, **kw):
            raise RuntimeError("db write failed")

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _FailingDao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    with pytest.raises(RuntimeError):
        await plugin_service.install_plugin(
            _build_zip(VALID_MANIFEST),
            operator_id="u1",
            operator_name="张三",
        )
    # DB 写失败 → 已解包目录被回滚删除
    assert not (plugin_loader.plugins_root() / "ops-readonly").exists()


@pytest.mark.asyncio
async def test_service_set_enabled_calls_dao_and_refreshes(monkeypatch):
    seen = {}

    class _Dao:
        async def set_enabled(self, **kw):
            seen.update(kw)
            return True

        async def list_enabled_names(self):
            return set()

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    ok = await plugin_service.set_plugin_enabled(
        "ops-readonly",
        enabled=False,
        operator_id="u1",
        operator_name="张三",
    )
    assert ok is True
    assert seen["plugin_name"] == "ops-readonly"
    assert seen["enabled"] is False
    assert seen["operator_id"] == "u1"


@pytest.mark.asyncio
async def test_service_set_enabled_reconciles_orphan_plugin(monkeypatch):
    """半装态自愈：盘上有目录、DB 无活跃行时，启停先补建行再落本次启停值。"""
    _install_default_plugin()
    calls = {"upsert": None, "set_enabled": []}

    class _Dao:
        async def set_enabled(self, **kw):
            calls["set_enabled"].append(kw)
            return False  # 模拟 DB 无活跃行

        async def upsert_installed(self, **kw):
            calls["upsert"] = kw

        async def list_enabled_names(self):
            return {"ops-readonly"}

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    ok = await plugin_service.set_plugin_enabled(
        "ops-readonly",
        enabled=True,
        operator_id="u1",
        operator_name="张三",
    )
    assert ok is True
    assert calls["upsert"]["plugin_name"] == "ops-readonly"
    assert calls["upsert"]["version"] == "1.0.0"
    assert calls["upsert"]["display_name"] == "智能运维只读插件"
    # 补行后按本次请求值落启停
    assert calls["set_enabled"][-1]["plugin_name"] == "ops-readonly"
    assert calls["set_enabled"][-1]["enabled"] is True


@pytest.mark.asyncio
async def test_service_set_enabled_returns_false_when_missing_on_disk_and_db(monkeypatch):
    """盘上无目录、DB 无行（真不存在）：不补行，返回 False。"""

    class _Dao:
        async def set_enabled(self, **kw):
            return False

        async def upsert_installed(self, **kw):  # pragma: no cover - 不应被调用
            raise AssertionError("磁盘上没有插件时不应补建行")

        async def list_enabled_names(self):
            return set()

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    ok = await plugin_service.set_plugin_enabled(
        "ghost-plugin",
        enabled=True,
        operator_id="u1",
        operator_name="张三",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_service_uninstall_removes_files_and_marks_deleted(monkeypatch):
    _install_default_plugin()
    seen = {}

    class _Dao:
        async def mark_deleted(self, **kw):
            seen.update(kw)
            return True

        async def list_enabled_names(self):
            return set()

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    manifest = await plugin_service.uninstall_plugin("ops-readonly", operator_id="u1", operator_name="张三")
    assert manifest.name == "ops-readonly"
    assert seen["plugin_name"] == "ops-readonly"
    assert not (plugin_loader.plugins_root() / "ops-readonly").exists()


@pytest.mark.asyncio
async def test_service_list_merges_db_audit_fields(monkeypatch):
    """列表合并 DB 审计字段（创建人/创建时间）；DB 无行的孤儿插件留空。"""
    import types as _types
    from datetime import datetime

    _install_default_plugin()
    orphan_dir = plugin_loader.plugins_root() / "ops-orphan"
    orphan_dir.mkdir()
    (orphan_dir / "plugin.json").write_text(
        json.dumps({**VALID_MANIFEST, "name": "ops-orphan"}, ensure_ascii=False),
        encoding="utf-8",
    )
    clear_metadata_cache()

    db_row = _types.SimpleNamespace(
        plugin_name="ops-readonly",
        create_by_name="张三",
        create_time=datetime(2026, 9, 22, 10, 0, 0),
    )

    class _Dao:
        async def list_active(self):
            return [db_row]

        async def list_enabled_names(self):
            return {"ops-readonly", "ops-orphan"}

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    data = await plugin_service.list_plugins()
    items = {item["name"]: item for item in data["plugins"]}
    assert data["total"] == 2
    assert items["ops-readonly"]["create_by_name"] == "张三"
    assert items["ops-readonly"]["create_time"] == "2026-09-22 10:00:00"
    # DB 无行的孤儿插件：审计字段留空，不报错
    assert items["ops-orphan"]["create_by_name"] == ""
    assert items["ops-orphan"]["create_time"] == ""


@pytest.mark.asyncio
async def test_service_list_paginates_sorted_by_name(monkeypatch):
    """分页：按插件名排序切片，total 为全量数。"""
    plugin_loader.plugins_root().mkdir(parents=True, exist_ok=True)
    for name in ("b-plugin", "a-plugin", "c-plugin"):
        plugin_dir = plugin_loader.plugins_root() / name
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({**VALID_MANIFEST, "name": name}, ensure_ascii=False),
            encoding="utf-8",
        )
    clear_metadata_cache()

    class _Dao:
        async def list_active(self):
            return []

        async def list_enabled_names(self):
            return set()

    monkeypatch.setattr(plugin_service, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_loader, "PluginDao", lambda *a, **k: _Dao())
    monkeypatch.setattr(plugin_service, "_reset_runtime_caches", lambda: None)

    page1 = await plugin_service.list_plugins(limit=2, offset=0)
    page2 = await plugin_service.list_plugins(limit=2, offset=2)
    assert page1["total"] == 3
    assert [item["name"] for item in page1["plugins"]] == ["a-plugin", "b-plugin"]
    assert [item["name"] for item in page2["plugins"]] == ["c-plugin"]


def test_install_plugin_rejects_mcp_server_failing_admission():
    """插件声明的 MCP server 未通过准入（远程非 https）→ 拒绝安装，不落盘。"""
    manifest = {
        **VALID_MANIFEST,
        "mcp_servers": [{"id": "remote", "transport": "streamable_http", "url": "http://remote.example.com/mcp"}],
    }
    with pytest.raises(PluginInstallError, match="未通过准入"):
        install_plugin_from_zip_bytes(_build_zip(manifest))
    assert not (plugin_loader.plugins_root() / "ops-readonly").exists()


def test_mcp_server_admission_view_reports_ok_and_errors():
    """管理页 MCP server 准入视图：通过/未通过 + 原因列表。"""
    ok = plugin_service._mcp_server_admission_view(
        {"id": "fault-db", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}
    )
    assert ok == {"ok": True, "errors": [], "warnings": []}

    bad = plugin_service._mcp_server_admission_view(
        {"id": "fault-db", "transport": "streamable_http", "url": "http://remote.example.com/mcp"}
    )
    assert bad["ok"] is False
    assert any("https_required" in error for error in bad["errors"])

    invalid = plugin_service._mcp_server_admission_view({"id": "x", "transport": "websocket"})
    assert invalid["ok"] is False
    assert invalid["errors"]
