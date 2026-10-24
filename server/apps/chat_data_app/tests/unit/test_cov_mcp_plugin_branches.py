"""MCP / 插件安装分支补齐：配置解析、检索目录、连接门面与 zip 安全边界。"""

from __future__ import annotations

import io
import json
import sys
import types
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel

from apps.chat_data_app.agent.mcp import config as config_module
from apps.chat_data_app.agent.mcp import manager as manager_module
from apps.chat_data_app.agent.mcp.catalog import (
    MCP_TOOL_SEARCH_TOOL_NAME,
    MCPToolSearchService,
    _BM25Index,
    _param_docs,
    create_mcp_tool_call_tool,
    create_mcp_tool_search_tool,
)
from apps.chat_data_app.agent.mcp.config import (
    MCPServerConfig,
    MCPServerConfigError,
    _admit_config,
    load_mcp_server_configs,
    parse_mcp_server_config,
)
from apps.chat_data_app.agent.mcp.manager import (
    MCPToolInfo,
    MCPToolManager,
    _extract_call_result_text,
    _schema_to_args_model,
    load_mcp_tools,
    open_mcp_session,
)
from apps.chat_data_app.agent.plugins import installer as installer_module
from apps.chat_data_app.agent.plugins.installer import PluginInstallError, install_plugin_from_zip_bytes

# ---------------------------------------------------------------------------
# mcp/config.py
# ---------------------------------------------------------------------------


def test_legacy_mcp_env_var_warns_on_module_execution(monkeypatch):
    """模块级兼容告警只在旧环境变量存在时触发；用独立命名空间执行，避免污染已导入模块。"""
    monkeypatch.setenv("CHAT_DATA_MCP_SERVERS", "legacy")
    probe = types.ModuleType("cov_branch_probe_mcp_config")
    probe.__package__ = "apps.chat_data_app.agent.mcp"
    with open(config_module.__file__, encoding="utf-8") as source_file:
        source = source_file.read()
    sys.modules[probe.__name__] = probe
    try:
        exec(compile(source, config_module.__file__, "exec"), probe.__dict__)
    finally:
        sys.modules.pop(probe.__name__, None)
    assert "load_mcp_server_configs" in probe.__dict__


def test_parse_transport_rejects_non_http_url():
    with pytest.raises(MCPServerConfigError, match="http"):
        config_module._parse_transport_fields({"transport": "streamable_http", "url": "ftp://mcp"}, "src")


def test_clean_tool_labels_rejects_blank_value():
    with pytest.raises(MCPServerConfigError, match="空白"):
        config_module._clean_tool_labels({"read": "  "}, "src")


def test_parse_config_rejects_non_bool_enabled():
    with pytest.raises(MCPServerConfigError, match="布尔"):
        parse_mcp_server_config(
            {"id": "srv", "transport": "streamable_http", "url": "https://mcp", "enabled": "yes"},
            source="src",
        )


def test_admit_config_logs_warning_and_rejects_invalid(monkeypatch):
    report = SimpleNamespace(
        warnings=[SimpleNamespace(code="warn", message="低风险")],
        ok=False,
        summary=lambda: "1 error",
    )
    monkeypatch.setattr(config_module, "validate_mcp_server_config", lambda _config: report)
    assert _admit_config(MCPServerConfig(server_id="srv", transport="streamable_http", url="https://x"), "src") is False


def test_load_configs_skips_server_rejected_by_admission(monkeypatch):
    entry = {"id": "srv", "transport": "streamable_http", "url": "https://mcp"}
    monkeypatch.setattr(config_module, "_admit_config", lambda _config, _source: False)
    monkeypatch.setattr(
        "apps.chat_data_app.agent.plugins.loader.enabled_plugin_mcp_servers",
        lambda: [entry],
    )
    assert load_mcp_server_configs() == []


# ---------------------------------------------------------------------------
# mcp/catalog.py
# ---------------------------------------------------------------------------


