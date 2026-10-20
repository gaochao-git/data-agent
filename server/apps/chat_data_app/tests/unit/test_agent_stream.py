import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import httpx
import openai
import pytest
from langchain_core.messages import AIMessageChunk, HumanMessageChunk, ToolMessageChunk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app import agent_stream as agent_stream_module
from apps.chat_data_app.agent.context.context_budget import ContextInputLimitExceededError
from apps.chat_data_app.agent.run.completion_options import normalize_enable_thinking
from apps.chat_data_app.agent.tools import data_analyzing as agent_sql_tools
from apps.chat_data_app.agent_run_support import (
    _prepare_conversation_workspace_sync,
    prepare_conversation_workspace,
)
from apps.chat_data_app.agent_stream import (
    AgentRunStreamFailedError,
    _append_reasoning_segment,
    _coerce_tool_args,
    _extract_reasoning_content,
    _finish_running_tool,
    _format_reasoning_for_display,
    _format_sse,
    _format_thinking_event,
    _format_tool_event,
    _generate_chat_data_stream,
    _is_tool_result_error,
    _model_failure_user_message,
    _parse_tool_args_buffer,
    _register_running_tool,
    _resolve_active_tool_call_id,
    _validate_workspace_file_selections,
    sql_completion_handler,
)
from apps.chat_data_app.services.semantic.run.semantic_conversation_context import SemanticConversationContext
from utils.builtin_skills import DATA_ANALYZING_SKILL


def _semantic_context_from_request(**kwargs) -> SemanticConversationContext:
    datasource_id = str(kwargs.get("datasource_id") or "").strip() or None
    semantic_config_id = str(kwargs.get("semantic_config_id") or "").strip() or None
    selected_ids = list(
        dict.fromkeys(
            str(member_id).strip() for member_id in kwargs.get("member_datasource_ids") or [] if str(member_id).strip()
        )
    )
    semantic_config = (
        {"id": semantic_config_id, "name": semantic_config_id, "metrics": [], "dimensions": []}
        if semantic_config_id
        else None
    )
    targets = [
        {"member_datasource_id": member_id, "member_name": member_id, "org_code": member_id}
        for member_id in selected_ids
    ]
    query = str(kwargs.get("user_query") or "")
    matched_ids = selected_ids if "所有机构" in query or "全部机构" in query else []
    return SemanticConversationContext(
        datasource_id=datasource_id,
        datasource_meta={"query_context": ""} if datasource_id else None,
        semantic_config_id=semantic_config_id,
        semantic_config=semantic_config,
        execution_targets=targets,
        available_member_datasource_ids=selected_ids,
        matched_member_datasource_ids=matched_ids,
        selected_member_datasource_ids=selected_ids,
    )


def _override_semantic_context(monkeypatch, **overrides):
    async def prepare_context(**kwargs):
        context = _semantic_context_from_request(**kwargs)
        values = {key: value(context, kwargs) if callable(value) else value for key, value in overrides.items()}
        return replace(context, **values)

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prepare_semantic_conversation_context",
        prepare_context,
    )


@pytest.fixture(autouse=True)
def _stub_semantic_conversation_context(monkeypatch):
    async def noop_event_write(**_kwargs):
        return None

    async def prepare_context(**kwargs):
        return _semantic_context_from_request(**kwargs)

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prepare_semantic_conversation_context",
        prepare_context,
    )

    async def resolve_datasource_binding(_conversation_id, _user_id, requested_datasource_id):
        return requested_datasource_id

    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.conversation.resolve_datasource_binding",
        resolve_datasource_binding,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.conversation_event_service.persist_user_message",
        noop_event_write,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.conversation_event_service.persist_turn",
        noop_event_write,
    )

    # 预物化不是这些用例的关注点：默认桩为“本轮无模型需物化”，
    # 需要验证物化行为的用例自行覆盖。
    async def noop_prematerialize(**_kwargs):
        return {"total": 0, "failed_ids": []}

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prematerialize_enabled_semantic_models",
        noop_prematerialize,
    )


def _tool_event_contents(events: list[str]) -> list[str]:
    """解析 SSE 工具事件中的原始工具结果。"""
    contents = []
    for event in events:
        if not event.startswith("event: tool\n"):
            continue
        payload = json.loads(event.split("data: ", 1)[1].strip())
        contents.append(str(payload.get("content") or ""))
    return contents


def test_resolve_active_tool_call_id_prefers_chunk_id():
    running = ["tool_a", "tool_b"]
    assert _resolve_active_tool_call_id("tool_c", "tool_b", running) == "tool_c"


def test_resolve_active_tool_call_id_uses_current_active_when_chunk_id_missing():
    running = ["tool_a", "tool_b"]
    assert _resolve_active_tool_call_id(None, "tool_b", running) == "tool_b"


def test_finish_running_tool_falls_back_to_previous_running_tool():
    running = []
    _register_running_tool(running, "tool_a")
    _register_running_tool(running, "tool_b")

    next_active = _finish_running_tool(running, "tool_b", "tool_b")

    assert running == ["tool_a"]
    assert next_active == "tool_a"


def test_finish_running_tool_clears_active_when_last_tool_finishes():
    running = []
    _register_running_tool(running, "tool_a")

    next_active = _finish_running_tool(running, "tool_a", "tool_a")

    assert running == []
    assert next_active is None


def test_format_sse_serializes_dict_as_event_payload():
    text = _format_sse("message", {"content": "你好"})

    assert text.startswith("event: message\n")
    assert 'data: {"content": "你好"}' in text
    assert text.endswith("\n\n")


def test_format_tool_event_uses_result_as_content():
    text = _format_tool_event(
        tool_name="data_analyzing_tool",
        tool_call_id="call_1",
        status="completed",
        args={"dsl": {"metrics": ["订单数"]}},
        result="执行完成",
        timestamp="2026-06-02 10:00:00",
    )

    assert "event: tool" in text
    assert '"tool_name": "data_analyzing_tool"' in text
    assert '"content": "执行完成"' in text
    assert '"timestamp": "2026-06-02 10:00:00"' in text


def test_reasoning_and_thinking_helper_branches():
    assert _extract_reasoning_content(SimpleNamespace()) == ""
    assert _extract_reasoning_content(SimpleNamespace(additional_kwargs={"reasoning_content": "思考A"})) == "思考A"
    assert _extract_reasoning_content(SimpleNamespace(response_metadata={"reasoning": "思考B"})) == "思考B"
    assert (
        _extract_reasoning_content(
            SimpleNamespace(additional_kwargs={}),
            {"response_metadata": {"reasoning_text": "思考C"}},
        )
        == "思考C"
    )
    assert (
        _extract_reasoning_content(
            SimpleNamespace(additional_kwargs={}),
            {"reasoning_content": "思考D"},
        )
        == "思考D"
    )
    assert _format_thinking_event(SimpleNamespace(additional_kwargs={})) is None
    thinking_event = _format_thinking_event(SimpleNamespace(additional_kwargs={"reasoning_content": "正在分析"}))
    assert thinking_event.startswith("event: thinking")
    assert "正在分析" in thinking_event
    tool_thinking_event = _format_thinking_event(
        SimpleNamespace(additional_kwargs={"reasoning_content": "调用 data_analyzing_tool"})
    )
    assert "数据分析" in tool_thinking_event
    assert "data_analyzing_tool" not in tool_thinking_event
    assert _format_reasoning_for_display("调用 data_analyzing_tool") == "调用 数据分析"
    assert _format_reasoning_for_display("调用 get_current_time") == "调用 获取当前时间"
    # 短工具名（task）只按词边界替换，不能命中 subtask / task_id / tasks 等长词的子串
    assert _format_reasoning_for_display("派发 task 处理") == "派发 派发子智能体 处理"
    assert _format_reasoning_for_display("拆成 3 个 subtask 分别处理") == "拆成 3 个 subtask 分别处理"
    assert _format_reasoning_for_display("先记录 task_id，再调用工具") == "先记录 task_id，再调用工具"
    assert _format_reasoning_for_display("这些 tasks 是独立的") == "这些 tasks 是独立的"

    segments = []
    _append_reasoning_segment(segments, "", 0)
    assert segments == []
    _append_reasoning_segment(segments, "a", 0)
    _append_reasoning_segment(segments, "b", 0)
    _append_reasoning_segment(segments, "c", 1)
    _append_reasoning_segment(segments, "调用 data_analyzing_tool", 1)
    assert segments == [
        {"id": "reasoning-0", "content": "ab", "tool_call_count": 0},
        {"id": "reasoning-1", "content": "c调用 数据分析", "tool_call_count": 1},
    ]


