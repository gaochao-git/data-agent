"""Evaluation routes for ChatData."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import JSONResponse, StreamingResponse

from utils.error_codes import ErrorCode, chatdata_http_exception_business_code
from utils.logger import get_logger
from utils.response import business_response

from ..schema import (
    AccuracyEvaluationCasesUpsertRequest,
    AccuracyEvaluationDraftCaseRequest,
    AccuracyEvaluationRunRequest,
)
from ..services.datasource.manager import (
    get_datasource_manager,  # noqa: F401  # 测试按模块属性引用 get_datasource_manager，属有意重导出
)
from ..services.evaluation_service import (
    EvaluationService,
    accuracy_bp,
    build_evaluation_service,
    case_row_to_payload,
    eval_run_case_row_to_payload,
    eval_run_row_to_payload,
    format_datetime,
    is_eval_case_passed,
    safe_json_loads,
    sanitize_scene_segment,
    scene_case_paths,
    scene_case_set_id,
    validate_eval_case_ids,
)
from ..services.semantic.core.common import is_time_logical_dimension
from ..services.semantic.run.semantic_config_service import can_mutate_semantic_config, get_semantic_config_service
from .dependencies import _get_current_user

if TYPE_CHECKING:
    from ..evaluate.accuracy.runner import AccuracyRunConfig

logger = get_logger(__name__)
from . import JSON_API_RESPONSES, SSE_RESPONSES

router = APIRouter(tags=["ChatData 语义评估"], responses=JSON_API_RESPONSES)
# 原在此处定义的 ACCURACY_QUERY_TYPES / ACCURACY_DIFFICULTIES 与 evaluation_service 中同名
# 常量同值，且本模块内无任何引用（全仓亦无其它引用者），属死代码，故连同末尾的 re-export
# 一并删除；权威定义保留在 services/evaluation_service.py。
SEMANTIC_CONFIG_NOT_FOUND_CODE = ErrorCode.CHATDATA_NOT_FOUND


def _build_evaluation_service() -> EvaluationService:
    """构建评测服务。"""
    return build_evaluation_service()


def _evaluation_http_exception_code(exc: HTTPException) -> int:
    """评测路由 HTTPException 固定业务码映射。"""
    if exc.status_code == http_status.HTTP_404_NOT_FOUND:
        return SEMANTIC_CONFIG_NOT_FOUND_CODE
    return chatdata_http_exception_business_code(exc.status_code)


def _sanitize_scene_segment(value: str, fallback: str) -> str:
    return sanitize_scene_segment(value, fallback)


def _scene_case_paths(datasource_id: str, semantic_config_id: str) -> dict[str, str]:
    """解析当前场景标识。"""
    return scene_case_paths(datasource_id, semantic_config_id)


def _scene_case_set_id(datasource_id: str, semantic_config_id: str) -> str:
    """构造当前指标集评测测试集 ID。"""
    return scene_case_set_id(datasource_id, semantic_config_id)


def _safe_json_loads(text: str | None, default: Any = None) -> Any:
    """安全解析 JSON 字符串。"""
    return safe_json_loads(text, default)


def _case_row_to_payload(row: Any) -> dict[str, Any]:
    """将评测用例行转换为接口 7 元组。"""
    return case_row_to_payload(row)


def _validate_eval_case_ids(cases: list[dict[str, Any]]) -> None:
    """校验评测用例 ID 不能为空且不能重复。"""
    validate_eval_case_ids(cases)


async def _load_scene_case_set(datasource_id: str, semantic_config_id: str) -> dict[str, Any]:
    """从数据库读取当前场景测试集。"""
    return await _build_evaluation_service().load_scene_case_set(datasource_id, semantic_config_id)


async def _save_scene_case_set(
    datasource_id: str,
    semantic_config_id: str,
    case_set_name: str,
    business_domain: str,
    cases: list[dict[str, Any]],
    operator_id: str | None = None,
    operator_name: str | None = None,
) -> str:
    """保存当前场景测试集到数据库。"""
    return await _build_evaluation_service().save_scene_case_set(
        datasource_id,
        semantic_config_id,
        case_set_name,
        business_domain,
        cases,
        operator_id=operator_id,
        operator_name=operator_name,
    )


async def _load_scene_runner_cases(datasource_id: str, semantic_config_id: str) -> list[dict[str, Any]]:
    """读取 runner 使用的当前场景数据库测试集。"""
    return await _build_evaluation_service().load_scene_runner_cases(datasource_id, semantic_config_id)


def _metric_names(config: dict[str, Any]) -> list[str]:
    return [
        str(item.get("name") or "").strip()
        for item in config.get("metrics") or []
        if str(item.get("name") or "").strip()
    ]


def _dimension_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in config.get("dimensions") or [] if str(item.get("name") or "").strip()]


def _time_dimensions(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _dimension_items(config) if is_time_logical_dimension(item)]


def _dimension_match_text(item: dict[str, Any]) -> str:
    """提取维度字段的匹配文本（小写）。"""
    return str(item.get("name") or item.get("field_name") or item.get("column") or "").lower()


def _dimension_matches_any_token(item: dict[str, Any], tokens: Sequence[str]) -> bool:
    """判断维度字段文本是否包含任一关键词。"""
    text = _dimension_match_text(item)
    return any(token in text for token in tokens)


def _categorical_dimensions(config: dict[str, Any]) -> list[dict[str, Any]]:
    blocked_tokens = ("id", "编号", "号码", "邮箱", "email", "phone", "手机")
    candidate_tokens = ("状态", "分类", "类型", "名称", "status", "category", "type", "name")
    candidates = [item for item in _dimension_items(config) if not is_time_logical_dimension(item)]
    preferred = [
        item
        for item in candidates
        if str(item.get("allowed_values") or "").strip() or _dimension_matches_any_token(item, candidate_tokens)
    ]
    return [item for item in (preferred or candidates) if not _dimension_matches_any_token(item, blocked_tokens)]


def _build_case_id(config_id: str, index: int) -> str:
    return f"{_sanitize_scene_segment(config_id, 'scene')}_{index:03d}"


def _new_case_payload(
    case_id: str, nl: str, gold_dsl: dict[str, Any], query_type: str, difficulty: str, business_domain: str
) -> dict[str, Any]:
    return {
        "id": case_id,
        "nl": nl,
        "gold_dsl": gold_dsl,
        "gold_sql": "",
        "query_type": query_type,
        "difficulty": difficulty,
        "source": "llm",
        "business_domain": business_domain,
    }


def _next_case_index(cases: list[dict[str, Any]], start_index: int) -> int:
    return start_index + len(cases)


def _dimension_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or "").strip()


def _first_allowed_value(item: dict[str, Any]) -> Any:
    allowed_values = item.get("allowed_values") or []
    return allowed_values[0] if allowed_values else None


def _build_detail_seed_cases(
    config_id: str,
    dimensions: list[dict[str, Any]],
    cat_dims: list[dict[str, Any]],
    business_domain: str,
    start_index: int,
) -> list[dict[str, Any]]:
    detail_dimension_names = [_dimension_name(item) for item in dimensions[:6] if _dimension_name(item)]
    if not detail_dimension_names:
        return []
    cases = [
        _new_case_payload(
            _build_case_id(config_id, start_index),
            "查看明细数据",
            {"metrics": [], "dimensions": detail_dimension_names, "filters": [], "having": [], "order_by": []},
            "filter",
            "easy",
            business_domain,
        )
    ]
    for dimension in cat_dims[:3]:
        dimension_name = _dimension_name(dimension)
        filter_value = _first_allowed_value(dimension)
        if not dimension_name or not filter_value:
            continue
        cases.append(
            _new_case_payload(
                _build_case_id(config_id, start_index + len(cases)),
                f"查看{filter_value}{dimension_name}的明细数据",
                {
                    "metrics": [],
                    "dimensions": detail_dimension_names,
                    "filters": [{"dimension": dimension_name, "operator": "=", "value": filter_value}],
                    "having": [],
                    "order_by": [],
                },
                "filter",
                "medium",
                business_domain,
            )
        )
    return cases


def _build_metric_seed_cases(
    config_id: str, metrics: list[str], business_domain: str, start_index: int
) -> list[dict[str, Any]]:
    return [
        _new_case_payload(
            _build_case_id(config_id, start_index + offset),
            f"查询{metric}",
            {"metrics": [metric], "dimensions": [], "filters": [], "having": [], "order_by": []},
            "aggregation",
            "easy",
            business_domain,
        )
        for offset, metric in enumerate(metrics[:8])
    ]


def _build_dimension_group_seed_cases(
    config_id: str,
    metrics: list[str],
    cat_dims: list[dict[str, Any]],
    business_domain: str,
    start_index: int,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for metric in metrics[:4]:
        for dimension in cat_dims[:3]:
            dimension_name = _dimension_name(dimension)
            if not dimension_name:
                continue
            cases.append(
                _new_case_payload(
                    _build_case_id(config_id, start_index + len(cases)),
                    f"查询各{dimension_name}的{metric}",
                    {"metrics": [metric], "dimensions": [dimension_name], "filters": [], "having": [], "order_by": []},
                    "aggregation",
                    "medium",
                    business_domain,
                )
            )
    return cases


def _build_time_seed_cases(
    config_id: str,
    metrics: list[str],
    time_dims: list[dict[str, Any]],
    business_domain: str,
    start_index: int,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for metric in metrics[:3]:
        for dimension in time_dims[:2]:
            dimension_name = _dimension_name(dimension)
            if not dimension_name:
                continue
            cases.append(
                _new_case_payload(
                    _build_case_id(config_id, start_index + len(cases)),
                    f"查询按{dimension_name}每月统计的{metric}",
                    {
                        "metrics": [metric],
                        "dimensions": [{"name": dimension_name, "time_grain": "month"}],
                        "filters": [],
                        "having": [],
                        "order_by": [],
                    },
                    "aggregation",
                    "medium",
                    business_domain,
                )
            )
    return cases


def _build_filter_seed_cases(
    config_id: str,
    metrics: list[str],
    cat_dims: list[dict[str, Any]],
    business_domain: str,
    start_index: int,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for metric in metrics[:3]:
        for dimension in cat_dims[:3]:
            dimension_name = _dimension_name(dimension)
            filter_value = _first_allowed_value(dimension)
            if not dimension_name or not filter_value:
                continue
            cases.append(
                _new_case_payload(
                    _build_case_id(config_id, start_index + len(cases)),
                    f"查询{filter_value}{dimension_name}的{metric}",
                    {
                        "metrics": [metric],
                        "dimensions": [],
                        "filters": [{"dimension": dimension_name, "operator": "=", "value": filter_value}],
                        "having": [],
                        "order_by": [],
                    },
                    "filter",
                    "medium",
                    business_domain,
                )
            )
    return cases


def _metric_table(config: dict[str, Any], metric: str) -> str:
    return next(
        (str(item.get("table") or "").strip() for item in config.get("metrics") or [] if item.get("name") == metric), ""
    )


def _build_join_seed_cases(
    config_id: str,
    config: dict[str, Any],
    metrics: list[str],
    cat_dims: list[dict[str, Any]],
    business_domain: str,
    start_index: int,
) -> list[dict[str, Any]]:
    if not config.get("relationships"):
        return []
    cases: list[dict[str, Any]] = []
    for metric in metrics[:3]:
        for dimension in cat_dims[:3]:
            dimension_name = _dimension_name(dimension)
            dimension_table = str(dimension.get("table") or "").strip()
            metric_table = _metric_table(config, metric)
            if not dimension_name or not dimension_table or not metric_table or dimension_table == metric_table:
                continue
            cases.append(
                _new_case_payload(
                    _build_case_id(config_id, start_index + len(cases)),
                    f"查询各{dimension_name}的{metric}",
                    {"metrics": [metric], "dimensions": [dimension_name], "filters": [], "having": [], "order_by": []},
                    "join",
                    "hard",
                    business_domain,
                )
            )
            break
        if start_index + len(cases) > 30:
            break
    return cases


def _dedupe_seed_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_questions: set[str] = set()
    for case in cases:
        question = str(case.get("nl") or "").strip()
        if not question or question in seen_questions:
            continue
        seen_questions.add(question)
        deduped.append(case)
    return deduped[:30]


def _build_seed_case_candidates(config_id: str, config: dict[str, Any], business_domain: str) -> list[dict[str, Any]]:
    """基于当前语义配置生成黄金集草稿。"""
    metrics = _metric_names(config)
    dimensions = _dimension_items(config)
    time_dims = _time_dimensions(config)
    cat_dims = _categorical_dimensions(config)
    cases: list[dict[str, Any]] = []
    if not metrics and dimensions:
        cases.extend(_build_detail_seed_cases(config_id, dimensions, cat_dims, business_domain, 1))
    cases.extend(_build_metric_seed_cases(config_id, metrics, business_domain, _next_case_index(cases, 1)))
    cases.extend(
        _build_dimension_group_seed_cases(config_id, metrics, cat_dims, business_domain, _next_case_index(cases, 1))
    )
    cases.extend(_build_time_seed_cases(config_id, metrics, time_dims, business_domain, _next_case_index(cases, 1)))
    cases.extend(_build_filter_seed_cases(config_id, metrics, cat_dims, business_domain, _next_case_index(cases, 1)))
    cases.extend(
        _build_join_seed_cases(config_id, config, metrics, cat_dims, business_domain, _next_case_index(cases, 1))
    )
    return _dedupe_seed_cases(cases)


# 草稿生成的 LLM 编排已下沉 evaluation_service；此处保留兼容导出，
# 供端点与既有测试按原模块属性名引用（monkeypatch 语义不变）。
from ..services.evaluation_service import (  # noqa: E402
    # 两个下划线别名在本模块未直接使用，但测试按 routers.evaluation._xxx 打桩，属有意重导出
    generate_draft_case_by_llm as _generate_draft_case_by_llm,  # noqa: F401
)
from ..services.evaluation_service import (
    looks_like_sql as _looks_like_sql,  # noqa: F401
)


async def _build_accuracy_runner_args(payload: AccuracyEvaluationRunRequest, current_user: Any) -> AccuracyRunConfig:
    """构造评测 runner 参数。"""
    from ..evaluate.accuracy.runner import AccuracyRunConfig

    if not payload.datasource_id or not payload.semantic_config_id:
        raise ValueError("评测需要指定数据源和指标集")
    return AccuracyRunConfig(
        query_mode=payload.query_mode,
        model_id=payload.model_id,
        datasource_id=payload.datasource_id,
        semantic_config_id=payload.semantic_config_id,
        user_id=str(current_user.user_id),
        request_timeout=payload.request_timeout,
        include_traces=payload.include_traces,
        local_compile=payload.local_compile,
        skip_execution=payload.skip_execution,
        limit=payload.limit,
        case_id=payload.case_ids or None,
    )


async def _load_accuracy_runner_cases(payload: AccuracyEvaluationRunRequest, args: Any) -> list[dict[str, Any]]:
    """读取当前指标集数据库评测用例。"""
    from ..evaluate.accuracy.runner import select_cases, validate_cases

    if not payload.datasource_id or not payload.semantic_config_id:
        raise ValueError("评测需要指定数据源和指标集")
    cases = await _load_scene_runner_cases(payload.datasource_id, payload.semantic_config_id)
    validate_cases(cases, Path("chat_data_eval_case"))
    return select_cases(cases, args)


async def _run_accuracy_evaluation_payload(
    payload: AccuracyEvaluationRunRequest, request: Request, current_user: Any
) -> dict[str, Any]:
    """执行准确率评测并返回报告。"""
    from ..evaluate.accuracy.runner import run_live_score

    run_id = uuid.uuid4().hex
    await _create_eval_run_record(run_id, payload, current_user)
    try:
        args = await _build_accuracy_runner_args(payload, current_user)
        cases = await _load_accuracy_runner_cases(payload, args)
        report = await run_live_score(args, cases)
    except Exception as exc:
        await _update_eval_run_record(run_id, status="failed", error_message=str(exc))
        raise
    user_id = str(getattr(current_user, "user_id", "") or "").strip()
    username = str(getattr(current_user, "username", "") or "").strip()
    for result in report.get("results") or []:
        await _upsert_eval_run_case_record(
            run_id, str(result.get("id") or ""), result, operator_id=user_id, operator_name=username
        )
    await _update_eval_run_record(run_id, status="succeeded", summary=report.get("summary") or {})
    return report


async def _persist_accuracy_event_record(
    run_id: str, event: dict[str, Any], operator_id: str, operator_name: str
) -> None:
    """按事件类型落库评测运行记录；其他事件类型忽略。"""
    event_name = str(event.get("event") or "")
    if event_name == "case_result":
        await _upsert_eval_run_case_record(
            run_id,
            str(event.get("id") or ""),
            event.get("result") or {},
            operator_id=operator_id,
            operator_name=operator_name,
        )
    elif event_name == "progress":
        await _update_eval_run_record(run_id, status="running", summary=event.get("summary") or {})
    elif event_name == "done":
        report = event.get("report") or {}
        await _update_eval_run_record(run_id, status="succeeded", summary=report.get("summary") or {})


async def _iter_accuracy_evaluation_sse(
    payload: AccuracyEvaluationRunRequest, request: Request, current_user: Any
) -> AsyncIterator[str]:
    """生成准确率评测 SSE 数据。"""
    from ..evaluate.accuracy.runner import iter_accuracy_evaluation_events

    run_id = uuid.uuid4().hex
    try:
        await _create_eval_run_record(run_id, payload, current_user)
        args = await _build_accuracy_runner_args(payload, current_user)
        cases = await _load_accuracy_runner_cases(payload, args)
        user_id = str(getattr(current_user, "user_id", "") or "").strip()
        username = str(getattr(current_user, "username", "") or "").strip()
        async for event in iter_accuracy_evaluation_events(args, cases):
            await _persist_accuracy_event_record(run_id, event, user_id, username)
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    except asyncio.CancelledError:
        await _update_eval_run_record(run_id, status="cancelled", error_message="评测已停止")
        raise
    except ValueError as exc:
        await _update_eval_run_record(run_id, status="failed", error_message=str(exc))
        yield f"data: {json.dumps({'event': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
    except Exception as exc:
        await _update_eval_run_record(run_id, status="failed", error_message=str(exc))
        logger.error("智能问数流式评测失败: %s", exc, exc_info=True)
        yield f"data: {json.dumps({'event': 'error', 'message': f'评测失败: {exc}'}, ensure_ascii=False)}\n\n"


def _accuracy_sse_response(
    payload: AccuracyEvaluationRunRequest, request: Request, current_user: Any
) -> StreamingResponse:
    """返回准确率评测 SSE 响应。"""
    return StreamingResponse(
        _iter_accuracy_evaluation_sse(payload, request, current_user),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _accuracy_bp(value: Any) -> int:
    """将 0-1 准确率转换为万分比整数。"""
    return accuracy_bp(value)


def _is_eval_case_passed(result: dict[str, Any]) -> bool:
    """判断单条评测结果是否通过。"""
    return is_eval_case_passed(result)


async def _create_eval_run_record(run_id: str, payload: AccuracyEvaluationRunRequest, current_user: Any) -> None:
    """创建评测运行记录。"""
    await _build_evaluation_service().create_eval_run_record(run_id, payload, current_user)


async def _upsert_eval_run_case_record(
    run_id: str,
    case_id_value: str,
    result: dict[str, Any],
    operator_id: str | None = None,
    operator_name: str | None = None,
) -> None:
    """保存单条评测用例结果。"""
    await _build_evaluation_service().upsert_eval_run_case_record(
        run_id,
        case_id_value,
        result,
        operator_id=operator_id,
        operator_name=operator_name,
    )


async def _update_eval_run_record(
    run_id: str,
    *,
    status: str | None = None,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    """更新评测运行汇总。"""
    await _build_evaluation_service().update_eval_run_record(
        run_id,
        status=status,
        summary=summary,
        error_message=error_message,
    )


def _format_datetime(value: Any) -> str | None:
    """格式化接口返回时间。"""
    return format_datetime(value)


def _eval_run_row_to_payload(row: Any) -> dict[str, Any]:
    """转换评测运行记录。"""
    return eval_run_row_to_payload(row)


def _eval_run_case_row_to_payload(row: Any) -> dict[str, Any]:
    """转换评测单用例结果。"""
    return eval_run_case_row_to_payload(row)


async def _ensure_evaluation_semantic_permission(
    datasource_id: str,
    config_id: str,
    current_user: Any,
    *,
    admin_manage: bool = False,
) -> dict[str, Any]:
    """评测入口按指标集维护权限校验。"""
    config = await get_semantic_config_service().get_config_async(datasource_id, config_id)
    if not config:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"语义配置不存在: {config_id}")
    user_id = str(getattr(current_user, "user_id", "") or "").strip()
    if not can_mutate_semantic_config(config, user_id, admin_manage):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="无权限操作该指标集")
    return config


async def _accuracy_error_stream(message: str) -> AsyncIterator[str]:
    yield f"data: {json.dumps({'event': 'error', 'message': message}, ensure_ascii=False)}\n\n"


async def _run_accuracy_evaluation_stream_route(
    payload: AccuracyEvaluationRunRequest, request: Request, *, admin_manage: bool = False
) -> StreamingResponse:
    """流式运行准确率评测，逐条返回用例结果。"""
    try:
        current_user = _get_current_user(request)
        await _ensure_evaluation_semantic_permission(
            payload.datasource_id or "", payload.semantic_config_id or "", current_user, admin_manage=admin_manage
        )
    except (HTTPException, ValueError) as e:
        message = str(getattr(e, "detail", None) or e)
        return StreamingResponse(_accuracy_error_stream(message), media_type="text/event-stream")
    return _accuracy_sse_response(payload, request, current_user)


async def _list_accuracy_evaluation_runs_route(
    request: Request,
    *,
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
    admin_manage: bool = False,
) -> JSONResponse:
    """按当前维护入口查询评测历史批次。"""
    try:
        if not datasource_id or not semantic_config_id:
            return business_response(
                msg="查询评测历史需要指定数据源和指标集", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error"
            )
        current_user = _get_current_user(request)
        await _ensure_evaluation_semantic_permission(
            datasource_id, semantic_config_id, current_user, admin_manage=admin_manage
        )
        data = await _build_evaluation_service().list_eval_runs(
            datasource_id=datasource_id,
            semantic_config_id=semantic_config_id,
            page=page,
            page_size=page_size,
        )
        return business_response(data=data, msg="获取成功")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=_evaluation_http_exception_code(e), status="error")
    except Exception as e:
        logger.error(
            "获取智能问数评测历史失败: datasource_id=%s config_id=%s error=%s",
            datasource_id,
            semantic_config_id,
            e,
            exc_info=True,
        )
        return business_response(
            msg="获取评测历史失败，请稍后重试", code=ErrorCode.CHATDATA_EVALUATION_HISTORY_FAILED, status="error"
        )


async def _get_accuracy_evaluation_run_detail_route(
    run_id: str, request: Request, *, admin_manage: bool = False
) -> JSONResponse:
    """按当前维护入口查询评测历史详情。"""
    try:
        data = await _build_evaluation_service().get_eval_run_detail(run_id)
        if not data:
            return business_response(msg="评测记录不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        run = data.get("run") or {}
        current_user = _get_current_user(request)
        await _ensure_evaluation_semantic_permission(
            str(run.get("datasource_id") or ""),
            str(run.get("semantic_config_id") or ""),
            current_user,
            admin_manage=admin_manage,
        )
        return business_response(data=data, msg="获取成功")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=_evaluation_http_exception_code(e), status="error")
    except Exception as e:
        logger.error("获取智能问数评测历史详情失败: run_id=%s error=%s", run_id, e, exc_info=True)
        return business_response(
            msg="获取评测历史详情失败，请稍后重试", code=ErrorCode.CHATDATA_EVALUATION_DETAIL_FAILED, status="error"
        )


@router.post("/evaluations/accuracy/run-stream", responses=SSE_RESPONSES)
async def run_accuracy_evaluation_stream(payload: AccuracyEvaluationRunRequest, request: Request) -> StreamingResponse:
    """流式运行个人指标集准确率评测，逐条返回用例结果。"""
    return await _run_accuracy_evaluation_stream_route(payload, request, admin_manage=False)


@router.post("/admin/evaluations/accuracy/run-stream", responses=SSE_RESPONSES)
async def run_admin_accuracy_evaluation_stream(
    payload: AccuracyEvaluationRunRequest, request: Request
) -> StreamingResponse:
    """后台流式运行公共指标集准确率评测，逐条返回用例结果。"""
    try:
        _get_current_user(request)
    except (HTTPException, ValueError) as e:
        message = str(getattr(e, "detail", None) or e)
        return StreamingResponse(_accuracy_error_stream(message), media_type="text/event-stream")
    return await _run_accuracy_evaluation_stream_route(payload, request, admin_manage=True)


@router.get("/evaluations/accuracy/runs")
async def list_accuracy_evaluation_runs(
    request: Request,
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> JSONResponse:
    """查询个人指标集评测历史批次。"""
    return await _list_accuracy_evaluation_runs_route(
        request,
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        page=page,
        page_size=page_size,
        admin_manage=False,
    )


@router.get("/admin/evaluations/accuracy/runs")
async def list_admin_accuracy_evaluation_runs(
    request: Request,
    datasource_id: str | None = None,
    semantic_config_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> JSONResponse:
    """后台查询公共指标集评测历史批次。"""
    _get_current_user(request)
    return await _list_accuracy_evaluation_runs_route(
        request,
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        page=page,
        page_size=page_size,
        admin_manage=True,
    )


@router.get("/evaluations/accuracy/runs/{run_id}")
async def get_accuracy_evaluation_run_detail(run_id: str, request: Request) -> JSONResponse:
    """查询个人指标集评测历史详情。"""
    return await _get_accuracy_evaluation_run_detail_route(run_id, request, admin_manage=False)


@router.get("/admin/evaluations/accuracy/runs/{run_id}")
async def get_admin_accuracy_evaluation_run_detail(run_id: str, request: Request) -> JSONResponse:
    """后台查询公共指标集评测历史详情。"""
    _get_current_user(request)
    return await _get_accuracy_evaluation_run_detail_route(run_id, request, admin_manage=True)


async def _compile_scene_case_gold_sql(
    datasource_id: str, semantic_config_id: str, case_payload: dict[str, Any]
) -> dict[str, Any]:
    """为当前场景用例编译标准 SQL。"""
    from ..evaluate.accuracy.runner import compile_dsl_locally

    cloned = deepcopy(case_payload)
    compile_result = await compile_dsl_locally(
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        dsl=cloned.get("gold_dsl"),
    )
    cloned["gold_sql"] = str(compile_result.get("sql") or "")
    if not compile_result.get("success"):
        raise ValueError(f"用例 {cloned.get('id')} DSL 编译失败: {compile_result.get('error') or '未知错误'}")
    return cloned


async def _get_scene_evaluation_cases_route(
    datasource_id: str, config_id: str, request: Request, *, admin_manage: bool = False
) -> JSONResponse:
    """读取当前指标集场景的评测测试集。"""
    try:
        current_user = _get_current_user(request)
        await _ensure_evaluation_semantic_permission(datasource_id, config_id, current_user, admin_manage=admin_manage)
        scene_paths = _scene_case_paths(datasource_id, config_id)
        loaded = await _load_scene_case_set(datasource_id, config_id)
        cases = loaded.get("cases") or []
        business_domain = loaded.get("business_domain") or (
            cases[0].get("business_domain") if cases else _sanitize_scene_segment(config_id, "scene")
        )
        return business_response(
            data={
                "cases": cases,
                "total": len(cases),
                "business_domain": business_domain,
                "scene_id": scene_paths["scene_id"],
            },
            msg="获取成功",
        )
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=_evaluation_http_exception_code(e), status="error")
    except Exception as e:
        logger.error(
            "获取场景评测测试集失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True
        )
        return business_response(
            msg=f"获取测试集失败: {e}", code=ErrorCode.CHATDATA_EVALUATION_CASE_LIST_FAILED, status="error"
        )


async def _save_scene_evaluation_cases_route(
    datasource_id: str,
    config_id: str,
    payload: AccuracyEvaluationCasesUpsertRequest,
    request: Request,
    *,
    admin_manage: bool = False,
) -> JSONResponse:
    """保存当前指标集场景的评测测试集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(getattr(current_user, "user_id", "") or "").strip()
        username = str(getattr(current_user, "username", "") or "").strip()
        config = await _ensure_evaluation_semantic_permission(
            datasource_id, config_id, current_user, admin_manage=admin_manage
        )
        cases = [item.model_dump() for item in payload.cases]
        business_domain = str(payload.business_domain or _sanitize_scene_segment(config_id, "scene")).strip()
        compiled_cases: list[dict[str, Any]] = []
        for case in cases:
            case["business_domain"] = business_domain
            compiled_cases.append(await _compile_scene_case_gold_sql(datasource_id, config_id, case))
        scene_paths = _scene_case_paths(datasource_id, config_id)
        await _save_scene_case_set(
            datasource_id,
            config_id,
            str((config or {}).get("name") or config_id),
            business_domain,
            compiled_cases,
            operator_id=user_id,
            operator_name=username,
        )
        return business_response(
            data={
                "cases": compiled_cases,
                "total": len(compiled_cases),
                "business_domain": business_domain,
                "scene_id": scene_paths["scene_id"],
            },
            msg="保存成功",
        )
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=_evaluation_http_exception_code(e), status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error(
            "保存场景评测测试集失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True
        )
        return business_response(
            msg=f"保存测试集失败: {e}", code=ErrorCode.CHATDATA_EVALUATION_CASE_SAVE_FAILED, status="error"
        )


