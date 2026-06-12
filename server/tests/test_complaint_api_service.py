import asyncio
import json
import os
import shutil
import sys
import types
from urllib.parse import quote
import pytest


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ.setdefault("DATA_ROOT", "/tmp/codex-test-data-root")

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

from apps.complaint_app.services import complaint_api_service as complaint_api_service_module
from apps.complaint_app.services.summary import llm_summary as llm_summary_module
from apps.complaint_app.services.analysis_agent import agent as analysis_agent_module
from apps.complaint_app.services.complaint_analysis_v2 import orchestrator as orchestrator_module
from apps.complaint_app import fastapi_routes as fastapi_routes_module
from apps.complaint_app.opencode_runtime.tools.builtin import shell_tool as shell_tool_module
from utils.storage_paths import get_scoped_complaint_root, resolve_complaint_scope


def test_shell_output_is_truncated(monkeypatch):
    monkeypatch.setenv("OPENCODE_SHELL_OUTPUT_MAX_CHARS", "1000")

    output, truncated, original_len = shell_tool_module._truncate_shell_output("a" * 1005)

    assert truncated is True
    assert original_len == 1005
    assert len(output) > 1000
    assert "[output truncated" in output


def test_stream_issue_analysis_events_two_stage_phase_nodes(monkeypatch):
    async def fake_run_complaint_analysis(*args, **kwargs):
        yield {"event": "node_start", "node_id": "complaint_extract_items"}
        yield {"event": "node_delta", "node_id": "complaint_match_experience", "line_id": "complaint_match-two-stage-1"}
        yield {"event": "node_delta", "node_id": "complaint_match_experience", "line_id": "complaint_match-two-stage-2"}
        yield {"event": "node_start", "node_id": "complaint_final_conclusion"}

    async def collect_events():
        return [item async for item in complaint_api_service_module.stream_issue_analysis_events("summary", "two_stage")]

    monkeypatch.setattr(complaint_api_service_module, "run_complaint_analysis", fake_run_complaint_analysis)

    events = asyncio.run(collect_events())
    stage_events = [item for item in events if item.get("kind") == "stage"]

    assert stage_events == [
        {"kind": "stage", "progress": 30, "message": "系统正在阅读举报材料，提取举报事项、诉求和被举报主体信息。", "phase_node": "complaint_extract_items"},
        {"kind": "stage", "progress": 50, "message": "系统正在判断每个举报事项属于哪类业务场景，例如商户、扣款、账户或支付结算问题。", "phase_node": "complaint_match_experience_stage1"},
        {"kind": "stage", "progress": 60, "message": "系统正在细化具体违规情形，并核对可引用的法律依据。", "phase_node": "complaint_match_experience_stage2"},
        {"kind": "stage", "progress": 80, "message": "系统正在汇总基础事实、法律适用性、核实思路和调阅材料。", "phase_node": "complaint_final_conclusion"},
    ]


def test_stream_issue_analysis_tags_next_issue_analysis_version(monkeypatch):
    user_id = "trace_version_user"
    base_id = "trace-version-base"
    captured = {}
    base_dir = os.path.dirname(complaint_api_service_module.stage_dir(user_id, base_id, "issue_analysis"))
    if os.path.isdir(base_dir):
        shutil.rmtree(base_dir)
    complaint_api_service_module.save_manifest(user_id, base_id, {"issue_analysis": {"current": 2}})

    async def fake_stream_issue_analysis_events(summary_text, match_strategy, trace_context=None, prefetched_extract_payload=None):
        captured["trace_context"] = dict(trace_context or {})
        yield {"kind": "stage", "progress": 30, "message": "阶段", "phase_node": "complaint_extract_items"}
        yield {"kind": "complaints", "complaints": [], "profile": {}, "incident": ""}
        yield {"kind": "laws", "laws": []}
        yield {"kind": "conclusion_delta", "delta": "结论"}

    async def fake_assemble_issue_analysis_result(*args, **kwargs):
        return {"api_kind": "complaint_analysis", "base_id": base_id}

    async def fake_save_issue_analysis_result(user_id_arg, base_id_arg, text_version, summary_version, result, issue_version=None, scope_context=None):
        captured["saved_issue_version"] = issue_version
        return result, int(issue_version or 0)

    monkeypatch.setattr(complaint_api_service_module, "load_latest_summary_bundle", lambda *args, **kwargs: ({}, 1))
    monkeypatch.setattr(complaint_api_service_module, "extract_structured_payload_from_summary_bundle", lambda *args, **kwargs: {})
    monkeypatch.setattr(complaint_api_service_module, "stream_issue_analysis_events", fake_stream_issue_analysis_events)
    monkeypatch.setattr(complaint_api_service_module, "assemble_issue_analysis_result", fake_assemble_issue_analysis_result)
    monkeypatch.setattr(complaint_api_service_module, "save_issue_analysis_result", fake_save_issue_analysis_result)

    async def collect_events():
        return [item async for item in complaint_api_service_module.stream_issue_analysis(user_id, base_id, "summary", 1, 1, force=True, match_strategy="two_stage", file_id="case.pdf")]

    events = asyncio.run(collect_events())

    assert captured["trace_context"]["issue_analysis_version"] == 3
    assert captured["saved_issue_version"] == 3
    assert events[0]["issue_analysis_version"] == 3
    assert events[-1]["issue_version"] == 3


def test_run_complaint_analysis_does_not_fallback_to_extract_items_llm(monkeypatch):
    async def fail_extract_items(*args, **kwargs):
        raise AssertionError("不应二次调用 LLM 提取举报事项")

    async def collect_events():
        return [item async for item in analysis_agent_module.run_complaint_analysis("question", "summary", prefetched_extract_payload={})]

    monkeypatch.setattr(analysis_agent_module, "complaint_extract_items_payload", fail_extract_items)

    events = asyncio.run(collect_events())

    assert any(item.get("line_id") == "complaint_items-empty" for item in events)
    assert events[-1]["event"] == "content_delta"
    assert "结构化摘要缺少举报事项" in events[-1]["delta"]


def test_summary_prompt_requires_original_violation_item_splitting():
    prompt = llm_summary_module._build_summary_structured_only_prompt()

    assert "优先按举报材料中“违规事项/违法事项/违反规定/举报事项”等段落的原始分项提取 complaints" in prompt
    assert "应逐项拆分为独立 complaint，不要合并成一个概括事项" in prompt
    assert "拆分维度优先是举报人主张的违规类型/监管义务，不是被举报主体或参与主体" in prompt
    assert "不要把同一“违规事项”段落拆成“银行事项、商户事项”等主体事项" in prompt
    assert "违反银行卡收单管理规定" in prompt
    assert "违规开立支付账户/支付账户被违规使用" in prompt
    assert "违反反电信网络诈骗法" in prompt
    assert "法律法规名称及其下列义务不是仅作为 `user_cited_laws` 记录" in prompt
    assert "也必须形成独立 complaint" in prompt
    assert "normalized_law` 只能填写从 `raw_text` 原文中提取或规范化得到的法律、法规、办法、规定名称" in prompt
    assert "仅出现“违规开立支付账户”“未履行风控义务”等义务表述时，`normalized_law` 必须留空" in prompt
    assert "禁止推理补全法规名称" in prompt
    assert "被举报机构或人员列表" in prompt
    assert "无法明确归属某一个被举报主体，应视为针对全部被举报主体" in prompt
    assert "义务类型" not in prompt


def test_summary_bundle_prompt_uses_same_violation_item_splitting_rule():
    prompt = llm_summary_module._build_summary_bundle_prompt()

    assert "优先按举报材料中“违规事项/违法事项/违反规定/举报事项”等段落的原始分项提取 complaints" in prompt
    assert "应逐项拆分为独立 complaint，不要合并成一个概括事项" in prompt
    assert "拆分维度优先是举报人主张的违规类型/监管义务，不是被举报主体或参与主体" in prompt
    assert "normalized_law` 只能填写从 `raw_text` 原文中提取或规范化得到的法律、法规、办法、规定名称" in prompt
    assert "被举报机构或人员列表" in prompt
    assert "义务类型" not in prompt


def test_build_base_id_for_file_ids_is_order_insensitive():
    first = complaint_api_service_module.build_base_id_for_file_ids(["b.pdf", "a.pdf", "a.pdf"])
    second = complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])

    assert first == second
    assert first != complaint_api_service_module.build_base_id("a.pdf")


def test_reply_draft_save_accepts_multiple_file_ids(monkeypatch):
    captured = {}
    payload = fastapi_routes_module.ReplyDraftSaveRequest(user_id="api_user", file_ids=["b.pdf", "a.pdf"], title="答复意见书", content="正文")

    async def fake_save_reply_result(user_id, base_id, title, content, source_versions, source, meta_extra=None, scope_context=None):
        captured["user_id"] = user_id
        captured["base_id"] = base_id
        captured["title"] = title
        captured["content"] = content
        captured["source"] = source
        return {"saved_at": "2026-03-12T10:05:00Z"}, 7

    monkeypatch.setattr(fastapi_routes_module, "save_reply_result", fake_save_reply_result)

    response = asyncio.run(fastapi_routes_module.complaint_reply_draft_save(payload))
    body = json.loads(response.body.decode("utf-8"))

    assert captured["user_id"] == "api_user"
    assert captured["base_id"] == complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])
    assert captured["source"] == "user"
    assert body["base_id"] == captured["base_id"]
    assert body["reply_version"] == 7


