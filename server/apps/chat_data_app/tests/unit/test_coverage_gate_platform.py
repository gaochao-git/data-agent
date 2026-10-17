"""平台模块分支覆盖率补强（非本体链路）。

只补 /tmp/cov_base.gate 基线里仍缺失的分支，全部用本地替身隔离：
- 插件 zip 安装：结构/大小/路径/暂存目录等错误分支，zip 字节流在内存构造；
- MCP 管理器：传输构造、schema 转换、健康检查、同步入口兜底；
- 语义质量检查器：无外部依赖的确定性纯函数/守卫分支；
- 会话工作区本地后端：文本分页预算、写入/替换/删除/移动错误分支、目录遍历；
- 上下文压缩：通知回调异常、事件构造、todo 占位、不可达分支说明；
- agent_stream：用量统计、事件构造等纯函数与轻量协程。

不触碰生产代码，不启动真实网络/数据库/LLM。
"""

import asyncio
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app import agent_stream  # noqa: E402
from apps.chat_data_app.agent.mcp import manager as mcp_manager  # noqa: E402
from apps.chat_data_app.agent.mcp.config import MCPServerConfig  # noqa: E402
from apps.chat_data_app.agent.middleware import context_compression  # noqa: E402
from apps.chat_data_app.agent.plugins import installer  # noqa: E402
from apps.chat_data_app.agent.workspace import workspace_local_backend as lb  # noqa: E402
from apps.chat_data_app.services.semantic.run.semantic_quality_checker import (  # noqa: E402
    SemanticQualityChecker,
    _collect_metric_owners,
)

# ===========================================================================
# 插件 zip 安装（agent/plugins/installer.py）
# ===========================================================================