async def _generate_scene_evaluation_cases_route(
    datasource_id: str, config_id: str, request: Request, *, admin_manage: bool = False
) -> JSONResponse:
    """按当前语义配置生成场景评测测试集草稿，不直接保存。"""
    try:
        current_user = _get_current_user(request)
        config = await _ensure_evaluation_semantic_permission(
            datasource_id, config_id, current_user, admin_manage=admin_manage
        )
        business_domain = _sanitize_scene_segment(config.get("name") or config_id, "scene")
        draft_cases = _build_seed_case_candidates(config_id, config, business_domain)
        compiled_cases: list[dict[str, Any]] = []
        for case in draft_cases:
            compiled_cases.append(await _compile_scene_case_gold_sql(datasource_id, config_id, case))
        scene_paths = _scene_case_paths(datasource_id, config_id)
        return business_response(
            data={
                "cases": compiled_cases,
                "total": len(compiled_cases),
                "business_domain": business_domain,
                "scene_id": scene_paths["scene_id"],
            },
            msg="生成草稿成功",
        )
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=_evaluation_http_exception_code(e), status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error(
            "生成场景评测测试集失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True
        )
        return business_response(
            msg=f"生成测试集失败: {e}", code=ErrorCode.CHATDATA_EVALUATION_HISTORY_FAILED, status="error"
        )