def test_reply_draft_save_accepts_comma_file_ids_string(monkeypatch):
    captured = {}
    payload = fastapi_routes_module.ReplyDraftSaveRequest(user_id="api_user", file_ids="b.pdf,a.pdf", title="答复意见书", content="正文")

    async def fake_save_reply_result(user_id, base_id, title, content, source_versions, source, meta_extra=None, scope_context=None):
        captured["base_id"] = base_id
        return {"saved_at": "2026-03-12T10:05:00Z"}, 8

    monkeypatch.setattr(fastapi_routes_module, "save_reply_result", fake_save_reply_result)

    response = asyncio.run(fastapi_routes_module.complaint_reply_draft_save(payload))
    body = json.loads(response.body.decode("utf-8"))

    assert captured["base_id"] == complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])
    assert body["reply_version"] == 8


def test_reply_draft_save_keeps_single_file_id_compatible(monkeypatch):
    captured = {}
    payload = fastapi_routes_module.ReplyDraftSaveRequest(user_id="api_user", file_id="a.pdf", title="答复意见书", content="正文")

    async def fake_save_reply_result(user_id, base_id, title, content, source_versions, source, meta_extra=None, scope_context=None):
        captured["base_id"] = base_id
        return {"saved_at": "2026-03-12T10:05:00Z"}, 3

    monkeypatch.setattr(fastapi_routes_module, "save_reply_result", fake_save_reply_result)

    response = asyncio.run(fastapi_routes_module.complaint_reply_draft_save(payload))
    body = json.loads(response.body.decode("utf-8"))

    assert captured["base_id"] == complaint_api_service_module.build_base_id("a.pdf")
    assert body["reply_version"] == 3


def test_reply_draft_get_accepts_comma_file_ids(monkeypatch):
    captured = {}

    def fake_load_reply_payload(user_id, base_id, version, scope_context=None):
        captured["user_id"] = user_id
        captured["base_id"] = base_id
        captured["version"] = version
        return {"base_id": base_id, "title": "答复意见书", "content": "正文"}, 5

    monkeypatch.setattr(fastapi_routes_module, "load_reply_payload", fake_load_reply_payload)

    response = asyncio.run(fastapi_routes_module.complaint_reply_draft_get(file_id=None, file_ids=["b.pdf,a.pdf"], user_id="api_user", version="latest"))
    body = json.loads(response.body.decode("utf-8"))

    assert captured["user_id"] == "api_user"
    assert captured["base_id"] == complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])
    assert captured["version"] == "latest"
    assert body["base_id"] == captured["base_id"]
    assert body["reply_version"] == 5


def test_reply_draft_get_decodes_chinese_file_ids(monkeypatch):
    captured = {}
    raw_file_ids = ["举报件一.pdf", "举报件二.pdf"]
    encoded = quote(",".join(raw_file_ids), safe="")
    double_encoded = quote(encoded, safe="")

    def fake_load_reply_payload(user_id, base_id, version, scope_context=None):
        captured["base_id"] = base_id
        return {"base_id": base_id, "title": "答复意见书", "content": "正文"}, 6

    monkeypatch.setattr(fastapi_routes_module, "load_reply_payload", fake_load_reply_payload)

    response = asyncio.run(fastapi_routes_module.complaint_reply_draft_get(file_id=None, file_ids=[double_encoded], user_id="api_user", version="latest"))
    body = json.loads(response.body.decode("utf-8"))

    assert captured["base_id"] == complaint_api_service_module.build_base_id_for_file_ids(raw_file_ids)
    assert body["base_id"] == captured["base_id"]
    assert body["reply_version"] == 6


def test_reply_draft_get_accepts_repeated_file_ids(monkeypatch):
    captured = {}

    def fake_load_reply_payload(user_id, base_id, version, scope_context=None):
        captured["base_id"] = base_id
        return {"base_id": base_id, "title": "答复意见书", "content": "正文"}, 2

    monkeypatch.setattr(fastapi_routes_module, "load_reply_payload", fake_load_reply_payload)

    response = asyncio.run(fastapi_routes_module.complaint_reply_draft_get(file_id=None, file_ids=["b.pdf", "a.pdf"], user_id="api_user", version="1"))
    body = json.loads(response.body.decode("utf-8"))

    assert captured["base_id"] == complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])
    assert body["reply_version"] == 2


def test_stream_complaint_analysis_accepts_multiple_file_ids(monkeypatch):
    captured = {}
    payload = fastapi_routes_module.ComplaintAnalysisRequest(user_id="api_user", file_ids=["b.pdf", "a.pdf"], force=False, use_uploaded_pdf=False, match_strategy="two_stage")

    async def fake_resolve(file_id, user_id, base_id, force=False, scope_context=None):
        return f"/tmp/{file_id}"

    async def fake_extract_texts(user_id, base_id, original_files, force=False, scope_context=None):
        captured["base_id"] = base_id
        captured["text_scope"] = scope_context.scope_path if scope_context else ""
        captured["original_files"] = original_files
        return "合并文本", 1

    async def fake_summary(user_id, base_id, text_content, text_version, force=False, reported_entities=None, scope_context=None):
        captured["summary_scope"] = scope_context.scope_path if scope_context else ""
        return "", 1

    async def fake_stream_issue_analysis(*args, **kwargs):
        captured["issue_file_id"] = kwargs.get("file_id")
        captured["issue_scope"] = kwargs.get("scope_context").scope_path if kwargs.get("scope_context") else ""
        yield {"kind": "completed", "result": {"complaint_items": []}, "issue_version": 1}

    monkeypatch.setattr(fastapi_routes_module, "resolve_pdf_file", fake_resolve)
    monkeypatch.setattr(fastapi_routes_module, "extract_texts_and_save", fake_extract_texts)
    monkeypatch.setattr(fastapi_routes_module, "generate_summary_and_save", fake_summary)
    monkeypatch.setattr(fastapi_routes_module, "stream_issue_analysis", fake_stream_issue_analysis)
    monkeypatch.setattr(fastapi_routes_module, "load_manifest", lambda *args, **kwargs: {"text": {"current": 1}, "summary": {"current": 1}, "issue_analysis": {"current": 1}})

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_complaint_analysis(payload)]

    events = asyncio.run(collect_events())

    assert captured["original_files"] == [("a.pdf", "/tmp/a.pdf"), ("b.pdf", "/tmp/b.pdf")]
    assert captured["base_id"] == complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])
    assert captured["text_scope"] == "default"
    assert captured["summary_scope"] == "default"
    assert captured["issue_scope"] == "default"
    assert captured["issue_file_id"] == "a.pdf,b.pdf"
    assert '"status": "completed"' in events[-1]


def test_stream_complaint_analysis_generates_summary_in_entity_scope(monkeypatch):
    captured = {}
    payload = fastapi_routes_module.ComplaintAnalysisRequest(user_id="api_user", file_id="case.pdf", force=True, scope="entity", org_id="org_a", entity_id="entity_a", reported_entities=["某银行"])

    async def fake_resolve(file_id, user_id, base_id, force=False, scope_context=None):
        return f"/tmp/{file_id}"

    async def fake_extract_texts(user_id, base_id, original_files, force=False, scope_context=None):
        captured["text_scope"] = scope_context.scope_path if scope_context else ""
        return "合并文本", 1

    async def fake_summary(user_id, base_id, text_content, text_version, force=False, reported_entities=None, scope_context=None):
        captured["summary_scope"] = scope_context.scope_path if scope_context else ""
        captured["reported_entities"] = reported_entities
        return "摘要文本", 1

    async def fake_stream_issue_analysis(*args, **kwargs):
        captured["issue_scope"] = kwargs.get("scope_context").scope_path if kwargs.get("scope_context") else ""
        yield {"kind": "completed", "result": {"complaint_items": []}, "issue_version": 1}

    monkeypatch.setattr(fastapi_routes_module, "load_latest_text", lambda *args, **kwargs: ("", 0))
    monkeypatch.setattr(fastapi_routes_module, "load_latest_summary_bundle", lambda *args, **kwargs: ({}, 0))
    monkeypatch.setattr(fastapi_routes_module, "resolve_pdf_file", fake_resolve)
    monkeypatch.setattr(fastapi_routes_module, "extract_texts_and_save", fake_extract_texts)
    monkeypatch.setattr(fastapi_routes_module, "generate_summary_and_save", fake_summary)
    monkeypatch.setattr(fastapi_routes_module, "stream_issue_analysis", fake_stream_issue_analysis)
    monkeypatch.setattr(fastapi_routes_module, "load_manifest", lambda *args, **kwargs: {"text": {"current": 1}, "summary": {"current": 1}, "issue_analysis": {"current": 1}})

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_complaint_analysis(payload)]

    events = asyncio.run(collect_events())

    assert captured["text_scope"] == "default"
    assert captured["summary_scope"] == "org_a/entity_a"
    assert captured["reported_entities"] == ["某银行"]
    assert captured["issue_scope"] == "org_a/entity_a"
    assert '"status": "completed"' in events[-1]


