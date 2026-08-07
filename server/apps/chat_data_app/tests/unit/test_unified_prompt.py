import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.prompts.unified import build_unified_system_prompt
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions


def test_system_prompt_requires_semantic_discovery_and_detail_read_before_query():
    prompt = build_unified_system_prompt(AgentRuntimeOptions())

    assert "[tool_result_offloaded]" in prompt
    assert "[tool_result_truncated]" in prompt
    assert "source_tool=semantic_tool" in prompt
    assert 'semantic_tool(operation="search_models")' in prompt
    assert "list_fields" in prompt
    assert "file_grep" in prompt
    assert "file_read(file_path=result_path, offset=0, limit=200)" in prompt
    assert "未完成所需字段和关系的实际读取前，禁止调用查询工具" in prompt
    assert "get_field" not in prompt
