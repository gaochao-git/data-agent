import os
import sys
import asyncio
import json
import time
import types
import threading
from pathlib import Path


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("DATA_ROOT", "/tmp/codex-test-data-root")

try:
    import langchain.chat_models as chat_models
    if not hasattr(chat_models, "init_chat_model"):
        chat_models.init_chat_model = lambda **kwargs: None
except Exception:
    langchain_module = types.ModuleType("langchain")
    chat_models_module = types.ModuleType("langchain.chat_models")
    chat_models_module.init_chat_model = lambda **kwargs: None
    langchain_module.chat_models = chat_models_module
    sys.modules.setdefault("langchain", langchain_module)
    sys.modules["langchain.chat_models"] = chat_models_module

try:
    import langchain_core.callbacks.manager as callback_manager
    if not hasattr(callback_manager, "adispatch_custom_event"):
        async def _dummy_dispatch_custom_event(*args, **kwargs):
            return None
        callback_manager.adispatch_custom_event = _dummy_dispatch_custom_event
except Exception:
    pass

wordfit_pkg = types.ModuleType("apps.word_fit_app")
wordfit_pkg.__path__ = [os.path.join(ROOT_DIR, "apps", "word_fit_app")]
sys.modules["apps.word_fit_app"] = wordfit_pkg

lang_util_pkg = types.ModuleType("utils.lang_util")
lang_util_pkg.__path__ = [os.path.join(ROOT_DIR, "utils", "lang_util")]
conversation_module = types.ModuleType("utils.lang_util.conversation")
conversation_module.get_checkpointer = lambda *args, **kwargs: None
llm_module = types.ModuleType("utils.lang_util.llm")
llm_module.get_llm = lambda *args, **kwargs: None
sys.modules["utils.lang_util"] = lang_util_pkg
sys.modules["utils.lang_util.conversation"] = conversation_module
sys.modules["utils.lang_util.llm"] = llm_module
file_service_pkg = types.ModuleType("utils.file_service")
file_service_pkg.__path__ = [os.path.join(ROOT_DIR, "utils", "file_service")]
file_service_module = types.ModuleType("utils.file_service.service")

async def _dummy_get_file_path_by_id(*args, **kwargs):
    return None

file_service_module.get_file_path_by_id = _dummy_get_file_path_by_id
sys.modules["utils.file_service"] = file_service_pkg
sys.modules["utils.file_service.service"] = file_service_module

if "fitz" not in sys.modules:
    fitz_module = types.ModuleType("fitz")
    fitz_module.open = lambda *args, **kwargs: None
    fitz_module.Matrix = lambda *args, **kwargs: None
    sys.modules["fitz"] = fitz_module

if "diskcache" not in sys.modules:
    diskcache_module = types.ModuleType("diskcache")

    class _DummyCache:
        def __init__(self, *args, **kwargs):
            self._data = {}

        def get(self, key):
            return self._data.get(key)

        def set(self, key, value):
            self._data[key] = value

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

doc_markdown_module = types.ModuleType("apps.complaint_app.tools.doc_markdown")
doc_markdown_module.convert_doc_to_markdown = lambda *args, **kwargs: ("", "")
doc_markdown_module.replace_images_with_ocr = lambda content, media_dir: content
complaint_api_service_module = types.ModuleType("apps.complaint_app.services.complaint_api_service")
complaint_api_service_module.build_base_id = lambda file_id: "base-id"
complaint_api_service_module.build_base_id_for_file_ids = lambda file_ids: "base-id"
complaint_api_service_module.ensure_user_id = lambda user_id: str(user_id)

async def _dummy_async_return(*args, **kwargs):
    return "", 0

complaint_api_service_module.resolve_pdf_file = _dummy_async_return
complaint_api_service_module.extract_text_and_save = _dummy_async_return
complaint_api_service_module.extract_texts_and_save = _dummy_async_return
complaint_api_service_module.generate_summary_and_save = _dummy_async_return
complaint_api_service_module.save_reused_text_content = _dummy_async_return
complaint_api_service_module.load_issue_analysis_list = lambda *args, **kwargs: {"items": [], "total": 0}
complaint_api_service_module.extract_supplement_material_sentence_from_summary = lambda *args, **kwargs: ""

async def _dummy_load_review_report_text(*args, **kwargs):
    return ""

complaint_api_service_module.load_review_report_text = _dummy_load_review_report_text

async def _dummy_build_issue_analysis(*args, **kwargs):
    return {}, 0