def test_normalize_enable_thinking_branches():
    assert normalize_enable_thinking(None) is None
    assert normalize_enable_thinking(True) is True
    assert normalize_enable_thinking(False) is False
    assert normalize_enable_thinking(1) is True
    assert normalize_enable_thinking(0) is False
    assert normalize_enable_thinking(" true ") is True
    assert normalize_enable_thinking("YES") is True
    assert normalize_enable_thinking("off") is False
    assert normalize_enable_thinking("no") is False
    assert normalize_enable_thinking("unknown") is None
    assert normalize_enable_thinking(object()) is None


def test_validate_workspace_file_selections_filters_invalid(tmp_path):
    root = tmp_path / "user-data"
    (root / "uploads").mkdir(parents=True)
    (root / "outputs" / "query_result").mkdir(parents=True)
    (root / "uploads" / "a.pdf").write_text("data", encoding="utf-8")
    (root / "outputs" / "query_result" / "r.json").write_text("{}", encoding="utf-8")
    root_str = str(root)

    # 合法文件保留；不存在/系统结果区/越界路径丢弃；按序去重
    assert _validate_workspace_file_selections(
        [
            "uploads/a.pdf",
            "uploads/missing.pdf",
            "outputs/query_result/r.json",
            "uploads/a.pdf",
            "../../etc/passwd",
            "/abs/path.pdf",
        ],
        root_str,
    ) == ["uploads/a.pdf"]