def test_stream_complaint_analysis_emits_extract_phase_after_summary(monkeypatch):
    summary_ready = {"value": False}
    payload = fastapi_routes_module.ComplaintAnalysisRequest(user_id="api_user", file_id="case.pdf", force=False, match_strategy="law_skill")
    structured_payload = {
        "incident": "举报人反映账户异常。",
        "profile": {"reporter": "张三", "reported_institution": "某银行", "appeals": ["依法查处", "退还损失"]},
        "complaints": [
            {"label": "未履行风控义务", "description": "银行未履行风控义务。", "claim_text": "银行未履行风控义务。"},
            {"label": "未及时处理投诉", "description": "银行未及时处理投诉。", "claim_text": "银行未及时处理投诉。"},
        ],
    }

    def fake_summary_bundle(*args, **kwargs):
        if not summary_ready["value"]:
            return {}, 0
        return {"summary_markdown": "摘要文本", "structured_payload": structured_payload}, 1

    async def fake_summary(*args, **kwargs):
        summary_ready["value"] = True
        return "摘要文本", 1

    async def fake_stream_issue_analysis(*args, **kwargs):
        yield {
            "kind": "stage",
            "progress": 30,
            "message": "系统已提取 2 个举报事项和 2 个诉求，下一步将逐项核对事实和法律适用性。",
            "phase_node": "complaint_extract_items",
            "phase_result": {"duplicated": True},
        }
        yield {"kind": "completed", "result": {"complaint_items": []}, "issue_version": 1}

    monkeypatch.setattr(fastapi_routes_module, "load_latest_text", lambda *args, **kwargs: ("预存 OCR 文本", 1))
    monkeypatch.setattr(fastapi_routes_module, "load_latest_summary_bundle", fake_summary_bundle)
    monkeypatch.setattr(fastapi_routes_module, "generate_summary_and_save", fake_summary)
    monkeypatch.setattr(fastapi_routes_module, "stream_issue_analysis", fake_stream_issue_analysis)
    monkeypatch.setattr(fastapi_routes_module, "load_manifest", lambda *args, **kwargs: {"text": {"current": 1}, "summary": {"current": 1}, "issue_analysis": {"current": 1}})

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_complaint_analysis(payload)]

    events = asyncio.run(collect_events())
    payloads = [json.loads(item.split("data:", 1)[1].strip()) for item in events if item.startswith("data:")]
    phase_events = [item for item in payloads if item.get("progress") == 30]

    assert len(phase_events) == 1
    assert phase_events[0]["message"] == "系统已提取 2 个举报事项和 2 个诉求，下一步将逐项核对事实和法律适用性。"
    assert phase_events[0]["match_strategy"] == "law_skill"
    assert phase_events[0]["phase_result"]["incident"] == "举报人反映账户异常。"
    assert len(phase_events[0]["phase_result"]["items_and_appeals"]["complaint_items"]) == 2


def test_stream_complaint_analysis_force_reuses_ready_text_and_regenerates_summary(monkeypatch):
    captured = {}
    payload = fastapi_routes_module.ComplaintAnalysisRequest(user_id="api_user", file_id="case.pdf", force=True, scope="entity", org_id="org_a", entity_id="entity_a")

    def fail_extract(*args, **kwargs):
        raise AssertionError("force=true 不应重跑 OCR")

    async def fake_summary(user_id, base_id, text_content, text_version, force=False, reported_entities=None, scope_context=None):
        captured["summary_force"] = force
        captured["summary_text_content"] = text_content
        captured["summary_scope_path"] = scope_context.scope_path if scope_context else ""
        return "新摘要文本", 2

    async def fake_stream_issue_analysis(*args, **kwargs):
        captured["summary_text"] = args[2]
        captured["force"] = kwargs.get("force")
        captured["scope_path"] = kwargs.get("scope_context").scope_path if kwargs.get("scope_context") else ""
        yield {"kind": "completed", "result": {"complaint_items": []}, "issue_version": 2}

    monkeypatch.setattr(fastapi_routes_module, "load_latest_text", lambda *args, **kwargs: ("预存 OCR 文本", 1))
    monkeypatch.setattr(
        fastapi_routes_module,
        "load_latest_summary_bundle",
        lambda *args, **kwargs: (
            {
                "summary_markdown": "",
                "structured_payload": {
                    "incident": "重新生成的摘要事项。",
                    "profile": {"reporter": "张三", "reported_institution": "某银行", "appeals": ["依法查处"]},
                    "complaints": [{"label": "重新分析事项", "description": "重新分析事项内容。", "claim_text": "重新分析事项内容。"}],
                },
            },
            2,
        ),
    )
    monkeypatch.setattr(fastapi_routes_module, "resolve_pdf_file", fail_extract)
    monkeypatch.setattr(fastapi_routes_module, "extract_texts_and_save", fail_extract)
    monkeypatch.setattr(fastapi_routes_module, "generate_summary_and_save", fake_summary)
    monkeypatch.setattr(fastapi_routes_module, "stream_issue_analysis", fake_stream_issue_analysis)
    monkeypatch.setattr(fastapi_routes_module, "load_manifest", lambda *args, **kwargs: {"text": {"current": 1}, "summary": {"current": 1}, "issue_analysis": {"current": 2}})

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_complaint_analysis(payload)]

    events = asyncio.run(collect_events())

    assert captured["force"] is True
    assert captured["scope_path"] == "org_a/entity_a"
    assert captured["summary_force"] is True
    assert captured["summary_text_content"] == "预存 OCR 文本"
    assert captured["summary_scope_path"] == "org_a/entity_a"
    assert captured["summary_text"] == "新摘要文本"
    assert '"status": "completed"' in events[-1]


def test_load_issue_analysis_list_returns_versions_desc():
    user_id = "list_user"
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["case-a.pdf", "case-b.pdf"])
    base_dir = os.path.dirname(complaint_api_service_module.stage_dir(user_id, base_id, "issue_analysis"))
    if os.path.isdir(base_dir):
        shutil.rmtree(base_dir)
    for version in range(1, 4):
        issue_dir = complaint_api_service_module.stage_dir(user_id, base_id, "issue_analysis", version)
        os.makedirs(issue_dir, exist_ok=True)
        with open(os.path.join(issue_dir, "final.json"), "w", encoding="utf-8") as handle:
            json.dump({"api_kind": "complaint_analysis", "base_id": base_id, "conclusion_text": f"v{version}"}, handle)
        complaint_api_service_module.write_meta(user_id, base_id, "issue_analysis", version, source="auto", extra={"task": "issue_analysis"})
    complaint_api_service_module.save_manifest(user_id, base_id, {"issue_analysis": {"current": 3}})

    result = complaint_api_service_module.load_issue_analysis_list(user_id, base_id, page=1, page_size=50)

    assert result["total"] == 3
    assert [item["issue_analysis_version"] for item in result["items"]] == [3, 2, 1]
    assert [item["conclusion_text"] for item in result["items"]] == ["v3", "v2", "v1"]
    assert result["items"][0]["api_kind"] == "complaint_analysis"
    assert "result" not in result["items"][0]
    assert result["items"][0]["created_at"]


def test_scoped_storage_default_is_shared_across_users():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["shared-default.pdf"])
    scope_context = resolve_complaint_scope("default")
    root = get_scoped_complaint_root(base_id, scope_context)
    if os.path.isdir(root):
        shutil.rmtree(root)

    text, version = asyncio.run(
        complaint_api_service_module.save_reused_text_content(
            "writer_user",
            base_id,
            "共享 default 文本",
            file_ids=["shared-default.pdf"],
            scope_context=scope_context,
        )
    )
    loaded_text, loaded_version = complaint_api_service_module.load_latest_text("reader_user", base_id, scope_context=scope_context)

    assert text == "共享 default 文本"
    assert version == 1
    assert loaded_text == "共享 default 文本"
    assert loaded_version == 1


def test_scoped_issue_analysis_uses_org_overlay():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["org-overlay.pdf"])
    default_scope = resolve_complaint_scope("default")
    org_scope = resolve_complaint_scope("org", "org_a")
    for scope_context in (default_scope, org_scope):
        root = get_scoped_complaint_root(base_id, scope_context)
        if os.path.isdir(root):
            shutil.rmtree(root)

    asyncio.run(complaint_api_service_module.save_issue_analysis_result("u1", base_id, 1, 1, {"base_id": base_id, "conclusion_text": "default"}, scope_context=default_scope))
    asyncio.run(complaint_api_service_module.save_issue_analysis_result("u2", base_id, 1, 1, {"base_id": base_id, "conclusion_text": "org"}, scope_context=org_scope))

    payload, version = complaint_api_service_module.load_latest_issue_analysis("u3", base_id, scope_context=org_scope)

    assert version == 1
    assert payload["conclusion_text"] == "org"
    assert payload["scope_context"]["scope_path"] == "org_a"


def test_scoped_issue_analysis_prefers_entity_then_org_then_default():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["entity-overlay.pdf"])
    default_scope = resolve_complaint_scope("default")
    org_scope = resolve_complaint_scope("org", "org_b")
    entity_scope = resolve_complaint_scope("entity", "org_b", "entity_1")
    for scope_context in (default_scope, org_scope, entity_scope):
        root = get_scoped_complaint_root(base_id, scope_context)
        if os.path.isdir(root):
            shutil.rmtree(root)

    asyncio.run(complaint_api_service_module.save_issue_analysis_result("u1", base_id, 1, 1, {"base_id": base_id, "conclusion_text": "default"}, scope_context=default_scope))
    payload, _version = complaint_api_service_module.load_latest_issue_analysis("u2", base_id, scope_context=entity_scope)
    assert payload["conclusion_text"] == "default"
    assert payload["scope_context"]["scope_path"] == "default"

    asyncio.run(complaint_api_service_module.save_issue_analysis_result("u1", base_id, 1, 1, {"base_id": base_id, "conclusion_text": "org"}, scope_context=org_scope))
    payload, _version = complaint_api_service_module.load_latest_issue_analysis("u2", base_id, scope_context=entity_scope)
    assert payload["conclusion_text"] == "org"
    assert payload["scope_context"]["scope_path"] == "org_b"

    asyncio.run(complaint_api_service_module.save_issue_analysis_result("u1", base_id, 1, 1, {"base_id": base_id, "conclusion_text": "entity"}, scope_context=entity_scope))
    payload, _version = complaint_api_service_module.load_latest_issue_analysis("u2", base_id, scope_context=entity_scope)
    assert payload["conclusion_text"] == "entity"
    assert payload["scope_context"]["scope_path"] == "org_b/entity_1"