complaint_api_service_module.build_issue_analysis = _dummy_build_issue_analysis
sys.modules["apps.complaint_app.tools.doc_markdown"] = doc_markdown_module
sys.modules["apps.complaint_app.services.complaint_api_service"] = complaint_api_service_module

langgraph_module = types.ModuleType("langgraph")
langgraph_graph_module = types.ModuleType("langgraph.graph")
langgraph_message_module = types.ModuleType("langgraph.graph.message")
langgraph_message_module.add_messages = lambda left, right: (left or []) + (right or [])
langgraph_graph_module.START = "__start__"
langgraph_graph_module.END = "__end__"

class _DummyStateGraph:
    def __init__(self, *args, **kwargs):
        pass

    def add_node(self, *args, **kwargs):
        pass

    def add_edge(self, *args, **kwargs):
        pass

    def add_conditional_edges(self, *args, **kwargs):
        pass

    def compile(self, *args, **kwargs):
        return None

langgraph_graph_module.StateGraph = _DummyStateGraph
sys.modules["langgraph"] = langgraph_module
sys.modules["langgraph.graph"] = langgraph_graph_module
sys.modules["langgraph.graph.message"] = langgraph_message_module

from apps.word_fit_app import routes as wordfit_routes
from apps.word_fit_app import handlers as wordfit_handlers
from apps.word_fit_app.agent import nodes as wordfit_nodes
from apps.word_fit_app.tools import image_text_extractor_llm as wordfit_ocr
from apps.complaint_app.services import trace_page_service


def test_extract_route_passes_force_and_model_options(monkeypatch):
    captured = {}

    async def fake_handler(conversation_id, query, options=None, file_ids=None):
        captured["conversation_id"] = conversation_id
        captured["query"] = query
        captured["options"] = options or {}
        captured["file_ids"] = file_ids or []
        return {"ok": True}

    monkeypatch.setattr(wordfit_routes, "word_fit_completion_handler", fake_handler)

    request = wordfit_routes.ExtractRequest(
        file_id="case.pdf",
        extract_fields=[{"msgkey": "reporter", "description": "举报人"}],
        conversation_id="conv-1",
        force=True,
        extract_model="qwen3.5",
        ocr_model="qwen2.5-vl",
        ocr_metrics_batch_id="batch-1",
        org_id="ORG_001",
        entity_id="ENTITY_001",
        scope="entity",
    )

    response = asyncio.run(wordfit_routes.extract_info(request))

    assert response == {"ok": True}
    assert captured["conversation_id"] == "conv-1"
    assert captured["file_ids"] == ["case.pdf"]
    assert captured["options"]["force"] is True
    assert captured["options"]["extract_model"] == "qwen3.5"
    assert captured["options"]["ocr_model"] == "qwen2.5-vl"
    assert captured["options"]["ocr_metrics_batch_id"] == "batch-1"
    assert captured["options"]["scope"] == "entity"
    assert captured["options"]["org_id"] == "ORG_001"
    assert captured["options"]["entity_id"] == "ENTITY_001"
    assert captured["options"]["scope_path"] == "ORG_001/ENTITY_001"


def test_extract_route_accepts_file_ids_and_sorts_for_stable_case(monkeypatch):
    captured = {}

    async def fake_handler(conversation_id, query, options=None, file_ids=None):
        captured["options"] = options or {}
        captured["file_ids"] = file_ids or []
        return {"ok": True}

    monkeypatch.setattr(wordfit_routes, "word_fit_completion_handler", fake_handler)

    request = wordfit_routes.ExtractRequest(
        file_ids=["b.pdf", "a.pdf", "a.pdf"],
        extract_fields=[{"msgkey": "reporter", "description": "举报人"}],
        conversation_id="conv-1",
    )

    response = asyncio.run(wordfit_routes.extract_info(request))

    assert response == {"ok": True}
    assert captured["file_ids"] == ["a.pdf", "b.pdf"]
    assert captured["options"]["file_id"] == "a.pdf"
    assert captured["options"]["file_ids"] == ["a.pdf", "b.pdf"]


def test_parse_extract_content_handles_markdown_json():
    content = '```json\n{"reporter": "张三", "amount": 100}\n```'

    parsed = wordfit_handlers.parse_extract_content(content)

    assert parsed == {"reporter": "张三", "amount": 100}


def test_parse_extract_content_keeps_plain_text_on_parse_failure():
    content = "无法读取文件内容，提取失败。"

    parsed = wordfit_handlers.parse_extract_content(content)

    assert parsed == content