def test_param_docs_handles_non_dict_props_and_empty_description():
    docs = _param_docs({"properties": {"raw": "oops", "blank": {"description": " "}, "named": {"description": "目标"}}})
    assert docs == "raw blank named 目标"


def test_bm25_search_without_documents_returns_empty_scores():
    assert _BM25Index([]).search("任意") == []


class _FakeManager:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools_async(self, force_refresh: bool = False):
        return list(self._tools)


def _info(name: str) -> MCPToolInfo:
    return MCPToolInfo(
        server_id="ssh",
        name=name,
        qualified_name=f"mcp_ssh_{name}",
        description="查看主机状态",
        input_schema={"type": "object", "properties": {}},
    )


@pytest.mark.asyncio
async def test_lookup_text_skips_blank_dedupes_and_reports_all_missing():
    service = MCPToolSearchService(_FakeManager([_info("overview")]))

    blank = json.loads(await service.lookup_text(["   "]))
    assert blank["count"] == 0

    duplicated = json.loads(await service.lookup_text(["mcp_ssh_overview", "mcp_ssh_overview"]))
    assert duplicated["count"] == 1

    missing = json.loads(await service.lookup_text(["mcp_ssh_ghost"]))
    assert missing["count"] == 0
    assert missing["missing"] == ["mcp_ssh_ghost"]


@pytest.mark.asyncio
async def test_search_tool_select_prefix_and_empty_arguments():
    service = MCPToolSearchService(_FakeManager([_info("overview")]))
    tool = create_mcp_tool_search_tool(service)

    selected = json.loads(await tool.coroutine(query="select:mcp_ssh_overview"))
    assert selected["count"] == 1
    empty = json.loads(await tool.coroutine())
    assert empty["count"] == 0


@pytest.mark.asyncio
async def test_call_tool_rejects_control_name_and_serializes_non_text_result():
    class _Args(BaseModel):
        query: str = ""

    async def _echo(query: str = "") -> str:
        return query

    target = StructuredTool(name="mcp_ssh_overview", description="x", args_schema=_Args, coroutine=_echo)
    proxy = create_mcp_tool_call_tool({target.name: target, MCP_TOOL_SEARCH_TOOL_NAME: target})
    with pytest.raises(ToolException, match="控制工具"):
        await proxy.coroutine(
            name=MCP_TOOL_SEARCH_TOOL_NAME,
            input={},
            state={"discovered_tool_names": [MCP_TOOL_SEARCH_TOOL_NAME]},
        )

    async def _dict_result(query: str = "") -> dict:
        return {"ok": query}

    dict_target = StructuredTool(name="mcp_ssh_metrics", description="x", args_schema=_Args, coroutine=_dict_result)
    dict_proxy = create_mcp_tool_call_tool({dict_target.name: dict_target})
    payload = await dict_proxy.coroutine(
        name=dict_target.name,
        input={"query": "cpu"},
        state={"discovered_tool_names": [dict_target.name]},
    )
    assert json.loads(payload) == {"ok": "cpu"}


# ---------------------------------------------------------------------------
# mcp/manager.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_session_rejects_non_streamable_transport():
    with pytest.raises(ValueError, match="streamable_http"):
        async with open_mcp_session(MCPServerConfig(server_id="srv", transport="stdio", url="")):
            pass


@pytest.mark.asyncio
async def test_open_session_injects_headers_client_and_plain_url(monkeypatch):
    entered: list[object] = []

    class _Client:
        def __init__(self, server, **_kwargs):
            entered.append(server)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(manager_module, "Client", _Client)
    monkeypatch.setattr(manager_module, "streamable_http_client", lambda url, http_client=None: ("wrapped", url))

    with_headers = MCPServerConfig(
        server_id="srv",
        transport="streamable_http",
        url="https://mcp",
        headers={"Authorization": "Bearer x"},
    )
    async with open_mcp_session(with_headers) as client:
        assert client is not None
    async with open_mcp_session(MCPServerConfig(server_id="srv2", transport="streamable_http", url="https://mcp2")):
        pass
    assert entered[0][0] == "wrapped"
    assert entered[1] == "https://mcp2"