def test_scoped_issue_analysis_isolated_by_reporter_name():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["reporter-scope.pdf"])
    zhang_scope = resolve_complaint_scope("entity", "org_r", "entity_r", "张三")
    li_scope = resolve_complaint_scope("entity", "org_r", "entity_r", "李四")
    for scope_context in (zhang_scope, li_scope):
        root = get_scoped_complaint_root(base_id, scope_context)
        if os.path.isdir(root):
            shutil.rmtree(root)

    asyncio.run(complaint_api_service_module.save_issue_analysis_result("u1", base_id, 1, 1, {"base_id": base_id, "conclusion_text": "张三分析"}, scope_context=zhang_scope))
    asyncio.run(complaint_api_service_module.save_issue_analysis_result("u1", base_id, 1, 1, {"base_id": base_id, "conclusion_text": "李四分析"}, scope_context=li_scope))

    payload, version = complaint_api_service_module.load_latest_issue_analysis("u2", base_id, scope_context=zhang_scope)

    assert version == 1
    assert payload["conclusion_text"] == "张三分析"
    assert payload["scope_context"]["reporter_name"] == "张三"
    assert payload["scope_context"]["scope_path"] == "org_r/entity_r/张三"


def test_reporter_name_strips_fys_suffix_for_scope():
    scope_context = resolve_complaint_scope("entity", "org_r", "entity_r", "张三-fys-001")
    dynamic_scope_context = resolve_complaint_scope("entity", "org_r", "entity_r", "张三-fys-临时标记@2026")

    assert scope_context.reporter_name == "张三"
    assert scope_context.scope_path == "org_r/entity_r/张三"
    assert dynamic_scope_context.reporter_name == "张三"
    assert dynamic_scope_context.scope_path == "org_r/entity_r/张三"


def test_reply_source_versions_include_scope_context():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["reply-scope.pdf"])
    scope_context = resolve_complaint_scope("entity", "org_c", "entity_9")
    root = get_scoped_complaint_root(base_id, scope_context)
    if os.path.isdir(root):
        shutil.rmtree(root)

    payload, version = asyncio.run(
        complaint_api_service_module.save_reply_result(
            "operator",
            base_id,
            "答复意见书",
            "正文",
            {"issue_analysis_version": 3},
            source="user",
            scope_context=scope_context,
        )
    )

    assert version == 1
    assert payload["scope_context"]["scope_path"] == "org_c/entity_9"
    assert payload["source_versions"]["scope_context"]["scope_path"] == "org_c/entity_9"


def test_load_complaint_analysis_v2_list_shares_scoped_results_across_users():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["shared-v2.pdf"])
    scope_context = resolve_complaint_scope("entity", "org_10", "entity_00004", "张三")
    root = get_scoped_complaint_root(base_id, scope_context)
    if os.path.isdir(root):
        shutil.rmtree(root)

    async def setup_data():
        summary_payload, summary_version = await orchestrator_module._save_stage_result(
            "u1",
            base_id,
            stage="summary_v2",
            markdown="张三摘要",
            result_payload={"summary_markdown": "张三摘要"},
            base_versions={"base_text_version": 1},
            meta_extra={"task": "summary_v2"},
            scope_context=scope_context,
        )
        analysis_payload, analysis_version = await orchestrator_module._save_stage_result(
            "u1",
            base_id,
            stage="analysis_v2",
            markdown="张三分析",
            result_payload={"analysis_markdown": "张三分析", "law_search": {"articles": [{"law_name": "测试法", "article": "第一条"}]}, "reference_content": [{"law_name": "测试法", "articles": ["第一条"]}]},
            base_versions={"base_text_version": 1, "summary_v2_version": summary_version},
            meta_extra={"task": "analysis_v2"},
            scope_context=scope_context,
        )
        await orchestrator_module._save_final_stage_result(
            user_id="u1",
            base_id=base_id,
            text_version=1,
            summary_version=summary_version,
            analysis_version=analysis_version,
            summary_markdown=str(summary_payload.get("summary_markdown") or ""),
            analysis_markdown=str(analysis_payload.get("analysis_markdown") or ""),
            reference_content=[{"law_name": "测试法", "articles": ["第一条"]}],
            final_markdown="### 调阅材料清单\n\n张三材料\n\n### 核实思路\n\n张三思路",
            scope_context=scope_context,
            model="test-model",
        )

    asyncio.run(setup_data())

    result = complaint_api_service_module.load_complaint_analysis_v2_list("u2", base_id, scope_context=scope_context)

    assert result["exists"] is True
    assert result["total"] == 1
    assert result["scope_context"]["scope_path"] == "org_10/entity_00004/张三"
    item = result["items"][0]
    assert item["summary"]["content"] == "张三摘要"
    assert item["analysis"]["content"] == "张三分析"
    assert item["reference"]["content"] == [{"law_name": "测试法", "articles": ["第一条"]}]
    assert "张三思路" in item["final"]["content"]



def test_load_complaint_analysis_v2_list_isolated_by_reporter_name():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["reporter-v2.pdf"])
    zhang_scope = resolve_complaint_scope("entity", "org_20", "entity_a", "张三")
    li_scope = resolve_complaint_scope("entity", "org_20", "entity_a", "李四")
    for scope_context in (zhang_scope, li_scope):
        root = get_scoped_complaint_root(base_id, scope_context)
        if os.path.isdir(root):
            shutil.rmtree(root)

    async def setup_data(scope_context, summary_text, analysis_text, final_text):
        _summary_payload, summary_version = await orchestrator_module._save_stage_result(
            "u1",
            base_id,
            stage="summary_v2",
            markdown=summary_text,
            result_payload={"summary_markdown": summary_text},
            base_versions={"base_text_version": 1},
            meta_extra={"task": "summary_v2"},
            scope_context=scope_context,
        )
        _analysis_payload, analysis_version = await orchestrator_module._save_stage_result(
            "u1",
            base_id,
            stage="analysis_v2",
            markdown=analysis_text,
            result_payload={"analysis_markdown": analysis_text, "law_search": {"articles": []}, "reference_content": []},
            base_versions={"base_text_version": 1, "summary_v2_version": summary_version},
            meta_extra={"task": "analysis_v2"},
            scope_context=scope_context,
        )
        await orchestrator_module._save_final_stage_result(
            user_id="u1",
            base_id=base_id,
            text_version=1,
            summary_version=summary_version,
            analysis_version=analysis_version,
            summary_markdown=summary_text,
            analysis_markdown=analysis_text,
            reference_content=[],
            final_markdown=final_text,
            scope_context=scope_context,
            model="test-model",
        )

    asyncio.run(setup_data(zhang_scope, "张三摘要", "张三分析", "张三final"))
    asyncio.run(setup_data(li_scope, "李四摘要", "李四分析", "李四final"))

    zhang_result = complaint_api_service_module.load_complaint_analysis_v2_list("u2", base_id, scope_context=zhang_scope)
    li_result = complaint_api_service_module.load_complaint_analysis_v2_list("u3", base_id, scope_context=li_scope)

    assert zhang_result["items"][0]["summary"]["content"] == "张三摘要"
    assert zhang_result["items"][0]["analysis"]["content"] == "张三分析"
    assert zhang_result["items"][0]["final"]["content"] == "张三final"
    assert li_result["items"][0]["summary"]["content"] == "李四摘要"
    assert li_result["items"][0]["analysis"]["content"] == "李四分析"
    assert li_result["items"][0]["final"]["content"] == "李四final"



def test_load_complaint_analysis_v2_list_isolated_by_entity_id():
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["entity-v2.pdf"])
    entity_a_scope = resolve_complaint_scope("entity", "org_30", "entity_a", "张三")
    entity_b_scope = resolve_complaint_scope("entity", "org_30", "entity_b", "张三")
    for scope_context in (entity_a_scope, entity_b_scope):
        root = get_scoped_complaint_root(base_id, scope_context)
        if os.path.isdir(root):
            shutil.rmtree(root)

    async def setup_data(scope_context, summary_text, analysis_text, final_text):
        _summary_payload, summary_version = await orchestrator_module._save_stage_result(
            "u1",
            base_id,
            stage="summary_v2",
            markdown=summary_text,
            result_payload={"summary_markdown": summary_text},
            base_versions={"base_text_version": 1},
            meta_extra={"task": "summary_v2"},
            scope_context=scope_context,
        )
        _analysis_payload, analysis_version = await orchestrator_module._save_stage_result(
            "u1",
            base_id,
            stage="analysis_v2",
            markdown=analysis_text,
            result_payload={"analysis_markdown": analysis_text, "law_search": {"articles": []}, "reference_content": []},
            base_versions={"base_text_version": 1, "summary_v2_version": summary_version},
            meta_extra={"task": "analysis_v2"},
            scope_context=scope_context,
        )
        await orchestrator_module._save_final_stage_result(
            user_id="u1",
            base_id=base_id,
            text_version=1,
            summary_version=summary_version,
            analysis_version=analysis_version,
            summary_markdown=summary_text,
            analysis_markdown=analysis_text,
            reference_content=[],
            final_markdown=final_text,
            scope_context=scope_context,
            model="test-model",
        )

    asyncio.run(setup_data(entity_a_scope, "A摘要", "A分析", "Afinal"))
    asyncio.run(setup_data(entity_b_scope, "B摘要", "B分析", "Bfinal"))

    entity_a_result = complaint_api_service_module.load_complaint_analysis_v2_list("u2", base_id, scope_context=entity_a_scope)
    entity_b_result = complaint_api_service_module.load_complaint_analysis_v2_list("u3", base_id, scope_context=entity_b_scope)

    assert entity_a_result["items"][0]["summary"]["content"] == "A摘要"
    assert entity_a_result["items"][0]["analysis"]["content"] == "A分析"
    assert entity_a_result["items"][0]["final"]["content"] == "Afinal"
    assert entity_b_result["items"][0]["summary"]["content"] == "B摘要"
    assert entity_b_result["items"][0]["analysis"]["content"] == "B分析"
    assert entity_b_result["items"][0]["final"]["content"] == "Bfinal"