@pytest.mark.asyncio
async def test_resolve_inbox_message_references_resolves_refs(tmp_path, monkeypatch):
    """排队消息采纳点：引用校验后追加引用短句、附件解析为占位行与 vision 标记。"""

    from langchain_core.messages import HumanMessage

    from apps.chat_data_app import agent_stream

    root = tmp_path / "user-data"
    (root / "uploads").mkdir(parents=True)
    (root / "uploads" / "a.pdf").write_text("data", encoding="utf-8")
    root_str = str(root)

    # 无随行元数据的消息原样返回（快速路径，不触任何 IO）
    plain = HumanMessage(id="m0", content="纯文本", additional_kwargs={"timestamp": "t"})
    resolved = await agent_stream._resolve_inbox_message_references(
        plain,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    assert resolved is plain

    # 引用 + 附件：短句/占位行进正文，kwargs 留痕与普通发送同构
    async def fake_resolve_file_attachments(file_ids, *, user_id, thread_id):
        assert file_ids == ["file-1"]
        assert user_id == "u1" and thread_id == "conv-1"
        return [
            {
                "file_id": "file-1",
                "original_filename": "b.md",
                "file_type": "md",
                "file_size": 3,
                "virtual_path": "/mnt/user-data/uploads/b.md",
            }
        ]

    monkeypatch.setattr(agent_stream, "_resolve_file_attachments", fake_resolve_file_attachments)
    message = HumanMessage(
        id="m1",
        content="排队消息",
        additional_kwargs={
            "timestamp": "t",
            "file_selections": ["uploads/a.pdf", "uploads/missing.pdf", "../../etc/passwd"],
            "file_ids": ["file-1"],
        },
    )
    resolved = await agent_stream._resolve_inbox_message_references(
        message,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    # 正文 = 原文 + 引用短句（只含校验通过的）+ 附件占位行，与普通发送同构
    assert resolved.content == (
        "排队消息\n\n[引用文件：/mnt/user-data/uploads/a.pdf]\n[attachment] /mnt/user-data/uploads/b.md"
    )
    assert resolved.additional_kwargs["file_selections"] == ["uploads/a.pdf"]
    # md 非图片：不打 vision 标记
    assert "vision_attachments" not in resolved.additional_kwargs
    assert resolved.id == "m1"

    # 图片附件：vision 标记进 kwargs，供 ImageInjectionMiddleware 装载
    async def fake_image_attachments(file_ids, *, user_id, thread_id):
        return [
            {
                "file_id": "file-img",
                "original_filename": "shot.png",
                "file_type": "png",
                "file_size": 10,
                "virtual_path": "/mnt/user-data/uploads/shot.png",
            }
        ]

    monkeypatch.setattr(agent_stream, "_resolve_file_attachments", fake_image_attachments)
    image_message = HumanMessage(
        id="m2",
        content="看图",
        additional_kwargs={"file_ids": ["file-img"]},
    )
    resolved = await agent_stream._resolve_inbox_message_references(
        image_message,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    assert resolved.additional_kwargs["vision_attachments"] == ["/mnt/user-data/uploads/shot.png"]
    assert "[attachment] /mnt/user-data/uploads/shot.png" in resolved.content

    # 划选引用：纯文本片段无外部资源可校验，重解析归一后留痕 + 标记块进正文
    quote_only = HumanMessage(
        id="m3",
        content="这个结论怎么得出的",
        additional_kwargs={
            "timestamp": "t",
            "quoted_snippets": [{"text": " 户表与订单表通过用户ID关联（一对多） "}, {"other": 1}],
        },
    )
    resolved = await agent_stream._resolve_inbox_message_references(
        quote_only,
        workspace_root=root_str,
        user_id="u1",
        thread_id="conv-1",
    )
    assert "[引用助手回复片段开始]\n户表与订单表通过用户ID关联（一对多）\n[引用助手回复片段结束]" in resolved.content
    assert resolved.additional_kwargs["quoted_snippets"] == [{"text": "户表与订单表通过用户ID关联（一对多）"}]


def test_materialize_conversation_semantic_models_failure_is_non_blocking(monkeypatch):
    """建会话物化 helper 的契约：失败吞异常返回 total=0，不阻断会话创建。"""
    import asyncio

    from apps.chat_data_app import agent_run_support

    # datasource_id 缺省：短路，不触任何 IO
    assert asyncio.run(
        agent_run_support.materialize_conversation_semantic_models(
            "conv_x",
            user_id="u1",
            datasource_id="",
        )
    ) == {"total": 0, "failed_ids": []}

    # 前置步骤（读会话日期）失败：吞异常
    def date_boom(*args, **kwargs):
        raise RuntimeError("date boom")

    monkeypatch.setattr(agent_run_support, "get_conversation_created_date", date_boom)
    assert asyncio.run(
        agent_run_support.materialize_conversation_semantic_models(
            "conv_x",
            user_id="u1",
            datasource_id="ds_1",
        )
    ) == {"total": 0, "failed_ids": []}

    # 正常路径：透传 prematerialize 结果
    async def fake_created_date(conversation_id, user_id=None):
        return None

    async def fake_prepare(conversation_id, user_id, created_date=None):
        return {"root": "/tmp/whatever"}

    class _FakeRuntime:
        def __init__(self, *, workspace_root, user_id):
            self.workspace_root = workspace_root
            self.user_id = user_id

    class _FakeManager:
        async def get_datasource_meta_async(self, datasource_id):
            return {"source_type": "mysql"}

    async def fake_prematerialize(**kwargs):
        return {"total": 2, "failed_ids": []}

    monkeypatch.setattr(agent_run_support, "get_conversation_created_date", fake_created_date)
    monkeypatch.setattr(agent_run_support, "prepare_conversation_workspace", fake_prepare)
    monkeypatch.setattr(agent_run_support, "ConversationWorkspaceRuntime", _FakeRuntime)
    monkeypatch.setattr(agent_run_support, "get_datasource_manager", lambda: _FakeManager())
    monkeypatch.setattr(agent_run_support, "prematerialize_enabled_semantic_models", fake_prematerialize)

    assert asyncio.run(
        agent_run_support.materialize_conversation_semantic_models(
            "conv_x",
            user_id="u1",
            datasource_id="ds_1",
        )
    ) == {"total": 2, "failed_ids": []}


def test_parse_tool_args_buffer_falls_back_on_invalid_json():
    fallback = {"sql": "select 1"}

    assert _parse_tool_args_buffer("", fallback) == fallback
    assert _parse_tool_args_buffer('{"sql":"select 2"}', fallback) == {"sql": "select 2"}
    assert _parse_tool_args_buffer('["not_dict"]', fallback) == fallback
    assert _parse_tool_args_buffer("{bad json", fallback) == fallback


def test_coerce_tool_args_handles_string_and_double_encoded_forms():
    """内网 Qwen3.6 经 vLLM 返回的 args 是 JSON 字符串甚至双层编码，须规整为 dict。"""

    assert _coerce_tool_args({"a": 1}) == {"a": 1}
    assert _coerce_tool_args('{"calculations": [{"expression": "1 + 1"}]}') == {
        "calculations": [{"expression": "1 + 1"}]
    }
    double_encoded = json.dumps(json.dumps({"a": 1}))
    assert _coerce_tool_args(double_encoded) == {"a": 1}
    assert _coerce_tool_args("raw-string") is None
    assert _coerce_tool_args('["not_dict"]') is None
    assert _coerce_tool_args("{bad json") is None
    assert _coerce_tool_args("") is None
    assert _coerce_tool_args(None) is None
    assert _coerce_tool_args(42) is None


def test_parse_tool_args_buffer_unwraps_double_encoded_json():
    fallback = {"sql": "select 1"}

    assert _parse_tool_args_buffer(json.dumps(json.dumps({"sql": "select 3"})), fallback) == {"sql": "select 3"}
    assert _parse_tool_args_buffer("raw-string", fallback) == fallback


def test_is_tool_result_error_ignores_plain_text_errors():
    # 成败只认显式协议位（native status / query_result envelope），不再做任何文本匹配
    assert _is_tool_result_error("执行失败: 表不存在") is False
    assert _is_tool_result_error("error: syntax error") is False
    assert _is_tool_result_error('{"type":"query_result","status":"failed"}') is True
    assert _is_tool_result_error('{"type":"query_result","status":"cancelled"}') is True
    assert _is_tool_result_error('{"type":"query_result","status":"succeeded"}') is False
    assert _is_tool_result_error('[dashboard_write] {"success": false}') is False
    assert _is_tool_result_error("执行完成") is False


def test_is_tool_result_error_prefers_native_status():
    # 结构化 status="error" 最优先：无论正文长什么样都判失败
    assert _is_tool_result_error("查询完成", "error") is True
    assert _is_tool_result_error('{"type":"query_result","status":"succeeded"}', "error") is True
    # 成败只认显式协议位：status="success" 或缺省时一律不算失败，即使正文含"执行失败"字样
    assert _is_tool_result_error("处理日志：步骤一执行失败,已自动重试成功", "success") is False
    assert _is_tool_result_error("bash_run 执行失败: exit 1", "success") is False
    # query_result envelope 的 failed/cancelled 是显式失败信号，优先于 success 文本位
    assert _is_tool_result_error('{"type":"query_result","status":"failed"}', "success") is True
    assert _is_tool_result_error('{"type":"query_result","status":"cancelled"}', "success") is True
    # 缺 status 且无 envelope 信号时一律视为成功
    assert _is_tool_result_error("officecli_run 执行失败: 超时") is False
    assert _is_tool_result_error("正常结果") is False


def _make_openai_status_error(exc_cls, status_code: int, body: dict | None = None) -> Exception:
    request = httpx.Request("POST", "https://model.example.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return exc_cls("model service error", response=response, body=body)


def test_resolve_client_timestamp_ms():
    """客户端点击时刻校验：合法采信、非法/超界回退服务端接收章。"""
    now = 1_000_000_000_000
    resolve = agent_stream_module._resolve_client_timestamp_ms
    # 合法：1 分钟前点击 → 采信（点击 → 请求到达的传输+建会话盲区计入等待）
    assert resolve(now - 60_000, now_ms=now) == now - 60_000
    # 客户端钟偏快（未来）→ 钳到服务端 now
    assert resolve(now + 5_000, now_ms=now) == now
    # 钟偏超界（> 1 小时）→ 回退服务端章
    assert resolve(now - 3_600_001, now_ms=now) == now
    # 非法：None / 0 / 负数 → 回退服务端章
    assert resolve(None, now_ms=now) == now
    assert resolve(0, now_ms=now) == now
    assert resolve(-1, now_ms=now) == now


def test_build_user_message_timestamp_override():
    """用户消息时间戳覆盖语义：计时起点章（receipt）传入时覆盖默认构建时刻章。

    气泡显示、历史回放、harness 首 token/总耗时三者同锚（客户端点击时刻优先，
    兜底服务端接收时刻）。
    """
    # 未传覆盖 → 默认构建时刻章（YYYY-MM-DD HH:MM:SS）
    default_message = agent_stream_module._build_user_message("你好")
    assert default_message.additional_kwargs["timestamp"] == agent_stream_module._get_timestamp()
    # 接收章经 message_kwargs 传入（_resolve_agent_turn_inputs 的覆盖路径）→ 生效
    receipt_message = agent_stream_module._build_user_message("你好", timestamp="2026-10-18 08:47:39")
    assert receipt_message.additional_kwargs["timestamp"] == "2026-10-18 08:47:39"
    # 空值不覆盖（extra_kwargs 过滤 falsy）→ 保留默认章
    fallback_message = agent_stream_module._build_user_message("你好", timestamp="")
    assert fallback_message.additional_kwargs["timestamp"] == agent_stream_module._get_timestamp()


def test_model_failure_user_message_maps_friendly_text():
    # 429 限流/过载（今天 ServerOverloaded 场景）
    rate_error = _make_openai_status_error(openai.RateLimitError, 429)
    assert "繁忙" in _model_failure_user_message(rate_error)
    # 401 认证失败（key 配置错误场景）
    auth_error = _make_openai_status_error(openai.AuthenticationError, 401)
    assert "认证失败" in _model_failure_user_message(auth_error)
    # 连接中断（流中途被上游断开场景）
    conn_error = openai.APIConnectionError(
        message="Connection error.",
        request=httpx.Request("POST", "https://model.example.com/v1/chat/completions"),
    )
    assert "连接中断" in _model_failure_user_message(conn_error)
    # 读超时是 APIConnectionError 子类，须优先命中超时文案
    timeout_error = openai.APITimeoutError("Request timed out")
    assert "超时" in _model_failure_user_message(timeout_error)
    # 流式分块超时（TCP 连接存活但长时间无内容，langchain-openai 看门狗中断）：
    # StreamChunkTimeoutError 是内置 TimeoutError 子类，须命中专用文案而非通用兜底
    from langchain_openai.chat_models._client_utils import StreamChunkTimeoutError

    stream_chunk_timeout_error = StreamChunkTimeoutError(120.0, model_name="stealth/union-alpha", chunks_received=0)
    assert "长时间未返回内容" in _model_failure_user_message(stream_chunk_timeout_error)
    # oMLX 内存护栏拒绝（prefill_memory_exceeded，400）：重试无效，须命中专用文案而非通用兜底
    omlx_body = {
        "error": {
            "message": "oMLX prefill memory guard rejected this prompt",
            "type": "invalid_request_error",
            "code": "prefill_memory_exceeded",
            "omlx_code": "prefill_memory_exceeded",
        },
        "type": "error",
    }
    omlx_error = _make_openai_status_error(openai.BadRequestError, 400, body=omlx_body)
    assert "内存上限" in _model_failure_user_message(omlx_error)
    # body 形状变化时按 message 关键字兜底（langchain 侧包装异常可能丢 body）
    wrapped_error = _make_openai_status_error(openai.BadRequestError, 400)
    plain = httpx.Response(400, request=httpx.Request("POST", "https://model.example.com"))
    assert "内存上限" in _model_failure_user_message(
        type(wrapped_error)("Error code: 400 - prefill_memory_exceeded rejected", response=plain, body=None)
    )
    # 其他异常一律兜底通用文案，不透出原始错误
    assert _model_failure_user_message(ValueError("raw internal detail")) == "智能问数执行失败，请稍后重试。"


def test_model_failure_user_message_passthrough_provider_message():
    """非枚举 APIStatusError（402 余额不足等）：透出服务商 message（提取+护栏），取不到回退兜底。"""
    # 402：dict body 取 error.message，剥离 request_id 噪音，带固定中文前缀
    balance_body = {
        "error": {"message": "Insufficient Balance (request_id: 245dcc9c)", "code": "invalid_request_error"}
    }
    err = _make_openai_status_error(openai.APIStatusError, 402, body=balance_body)
    assert _model_failure_user_message(err) == "模型服务返回错误：Insufficient Balance"
    # body 为字符串（SDK 解析失败场景）取原文
    request = httpx.Request("POST", "https://model.example.com/v1/chat/completions")
    str_body_error = openai.APIStatusError(
        "Error code: 402 - balance low", response=httpx.Response(402, request=request), body="balance low"
    )
    assert _model_failure_user_message(str_body_error) == "模型服务返回错误：balance low"
    # body 缺失 / message 为空 / 超长（>200）→ 回退兜底文案
    no_body = _make_openai_status_error(openai.APIStatusError, 402)
    assert _model_failure_user_message(no_body) == "智能问数执行失败，请稍后重试。"
    empty_body = {"error": {"message": "  "}}
    assert _model_failure_user_message(_make_openai_status_error(openai.APIStatusError, 402, body=empty_body)) == (
        "智能问数执行失败，请稍后重试。"
    )
    long_body = {"error": {"message": "x" * 201}}
    assert _model_failure_user_message(_make_openai_status_error(openai.APIStatusError, 402, body=long_body)) == (
        "智能问数执行失败，请稍后重试。"
    )


def test_prepare_conversation_workspace_sync_and_async(monkeypatch):
    import asyncio

    calls = {}

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.ensure_conversation_workspace",
        lambda conversation_id, user_id, *, created_date=None: calls.setdefault(
            "workspace", {"root": f"/tmp/{user_id}/{created_date}/{conversation_id}"}
        ),
    )

    paths = _prepare_conversation_workspace_sync("conv_ws", "user_a", created_date="2026-09-14")
    assert paths["root"] == "/tmp/user_a/2026-09-14/conv_ws"

    async_paths = asyncio.run(prepare_conversation_workspace("conv_ws", "user_a"))
    assert async_paths == paths


def test_agent_sql_normalize_member_datasource_ids_dedupes_and_strips():
    assert agent_sql_tools.normalize_member_datasource_ids([" m1 ", "", None, "m1", "m2"]) == ["m1", "m2"]


def _FakeMessageChunk(
    *,
    content="",
    type_="ai",
    tool_calls=None,
    tool_call_chunks=None,
    tool_call_id="",
    name="",
):
    """测试工厂：返回真实 LangChain 消息 Chunk，不伪造 type 字段。"""
    if type_ == "human":
        return HumanMessageChunk(content=content)
    if type_ == "tool":
        return ToolMessageChunk(content=content, tool_call_id=tool_call_id, name=name or None)
    normalized_tool_calls = [{**tool_call, "args": tool_call.get("args") or {}} for tool_call in tool_calls or []]
    return AIMessageChunk(
        content=content,
        tool_calls=normalized_tool_calls,
        tool_call_chunks=tool_call_chunks or [],
    )


class _FakeGraph:
    def __init__(self, events=None, state=None, raise_on_stream=False):
        self.events = events or []
        self.state = state or SimpleNamespace(tasks=[], values={})
        self.raise_on_stream = raise_on_stream
        self.initial_state = None

    async def aget_state(self, config):
        return self.state

    async def astream(self, initial_state, config=None, stream_mode=None):
        self.initial_state = initial_state
        if self.raise_on_stream:
            raise RuntimeError("graph failed")
        for event in self.events:
            yield event


@asynccontextmanager
async def _fake_checkpointer():
    yield object()


async def _collect_async_iter(async_iterable):
    return [item async for item in async_iterable]


async def _collect_async_iter_until_failure(async_iterable):
    """收集事件并捕获 AgentRunStreamFailedError，返回 (events, failure)。

    failure 非 None 表示生成器按契约把失败异常抛给了调用方（Runtime）。
    """
    events = []
    failure = None
    try:
        async for item in async_iterable:
            events.append(item)
    except AgentRunStreamFailedError as exc:
        failure = exc
    return events, failure


def test_generate_chat_data_stream_enters_agent_when_semantic_config_not_selected(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="5+5=10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )
    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", create_agent)
    _override_semantic_context(monkeypatch)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_auto_file_grep",
                    user_query="5+5等于多少",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    joined = "".join(events)
    assert "5+5=10" in joined
    # 语义模型段已整体退出系统提示词（未选中/选中都不再注入；选中走 @ 引用短句）
    assert "## 当前语义模型" not in captured_agent_kwargs["model_invocation"].system_prompt
    # 系统提示词不再枚举模型目录、不重复检索步骤，指向数据分析技能
    assert "当前可见指标集目录" not in captured_agent_kwargs["model_invocation"].system_prompt
    assert DATA_ANALYZING_SKILL in captured_agent_kwargs["model_invocation"].system_prompt
    human_message = fake_graph.initial_state["messages"][0]
    assert "skill_names" not in human_message.additional_kwargs


def test_generate_chat_data_stream_injects_note_on_partial_prematerialize(monkeypatch):
    """部分模型物化失败不终止本轮，但缺失清单必须注入上下文，让模型能区分“真没有”与“没物化”。"""
    import asyncio

    captured_agent_kwargs = {}

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return _FakeGraph(
            events=[_format_sse("message", {"content": "ok"})],
            state=SimpleNamespace(tasks=[], values={}),
        )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", create_agent)

    async def partial_prematerialize(**_kwargs):
        return {"total": 2, "failed_ids": ["sc_broken"]}

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prematerialize_enabled_semantic_models",
        partial_prematerialize,
    )
    _override_semantic_context(monkeypatch)

    asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_partial_prematerialize",
                    user_query="查询订单数",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    system_prompt = captured_agent_kwargs["model_invocation"].system_prompt
    assert "本轮语义目录物化不完整" in system_prompt
    assert "sc_broken" in system_prompt
    assert "不要判定为数据缺失" in system_prompt
    # 部分失败不终止本轮：agent 正常进入循环
    assert captured_agent_kwargs


def test_generate_chat_data_stream_aborts_when_semantic_prematerialize_fails(monkeypatch):
    """语义目录物化失败属基础设施故障：必须终止本轮，不能带着空目录进 agent 循环。"""
    import asyncio

    agent_calls = {"count": 0}

    def create_agent(_runtime_options, **_kwargs):
        agent_calls["count"] += 1
        return _FakeGraph(events=[], state=SimpleNamespace(tasks=[], values={}))

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", create_agent)

    async def failing_prematerialize(**_kwargs):
        raise RuntimeError("workspace 写盘失败")

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prematerialize_enabled_semantic_models",
        failing_prematerialize,
    )

    events, failure = asyncio.run(
        _collect_async_iter_until_failure(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_prematerialize_failed",
                    user_query="查询订单数",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    assert failure is not None
    assert "语义模型目录初始化失败" in "".join(events)
    assert agent_calls["count"] == 0


def test_generate_chat_data_stream_uses_runtime_snapshot_for_system_prompt(monkeypatch, tmp_path):
    import asyncio

    captured_agent_kwargs = {}
    snapshot_dir = tmp_path / "runtime-skills"
    runtime_skill = snapshot_dir / "runtime-skill"
    runtime_skill.mkdir(parents=True)
    (runtime_skill / "SKILL.md").write_text(
        "---\nname: stale-management-name\ntitle: Runtime skill\ndescription: Only in this snapshot\n---\n# skill\n",
        encoding="utf-8",
    )
    workspace_root = tmp_path / "threads" / "u1" / "conv_snapshot" / "user-data"

    async def fake_prepare_workspace(*_args, **_kwargs):
        workspace_root.mkdir(parents=True)
        return {"root": str(workspace_root)}

    async def fake_build_snapshot(user_id, conversation_id, *, target_dir=None):
        assert (user_id, conversation_id) == ("u1", "conv_snapshot")
        return snapshot_dir

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )
    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_run_support.prepare_conversation_workspace", fake_prepare_workspace)
    monkeypatch.setattr("apps.chat_data_app.agent_run_support.build_runtime_skill_snapshot_async", fake_build_snapshot)

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", create_agent)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    thread_id="conv_snapshot",
                    user_query="查询订单",
                    datasource_id="ds_a",
                    user_id="u1",
                )
            )
        )
    )

    assert "完成" in "".join(events)
    system_prompt = captured_agent_kwargs["model_invocation"].system_prompt
    assert "<name>runtime-skill</name>" in system_prompt
    assert "<title>Runtime skill</title>" in system_prompt
    assert "stale-management-name" not in system_prompt
    assert captured_agent_kwargs["workspace_runtime"].available_skill_names == ["runtime-skill"]


