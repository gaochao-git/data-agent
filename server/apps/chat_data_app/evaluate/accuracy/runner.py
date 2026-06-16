"""Accuracy evaluation service for ChatData NL -> DSL -> SQL."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys
import time
from typing import Any
import uuid

try:
    from .scorers import score_dsl
except ImportError:
    from scorers import score_dsl


ACCURACY_DIR = Path(__file__).resolve().parent
SERVER_DIR = ACCURACY_DIR.parents[3]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

QUERY_TYPES = {"filter", "join", "aggregation", "subquery"}
DIFFICULTIES = {"easy", "medium", "hard"}
SOURCES = {"human", "llm"}
_LEGACY_SOURCE_MAP = {"model_generated": "llm"}
SEVEN_TUPLE_KEYS = ("nl", "gold_dsl", "gold_sql", "query_type", "difficulty", "source", "business_domain")


@dataclass
class AccuracyRunConfig:
    """准确率评测运行配置，供 API 层显式传入。"""

    agent_id: str = "chat_data"
    query_mode: str = "query"
    model_id: str | None = None
    datasource_id: str | None = None
    semantic_config_id: str | None = None
    user_id: str = "accuracy-evaluator"
    request_timeout: float = 180.0
    include_traces: bool = False
    local_compile: bool = True
    skip_execution: bool = True
    limit: int | None = None
    case_id: list[str] | None = None


@dataclass
class ToolTrace:
    name: str
    status: str
    args: dict[str, Any] = field(default_factory=dict)
    content: str = ""


@dataclass
class LiveCaseResult:
    case_id: str
    question: str
    status: str
    dsl_score: dict[str, Any]
    sql_score: dict[str, Any]
    compile_success: bool
    execution_success: bool | None
    metadata: dict[str, Any] = field(default_factory=dict)
    generated_sql: str = ""
    agent_tool_sql: str = ""
    error: str = ""
    elapsed_ms: int = 0
    conversation_id: str = ""
    tool_traces: list[ToolTrace] = field(default_factory=list)


def case_id(case: dict[str, Any]) -> str:
    """读取评估用例 ID。"""
    return str(case.get("id") or "").strip()


def case_question(case: dict[str, Any]) -> str:
    """兼容读取 7 元组和旧格式问题。"""
    return str(case.get("nl") or case.get("question") or "").strip()


def case_expected_dsl(case: dict[str, Any]) -> dict[str, Any] | None:
    """兼容读取 7 元组和旧格式 DSL。"""
    value = case.get("gold_dsl") if "gold_dsl" in case else case.get("expected_dsl")
    return value if isinstance(value, dict) else None


def case_gold_sql(case: dict[str, Any]) -> str:
    """读取标准 SQL。"""
    return str(case.get("gold_sql") or "").strip()


def case_metadata(case: dict[str, Any]) -> dict[str, Any]:
    """提取用例分组统计字段。"""
    return {
        "query_type": case.get("query_type") or case.get("category"),
        "difficulty": case.get("difficulty"),
        "source": case.get("source"),
        "business_domain": case.get("business_domain"),
    }


def is_seven_tuple_case(case: dict[str, Any]) -> bool:
    """判断用例是否使用 7 元组格式。"""
    return any(key in case for key in ("nl", "gold_dsl", "gold_sql"))


def validate_cases(cases: list[dict[str, Any]], path: Path) -> None:
    """校验 7 元组字段并保留旧格式兼容。"""
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"case 必须是对象: {path}#{index}")
        if not case_question(case):
            raise ValueError(f"case 缺少 nl/question: {path}#{index}")
        if not case_expected_dsl(case):
            raise ValueError(f"case 缺少 gold_dsl/expected_dsl: {path}#{index}")
        if not is_seven_tuple_case(case):
            continue
        missing = [key for key in SEVEN_TUPLE_KEYS if key not in case]
        if missing:
            raise ValueError(f"7 元组 case 字段不完整: {path}#{index} missing={missing}")
        if case.get("query_type") not in QUERY_TYPES:
            raise ValueError(f"query_type 非法: {path}#{index} value={case.get('query_type')}")
        if case.get("difficulty") not in DIFFICULTIES:
            raise ValueError(f"difficulty 非法: {path}#{index} value={case.get('difficulty')}")
        source = _LEGACY_SOURCE_MAP.get(case.get("source"), case.get("source"))
        if source not in SOURCES:
            raise ValueError(f"source 非法: {path}#{index} value={case.get('source')}")
        case["source"] = source
        if not str(case.get("business_domain") or "").strip():
            raise ValueError(f"business_domain 不能为空: {path}#{index}")


def normalize_sql(sql: str) -> str:
    """标准化 SQL 空白后用于精确匹配。"""
    return " ".join(str(sql or "").strip().split())


def score_sql(predicted_sql: str, gold_sql: str) -> dict[str, Any]:
    """评估 SQL 精确匹配。"""
    expected = normalize_sql(gold_sql)
    predicted = normalize_sql(predicted_sql)
    if not expected:
        return {"sql_exact_match": None, "predicted_sql": predicted, "gold_sql": expected}
    return {"sql_exact_match": predicted == expected, "predicted_sql": predicted, "gold_sql": expected}


def parse_sse_data(data: str) -> Any:
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data


def parse_tool_content(content: str) -> dict[str, Any]:
    text = strip_tool_content_prefix(content)
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def strip_tool_content_prefix(content: str) -> str:
    """移除工具返回内容中的内部前缀。"""
    text = str(content or "").strip()
    for prefix in ("[dsl2sql_tool]", "[execute_sql_tool]"):
        if text.startswith(prefix):
            return text.replace(prefix, "", 1).strip()
    return text


def extract_sql_from_tool_content(content: str) -> str:
    """从智能问数 dsl2sql_tool 返回内容中提取 SQL。"""
    text = strip_tool_content_prefix(content)
    if not text:
        return ""
    payload = parse_tool_content(content)
    if payload.get("sql"):
        return str(payload.get("sql") or "").strip()
    matched = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if matched:
        return matched.group(1).strip()
    if text.lower().lstrip().startswith(("select", "with")):
        return text.strip()
    return ""


def build_dsl_from_tool_args(args: dict[str, Any]) -> dict[str, Any]:
    """将智能问数语义工具参数还原为评测 DSL。"""
    if not isinstance(args, dict):
        return {}
    nested_dsl = args.get("dsl")
    if isinstance(nested_dsl, dict):
        return normalize_pagination_values(nested_dsl)
    dsl: dict[str, Any] = {}
    for key in ("metrics", "dimensions", "filters", "having", "order_by", "limit", "offset", "visualization"):
        if key in args:
            dsl[key] = args.get(key)
    return normalize_pagination_values(dsl)


def normalize_pagination_values(dsl: dict[str, Any]) -> dict[str, Any]:
    """把模型输出中的纯数字分页字符串收敛为整数。"""
    normalized = dict(dsl)
    for key in ("limit", "offset"):
        value = normalized.get(key)
        if isinstance(value, str) and value.strip().isdigit():
            normalized[key] = int(value.strip())
    return normalized


def build_dsl2sql_payload(trace: ToolTrace) -> dict[str, Any]:
    """兼容智能分析 JSON 结果和智能问数 SQL 代码块结果。"""
    payload = parse_tool_content(trace.content)
    if payload:
        if "dsl" not in payload:
            dsl = build_dsl_from_tool_args(trace.args)
            if dsl:
                payload["dsl"] = dsl
        if "sql" not in payload:
            sql = extract_sql_from_tool_content(trace.content)
            if sql:
                payload["sql"] = sql
        return payload
    dsl = build_dsl_from_tool_args(trace.args)
    sql = extract_sql_from_tool_content(trace.content)
    if not dsl and not sql:
        return {}
    raw_content = strip_tool_content_prefix(trace.content)
    success = bool(sql) or not raw_content
    return {
        "success": success,
        "dsl": dsl,
        "sql": sql,
        "error": "" if success else raw_content[:1000],
    }


def extract_error_from_event(event_name: str, payload: Any) -> str:
    if event_name != "error":
        return ""
    if isinstance(payload, dict):
        return str(payload.get("error") or payload.get("message") or payload)[:1000]
    return str(payload)[:1000]


def consume_sse_line(
    *,
    line: str,
    state: dict[str, Any],
    tool_traces: list[ToolTrace],
) -> None:
    """解析一行 SSE 响应并提取工具轨迹。"""
    if line == "":
        flush_sse_event(state=state, tool_traces=tool_traces)
        return
    if line.startswith("event:"):
        state["current_event"] = line.split(":", 1)[1].strip() or "message"
    elif line.startswith("data:"):
        state["data_lines"].append(line.split(":", 1)[1].lstrip())


def flush_sse_event(*, state: dict[str, Any], tool_traces: list[ToolTrace]) -> None:
    """消费当前 SSE 事件缓存。"""
    current_event = str(state.get("current_event") or "message")
    data_lines = state.get("data_lines") or []
    if not data_lines and not current_event:
        return
    raw_data = "\n".join(data_lines)
    payload = parse_sse_data(raw_data)
    event_error = extract_error_from_event(current_event, payload)
    if event_error:
        state["error"] = event_error
    if current_event == "tool" and isinstance(payload, dict):
        tool_traces.append(ToolTrace(
            name=str(payload.get("tool_name") or payload.get("name") or ""),
            status=str(payload.get("status") or ""),
            args=payload.get("accumulated_args") or payload.get("args") or {},
            content=str(payload.get("content") or ""),
        ))
    state["current_event"] = "message"
    state["data_lines"] = []


async def consume_internal_completion(
    *,
    conversation_id: str,
    agent_id: str,
    query: str,
    options: dict[str, Any],
    user_id: str,
) -> tuple[list[ToolTrace], str]:
    """内部调用智能问数 handler，避免评测通过 URL 反调自身。"""
    from apps.chat_data_app.handlers import data_analyze_completion_handler, sql_completion_handler

    if agent_id == "chat_data_data_analyze":
        response = await data_analyze_completion_handler(conversation_id, query, options=options, user_id=user_id)
    elif agent_id == "chat_data":
        response = await sql_completion_handler(conversation_id, query, options=options, user_id=user_id)
    else:
        raise ValueError(f"不支持的评测 Agent: {agent_id}")

    tool_traces: list[ToolTrace] = []
    state: dict[str, Any] = {"current_event": "message", "data_lines": [], "error": ""}
    async for chunk in response.body_iterator:
        text = chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
        for line in text.splitlines():
            consume_sse_line(line=line, state=state, tool_traces=tool_traces)
    flush_sse_event(state=state, tool_traces=tool_traces)
    return tool_traces, str(state.get("error") or "")


def choose_final_dsl2sql_trace(tool_traces: list[ToolTrace]) -> tuple[ToolTrace | None, dict[str, Any]]:
    selected_trace = None
    selected_payload: dict[str, Any] = {}
    for trace in tool_traces:
        if trace.name != "dsl2sql_tool":
            continue
        payload = build_dsl2sql_payload(trace)
        if payload:
            selected_trace = trace
            selected_payload = payload
    return selected_trace, selected_payload


def summarize_execution_success(tool_traces: list[ToolTrace]) -> bool | None:
    execute_payloads = [
        parse_tool_content(trace.content)
        for trace in tool_traces
        if trace.name == "execute_sql_tool" and trace.content
    ]
    execute_payloads = [payload for payload in execute_payloads if payload]
    if not execute_payloads:
        return None
    return bool(execute_payloads[-1].get("success"))


async def compile_dsl_locally(
    *,
    datasource_id: str,
    semantic_config_id: str,
    dsl: dict[str, Any] | None,
    user_query: str,
) -> dict[str, Any]:
    """Compile captured DSL with the current local DSL2SQL implementation."""
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "未捕获到可编译 DSL"}
    from apps.chat_data_app.services.dashboard_spec_service import (
        get_dashboard_spec_semantic_config,
        remove_mixed_raw_metrics_for_aggregate_dsl,
    )
    from apps.chat_data_app.semantic.compiler import SemanticSQLGenerator

    semantic_config = await get_dashboard_spec_semantic_config(datasource_id, semantic_config_id)
    payload, warnings = remove_mixed_raw_metrics_for_aggregate_dsl(dsl, semantic_config)
    payload = normalize_pagination_values(payload)
    if payload.get("compare"):
        return {"success": False, "sql": "", "error": "DSL2SQL 不支持 compare；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析", "warnings": warnings}
    result = SemanticSQLGenerator(semantic_config).generate_sql(
        metrics=payload.get("metrics") or [],
        dimensions=payload.get("dimensions") or [],
        filters=payload.get("filters") or [],
        having=payload.get("having") or [],
        order_by=payload.get("order_by") or [],
        limit=payload.get("limit"),
        offset=payload.get("offset"),
        user_query=user_query,
    )
    return {
        "success": bool(result.get("success")),
        "sql": str(result.get("sql") or ""),
        "error": result.get("error"),
        "warnings": warnings,
    }


async def execute_sql_locally(*, sql: str, datasource_id: str, user_id: str) -> dict[str, Any]:
    """Execute generated SQL through the same backend SQL executor used by DataAnalyze."""
    if not sql.strip():
        return {"success": False, "error": "SQL 为空"}
    from apps.chat_data_app.agent.tools.sql import _execute_sql_real

    return await _execute_sql_real(sql=sql, datasource_id=datasource_id, member_datasource_ids=None, user_id=user_id)


async def run_live_case(
    *,
    args: AccuracyRunConfig,
    case: dict[str, Any],
    datasource_id: str,
    semantic_config_id: str,
) -> LiveCaseResult:
    started_at = time.perf_counter()
    question = case_question(case)
    conversation_id = ""
    try:
        conversation_id = str(uuid.uuid4())
        options = {
            "datasource_id": datasource_id,
            "semantic_config_id": semantic_config_id,
            "mode": args.query_mode,
        }
        if args.model_id:
            options["model_id"] = args.model_id
        tool_traces, stream_error = await asyncio.wait_for(
            consume_internal_completion(
                conversation_id=conversation_id,
                agent_id=args.agent_id,
                query=question,
                options=options,
                user_id=args.user_id,
            ),
            timeout=args.request_timeout,
        )
        dsl_trace, dsl_payload = choose_final_dsl2sql_trace(tool_traces)
        predicted_dsl = dsl_payload.get("dsl") if isinstance(dsl_payload, dict) else None
        dsl_score = score_dsl(predicted_dsl, case_expected_dsl(case))
        agent_tool_sql = str(dsl_payload.get("sql") or "") if dsl_payload else ""
        if args.local_compile:
            compile_result = await compile_dsl_locally(
                datasource_id=datasource_id,
                semantic_config_id=semantic_config_id,
                dsl=predicted_dsl,
                user_query=question,
            )
            compile_success = bool(compile_result.get("success"))
            generated_sql = str(compile_result.get("sql") or "")
            execution_success = None
            if compile_success and not args.skip_execution:
                execution_result = await execute_sql_locally(sql=generated_sql, datasource_id=datasource_id, user_id=args.user_id)
                execution_success = bool(execution_result.get("success"))
                if not execution_success:
                    stream_error = stream_error or str(execution_result.get("error") or "SQL 执行失败")
        else:
            compile_success = bool(dsl_payload.get("success")) if dsl_payload else False
            generated_sql = agent_tool_sql
            execution_success = summarize_execution_success(tool_traces)
        error = stream_error
        if not dsl_trace:
            error = error or "未捕获到 dsl2sql_tool 调用"
        elif not compile_success:
            error = error or str((compile_result if args.local_compile else dsl_payload).get("error") or "DSL2SQL 编译失败")
        status = "scored" if dsl_trace else "failed"
        sql_score = score_sql(generated_sql, case_gold_sql(case))
        return LiveCaseResult(
            case_id=case_id(case),
            question=question,
            status=status,
            dsl_score=dsl_score,
            sql_score=sql_score,
            compile_success=compile_success,
            execution_success=execution_success,
            metadata=case_metadata(case),
            generated_sql=generated_sql,
            agent_tool_sql=agent_tool_sql,
            error=error,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            conversation_id=conversation_id,
            tool_traces=tool_traces,
        )
    except Exception as exc:
        return LiveCaseResult(
            case_id=case_id(case),
            question=question,
            status="failed",
            dsl_score=score_dsl(None, case_expected_dsl(case)),
            sql_score=score_sql("", case_gold_sql(case)),
            compile_success=False,
            execution_success=None,
            metadata=case_metadata(case),
            error=repr(exc),
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            conversation_id=conversation_id,
        )


def live_result_to_dict(result: LiveCaseResult, include_traces: bool) -> dict[str, Any]:
    payload = {
        "id": result.case_id,
        "question": result.question,
        "status": result.status,
        "conversation_id": result.conversation_id,
        "elapsed_ms": result.elapsed_ms,
        "compile_success": result.compile_success,
        "execution_success": result.execution_success,
        "generated_sql": result.generated_sql,
        "agent_tool_sql": result.agent_tool_sql,
        "error": result.error,
        **result.metadata,
        **result.dsl_score,
        **result.sql_score,
    }
    if include_traces:
        payload["tool_traces"] = [trace.__dict__ for trace in result.tool_traces]
    return payload


def build_live_report(results: list[LiveCaseResult], include_traces: bool) -> dict[str, Any]:
    total = len(results)
    scored = [item for item in results if item.status == "scored"]
    exact = sum(1 for item in scored if item.dsl_score.get("dsl_exact_match"))
    sql_scored = [item for item in scored if item.sql_score.get("sql_exact_match") is not None]
    sql_exact = sum(1 for item in sql_scored if item.sql_score.get("sql_exact_match"))
    compile_success = sum(1 for item in scored if item.compile_success)
    executable = [item for item in results if item.execution_success is not None]
    execution_success = sum(1 for item in executable if item.execution_success)
    semantic_accuracy = (
        sum(float(item.dsl_score.get("semantic_accuracy") or 0) for item in scored) / len(scored)
        if scored else 0.0
    )
    return {
        "summary": {
            "total": total,
            "scored": len(scored),
            "failed": total - len(scored),
            "semantic_accuracy": semantic_accuracy,
            "compile_success_count": compile_success,
            "dsl2sql_compile_success_rate": compile_success / len(scored) if scored else 0.0,
            "execution_checked": len(executable),
            "execution_success_count": execution_success,
            "sql_execution_success_rate": execution_success / len(executable) if executable else None,
            "debug_metrics": {
                "dsl_exact_match_count": exact,
                "dsl_exact_match_rate": exact / len(scored) if scored else 0.0,
                "sql_exact_match_count": sql_exact,
                "sql_exact_match_rate": sql_exact / len(sql_scored) if sql_scored else None,
                "avg_dsl_slot_accuracy": semantic_accuracy,
            },
        },
        "results": [live_result_to_dict(item, include_traces) for item in results],
    }


async def run_live_score(args: AccuracyRunConfig, cases: list[dict[str, Any]]) -> dict[str, Any]:
    datasource_id = args.datasource_id
    semantic_config_id = args.semantic_config_id
    if not datasource_id:
        raise ValueError("缺少 datasource_id")
    if not semantic_config_id:
        raise ValueError("缺少 semantic_config_id")

    results: list[LiveCaseResult] = []
    for case in cases:
        result = await run_live_case(
            args=args,
            case=case,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
        )
        results.append(result)
    return build_live_report(results, args.include_traces)


async def iter_accuracy_evaluation_events(
    args: AccuracyRunConfig,
    cases: list[dict[str, Any]],
) -> Any:
    """按用例逐个产出评测事件，供 SSE 流式返回。"""
    total = len(cases)
    yield {
        "event": "start",
        "total": total,
        "case_ids": [case_id(case) for case in cases],
    }
    datasource_id = args.datasource_id
    semantic_config_id = args.semantic_config_id
    if not datasource_id:
        raise ValueError("缺少 datasource_id")
    if not semantic_config_id:
        raise ValueError("缺少 semantic_config_id")

    live_results: list[LiveCaseResult] = []
    for index, case in enumerate(cases, start=1):
        case_key = case_id(case)
        yield {
            "event": "case_start",
            "index": index,
            "total": total,
            "id": case_key,
            "question": case_question(case),
        }
        result = await run_live_case(
            args=args,
            case=case,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
        )
        live_results.append(result)
        payload = live_result_to_dict(result, args.include_traces)
        yield {"event": "case_result", "index": index, "total": total, "id": case_key, "result": payload}
        yield {
            "event": "progress",
            "index": index,
            "total": total,
            "summary": build_live_report(live_results, args.include_traces)["summary"],
        }
    yield {"event": "done", "report": build_live_report(live_results, args.include_traces)}


def select_cases(cases: list[dict[str, Any]], args: AccuracyRunConfig) -> list[dict[str, Any]]:
    selected = list(cases)
    if args.case_id:
        wanted = set(args.case_id)
        selected = [case for case in selected if str(case.get("id") or "") in wanted]
        missing = sorted(wanted - {str(case.get("id") or "") for case in selected})
        if missing:
            raise ValueError(f"指定 case-id 不存在: {', '.join(missing)}")
    if args.limit is not None:
        selected = selected[:args.limit]
    if not selected:
        raise ValueError("没有匹配的评估用例")
    return selected