def test_save_reused_text_content_reuses_existing_text_cache():
    user_id = "reuse_text_user"
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["reuse.pdf"])
    text_base_dir = os.path.dirname(complaint_api_service_module.stage_dir(user_id, base_id, "text"))
    if os.path.isdir(text_base_dir):
        shutil.rmtree(text_base_dir)

    first_text, first_version = asyncio.run(
        complaint_api_service_module.save_reused_text_content(
            user_id,
            base_id,
            "字段提取 OCR 文本",
            file_ids=["reuse.pdf"],
            source_request_id="conv-1",
        )
    )
    second_text, second_version = asyncio.run(
        complaint_api_service_module.save_reused_text_content(
            user_id,
            base_id,
            "新的 OCR 文本",
            file_ids=["reuse.pdf"],
            source_request_id="conv-2",
        )
    )

    assert first_text == "字段提取 OCR 文本"
    assert second_text == "字段提取 OCR 文本"
    assert first_version == 1
    assert second_version == 1
    text_path = os.path.join(complaint_api_service_module.stage_dir(user_id, base_id, "text", 1), "final.txt")
    with open(text_path, "r", encoding="utf-8") as handle:
        assert handle.read() == "字段提取 OCR 文本"


def test_extract_text_and_save_reuses_wordfit_extract_text(monkeypatch, tmp_path):
    user_id = "wordfit_text_user"
    base_id = complaint_api_service_module.build_base_id("wordfit-case.pdf")
    scope_context = resolve_complaint_scope("default")
    root = get_scoped_complaint_root(base_id, scope_context)
    if os.path.isdir(root):
        shutil.rmtree(root)
    original_path = tmp_path / "case.pdf"
    original_path.write_bytes(b"%PDF-1.4\n")
    captured = {}

    async def fake_wordfit(user_id_arg, base_id_arg, original_path_arg, image_prefix, ocr_model=None, scope_context=None):
        captured["user_id"] = user_id_arg
        captured["base_id"] = base_id_arg
        captured["original_path"] = original_path_arg
        captured["ocr_model"] = ocr_model
        captured["scope_path"] = scope_context.scope_path if scope_context else ""
        return "wordfit OCR 文本"

    monkeypatch.setattr(complaint_api_service_module, "extract_text_from_original_path_with_wordfit", fake_wordfit)

    text, version = asyncio.run(complaint_api_service_module.extract_text_and_save(user_id, base_id, str(original_path), force=True, ocr_model="ocr-model-a", scope_context=scope_context))

    assert text == "wordfit OCR 文本"
    assert version == 1
    assert captured == {"user_id": user_id, "base_id": base_id, "original_path": str(original_path), "ocr_model": "ocr-model-a", "scope_path": "default"}
    text_path = os.path.join(complaint_api_service_module.stage_dir(user_id, base_id, "text", 1, scope_context=scope_context), "final.txt")
    meta_path = os.path.join(complaint_api_service_module.stage_dir(user_id, base_id, "text", 1, scope_context=scope_context), "meta.json")
    with open(text_path, "r", encoding="utf-8") as handle:
        assert handle.read() == "wordfit OCR 文本"
    with open(meta_path, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
    assert meta["source"] == "word_fit_extract"
    assert meta["extraction_engine"] == "word_fit_extract"
    assert meta["scope_path"] == "default"


def test_extract_text_from_original_path_falls_back_when_wordfit_fails(monkeypatch, tmp_path):
    user_id = "wordfit_fallback_user"
    base_id = "wordfit-fallback-base"
    original_path = tmp_path / "case.pdf"
    original_path.write_bytes(b"%PDF-1.4\n")
    calls = {"legacy": 0}

    async def fake_wordfit(*args, **kwargs):
        raise RuntimeError("wordfit failed")

    async def fake_legacy(*args, **kwargs):
        calls["legacy"] += 1
        return "legacy OCR 文本"

    monkeypatch.setattr(complaint_api_service_module, "extract_text_from_original_path_with_wordfit", fake_wordfit)
    monkeypatch.setattr(complaint_api_service_module, "extract_text_from_original_path_legacy", fake_legacy)

    text = asyncio.run(complaint_api_service_module.extract_text_from_original_path(user_id, base_id, str(original_path), "prefix"))

    assert text == "legacy OCR 文本"
    assert calls["legacy"] == 1


def test_generate_summary_and_save_only_persists_structured_payload(monkeypatch):
    user_id = "summary_user"
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["summary.pdf"])
    summary_base_dir = os.path.dirname(complaint_api_service_module.stage_dir(user_id, base_id, "summary"))
    if os.path.isdir(summary_base_dir):
        shutil.rmtree(summary_base_dir)

    structured_payload = {
        "incident": "举报人称账户异常。",
        "profile": {"reporter": "张三", "reported_institution": "某银行", "appeals": ["依法查处"]},
        "complaints": [{"label": "未履行风控义务", "description": "银行未履行风控义务。", "claim_text": "银行未履行风控义务。"}],
    }

    async def fake_generate_summary_structured_payload(*args, **kwargs):
        return structured_payload, ""

    monkeypatch.setattr(complaint_api_service_module, "generate_summary_structured_payload", fake_generate_summary_structured_payload)

    summary_text, summary_version = asyncio.run(
        complaint_api_service_module.generate_summary_and_save(user_id, base_id, "原文文本", 1, force=True)
    )

    assert summary_text == "事情经过：举报人称账户异常。\n基本信息：举报人：张三；被举报机构：某银行；诉求：依法查处\n举报事项1：未履行风控义务；银行未履行风控义务。；银行未履行风控义务。"
    assert summary_version == 1

    summary_dir = complaint_api_service_module.stage_dir(user_id, base_id, "summary", summary_version)
    with open(os.path.join(summary_dir, "final.json"), "r", encoding="utf-8") as handle:
        saved = json.load(handle)

    assert saved["_schema"] == "v2"
    assert "_raw" not in saved
    assert "summary_markdown" not in saved
    assert saved["structured_payload"] == complaint_api_service_module.merge_reply_draft_context(structured_payload, "原文文本")


    user_id = "running_user"
    base_id = complaint_api_service_module.build_base_id_for_file_ids(["running-a.pdf"])
    base_dir = os.path.dirname(complaint_api_service_module.stage_dir(user_id, base_id, "issue_analysis"))
    if os.path.isdir(base_dir):
        shutil.rmtree(base_dir)
    summary_base_dir = os.path.dirname(complaint_api_service_module.stage_dir(user_id, base_id, "summary"))
    if os.path.isdir(summary_base_dir):
        shutil.rmtree(summary_base_dir)

    summary_dir = complaint_api_service_module.stage_dir(user_id, base_id, "summary", 1)
    os.makedirs(summary_dir, exist_ok=True)
    structured_payload = {
        "incident": "举报人称账户被违规使用。",
        "profile": {
            "reporter": "张三",
            "report_date": "2026-05-01",
            "reported_institution": "某银行",
            "title_base": "关于某银行的举报",
            "appeals": ["依法查处"],
        },
        "complaints": [
            {
                "label": "未履行风控义务",
                "description": "举报人称银行未履行风险管理义务。",
                "claim_text": "银行未履行风控义务。",
                "original_quotes": ["未风控"],
                "evidence_materials": ["交易流水"],
                "user_cited_laws": [{"raw_text": "反电信网络诈骗法"}],
            }
        ],
    }
    with open(os.path.join(summary_dir, "final.json"), "w", encoding="utf-8") as handle:
        json.dump({"structured_payload": structured_payload}, handle, ensure_ascii=False)
    complaint_api_service_module.save_manifest(user_id, base_id, {"text": {"current": 1}, "summary": {"current": 1}})

    result = complaint_api_service_module.load_issue_analysis_list(user_id, base_id, page=1, page_size=50)

    assert result["total"] == 1
    item = result["items"][0]
    assert item["status"] == "running"
    assert item["issue_analysis_version"] == 0
    assert item["incident"] == "举报人称账户被违规使用。"
    assert item["complaint_profile"]["reporter"] == "张三"
    assert item["items_and_appeals"]["complaint_items"][0]["content"] == "举报人称银行未履行风险管理义务。"
    assert item["items_and_appeals"]["appeals"] == [{"id": "appeal_1", "content": "依法查处"}]
    assert item["law_basis"] == []
    assert item["versions"]["summary_version"] == 1


def test_build_supplement_material_context_extracts_date_and_evidence():
    context = complaint_api_service_module.build_supplement_material_context("本单位于2024年8月12日收到举报信。你于2024年8月29日提交二次补充材料，反映交易流水问题。")
    supplement = context["supplement_material"]

    assert supplement["exists"] is True
    assert supplement["dates"] == ["2024年8月29日"]
    assert supplement["sentence"] == "，于2024年8月29日收到你的补充材料"
    assert supplement["evidence_spans"][0]["keyword"] == "二次补充材料"
    assert "二次补充材料" in supplement["evidence_spans"][0]["text"]


def test_build_supplement_material_context_uses_generic_sentence_without_date():
    context = complaint_api_service_module.build_supplement_material_context("举报人提交二次补充材料，补充说明相关问题。")

    assert context["supplement_material"]["exists"] is True
    assert context["supplement_material"]["dates"] == []
    assert context["supplement_material"]["sentence"] == "，并收到你的补充材料"


def test_complaint_analysis_list_returns_empty_response_on_error():
    payload = fastapi_routes_module.ComplaintAnalysisListRequest(user_id="api_user", file_id="", page=1, page_size=50)

    response = asyncio.run(fastapi_routes_module.complaint_analysis_list(payload))
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert body["items"] == []
    assert body["total"] == 0
    assert body["exists"] is False
    assert body["message"] == "file_id 或 file_ids 不能为空"


