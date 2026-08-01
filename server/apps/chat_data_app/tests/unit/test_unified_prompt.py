import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.prompts.unified import build_unified_system_prompt
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions


def test_system_prompt_requires_reading_offloaded_search_dataset_before_query():
    prompt = build_unified_system_prompt(AgentRuntimeOptions())

    assert "[tool_result_offloaded]" in prompt
    assert "[tool_result_truncated]" in prompt
    assert "source_tool=search_dataset" in prompt
    assert "file_read(file_path=result_path, offset=0, limit=200)" in prompt
    assert "未完成所需配置的实际读取前，禁止调用查询工具" in prompt
