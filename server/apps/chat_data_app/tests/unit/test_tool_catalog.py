"""ToolCatalog：内置/中间件/MCP 工具的统一管理视图。"""

from types import SimpleNamespace

from apps.chat_data_app.agent.tools import catalog as catalog_module
from apps.chat_data_app.agent.tools.catalog import build_tool_catalog
from apps.chat_data_app.agent.tools.registry import SYSTEM_TOOL_APPROVALS


def test_tool_catalog_covers_builtin_and_middleware_tools():
    catalog = build_tool_catalog()
    by_name = catalog.by_name
    assert by_name["file_read"].source == "builtin"
    assert by_name["file_read"].risk == "low"
    assert by_name["file_delete"].risk == "high"
    assert by_name["write_todos"].source == "middleware"
    assert by_name["task"].source == "middleware"
    assert by_name["file_read"].approval == "allow"
    assert by_name["file_read"].display_name == "读取文件"
    assert by_name["data_analyzing_tool"].required_skills
    assert by_name["ask_user_question"].requires_user_interaction is True
    assert by_name["bash_run"].enabled is False
    assert catalog.to_dicts()[0]["display_name"]


def test_tool_catalog_accepts_mcp_and_runtime_tools():
    catalog = build_tool_catalog(
        [
            SimpleNamespace(
                name="mcp_fault_db_info",
                description="db info",
                metadata={"origin": "mcp", "mcp_server_id": "fault"},
            ),
            SimpleNamespace(
                name="mcp_fault_db_read",
                description="read",
                metadata={"origin": "mcp", "approval": "allow", "mcp_server_id": "fault"},
            ),
            SimpleNamespace(name="runtime_custom", description="custom", metadata={}),
        ]
    )
    by_name = catalog.by_name
    assert by_name["mcp_fault_db_info"].source == "mcp"
    assert by_name["mcp_fault_db_info"].approval == "ask"
    assert by_name["mcp_fault_db_info"].risk == "high"
    assert by_name["mcp_fault_db_info"].searchable is True
    assert by_name["mcp_fault_db_read"].approval == "allow"
    assert by_name["mcp_fault_db_read"].risk == "medium"
    assert by_name["runtime_custom"].source == "runtime"


def test_tool_catalog_reads_current_system_approval_map(monkeypatch):
    monkeypatch.setitem(SYSTEM_TOOL_APPROVALS, "file_delete", "ask")
    catalog = build_tool_catalog()
    assert catalog.by_name["file_delete"].approval == "ask"
    assert catalog.approval_policies()["file_delete"] == "ask"


def test_tool_catalog_normalizes_invalid_mcp_approval():
    catalog = build_tool_catalog(
        [
            SimpleNamespace(
                name="mcp_bad",
                description="",
                metadata={"origin": "mcp", "approval": "force"},
            )
        ]
    )
    assert catalog.by_name["mcp_bad"].approval == "ask"
    assert catalog_module.normalize_tool_approval("deny") == "deny"