def test_build_extract_phase_result_returns_incident_and_item_details():
    result = complaint_api_service_module.build_extract_phase_result(
        [
            {
                "label": "违反反诈义务",
                "description": "举报人称银行未履行反电信网络诈骗风险管理义务。",
                "claim_text": "举报人反映银行涉嫌未履行反诈义务。",
                "original_quotes": ["违反电信网络诈骗法"],
                "evidence_materials": ["U盘证据"],
                "user_cited_laws": [{"raw_text": "反电信网络诈骗法", "normalized_law": "中华人民共和国反电信网络诈骗法", "cited_clause_text": "异常账户、可疑交易监测处置义务"}],
            }
        ],
        {"reporter": "张三", "report_date": "2026年5月6日", "reported_institution": "中国银行", "appeals": ["移送公安机关"], "title_base": "张三举报中国银行问题"},
        "举报人称相关账户被用于网络赌博平台收款。",
    )

    assert result["incident"] == "举报人称相关账户被用于网络赌博平台收款。"
    assert result["complaint_profile"]["reporter"] == "张三"
    assert result["items_and_appeals"]["appeals"] == [{"id": "appeal_1", "content": "移送公安机关"}]
    item = result["items_and_appeals"]["complaint_items"][0]
    assert item["content"] == "举报人称银行未履行反电信网络诈骗风险管理义务。"
    assert item["claim_text"] == "举报人反映银行涉嫌未履行反诈义务。"
    assert item["original_quotes"] == ["违反电信网络诈骗法"]
    assert item["evidence_materials"] == ["U盘证据"]
    assert item["user_cited_laws"][0]["normalized_law"] == "中华人民共和国反电信网络诈骗法"


def test_assemble_issue_analysis_result_returns_incident_and_item_details():
    result = asyncio.run(
        complaint_api_service_module.assemble_issue_analysis_result(
            "u1",
            "b1",
            1,
            2,
            [
                {
                    "label": "违规开立账户",
                    "description": "举报人称银行违规开立支付账户。",
                    "original_quotes": ["违规开立支付账户"],
                    "evidence_materials": ["交易流水"],
                    "user_cited_laws": [{"raw_text": "支付账户被违规使用", "normalized_law": "", "cited_clause_text": "支付账户被违规使用"}],
                }
            ],
            [],
            ["结论"],
            profile={"reported_institution": "中国银行", "appeals": ["行政处罚"]},
            incident="举报人称商户账户被用于网络赌博平台。",
        )
    )

    assert result["incident"] == "举报人称商户账户被用于网络赌博平台。"
    assert result["items_and_appeals"]["appeals"] == [{"id": "appeal_1", "content": "行政处罚"}]
    item = result["items_and_appeals"]["complaint_items"][0]
    assert item["claim_text"] == "举报人称银行违规开立支付账户。"
    assert item["original_quotes"] == ["违规开立支付账户"]
    assert item["evidence_materials"] == ["交易流水"]
    assert item["user_cited_laws"][0]["cited_clause_text"] == "支付账户被违规使用"


def test_assemble_issue_analysis_result_keeps_law_skill_analysis_per_item():
    analysis = {
        "complaint_id": "complaint_1",
        "neutral_claim": "举报人反映银行违规开户。",
        "user_cited_laws_analysis": [{"normalized_law": "支付结算办法"}],
        "possible_laws": [{"law": "支付结算办法"}],
        "verification_points": ["核实开户资料"],
        "document_checklist": ["开户申请书"],
        "preliminary_conclusion": "需进一步核实。",
    }
    result = asyncio.run(
        complaint_api_service_module.assemble_issue_analysis_result(
            "u1",
            "b1",
            1,
            2,
            [{"label": "违规开户", "description": "举报人称银行违规开户。"}],
            [{"complaint": "举报人称银行违规开户。", "categories": [{"law_skill_analysis": analysis}]}],
            ["结论"],
        )
    )

    item = result["items_and_appeals"]["complaint_items"][0]
    assert item["law_skill_analysis"] == analysis


def test_assemble_issue_analysis_result_groups_items_and_appeals_by_reported_entities():
    result = asyncio.run(
        complaint_api_service_module.assemble_issue_analysis_result(
            "u1",
            "b1",
            1,
            2,
            [
                {"label": "A银行违规开户", "description": "举报人称A银行违规开户。"},
                {"label": "未说明机构的转账问题", "description": "举报人称相关账户存在异常转账。"},
            ],
            [],
            ["结论"],
            profile={"appeals": ["要求A银行整改", "依法查处"]},
            incident="举报人反映多个机构存在问题。",
            reported_entities=["A银行", "B支付公司"],
        )
    )

    items = result["items_and_appeals"]["complaint_items"]
    appeals = result["items_and_appeals"]["appeals"]
    assert items[0]["reported_entities"] == ["A银行"]
    assert items[1]["reported_entities"] == ["A银行", "B支付公司"]
    assert appeals[0]["reported_entities"] == ["A银行"]
    assert appeals[1]["reported_entities"] == ["A银行", "B支付公司"]
    groups = {item["name"]: item for item in result["reported_entity_groups"]}
    assert len(groups["A银行"]["complaint_items"]) == 2
    assert len(groups["B支付公司"]["complaint_items"]) == 1


def test_aggregate_law_and_checks_removes_txt_suffix_from_law_name():
    law_basis, _, _ = complaint_api_service_module.aggregate_law_and_checks(
        [
            {
                "categories": [
                    {
                        "一级分类": "支付账户",
                        "法律法规及其原文": [
                            {"law": "非银行支付机构监督管理条例.txt", "item": "第一条", "item_text": "条款原文"}
                        ],
                    }
                ]
            }
        ]
    )

    assert law_basis == [{"law_name": "非银行支付机构监督管理条例", "articles": [{"item": "第一条", "item_text": "条款原文"}]}]


def test_aggregate_law_and_checks_removes_law_line_reference():
    law_basis, _, _ = complaint_api_service_module.aggregate_law_and_checks(
        [
            {
                "categories": [
                    {
                        "一级分类": "支付账户",
                        "法律法规及其原文": [
                            {"law": "银行卡收单业务管理办法.txt", "item": "第二条（约第8-10行）", "item_text": "定义了收单业务（约第8-10行）"}
                        ],
                    }
                ]
            }
        ]
    )

    assert law_basis == [{"law_name": "银行卡收单业务管理办法", "articles": [{"item": "第二条", "item_text": "定义了收单业务"}]}]


def test_aggregate_law_and_checks_filters_generic_law_article():
    law_basis, _, _ = complaint_api_service_module.aggregate_law_and_checks(
        [
            {
                "categories": [
                    {
                        "一级分类": "支付账户",
                        "法律法规及其原文": [
                            {"law": "银行卡收单业务管理办法.txt", "item": "相关条款", "item_text": "泛化条款摘要"}
                        ],
                    }
                ]
            }
        ]
    )

    assert law_basis == []


def test_skill_matched_clauses_to_law_items_removes_law_line_reference():
    law_items = analysis_agent_module._skill_matched_clauses_to_law_items(
        {
            "possible_laws": [
                {
                    "matched_clauses": [
                        {"law": "银行卡收单业务管理办法.txt", "article": "第二条（约第8-10行）", "text_summary": "定义了收单业务（约第8-10行）"}
                    ]
                }
            ]
        }
    )

    assert law_items == [{"law": "银行卡收单业务管理办法.txt", "item": "第二条", "item_text": "定义了收单业务"}]


def test_skill_matched_clauses_to_law_items_filters_generic_law_article():
    law_items = analysis_agent_module._skill_matched_clauses_to_law_items(
        {
            "possible_laws": [
                {
                    "matched_clauses": [
                        {"law": "银行卡收单业务管理办法.txt", "article": "相关条款", "text_summary": "泛化条款摘要"}
                    ]
                }
            ]
        }
    )

    assert law_items == []


def test_skill_matched_clauses_to_law_items_normalizes_article_aliases():
    law_items = analysis_agent_module._skill_matched_clauses_to_law_items(
        {
            "possible_laws": [
                {
                    "matched_clauses": [
                        {"law": "银行卡收单业务管理办法.txt", "items": "第二条", "text_summary": "定义了收单业务"},
                        {"law": "银行卡收单业务管理办法.txt", "time": "第十三条", "text_summary": "规定了收单机构管理要求"},
                    ]
                }
            ]
        }
    )

    assert law_items == [
        {"law": "银行卡收单业务管理办法.txt", "item": "第二条", "item_text": "定义了收单业务"},
        {"law": "银行卡收单业务管理办法.txt", "item": "第十三条", "item_text": "规定了收单机构管理要求"},
    ]


def test_aggregate_law_and_checks_normalizes_article_aliases():
    law_basis, _, _ = complaint_api_service_module.aggregate_law_and_checks(
        [
            {
                "categories": [
                    {
                        "一级分类": "支付账户",
                        "法律法规及其原文": [
                            {"law": "银行卡收单业务管理办法.txt", "items": "第二条", "item_text": "定义了收单业务"},
                            {"law": "银行卡收单业务管理办法.txt", "time": "第十三条", "item_text": "规定了收单机构管理要求"},
                        ],
                    }
                ]
            }
        ]
    )

    assert law_basis == [
        {
            "law_name": "银行卡收单业务管理办法",
            "articles": [
                {"item": "第二条", "item_text": "定义了收单业务"},
                {"item": "第十三条", "item_text": "规定了收单机构管理要求"},
            ],
        }
    ]


def test_normalize_reply_case_title_adds_prefix_and_trims_punctuation():
    assert complaint_api_service_module.normalize_reply_case_title("杉德支付公司违规开展支付业务的举报。") == "关于杉德支付公司违规开展支付业务的举报"
    assert complaint_api_service_module.normalize_reply_case_title("关于杉德支付公司违规开展支付业务的举报") == "关于杉德支付公司违规开展支付业务的举报"