async def _draft_scene_evaluation_case_route(
    datasource_id: str,
    config_id: str,
    payload: AccuracyEvaluationDraftCaseRequest,
    request: Request,
    *,
    admin_manage: bool = False,
) -> JSONResponse:
    """用 LLM 生成单条当前场景评测用例草稿。"""
    try:
        current_user = _get_current_user(request)
        config = await _ensure_evaluation_semantic_permission(
            datasource_id, config_id, current_user, admin_manage=admin_manage
        )
        case = await _generate_draft_case_by_llm(config_id, config, payload)
        compiled_case = await _compile_scene_case_gold_sql(datasource_id, config_id, case)
        return business_response(data={"case": compiled_case}, msg="生成成功")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=_evaluation_http_exception_code(e), status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error(
            "生成单条评测用例草稿失败: datasource_id=%s config_id=%s error=%s",
            datasource_id,
            config_id,
            e,
            exc_info=True,
        )
        return business_response(
            msg=f"生成用例草稿失败: {e}", code=ErrorCode.CHATDATA_EVALUATION_DETAIL_FAILED, status="error"
        )


@router.get("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases")
async def get_scene_evaluation_cases(datasource_id: str, config_id: str, request: Request) -> JSONResponse:
    """读取个人指标集场景的评测测试集。"""
    return await _get_scene_evaluation_cases_route(datasource_id, config_id, request, admin_manage=False)