def test_generate_chat_data_stream_streams_semantic_retrieval_tool_result(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_pay"],
        "semantic_configs": {
            "cfg_pay": {
                "id": "cfg_pay",
                "name": "支付汇总",
                "metrics": [{"name": "交易金额", "expression": "SUM(pay.amount)"}],
            },
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (
                _FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "file_grep"}]),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="",
                    tool_call_chunks=[{"id": "call_select", "name": "file_grep", "args": '{"query":"查交易金额"}'}],
                ),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="[file_grep]\n" + json.dumps(payload, ensure_ascii=False),
                    type_="tool",
                    tool_call_id="call_select",
                    name="file_grep",
                ),
                {"langgraph_node": "tools"},
            ),
            (_FakeMessageChunk(content="已查询支付数据"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_select_tool_event",
                    user_query="按机构查询交易金额",
                    datasource_id="ds_a",
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: tool" in joined
    assert "[file_grep]" in _tool_event_contents(events)[-1]
    assert '"semantic_config_ids": ["cfg_pay"]' in _tool_event_contents(events)[-1]
    assert '"expression": "SUM(pay.amount)"' in _tool_event_contents(events)[-1]
    assert '"tool_name": "file_grep"' in joined
    assert "event: semantic_config_selected" not in joined
    assert "event: semantic_config_select_required" not in joined
    assert "已查询支付数据" in joined


def test_generate_chat_data_stream_passes_valid_file_selections(monkeypatch, tmp_path):
    """@ 引用文件：白名单校验通过后随 Human 消息留痕，正文追加引用短句。"""
    import asyncio

    workspace_root = tmp_path / "user-data"
    (workspace_root / "uploads").mkdir(parents=True)
    (workspace_root / "uploads" / "a.pdf").write_text("data", encoding="utf-8")
    (workspace_root / "outputs" / "query_result").mkdir(parents=True)
    (workspace_root / "outputs" / "query_result" / "r.json").write_text("{}", encoding="utf-8")

    async def fake_prepare_workspace(*_args, **_kwargs):
        return {"root": str(workspace_root)}

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="商品数量为 10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_run_support.prepare_conversation_workspace", fake_prepare_workspace)
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_file_selections",
                    user_query="分析这个文件",
                    datasource_id="ds_a",
                    file_selections=["uploads/a.pdf", "uploads/missing.pdf", "outputs/query_result/r.json"],
                )
            )
        )
    )

    joined = "".join(events)
    assert "商品数量为 10" in joined
    human_message = fake_graph.initial_state["messages"][0]
    # 只有白名单内真实存在的文件进入留痕；正文追加模型可见的引用短句
    assert human_message.additional_kwargs["file_selections"] == ["uploads/a.pdf"]
    assert human_message.content.endswith("[引用文件：/mnt/user-data/uploads/a.pdf]")
    assert "skill_names" not in human_message.additional_kwargs