def test_load_cached_reply_by_input_requires_matching_auto_meta(monkeypatch):
    monkeypatch.setattr(
        complaint_api_service_module,
        "load_latest_reply",
        lambda user_id, base_id, scope_context=None: ({"title": "t", "content": "c"}, 3),
    )
    monkeypatch.setattr(
        complaint_api_service_module,
        "load_reply_meta",
        lambda user_id, base_id, version, scope_context=None: {
            "source": "auto",
            "input_hash": "abc",
            "prompt_version": complaint_api_service_module.REPLY_DRAFT_PROMPT_VERSION,
            "model": "m1",
        },
    )

    cached, version = complaint_api_service_module.load_cached_reply_by_input("u1", "b1", "abc", "m1")

    assert cached == {"title": "t", "content": "c"}
    assert version == 3


def test_build_reply_generation_context_removes_reporter_id_card():
    context = complaint_api_service_module.build_reply_generation_context(
        {
            "complaint_profile": {"reporter": "王志武(220323198605037444)", "report_date": "2026年1月1日"},
            "items_and_appeals": {"complaint_items": []},
        },
        [],
    )

    assert context["reporter"] == "王志武"
    assert complaint_api_service_module.normalize_reply_reporter("王志武（身份证号：220323198605037444）") == "王志武"


def test_build_reply_generation_context_prefers_reporter_name_param():
    context = complaint_api_service_module.build_reply_generation_context(
        {
            "complaint_profile": {"reporter": "王志武(220323198605037444)", "report_date": "2026年1月1日"},
            "items_and_appeals": {"complaint_items": []},
        },
        [],
        reporter_name="张三-fys-临时标记",
    )

    assert context["reporter"] == "张三"


def test_build_reply_draft_prompt_requires_semantic_attribution():
    system_prompt, user_content = complaint_api_service_module.build_reply_draft_prompt(
        "答复意见书",
        {"appeals": [], "complaint_items": [{"id": "complaint_1", "content": "你反映扣费问题"}]},
        "核实内容要点",
        True,
        "处理措施要点",
        "2026年3月12日",
    )

    assert "语义判断 verification_content" in system_prompt
    assert "不得默认套用到每一个举报事项" in system_prompt
    assert "必须融合改写" in system_prompt
    assert "不得机械拼接“事项简述 + 全案材料摘录”" in system_prompt
    assert "重新组织表达" in system_prompt
    assert "不得删除关键主体、交易行为、金额、时间、法规名称" in system_prompt
    assert "不得添加“先生”“女士”“同志”“尊敬的”" in system_prompt
    assert "不得使用“举报人反映/举报人称”" in system_prompt
    assert "verification_content" in user_content
    assert "{收件人称谓}" in user_content
    assert "属地监管原则" in user_content
    assert "属地管理原则" not in user_content
    assert "supplement_material_sentence" in user_content
    assert "review_report_text" in user_content
    assert "请补充补充材料收到情况" not in user_content
    assert "请补充延长举报办理期限告知书寄送情况" not in user_content


def test_reply_draft_skill_payload_does_not_force_global_materials():
    payload = complaint_api_service_module.build_reply_draft_skill_payload(
        "答复意见书",
        {"appeals": [], "complaint_items": [{"id": "complaint_1", "content": "你反映扣费问题"}]},
        "核实内容要点",
        True,
        "处理措施要点",
        "2026年3月12日",
    )

    assert payload["manual_inputs"]["verification_content"] == "核实内容要点"
    assert payload["manual_inputs"]["disposal_measures"] == "处理措施要点"
    assert payload["must_include_texts"] == []
    assert "属地监管原则" in payload["content_template"]
    assert "属地管理原则" not in payload["content_template"]
    assert "supplement_material_sentence" in payload["content_template"]
    assert "review_report_text" in payload
    assert "请补充补充材料收到情况" not in payload["content_template"]
    assert "请补充延长举报办理期限告知书寄送情况" not in payload["content_template"]


def test_validate_reply_draft_content_does_not_require_disposal_measures():
    content = "\n".join(
        [
            "答复意见书",
            "编号：{请补充文书编号}",
            "你：",
            "现答复如下：",
            "一、关于你反映事项的调查",
            "{请补充关于该事项的调查核实及法律适用意见}",
            "二、关于你举报信中的其他诉求",
            "{请补充需要答复的内容}",
            "感谢你对我行工作的支持。",
            "{请补充落款机关}",
        ]
    )

    complaint_api_service_module.validate_reply_draft_content(content, expected_title="答复意见书", violation_found=True, disposal_measures="处理措施要点", require_appeal_placeholder=True)


def test_validate_reply_draft_content_rejects_extra_salutation_title():
    content = "\n".join(
        [
            "答复意见书",
            "编号：{请补充文书编号}",
            "张三先生：",
            "现答复如下：",
            "一、关于你反映事项的调查",
            "{请补充关于该事项的调查核实及法律适用意见}",
            "二、关于你举报信中的其他诉求",
            "{请补充需要答复的内容}",
            "感谢你对我行工作的支持。",
            "{请补充落款机关}",
        ]
    )

    with pytest.raises(ValueError, match="抬头包含多余称谓"):
        complaint_api_service_module.validate_reply_draft_content(content, expected_title="答复意见书", require_appeal_placeholder=True)


def test_generate_reply_content_from_inputs_overrides_title_and_validates_sections(monkeypatch):
    async def fake_call_llm_text(system_prompt, user_content, model, temperature=0.2):
        return """```json
{"title":"错误标题","content":"关于杉德支付公司违规开展支付业务的举报的调查核实情况答复\\n\\n尊敬的举报人：\\n\\n您好！\\n\\n您提交的“关于杉德支付公司违规开展支付业务的举报”举报材料已收悉。我们高度重视，立即组织力量对您反映的问题进行了全面调查核实，现将有关情况答复如下：\\n\\n一、调查核实情况\\n\\n经查，被举报机构在相关业务中存在以下情况：\\n核实内容\\n\\n二、处理意见\\n\\n根据调查核实情况，依据相关法律法规规定，我们做出如下处理决定：\\n处理措施\\n\\n三、下一步措施\\n\\n我单位将根据核查情况依法开展后续工作。\\n\\n感谢您对我们的监督和支持。如您对本答复有异议，可依法申请行政复议或提起行政诉讼。\\n\\n此复。\\n\\n2026年3月12日"}
```"""

    monkeypatch.setattr(complaint_api_service_module, "call_llm_text", fake_call_llm_text)
    monkeypatch.setattr(complaint_api_service_module, "get_reply_model", lambda: "mock-model")
    monkeypatch.setattr(complaint_api_service_module, "build_reply_document_date", lambda: "2026年3月12日")

    title, content = asyncio.run(
        complaint_api_service_module.generate_reply_content_from_inputs(
            "杉德支付公司违规开展支付业务的举报",
            "核实内容",
            "处理措施",
        )
    )

    assert title == "关于杉德支付公司违规开展支付业务的举报的答复意见书"
    assert "一、调查核实情况" in content
    assert "二、处理意见" in content
    assert "三、下一步措施" in content


def test_extract_reply_message_text_falls_back_to_reasoning_field():
    class _Message:
        content = ""
        reasoning_content = '{"title":"t","content":"c"}'

    class _Choice:
        message = _Message()

    class _Resp:
        choices = [_Choice()]

    assert complaint_api_service_module.extract_reply_message_text(_Resp()) == '{"title":"t","content":"c"}'


def test_call_llm_text_passes_disable_thinking_extra_body(monkeypatch):
    captured = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)

            class _Message:
                content = "ok"

            class _Choice:
                message = _Message()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(complaint_api_service_module, "get_async_openai_client", lambda: _Client())
    monkeypatch.setattr(complaint_api_service_module, "build_thinking_extra_body", lambda model, enable: {"chat_template_kwargs": {"enable_thinking": enable}})

    text = asyncio.run(complaint_api_service_module.call_llm_text("sys", "user", "GLM-4.7", temperature=0.1))

    assert text == "ok"
    assert captured["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_stream_reply_draft_uses_new_inputs_and_saves_generated_reply(monkeypatch):
    payload = fastapi_routes_module.ReplyDraftRequest(
        user_id="api_user",
        file_id="FILE_1",
        force=True,
        case_title="杉德支付公司违规开展支付业务的举报",
        verification_content="核实内容",
        violation_found=True,
        disposal_measures="处理措施",
    )

    monkeypatch.setattr(fastapi_routes_module, "load_latest_issue_analysis", lambda *args, **kwargs: ({"complaint_items": []}, 1))
    monkeypatch.setattr(fastapi_routes_module, "get_reply_model", lambda: "mock-model")
    monkeypatch.setattr(fastapi_routes_module, "build_reply_draft_input_hash", lambda *args, **kwargs: "hash-1")
    monkeypatch.setattr(fastapi_routes_module, "generate_reply_content_from_inputs", lambda *args, **kwargs: _async_return(("关于杉德支付公司违规开展支付业务的举报的答复意见书", "正文内容")))
    monkeypatch.setattr(
        fastapi_routes_module,
        "save_reply_result",
        lambda *args, **kwargs: _async_return(({"base_id": "a79100eeba420af1", "title": "关于杉德支付公司违规开展支付业务的举报的答复意见书", "content": "正文内容", "source_versions": {}, "source": "auto", "saved_at": "2026-03-12T10:00:00Z"}, 1)),
    )

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_reply_draft(payload)]

    events = asyncio.run(collect_events())

    assert len(events) == 6
    assert '"message": "开始生成答复意见书"' in events[0]
    assert '"message": "正在整理案件信息"' in events[1]
    assert '"message": "正在组织文书结构"' in events[2]
    assert '"message": "正在生成答复内容"' in events[3]
    assert '"message": "正在校验并保存草稿"' in events[4]
    assert '"reply_version": 1' in events[5]
    assert '"cache_hit": false' in events[5]