def test_wordfit_download_path_isolated_by_conversation(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WORDFIT_DOWNLOAD_DIR", str(tmp_path))

    first = wordfit_nodes.build_wordfit_download_path("/same/file.pdf", "conv-1")
    second = wordfit_nodes.build_wordfit_download_path("/same/file.pdf", "conv-2")

    assert first != second
    assert first.endswith("runs/conv-1/same/file.pdf")
    assert second.endswith("runs/conv-2/same/file.pdf")


def test_wordfit_ocr_text_dir_uses_ocr_texts_under_complaint_scope(tmp_path: Path):
    image_path = tmp_path / "complaints" / "base-id" / "default" / "original" / "case.pdf"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"%PDF-1.4\n")

    text_dir = wordfit_ocr._resolve_text_dir(str(image_path))

    assert text_dir == tmp_path / "complaints" / "base-id" / "default" / "ocr_texts"
    assert text_dir.is_dir()


def test_trigger_preheat_is_disabled_even_when_analysis_scene_present(monkeypatch):
    captured = {}
    scheduled = {}

    async def fake_save_reused_text_content(user_id, base_id, text_content, file_ids=None, source_request_id=""):
        captured["user_id"] = user_id
        captured["base_id"] = base_id
        captured["text_content"] = text_content
        captured["file_ids"] = file_ids
        captured["source_request_id"] = source_request_id
        return text_content, 1

    def fake_create_task(coro):
        scheduled["created"] = True
        coro.close()
        return None

    monkeypatch.setattr(wordfit_nodes, "save_reused_text_content", fake_save_reused_text_content)
    monkeypatch.setattr(wordfit_nodes.asyncio, "create_task", fake_create_task)

    asyncio.run(
        wordfit_nodes.trigger_complaint_analysis_preheat(
            {
                "analysis_scene": "complaint",
                "analysis_user_id": "user-1",
                "analysis_match_strategy": "",
                "conversation_id": "conv-1",
                "file_id": "case.pdf",
                "file_ids": ["case.pdf"],
                "file_content": "字段提取 OCR 文本",
            }
        )
    )

    assert captured == {}
    assert scheduled == {}


def test_persist_complaint_text_cache_saves_default_scope_without_scheduling(monkeypatch):
    captured = {}
    scheduled = {}

    async def fake_save_reused_text_content(user_id, base_id, text_content, file_ids=None, source_request_id="", scope_context=None):
        captured["user_id"] = user_id
        captured["base_id"] = base_id
        captured["text_content"] = text_content
        captured["file_ids"] = file_ids
        captured["source_request_id"] = source_request_id
        captured["scope_path"] = scope_context.scope_path if scope_context else ""
        return text_content, 1

    def fake_create_task(coro):
        scheduled["created"] = True
        coro.close()
        return None

    monkeypatch.setattr(wordfit_nodes, "save_reused_text_content", fake_save_reused_text_content)
    monkeypatch.setattr(wordfit_nodes.asyncio, "create_task", fake_create_task)

    asyncio.run(
        wordfit_nodes.persist_complaint_text_cache_from_wordfit(
            {
                "analysis_user_id": "user-1",
                "conversation_id": "conv-1",
                "file_id": "case.pdf",
                "file_ids": ["case.pdf"],
                "file_content": "字段提取 OCR 文本",
                "scope": "entity",
                "org_id": "ORG_001",
                "entity_id": "ENTITY_001",
                "scope_path": "ORG_001/ENTITY_001",
            }
        )
    )

    assert captured == {
        "user_id": "user-1",
        "base_id": "base-id",
        "text_content": "字段提取 OCR 文本",
        "file_ids": ["case.pdf"],
        "source_request_id": "conv-1",
        "scope_path": "default",
    }
    assert scheduled == {}


