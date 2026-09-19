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
from apps.chat_data_app.agent.plugins import manifest as plugin_manifest  # noqa: E402
from apps.chat_data_app.agent.plugins.installer import (  # noqa: E402
    PluginInstallError,
    PluginNotFoundError,
    install_plugin_from_zip_bytes,
    uninstall_plugin,
)
from apps.chat_data_app.agent.plugins.loader import (  # noqa: E402
    PLUGIN_DISABLED_MARKER,
    clear_metadata_cache,
    enabled_plugin_mcp_servers,
    enabled_plugin_skill_sources,
    load_plugins_metadata,
    set_plugin_enabled,
)
from apps.chat_data_app.agent.plugins.manifest import (  # noqa: E402
    PluginManifestError,
    parse_plugin_manifest,
)

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
    parsed = parse_plugin_manifest({
        **VALID_MANIFEST,
        "skills": [],
        "mcp_servers": [{"id": "fs-ro", "transport": "stdio", "command": "npx"}],
        "future_field": {"anything": True},
    })
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


def test_install_plugin_rejects_symlink_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(VALID_MANIFEST, ensure_ascii=False))
        archive.writestr("skills/fault-analysis/SKILL.md", "---\nname: fault-analysis\n---\n")
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
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
    install_plugin_from_zip_bytes(_build_zip({
        **VALID_MANIFEST,
        "mcp_servers": [{"id": "fs-ro", "transport": "stdio", "command": "npx"}],
    }))


def test_load_plugins_metadata_and_disable_toggle():
    _install_default_plugin()
    metadata = load_plugins_metadata()
    assert set(metadata) == {"ops-readonly"}
    assert metadata["ops-readonly"].enabled is True

    assert set_plugin_enabled("ops-readonly", enabled=False) is True
    assert load_plugins_metadata()["ops-readonly"].enabled is False
    assert (plugin_loader.plugins_root() / "ops-readonly" / PLUGIN_DISABLED_MARKER).exists()

    assert set_plugin_enabled("ops-readonly", enabled=True) is True
    assert load_plugins_metadata()["ops-readonly"].enabled is True


def test_set_plugin_enabled_missing_returns_false():
    assert set_plugin_enabled("ghost", enabled=False) is False


def test_set_plugin_enabled_rejects_bad_name():
    with pytest.raises(PluginManifestError):
        set_plugin_enabled("../escape", enabled=False)


def test_enabled_views_exclude_disabled_plugins():
    _install_default_plugin()
    assert enabled_plugin_skill_sources()
    assert enabled_plugin_mcp_servers()
    set_plugin_enabled("ops-readonly", enabled=False)
    assert enabled_plugin_skill_sources() == []
    assert enabled_plugin_mcp_servers() == []


def test_plugin_loader_skips_dir_name_mismatch():
    root = plugin_loader.plugins_root()
    target = root / "other-name"
    target.mkdir(parents=True)
    (target / "plugin.json").write_text(json.dumps(VALID_MANIFEST, ensure_ascii=False), encoding="utf-8")
    clear_metadata_cache()
    assert load_plugins_metadata() == {}