def test_generate_chat_data_stream_passes_quoted_snippets_to_inner(monkeypatch, tmp_path):
    """划选引用：quoted_snippets 必须经生成器 re-wrap 透传到内层。

    回归防线：_generate_chat_data_stream 拆包重建 ChatDataStreamRequest 时若漏带
    quoted_snippets，内层 parse 永远拿到 None——模型看不到标记块、
    additional_kwargs 不留痕（历史回放 chip 消失）。本用例从公开生成器入口
    驱动到消息断言，专防这类 wiring 断点。
    """
    import asyncio

    workspace_root = tmp_path / "user-data"
    (workspace_root / "uploads").mkdir(parents=True)
    (workspace_root / "outputs" / "query_result").mkdir(parents=True)
    (workspace_root / "outputs" / "query_result" / "r.json").write_text("{}", encoding="utf-8")

    async def fake_prepare_workspace(*_args, **_kwargs):
        return {"root": str(workspace_root)}

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已结合引用片段回答"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_run_support.prepare_conversation_workspace", fake_prepare_workspace)
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_quoted_snippets",
                    user_query="这个结论怎么得出的",
                    datasource_id="ds_a",
                    quoted_snippets=[{"text": "户表与订单表通过用户ID关联（一对多）"}],
                )
            )
        )
    )

    joined = "".join(events)
    assert "已结合引用片段回答" in joined
    human_message = fake_graph.initial_state["messages"][0]
    # 模型可见标记块进入正文；additional_kwargs 留痕供事件表持久化（历史回放渲染 chip）
    assert (
        "[引用助手回复片段开始]\n户表与订单表通过用户ID关联（一对多）\n[引用助手回复片段结束]" in human_message.content
    )
    assert human_message.additional_kwargs["quoted_snippets"] == [{"text": "户表与订单表通过用户ID关联（一对多）"}]