def test_ocr_node_passes_force_and_ocr_model(monkeypatch, tmp_path: Path):
    captured = {}
    image_path = tmp_path / "case.png"
    image_path.write_bytes(b"fake image")

    def fake_recognize_image(image_path_arg, force=False, prompt=None, progress_callback=None, model=None, metrics_batch_id=None, request_id=None, base_id=None, user_id=None):
        captured["image_path"] = image_path_arg
        captured["force"] = force
        captured["prompt"] = prompt
        captured["model"] = model
        captured["metrics_batch_id"] = metrics_batch_id
        captured["request_id"] = request_id
        captured["base_id"] = base_id
        captured["user_id"] = user_id
        if progress_callback:
            progress_callback(1, 1)
        return {"text_content": "识别文本", "cache_hit": False}

    monkeypatch.setattr(wordfit_nodes, "recognize_image", fake_recognize_image)

    result = asyncio.run(
        wordfit_nodes.ocr_node(
            {
                "file_path": str(image_path),
                "extract_fields": [{"msgkey": "reporter", "description": "举报人"}],
                "force": True,
                "ocr_model": "glm-ocr",
                "ocr_metrics_batch_id": "batch-1",
                "conversation_id": "req-1",
            }
        )
    )

    assert result == {"file_content": "识别文本"}
    assert captured["image_path"] == str(image_path)
    assert captured["force"] is True
    assert captured["model"] == "glm-ocr"
    assert captured["metrics_batch_id"] == "batch-1"
    assert captured["request_id"] == "req-1"


def test_ocr_node_emits_countdown_event(monkeypatch, tmp_path: Path):
    events = []
    image_path = tmp_path / "case.png"
    image_path.write_bytes(b"fake image")

    def fake_recognize_image(*args, **kwargs):
        time.sleep(0.35)
        return {"text_content": "识别文本", "cache_hit": False}

    async def fake_dispatch(name, data, config=None):
        events.append((name, data))

    monkeypatch.setattr(wordfit_nodes, "recognize_image", fake_recognize_image)
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_MAX_WAIT_SECONDS", 2)
    monkeypatch.setattr(wordfit_nodes, "adispatch_custom_event", fake_dispatch)

    result = asyncio.run(
        wordfit_nodes.ocr_node(
            {
                "file_path": str(image_path),
                "extract_fields": [],
                "force": True,
                "ocr_model": "",
                "ocr_metrics_batch_id": "",
                "conversation_id": "req-1",
            }
        )
    )

    assert result == {"file_content": "识别文本"}
    assert events[0][0] == "word_fit_ocr_progress"
    assert events[0][1]["type"] == "ocr_countdown"
    assert events[0][1]["content"] == 2
    assert events[0][1]["total"] == 2
    assert events[0][1]["message"] == "共1页，预计完成时间：2秒"
    assert events[0][1]["node"] == "ocr_node"
    assert events[0][1]["node_name"] == "OCR识别"