def _build_zip(entries, *, directories=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in directories:
            archive.writestr(zipfile.ZipInfo(name), b"")
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _manifest_bytes(**overrides):
    payload = {"name": "demo", "version": "1.0.0"}
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def plugin_install_root(tmp_path, monkeypatch):
    """把插件安装目录指向 tmp，并让元数据缓存清理成为空操作。"""
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(installer, "plugin_dir", lambda name: root / name)
    monkeypatch.setattr(installer, "clear_metadata_cache", lambda: None)
    return root


def test_validate_zip_members_skips_directory_entries():
    data = _build_zip({"pkg/a.txt": b"payload"}, directories=("pkg/",))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        # 目录成员不参与解压总量累计；有目录成员也不能误报
        installer._validate_zip_members(archive)


def test_validate_zip_members_rejects_member_count_overflow(monkeypatch):
    monkeypatch.setattr(installer, "PLUGIN_ZIP_MAX_MEMBERS", 1)
    data = _build_zip({"a.txt": b"a", "b.txt": b"b"})
    with (
        zipfile.ZipFile(io.BytesIO(data)) as archive,
        pytest.raises(installer.PluginInstallError, match="成员数超过上限"),
    ):
        installer._validate_zip_members(archive)


def test_validate_zip_members_rejects_uncompressed_total_overflow(monkeypatch):
    monkeypatch.setattr(installer, "PLUGIN_ZIP_MAX_UNCOMPRESSED_BYTES", 4)
    data = _build_zip({"big.txt": b"0123456789"})
    with (
        zipfile.ZipFile(io.BytesIO(data)) as archive,
        pytest.raises(installer.PluginInstallError, match="解压总量超过上限"),
    ):
        installer._validate_zip_members(archive)


def test_safe_extract_skips_members_outside_zip_root(tmp_path):
    data = _build_zip({"pkg/plugin.json": _manifest_bytes(), "outside.txt": b"nope"})
    target = tmp_path / "target"
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        installer._safe_extract(archive, archive.infolist(), target, "pkg/")
    assert (target / "plugin.json").is_file()
    assert not (target / "outside.txt").exists()


def test_safe_extract_skips_directory_relative_entries(tmp_path):
    data = _build_zip({"pkg/a.txt": b"a"}, directories=("pkg/sub/",))
    target = tmp_path / "target"
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        installer._safe_extract(archive, archive.infolist(), target, "pkg/")
    assert (target / "a.txt").read_bytes() == b"a"
    # 目录成员被跳过，由文件成员的 mkdir(parents=True) 隐式创建
    assert not (target / "sub").exists()


def test_safe_extract_rejects_escaping_member(tmp_path):
    data = _build_zip({"../evil.txt": b"nope"})
    target = tmp_path / "target"
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive, pytest.raises(installer.PluginInstallError, match="越界"):
        installer._safe_extract(archive, archive.infolist(), target, "")


def test_install_plugin_rejects_empty_payload():
    with pytest.raises(installer.PluginInstallError, match="内容为空"):
        installer.install_plugin_from_zip_bytes(b"")


def test_install_plugin_reports_missing_manifest_key():
    # 只有 plugin.json/ 目录成员：_find_zip_root 认为根目录有 manifest，但 read 时 KeyError
    data = _build_zip({}, directories=("plugin.json/",))
    with pytest.raises(installer.PluginInstallError, match="zip 内缺少 plugin.json"):
        installer.install_plugin_from_zip_bytes(data)


def test_install_plugin_rejects_invalid_manifest_json(plugin_install_root):
    data = _build_zip({"plugin.json": b"{not-json"})
    with pytest.raises(installer.PluginInstallError, match="解析失败"):
        installer.install_plugin_from_zip_bytes(data)


def test_install_plugin_rejects_declared_skill_without_skeleton(plugin_install_root):
    data = _build_zip(
        {
            "plugin.json": _manifest_bytes(skills=["demo-skill"]),
            "skills/other/SKILL.md": b"# other",
        }
    )
    with pytest.raises(installer.PluginInstallError, match="缺少 SKILL.md"):
        installer.install_plugin_from_zip_bytes(data)


def test_install_plugin_cleans_stale_staging_dir(plugin_install_root):
    data = _build_zip({"plugin.json": _manifest_bytes(), "notes.txt": b"hi"})
    staging = plugin_install_root / "demo.installing"
    staging.mkdir()
    (staging / "stale.txt").write_text("stale", encoding="utf-8")

    manifest = installer.install_plugin_from_zip_bytes(data)

    assert manifest.name == "demo"
    assert (plugin_install_root / "demo" / "plugin.json").is_file()
    assert not staging.exists()


def test_install_plugin_rejects_extraction_without_manifest(plugin_install_root, monkeypatch):
    data = _build_zip({"plugin.json": _manifest_bytes(), "notes.txt": b"hi"})
    monkeypatch.setattr(installer, "_safe_extract", lambda *_args, **_kwargs: None)

    with pytest.raises(installer.PluginInstallError, match="解压结果缺少"):
        installer.install_plugin_from_zip_bytes(data)
    assert not (plugin_install_root / "demo.installing").exists()
    assert not (plugin_install_root / "demo").exists()


def test_install_plugin_cleans_staging_on_extract_failure(plugin_install_root, monkeypatch):
    data = _build_zip({"plugin.json": _manifest_bytes(), "notes.txt": b"hi"})

    def _boom(*_args, **_kwargs):
        raise RuntimeError("extract boom")

    monkeypatch.setattr(installer, "_safe_extract", _boom)

    with pytest.raises(RuntimeError, match="extract boom"):
        installer.install_plugin_from_zip_bytes(data)
    assert not (plugin_install_root / "demo.installing").exists()
    assert not (plugin_install_root / "demo").exists()


# ===========================================================================
# MCP 管理器（agent/mcp/manager.py）
# ===========================================================================


@pytest.fixture
def fake_mcp_transports(monkeypatch):
    """把 SDK 传输与 Client 门面全部替换成可观测替身。"""
    captured = {}

    class _FakeClient:
        def __init__(self, server, *, mode=None, read_timeout_seconds=None):
            captured["client"] = {"server": server, "mode": mode, "timeout": read_timeout_seconds}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(mcp_manager, "Client", _FakeClient)
    monkeypatch.setattr(mcp_manager, "StdioServerParameters", lambda **kwargs: {"stdio": kwargs})

    class _FakeHttpClient:
        def __init__(self, *, headers=None):
            captured["http_headers"] = headers

    fake_httpx2 = SimpleNamespace(AsyncClient=_FakeHttpClient)
    monkeypatch.setitem(sys.modules, "httpx2", fake_httpx2)
    monkeypatch.setattr(
        mcp_manager,
        "streamable_http_client",
        lambda url, http_client=None: {"streamable_http": url, "http_client": http_client},
    )
    monkeypatch.setattr(mcp_manager, "sse_client", lambda url, headers=None: {"sse": url, "headers": headers})
    return captured


def test_open_mcp_session_builds_stdio_transport(fake_mcp_transports):
    captured = fake_mcp_transports
    config = MCPServerConfig(server_id="srv", transport="stdio", command="npx", args=("-y", "srv"), env={"A": "1"})

    async def _use():
        async with mcp_manager.open_mcp_session(config, timeout_seconds=7) as client:
            return client

    client = asyncio.run(_use())
    assert client is not None
    assert captured["client"]["server"] == {"stdio": {"command": "npx", "args": ["-y", "srv"], "env": {"A": "1"}}}
    assert captured["client"]["mode"] == "auto"
    assert captured["client"]["timeout"] == 7.0


def test_open_mcp_session_builds_streamable_http_with_headers(fake_mcp_transports):
    captured = fake_mcp_transports
    config = MCPServerConfig(
        server_id="srv",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        headers={"k": "v"},
    )

    async def _use():
        async with mcp_manager.open_mcp_session(config) as client:
            return client

    asyncio.run(_use())
    assert captured["client"]["server"]["streamable_http"] == "https://mcp.example.com/mcp"
    assert captured["client"]["server"]["http_client"] is not None
    assert captured["http_headers"] == {"k": "v"}


def test_open_mcp_session_builds_streamable_http_without_headers_and_sse(fake_mcp_transports):
    captured = fake_mcp_transports
    http_config = MCPServerConfig(server_id="srv", transport="streamable_http", url="https://a.example/mcp")
    sse_config = MCPServerConfig(server_id="srv", transport="sse", url="https://b.example/sse", headers={"h": "1"})

    async def _use(config):
        async with mcp_manager.open_mcp_session(config) as client:
            return client

    asyncio.run(_use(http_config))
    assert captured["client"]["server"] == "https://a.example/mcp"
    asyncio.run(_use(sse_config))
    assert captured["client"]["server"] == {"sse": "https://b.example/sse", "headers": {"h": "1"}}


def test_open_mcp_session_rejects_unknown_transport(fake_mcp_transports):
    config = MCPServerConfig(server_id="srv", transport="websocket", url="https://x.example")

    async def _use():
        async with mcp_manager.open_mcp_session(config):
            pass

    with pytest.raises(ValueError, match="不支持的 MCP transport"):
        asyncio.run(_use())


def test_schema_to_args_model_tolerates_non_dict_property():
    model = mcp_manager._schema_to_args_model(
        "mcp_srv_tool",
        {"type": "object", "properties": {"weird": "not-a-dict"}, "required": []},
    )
    assert set(model.model_fields) == {"weird"}
    assert model.model_fields["weird"].default is None


def test_extract_call_result_text_uses_structured_content_and_error_flags():
    structured_only = SimpleNamespace(
        content=[SimpleNamespace(text=None)],
        structuredContent={"rows": 1},
        isError=False,
        resultType=None,
    )
    assert mcp_manager._extract_call_result_text(structured_only) == '{"rows": 1}'

    unserializable = SimpleNamespace(
        content=[],
        structuredContent=object(),
        isError=False,
        resultType=None,
    )
    text = mcp_manager._extract_call_result_text(unserializable)
    assert text.startswith("<object object at")

    errored = SimpleNamespace(
        content=[SimpleNamespace(text="boom")],
        structuredContent=None,
        isError=True,
        resultType=None,
    )
    assert mcp_manager._extract_call_result_text(errored) == "[mcp tool error] boom"


def test_list_tools_async_empty_configs_caches_empty_list():
    manager = mcp_manager.MCPToolManager(config_loader=lambda: [])

    async def _run():
        return await manager.list_tools_async(), await manager.list_tools_async(force_refresh=True)

    first, second = asyncio.run(_run())
    assert first == [] and second == []
    assert manager._tools_cache == []
    assert manager._cache_fresh() is True


def test_check_health_async_returns_false_when_session_fails(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _failing_factory(_config):
        raise RuntimeError("connect failed")
        yield  # pragma: no cover - 只为构成异步生成器

    manager = mcp_manager.MCPToolManager(
        config_loader=lambda: [MCPServerConfig(server_id="srv", transport="stdio", command="x")],
        session_factory=_failing_factory,
    )
    assert asyncio.run(manager.check_health_async("missing")) is False
    assert asyncio.run(manager.check_health_async("srv")) is False


def test_get_mcp_tool_manager_builds_singleton_when_missing(monkeypatch):
    monkeypatch.setattr(mcp_manager, "_manager", None)
    manager = mcp_manager.get_mcp_tool_manager()
    assert isinstance(manager, mcp_manager.MCPToolManager)
    assert mcp_manager.get_mcp_tool_manager() is manager
    # 清理进程级单例，避免影响其他用例
    monkeypatch.setattr(mcp_manager, "_manager", None)


def test_run_coro_sync_uses_thread_when_loop_running():
    async def _produce():
        return "done"

    async def _inside_loop():
        return mcp_manager._run_coro_sync(_produce())

    assert asyncio.run(_inside_loop()) == "done"


def test_load_mcp_tools_filters_by_server_ids(monkeypatch):
    config = MCPServerConfig(server_id="srv-a", transport="stdio", command="x")
    infos = [
        mcp_manager.MCPToolInfo(
            server_id="srv-a",
            name="alpha",
            qualified_name="mcp_srv-a_alpha",
            description="",
            input_schema={"type": "object", "properties": {}},
        ),
        mcp_manager.MCPToolInfo(
            server_id="srv-b",
            name="beta",
            qualified_name="mcp_srv-b_beta",
            description="",
            input_schema={"type": "object", "properties": {}},
        ),
    ]

    class _FakeManager:
        async def call_tool_async(self, server_id, tool_name, arguments):
            return f"{server_id}:{tool_name}"

        def list_tools(self):
            return list(infos)

    monkeypatch.setattr(mcp_manager, "load_mcp_server_configs", lambda: [config])
    monkeypatch.setattr(mcp_manager, "get_mcp_tool_manager", lambda: _FakeManager())

    tools = mcp_manager.load_mcp_tools(frozenset({"srv-a"}))
    assert [tool.name for tool in tools] == ["mcp_srv-a_alpha"]
    assert [tool.name for tool in mcp_manager.load_mcp_tools()] == [
        "mcp_srv-a_alpha",
        "mcp_srv-b_beta",
    ]

    async def _call():
        return await tools[0].coroutine(alpha=1)

    assert asyncio.run(_call()) == "srv-a:alpha"


# ===========================================================================
# 语义质量检查器（services/semantic/run/semantic_quality_checker.py）
# ===========================================================================


def test_derived_or_composite_skips_unknown_metric_kind():
    raw = []
    SemanticQualityChecker._check_derived_or_composite(
        {"name": "排名", "metric_kind": "ranking"},
        {"排名"},
        raw,
    )
    assert raw == []


def test_unresolved_formula_tokens_skips_blank_names_and_duplicate_tokens():
    tokens = SemanticQualityChecker._unresolved_formula_tokens("x + x + y", {"", "y"})
    # 空名不参与剔除；重复片段只保留一次；已知指标 y 被剔除
    assert tokens == ["x"]


def test_declared_table_keys_skips_invalid_tables_and_reports_key_sets():
    config = {
        "tables": [
            "not-a-dict",
            {"table": ""},
            {"table": "no_keys"},
            {
                "table": "orders",
                "primary_keys": [["id"], ["id"], []],
            },
            {
                "table": "users",
                "unique_indexes": [{"columns": ["email"]}, "raw-entry"],
                "manual_indexes": [
                    {"index_type": "normal", "columns": ["ignored"]},
                    {"index_type": "PRIMARY", "columns": ["uid"]},
                ],
            },
        ]
    }
    declared = SemanticQualityChecker._declared_table_keys(config)
    assert set(declared) == {"orders", "users"}
    assert declared["orders"] == [{"id"}]
    assert {"email"} in declared["users"]
    assert {"uid"} in declared["users"]


def test_add_relationship_edge_skips_blank_side_but_counts_pair():
    seen_pairs: dict = {}
    edges: dict = {}
    raw: list = []
    SemanticQualityChecker._add_relationship_edge(
        {"left_table": "", "right_table": "orders", "join_conditions": []},
        {"orders"},
        raw,
        seen_pairs,
        edges,
    )
    assert ("", "orders", "") in seen_pairs
    assert seen_pairs[("", "orders", "")] == 1
    assert edges == {}
    assert raw == []


def test_collect_duplicate_expressions_skips_blank_expression():
    raw: list = []
    owner = SemanticQualityChecker._collect_duplicate_expressions(
        [{"name": "空表达式", "expression": "   "}],
        raw,
    )
    assert owner == {}
    assert raw == []


def test_collect_metric_owners_skips_invalid_configs_and_metrics():
    name_owners = _collect_metric_owners(
        [
            "bad-config",
            {"name": "  ", "metrics": [{"name": "x", "expression": "SUM(a)"}]},
            {
                "name": "m1",
                "metrics": [
                    {"name": "", "expression": "SUM(a)"},
                    {"name": "delta", "metric_kind": "derived", "base_metric": "x"},
                    {"name": "ok", "expression": " SUM( b ) "},
                ],
            },
        ]
    )
    assert name_owners == {"ok": {"m1": "SUM(B)"}}


def test_relationship_key_coverage_skips_blank_tables():
    raw: list = []
    SemanticQualityChecker._check_relationship_key_coverage(
        [{}, {"left_table": "", "right_table": ""}],
        raw,
        {"orders": [{"id"}]},
    )
    assert raw == []


# ===========================================================================
# 会话工作区本地后端（agent/workspace/workspace_local_backend.py）
# ===========================================================================


@pytest.fixture
def local_workspace(tmp_path):
    root = tmp_path / "chat_data" / "conv"
    for name in ("workspace", "outputs", "uploads"):
        (root / name).mkdir(parents=True)
    runtime = SimpleNamespace(workspace_root=str(root), runtime_skills_dir=tmp_path / "skills")
    return root, runtime


def test_read_text_page_with_zero_max_chars_returns_empty(local_workspace):
    root, _runtime = local_workspace
    path = root / "workspace" / "lines.txt"
    path.write_text("a\nb\n", encoding="utf-8")
    result = lb._read_text_page_from_path(path, "/mnt/user-data/workspace/lines.txt", offset=0, limit=10, max_chars=0)
    assert result["empty"] is True
    assert "lines" not in result


def test_write_local_text_file_rejects_unsupported_mode(local_workspace):
    root, _runtime = local_workspace
    with pytest.raises(ValueError, match="只支持 overwrite"):
        lb._write_local_text_file(str(root), "/mnt/user-data/workspace/a.txt", "x", "append")
    assert not (root / "workspace" / "a.txt").exists()


def test_write_local_text_file_rejects_managed_write_outside_semantic(local_workspace):
    root, _runtime = local_workspace
    with pytest.raises(ValueError, match="内部系统写入只允许"):
        lb._write_local_text_file(
            str(root),
            "/mnt/user-data/workspace/note.txt",
            "x",
            "overwrite",
            allow_managed_semantic_model_write=True,
        )


def test_write_local_text_file_removes_temp_when_replace_fails(local_workspace, monkeypatch):
    root, _runtime = local_workspace

    def _boom(*_args, **_kwargs):
        raise OSError("replace boom")

    monkeypatch.setattr(lb.os, "replace", _boom)
    with pytest.raises(OSError, match="replace boom"):
        lb._write_local_text_file(str(root), "/mnt/user-data/workspace/a.txt", "content", "overwrite")
    assert not (root / "workspace" / "a.txt").exists()
    assert list((root / "workspace").glob(".*.tmp")) == []


def test_replace_local_text_file_error_branches(local_workspace):
    root, _runtime = local_workspace
    workspace = root / "workspace"
    with pytest.raises(ValueError, match="文件不存在"):
        lb._replace_local_text_file(str(root), "/mnt/user-data/workspace/missing.txt", "a", "b")
    (workspace / "dir").mkdir()
    with pytest.raises(ValueError, match="目标是目录"):
        lb._replace_local_text_file(str(root), "/mnt/user-data/workspace/dir", "a", "b")
    (workspace / "binary.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="暂不支持修改二进制文件"):
        lb._replace_local_text_file(str(root), "/mnt/user-data/workspace/binary.bin", "a", "b")


def test_write_local_text_file_managed_guard_rejects_non_semantic_path(local_workspace, monkeypatch):
    """_write_local_text_file 内部第二道闸：`resolve_workspace_path` 被替换为放行时仍拒绝非 YAML。"""
    root, _runtime = local_workspace
    target = root / "workspace" / "note.txt"
    monkeypatch.setattr(lb, "resolve_workspace_path", lambda *_args, **_kwargs: target)

    with pytest.raises(ValueError, match="内部系统写入只允许"):
        lb._write_local_text_file(
            str(root),
            "/mnt/user-data/workspace/note.txt",
            "x",
            "overwrite",
            allow_managed_semantic_model_write=True,
        )
    assert not target.exists()


def test_write_local_tool_result_rejects_non_result_path(local_workspace):
    root, _runtime = local_workspace
    with pytest.raises(ValueError, match="内部工具结果只能写入系统结果目录"):
        lb._write_local_tool_result(str(root), "/mnt/user-data/workspace/x.json", "{}")


def test_load_local_artifact_payload_returns_empty_for_invalid_or_unreadable(local_workspace, monkeypatch):
    root, _runtime = local_workspace
    # 技能路径由 resolve_workspace_path 直接拒绝 → ValueError 分支
    assert lb._load_local_artifact_payload(str(root), "/mnt/skills/demo/SKILL.md") == {}

    artifact = root / "outputs" / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")

    def _boom(**_kwargs):
        raise OSError("read boom")

    monkeypatch.setattr(lb, "parse_output_artifact", _boom)
    assert lb._load_local_artifact_payload(str(root), "/mnt/user-data/outputs/artifact.json") == {}


def test_sorted_local_children_returns_none_on_oserror(tmp_path, monkeypatch):
    def _boom(self):
        raise OSError("iterdir boom")

    monkeypatch.setattr(Path, "iterdir", _boom)
    assert lb._sorted_local_children(tmp_path) is None


def test_local_entry_visible_path_and_entry_return_none_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    child = tmp_path / "outside.txt"
    child.write_text("x", encoding="utf-8")

    assert lb._local_entry_visible_path(child, is_skill=False, visible_dir="", root=root) is None
    assert lb._local_dir_entry(child, child.stat(), False, is_skill=False, visible_dir="", root=root) is None
    # 技能路径不做越界转换
    assert lb._local_entry_visible_path(child, is_skill=True, visible_dir="/mnt/skills/demo", root=root) == (
        "/mnt/skills/demo/outside.txt"
    )


def test_visit_local_dir_limit_and_unreadable_children(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text("x", encoding="utf-8")

    entries: list = []
    monkeypatch.setattr(lb, "_MAX_LIST_ENTRIES", 1)
    lb._visit_local_dir(root, "", 0, entries=entries, root=root, is_skill=False, max_depth=3)
    assert len(entries) == 1

    prefilled = [{"path": "/mnt/user-data/workspace/keep.txt"}]
    lb._visit_local_dir(root, "", 0, entries=prefilled, root=root, is_skill=False, max_depth=3)
    assert prefilled == [{"path": "/mnt/user-data/workspace/keep.txt"}]

    monkeypatch.setattr(lb, "_sorted_local_children", lambda _directory: None)
    unreadable_entries: list = []
    assert lb._visit_local_dir(root, "", 0, entries=unreadable_entries, root=root, is_skill=False, max_depth=3) is None
    assert unreadable_entries == []


def test_visit_local_dir_skips_children_with_stat_failure():
    class _BrokenChild:
        name = "broken.txt"

        def is_file(self):
            return True

        def is_symlink(self):
            return False

        def stat(self):
            raise OSError("stat boom")

    class _BrokenDir:
        def iterdir(self):
            return [_BrokenChild()]

    entries: list = []
    lb._visit_local_dir(_BrokenDir(), "", 0, entries=entries, root=Path("/tmp/none"), is_skill=False, max_depth=2)
    assert entries == []


def test_visit_local_dir_skips_entries_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.txt").write_text("x", encoding="utf-8")

    entries: list = []
    lb._visit_local_dir(outside, "", 0, entries=entries, root=root, is_skill=False, max_depth=2)
    assert entries == []


def test_list_local_workspace_dir_missing_target(local_workspace):
    _root, runtime = local_workspace
    with pytest.raises(FileNotFoundError, match="目录不存在"):
        lb._list_local_workspace_dir(runtime, "/mnt/user-data/workspace/missing", 1)


def test_delete_and_move_reject_symlink_guard(local_workspace, monkeypatch):
    root, _runtime = local_workspace
    link = root / "workspace" / "link.txt"
    link.symlink_to(root / "workspace")
    monkeypatch.setattr(lb, "_resolve_local_mutable_path", lambda *_args, **_kwargs: link)

    with pytest.raises(ValueError, match="不允许操作符号链接"):
        lb._delete_local_path(str(root), "/mnt/user-data/workspace/link.txt", False, "file")
    with pytest.raises(ValueError, match="不允许操作符号链接"):
        lb._move_local_path(
            str(root),
            "/mnt/user-data/workspace/link.txt",
            "/mnt/user-data/workspace/dst.txt",
        )


def test_move_local_path_requires_existing_destination_parent(local_workspace):
    root, _runtime = local_workspace
    source = root / "workspace" / "src.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="目标父目录不存在"):
        lb._move_local_path(
            str(root),
            "/mnt/user-data/workspace/src.txt",
            "/mnt/user-data/workspace/missing/dst.txt",
        )


# ===========================================================================
# 上下文压缩（agent/middleware/context_compression.py）
# ===========================================================================


def test_notify_compression_started_swallows_callback_error():
    def _boom():
        raise RuntimeError("callback boom")

    # 回调异常不得影响压缩主流程
    context_compression._notify_compression_started(_boom)
    context_compression._notify_compression_started(None)


def test_format_todo_snapshot_lines_skips_non_dict_items():
    lines, unfinished = context_compression.format_todo_snapshot_lines(
        [{"content": "任务A", "status": "pending"}, "bad-item", {"content": "任务B", "status": "completed"}]
    )
    assert lines == ["1. [待办] 任务A", "2. [已完成] 任务B"]
    assert unfinished == 1


def test_emit_compression_started_event_skips_when_event_builder_returns_none(monkeypatch):
    emitted: list = []
    monkeypatch.setattr(context_compression, "_build_context_compression_started_event", lambda _messages: None)
    monkeypatch.setattr(context_compression, "_emit_context_compression_event", emitted.append)
    context_compression._emit_compression_started_event([HumanMessage(content="q")])
    assert emitted == []


def test_build_context_compression_started_event_handles_empty_and_blank_ids():
    empty = context_compression._build_context_compression_started_event([])
    assert empty["turn_id"] == ""
    assert empty["phase"] == "started"

    no_human = context_compression._build_context_compression_started_event([AIMessage(content="answer")])
    assert no_human["turn_id"] == ""

    blank_id = context_compression._build_context_compression_started_event([HumanMessage(content="q", id="")])
    assert blank_id["turn_id"] == ""


def test_emit_context_compression_event_swallows_stream_writer_error(monkeypatch):
    def _boom():
        raise RuntimeError("no stream writer")

    monkeypatch.setattr(context_compression, "get_stream_writer", _boom)
    context_compression._emit_context_compression_event({"id": "e1"})


def test_non_negative_int_falls_back_for_invalid_values():
    assert context_compression._non_negative_int("abc") == 0
    assert context_compression._non_negative_int(object()) == 0
    assert context_compression._non_negative_int(-3) == 0
    assert context_compression._non_negative_int("7") == 7


def test_neutralize_stale_todo_tool_messages_replaces_multiple_entries():
    first = ToolMessage("snapshot-1", tool_call_id="c1", name="write_todos")
    second = ToolMessage("snapshot-2", tool_call_id="c2", name="write_todos")
    human = HumanMessage(content="q")

    neutralized = context_compression.neutralize_stale_todo_tool_messages([first, human, second])

    assert neutralized[0] is not first and neutralized[2] is not second
    assert neutralized[0].content.startswith("[todo 列表已由")
    assert neutralized[2].content.startswith("[todo 列表已由")
    assert neutralized[1] is human


@pytest.mark.asyncio
async def test_build_context_compression_update_returns_no_update_on_summary_retry(monkeypatch):
    async def _fake_compress(*_args, **_kwargs):
        return [HumanMessage(content="q")], "旧摘要", {"triggered": True, "summary_retry_next_turn": True}

    monkeypatch.setattr(context_compression, "compress_messages_for_state", _fake_compress)
    updates, summary, report = await context_compression.build_context_compression_update(
        [HumanMessage(content="q")],
        static_input_tokens=0,
        compression_trigger_tokens=1,
        hard_input_limit_tokens=1000,
        compression_target_tokens=1,
    )
    assert updates is None
    assert summary == "旧摘要"
    assert report["summary_retry_next_turn"] is True


@pytest.mark.asyncio
async def test_middleware_raises_when_report_exceeds_hard_limit(monkeypatch):
    monkeypatch.setattr(
        context_compression.ContextRequestBudget,
        "configure",
        lambda self, system_prompt, tools, model_id=None: SimpleNamespace(
            hard_input_limit_tokens=10,
            compression_trigger_tokens=1,
            compression_target_tokens=1,
        ),
    )
    monkeypatch.setattr(
        context_compression,
        "get_context_compression_config",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(context_compression, "_get_summary_input_limit_tokens", lambda _model: None)

    async def _fake_update(*_args, **_kwargs):
        return None, None, {"estimated_tokens_after": 999}

    monkeypatch.setattr(context_compression, "build_context_compression_update", _fake_update)
    monkeypatch.setattr(context_compression, "get_stream_writer", lambda: (lambda *_args: None))

    middleware = context_compression.create_context_compression_middleware(
        system_prompt="系统提示词",
        state_schema=dict,
    )
    with pytest.raises(context_compression.ContextInputLimitExceededError):
        await middleware.abefore_model({"messages": [HumanMessage(content="q", id="t1")]}, SimpleNamespace())


# ===========================================================================
# agent_stream（agent_stream.py）
# ===========================================================================


def test_is_omlx_prefill_memory_rejected_falls_back_to_message():
    class _Err(Exception):
        body = {"error": {"code": "other_code"}}

        def __str__(self):
            return "plain failure"

    assert agent_stream._is_omlx_prefill_memory_rejected(_Err()) is False
    assert agent_stream._is_omlx_prefill_memory_rejected(RuntimeError("prefill_memory_exceeded")) is True


def test_tool_duration_in_window_sums_only_valid_tool_rows():
    breakdown = [
        "not-a-dict",
        {"role": "assistant", "started_at_ms": 100, "ended_at_ms": 200},
        {"role": "tool", "started_at_ms": 100, "ended_at_ms": 100},
        {"role": "tool", "started_at_ms": 50, "ended_at_ms": 400},
        {"role": "tool", "started_at_ms": 120, "ended_at_ms": 180},
        {"role": "tool", "started_at_ms": 500, "ended_at_ms": 600},
    ]
    assert agent_stream._tool_duration_in_window(breakdown, 100, 250) == 60
    assert agent_stream._tool_duration_in_window(None, 0, 10) == 0


def test_generation_stats_requires_positive_duration_and_tokens(monkeypatch):
    import time

    now_ms = int(time.time() * 1000)
    assert (
        agent_stream._generation_stats_from_usage(first_token_ms=None, stream_started_wall_ms=now_ms, usage={}) is None
    )
    assert (
        agent_stream._generation_stats_from_usage(
            first_token_ms=10,
            stream_started_wall_ms=now_ms,
            usage={"output_tokens": 0},
        )
        is None
    )
    # 流已开始但统计窗口为 0 → 无法得到正时长
    monkeypatch.setattr(agent_stream.time, "time", lambda: now_ms / 1000)
    assert (
        agent_stream._generation_stats_from_usage(
            first_token_ms=10,
            stream_started_wall_ms=now_ms,
            usage={"output_tokens": 10},
        )
        is None
    )
    # 正常路径：窗口 1000ms，其中 250ms 为工具耗时 → 生成时长 750ms
    stats = agent_stream._generation_stats_from_usage(
        first_token_ms=10,
        stream_started_wall_ms=now_ms - 1000,
        usage={
            "output_tokens": 30,
            "breakdown": [{"role": "tool", "started_at_ms": now_ms - 900, "ended_at_ms": now_ms - 650}],
        },
    )
    assert stats is not None
    assert stats["duration_ms"] == 750
    assert stats["output_tokens"] == 30
    assert stats["tokens_per_second"] == 40


def test_handle_message_chunk_emits_tool_call_chunk_events():
    """仅 tool_call_chunks（无完整 tool_calls）的 chunk 走流式工具事件分支。"""

    class _Chunk:
        content = ""
        tool_calls = []
        tool_call_chunks = [{"name": "demo_tool", "args": '{"x": 1}', "id": "call-1", "index": 0}]

        def model_dump(self):
            return {"content": ""}

    adapter = agent_stream._AgentSseStreamAdapter(agent=SimpleNamespace(), initial_state={}, config={})

    async def _collect():
        return [event async for event in adapter._handle_message_chunk(_Chunk(), {})]

    events = asyncio.run(_collect())
    assert events[0].startswith("event: tool_started")
    assert any(event.startswith("event: tool\n") for event in events)
    assert events[-1].startswith("event: message")


def test_stream_state_set_input_state_and_empty_payload_handlers():
    holder = SimpleNamespace()
    agent_stream._AgentSseStreamAdapter.set_input_state(holder, {"messages": []})
    assert holder.initial_state == {"messages": []}

    stream_holder = SimpleNamespace(latest_token_usage=None, archived_turn_messages=[], _queued_events=[])
    assert agent_stream._AgentSseStreamAdapter._handle_token_usage_payload(stream_holder, {}) == []
    assert (
        agent_stream._AgentSseStreamAdapter._handle_context_compression_payload(stream_holder, {"event": "bad"}) == []
    )
    assert (
        agent_stream._AgentSseStreamAdapter._handle_inbox_admitted_payload(stream_holder, {"message_ids": "bad"}) == []
    )
    # 空 delta 的子智能体 delta 事件被丢弃（流式拼接无内容可展示）
    assert (
        agent_stream._AgentSseStreamAdapter._handle_subagent_tool_payload(
            stream_holder,
            {"event": {"event_type": "subagent_message_delta", "delta": ""}},
        )
        == []
    )


@pytest.mark.asyncio
async def test_admit_agent_inbox_messages_resolves_and_persists(monkeypatch):
    resolved = []
    persisted = []

    async def _fake_resolve(message, **_kwargs):
        resolved.append(message)
        return message.model_copy(update={"content": message.content + "+ref"})

    async def _fake_persist(callbacks, messages):
        persisted.append([message.content for message in messages])

    monkeypatch.setattr(agent_stream, "_resolve_inbox_message_references", _fake_resolve)
    monkeypatch.setattr(agent_stream, "_persist_admitted_inbox_messages", _fake_persist)
    callbacks = SimpleNamespace(workspace_root="/tmp/ws", user_id="u1", thread_id="t1")
    messages = [HumanMessage(content="q1"), HumanMessage(content="q2")]

    await agent_stream._admit_agent_inbox_messages(callbacks, messages)

    assert [message.content for message in messages] == ["q1+ref", "q2+ref"]
    assert persisted == [["q1+ref", "q2+ref"]]
    # 解析回调收到的是替换前的原消息对象
    assert [message.content for message in resolved] == ["q1", "q2"]


@pytest.mark.asyncio
async def test_mark_agent_waiting_for_user_skips_without_run_id_and_marks_with_run_id(monkeypatch):
    called = []

    async def _fake_mark(*args):
        called.append(args)

    monkeypatch.setattr(agent_stream.agent_inbox_service, "mark_waiting_for_user", _fake_mark)

    question = {"id": "q1"}
    skip = SimpleNamespace(run_id=None, effective_turn_id="turn-1", username=None, user_id=None, owner_token="")
    await agent_stream._mark_agent_waiting_for_user(skip, question)
    assert "turn_id" not in question
    assert called == []

    mark = SimpleNamespace(run_id="run-1", effective_turn_id="turn-1", username=None, user_id=None, owner_token="tok")
    await agent_stream._mark_agent_waiting_for_user(mark, question)
    assert question["turn_id"] == "turn-1"
    assert called == [("run-1", question, "system", "system", "tok")]