def test_generate_chat_data_stream_keeps_explicit_query_skill(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_query_skill_names",
                    user_query="查询金额",
                    datasource_id="ds_a",
                    skill_names=["custom-query-skill", "risk-query-skill"],
                )
            )
        )
    )

    assert "完成" in "".join(events)
    human_message = fake_graph.initial_state["messages"][0]
    assert human_message.additional_kwargs["skill_names"] == ["custom-query-skill", "risk-query-skill"]


def test_generate_chat_data_stream_streams_multiple_semantic_configs_as_tool_result(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_pay"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_pay": {"id": "cfg_pay", "name": "支付汇总"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (
                _FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "file_grep"}]),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="[file_grep]\n" + json.dumps(payload, ensure_ascii=False),
                    type_="tool",
                    tool_call_id="call_select",
                    name="file_grep",
                ),
                {"langgraph_node": "tools"},
            ),
            (_FakeMessageChunk(content="请选择指标集"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_multiple_file_grep_event",
                    user_query="查询金额",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: tool" in joined
    assert '"semantic_config_ids": ["cfg_order", "cfg_pay"]' in _tool_event_contents(events)[-1]
    assert "订单分析" in joined
    assert "支付汇总" in joined
    assert "event: semantic_config_selected" not in joined
    assert "event: semantic_config_select_required" not in joined


def test_generate_chat_data_stream_streams_retrieval_without_query_plan(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_user"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_user": {"id": "cfg_user", "name": "用户分析"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (
                _FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "file_grep"}]),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="[file_grep]\n" + json.dumps(payload, ensure_ascii=False),
                    type_="tool",
                    tool_call_id="call_select",
                    name="file_grep",
                ),
                {"langgraph_node": "tools"},
            ),
            (_FakeMessageChunk(content="已按多个指标集完成查询"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_retrieval_tool_event",
                    user_query="查询订单金额和用户数",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "query_plan" not in joined
    assert "已按多个指标集完成查询" in joined


def test_generate_chat_data_stream_streams_retrieval_without_cross_config_decision(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_user"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_user": {"id": "cfg_user", "name": "用户分析"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (
                _FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "file_grep"}]),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="[file_grep]\n" + json.dumps(payload, ensure_ascii=False),
                    type_="tool",
                    tool_call_id="call_select",
                    name="file_grep",
                ),
                {"langgraph_node": "tools"},
            ),
            (_FakeMessageChunk(content="这个问题需要跨指标集合并，当前不支持。"), {"langgraph_node": "model"}),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_cross_config_retrieval",
                    user_query="按用户等级关联订单金额",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "cross_config_required" not in joined
    assert "这个问题需要跨指标集合并，当前不支持。" in joined


def test_generate_chat_data_stream_uses_selected_config_without_precheck(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已进入当前指标集链路"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_selected_semantic_no_precheck",
                    user_query="查询明细",
                    datasource_id="ds_a",
                    semantic_config_id="cfg_order",
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: semantic_config_selected" not in joined
    assert "已进入当前指标集链路" in joined
    assert "file_selections" not in fake_graph.initial_state["messages"][0].additional_kwargs


def test_generate_chat_data_stream_keeps_supported_selected_config(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已查询订单数据"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_keep_semantic",
                    user_query="查询订单金额",
                    datasource_id="ds_a",
                    semantic_config_id="cfg_order",
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: semantic_config_selected" not in joined
    assert "已查询订单数据" in joined
    assert "file_selections" not in fake_graph.initial_state["messages"][0].additional_kwargs


def test_generate_chat_data_stream_runs_after_user_selects_ambiguous_candidate(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="商品数量为 10"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_selected_ambiguous_semantic",
                    user_query="商品数量是多少",
                    datasource_id="ds_a",
                    semantic_config_id="cfg_order",
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: semantic_config_select_required" not in joined
    assert "商品数量为 10" in joined


def test_generate_chat_data_stream_does_not_preblock_selected_config(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="原始链路返回当前指标集不支持"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_no_preblock_semantic",
                    user_query="查询退款金额",
                    datasource_id="ds_a",
                    semantic_config_id="cfg_order",
                )
            )
        )
    )

    joined = "".join(events)
    assert "当前选择的指标集不支持该问题：缺少退款金额指标" not in joined
    assert "原始链路返回当前指标集不支持" in joined


def test_generate_chat_data_stream_does_not_block_multiple_semantic_configs(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_pay"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_pay": {"id": "cfg_pay", "name": "支付汇总"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (
                _FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "file_grep"}]),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="[file_grep]\n" + json.dumps(payload, ensure_ascii=False),
                    type_="tool",
                    tool_call_id="call_select",
                    name="file_grep",
                ),
                {"langgraph_node": "tools"},
            ),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_multiple_semantic",
                    user_query="查询金额",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "订单分析" in joined
    assert "支付汇总" in joined


def test_generate_chat_data_stream_does_not_block_cross_config_retrieval(monkeypatch):
    import asyncio

    payload = {
        "success": True,
        "semantic_config_ids": ["cfg_order", "cfg_product"],
        "semantic_configs": {
            "cfg_order": {"id": "cfg_order", "name": "订单分析"},
            "cfg_product": {"id": "cfg_product", "name": "商品分析"},
        },
    }
    fake_graph = _FakeGraph(
        events=[
            (
                _FakeMessageChunk(content="", tool_calls=[{"id": "call_select", "name": "file_grep"}]),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="[file_grep]\n" + json.dumps(payload, ensure_ascii=False),
                    type_="tool",
                    tool_call_id="call_select",
                    name="file_grep",
                ),
                {"langgraph_node": "tools"},
            ),
        ],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_cross_semantic_retrieval",
                    user_query="查询订单金额和商品库存",
                    datasource_id="ds_a",
                    semantic_config_id=None,
                )
            )
        )
    )

    joined = "".join(events)
    assert "event: tool" in joined
    assert "event: semantic_config_select_required" not in joined
    assert "订单分析" in joined
    assert "商品分析" in joined


def test_generate_chat_data_stream_emits_tool_message_usage_and_done(monkeypatch):
    import asyncio

    fake_graph = _FakeGraph(
        events=[
            (
                _FakeMessageChunk(content="", tool_calls=[{"id": "call_1", "name": "data_analyzing_tool"}]),
                {"langgraph_node": "model"},
            ),
            (
                _FakeMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"id": "call_1", "name": "data_analyzing_tool", "args": '{"dsl":{"metrics":["订单数"]}}'}
                    ],
                ),
                {"langgraph_node": "model"},
            ),
            (_FakeMessageChunk(content="执行完成", type_="tool", tool_call_id="call_1"), {"langgraph_node": "tools"}),
            (_FakeMessageChunk(content="已查询订单数据"), {"langgraph_node": "model"}),
            (_FakeMessageChunk(content="内部工具模型输出"), {"langgraph_node": "tools"}),
        ],
        state=SimpleNamespace(tasks=[], values={"token_usage": {"total_tokens": 12}}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    thread_id="conv_a",
                    user_query="查订单数",
                    datasource_id="ds_a",
                    user_id="user_a",
                    username="测试用户",
                )
            )
        )
    )

    joined = "".join(events)
    assert '"status": "running"' in joined
    assert '"accumulated_args": {"dsl": {"metrics": ["订单数"]}}' in joined
    assert "已查询订单数据" in joined
    assert "内部工具模型输出" not in joined
    assert "event: message_end" in joined
    assert '"usage": {"total_tokens": 12}' in joined
    assert '"timestamp":' in joined
    assert "event: done" not in joined  # done 由 Runtime 统一发布，生成器不再输出
    assert fake_graph.initial_state["execution_context"]["datasource_id"] == "ds_a"
    assert fake_graph.initial_state["operator_name"] == "测试用户"


