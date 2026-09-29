"""评测管理业务服务。"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.messages import HumanMessage

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.database import get_session as default_get_session
from utils.llm_json import parse_llm_json
from utils.models import ChatDataEvalCase, ChatDataEvalRun, ChatDataEvalRunCase

from ..agent.prompts.generate_evaluate_set import build_evaluation_draft_case_prompt
from ..daos.evaluation_dao import EvaluationDao
from .llm_config import get_chat_data_llm


def sanitize_scene_segment(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return text or fallback


def scene_case_paths(datasource_id: str, semantic_config_id: str) -> Dict[str, str]:
    ds_segment = sanitize_scene_segment(datasource_id, "datasource")
    config_segment = sanitize_scene_segment(semantic_config_id, "semantic_config")
    return {"scene_id": f"{ds_segment}__{config_segment}"}


def scene_case_set_id(datasource_id: str, semantic_config_id: str) -> str:
    return hashlib.sha256(f"{datasource_id}::{semantic_config_id}".encode("utf-8")).hexdigest()[:32]


def safe_json_loads(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def case_row_to_payload(row: ChatDataEvalCase) -> Dict[str, Any]:
    return {
        "id": row.case_id,
        "nl": row.nl_text,
        "gold_dsl": safe_json_loads(row.gold_dsl_text, {}) or {},
        "gold_sql": row.gold_sql_text or "",
        "query_type": row.query_type or "aggregation",
        "difficulty": row.case_difficulty or "easy",
        "source": "llm" if row.case_source == "model_generated" else (row.case_source or "human"),
        "business_domain": row.business_domain or "",
    }


def validate_eval_case_ids(cases: Sequence[Dict[str, Any]]) -> None:
    seen = set()
    for index, case in enumerate(cases, start=1):
        case_id_value = str(case.get("id") or "").strip()
        if not case_id_value:
            raise ValueError(f"第 {index} 条用例缺少用例ID")
        if case_id_value in seen:
            raise ValueError(f"用例ID重复: {case_id_value}")
        seen.add(case_id_value)


def accuracy_bp(value: Any) -> int:
    try:
        return max(0, min(10000, int(round(float(value or 0) * 10000))))
    except Exception:
        return 0


def is_eval_case_passed(result: Dict[str, Any]) -> bool:
    return (
        str(result.get("status") or "") != "failed"
        and float(result.get("semantic_accuracy") or 0) >= 1
        and result.get("dsl_exact_match") is not False
        and result.get("compile_success") is not False
    )


def format_datetime(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def eval_run_row_to_payload(row: ChatDataEvalRun) -> Dict[str, Any]:
    request_payload = safe_json_loads(row.request_payload, {}) or {}
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
        "summary": safe_json_loads(row.summary_payload, {}) or {},
        "total_count": row.total_count,
        "scored_count": row.scored_count,
        "passed_count": row.passed_count,
        "failed_count": row.failed_count,
        "semantic_accuracy": float(row.semantic_accuracy_bp or 0) / 10000,
        "error_message": row.error_message or "",
        "started_time": format_datetime(row.started_time),
        "finished_time": format_datetime(row.finished_time),
        "create_time": format_datetime(row.create_time),
        "update_time": format_datetime(row.update_time),
    }


def eval_run_case_row_to_payload(row: ChatDataEvalRunCase) -> Dict[str, Any]:
    result_payload = safe_json_loads(row.result_payload, {}) or {}
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
        "create_time": format_datetime(row.create_time),
        "update_time": format_datetime(row.update_time),
    }


def _storage_cases(cases: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            **case,
            "gold_dsl_text": json.dumps(case.get("gold_dsl") or {}, ensure_ascii=False),
        }
        for case in cases
    ]


class EvaluationService:
    """准确率评测测试集和运行记录编排服务。"""

    def __init__(self, dao: Optional[EvaluationDao] = None, session_context: Optional[Callable] = None):
        self.dao = dao or EvaluationDao()
        self._session_context = session_context or default_get_session

    @staticmethod
    async def _commit(db: Any) -> None:
        commit = getattr(db, "commit", None)
        if commit:
            await commit()

    async def load_scene_case_set(self, datasource_id: str, semantic_config_id: str) -> Dict[str, Any]:
        case_set_id = scene_case_set_id(datasource_id, semantic_config_id)
        async with self._session_context() as db:
            case_set, case_rows = await self.dao.load_scene_case_set(db, case_set_id)
        if not case_set:
            return {"case_set_id": case_set_id, "business_domain": "", "cases": []}
        return {
            "case_set_id": case_set_id,
            "business_domain": case_set.business_domain or "",
            "cases": [case_row_to_payload(row) for row in case_rows],
        }

    async def save_scene_case_set(
        self,
        datasource_id: str,
        semantic_config_id: str,
        case_set_name: str,
        business_domain: str,
        cases: Sequence[Dict[str, Any]],
        operator_id: Optional[str] = None,
        operator_name: Optional[str] = None,
    ) -> str:
        validate_eval_case_ids(cases)
        case_set_id = scene_case_set_id(datasource_id, semantic_config_id)
        async with self._session_context() as db:
            await self.dao.save_scene_case_set(
                db,
                case_set_id=case_set_id,
                datasource_id=datasource_id,
                semantic_config_id=semantic_config_id,
                case_set_name=case_set_name,
                business_domain=business_domain,
                cases=_storage_cases(cases),
                operator_id=str(operator_id or "").strip(),
                operator_name=str(operator_name or "").strip(),
                now=datetime.now(),
            )
            await self._commit(db)
        return case_set_id

    async def load_scene_runner_cases(self, datasource_id: str, semantic_config_id: str) -> List[Dict[str, Any]]:
        loaded = await self.load_scene_case_set(datasource_id, semantic_config_id)
        cases = loaded.get("cases") or []
        if not cases:
            raise ValueError("当前指标集尚未配置测试集，请先生成测试集草稿并保存后再评测")
        return cases

    async def create_eval_run_record(self, run_id: str, payload: Any, current_user: Any) -> None:
        now = datetime.now()
        user_id = str(getattr(current_user, "user_id", "") or "").strip()
        operator_name = str(getattr(current_user, "username", "") or "").strip()
        operator_user_id = user_id or "accuracy-evaluator"
        request_payload = payload.model_dump()
        if operator_name:
            request_payload["operator_name"] = operator_name
        async with self._session_context() as db:
            await self.dao.create_eval_run(
                db,
                ChatDataEvalRun(
                    run_id=run_id,
                    case_set_id=scene_case_set_id(payload.datasource_id or "", payload.semantic_config_id or "")
                    if payload.datasource_id and payload.semantic_config_id
                    else "",
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
                ),
            )
            await self._commit(db)

    async def upsert_eval_run_case_record(
        self,
        run_id: str,
        case_id_value: str,
        result: Dict[str, Any],
        operator_id: Optional[str] = None,
        operator_name: Optional[str] = None,
    ) -> None:
        now = datetime.now()
        async with self._session_context() as db:
            await self.dao.upsert_eval_run_case(
                db,
                {
                    "run_case_id": uuid.uuid4().hex,
                    "run_id": run_id,
                    "case_id": case_id_value,
                    "case_status": "passed" if is_eval_case_passed(result) else "failed",
                    "result_payload": json.dumps(result, ensure_ascii=False),
                    "semantic_accuracy_bp": accuracy_bp(result.get("semantic_accuracy")),
                    "elapsed_ms": int(result.get("elapsed_ms") or 0) if result.get("elapsed_ms") is not None else None,
                    "error_message": str(result.get("error") or "") or None,
                    "delete_flag": 0,
                    "create_time": now,
                    "update_time": now,
                    "create_by": str(operator_id or "accuracy-evaluator").strip(),
                    "create_by_name": str(operator_name or "").strip(),
                    "update_by": str(operator_id or "accuracy-evaluator").strip(),
                    "update_by_name": str(operator_name or "").strip(),
                },
            )
            await self._commit(db)

    async def update_eval_run_record(
        self,
        run_id: str,
        *,
        status: Optional[str] = None,
        summary: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        values: Dict[str, Any] = {"update_time": datetime.now()}
        if status:
            values["run_status"] = status
            if status in {"succeeded", "failed", "cancelled"}:
                values["finished_time"] = values["update_time"]
        if summary is not None:
            values.update(
                {
                    "summary_payload": json.dumps(summary, ensure_ascii=False),
                    "total_count": int(summary.get("total") or 0),
                    "scored_count": int(summary.get("scored") or 0),
                    "failed_count": int(summary.get("failed") or 0),
                    "semantic_accuracy_bp": accuracy_bp(summary.get("semantic_accuracy")),
                }
            )
            values["passed_count"] = max(0, values["scored_count"] - values["failed_count"])
        if error_message is not None:
            values["error_message"] = error_message or None
        async with self._session_context() as db:
            await self.dao.update_eval_run(db, run_id, values)
            await self._commit(db)

    async def list_eval_runs(
        self,
        *,
        datasource_id: Optional[str] = None,
        semantic_config_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_page = max(1, int(page or 1))
        normalized_size = min(100, max(1, int(page_size or 20)))
        conditions = [ChatDataEvalRun.delete_flag == 0]
        if datasource_id:
            conditions.append(ChatDataEvalRun.datasource_id == datasource_id)
        if semantic_config_id:
            conditions.append(ChatDataEvalRun.semantic_config_id == semantic_config_id)
        async with self._session_context() as db:
            rows, total = await self.dao.list_eval_runs(
                db,
                conditions=conditions,
                page=normalized_page,
                page_size=normalized_size,
            )
        return {
            "runs": [eval_run_row_to_payload(row) for row in rows],
            "total": total,
            "page": normalized_page,
            "page_size": normalized_size,
        }

    async def get_eval_run_detail(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with self._session_context() as db:
            run_row = await self.dao.get_eval_run(db, run_id)
            if not run_row:
                return None
            cases = await self.dao.list_eval_run_cases(db, run_id)
        return {
            "run": eval_run_row_to_payload(run_row),
            "cases": [eval_run_case_row_to_payload(row) for row in cases],
        }


def build_evaluation_service(session_context: Optional[Callable] = None) -> EvaluationService:
    """构建评测服务。"""
    return EvaluationService(session_context=session_context or default_get_session)


# ---------------------------------------------------------------------------
# 评测用例草稿生成（自 routers/evaluation.py 下沉的 LLM 编排）
# ---------------------------------------------------------------------------

ACCURACY_QUERY_TYPES = {"filter", "join", "aggregation", "subquery"}
ACCURACY_DIFFICULTIES = {"easy", "medium", "hard"}


def looks_like_sql(text: str) -> bool:
    """判断用户输入是否更像 SQL。"""
    normalized = str(text or "").strip().lower()
    return bool(
        re.search(r"^\s*(select|with)\b", normalized)
        or re.search(r"\bfrom\b.+\bselect\b|\bselect\b.+\bfrom\b", normalized, flags=re.DOTALL)
    )


async def invoke_evaluation_draft_llm(prompt: str, config_id: str):
    """调用评测用例草稿生成模型，失败时上报连接报警。"""
    llm = get_chat_data_llm(streaming=False, max_tokens=2000)
    try:
        return await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={"purpose": "evaluation_draft_case", "config_id": config_id},
        )
        raise


async def generate_draft_case_by_llm(config_id: str, config: Dict[str, Any], payload) -> Dict[str, Any]:
    """用 LLM 基于用户问题和当前语义配置生成 gold_dsl 草稿。"""
    user_input = str(payload.hint or "").strip()
    if not user_input:
        raise ValueError("请先输入自然语言问题或 SQL，再生成 gold_dsl")
    is_sql_input = looks_like_sql(user_input)
    nl_example = json.dumps("请基于 SQL 生成自然语言问题" if is_sql_input else user_input, ensure_ascii=False)
    business_domain = sanitize_scene_segment(config.get("name") or config_id, "scene")
    prompt = build_evaluation_draft_case_prompt(
        user_input=user_input,
        is_sql_input=is_sql_input,
        nl_example=nl_example,
        business_domain=business_domain,
        existing_questions=payload.existing_questions,
        semantic_config_payload=config,
    )
    response = await invoke_evaluation_draft_llm(prompt, config_id)
    content = getattr(response, "content", response)
    parsed = parse_llm_json(str(content), strict=False)
    if not isinstance(parsed, dict) or not parsed.get("nl") or not isinstance(parsed.get("gold_dsl"), dict):
        raise ValueError("大模型未返回合法评测用例 JSON")
    case_id = f"{sanitize_scene_segment(config_id, 'scene')}_{int(time.time() * 1000)}"
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
