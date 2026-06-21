"""Evaluation routes for ChatData."""

from __future__ import annotations

import hashlib
import asyncio
import json
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from sqlalchemy import and_, func, select

from utils.lang_util.database import get_session
from utils.lang_util.models import ChatDataEvalCase, ChatDataEvalCaseSet, ChatDataEvalRun, ChatDataEvalRunCase
from utils.logger import get_logger
from ..agent.prompts.generate_evaluate_set import build_evaluation_draft_case_prompt
from ..schema import AccuracyEvaluationCasesUpsertRequest, AccuracyEvaluationDraftCaseRequest, AccuracyEvaluationRunRequest
from ..semantic.utils import parse_llm_json
from ..services.datasource_manager import get_datasource_manager
from ..services.llm_config import get_chat_data_llm
from .common import _get_current_user, _semantic_config_prompt_payload, business_response

logger = get_logger(__name__)
router = APIRouter()
ACCURACY_QUERY_TYPES = {"filter", "join", "aggregation", "subquery"}
ACCURACY_DIFFICULTIES = {"easy", "medium", "hard"}

def _sanitize_scene_segment(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return text or fallback


def _scene_case_paths(datasource_id: str, semantic_config_id: str) -> Dict[str, str]:
    """解析当前场景标识。"""
    ds_segment = _sanitize_scene_segment(datasource_id, "datasource")
    config_segment = _sanitize_scene_segment(semantic_config_id, "semantic_config")
    scene_id = f"{ds_segment}__{config_segment}"
    return {"scene_id": scene_id}


def _scene_case_set_id(datasource_id: str, semantic_config_id: str) -> str:
    """构造当前指标集评测测试集 ID。"""
    return hashlib.sha256(f"{datasource_id}::{semantic_config_id}".encode("utf-8")).hexdigest()[:32]


def _safe_json_loads(text: Optional[str], default: Any = None) -> Any:
    """安全解析 JSON 字符串。"""
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _case_row_to_payload(row: ChatDataEvalCase) -> Dict[str, Any]:
    """将评测用例行转换为接口 7 元组。"""
    return {
        "id": row.case_id,
        "nl": row.nl_text,
        "gold_dsl": _safe_json_loads(row.gold_dsl_text, {}) or {},
        "gold_sql": row.gold_sql_text or "",
        "query_type": row.query_type or "aggregation",
        "difficulty": row.case_difficulty or "easy",
        "source": "llm" if row.case_source == "model_generated" else (row.case_source or "human"),
        "business_domain": row.business_domain or "",
    }


def _validate_eval_case_ids(cases: List[Dict[str, Any]]) -> None:
    """校验评测用例 ID 不能为空且不能重复。"""
    seen: Set[str] = set()
    for index, case in enumerate(cases, start=1):
        case_id_value = str(case.get("id") or "").strip()
        if not case_id_value:
            raise ValueError(f"第 {index} 条用例缺少用例ID")
        if case_id_value in seen:
            raise ValueError(f"用例ID重复: {case_id_value}")
        seen.add(case_id_value)


async def _load_scene_case_set(datasource_id: str, semantic_config_id: str) -> Dict[str, Any]:
    """从数据库读取当前场景测试集。"""
    case_set_id = _scene_case_set_id(datasource_id, semantic_config_id)
    async with get_session() as db:
        set_result = await db.execute(
            select(ChatDataEvalCaseSet).filter_by(case_set_id=case_set_id, delete_flag=0)
        )
        case_set = set_result.scalar_one_or_none()
        if not case_set:
            return {"case_set_id": case_set_id, "business_domain": "", "cases": []}
        case_result = await db.execute(
            select(ChatDataEvalCase)
            .filter_by(case_set_id=case_set_id, delete_flag=0, enabled_flag=1)
            .order_by(ChatDataEvalCase.sort_no.asc(), ChatDataEvalCase.id.asc())
        )
        cases = [_case_row_to_payload(row) for row in case_result.scalars().all()]
        return {"case_set_id": case_set_id, "business_domain": case_set.business_domain or "", "cases": cases}


async def _save_scene_case_set(
    datasource_id: str,
    semantic_config_id: str,
    case_set_name: str,
    business_domain: str,
    cases: List[Dict[str, Any]],
    operator_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> str:
    """保存当前场景测试集到数据库。"""
    case_set_id = _scene_case_set_id(datasource_id, semantic_config_id)
    operator_user_id = str(operator_id or "").strip()
    operator_username = str(operator_name or "").strip()
    now = datetime.now()
    _validate_eval_case_ids(cases)
    async with get_session() as db:
        set_result = await db.execute(select(ChatDataEvalCaseSet).filter_by(case_set_id=case_set_id))
        case_set = set_result.scalar_one_or_none()
        if case_set:
            case_set.datasource_id = datasource_id
            case_set.semantic_config_id = semantic_config_id
            case_set.case_set_name = case_set_name
            case_set.business_domain = business_domain
            case_set.version_no = int(case_set.version_no or 0) + 1
            case_set.case_count = len(cases)
            case_set.case_set_status = "active"
            case_set.delete_flag = 0
            case_set.update_time = now
            case_set.update_by = operator_user_id
            case_set.update_by_name = operator_username
        else:
            db.add(ChatDataEvalCaseSet(
                case_set_id=case_set_id,
                datasource_id=datasource_id,
                semantic_config_id=semantic_config_id,
                case_set_name=case_set_name,
                business_domain=business_domain,
                version_no=1,
                case_count=len(cases),
                case_set_status="active",
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=operator_user_id,
                create_by_name=operator_username,
                update_by=operator_user_id,
                update_by_name=operator_username,
            ))
        existing_result = await db.execute(select(ChatDataEvalCase).filter_by(case_set_id=case_set_id))
        existing_cases = {row.case_id: row for row in existing_result.scalars().all()}
        next_case_ids = {str(case.get("id") or "").strip() for case in cases}
        for case_id_value, row in existing_cases.items():
            if case_id_value not in next_case_ids:
                row.delete_flag = 1
                row.update_time = now
                row.update_by = operator_user_id
                row.update_by_name = operator_username
        for index, case in enumerate(cases, start=1):
            case_id_value = str(case.get("id") or "").strip()
            row = existing_cases.get(case_id_value)
            if row:
                row.nl_text = str(case.get("nl") or "").strip()
                row.gold_dsl_text = json.dumps(case.get("gold_dsl") or {}, ensure_ascii=False)
                row.gold_sql_text = str(case.get("gold_sql") or "")
                row.query_type = str(case.get("query_type") or "aggregation")
                row.case_difficulty = str(case.get("difficulty") or "easy")
                row.case_source = str(case.get("source") or "human")
                row.business_domain = str(case.get("business_domain") or business_domain)
                row.enabled_flag = 1
                row.sort_no = index
                row.delete_flag = 0
                row.update_time = now
                row.update_by = operator_user_id
                row.update_by_name = operator_username
            else:
                db.add(ChatDataEvalCase(
                    case_set_id=case_set_id,
                    case_id=case_id_value,
                    nl_text=str(case.get("nl") or "").strip(),
                    gold_dsl_text=json.dumps(case.get("gold_dsl") or {}, ensure_ascii=False),
                    gold_sql_text=str(case.get("gold_sql") or ""),
                    query_type=str(case.get("query_type") or "aggregation"),
                    case_difficulty=str(case.get("difficulty") or "easy"),
                    case_source=str(case.get("source") or "human"),
                    business_domain=str(case.get("business_domain") or business_domain),
                    enabled_flag=1,
                    sort_no=index,
                    delete_flag=0,
                    create_time=now,
                    update_time=now,
                    create_by=operator_user_id,
                    create_by_name=operator_username,
                    update_by=operator_user_id,
                    update_by_name=operator_username,
                ))
        await db.commit()
    return case_set_id


async def _load_scene_runner_cases(datasource_id: str, semantic_config_id: str) -> List[Dict[str, Any]]:
    """读取 runner 使用的当前场景数据库测试集。"""
    loaded = await _load_scene_case_set(datasource_id, semantic_config_id)
    cases = loaded.get("cases") or []
    if not cases:
        raise ValueError("当前指标集尚未配置测试集，请先生成测试集草稿并保存后再评测")
    return cases


def _metric_names(config: Dict[str, Any]) -> List[str]:
    return [str(item.get("name") or "").strip() for item in config.get("metrics") or [] if str(item.get("name") or "").strip()]


def _dimension_items(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in config.get("dimensions") or [] if str(item.get("name") or "").strip()]


def _time_dimensions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in _dimension_items(config) if str(item.get("type") or "") == "time"]


def _categorical_dimensions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    blocked_tokens = ("id", "编号", "号码", "邮箱", "email", "phone", "手机")
    candidates = [item for item in _dimension_items(config) if str(item.get("type") or "") == "categorical"]
    preferred = [
        item for item in candidates
        if str(item.get("allowed_values") or "").strip()
        or any(token in str(item.get("name") or item.get("field_name") or item.get("column") or "").lower() for token in ("状态", "分类", "类型", "名称", "status", "category", "type", "name"))
    ]
    return [
        item for item in (preferred or candidates)
        if not any(token in str(item.get("name") or item.get("field_name") or item.get("column") or "").lower() for token in blocked_tokens)
    ]


def _build_case_id(config_id: str, index: int) -> str:
    return f"{_sanitize_scene_segment(config_id, 'scene')}_{index:03d}"


def _new_case_payload(case_id: str, nl: str, gold_dsl: Dict[str, Any], query_type: str, difficulty: str, business_domain: str) -> Dict[str, Any]:
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


def _build_seed_case_candidates(config_id: str, config: Dict[str, Any], business_domain: str) -> List[Dict[str, Any]]:
    """基于当前语义配置生成黄金集草稿。"""
    metrics = _metric_names(config)
    dimensions = _dimension_items(config)
    time_dims = _time_dimensions(config)
    cat_dims = _categorical_dimensions(config)
    cases: List[Dict[str, Any]] = []
    case_index = 1

    if not metrics and dimensions:
        detail_dimension_names = [str(item.get("name") or "").strip() for item in dimensions[:6] if str(item.get("name") or "").strip()]
        if detail_dimension_names:
            cases.append(_new_case_payload(
                _build_case_id(config_id, case_index),
                "查看明细数据",
                {
                    "metrics": [],
                    "dimensions": detail_dimension_names,
                    "filters": [],
                    "having": [],
                    "order_by": [],
                },
                "filter",
                "easy",
                business_domain,
            ))
            case_index += 1
        for dimension in cat_dims[:3]:
            dimension_name = str(dimension.get("name") or "").strip()
            allowed_values = dimension.get("allowed_values") or []
            filter_value = allowed_values[0] if allowed_values else None
            if not dimension_name or not filter_value:
                continue
            cases.append(_new_case_payload(
                _build_case_id(config_id, case_index),
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
            ))
            case_index += 1

    for metric in metrics[:8]:
        cases.append(_new_case_payload(
            _build_case_id(config_id, case_index),
            f"查询{metric}",
            {"metrics": [metric], "dimensions": [], "filters": [], "having": [], "order_by": []},
            "aggregation",
            "easy",
            business_domain,
        ))
        case_index += 1

    for metric in metrics[:4]:
        for dimension in cat_dims[:3]:
            dimension_name = str(dimension.get("name") or "").strip()
            if not dimension_name:
                continue
            cases.append(_new_case_payload(
                _build_case_id(config_id, case_index),
                f"查询各{dimension_name}的{metric}",
                {
                    "metrics": [metric],
                    "dimensions": [dimension_name],
                    "filters": [],
                    "having": [],
                    "order_by": [],
                },
                "aggregation",
                "medium",
                business_domain,
            ))
            case_index += 1

    for metric in metrics[:3]:
        for dimension in time_dims[:2]:
            dimension_name = str(dimension.get("name") or "").strip()
            if not dimension_name:
                continue
            cases.append(_new_case_payload(
                _build_case_id(config_id, case_index),
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
            ))
            case_index += 1

    for metric in metrics[:3]:
        for dimension in cat_dims[:3]:
            dimension_name = str(dimension.get("name") or "").strip()
            allowed_values = dimension.get("allowed_values") or []
            filter_value = allowed_values[0] if allowed_values else None
            if not dimension_name or not filter_value:
                continue
            cases.append(_new_case_payload(
                _build_case_id(config_id, case_index),
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
            ))
            case_index += 1

    if len(config.get("relationships") or []) > 0:
        for metric in metrics[:3]:
            for dimension in cat_dims[:3]:
                dimension_name = str(dimension.get("name") or "").strip()
                dimension_table = str(dimension.get("table") or "").strip()
                metric_table = next((str(item.get("table") or "").strip() for item in config.get("metrics") or [] if item.get("name") == metric), "")
                if not dimension_name or not dimension_table or not metric_table or dimension_table == metric_table:
                    continue
                cases.append(_new_case_payload(
                    _build_case_id(config_id, case_index),
                    f"查询各{dimension_name}的{metric}",
                    {
                        "metrics": [metric],
                        "dimensions": [dimension_name],
                        "filters": [],
                        "having": [],
                        "order_by": [],
                    },
                    "join",
                    "hard",
                    business_domain,
                ))
                case_index += 1
                break
            if case_index > 30:
                break

    deduped: List[Dict[str, Any]] = []
    seen_questions: Set[str] = set()
    for case in cases:
        question = str(case.get("nl") or "").strip()
        if not question or question in seen_questions:
            continue
        seen_questions.add(question)
        deduped.append(case)
    return deduped[:30]


def _looks_like_sql(text: str) -> bool:
    """判断用户输入是否更像 SQL。"""
    normalized = str(text or "").strip().lower()
    return bool(re.search(r"^\s*(select|with)\b", normalized) or re.search(r"\bfrom\b.+\bselect\b|\bselect\b.+\bfrom\b", normalized, flags=re.DOTALL))


async def _generate_draft_case_by_llm(config_id: str, config: Dict[str, Any], payload: AccuracyEvaluationDraftCaseRequest) -> Dict[str, Any]:
    """用 LLM 基于用户问题和当前语义配置生成 gold_dsl 草稿。"""
    user_input = str(payload.hint or "").strip()
    if not user_input:
        raise ValueError("请先输入自然语言问题或 SQL，再生成 gold_dsl")
    is_sql_input = _looks_like_sql(user_input)
    nl_example = json.dumps("请基于 SQL 生成自然语言问题" if is_sql_input else user_input, ensure_ascii=False)
    business_domain = _sanitize_scene_segment(config.get("name") or config_id, "scene")
    prompt = build_evaluation_draft_case_prompt(
        user_input=user_input,
        is_sql_input=is_sql_input,
        nl_example=nl_example,
        business_domain=business_domain,
        existing_questions=payload.existing_questions,
        semantic_config_payload=_semantic_config_prompt_payload(config),
    )
    llm = get_chat_data_llm(streaming=False, temperature=0.2, max_tokens=2000)
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", response)
    parsed = parse_llm_json(str(content), strict=False)
    if not isinstance(parsed, dict) or not parsed.get("nl") or not isinstance(parsed.get("gold_dsl"), dict):
        raise ValueError("大模型未返回合法评测用例 JSON")
    case_id = f"{_sanitize_scene_segment(config_id, 'scene')}_{int(time.time() * 1000)}"
    return {
        "id": case_id,
        "nl": str(parsed.get("nl") or "").strip() if is_sql_input else user_input,
        "gold_dsl": parsed.get("gold_dsl") or {},
        "gold_sql": user_input if is_sql_input else "",
        "query_type": parsed.get("query_type") if parsed.get("query_type") in ACCURACY_QUERY_TYPES else "aggregation",
        "difficulty": parsed.get("difficulty") if parsed.get("difficulty") in ACCURACY_DIFFICULTIES else "medium",
        "source": "llm",
        "business_domain": business_domain,
    }


async def _build_accuracy_runner_args(payload: AccuracyEvaluationRunRequest, current_user: Any):
    """构造评测 runner 参数。"""
    from ..evaluate.accuracy.runner import AccuracyRunConfig

    if not payload.datasource_id or not payload.semantic_config_id:
        raise ValueError("评测需要指定数据源和指标集")
    return AccuracyRunConfig(
        agent_id="chat_data",
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


async def _load_accuracy_runner_cases(payload: AccuracyEvaluationRunRequest, args: Any) -> List[Dict[str, Any]]:
    """读取当前指标集数据库评测用例。"""
    from ..evaluate.accuracy.runner import select_cases, validate_cases

    if not payload.datasource_id or not payload.semantic_config_id:
        raise ValueError("评测需要指定数据源和指标集")
    cases = await _load_scene_runner_cases(payload.datasource_id, payload.semantic_config_id)
    validate_cases(cases, Path("chat_data_eval_case"))
    return select_cases(cases, args)


async def _run_accuracy_evaluation_payload(payload: AccuracyEvaluationRunRequest, request: Request, current_user: Any):
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
        await _upsert_eval_run_case_record(run_id, str(result.get("id") or ""), result, operator_id=user_id, operator_name=username)
    await _update_eval_run_record(run_id, status="succeeded", summary=report.get("summary") or {})
    return report


async def _iter_accuracy_evaluation_sse(payload: AccuracyEvaluationRunRequest, request: Request, current_user: Any):
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
            event_name = str(event.get("event") or "")
            if event_name == "case_result":
                await _upsert_eval_run_case_record(run_id, str(event.get("id") or ""), event.get("result") or {}, operator_id=user_id, operator_name=username)
            elif event_name == "progress":
                await _update_eval_run_record(run_id, status="running", summary=event.get("summary") or {})
            elif event_name == "done":
                report = event.get("report") or {}
                await _update_eval_run_record(run_id, status="succeeded", summary=report.get("summary") or {})
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


def _accuracy_sse_response(payload: AccuracyEvaluationRunRequest, request: Request, current_user: Any) -> StreamingResponse:
    """返回准确率评测 SSE 响应。"""
    return StreamingResponse(
        _iter_accuracy_evaluation_sse(payload, request, current_user),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _accuracy_bp(value: Any) -> int:
    """将 0-1 准确率转换为万分比整数。"""
    try:
        return max(0, min(10000, int(round(float(value or 0) * 10000))))
    except Exception:
        return 0


def _is_eval_case_passed(result: Dict[str, Any]) -> bool:
    """判断单条评测结果是否通过。"""
    return (
        str(result.get("status") or "") != "failed"
        and float(result.get("semantic_accuracy") or 0) >= 1
        and result.get("dsl_exact_match") is not False
        and result.get("compile_success") is not False
    )


async def _create_eval_run_record(run_id: str, payload: AccuracyEvaluationRunRequest, current_user: Any) -> None:
    """创建评测运行记录。"""
    now = datetime.now()
    user_id = str(getattr(current_user, "user_id", "") or "").strip()
    operator_name = str(getattr(current_user, "username", "") or "").strip()
    operator_user_id = user_id or "accuracy-evaluator"
    request_payload = payload.model_dump()
    if operator_name:
        request_payload["operator_name"] = operator_name
    async with get_session() as db:
        db.add(ChatDataEvalRun(
            run_id=run_id,
            case_set_id=_scene_case_set_id(payload.datasource_id or "", payload.semantic_config_id or "") if payload.datasource_id and payload.semantic_config_id else "",
            datasource_id=payload.datasource_id or "",
            semantic_config_id=payload.semantic_config_id or "",
            user_id=user_id,
            run_mode="live",
            run_status="running",
            request_payload=json.dumps(request_payload, ensure_ascii=False),
            total_count=0,
            scored_count=0,
            passed_count=0,
            failed_count=0,
            semantic_accuracy_bp=0,
            started_time=now,
            create_time=now,
            update_time=now,
            create_by=operator_user_id,
            create_by_name=operator_name,
            update_by=operator_user_id,
            update_by_name=operator_name,
        ))
        await db.commit()


async def _upsert_eval_run_case_record(run_id: str, case_id_value: str, result: Dict[str, Any], operator_id: Optional[str] = None, operator_name: Optional[str] = None) -> None:
    """保存单条评测用例结果。"""
    now = datetime.now()
    status = "passed" if _is_eval_case_passed(result) else "failed"
    operator_user_id = str(operator_id or "accuracy-evaluator").strip()
    operator_username = str(operator_name or "").strip()
    async with get_session() as db:
        query_result = await db.execute(select(ChatDataEvalRunCase).filter_by(run_id=run_id, case_id=case_id_value))
        row = query_result.scalar_one_or_none()
        if row:
            row.case_status = status
            row.result_payload = json.dumps(result, ensure_ascii=False)
            row.semantic_accuracy_bp = _accuracy_bp(result.get("semantic_accuracy"))
            row.elapsed_ms = int(result.get("elapsed_ms") or 0) if result.get("elapsed_ms") is not None else None
            row.error_message = str(result.get("error") or "") or None
            row.delete_flag = 0
            row.update_time = now
            row.update_by = operator_user_id
            row.update_by_name = operator_username
        else:
            db.add(ChatDataEvalRunCase(
                run_case_id=uuid.uuid4().hex,
                run_id=run_id,
                case_id=case_id_value,
                case_status=status,
                result_payload=json.dumps(result, ensure_ascii=False),
                semantic_accuracy_bp=_accuracy_bp(result.get("semantic_accuracy")),
                elapsed_ms=int(result.get("elapsed_ms") or 0) if result.get("elapsed_ms") is not None else None,
                error_message=str(result.get("error") or "") or None,
                delete_flag=0,
                create_time=now,
                update_time=now,
                create_by=operator_user_id,
                create_by_name=operator_username,
                update_by=operator_user_id,
                update_by_name=operator_username,
            ))
        await db.commit()


async def _update_eval_run_record(run_id: str, *, status: Optional[str] = None, summary: Optional[Dict[str, Any]] = None, error_message: Optional[str] = None) -> None:
    """更新评测运行汇总。"""
    now = datetime.now()
    async with get_session() as db:
        query_result = await db.execute(select(ChatDataEvalRun).filter_by(run_id=run_id, delete_flag=0))
        row = query_result.scalar_one_or_none()
        if not row:
            return
        if status:
            row.run_status = status
            if status in {"succeeded", "failed", "cancelled"}:
                row.finished_time = now
        if summary is not None:
            row.summary_payload = json.dumps(summary, ensure_ascii=False)
            row.total_count = int(summary.get("total") or 0)
            row.scored_count = int(summary.get("scored") or 0)
            row.failed_count = int(summary.get("failed") or 0)
            row.passed_count = max(0, row.scored_count - row.failed_count)
            row.semantic_accuracy_bp = _accuracy_bp(summary.get("semantic_accuracy"))
        if error_message is not None:
            row.error_message = error_message or None
        row.update_time = now
        await db.commit()


def _format_datetime(value: Optional[datetime]) -> Optional[str]:
    """格式化接口返回时间。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _eval_run_row_to_payload(row: ChatDataEvalRun) -> Dict[str, Any]:
    """转换评测运行记录。"""
    request_payload = _safe_json_loads(row.request_payload, {}) or {}
    tester_name = str(row.create_by_name or row.update_by_name or request_payload.get("operator_name") or "").strip()
    return {
        "run_id": row.run_id,
        "case_set_id": row.case_set_id,
        "datasource_id": row.datasource_id,
        "semantic_config_id": row.semantic_config_id,
        "user_id": row.user_id,
        "create_by": row.create_by or "",
        "create_by_name": row.create_by_name or "",
        "update_by": row.update_by or "",
        "update_by_name": row.update_by_name or "",
        "tester_name": tester_name,
        "run_mode": row.run_mode,
        "run_status": row.run_status,
        "request_payload": request_payload,
        "summary": _safe_json_loads(row.summary_payload, {}) or {},
        "total_count": row.total_count,
        "scored_count": row.scored_count,
        "passed_count": row.passed_count,
        "failed_count": row.failed_count,
        "semantic_accuracy": float(row.semantic_accuracy_bp or 0) / 10000,
        "error_message": row.error_message or "",
        "started_time": _format_datetime(row.started_time),
        "finished_time": _format_datetime(row.finished_time),
        "create_time": _format_datetime(row.create_time),
        "update_time": _format_datetime(row.update_time),
    }


def _eval_run_case_row_to_payload(row: ChatDataEvalRunCase) -> Dict[str, Any]:
    """转换评测单用例结果。"""
    result_payload = _safe_json_loads(row.result_payload, {}) or {}
    if isinstance(result_payload, dict):
        result_payload.setdefault("id", row.case_id)
        result_payload.setdefault("status", row.case_status)
        result_payload.setdefault("error", row.error_message or "")
        result_payload.setdefault("elapsed_ms", row.elapsed_ms)
    return {
        "run_case_id": row.run_case_id,
        "run_id": row.run_id,
        "case_id": row.case_id,
        "case_status": row.case_status,
        "result": result_payload,
        "semantic_accuracy": float(row.semantic_accuracy_bp or 0) / 10000,
        "elapsed_ms": row.elapsed_ms,
        "error_message": row.error_message or "",
        "create_time": _format_datetime(row.create_time),
        "update_time": _format_datetime(row.update_time),
    }


@router.post("/evaluations/accuracy/run")
async def run_accuracy_evaluation(payload: AccuracyEvaluationRunRequest, request: Request):
    """运行智能问数准确率评测。"""
    try:
        current_user = _get_current_user(request)
        report = await _run_accuracy_evaluation_payload(payload, request, current_user)
        return business_response(data=report, msg="评测完成")
    except HTTPException as e:
        return business_response(msg=str(e.detail), code=e.status_code * 100 + 1, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=40001, status="error")
    except Exception as e:
        logger.error("智能问数准确率评测失败: %s", e, exc_info=True)
        return business_response(msg=f"评测失败: {e}", code=50056, status="error")


@router.post("/evaluations/accuracy/run-stream")
async def run_accuracy_evaluation_stream(payload: AccuracyEvaluationRunRequest, request: Request):
    """流式运行准确率评测，逐条返回用例结果。"""
    try:
        current_user = _get_current_user(request)
    except HTTPException as e:
        message = str(e.detail)
        async def auth_error_stream():
            yield f"data: {json.dumps({'event': 'error', 'message': message}, ensure_ascii=False)}\n\n"
        return StreamingResponse(auth_error_stream(), media_type="text/event-stream")
    return _accuracy_sse_response(payload, request, current_user)


@router.get("/evaluations/accuracy/runs")
async def list_accuracy_evaluation_runs(
    datasource_id: Optional[str] = None,
    semantic_config_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """查询智能问数评测历史批次。"""
    try:
        normalized_page = max(1, int(page or 1))
        normalized_size = min(100, max(1, int(page_size or 20)))
        conditions = [ChatDataEvalRun.delete_flag == 0]
        if datasource_id:
            conditions.append(ChatDataEvalRun.datasource_id == datasource_id)
        if semantic_config_id:
            conditions.append(ChatDataEvalRun.semantic_config_id == semantic_config_id)
        async with get_session() as db:
            total_result = await db.execute(select(func.count()).select_from(ChatDataEvalRun).where(and_(*conditions)))
            total = int(total_result.scalar_one() or 0)
            result = await db.execute(
                select(ChatDataEvalRun)
                .where(and_(*conditions))
                .order_by(ChatDataEvalRun.create_time.desc(), ChatDataEvalRun.id.desc())
                .offset((normalized_page - 1) * normalized_size)
                .limit(normalized_size)
            )
            runs = [_eval_run_row_to_payload(row) for row in result.scalars().all()]
        return business_response(data={"runs": runs, "total": total, "page": normalized_page, "page_size": normalized_size}, msg="获取成功")
    except Exception as e:
        logger.error("获取智能问数评测历史失败: datasource_id=%s config_id=%s error=%s", datasource_id, semantic_config_id, e, exc_info=True)
        return business_response(msg="获取评测历史失败，请稍后重试", code=50059, status="error")


@router.get("/evaluations/accuracy/runs/{run_id}")
async def get_accuracy_evaluation_run_detail(run_id: str):
    """查询智能问数评测历史详情。"""
    try:
        async with get_session() as db:
            run_result = await db.execute(select(ChatDataEvalRun).filter_by(run_id=run_id, delete_flag=0))
            run_row = run_result.scalar_one_or_none()
            if not run_row:
                return business_response(msg="评测记录不存在", code=40404, status="error")
            case_result = await db.execute(
                select(ChatDataEvalRunCase)
                .filter_by(run_id=run_id, delete_flag=0)
                .order_by(ChatDataEvalRunCase.id.asc())
            )
            cases = [_eval_run_case_row_to_payload(row) for row in case_result.scalars().all()]
        return business_response(data={"run": _eval_run_row_to_payload(run_row), "cases": cases}, msg="获取成功")
    except Exception as e:
        logger.error("获取智能问数评测历史详情失败: run_id=%s error=%s", run_id, e, exc_info=True)
        return business_response(msg="获取评测历史详情失败，请稍后重试", code=50060, status="error")


async def _compile_scene_case_gold_sql(datasource_id: str, semantic_config_id: str, case_payload: Dict[str, Any]) -> Dict[str, Any]:
    """为当前场景用例编译标准 SQL。"""
    from ..evaluate.accuracy.runner import compile_dsl_locally

    cloned = deepcopy(case_payload)
    compile_result = await compile_dsl_locally(
        datasource_id=datasource_id,
        semantic_config_id=semantic_config_id,
        dsl=cloned.get("gold_dsl"),
        user_query=str(cloned.get("nl") or ""),
    )
    cloned["gold_sql"] = str(compile_result.get("sql") or "")
    if not compile_result.get("success"):
        raise ValueError(f"用例 {cloned.get('id')} DSL 编译失败: {compile_result.get('error') or '未知错误'}")
    return cloned


@router.get("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases")
async def get_scene_evaluation_cases(datasource_id: str, config_id: str):
    """读取当前指标集场景的评测测试集。"""
    try:
        scene_paths = _scene_case_paths(datasource_id, config_id)
        loaded = await _load_scene_case_set(datasource_id, config_id)
        cases = loaded.get("cases") or []
        business_domain = loaded.get("business_domain") or (cases[0].get("business_domain") if cases else _sanitize_scene_segment(config_id, "scene"))
        return business_response(
            data={"cases": cases, "total": len(cases), "business_domain": business_domain, "scene_id": scene_paths["scene_id"]},
            msg="获取成功",
        )
    except Exception as e:
        logger.error("获取场景评测测试集失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True)
        return business_response(msg=f"获取测试集失败: {e}", code=50057, status="error")


@router.put("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases")
async def save_scene_evaluation_cases(datasource_id: str, config_id: str, payload: AccuracyEvaluationCasesUpsertRequest, request: Request):
    """保存当前指标集场景的评测测试集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(getattr(current_user, "user_id", "") or "").strip()
        username = str(getattr(current_user, "username", "") or "").strip()
        ds_manager = get_datasource_manager()
        config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        cases = [item.model_dump() for item in payload.cases]
        business_domain = str(payload.business_domain or _sanitize_scene_segment(config_id, "scene")).strip()
        compiled_cases: List[Dict[str, Any]] = []
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
            data={"cases": compiled_cases, "total": len(compiled_cases), "business_domain": business_domain, "scene_id": scene_paths["scene_id"]},
            msg="保存成功",
        )
    except ValueError as e:
        return business_response(msg=str(e), code=40001, status="error")
    except Exception as e:
        logger.error("保存场景评测测试集失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True)
        return business_response(msg=f"保存测试集失败: {e}", code=50058, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases/generate")
async def generate_scene_evaluation_cases(datasource_id: str, config_id: str):
    """按当前语义配置生成场景评测测试集草稿，不直接保存。"""
    try:
        ds_manager = get_datasource_manager()
        config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")
        business_domain = _sanitize_scene_segment(config.get("name") or config_id, "scene")
        draft_cases = _build_seed_case_candidates(config_id, config, business_domain)
        compiled_cases: List[Dict[str, Any]] = []
        for case in draft_cases:
            compiled_cases.append(await _compile_scene_case_gold_sql(datasource_id, config_id, case))
        scene_paths = _scene_case_paths(datasource_id, config_id)
        return business_response(
            data={"cases": compiled_cases, "total": len(compiled_cases), "business_domain": business_domain, "scene_id": scene_paths["scene_id"]},
            msg="生成草稿成功",
        )
    except ValueError as e:
        return business_response(msg=str(e), code=40001, status="error")
    except Exception as e:
        logger.error("生成场景评测测试集失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True)
        return business_response(msg=f"生成测试集失败: {e}", code=50059, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/evaluation-cases/draft-case")
async def draft_scene_evaluation_case(datasource_id: str, config_id: str, payload: AccuracyEvaluationDraftCaseRequest):
    """用 LLM 生成单条当前场景评测用例草稿。"""
    try:
        ds_manager = get_datasource_manager()
        config = await ds_manager.get_semantic_config_async(datasource_id, config_id)
        if not config:
            return business_response(msg=f"语义配置不存在: {config_id}", code=40404, status="error")
        case = await _generate_draft_case_by_llm(config_id, config, payload)
        compiled_case = await _compile_scene_case_gold_sql(datasource_id, config_id, case)
        return business_response(data={"case": compiled_case}, msg="生成成功")
    except ValueError as e:
        return business_response(msg=str(e), code=40001, status="error")
    except Exception as e:
        logger.error("生成单条评测用例草稿失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True)
        return business_response(msg=f"生成用例草稿失败: {e}", code=50060, status="error")