def test_generate_chat_data_stream_projects_visible_members_from_shared_context(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", create_agent)

    asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    thread_id="conv_query_members",
                    user_query="查询所有机构订单数",
                    datasource_id="ds_a",
                    user_id="user_a",
                    member_datasource_ids=["m1"],
                    semantic_config_id="cfg_a",
                )
            )
        )
    )

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == ["m1"]
    assert fake_graph.initial_state["execution_context"]["matched_member_datasource_ids"] == ["m1"]


def test_generate_chat_data_stream_keeps_empty_member_scope_from_shared_context(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", create_agent)

    asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    thread_id="conv_query_members",
                    user_query="查询所有机构订单数",
                    datasource_id="ds_a",
                    user_id="user_a",
                    member_datasource_ids=[],
                    semantic_config_id="cfg_a",
                )
            )
        )
    )

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == []
    assert fake_graph.initial_state["execution_context"]["matched_member_datasource_ids"] == []


def test_generate_chat_data_stream_projects_selected_member_scope_from_shared_context(monkeypatch):
    import asyncio

    captured_agent_kwargs = {}
    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="已完成"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())

    def create_agent(_runtime_options, **kwargs):
        captured_agent_kwargs.update(kwargs)
        return fake_graph

    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", create_agent)

    asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    thread_id="conv_query_member_selected",
                    user_query="查询订单数",
                    datasource_id="ds_a",
                    user_id="user_a",
                    member_datasource_ids=["m1"],
                    semantic_config_id="cfg_a",
                )
            )
        )
    )

    assert fake_graph.initial_state["execution_context"]["available_member_datasource_ids"] == ["m1"]


def test_generate_chat_data_stream_emits_error_when_graph_fails(monkeypatch):
    import asyncio

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: _FakeGraph(raise_on_stream=True)
    )

    events, failure = asyncio.run(
        _collect_async_iter_until_failure(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_error",
                    user_query="查订单数",
                    datasource_id="ds_a",
                )
            )
        )
    )

    joined = "".join(events)
    assert 'event: error\ndata: {"error": "智能问数执行失败，请稍后重试。"}' in joined
    assert "graph failed" not in joined
    assert "event: done" not in joined  # done 由 Runtime 统一发布，生成器不再输出
    # 失败异常必须抛给 Runtime（由其 finish_run("failed") 收口），不能被 Handler 吞掉。
    assert failure is not None


def test_generate_chat_data_stream_keeps_context_limit_error_actionable(monkeypatch):
    import asyncio

    class ContextLimitGraph(_FakeGraph):
        async def astream(self, initial_state, config=None, stream_mode=None):
            _ = initial_state, config, stream_mode
            raise ContextInputLimitExceededError("当前输入内容过长，请拆分问题后重试。")
            yield  # pragma: no cover - keeps this an async generator for the stream adapter

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: ContextLimitGraph()
    )

    events, failure = asyncio.run(
        _collect_async_iter_until_failure(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_context_limit",
                    user_query="查订单数",
                    datasource_id="ds_a",
                )
            )
        )
    )

    joined = "".join(events)
    assert "当前输入内容过长，请拆分问题后重试。" in joined
    assert "智能问数执行失败，请稍后重试。" not in joined
    assert "event: done" not in joined  # done 由 Runtime 统一发布，生成器不再输出
    assert failure is not None  # 失败异常抛给 Runtime 收口


def test_generate_chat_data_stream_semantic_config_missing(monkeypatch):
    import asyncio

    _override_semantic_context(
        monkeypatch,
        semantic_config=None,
        semantic_config_error="未找到可用指标集: cfg_missing",
    )
    missing_events, failure = asyncio.run(
        _collect_async_iter_until_failure(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_missing_semantic",
                    user_query="查订单",
                    datasource_id="ds_a",
                    semantic_config_id="cfg_missing",
                )
            )
        )
    )
    assert "未找到可用指标集: cfg_missing" in "".join(missing_events)
    assert failure is not None  # 失败异常抛给 Runtime 收口


def test_generate_chat_data_stream_handles_disconnect_and_cached_tables(monkeypatch):
    import asyncio

    class DisconnectRequest:
        async def is_disconnected(self):
            return True

    fake_graph = _FakeGraph(
        events=[(_FakeMessageChunk(content="不应返回"), {"langgraph_node": "model"})],
        state=SimpleNamespace(tasks=[], values={}),
    )

    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events = asyncio.run(
        _collect_async_iter(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_disconnect",
                    user_query="查订单",
                    datasource_id="ds_a",
                    request=DisconnectRequest(),
                )
            )
        )
    )

    assert events == []
    assert fake_graph.initial_state is None


def test_generate_chat_data_stream_handles_state_lookup_failure_and_chunk_without_content(monkeypatch):
    import asyncio

    class StateFailGraph(_FakeGraph):
        async def aget_state(self, config):
            raise RuntimeError("state missing")

    fake_graph = StateFailGraph(
        events=[
            (
                _FakeMessageChunk(
                    content="", tool_call_chunks=[{"id": "call_1", "name": "data_analyzing_tool", "args": ""}]
                ),
                {"langgraph_node": "model"},
            ),
            (_FakeMessageChunk(content="完成"), {"langgraph_node": "model"}),
        ]
    )
    monkeypatch.setattr("apps.chat_data_app.agent_stream.get_checkpointer", lambda: _fake_checkpointer())
    monkeypatch.setattr("apps.chat_data_app.agent_stream.create_data_agent", lambda *_args, **_kwargs: fake_graph)

    events, failure = asyncio.run(
        _collect_async_iter_until_failure(
            _generate_chat_data_stream(
                ChatDataStreamRequest(
                    user_id="u_test",
                    thread_id="conv_state_fail",
                    user_query="查订单",
                    datasource_id="ds_a",
                )
            )
        )
    )

    joined = "".join(events)
    assert "完成" in joined
    assert "event: done" not in joined  # done 由 Runtime 统一发布
    assert failure is not None  # state 查找失败属于流失败契约，异常抛给 Runtime 收口


def test_sql_completion_handler_wraps_stream(monkeypatch):
    import asyncio

    captured = {}

    async def fake_get_or_create(*args, **kwargs):
        captured["conversation"] = args
        return {}

    async def fake_start_run(*args, **kwargs):
        captured["start_run_args"] = args
        captured["start_run_kwargs"] = kwargs
        return "run-test", []

    def fake_submit(**kwargs):
        captured["submit_kwargs"] = kwargs

    async def fake_event_stream(**kwargs):
        captured["event_stream_kwargs"] = kwargs
        yield {
            "stream_id": "test-1-0",
            "payload": _format_sse("done", "[DONE]"),
        }

    monkeypatch.setattr("apps.chat_data_app.agent_stream.conversation.get_or_create_conversation", fake_get_or_create)
    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.agent_inbox_service.start_run",
        fake_start_run,
    )
    monkeypatch.setattr("apps.chat_data_app.agent_stream.agent_run_runtime.submit", fake_submit)
    monkeypatch.setattr("apps.chat_data_app.agent_stream.agent_run_event_service.stream", fake_event_stream)

    response = asyncio.run(
        sql_completion_handler(
            conversation_id="conv_sql",
            query="查订单",
            options={
                "datasource_id": "ds_a",
                "mode": "query",
                "semantic_config_id": "cfg",
                "model_id": "m1",
                "member_datasource_ids": ["m1"],
            },
            user_id="u1",
            username="测试用户",
        )
    )
    assert response.media_type == "text/event-stream"
    assert response.headers["X-ChatData-Run-Id"] == "run-test"
    assert response.headers["X-Accel-Buffering"] == "no"
    assert captured["conversation"] == ("chat_data", "conv_sql", "u1")
    chunks = asyncio.run(_collect_async_iter(response.body_iterator))
    assert chunks == ["id: test-1-0\nevent: done\ndata: [DONE]\n\n"]
    runtime_context = captured["start_run_args"][3]
    assert captured["start_run_args"][:3] == ("conv_sql", "u1", "测试用户")
    assert runtime_context["member_datasource_ids"] == ["m1"]
    assert runtime_context["semantic_config_id"] == "cfg"
    assert runtime_context["model_id"] == "m1"
    # 请求到达墙钟随 Run 行透传（harness 首 token / 任务总耗时起点）
    assert captured["start_run_kwargs"]["stream_started_wall_ms"] > 0
    assert captured["submit_kwargs"] == {
        "run_id": "run-test",
        "conversation_id": "conv_sql",
        "user_id": "u1",
        "operator_name": "测试用户",
    }
    assert captured["event_stream_kwargs"]["run_id"] == "run-test"
    assert captured["event_stream_kwargs"]["last_event_id"] == "0-0"