@router.get("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases")
async def get_admin_scene_evaluation_cases(datasource_id: str, config_id: str, request: Request) -> JSONResponse:
    """后台读取公共指标集场景的评测测试集。"""
    _get_current_user(request)
    return await _get_scene_evaluation_cases_route(datasource_id, config_id, request, admin_manage=True)


@router.put("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases")
async def save_scene_evaluation_cases(
    datasource_id: str, config_id: str, payload: AccuracyEvaluationCasesUpsertRequest, request: Request
) -> JSONResponse:
    """保存个人指标集场景的评测测试集。"""
    return await _save_scene_evaluation_cases_route(datasource_id, config_id, payload, request, admin_manage=False)


@router.put("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases")
async def save_admin_scene_evaluation_cases(
    datasource_id: str, config_id: str, payload: AccuracyEvaluationCasesUpsertRequest, request: Request
) -> JSONResponse:
    """后台保存公共指标集场景的评测测试集。"""
    _get_current_user(request)
    return await _save_scene_evaluation_cases_route(datasource_id, config_id, payload, request, admin_manage=True)


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases/generate")
async def generate_scene_evaluation_cases(datasource_id: str, config_id: str, request: Request) -> JSONResponse:
    """按个人指标集生成场景评测测试集草稿，不直接保存。"""
    return await _generate_scene_evaluation_cases_route(datasource_id, config_id, request, admin_manage=False)


@router.post("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases/generate")
async def generate_admin_scene_evaluation_cases(datasource_id: str, config_id: str, request: Request) -> JSONResponse:
    """后台按公共指标集生成场景评测测试集草稿，不直接保存。"""
    _get_current_user(request)
    return await _generate_scene_evaluation_cases_route(datasource_id, config_id, request, admin_manage=True)


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases/draft-case")
async def draft_scene_evaluation_case(
    datasource_id: str, config_id: str, payload: AccuracyEvaluationDraftCaseRequest, request: Request
) -> JSONResponse:
    """用 LLM 生成个人指标集评测用例草稿。"""
    return await _draft_scene_evaluation_case_route(datasource_id, config_id, payload, request, admin_manage=False)


@router.post("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases/draft-case")
async def draft_admin_scene_evaluation_case(
    datasource_id: str, config_id: str, payload: AccuracyEvaluationDraftCaseRequest, request: Request
) -> JSONResponse:
    """后台用 LLM 生成公共指标集评测用例草稿。"""
    _get_current_user(request)
    return await _draft_scene_evaluation_case_route(datasource_id, config_id, payload, request, admin_manage=True)
