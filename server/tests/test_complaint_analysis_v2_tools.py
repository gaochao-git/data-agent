import asyncio
import json
import os
import sys
import types

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("DATA_ROOT", "/tmp/codex-test-data-root")
os.environ.setdefault("COMPLAINT_LAWS_ROOT", "/tmp/codex-test-data-root/laws")

if "docx" not in sys.modules:
    docx_module = types.ModuleType("docx")
    docx_module.Document = object
    sys.modules["docx"] = docx_module

if "diskcache" not in sys.modules:
    diskcache_module = types.ModuleType("diskcache")

    class _DummyCache:
        def __init__(self, *args, **kwargs):
            pass

    diskcache_module.Cache = _DummyCache
    sys.modules["diskcache"] = diskcache_module

if "dotenv" not in sys.modules:
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_module

if "openai" not in sys.modules:
    openai_module = types.ModuleType("openai")
    openai_module.OpenAI = object
    sys.modules["openai"] = openai_module

if "strictyaml" not in sys.modules:
    strictyaml_module = types.ModuleType("strictyaml")

    class _DummyYaml:
        data = {}

    strictyaml_module.YAMLError = Exception
    strictyaml_module.load = lambda *args, **kwargs: _DummyYaml()
    sys.modules["strictyaml"] = strictyaml_module

from apps.complaint_app.opencode_runtime.tools.base_tool import ToolContext
from apps.complaint_app.services.complaint_analysis_v2 import search_law_runner as search_law_runner_module
from apps.complaint_app.services.complaint_analysis_v2 import tool_runtime as tool_runtime_module
from apps.complaint_app.services.complaint_analysis_v2 import orchestrator as orchestrator_module
from utils.storage_paths import resolve_complaint_scope


def test_search_law_tool_executes_independent_runner(monkeypatch):
    captured = {}

    async def fake_search_law(sentence: str, trace_context=None):
        captured["sentence"] = sentence
        captured["trace_context"] = dict(trace_context or {})
        return {
            "query": sentence,
            "task_understanding": {"reported_issue": "测试事项", "scene": "测试场景", "law_scope": ["测试法范围"], "search_target": "测试义务"},
            "related_laws": {"high_relevance": [{"law_name": "测试法", "reason": "高相关"}], "medium_relevance": [], "not_read_but_possible": []},
            "articles": [{"law_name": "测试法", "article": "第一条", "content": "条文内容", "relevance": "与事项相关"}],
            "summary": "ok"
        }

    monkeypatch.setattr(tool_runtime_module, "search_law", fake_search_law)
    tool = tool_runtime_module.search_law_tool()
    context = ToolContext(session_id="outer-session", message_id="tool-1", agent_name=None, metadata={"base_id": "b1", "user_id": "u1", "file_id": "f1"})

    result = asyncio.run(tool.execute(payload={"sentence": "测试事项"}, context=context))

    assert captured["sentence"] == "测试事项"
    assert captured["trace_context"]["base_id"] == "b1"
    assert result["summary"] == "ok"
    assert result["articles"][0]["law_name"] == "测试法"


def test_search_law_runner_uses_independent_session_prompt(monkeypatch):
    captured = {}

    class DummyAsyncLLMClient:
        def __init__(self, *args, **kwargs):
            pass

    class DummyPromptResult:
        def __init__(self):
            self.message = types.SimpleNamespace(parts=[types.SimpleNamespace(content=json.dumps({
                "query": "测试事项",
                "task_understanding": {
                    "reported_issue": "测试事项核心描述",
                    "scene": "测试场景",
                    "law_scope": ["测试法范围"],
                    "search_target": "验证条款"
                },
                "related_laws": {
                    "high_relevance": [{"law_name": "示例法规", "reason": "直接规制该行为"}],
                    "medium_relevance": [],
                    "not_read_but_possible": []
                },
                "articles": [{"law_name": "示例法规", "article": "第一条", "content": "条文内容", "relevance": "直接相关"}],
                "summary": "命中一部法规"
            }, ensure_ascii=False))])
            self.tool_results = [types.SimpleNamespace(name="document_read", arguments={"path": "/tmp/laws/sample.txt"}, output={"metadata": {"ok": True, "path": "/tmp/laws/sample.txt", "truncated": False}, "output": "示例法规全文"})]
            self.stats = {"tool_rounds": 1, "parallel_rounds": 0}

    class DummySessionPrompt:
        def __init__(self, *args, **kwargs):
            pass

        async def prompt(self, **kwargs):
            captured["prompt_kwargs"] = kwargs
            return DummyPromptResult()

    monkeypatch.setattr(search_law_runner_module, "AsyncLLMClient", DummyAsyncLLMClient)
    monkeypatch.setattr(search_law_runner_module, "SessionPrompt", DummySessionPrompt)

    result = asyncio.run(search_law_runner_module.search_law("测试事项", trace_context={"base_id": "b1", "user_id": "u1"}))

    assert captured["prompt_kwargs"]["allowed_tools"] == ["document_read"]
    assert captured["prompt_kwargs"]["record_messages"] is False
    assert captured["prompt_kwargs"]["user_input"]
    assert result["query"] == "测试事项"
    assert result["task_understanding"]["scene"] == "测试场景"
    assert result["articles"][0]["law_name"] == "示例法规"
    assert result["audit"]["tool_rounds"] == 1
    assert result["audit"]["tool_calls"][0]["name"] == "document_read"