def test_pdf_countdown_message_uses_expected_finish_time(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "case.pdf"
    pdf_path.write_bytes(b"%PDF")

    class FakeDoc:
        def __len__(self):
            return 25

        def close(self):
            return None

    monkeypatch.setattr(wordfit_nodes.fitz, "open", lambda *args, **kwargs: FakeDoc())
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_MAX_WAIT_SECONDS", 10)
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_PDF_MAX_WORKERS", 20)

    countdown_total, message = wordfit_nodes.build_ocr_countdown(str(pdf_path))

    assert countdown_total == 20
    assert message == "共25页，预计完成时间：20秒"


def test_single_pdf_ocr_node_uses_pdf_countdown_message(monkeypatch, tmp_path: Path):
    events = []
    pdf_path = tmp_path / "case.pdf"
    pdf_path.write_bytes(b"%PDF")

    class FakeDoc:
        def __len__(self):
            return 25

        def close(self):
            return None

    def fake_recognize_image(*args, **kwargs):
        time.sleep(0.35)
        return {"text_content": "识别文本", "cache_hit": False}

    async def fake_dispatch(name, data, config=None):
        events.append((name, data))

    monkeypatch.setattr(wordfit_nodes.fitz, "open", lambda *args, **kwargs: FakeDoc())
    monkeypatch.setattr(wordfit_nodes, "recognize_image", fake_recognize_image)
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_MAX_WAIT_SECONDS", 10)
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_PDF_MAX_WORKERS", 20)
    monkeypatch.setattr(wordfit_nodes, "adispatch_custom_event", fake_dispatch)

    result = asyncio.run(
        wordfit_nodes.ocr_node(
            {
                "file_path": str(pdf_path),
                "extract_fields": [],
                "force": True,
                "ocr_model": "",
                "ocr_metrics_batch_id": "",
                "conversation_id": "req-1",
            }
        )
    )

    assert result == {"file_content": "识别文本"}
    assert events[0][1]["type"] == "ocr_countdown"
    assert events[0][1]["message"] == "共25页，预计完成时间：20秒"


def test_ocr_countdown_message_uses_remaining_seconds():
    message = wordfit_nodes.format_ocr_countdown_message("共25页，预计完成时间：20秒", 17)

    assert message == "共25页，预计完成时间：17秒"


def test_multi_file_extract_streams_ocr_callback_events(monkeypatch):
    async def fake_build_batch_file_contents(file_ids, base_state, ocr_event_callback=None):
        if ocr_event_callback is not None:
            await ocr_event_callback(
                {
                    "type": "ocr_countdown",
                    "content": 10,
                    "total": 10,
                    "node": "ocr_node",
                    "node_name": "OCR识别",
                }
            )
        return [f"text:{file_id}" for file_id in file_ids]

    async def fake_extract_info_node(state):
        return {"messages": [types.SimpleNamespace(content='{"ok": true}')]}

    monkeypatch.setattr(wordfit_handlers, "build_batch_file_contents_for_extract", fake_build_batch_file_contents)
    monkeypatch.setattr(wordfit_handlers, "extract_info_node", fake_extract_info_node)

    async def collect_events():
        response = await wordfit_handlers.word_fit_completion_handler(
            conversation_id="conv-1",
            query="Extract Info",
            options={"file_id": "a.png", "file_ids": ["a.png", "b.png"], "extract_fields": []},
            file_ids=["a.png", "b.png"],
        )
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        return chunks

    chunks = asyncio.run(collect_events())
    events = [json.loads(chunk) for chunk in chunks if chunk.strip()]

    assert any('"type": "ocr_countdown"' in chunk and '"content": 10' in chunk for chunk in chunks)
    assert all(event.get("base_id") for event in events)


def test_trace_page_builds_wordfit_nodes_from_timing_logs():
    lines = [
        '[timing] app=word_fit_app kind=phase route=POST /api/word-fit/extract phase=fetch_file duration_ms=12 success=True extra={"base_id":"base-id","conversation_id":"conv-1","file_id":"case.pdf","scope":"entity","scope_path":"ORG_001/ENTITY_001","org_id":"ORG_001","entity_id":"ENTITY_001"}',
        '[timing] app=word_fit_app kind=phase route=POST /api/word-fit/extract phase=extract_info duration_ms=34 success=True extra={"base_id":"base-id","conversation_id":"conv-1","file_id":"case.pdf","scope":"entity","scope_path":"ORG_001/ENTITY_001","org_id":"ORG_001","entity_id":"ENTITY_001"}',
        '[timing] app=word_fit_app kind=route route=POST /api/word-fit/extract duration_ms=50 success=True extra={"base_id":"base-id","conversation_id":"conv-1","file_id":"case.pdf","extract_model":"m1","ocr_model":"m2","scope":"entity","scope_path":"ORG_001/ENTITY_001","org_id":"ORG_001","entity_id":"ENTITY_001"}',
    ]
    rows = []
    for index, line in enumerate(lines, start=1):
        item = trace_page_service._parse_log_line(line)
        item["request_group"] = "request1_wordfit_extract"
        item["line"] = str(index)
        rows.append(item)

    records = trace_page_service._build_wordfit_records_from_logs(rows, "base-id")

    assert len(records) == 1
    assert records[0]["conversation_id"] == "conv-1"
    assert records[0]["scope_path"] == "ORG_001/ENTITY_001"
    assert records[0]["org_id"] == "ORG_001"
    assert records[0]["entity_id"] == "ENTITY_001"
    assert records[0]["extract_model"] == "m1"
    assert records[0]["node_timings"] == [
        {"node": "fetch_file", "duration_ms": "12", "success": True},
        {"node": "extract_info", "duration_ms": "34", "success": True},
    ]
    html = trace_page_service._render_wordfit_timeline(records)
    assert "ORG_001/ENTITY_001" in html
    assert "org=ORG_001" in html
    assert "entity=ENTITY_001" in html


def test_trace_page_groups_complaint_law_skill_logs_by_item():
    lines = [
        "[complaint_law_skill] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill stream_start complaints=2 max_concurrent=2",
        "[complaint_law_skill] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 item_start complaint=事项一",
        "[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 llm_request_done round=1 elapsed=1.234s",
        "[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 response metrics round=1 input_chars=100 output_chars=20 tool_call_count=1 duration_ms=1234",
        '[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 request_messages=[{"role":"user","content":"分析事项一"}]',
        '[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 assistant_message round=1 content=需要检索法规 tool_calls=[{"id":"call-1","function":{"name":"shell","arguments":"{}"}}]',
        "[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 tool_call round=1 index=0 name=shell call_id=call-1 args={} command=grep -R -n \"开户\" \"/data/aigc/fxcjg_data/complaints/laws\"",
        "[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 tool_call_start round=1 index=0 name=shell call_id=call-1",
        "[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 tool_call_done round=1 index=0 name=shell call_id=call-1 elapsed=0.456s",
        '[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 tool_result round=1 name=shell call_id=call-1 output=命中第十条',
        "[session_prompt] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 final_assistant_message content=模型结论",
        '[complaint_law_skill] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=0 complaint_id=complaint_1 complaint_hash=aaa111 item_done matched_count=1 law_summary={"user_cited_laws":[],"possible_laws":[{"law":"支付结算办法","reason":"开户线索","matched_clauses":[{"law":"支付结算办法","article":"第十条","text_summary":"账户开立要求"}]}]}',
        "[complaint_law_skill] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf match_strategy=law_skill complaint_index=1 complaint_id=complaint_2 complaint_hash=bbb222 item_start complaint=事项二",
    ]
    rows = []
    for index, line in enumerate(lines, start=1):
        item = trace_page_service._parse_log_line(line)
        item["request_group"] = "request3_complaint_analysis"
        item["line"] = str(index)
        rows.append(item)

    assert rows[1]["complaint_index"] == "0"
    assert rows[1]["complaint_id"] == "complaint_1"
    html = trace_page_service._render_trace_logs(rows)

    assert "接口级日志" in html
    assert "举报事项1 | complaint_id=complaint_1 | hash=aaa111" in html
    assert "对话式工具调用" in html
    assert "工具调用执行" in html
    assert "模型输出" in html
    assert "分析事项一" in html
    assert "需要检索法规" in html
    assert "命中第十条" in html
    assert "模型结论" in html
    assert "Skill 执行摘要" in html
    assert "事件概览" not in html
    assert "1.234s" in html
    assert "shell call_id=call-1 耗时=0.456s" in html
    assert "grep -R -n" in html
    assert "支付结算办法" in html
    assert "第十条" in html
    assert "举报事项2 | complaint_id=complaint_2 | hash=bbb222" in html


def test_trace_page_filters_complaint_logs_by_issue_analysis_version():
    lines = [
        "[complaint_law_skill] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf issue_analysis_version=2 complaint_index=0 complaint_id=complaint_1 item_start",
        "[complaint_law_skill] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf issue_analysis_version=3 complaint_index=0 complaint_id=complaint_1 item_start",
        '[timing] app=complaint_app kind=phase route=POST /api/complaint/complaint-analysis phase=issue_analysis duration_ms=12 success=True extra={"base_id":"base-id","file_id":"f1.pdf","issue_analysis_version":3}',
    ]
    rows = [trace_page_service._parse_log_line(line) for line in lines]

    filtered = trace_page_service._filter_logs_by_issue_analysis_version(rows, 3)

    assert len(filtered) == 2
    assert all(trace_page_service._row_issue_analysis_version(row) == "3" for row in filtered)


def test_trace_page_defaults_to_latest_issue_analysis_version_from_logs(monkeypatch):
    rows = [
        trace_page_service._parse_log_line("[complaint_api] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf issue_analysis_version=1 stage=a"),
        trace_page_service._parse_log_line("[complaint_api] request_id=- base_id=base-id user_id=u1 file_id=f1.pdf issue_analysis_version=2 stage=b"),
    ]
    monkeypatch.setattr(trace_page_service, "_search_trace_logs", lambda base_id: rows)
    monkeypatch.setattr(trace_page_service, "load_manifest", lambda *args, **kwargs: {"issue_analysis": {"current": 1}})

    payload = trace_page_service.build_complaint_trace_payload("u1", base_id="base-id")

    assert payload["issue_analysis_version"] == 2
    assert len(payload["trace_logs"]) == 1
    assert "stage=b" in payload["trace_logs"][0]["raw"]


def test_pdf_ocr_keeps_successful_pages_when_one_page_fails(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "case.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    saved_pages = {}

    class FakeDoc:
        def __len__(self):
            return 2

        def close(self):
            return None

    def fake_process_pdf_page(image_path, page_index, prompt=None, model=None, base_id=None, user_id=None):
        if page_index == 1:
            raise RuntimeError("LLM 流式调用未返回有效内容")
        return page_index, "第一页文本", str(tmp_path / "case_page_1_ocr.txt"), "start", 1, 2, 3, {"model": model}

    def fake_persist_page_text(image_path, page_index, text, base_id=None, request_id=None):
        saved_pages[page_index] = text
        path = tmp_path / f"case_page_{page_index + 1}_ocr.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    monkeypatch.setattr(wordfit_ocr.fitz, "open", lambda *args, **kwargs: FakeDoc())
    monkeypatch.setattr(wordfit_ocr, "_process_pdf_page", fake_process_pdf_page)
    monkeypatch.setattr(wordfit_ocr, "_persist_page_text", fake_persist_page_text)
    monkeypatch.setattr(wordfit_ocr, "_persist_text", lambda image_path, text, base_id=None, request_id=None: str(tmp_path / "case_ocr.txt"))
    monkeypatch.setattr(wordfit_ocr, "_write_ocr_metric", lambda *args, **kwargs: None)
    monkeypatch.setattr(wordfit_ocr, "_cache", types.SimpleNamespace(get=lambda key: None, set=lambda key, value: None))
    monkeypatch.setattr(wordfit_ocr.mimetypes, "guess_type", lambda path: ("application/pdf", None))

    result = wordfit_ocr.recognize_image(str(pdf_path), force=True, model="ocr-model")

    assert result["cache_hit"] is False
    assert "第一页文本" in result["text_content"]
    assert "OCR识别失败：LLM 流式调用未返回有效内容" in result["text_content"]
    assert saved_pages[1] == "OCR识别失败：LLM 流式调用未返回有效内容"


def test_multi_pdf_ocr_uses_shared_page_pool(monkeypatch, tmp_path: Path):
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    pdf_a.write_bytes(b"%PDF-a")
    pdf_b.write_bytes(b"%PDF-b")
    page_counts = {str(pdf_a): 2, str(pdf_b): 3}
    active = 0
    max_active = 0
    lock = threading.Lock()

    class FakeDoc:
        def __init__(self, path):
            self.path = path

        def __len__(self):
            return page_counts[self.path]

        def close(self):
            return None

    def fake_process_pdf_page(image_path, page_index, prompt=None, model=None, base_id=None, user_id=None):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return page_index, f"{Path(image_path).name}:{page_index + 1}", str(tmp_path / f"{Path(image_path).stem}_{page_index + 1}.txt"), "start", 1, 2, 3, {"model": model}

    monkeypatch.setattr(wordfit_ocr.fitz, "open", lambda path, *args, **kwargs: FakeDoc(str(path)))
    monkeypatch.setattr(wordfit_ocr, "_process_pdf_page", fake_process_pdf_page)
    monkeypatch.setattr(wordfit_ocr, "_persist_text", lambda image_path, text, base_id=None, request_id=None: str(tmp_path / f"{Path(image_path).stem}_ocr.txt"))
    monkeypatch.setattr(wordfit_ocr, "_write_ocr_metric", lambda *args, **kwargs: None)
    monkeypatch.setattr(wordfit_ocr, "_cache", types.SimpleNamespace(get=lambda key: None, set=lambda key, value: None))
    monkeypatch.setattr(wordfit_ocr, "WORDFIT_OCR_PDF_MAX_WORKERS", 20)

    result = wordfit_ocr.recognize_pdf_files_concurrently([str(pdf_a), str(pdf_b)], force=True, model="ocr-model")

    assert max_active == 5
    assert result[str(pdf_a)]["text_content"] == "a.pdf:1\n\na.pdf:2"
    assert result[str(pdf_b)]["text_content"] == "b.pdf:1\n\nb.pdf:2\n\nb.pdf:3"


def test_build_pdf_file_contents_keeps_file_order(monkeypatch, tmp_path: Path):
    pdf_paths = {
        "b.pdf": str(tmp_path / "b.pdf"),
        "a.pdf": str(tmp_path / "a.pdf"),
    }
    for path in pdf_paths.values():
        Path(path).write_bytes(b"%PDF")
    page_counts = {pdf_paths["b.pdf"]: 1, pdf_paths["a.pdf"]: 1}

    class FakeDoc:
        def __init__(self, path):
            self.path = str(path)

        def __len__(self):
            return page_counts[self.path]

        def close(self):
            return None

    async def fake_fetch_file_node(state):
        return {"file_path": pdf_paths[state["file_id"]]}

    async def fake_resolve_file_node(state):
        return {"file_path": state["file_path"]}

    def fake_recognize_pdf_files_concurrently(pdf_paths_arg, force=False, prompt=None, progress_callback=None, model=None, metrics_batch_id=None, request_id=None, base_id=None, user_id=None, image_progress_as_page=False):
        time.sleep(0.35)
        if progress_callback is not None:
            progress_callback(2, 2)
        return {path: {"text_content": f"text:{Path(path).name}"} for path in pdf_paths_arg}

    events = []
    monkeypatch.setattr(wordfit_nodes, "fetch_file_node", fake_fetch_file_node)
    monkeypatch.setattr(wordfit_nodes, "resolve_file_node", fake_resolve_file_node)
    monkeypatch.setattr(wordfit_nodes, "recognize_pdf_files_concurrently", fake_recognize_pdf_files_concurrently)
    monkeypatch.setattr(wordfit_nodes.fitz, "open", lambda path, *args, **kwargs: FakeDoc(path))
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_MAX_WAIT_SECONDS", 2)
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_PDF_MAX_WORKERS", 20)

    async def record_event(event_data):
        events.append(event_data)

    async def collect():
        return await wordfit_nodes.build_pdf_file_contents_for_extract(
            ["b.pdf", "a.pdf"],
            {"extract_fields": [], "force": False, "conversation_id": "conv-1", "base_id": "base-1", "analysis_user_id": "u1"},
            record_event,
        )

    contents = asyncio.run(collect())

    assert contents == ["text:b.pdf", "text:a.pdf"]
    assert any(event["type"] == "ocr_countdown" and event["message"] == "共2页，预计完成时间：2秒" for event in events)
    assert events[-1]["current"] == 2
    assert events[-1]["total"] == 2


def test_build_batch_file_contents_handles_mixed_pdf_and_images(monkeypatch, tmp_path: Path):
    pdf_path = tmp_path / "case.pdf"
    image_path = tmp_path / "img.png"
    pdf_path.write_bytes(b"%PDF")
    image_path.write_bytes(b"fake image")

    async def fake_fetch_file_node(state):
        return {"file_path": str(pdf_path if state["file_id"] == "case.pdf" else image_path)}

    async def fake_resolve_file_node(state):
        return {"file_path": state["file_path"]}

    def fake_recognize_pdf_files_concurrently(pdf_paths_arg, force=False, prompt=None, progress_callback=None, model=None, metrics_batch_id=None, request_id=None, base_id=None, user_id=None, image_progress_as_page=False):
        assert image_progress_as_page is True
        time.sleep(0.35)
        if progress_callback is not None:
            progress_callback(2, 2)
        return {
            str(pdf_path): {"text_content": "text:case.pdf"},
            str(image_path): {"text_content": "text:img.png"},
        }

    events = []
    monkeypatch.setattr(wordfit_nodes, "fetch_file_node", fake_fetch_file_node)
    monkeypatch.setattr(wordfit_nodes, "resolve_file_node", fake_resolve_file_node)
    monkeypatch.setattr(wordfit_nodes, "recognize_pdf_files_concurrently", fake_recognize_pdf_files_concurrently)
    monkeypatch.setattr(wordfit_nodes.fitz, "open", lambda path, *args, **kwargs: type("FakeDoc", (), {"__len__": lambda self: 1, "close": lambda self: None})())
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_MAX_WAIT_SECONDS", 2)
    monkeypatch.setattr(wordfit_nodes, "WORDFIT_OCR_PDF_MAX_WORKERS", 20)

    async def record_event(event_data):
        events.append(event_data)

    async def collect():
        return await wordfit_nodes.build_batch_file_contents_for_extract(
            ["case.pdf", "img.png"],
            {"extract_fields": [], "force": False, "conversation_id": "conv-1", "base_id": "base-1", "analysis_user_id": "u1"},
            record_event,
        )

    contents = asyncio.run(collect())

    assert contents == ["text:case.pdf", "text:img.png"]
    assert any(event["type"] == "ocr_countdown" and event["message"] == "共2页，预计完成时间：2秒" for event in events)
    assert events[-1]["current"] == 2
    assert events[-1]["total"] == 2


def test_wordfit_ocr_text_path_is_scoped_by_base_and_request(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "runs" / "req-1" / "same-name.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"fake image")
    text_root = tmp_path / "ocr" / "texts"
    monkeypatch.setattr(wordfit_ocr, "_TEXT_FALLBACK_DIR", str(text_root))

    saved_path = wordfit_ocr._persist_text(str(image_path), "识别文本", base_id="base-1", request_id="req-1")

    saved_file = Path(saved_path)
    assert saved_file.read_text(encoding="utf-8") == "识别文本\n"
    assert saved_file.parent.parent.name == "req-1"
    assert saved_file.parent.parent.parent.name == "base-1"
    assert saved_file.parent.name.startswith("same-name-")