def test_schema_to_args_model_treats_non_dict_property_as_empty_object():
    model = _schema_to_args_model("mcp_ssh_raw", {"properties": {"raw": "oops"}})
    assert model.model_fields["raw"].annotation is not None


def test_extract_call_result_prefers_structured_content_and_marks_error():
    result = SimpleNamespace(
        content=[SimpleNamespace()],
        structuredContent={"rows": 1},
        isError=True,
    )
    assert _extract_call_result_text(result) == '[mcp tool error] {"rows": 1}'


@pytest.mark.asyncio
async def test_manager_handles_empty_configs_and_warning_report(monkeypatch):
    empty_manager = MCPToolManager(config_loader=lambda: [], session_factory=lambda _config: None)
    assert await empty_manager.list_tools_async() == []

    @asynccontextmanager
    async def _factory(_config):
        class _Client:
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(name="overview", description="状态", input_schema={})])

        yield _Client()

    monkeypatch.setattr(
        manager_module,
        "validate_mcp_server_tools",
        lambda _server_id, _tools: SimpleNamespace(
            warnings=[SimpleNamespace(code="warn", message="低风险")], ok=True, summary=lambda: "ok"
        ),
    )
    manager = MCPToolManager(config_loader=lambda: [], session_factory=_factory)
    tools, ttl = await manager._list_server_tools(
        MCPServerConfig(server_id="srv", transport="streamable_http", url="https://mcp")
    )
    assert [tool.name for tool in tools] == ["overview"]
    assert ttl is None


def test_load_mcp_tools_filters_by_server_ids(monkeypatch):
    info = _info("overview")

    class _Manager:
        def list_tools(self):
            return [info]

        async def call_tool_async(self, _name, _arguments):
            return "ok"

    monkeypatch.setattr(manager_module, "load_mcp_server_configs", lambda: [object()])
    monkeypatch.setattr(manager_module, "get_mcp_tool_manager", lambda: _Manager())

    assert [tool.name for tool in load_mcp_tools()] == ["mcp_ssh_overview"]
    assert load_mcp_tools(frozenset({"other"})) == []
    assert [tool.name for tool in load_mcp_tools(frozenset({"ssh"}))] == ["mcp_ssh_overview"]


# ---------------------------------------------------------------------------
# agent/plugins/installer.py
# ---------------------------------------------------------------------------


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    monkeypatch.setattr(installer_module, "plugin_dir", lambda name, chat_data_home=None: root / name)
    yield root


def _manifest(name: str = "cov-plugin", skills: tuple[str, ...] = ("cov-skill",)) -> dict:
    return {"name": name, "version": "1.0.0", "skills": list(skills)}


