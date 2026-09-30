"""MCP 工具两级加载单测：BM25 检索目录、``mcp_tool_search`` 工具与发现中间件。

- ``catalog``：指纹同步失效、BM25 检索相关性、server 清单描述；
- ``service/tool``：检索结果 JSON 契约（发现中间件按此解析）与无命中提示；
- ``middleware``：检索模式下按"始终挂载 + 已发现"过滤模型请求工具，
  检索结果并入 ``discovered_tool_names``。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from langchain_core.messages import ToolMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from apps.chat_data_app.agent.agent_state import merge_discovered_tool_names  # noqa: E402
from apps.chat_data_app.agent.mcp.catalog import (  # noqa: E402
    MCP_TOOL_SEARCH_TOOL_NAME,
    MCPCatalog,
    MCPToolSearchService,
    ToolCatalogEntry,
    create_mcp_tool_search_tool,
    warn_long_mcp_descriptions,  # noqa: F401  # 测试按模块属性引用 warn_long_mcp_descriptions，属有意重导出
)
from apps.chat_data_app.agent.mcp.manager import MCPToolInfo  # noqa: E402
from apps.chat_data_app.agent.middleware.mcp_tool_discovery import (  # noqa: E402
    MCPToolDiscoveryMiddleware,
)


def _info(server_id: str, name: str, description: str, schema: dict | None = None, label: str = "") -> MCPToolInfo:
    return MCPToolInfo(
        server_id=server_id,
        name=name,
        qualified_name=f"mcp_{server_id}_{name}",
        description=description,
        input_schema=schema or {"type": "object", "properties": {}},
        label=label,
    )


def SSH_OVERVIEW():
    return _info(
        "ssh",
        "system_overview",
        "查看系统整体运行状态：CPU、内存、磁盘使用率",
        {"type": "object", "properties": {"ip": {"type": "string", "description": "目标主机地址"}}},
    )


def LOG_GREP():
    return _info(
        "fault",
        "log_grep",
        "在服务器日志文件中按关键词检索错误记录",
        {"type": "object", "properties": {"keyword": {"type": "string", "description": "检索关键词"}}},
    )


def DB_SLOW():
    return _info("db", "slow_query", "列出数据库最近出现的慢查询语句")


class FakeManager:
    """只提供枚举的假 manager；list_calls 记录调用次数验证 TTL 复用。"""

    def __init__(self, tools):
        self._tools = tools
        self.list_calls = 0

    async def list_tools_async(self, force_refresh: bool = False):
        self.list_calls += 1
        return list(self._tools)


# ============ MCPCatalog ============


def test_catalog_sync_rebuilds_only_on_fingerprint_change():
    catalog = MCPCatalog()
    assert catalog.sync([SSH_OVERVIEW(), LOG_GREP()]) is True
    first = catalog.fingerprint
    assert catalog.sync([SSH_OVERVIEW(), LOG_GREP()]) is False
    assert catalog.fingerprint == first
    # 描述变化 → 指纹变化 → 重建
    changed = _info("ssh", "system_overview", "改过的描述")
    assert catalog.sync([changed, LOG_GREP()]) is True


def test_catalog_search_ranks_relevant_tool_first():
    catalog = MCPCatalog()
    catalog.sync([SSH_OVERVIEW(), LOG_GREP(), DB_SLOW()])
    hits = [entry.qualified_name for entry in catalog.search("CPU 内存 状态")]
    assert hits and hits[0] == "mcp_ssh_system_overview"
    hits = [entry.qualified_name for entry in catalog.search("日志 错误 检索")]
    assert hits and hits[0] == "mcp_fault_log_grep"


def test_catalog_search_param_docs_participate_in_retrieval():
    catalog = MCPCatalog()
    catalog.sync([SSH_OVERVIEW(), LOG_GREP()])
    hits = [entry.qualified_name for entry in catalog.search("目标主机地址")]
    assert hits and hits[0] == "mcp_ssh_system_overview"


def test_catalog_search_empty_query_and_no_match_return_empty():
    catalog = MCPCatalog()
    catalog.sync([SSH_OVERVIEW()])
    assert catalog.search("   ") == []
    assert catalog.search("qazxsw_edc") == []


def test_catalog_search_respects_limit_and_excluded_names():
    catalog = MCPCatalog()
    catalog.sync([SSH_OVERVIEW(), LOG_GREP(), DB_SLOW()])
    hits = catalog.search("查询 语句 检索 状态", limit=2)
    assert len(hits) <= 2
    excluded = frozenset({entry.qualified_name for entry in catalog.search("查询", limit=1)})
    assert all(entry.qualified_name not in excluded for entry in catalog.search("查询", excluded_names=excluded))


def test_catalog_describe_servers_counts_tools():
    catalog = MCPCatalog()
    catalog.sync([SSH_OVERVIEW(), LOG_GREP(), DB_SLOW()])
    assert catalog.describe_servers() == [
        {"server_id": "db", "tool_count": 1},
        {"server_id": "fault", "tool_count": 1},
        {"server_id": "ssh", "tool_count": 1},
    ]


def test_catalog_entry_lookup():
    catalog = MCPCatalog()
    catalog.sync([SSH_OVERVIEW()])
    entry = catalog.entry("mcp_ssh_system_overview")
    assert isinstance(entry, ToolCatalogEntry)
    assert entry.server_id == "ssh"
    assert catalog.entry("missing") is None


def test_catalog_label_participates_in_retrieval_and_summary():
    """tool_labels 展示名既进 BM25 检索域，也随检索摘要下发给模型。"""
    labeled = _info("ssh", "system_overview", "查看系统整体运行状态", label="系统概览巡检")
    other = LOG_GREP()
    catalog = MCPCatalog()
    catalog.sync([labeled, other])
    # 仅按中文展示名检索也能命中（描述里没有"巡检"二字）
    hits = [entry.qualified_name for entry in catalog.search("概览 巡检")]
    assert hits and hits[0] == "mcp_ssh_system_overview"
    entry = catalog.entry("mcp_ssh_system_overview")
    assert entry.label == "系统概览巡检"
    assert entry.summary()["display_name"] == "系统概览巡检"
    # 无 label 的条目摘要不携带 display_name 键
    assert "display_name" not in catalog.entry("mcp_fault_log_grep").summary()


@pytest.mark.asyncio
async def test_service_search_text_summary_includes_display_name():
    service = MCPToolSearchService(FakeManager([_info("ssh", "system_overview", "查看系统状态", label="系统概览")]))
    payload = json.loads(await service.search_text("系统 状态"))
    assert payload["results"][0]["display_name"] == "系统概览"


def test_catalog_sync_rebuilds_on_label_change():
    """展示名变化必须触发目录指纹失效重建。"""
    catalog = MCPCatalog()
    catalog.sync([_info("ssh", "system_overview", "查看系统状态")])
    assert catalog.sync([_info("ssh", "system_overview", "查看系统状态", label="系统概览")]) is True


# ============ MCPToolSearchService / 工具 ============


@pytest.mark.asyncio
async def test_service_search_text_hit_contract():
    manager = FakeManager([SSH_OVERVIEW(), LOG_GREP()])
    service = MCPToolSearchService(manager)
    payload = json.loads(await service.search_text("CPU 状态"))
    assert payload["count"] == 1
    assert payload["results"][0]["name"] == "mcp_ssh_system_overview"
    assert payload["results"][0]["server_id"] == "ssh"
    assert "guidance" in payload
    # 二次检索同一清单：目录指纹未变，不重建索引
    fingerprint_before = service.catalog.fingerprint
    await service.search_text("日志 错误")
    assert service.catalog.fingerprint == fingerprint_before


@pytest.mark.asyncio
async def test_service_search_text_no_hit_lists_servers():
    # 中文按单字切分，查询词必须与目录文档完全无公共 token 才算无命中
    service = MCPToolSearchService(FakeManager([SSH_OVERVIEW()]))
    payload = json.loads(await service.search_text("qzxwv99_notexist"))
    assert payload["count"] == 0
    assert {"server_id": "ssh", "tool_count": 1} in payload["servers"]
    assert "guidance" in payload


@pytest.mark.asyncio
async def test_service_search_text_server_id_filter():
    service = MCPToolSearchService(FakeManager([SSH_OVERVIEW(), LOG_GREP()]))
    payload = json.loads(await service.search_text("状态 检索", server_id="fault"))
    assert all(item["server_id"] == "fault" for item in payload["results"])


@pytest.mark.asyncio
async def test_mcp_tool_search_tool_invocation():
    service = MCPToolSearchService(FakeManager([SSH_OVERVIEW()]))
    tool = create_mcp_tool_search_tool(service)
    assert tool.name == MCP_TOOL_SEARCH_TOOL_NAME
    schema = tool.args_schema.model_json_schema()
    assert set(schema["properties"]) == {"query", "server_id", "limit", "intent"}
    result = await tool.coroutine(query="CPU 状态", intent="检索系统状态工具")
    payload = json.loads(result)
    assert payload["count"] == 1


def test_warn_long_mcp_descriptions_logs_warning(monkeypatch):
    from apps.chat_data_app.agent.mcp import catalog as catalog_module

    warned: list[str] = []
    monkeypatch.setattr(
        catalog_module.logger,
        "warning",
        lambda message, *args, **kwargs: warned.append(message % args if args else message),
    )
    long_tool = _info("ssh", "verbose", "长" * 501)
    short_tool = _info("ssh", "brief", "短描述")
    catalog_module.warn_long_mcp_descriptions([long_tool, short_tool])
    assert warned and "verbose" in warned[0]


# ============ MCPToolDiscoveryMiddleware ============


class FakeRequest:
    """轻量 ModelRequest 替身：记录 override 调用。"""

    def __init__(self, tools, state=None, messages=None):
        self.tools = tools
        self.state = state if state is not None else {}
        self.messages = messages or []
        self.overrides = None

    def override(self, **kwargs):
        self.overrides = kwargs
        return self


class _Args(BaseModel):
    query: str = ""


def _named_tool(name: str):
    from langchain_core.tools import StructuredTool

    return StructuredTool(name=name, description=name, args_schema=_Args, coroutine=lambda **kw: None)


def _request_tools():
    return [
        _named_tool("file_read"),
        _named_tool("mcp_ssh_system_overview"),
        _named_tool("mcp_fault_log_grep"),
        _named_tool("mcp_db_slow_query"),
    ]


@pytest.mark.asyncio
async def test_middleware_filters_tools_by_discovered_and_always_mounted():
    middleware = MCPToolDiscoveryMiddleware(
        search_active=True,
        always_mounted_tools=frozenset({"mcp_fault_log_grep"}),
    )
    state = {"discovered_tool_names": ["mcp_ssh_system_overview"]}
    request = FakeRequest(_request_tools(), state=state)
    result = await middleware.awrap_model_call(request, lambda req: _async_identity(req))
    assert request.overrides is not None
    exposed = [tool.name for tool in request.overrides["tools"]]
    assert exposed == ["file_read", "mcp_ssh_system_overview", "mcp_fault_log_grep"]
    assert result is request


@pytest.mark.asyncio
async def test_middleware_falls_back_to_history_discovery_when_state_empty():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    history_message = ToolMessage(
        content=json.dumps(
            {
                "count": 1,
                "results": [{"name": "mcp_db_slow_query", "server_id": "db"}],
            }
        ),
        name=MCP_TOOL_SEARCH_TOOL_NAME,
        tool_call_id="call-1",
    )
    request = FakeRequest(_request_tools(), state={}, messages=[history_message])
    await middleware.awrap_model_call(request, lambda req: _async_identity(req))
    exposed = [tool.name for tool in request.overrides["tools"]]
    assert "mcp_db_slow_query" in exposed
    assert "mcp_ssh_system_overview" not in exposed


@pytest.mark.asyncio
async def test_middleware_inactive_or_without_mcp_passes_through():
    request = FakeRequest(_request_tools())
    inactive = MCPToolDiscoveryMiddleware(search_active=False)
    assert await inactive.awrap_model_call(request, _async_identity) is request
    assert request.overrides is None
    # 无 MCP 工具时不过滤
    builtin_only = FakeRequest([_named_tool("file_read")])
    active = MCPToolDiscoveryMiddleware(search_active=True)
    assert await active.awrap_model_call(builtin_only, _async_identity) is builtin_only
    assert builtin_only.overrides is None


@pytest.mark.asyncio
async def test_middleware_no_override_when_all_tools_exposed():
    middleware = MCPToolDiscoveryMiddleware(
        search_active=True,
        always_mounted_tools=frozenset({"mcp_ssh_system_overview", "mcp_fault_log_grep", "mcp_db_slow_query"}),
    )
    request = FakeRequest(_request_tools())
    await middleware.awrap_model_call(request, _async_identity)
    assert request.overrides is None


@pytest.mark.asyncio
async def test_middleware_captures_discovered_names_from_search_result():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "count": 2,
                "results": [
                    {"name": "mcp_ssh_system_overview", "server_id": "ssh"},
                    {"name": "mcp_fault_log_grep", "server_id": "fault"},
                ],
            }
        ),
        name=MCP_TOOL_SEARCH_TOOL_NAME,
        tool_call_id="call-1",
    )

    async def handler(_request):
        return tool_message

    class ToolRequest:
        tool_call = {"name": MCP_TOOL_SEARCH_TOOL_NAME, "args": {"query": "x"}}

    result = await middleware.awrap_tool_call(ToolRequest(), handler)
    assert isinstance(result, Command)
    assert result.update["discovered_tool_names"] == ["mcp_ssh_system_overview", "mcp_fault_log_grep"]
    assert result.update["messages"] == [tool_message]


@pytest.mark.asyncio
async def test_middleware_passes_through_non_search_and_unparsable_results():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)

    class ToolRequest:
        tool_call = {"name": "file_read", "args": {}}

    async def handler(_request):
        return ToolMessage(content="普通结果", name="file_read", tool_call_id="c1")

    assert await middleware.awrap_tool_call(ToolRequest(), handler) == await handler(ToolRequest())

    class SearchRequest:
        tool_call = {"name": MCP_TOOL_SEARCH_TOOL_NAME, "args": {}}

    unparsable = ToolMessage(content="不是 JSON", name=MCP_TOOL_SEARCH_TOOL_NAME, tool_call_id="c2")

    async def broken_handler(_request):
        return unparsable

    assert await middleware.awrap_tool_call(SearchRequest(), broken_handler) is unparsable


def test_middleware_sync_model_call_filters():
    middleware = MCPToolDiscoveryMiddleware(search_active=True)
    state = {"discovered_tool_names": ["mcp_db_slow_query"]}
    request = FakeRequest(_request_tools(), state=state)
    middleware.wrap_model_call(request, lambda req: req)
    assert request.overrides is not None
    exposed = [tool.name for tool in request.overrides["tools"]]
    assert exposed == ["file_read", "mcp_db_slow_query"]


def test_merge_discovered_tool_names_dedupes_and_preserves_order():
    assert merge_discovered_tool_names(["b", "a"], ["a", " c ", "", "b"]) == ["b", "a", "c"]


async def _async_identity(value):
    return value
