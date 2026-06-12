import asyncio
import os
import sys
import types


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

from apps.complaint_app.services import complaint_api_service as complaint_api_service_module
from apps.complaint_app import fastapi_routes as fastapi_routes_module


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
        {"kind": "stage", "progress": 30, "message": "正在提取举报事项与诉求", "phase_node": "complaint_extract_items"},
        {"kind": "stage", "progress": 50, "message": "正在判断举报属于哪类问题（如商户、扣款、账户）", "phase_node": "complaint_match_experience_stage1"},
        {"kind": "stage", "progress": 60, "message": "正在细化具体违规情形并补充法律依据", "phase_node": "complaint_match_experience_stage2"},
        {"kind": "stage", "progress": 80, "message": "正在生成核实思路", "phase_node": "complaint_final_conclusion"},
    ]


def test_normalize_reply_case_title_adds_prefix_and_trims_punctuation():
    assert complaint_api_service_module.normalize_reply_case_title("杉德支付公司违规开展支付业务的举报。") == "关于杉德支付公司违规开展支付业务的举报"
    assert complaint_api_service_module.normalize_reply_case_title("关于杉德支付公司违规开展支付业务的举报") == "关于杉德支付公司违规开展支付业务的举报"


def test_load_cached_reply_by_input_requires_matching_auto_meta(monkeypatch):
    monkeypatch.setattr(
        complaint_api_service_module,
        "load_latest_reply",
        lambda user_id, base_id: ({"title": "t", "content": "c"}, 3),
    )
    monkeypatch.setattr(
        complaint_api_service_module,
        "load_reply_meta",
        lambda user_id, base_id, version: {
            "source": "auto",
            "input_hash": "abc",
            "prompt_version": complaint_api_service_module.REPLY_DRAFT_PROMPT_VERSION,
            "model": "m1",
        },
    )

    cached, version = complaint_api_service_module.load_cached_reply_by_input("u1", "b1", "abc", "m1")

    assert cached == {"title": "t", "content": "c"}
    assert version == 3


def test_build_reply_draft_prompt_requires_material_to_be_reorganized():
    system_prompt, user_content = complaint_api_service_module.build_reply_draft_prompt(
        "关于杉德支付公司违规开展支付业务的举报的答复意见书",
        "关于杉德支付公司违规开展支付业务的举报",
        "核实内容要点",
        "处理措施要点",
        "2026年3月12日",
    )

    assert "是事实素材" in system_prompt
    assert "整理为正式、公文风格、连贯完整的自然段" in system_prompt
    assert "writing_requirements" in user_content


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
        disposal_measures="处理措施",
    )

    monkeypatch.setattr(fastapi_routes_module, "normalize_reply_case_title", lambda value: "关于杉德支付公司违规开展支付业务的举报")
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

    assert len(events) == 5
    assert '"message": "正在整理案件信息"' in events[0]
    assert '"message": "正在组织文书结构"' in events[1]
    assert '"message": "正在生成答复内容"' in events[2]
    assert '"message": "正在校验并保存草稿"' in events[3]
    assert '"reply_version": 1' in events[4]
    assert '"cache_hit": false' in events[4]


def test_stream_reply_draft_returns_cached_reply_when_input_matches(monkeypatch):
    payload = fastapi_routes_module.ReplyDraftRequest(
        user_id="api_user",
        file_id="FILE_1",
        force=False,
        case_title="杉德支付公司违规开展支付业务的举报",
        verification_content="核实内容",
        disposal_measures="处理措施",
    )

    monkeypatch.setattr(fastapi_routes_module, "normalize_reply_case_title", lambda value: "关于杉德支付公司违规开展支付业务的举报")
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

    assert len(events) == 2
    assert '"message": "正在整理案件信息"' in events[0]
    assert '"reply_version": 5' in events[1]
    assert '"cache_hit": true' in events[1]


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