def _zip_bytes(manifest: dict, files: dict[str, str] | None = None, dirs: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        for path, content in (files or {}).items():
            archive.writestr(path, content)
        for directory in dirs:
            archive.writestr(directory, "")
    return buffer.getvalue()


def _skill_file(name: str, body: str = "---\nname: cov-skill\n---\n# skill") -> dict[str, str]:
    return {f"skills/{name}/SKILL.md": body}


def _open_zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def test_zip_member_limit_and_directory_members(monkeypatch):
    data = _zip_bytes(_manifest(), dirs=("skills/",))
    with _open_zip(data) as archive:
        monkeypatch.setattr(installer_module, "PLUGIN_ZIP_MAX_MEMBERS", 1)
        with pytest.raises(PluginInstallError, match="成员数"):
            installer_module._validate_zip_members(archive)
        monkeypatch.setattr(installer_module, "PLUGIN_ZIP_MAX_MEMBERS", 100)
        installer_module._validate_zip_members(archive)


def test_zip_uncompressed_limit(monkeypatch):
    data = _zip_bytes(_manifest(), files={"blob.txt": "0123456789"})
    with _open_zip(data) as archive:
        monkeypatch.setattr(installer_module, "PLUGIN_ZIP_MAX_UNCOMPRESSED_BYTES", 1)
        with pytest.raises(PluginInstallError, match="解压总量"):
            installer_module._validate_zip_members(archive)


def test_safe_extract_ignores_members_outside_zip_root(tmp_path):
    data = _zip_bytes(_manifest(), files={"other.txt": "x"})
    with _open_zip(data) as archive:
        members = archive.infolist()
        installer_module._safe_extract(archive, members, tmp_path / "target", "")
    data_wrapped = io.BytesIO()
    with zipfile.ZipFile(data_wrapped, "w") as archive:
        archive.writestr("root/plugin.json", json.dumps(_manifest()))
        archive.writestr("root/skills/", "")
        archive.writestr("outside.txt", "x")
    with _open_zip(data_wrapped.getvalue()) as archive:
        installer_module._safe_extract(archive, archive.infolist(), tmp_path / "wrapped", "root/")
    assert (tmp_path / "wrapped" / "plugin.json").is_file()
    assert not (tmp_path / "wrapped" / "outside.txt").exists()


def test_safe_extract_rejects_traversal_member(tmp_path):
    data = _zip_bytes(_manifest())
    with _open_zip(data) as archive:
        evil = zipfile.ZipInfo("../evil.txt")
        with pytest.raises(PluginInstallError, match="越界"):
            installer_module._safe_extract(archive, [evil], tmp_path / "target", "")


def test_manifest_mcp_admission_logs_warning(monkeypatch):
    manifest = installer_module.parse_plugin_manifest(
        {**_manifest(), "mcp_servers": [{"id": "srv", "transport": "streamable_http", "url": "https://mcp"}]}
    )
    monkeypatch.setattr(
        installer_module,
        "validate_mcp_server_config",
        lambda _config: SimpleNamespace(
            warnings=[SimpleNamespace(code="warn", message="低风险")], ok=True, summary=lambda: "ok"
        ),
    )
    installer_module._validate_manifest_mcp_servers(manifest)


def test_install_rejects_oversized_skill_md(monkeypatch, plugin_root):
    monkeypatch.setattr(installer_module, "SKILL_MD_MAX_BYTES", 1)
    data = _zip_bytes(_manifest(), files=_skill_file("cov-skill", "---\nname: cov-skill\n---\n# skill"))
    with pytest.raises(PluginInstallError, match="SKILL.md 超过"):
        install_plugin_from_zip_bytes(data)


def test_install_rejects_mismatched_skill_frontmatter(plugin_root):
    data = _zip_bytes(_manifest(), files=_skill_file("cov-skill", "---\nname: other\n---\n"))
    with pytest.raises(PluginInstallError, match="frontmatter.name"):
        install_plugin_from_zip_bytes(data)


def test_install_rejects_empty_payload(plugin_root):
    with pytest.raises(PluginInstallError, match="内容为空"):
        install_plugin_from_zip_bytes(b"")


def test_install_cleans_stale_staging_directory(plugin_root):
    (plugin_root / "cov-plugin.installing").mkdir(parents=True)
    manifest = install_plugin_from_zip_bytes(_zip_bytes(_manifest(), files=_skill_file("cov-skill")))
    assert manifest.name == "cov-plugin"
    assert (plugin_root / "cov-plugin" / "plugin.json").is_file()
    assert not (plugin_root / "cov-plugin.installing").exists()


def test_install_fails_when_extraction_lost_manifest(monkeypatch, plugin_root):
    monkeypatch.setattr(installer_module, "_safe_extract", lambda *_args, **_kwargs: None)
    with pytest.raises(PluginInstallError, match="缺少 plugin.json"):
        install_plugin_from_zip_bytes(_zip_bytes(_manifest(), files=_skill_file("cov-skill")))
    assert not (plugin_root / "cov-plugin.installing").exists()


def test_param_docs_without_properties_returns_empty():
    assert _param_docs({"properties": []}) == ""
    assert _param_docs({"properties": None}) == ""
