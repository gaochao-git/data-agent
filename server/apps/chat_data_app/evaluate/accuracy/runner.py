"""Accuracy evaluation service for ChatData NL -> DSL -> SQL."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from .scorers import score_dsl
except ImportError:
    from scorers import score_dsl


def _result_succeeded(result: Any) -> bool:
    """查询结果是否成功；status 为唯一权威口径（query_result_succeeded 内兼容旧 success 记录）。"""
    from apps.chat_data_app.services.sql_run.result import query_result_succeeded

    return query_result_succeeded(result)


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


def validate_basic_case(case: Any, path: Path, index: int) -> None:
    if not isinstance(case, dict):
        raise ValueError(f"case 必须是对象: {path}#{index}")
    if not case_question(case):
        raise ValueError(f"case 缺少 nl/question: {path}#{index}")
    if not case_expected_dsl(case):
        raise ValueError(f"case 缺少 gold_dsl/expected_dsl: {path}#{index}")


def validate_seven_tuple_fields(case: dict[str, Any], path: Path, index: int) -> None:
    missing = [key for key in SEVEN_TUPLE_KEYS if key not in case]
    if missing:
        raise ValueError(f"7 元组 case 字段不完整: {path}#{index} missing={missing}")
    if case.get("query_type") not in QUERY_TYPES:
        raise ValueError(f"query_type 非法: {path}#{index} value={case.get('query_type')}")
    if case.get("difficulty") not in DIFFICULTIES:
        raise ValueError(f"difficulty 非法: {path}#{index} value={case.get('difficulty')}")
    if not str(case.get("business_domain") or "").strip():
        raise ValueError(f"business_domain 不能为空: {path}#{index}")


def normalize_case_source(case: dict[str, Any], path: Path, index: int) -> None:
    source = _LEGACY_SOURCE_MAP.get(case.get("source"), case.get("source"))
    if source not in SOURCES:
        raise ValueError(f"source 非法: {path}#{index} value={case.get('source')}")
    case["source"] = source


def validate_case(case: Any, path: Path, index: int) -> None:
    validate_basic_case(case, path, index)
    if not is_seven_tuple_case(case):
        return
    validate_seven_tuple_fields(case, path, index)
    normalize_case_source(case, path, index)


def validate_cases(cases: list[dict[str, Any]], path: Path) -> None:
    """校验 7 元组字段并保留旧格式兼容。"""
    for index, case in enumerate(cases, start=1):
        validate_case(case, path, index)


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
    prefix = "[data_analyzing_tool]"
    if text.startswith(prefix):
        return text.replace(prefix, "", 1).strip()
    return text


def extract_sql_from_tool_content(content: str) -> str:
    """从数据分析工具返回内容中提取 SQL。"""
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
    """将智能问数语义工具参数还原为评测 DSL（兼容合并后的 action_payload 包装）。"""
    if not isinstance(args, dict):
        return {}
    if isinstance(args.get("action_payload"), dict):
        args = args["action_payload"]
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
        tool_traces.append(
            ToolTrace(
                name=str(payload.get("tool_name") or payload.get("name") or ""),
                status=str(payload.get("status") or ""),
                args=payload.get("accumulated_args") or payload.get("args") or {},
                content=str(payload.get("content") or ""),
            )
        )
    state["current_event"] = "message"
    state["data_lines"] = []


async def consume_internal_completion(
    *,
    conversation_id: str,
    query: str,
    options: dict[str, Any],
    user_id: str,
) -> tuple[list[ToolTrace], str]:
    """内部调用智能问数 handler，避免评测通过 URL 反调自身。"""
    from apps.chat_data_app.agent_stream import sql_completion_handler

    response = await sql_completion_handler(conversation_id, query, options=options, user_id=user_id)

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
        if trace.name != "data_analyzing_tool":
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
        if trace.name == "data_analyzing_tool" and trace.content
    ]
    # 只统计执行查询结果（query_result envelope / 带 status）；validate 的 success 仅代表编译通过
    execute_payloads = [
        payload
        for payload in execute_payloads
        if payload and (payload.get("type") == "query_result" or "status" in payload)
    ]
    if not execute_payloads:
        return None
    return _result_succeeded(execute_payloads[-1])


async def compile_dsl_locally(
    *,
    datasource_id: str,
    semantic_config_id: str,
    dsl: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compile captured DSL through the shared semantic query facade."""
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "未捕获到可编译 DSL"}
    from apps.chat_data_app.services.dashboard.spec_service import (
        get_dashboard_spec_semantic_config,
        remove_mixed_raw_metrics_for_aggregate_dsl,
    )
    from apps.chat_data_app.services.semantic.run.semantic_query_service import (
        compile_semantic_ai_dsl,
    )

    semantic_config = await get_dashboard_spec_semantic_config(datasource_id, semantic_config_id)
    payload, warnings = remove_mixed_raw_metrics_for_aggregate_dsl(dsl, semantic_config)
    payload = normalize_pagination_values(payload)
    if payload.get("compare"):
        return {
            "success": False,
            "sql": "",
            "error": "DSL2SQL 不支持 compare；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析",
            "warnings": warnings,
        }
    result = compile_semantic_ai_dsl(
        semantic_config,
        payload,
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
    from apps.chat_data_app.services.semantic.run.semantic_query_service import execute_semantic_query_sql

    return await execute_semantic_query_sql(
        sql=sql, datasource_id=datasource_id, member_datasource_ids=None, user_id=user_id
    )


def build_completion_options(args: AccuracyRunConfig, datasource_id: str, semantic_config_id: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "datasource_id": datasource_id,
        "semantic_config_id": semantic_config_id,
        "mode": args.query_mode,
    }
    if args.model_id:
        options["model_id"] = args.model_id
    return options


async def collect_live_case_traces(
    *,
    args: AccuracyRunConfig,
    question: str,
    conversation_id: str,
    datasource_id: str,
    semantic_config_id: str,
) -> tuple[list[ToolTrace], str]:
    return await asyncio.wait_for(
        consume_internal_completion(
            conversation_id=conversation_id,
            query=question,
            options=build_completion_options(args, datasource_id, semantic_config_id),
            user_id=args.user_id,
        ),
        timeout=args.request_timeout,
    )


async def compile_or_reuse_agent_sql(
    *,
    args: AccuracyRunConfig,
    datasource_id: str,
    semantic_config_id: str,
    predicted_dsl: dict[str, Any] | None,
    dsl_payload: dict[str, Any],
    agent_tool_sql: str,
) -> dict[str, Any]:
    if not args.local_compile:
        return {
            "compile_success": bool(dsl_payload.get("success")) if dsl_payload else False,
            "generated_sql": agent_tool_sql,
            "compile_result": dsl_payload,
        }
    compile_result = await compile_dsl_locally(
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        dsl=predicted_dsl,
    )
    return {
        "compile_success": bool(compile_result.get("success")),
        "generated_sql": str(compile_result.get("sql") or ""),
        "compile_result": compile_result,
    }


async def maybe_execute_generated_sql(
    *,
    args: AccuracyRunConfig,
    compile_success: bool,
    generated_sql: str,
    datasource_id: str,
) -> tuple[bool | None, str]:
    if args.skip_execution or not compile_success:
        return None, ""
    execution_result = await execute_sql_locally(sql=generated_sql, datasource_id=datasource_id, user_id=args.user_id)
    execution_success = _result_succeeded(execution_result)
    if execution_success:
        return execution_success, ""
    return execution_success, str(execution_result.get("error") or "SQL 执行失败")


def resolve_live_case_error(
    *,
    stream_error: str,
    dsl_trace: ToolTrace | None,
    compile_success: bool,
    compile_result: dict[str, Any],
) -> str:
    if not dsl_trace:
        return stream_error or "未捕获到 data_analyzing_tool 校验调用"
    if not compile_success:
        return stream_error or str(compile_result.get("error") or "DSL2SQL 编译失败")
    return stream_error


def build_failed_live_case_result(
    *,
    case: dict[str, Any],
    question: str,
    started_at: float,
    conversation_id: str,
    error: Exception,
) -> LiveCaseResult:
    return LiveCaseResult(
        case_id=case_id(case),
        question=question,
        status="failed",
        dsl_score=score_dsl(None, case_expected_dsl(case)),
        sql_score=score_sql("", case_gold_sql(case)),
        compile_success=False,
        execution_success=None,
        metadata=case_metadata(case),
        error=repr(error),
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        conversation_id=conversation_id,
    )


def build_scored_live_case_result(
    *,
    case: dict[str, Any],
    question: str,
    started_at: float,
    conversation_id: str,
    dsl_trace: ToolTrace | None,
    dsl_score: dict[str, Any],
    sql_score: dict[str, Any],
    compile_success: bool,
    execution_success: bool | None,
    generated_sql: str,
    agent_tool_sql: str,
    error: str,
    tool_traces: list[ToolTrace],
) -> LiveCaseResult:
    return LiveCaseResult(
        case_id=case_id(case),
        question=question,
        status="scored" if dsl_trace else "failed",
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
        tool_traces, stream_error = await collect_live_case_traces(
            args=args,
            question=question,
            conversation_id=conversation_id,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
        )
        dsl_trace, dsl_payload = choose_final_dsl2sql_trace(tool_traces)
        predicted_dsl = dsl_payload.get("dsl") if isinstance(dsl_payload, dict) else None
        dsl_score = score_dsl(predicted_dsl, case_expected_dsl(case))
        agent_tool_sql = str(dsl_payload.get("sql") or "") if dsl_payload else ""
        compile_state = await compile_or_reuse_agent_sql(
            args=args,
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            predicted_dsl=predicted_dsl,
            dsl_payload=dsl_payload,
            agent_tool_sql=agent_tool_sql,
        )
        compile_success = bool(compile_state["compile_success"])
        generated_sql = str(compile_state["generated_sql"])
        compile_result = compile_state["compile_result"]
        execution_success, execution_error = (None, "")
        if args.local_compile:
            execution_success, execution_error = await maybe_execute_generated_sql(
                args=args,
                compile_success=compile_success,
                generated_sql=generated_sql,
                datasource_id=datasource_id,
            )
        else:
            execution_success = summarize_execution_success(tool_traces)
        stream_error = stream_error or execution_error
        error = resolve_live_case_error(
            stream_error=stream_error,
            dsl_trace=dsl_trace,
            compile_success=compile_success,
            compile_result=compile_result,
        )
        sql_score = score_sql(generated_sql, case_gold_sql(case))
        return build_scored_live_case_result(
            case=case,
            question=question,
            started_at=started_at,
            conversation_id=conversation_id,
            dsl_trace=dsl_trace,
            dsl_score=dsl_score,
            sql_score=sql_score,
            compile_success=compile_success,
            execution_success=execution_success,
            generated_sql=generated_sql,
            agent_tool_sql=agent_tool_sql,
            error=error,
            tool_traces=tool_traces,
        )
    except Exception as exc:
        return build_failed_live_case_result(
            case=case,
            question=question,
            started_at=started_at,
            conversation_id=conversation_id,
            error=exc,
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


def collect_live_report_counts(results: list[LiveCaseResult]) -> dict[str, Any]:
    scored = [item for item in results if item.status == "scored"]
    sql_scored = [item for item in scored if item.sql_score.get("sql_exact_match") is not None]
    executable = [item for item in results if item.execution_success is not None]
    return {
        "total": len(results),
        "scored": scored,
        "sql_scored": sql_scored,
        "executable": executable,
        "dsl_exact": sum(1 for item in scored if item.dsl_score.get("dsl_exact_match")),
        "sql_exact": sum(1 for item in sql_scored if item.sql_score.get("sql_exact_match")),
        "compile_success": sum(1 for item in scored if item.compile_success),
        "execution_success": sum(1 for item in executable if item.execution_success),
    }


def calculate_semantic_accuracy(scored: list[LiveCaseResult]) -> float:
    if not scored:
        return 0.0
    return sum(float(item.dsl_score.get("semantic_accuracy") or 0) for item in scored) / len(scored)


def build_live_summary(counts: dict[str, Any]) -> dict[str, Any]:
    scored = counts["scored"]
    sql_scored = counts["sql_scored"]
    executable = counts["executable"]
    semantic_accuracy = calculate_semantic_accuracy(scored)
    return {
        "total": counts["total"],
        "scored": len(scored),
        "failed": counts["total"] - len(scored),
        "semantic_accuracy": semantic_accuracy,
        "compile_success_count": counts["compile_success"],
        "dsl2sql_compile_success_rate": counts["compile_success"] / len(scored) if scored else 0.0,
        "execution_checked": len(executable),
        "execution_success_count": counts["execution_success"],
        "sql_execution_success_rate": counts["execution_success"] / len(executable) if executable else None,
        "debug_metrics": {
            "dsl_exact_match_count": counts["dsl_exact"],
            "dsl_exact_match_rate": counts["dsl_exact"] / len(scored) if scored else 0.0,
            "sql_exact_match_count": counts["sql_exact"],
            "sql_exact_match_rate": counts["sql_exact"] / len(sql_scored) if sql_scored else None,
            "avg_dsl_slot_accuracy": semantic_accuracy,
        },
    }


def build_live_report(results: list[LiveCaseResult], include_traces: bool) -> dict[str, Any]:
    counts = collect_live_report_counts(results)
    return {
        "summary": build_live_summary(counts),
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
        selected = selected[: args.limit]
    if not selected:
        raise ValueError("没有匹配的评估用例")
    return selected