# ---- 图片附件准入（vision_attachments 路径标记，base64 由 middleware 请求层装载） ----

from apps.chat_data_app.agent.run.chat_data_stream_request import ChatDataStreamRequest
from apps.chat_data_app.agent.run.conversation_event_service import _extract_text_content
from apps.chat_data_app.services.agent_run.message_compose import (
    is_image_attachment as _is_image_attachment,
)
from apps.chat_data_app.services.agent_run.message_compose import (
    select_vision_attachments as _select_vision_attachments,
)
from apps.chat_data_app.services.agent_run.uploaded_file_service import MAX_IMAGES_PER_MESSAGE

# 图片大小上限（字节）：与注册表默认值（CHAT_DATA_MAX_IMAGE_SIZE_MB=5）一致，显式传参。
_VISION_LIMIT = 5 * 1024 * 1024


def test_is_image_attachment_checks_type_and_size():
    ok = {"file_id": "f1", "file_type": "png", "file_size": 1024}
    assert _is_image_attachment(ok, max_image_size=_VISION_LIMIT)
    assert not _is_image_attachment(
        {"file_id": "f2", "file_type": "pdf", "file_size": 1024}, max_image_size=_VISION_LIMIT
    )
    assert not _is_image_attachment({"file_type": "png", "file_size": 1024}, max_image_size=_VISION_LIMIT)  # 无 file_id
    assert not _is_image_attachment({**ok, "file_size": 99 * 1024 * 1024}, max_image_size=_VISION_LIMIT)  # 超大小上限
    # 输入卸载附件（无 file_id 的 parsed_path 条目）
    assert not _is_image_attachment(
        {"original_filename": "input.txt", "parsed_path": "/mnt/x"}, max_image_size=_VISION_LIMIT
    )


def test_select_vision_attachments_returns_image_paths_only():
    atts = [
        {
            "file_id": "img1",
            "original_filename": "a.png",
            "file_type": "png",
            "file_size": 10,
            "virtual_path": "/mnt/user-data/uploads/a.png",
        },
        {
            "file_id": "doc1",
            "original_filename": "b.pdf",
            "file_type": "pdf",
            "file_size": 10,
            "virtual_path": "/mnt/user-data/uploads/b.pdf",
        },
        {
            "file_id": "img2",
            "original_filename": "c.jpg",
            "file_type": "jpg",
            "file_size": 10,
            "virtual_path": "/mnt/user-data/uploads/c.jpg",
        },
    ]
    paths = _select_vision_attachments(atts, max_image_size=_VISION_LIMIT)
    assert paths == ["/mnt/user-data/uploads/a.png", "/mnt/user-data/uploads/c.jpg"]


def test_select_vision_attachments_caps_at_limit_and_skips_invalid():
    images = [
        {
            "file_id": f"img{i}",
            "original_filename": f"{i}.png",
            "file_type": "png",
            "file_size": 10,
            "virtual_path": f"/mnt/user-data/uploads/{i}.png",
        }
        for i in range(MAX_IMAGES_PER_MESSAGE + 2)
    ]
    oversized = [
        {
            "file_id": "big",
            "original_filename": "big.png",
            "file_type": "png",
            "file_size": 99 * 1024 * 1024,
            "virtual_path": "/mnt/user-data/uploads/big.png",
        }
    ]
    paths = _select_vision_attachments(oversized + images, max_image_size=_VISION_LIMIT)
    assert len(paths) == MAX_IMAGES_PER_MESSAGE
    assert all(path.startswith("/mnt/user-data/uploads/") for path in paths)


def test_select_vision_attachments_empty_without_images():
    atts = [
        {
            "file_id": "doc1",
            "original_filename": "b.pdf",
            "file_type": "pdf",
            "file_size": 10,
            "virtual_path": "/mnt/user-data/uploads/b.pdf",
        },
        {"original_filename": "input.txt", "parsed_path": "/mnt/x"},
    ]
    assert _select_vision_attachments([], max_image_size=_VISION_LIMIT) == []
    assert _select_vision_attachments(atts, max_image_size=_VISION_LIMIT) == []


def test_extract_text_content_strips_attachment_lines():
    blocks = [
        {"type": "text", "text": "你好\n[attachment] /mnt/user-data/uploads/a.png"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
    ]
    assert _extract_text_content(blocks) == "你好"
    # 纯字符串历史消息同样剔除占位行
    assert _extract_text_content("查订单\n[attachment] /mnt/x.xlsx") == "查订单"
    # 只发文件无文字 → 空字符串（标题生成走兜底）
    assert _extract_text_content([{"type": "text", "text": "[attachment] /x/a.png"}]) == ""


def test_resolve_task_durations_interrupt_handover():
    """任务总耗时接力：本段 + Run 行历史累计；interrupt 段写回 Run 行。"""
    import asyncio
    import time

    adapter = agent_stream_module._AgentSseStreamAdapter(
        agent=None,
        initial_state=None,
        config={},
        request=None,
    )
    # 请求到达锚点缺失 → 不产耗时（不估算）
    assert asyncio.run(adapter._resolve_task_durations()) == {}

    now_ms = int(time.time() * 1000)
    writes: list[dict[str, int]] = []

    class _State:
        interrupts = (object(),)  # interrupt 段

    adapter = agent_stream_module._AgentSseStreamAdapter(
        agent=None,
        initial_state=None,
        config={},
        request=None,
        duration_relay=agent_stream_module._DurationRelayHooks(
            stream_started_wall_ms=now_ms - 3000,
            resolve_inherited_duration=_async_return({"total_duration_ms": 1200, "model_duration_ms": 900}),
            on_interrupt_finalize=_AsyncAppend(writes),
        ),
    )
    adapter._stream_ended_wall_ms = now_ms
    adapter.state = _State()
    durations = asyncio.run(adapter._resolve_task_durations())
    assert durations == {"total_duration_ms": 4200}  # 本段 3000 + 继承 1200；历史 model 键忽略
    assert writes == [{"total_duration_ms": 4200}]  # interrupt 段写回 Run 行

    # 非 interrupt 段（正常完成）→ 不写回，仅返回累计值
    writes.clear()

    class _DoneState:
        interrupts = ()

    adapter.state = _DoneState()
    durations = asyncio.run(adapter._resolve_task_durations())
    assert durations == {"total_duration_ms": 4200}
    assert writes == []


class _AsyncAppend:
    """把 append 包装成 async callable，用于断言 on_interrupt_finalize 回调。"""

    def __init__(self, sink: list[dict[str, int]]) -> None:
        self._sink = sink

    async def __call__(self, value: dict[str, int]) -> None:
        self._sink.append(value)


def _async_return(value):
    async def _reader():
        return value

    return _reader
