"""评测管理数据访问层。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select

from utils.models import ChatDataEvalCase, ChatDataEvalCaseSet, ChatDataEvalRun, ChatDataEvalRunCase


class EvaluationDao:
    """封装准确率评测相关表读写。"""

    async def load_scene_case_set(
        self, db: Any, case_set_id: str
    ) -> tuple[ChatDataEvalCaseSet | None, list[ChatDataEvalCase]]:
        set_result = await db.execute(select(ChatDataEvalCaseSet).filter_by(case_set_id=case_set_id, delete_flag=0))
        case_set = set_result.scalar_one_or_none()
        if not case_set:
            return None, []
        case_result = await db.execute(
            select(ChatDataEvalCase)
            .filter_by(case_set_id=case_set_id, delete_flag=0, enabled_flag=1)
            .order_by(ChatDataEvalCase.sort_no.asc(), ChatDataEvalCase.id.asc())
        )
        return case_set, list(case_result.scalars().all())

    async def save_scene_case_set(
        self,
        db: Any,
        *,
        case_set_id: str,
        datasource_id: str,
        semantic_config_id: str,
        case_set_name: str,
        business_domain: str,
        cases: Sequence[dict[str, Any]],
        operator_id: str,
        operator_name: str,
        now: datetime,
    ) -> None:
        set_result = await db.execute(select(ChatDataEvalCaseSet).filter_by(case_set_id=case_set_id))
        case_set = set_result.scalar_one_or_none()
        if case_set:
            self._update_case_set(
                case_set,
                datasource_id,
                semantic_config_id,
                case_set_name,
                business_domain,
                cases,
                operator_id,
                operator_name,
                now,
            )
        else:
            db.add(
                self._new_case_set(
                    case_set_id,
                    datasource_id,
                    semantic_config_id,
                    case_set_name,
                    business_domain,
                    cases,
                    operator_id,
                    operator_name,
                    now,
                )
            )

        existing_result = await db.execute(select(ChatDataEvalCase).filter_by(case_set_id=case_set_id))
        existing_cases = {row.case_id: row for row in existing_result.scalars().all()}
        self._sync_case_rows(db, case_set_id, business_domain, cases, existing_cases, operator_id, operator_name, now)

    @staticmethod
    def _update_case_set(
        row: ChatDataEvalCaseSet,
        datasource_id: str,
        semantic_config_id: str,
        case_set_name: str,
        business_domain: str,
        cases: Sequence[dict[str, Any]],
        operator_id: str,
        operator_name: str,
        now: datetime,
    ) -> None:
        row.datasource_id = datasource_id
        row.semantic_config_id = semantic_config_id
        row.case_set_name = case_set_name
        row.business_domain = business_domain
        row.version_no = int(row.version_no or 0) + 1
        row.case_count = len(cases)
        row.case_set_status = "active"
        row.delete_flag = 0
        row.update_time = now
        row.update_by = operator_id
        row.update_by_name = operator_name

    @staticmethod
    def _new_case_set(
        case_set_id: str,
        datasource_id: str,
        semantic_config_id: str,
        case_set_name: str,
        business_domain: str,
        cases: Sequence[dict[str, Any]],
        operator_id: str,
        operator_name: str,
        now: datetime,
    ) -> ChatDataEvalCaseSet:
        return ChatDataEvalCaseSet(
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
            create_by=operator_id,
            create_by_name=operator_name,
            update_by=operator_id,
            update_by_name=operator_name,
        )

    @classmethod
    def _sync_case_rows(
        cls,
        db: Any,
        case_set_id: str,
        business_domain: str,
        cases: Sequence[dict[str, Any]],
        existing_cases: dict[str, ChatDataEvalCase],
        operator_id: str,
        operator_name: str,
        now: datetime,
    ) -> None:
        next_case_ids = {str(case.get("id") or "").strip() for case in cases}
        for case_id_value, row in existing_cases.items():
            if case_id_value not in next_case_ids:
                cls._mark_case_deleted(row, operator_id, operator_name, now)
        for index, case in enumerate(cases, start=1):
            case_id_value = str(case.get("id") or "").strip()
            row = existing_cases.get(case_id_value)
            if row:
                cls._update_case_row(row, case, business_domain, index, operator_id, operator_name, now)
            else:
                db.add(cls._new_case_row(case_set_id, case, business_domain, index, operator_id, operator_name, now))

    @staticmethod
    def _mark_case_deleted(row: ChatDataEvalCase, operator_id: str, operator_name: str, now: datetime) -> None:
        row.delete_flag = 1
        row.update_time = now
        row.update_by = operator_id
        row.update_by_name = operator_name

    @staticmethod
    def _case_values(case: dict[str, Any], business_domain: str, index: int) -> dict[str, Any]:
        return {
            "case_id": str(case.get("id") or "").strip(),
            "nl_text": str(case.get("nl") or "").strip(),
            "gold_dsl_text": case.get("gold_dsl_text") or "",
            "gold_sql_text": str(case.get("gold_sql") or ""),
            "query_type": str(case.get("query_type") or "aggregation"),
            "case_difficulty": str(case.get("difficulty") or "easy"),
            "case_source": str(case.get("source") or "human"),
            "business_domain": str(case.get("business_domain") or business_domain),
            "enabled_flag": 1,
            "sort_no": index,
            "delete_flag": 0,
        }

    @classmethod
    def _update_case_row(
        cls,
        row: ChatDataEvalCase,
        case: dict[str, Any],
        business_domain: str,
        index: int,
        operator_id: str,
        operator_name: str,
        now: datetime,
    ) -> None:
        for key, value in cls._case_values(case, business_domain, index).items():
            setattr(row, key, value)
        row.update_time = now
        row.update_by = operator_id
        row.update_by_name = operator_name

    @classmethod
    def _new_case_row(
        cls,
        case_set_id: str,
        case: dict[str, Any],
        business_domain: str,
        index: int,
        operator_id: str,
        operator_name: str,
        now: datetime,
    ) -> ChatDataEvalCase:
        values = cls._case_values(case, business_domain, index)
        return ChatDataEvalCase(
            case_set_id=case_set_id,
            create_time=now,
            update_time=now,
            create_by=operator_id,
            create_by_name=operator_name,
            update_by=operator_id,
            update_by_name=operator_name,
            **values,
        )

    async def create_eval_run(self, db: Any, row: ChatDataEvalRun) -> None:
        db.add(row)

    async def get_eval_run(self, db: Any, run_id: str) -> ChatDataEvalRun | None:
        result = await db.execute(select(ChatDataEvalRun).filter_by(run_id=run_id, delete_flag=0))
        return result.scalar_one_or_none()

    async def list_eval_run_cases(self, db: Any, run_id: str) -> list[ChatDataEvalRunCase]:
        result = await db.execute(
            select(ChatDataEvalRunCase).filter_by(run_id=run_id, delete_flag=0).order_by(ChatDataEvalRunCase.id.asc())
        )
        return list(result.scalars().all())

    async def list_eval_runs(
        self,
        db: Any,
        *,
        conditions: Sequence[Any],
        page: int,
        page_size: int,
    ) -> tuple[list[ChatDataEvalRun], int]:
        total_result = await db.execute(select(func.count()).select_from(ChatDataEvalRun).where(and_(*conditions)))
        total = int(total_result.scalar_one() or 0)
        result = await db.execute(
            select(ChatDataEvalRun)
            .where(and_(*conditions))
            .order_by(ChatDataEvalRun.create_time.desc(), ChatDataEvalRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def upsert_eval_run_case(self, db: Any, row_data: dict[str, Any]) -> None:
        query_result = await db.execute(
            select(ChatDataEvalRunCase).filter_by(run_id=row_data["run_id"], case_id=row_data["case_id"])
        )
        row = query_result.scalar_one_or_none()
        if row:
            for key, value in row_data.items():
                if key not in {"run_case_id", "create_time", "create_by", "create_by_name"}:
                    setattr(row, key, value)
        else:
            db.add(ChatDataEvalRunCase(**row_data))

    async def update_eval_run(self, db: Any, run_id: str, values: dict[str, Any]) -> None:
        query_result = await db.execute(select(ChatDataEvalRun).filter_by(run_id=run_id, delete_flag=0))
        row = query_result.scalar_one_or_none()
        if not row:
            return
        for key, value in values.items():
            setattr(row, key, value)
