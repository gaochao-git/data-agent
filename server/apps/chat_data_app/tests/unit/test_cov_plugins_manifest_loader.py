"""覆盖率补测：``agent/plugins/manifest.py`` 与 ``agent/plugins/loader.py`` 的缺失分支。

只针对 master 基线的解析/扫描分支：manifest 的结构校验错误、读写异常包装，
以及目录扫描与插件技能源过滤（非目录条目、非法 manifest、目录名不一致、
技能目录缺失、缺少 SKILL.md）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from apps.chat_data_app.agent.plugins import loader as plugin_loader
from apps.chat_data_app.agent.plugins.loader import PluginMetadata
from apps.chat_data_app.agent.plugins.manifest import (
    PluginManifest,
    PluginManifestError,
    load_plugin_manifest,
    parse_plugin_manifest,
)

VALID_MANIFEST = {"name": "ops-readonly", "version": "1.0.0", "skills": []}


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({**VALID_MANIFEST, "mcp_servers": ["not-an-object"]}, id="entry-not-object"),
        pytest.param(
            {
                **VALID_MANIFEST,
                "mcp_servers": [
                    {"id": "bad id!", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}
                ],
            },
            id="invalid-server-id",
        ),
        pytest.param(
            {
                **VALID_MANIFEST,
                "mcp_servers": [{"id": "fs-ro", "transport": "streamable_http", "url": "ftp://mcp.example.com"}],
            },
            id="non-http-url",
        ),
        pytest.param({**VALID_MANIFEST, "mcp_servers": "not-a-list"}, id="servers-not-list"),
    ],
)
def test_parse_manifest_rejects_invalid_mcp_structure(payload):
    with pytest.raises(PluginManifestError):
        parse_plugin_manifest(payload)


def test_parse_manifest_rejects_non_object_root():
    with pytest.raises(PluginManifestError, match="JSON 对象"):
        parse_plugin_manifest(["not", "a", "dict"])


def test_load_plugin_manifest_wraps_read_error(tmp_path: Path):
    with pytest.raises(PluginManifestError, match="读取 plugin.json 失败"):
        load_plugin_manifest(tmp_path / "missing-plugin")


def test_load_plugin_manifest_wraps_invalid_json(tmp_path: Path):
    (tmp_path / "plugin.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(PluginManifestError, match="不是合法 JSON"):
        load_plugin_manifest(tmp_path)


@pytest.fixture()
def plugin_root(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(plugin_loader, "plugins_root", lambda chat_data_home=None: root)
    plugin_loader.clear_metadata_cache()
    yield root
    plugin_loader.clear_metadata_cache()


def _write_plugin(root: Path, name: str, skills: list[str]) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plugin.json").write_text(
        json.dumps({**VALID_MANIFEST, "name": name, "skills": skills}, ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


def test_scan_disk_plugins_skips_non_directory_entries(plugin_root: Path):
    (plugin_root / "README.txt").write_text("not a plugin", encoding="utf-8")
    (plugin_root / ".hidden").mkdir()
    plugin_dir = _write_plugin(plugin_root, "ops-readonly", [])

    found = plugin_loader._scan_disk_plugins()

    assert list(found) == ["ops-readonly"]
    assert found["ops-readonly"][1] == plugin_dir


def test_scan_disk_plugins_skips_broken_and_mismatched_manifests(plugin_root: Path):
    broken = plugin_root / "broken"
    broken.mkdir()
    (broken / "plugin.json").write_text("{oops", encoding="utf-8")
    mismatched = plugin_root / "dir-name"
    mismatched.mkdir()
    (mismatched / "plugin.json").write_text(
        json.dumps({**VALID_MANIFEST, "name": "other-name"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert plugin_loader._scan_disk_plugins() == {}


def test_enabled_plugin_skill_sources_skips_missing_dir_and_skill_md(tmp_path: Path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    ok_skill = plugin_dir / "skills" / "ok-skill"
    ok_skill.mkdir(parents=True)
    (ok_skill / "SKILL.md").write_text("# ok", encoding="utf-8")
    no_skill_md = plugin_dir / "skills" / "no-skill-md"
    no_skill_md.mkdir(parents=True)

    manifest = PluginManifest(
        name="demo-plugin",
        version="1.0.0",
        display_name="演示插件",
        description="",
        skills=("ok-skill", "no-skill-md", "missing-skill"),
        mcp_servers=(),
    )
    monkeypatch.setattr(
        plugin_loader,
        "enabled_plugins",
        lambda: [PluginMetadata(manifest=manifest, dir=plugin_dir, enabled=True)],
    )

    assert plugin_loader.enabled_plugin_skill_sources() == [("ok-skill", ok_skill)]