def test_stream_reply_draft_accepts_multiple_file_ids(monkeypatch):
    payload = fastapi_routes_module.ReplyDraftRequest(
        user_id="api_user",
        file_ids=["b.pdf", "a.pdf"],
        force=True,
        verification_content="核实内容",
        violation_found=True,
        disposal_measures="处理措施",
    )
    captured = {}

    def fake_load_latest_issue_analysis(user_id, base_id, scope_context=None):
        captured["load_base_id"] = base_id
        return {"complaint_items": []}, 1

    def fake_hash(file_id, *args, **kwargs):
        captured["hash_file_id"] = file_id
        return "hash-1"

    async def fake_save_reply_result(user_id, base_id, title, content, source_versions, source, meta_extra=None, scope_context=None):
        captured["save_base_id"] = base_id
        return {"base_id": base_id, "title": title, "content": content, "source_versions": source_versions, "source": source, "saved_at": "2026-03-12T10:00:00Z"}, 1

    monkeypatch.setattr(fastapi_routes_module, "load_latest_issue_analysis", fake_load_latest_issue_analysis)
    monkeypatch.setattr(fastapi_routes_module, "get_reply_model", lambda: "mock-model")
    monkeypatch.setattr(fastapi_routes_module, "build_reply_draft_input_hash", fake_hash)
    monkeypatch.setattr(fastapi_routes_module, "generate_reply_content_from_inputs", lambda *args, **kwargs: _async_return(("答复意见书", "正文内容")))
    monkeypatch.setattr(fastapi_routes_module, "save_reply_result", fake_save_reply_result)

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_reply_draft(payload)]

    events = asyncio.run(collect_events())
    expected_base_id = complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])

    assert captured["load_base_id"] == expected_base_id
    assert captured["save_base_id"] == expected_base_id
    assert captured["hash_file_id"] == "a.pdf,b.pdf"
    assert f'"base_id": "{expected_base_id}"' in events[-1]
    assert '"status": "completed"' in events[-1]


def test_stream_reply_draft_accepts_comma_file_ids_string(monkeypatch):
    payload = fastapi_routes_module.ReplyDraftRequest(
        user_id="api_user",
        file_ids="b.pdf,a.pdf",
        force=True,
        verification_content="核实内容",
        violation_found=True,
        disposal_measures="处理措施",
    )
    captured = {}

    def fake_load_latest_issue_analysis(user_id, base_id, scope_context=None):
        captured["base_id"] = base_id
        return {"complaint_items": []}, 1

    def fake_hash(file_id, *args, **kwargs):
        captured["hash_file_id"] = file_id
        return "hash-1"

    monkeypatch.setattr(fastapi_routes_module, "load_latest_issue_analysis", fake_load_latest_issue_analysis)
    monkeypatch.setattr(fastapi_routes_module, "get_reply_model", lambda: "mock-model")
    monkeypatch.setattr(fastapi_routes_module, "build_reply_draft_input_hash", fake_hash)
    monkeypatch.setattr(fastapi_routes_module, "generate_reply_content_from_inputs", lambda *args, **kwargs: _async_return(("答复意见书", "正文内容")))
    monkeypatch.setattr(
        fastapi_routes_module,
        "save_reply_result",
        lambda *args, **kwargs: _async_return(({"base_id": captured["base_id"], "title": "答复意见书", "content": "正文内容", "source_versions": {}, "source": "auto", "saved_at": "2026-03-12T10:00:00Z"}, 1)),
    )

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_reply_draft(payload)]

    events = asyncio.run(collect_events())

    assert captured["base_id"] == complaint_api_service_module.build_base_id_for_file_ids(["a.pdf", "b.pdf"])
    assert captured["hash_file_id"] == "a.pdf,b.pdf"
    assert '"status": "completed"' in events[-1]


def test_stream_reply_draft_returns_cached_reply_when_input_matches(monkeypatch):
    payload = fastapi_routes_module.ReplyDraftRequest(
        user_id="api_user",
        file_id="FILE_1",
        force=False,
        case_title="杉德支付公司违规开展支付业务的举报",
        verification_content="核实内容",
        violation_found=True,
        disposal_measures="处理措施",
    )

    monkeypatch.setattr(fastapi_routes_module, "load_latest_issue_analysis", lambda *args, **kwargs: ({"complaint_items": []}, 1))
    monkeypatch.setattr(fastapi_routes_module, "get_reply_model", lambda: "mock-model")
    monkeypatch.setattr(fastapi_routes_module, "build_reply_draft_input_hash", lambda *args, **kwargs: "hash-1")
    monkeypatch.setattr(
        fastapi_routes_module,
        "load_cached_reply_by_input",
        lambda *args, **kwargs: (
            {
                "base_id": "a79100eeba420af1",
                "title": "关于杉德支付公司违规开展支付业务的举报的答复意见书",
                "content": "正文内容",
                "source_versions": {},
                "source": "auto",
                "saved_at": "2026-03-12T10:00:00Z",
            },
            5,
        ),
    )

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_reply_draft(payload)]

    events = asyncio.run(collect_events())

    assert len(events) == 3
    assert '"message": "开始生成答复意见书"' in events[0]
    assert '"message": "正在整理案件信息"' in events[1]
    assert '"reply_version": 5' in events[2]
    assert '"cache_hit": true' in events[2]


def test_stream_reply_draft_allows_missing_disposal_measures(monkeypatch):
    payload = fastapi_routes_module.ReplyDraftRequest(
        user_id="api_user",
        file_id="FILE_1",
        force=True,
        verification_content="核实内容",
        violation_found=False,
    )
    captured = {}

    monkeypatch.setattr(fastapi_routes_module, "load_latest_issue_analysis", lambda *args, **kwargs: ({"complaint_items": []}, 1))
    monkeypatch.setattr(fastapi_routes_module, "get_reply_model", lambda: "mock-model")
    monkeypatch.setattr(fastapi_routes_module, "build_reply_draft_input_hash", lambda *args, **kwargs: "hash-1")

    async def fake_generate_reply(*args, **kwargs):
        captured["disposal_measures"] = args[3]
        return "答复意见书", "正文内容"

    monkeypatch.setattr(fastapi_routes_module, "generate_reply_content_from_inputs", fake_generate_reply)
    monkeypatch.setattr(
        fastapi_routes_module,
        "save_reply_result",
        lambda *args, **kwargs: _async_return(({"base_id": "a79100eeba420af1", "title": "答复意见书", "content": "正文内容", "source_versions": {}, "source": "auto", "saved_at": "2026-03-12T10:00:00Z"}, 1)),
    )

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_reply_draft(payload)]

    events = asyncio.run(collect_events())

    assert captured["disposal_measures"] == ""
    assert '"status": "completed"' in events[-1]


def test_stream_reply_draft_passes_review_report_text(monkeypatch):
    payload = fastapi_routes_module.ReplyDraftRequest(
        user_id="api_user",
        file_id="FILE_1",
        force=True,
        verification_content="核实内容",
        violation_found=False,
        review_report="review.docx",
    )
    captured = {}

    monkeypatch.setattr(fastapi_routes_module, "load_latest_issue_analysis", lambda *args, **kwargs: ({"items_and_appeals": {"appeals": [{"id": "appeal_1", "content": "退赔损失"}]}}, 1))
    monkeypatch.setattr(fastapi_routes_module, "get_reply_model", lambda: "mock-model")
    monkeypatch.setattr(fastapi_routes_module, "extract_supplement_material_sentence_from_summary", lambda *args, **kwargs: "")

    async def fake_load_review_report_text(review_report, user_id, base_id, scope_context=None):
        captured["review_report"] = review_report
        return "调查报告显示，关于退赔损失诉求，应通过司法途径解决。"

    def fake_hash(*args, **kwargs):
        captured["hash_kwargs"] = kwargs
        return "hash-1"

    async def fake_generate(*args, **kwargs):
        captured["generate_kwargs"] = kwargs
        return "答复意见书", "正文内容"

    monkeypatch.setattr(fastapi_routes_module, "load_review_report_text", fake_load_review_report_text)
    monkeypatch.setattr(fastapi_routes_module, "build_reply_draft_input_hash", fake_hash)
    monkeypatch.setattr(fastapi_routes_module, "generate_reply_content_from_inputs", fake_generate)
    monkeypatch.setattr(
        fastapi_routes_module,
        "save_reply_result",
        lambda *args, **kwargs: _async_return(({"base_id": "a79100eeba420af1", "title": "答复意见书", "content": "正文内容", "source_versions": {}, "source": "auto", "saved_at": "2026-03-12T10:00:00Z"}, 1)),
    )

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_reply_draft(payload)]

    events = asyncio.run(collect_events())

    assert '"status": "completed"' in events[-1]
    assert captured["review_report"] == "review.docx"
    assert captured["hash_kwargs"]["review_report"] == "review.docx"
    assert captured["hash_kwargs"]["review_report_text"] == "调查报告显示，关于退赔损失诉求，应通过司法途径解决。"
    assert captured["generate_kwargs"]["review_report_text"] == "调查报告显示，关于退赔损失诉求，应通过司法途径解决。"


async def _async_return(value):
    return value


def test_stream_mock_complaint_analysis_returns_completed_empty_result():
    payload = fastapi_routes_module.ComplaintAnalysisRequest(user_id="api_user", file_id="mock.zip", force=False, use_uploaded_pdf=False, match_strategy="two_stage")

    async def collect_events():
        return [item async for item in fastapi_routes_module._stream_mock_complaint_analysis(payload, None, "仅支持 PDF/PNG/JPG/JPEG 文件")]

    events = asyncio.run(collect_events())

    assert len(events) == 2
    assert '"status": "started"' in events[0]
    assert '"status": "completed"' in events[1]
    assert '"unsupported_file_type": true' in events[1]
    assert '"issue_analysis_version": 0' in events[1]
    assert '"complaint_items": []' in events[1]