def test_summary_stage_uses_scope_context_for_cache_and_save(monkeypatch):
    captured = {}
    scope_context = resolve_complaint_scope("entity", "org_a", "entity_a", "张三")

    async def fake_maybe_load_cached_stage(*args, **kwargs):
        captured["cache_scope_path"] = kwargs["scope_context"].scope_path if kwargs.get("scope_context") else ""
        return None, "", 0

    async def fake_call_stage_model(*args, **kwargs):
        return "摘要阶段输出"

    async def fake_save_stage_result(user_id, base_id, *, stage, markdown, result_payload, base_versions, meta_extra, scope_context=None):
        captured["save_scope_path"] = scope_context.scope_path if scope_context else ""
        captured["stage"] = stage
        captured["markdown"] = markdown
        return result_payload, 1

    monkeypatch.setattr(orchestrator_module, "_maybe_load_cached_stage", fake_maybe_load_cached_stage)
    monkeypatch.setattr(orchestrator_module, "_call_stage_model", fake_call_stage_model)
    monkeypatch.setattr(orchestrator_module, "_save_stage_result", fake_save_stage_result)

    payload, markdown, version, cache_hit = asyncio.run(
        orchestrator_module._run_summary_stage(
            user_id="u1",
            base_id="b1",
            file_id="f1",
            complaint_text="举报原文",
            text_version=1,
            reporter_name="张三",
            reported_entities=["某机构"],
            force=False,
            scope_context=scope_context,
        )
    )

    assert captured["cache_scope_path"] == "org_a/entity_a/张三"
    assert captured["save_scope_path"] == "org_a/entity_a/张三"
    assert captured["stage"] == "summary_v2"
    assert markdown == "摘要阶段输出"
    assert payload["summary_markdown"] == "摘要阶段输出"
    assert cache_hit is False
    assert version == 1



def test_analysis_stage_uses_scope_context_for_cache_and_save(monkeypatch):
    captured = {}
    scope_context = resolve_complaint_scope("entity", "org_a", "entity_a", "张三")

    async def fake_maybe_load_cached_stage(*args, **kwargs):
        captured["cache_scope_path"] = kwargs["scope_context"].scope_path if kwargs.get("scope_context") else ""
        return None, "", 0

    async def fake_save_stage_result(user_id, base_id, *, stage, markdown, result_payload, base_versions, meta_extra, scope_context=None):
        captured["save_scope_path"] = scope_context.scope_path if scope_context else ""
        captured["stage"] = stage
        captured["markdown"] = markdown
        captured["result_payload"] = result_payload
        captured["meta_extra"] = meta_extra
        return result_payload, 1

    class DummyAsyncLLMClient:
        def __init__(self, *args, **kwargs):
            pass

    class DummySessionPrompt:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, **kwargs):
            captured["allowed_tools"] = kwargs.get("allowed_tools")
            captured["system_prompt"] = kwargs.get("system_prompt")
            captured["user_input"] = kwargs.get("user_input")
            yield {"event": "content_delta", "delta": "分析阶段输出"}
            yield {
                "event": "message_end",
                "tool_results": [
                    {
                        "name": "search_law",
                        "output": {
                            "query": "摘要文本",
                            "task_understanding": {"reported_issue": "测试事项", "scene": "测试场景", "law_scope": ["测试法范围"], "search_target": "验证条款"},
                            "related_laws": {"high_relevance": [{"law_name": "测试法", "reason": "高相关"}], "medium_relevance": [], "not_read_but_possible": []},
                            "articles": [{"law_name": "测试法", "article": "第一条", "content": "条文内容", "relevance": "与事项相关"}],
                            "summary": "命中",
                            "audit": {"tool_rounds": 1, "tool_calls": [{"name": "document_read"}]},
                        },
                    }
                ],
            }

    monkeypatch.setattr(orchestrator_module, "_maybe_load_cached_stage", fake_maybe_load_cached_stage)
    monkeypatch.setattr(orchestrator_module, "_save_stage_result", fake_save_stage_result)
    monkeypatch.setattr(orchestrator_module, "AsyncLLMClient", DummyAsyncLLMClient)
    monkeypatch.setattr(orchestrator_module, "SessionPrompt", DummySessionPrompt)

    async def collect_events():
        return [
            item
            async for item in orchestrator_module._stream_analysis_stage(
                user_id="u1",
                base_id="b1",
                file_id="f1",
                complaint_text="举报原文",
                text_version=1,
                summary_version=1,
                summary_markdown="摘要文本",
                reporter_name="张三",
                reported_entities=["某机构"],
                force=False,
                scope_context=scope_context,
            )
        ]

    events = asyncio.run(collect_events())

    assert captured["cache_scope_path"] == "org_a/entity_a/张三"
    assert captured["save_scope_path"] == "org_a/entity_a/张三"
    assert captured["allowed_tools"] == ["search_law"]
    assert "测试法" in json.dumps(captured["result_payload"], ensure_ascii=False)
    assert captured["meta_extra"]["enabled_tools"] == ["search_law"]
    assert captured["meta_extra"]["law_search_audit"]["tool_calls"][0]["name"] == "document_read"
    assert captured["stage"] == "analysis_v2"
    assert captured["markdown"] == "分析阶段输出"
    assert events[-1]["kind"] == "completed"
    assert events[-1]["markdown"] == "分析阶段输出"
    assert events[-1]["version"] == 1
