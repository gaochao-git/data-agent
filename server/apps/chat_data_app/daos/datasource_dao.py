"""
数据源/语义配置数据库存储。

当前运行时以 datasource_group 作为唯一数据源入口：
- datasource_group: 组即数据源
- datasource_table: 数据源表结构缓存
- semantic_config: 数据源语义配置
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
import json
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from utils.database import get_session
from utils.models import DatasourceColumnDict, DatasourceGroup, DatasourceTable, DatasourceTableGroupRel, SemanticConfig
from utils.logger import get_logger
from .datasource_dao_helpers import (
    _batch_items,
    _column_dict_is_empty,
    _format_time,
    _load_json,
    _normalize_column_dict,
    _parse_member_source_entries,
    _parse_member_source_entry,
    _parse_member_sources_text,
    _parse_source_line,
    _unique_non_empty_texts,
)

logger = get_logger(__name__)

_UNSET = object()
_ORM_SESSION: ContextVar[Any] = ContextVar("chat_data_orm_session", default=None)
_MAX_RECOMMENDED_QUESTION_COUNT = 50
_MAX_RECOMMENDED_QUESTION_LENGTH = 50


class DataSourceDao:
    """基于 MySQL 的数据源/语义配置数据访问层。"""

    @asynccontextmanager
    async def _session_scope(self):
        active_session = _ORM_SESSION.get()
        if active_session is not None:
            yield active_session
            return
        async with get_session() as session:
            yield session

    @asynccontextmanager
    async def transaction_async(self):
        active_session = _ORM_SESSION.get()
        if active_session is not None:
            yield active_session
            return

        async with get_session() as session:
            token = _ORM_SESSION.set(session)
            try:
                yield session
            finally:
                _ORM_SESSION.reset(token)

    def _group_model_to_row(self, model: DatasourceGroup) -> Dict[str, Any]:
        return {
            "group_id": model.group_id,
            "group_name": model.group_name,
            "group_desc": model.group_desc,
            "query_context": model.query_context,
            "recommended_questions_json": getattr(model, "recommended_questions_json", None),
            "source_type": model.source_type,
            "template_source": model.template_source,
            "member_sources_text": model.member_sources_text,
            "mapped_modules_json": model.mapped_modules_json,
            "is_default": model.is_default,
            "create_time": model.create_time,
            "update_time": model.update_time,
            "create_by": model.create_by,
            "create_by_name": model.create_by_name,
            "update_by": model.update_by,
            "update_by_name": model.update_by_name,
        }

    def _semantic_config_model_to_row(self, model: SemanticConfig) -> Dict[str, Any]:
        return {
            "semantic_config_id": model.semantic_config_id,
            "group_id": model.group_id,
            "semantic_config_name": model.semantic_config_name,
            "semantic_config_desc": model.semantic_config_desc,
            "ontology_rules": model.ontology_rules,
            "enabled_flag": model.enabled_flag,
            "metrics_json": model.metrics_json,
            "dimensions_json": model.dimensions_json,
            "relationships_json": model.relationships_json,
            "scope_type": model.scope_type,
            "create_time": model.create_time,
            "update_time": model.update_time,
            "create_by": model.create_by,
            "create_by_name": model.create_by_name,
            "update_by": model.update_by,
            "update_by_name": model.update_by_name,
        }

    def _datasource_table_model_to_row(self, model: DatasourceTable) -> Dict[str, Any]:
        return {
            "group_id": model.group_id,
            "table_name": model.table_names,
            "table_desc": model.table_desc,
            "table_business_desc": model.table_business_desc,
            "columns_json": model.columns_json,
        }

    def _normalize_recommended_questions(self, value: Any) -> List[str]:
        raw_items = _load_json(value, [])
        if not isinstance(raw_items, list):
            return []

        result: List[str] = []
        seen: set[str] = set()
        for item in raw_items:
            question = str(item or "").strip()
            if not question or question in seen or len(question) > _MAX_RECOMMENDED_QUESTION_LENGTH:
                continue
            seen.add(question)
            result.append(question)
            if len(result) >= _MAX_RECOMMENDED_QUESTION_COUNT:
                break
        return result

    def _map_group_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        group_id = row.get("group_id") or ""
        template_source = str(row.get("template_source") or "").strip()
        parsed_template = _parse_member_source_entry(
            template_source,
            row.get("source_type"),
            None,
        ) or {}
        if not parsed_template:
            parsed_template = _parse_source_line(template_source) or {}
        member_entries = _parse_member_source_entries(
            row.get("member_sources_text"),
            template_source,
            row.get("source_type"),
            None,
        )
        template_member_id = member_entries[0]["datasource_id"] if member_entries else template_source
        return {
            "id": group_id,
            "datasource_id": group_id,
            "group_id": group_id,
            "group_ids": [group_id] if group_id else [],
            "template_datasource_id": template_member_id,
            "template_source": template_source,
            "member_sources_text": row.get("member_sources_text") or "",
            "group_name": row.get("group_name") or "",
            "ds_description": row.get("group_desc") or "",
            "query_context": row.get("query_context") or "",
            "recommended_questions": self._normalize_recommended_questions(row.get("recommended_questions_json")),
            "source_type": row.get("source_type") or "",
            "query_executor_type": parsed_template.get("query_executor_type") or "direct",
            "create_time": _format_time(row.get("create_time")) or "",
            "update_time": _format_time(row.get("update_time")) or "",
            "create_by": row.get("create_by") or "",
            "create_by_name": row.get("create_by_name") or "",
            "update_by": row.get("update_by") or "",
            "update_by_name": row.get("update_by_name") or "",
            "ds_tables": [],
            "mapped_modules": _load_json(row.get("mapped_modules_json"), []),
            "is_default": bool(row.get("is_default", 0)),
            "source_config": {
                "host": parsed_template.get("host"),
                "port": parsed_template.get("port"),
                "database": parsed_template.get("database"),
            },
        }

    def _map_semantic_config_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": row["semantic_config_id"],
            "semantic_config_id": row["semantic_config_id"],
            "group_id": row["group_id"],
            "name": row.get("semantic_config_name") or "",
            "description": row.get("semantic_config_desc") or "",
            "ontology_rules": row.get("ontology_rules"),
            "scope_type": row.get("scope_type") or "public",
            "enabled": bool(row.get("enabled_flag", 1)),
            "metrics": _load_json(row.get("metrics_json"), []),
            "dimensions": _load_json(row.get("dimensions_json"), []),
            "relationships": _load_json(row.get("relationships_json"), []),
            "create_time": _format_time(row.get("create_time")) or "",
            "update_time": _format_time(row.get("update_time")) or "",
            "create_by": row.get("create_by") or "",
            "create_by_name": row.get("create_by_name") or "",
            "update_by": row.get("update_by") or "",
            "update_by_name": row.get("update_by_name") or "",
        }

    def _map_datasource_table_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "table_name": row.get("table_name") or "",
            "description": row.get("table_desc") or row.get("table_name") or "",
            "business_desc": row.get("table_business_desc") or "",
            "columns": _load_json(row.get("columns_json"), []),
            "groups": [],
        }

    def _map_table_group_model(self, model: DatasourceTableGroupRel) -> Dict[str, Any]:
        return {
            "table_group_name": model.table_group_name or "",
            "table_name": model.table_name or "",
        }

    async def _attach_table_groups_async(self, group_id: str, tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not group_id or not tables:
            return tables
        table_names = _unique_non_empty_texts([table.get("table_name") for table in tables])
        if not table_names:
            return tables
        groups_by_table = await self.get_table_groups_by_table_names_async(group_id, table_names)
        for table in tables:
            table["groups"] = groups_by_table.get(table.get("table_name"), [])
        return tables

    def _build_member_sources(self, row: Dict[str, Any]) -> List[Dict[str, Any]]:
        source_type = row.get("source_type") or ""
        query_executor_type = None
        template_source = row.get("template_source")
        group_name = row.get("group_name") or ""
        result: List[Dict[str, Any]] = []
        for parsed in _parse_member_source_entries(
            row.get("member_sources_text"),
            template_source,
            source_type,
            query_executor_type,
        ):
            source_line = parsed["source_line"]
            is_template_member = parsed.get("raw_line") == str(template_source or "").strip() or source_line == str(template_source or "").strip()
            label = parsed.get("org_name") or (group_name if is_template_member else source_line)
            member_query_executor_type = parsed.get("query_executor_type") or query_executor_type
            org_code = parsed.get("org_code")
            result.append({
                "member_datasource_id": parsed["datasource_id"],
                "org_name": label,
                "org_code": org_code,
                "source_type": source_type,
                "query_executor_type": member_query_executor_type,
                "host": parsed.get("host") or "",
                "port": parsed.get("port"),
                "database": parsed.get("database") or "",
            })
        return result

    async def _get_group_row_by_id_async(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        if not datasource_id:
            return None
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceGroup)
                .where(
                    DatasourceGroup.group_id == datasource_id,
                    DatasourceGroup.delete_flag == 0,
                )
                .limit(1)
            )
            row = query_result.scalars().first()
        return self._group_model_to_row(row) if row else None

    async def has_datasource_async(self, datasource_id: str) -> bool:
        if not datasource_id:
            return False
        return await self._get_group_row_by_id_async(datasource_id) is not None

    async def _get_group_row_by_name_async(self, group_name: str) -> Optional[Dict[str, Any]]:
        if not group_name:
            return None
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceGroup)
                .where(
                    DatasourceGroup.group_name == group_name,
                    DatasourceGroup.delete_flag == 0,
                )
                .order_by(DatasourceGroup.id.asc())
                .limit(1)
            )
            row = query_result.scalars().first()
        return self._group_model_to_row(row) if row else None

    async def get_group_member_datasource_ids_async(self, group_id: str) -> List[str]:
        row = await self._get_group_row_by_id_async(group_id)
        if not row:
            return []
        return _parse_member_sources_text(
            row.get("member_sources_text"),
            row.get("template_source"),
            row.get("source_type"),
            None,
        )

    async def get_all_datasources_async(self, include_tables: bool = True) -> List[Dict[str, Any]]:
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceGroup)
                .where(DatasourceGroup.delete_flag == 0)
                .order_by(DatasourceGroup.is_default.desc(), DatasourceGroup.id.asc())
            )
            rows = [self._group_model_to_row(row) for row in query_result.scalars().all()]
        tables_map: Dict[str, List[Dict[str, Any]]] = {}
        if include_tables:
            group_ids = [row.get("group_id") for row in rows if row.get("group_id")]
            tables_map = await self._get_group_tables_map_async(group_ids)
        result = []
        for row in rows:
            datasource = self._map_group_row(row)
            group_id = datasource.get("group_id")
            datasource["ds_tables"] = tables_map.get(group_id, []) if include_tables else []
            datasource["member_datasource_ids"] = _parse_member_sources_text(
                row.get("member_sources_text"),
                row.get("template_source"),
                row.get("source_type"),
                None,
            )
            datasource["member_sources"] = self._build_member_sources(row)
            result.append(datasource)
        return result

    async def _get_group_tables_map_async(self, group_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        if not group_ids:
            return {}
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceTable)
                .where(
                    DatasourceTable.group_id.in_(group_ids),
                    DatasourceTable.delete_flag == 0,
                )
                .order_by(DatasourceTable.group_id.asc(), DatasourceTable.sort_no.asc(), DatasourceTable.id.asc())
            )
            rows = [self._datasource_table_model_to_row(row) for row in query_result.scalars().all()]
        result: Dict[str, List[Dict[str, Any]]] = {group_id: [] for group_id in group_ids}
        for row in rows:
            group_id = row.get("group_id")
            if not group_id:
                continue
            result.setdefault(group_id, []).append(self._map_datasource_table_row(row))
        groups_map = await self.get_table_groups_by_group_ids_async(group_ids)
        for group_id, tables in result.items():
            groups_by_table = groups_map.get(group_id, {})
            for table in tables:
                table["groups"] = groups_by_table.get(table.get("table_name"), [])
        return result

    async def get_datasource_tables_by_group_id_async(self, group_id: str) -> List[Dict[str, Any]]:
        if not group_id:
            return []
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceTable)
                .where(
                    DatasourceTable.group_id == group_id,
                    DatasourceTable.delete_flag == 0,
                )
                .order_by(DatasourceTable.sort_no.asc(), DatasourceTable.id.asc())
            )
            rows = [self._datasource_table_model_to_row(row) for row in query_result.scalars().all()]
        tables = [self._map_datasource_table_row(row) for row in rows]
        return await self._attach_table_groups_async(group_id, tables)

    async def get_table_groups_by_group_ids_async(self, group_ids: List[str]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        normalized_group_ids = _unique_non_empty_texts(group_ids)
        if not normalized_group_ids:
            return {}
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceTableGroupRel)
                .where(
                    DatasourceTableGroupRel.group_id.in_(normalized_group_ids),
                    DatasourceTableGroupRel.delete_flag == 0,
                )
                .order_by(
                    DatasourceTableGroupRel.table_group_name.asc(),
                    DatasourceTableGroupRel.table_name.asc(),
                    DatasourceTableGroupRel.id.asc(),
                )
            )
            rows = list(query_result.scalars().all())
        result: Dict[str, Dict[str, List[Dict[str, Any]]]] = {group_id: {} for group_id in normalized_group_ids}
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            group_id = str(row.group_id or "").strip()
            table_name = str(row.table_name or "").strip()
            table_group_name = str(row.table_group_name or "").strip()
            if not group_id or not table_name or not table_group_name:
                continue
            key = (group_id, table_name, table_group_name)
            if key in seen:
                continue
            seen.add(key)
            result.setdefault(group_id, {}).setdefault(table_name, []).append({"table_group_name": table_group_name})
        return result

    async def get_table_groups_by_table_names_async(self, group_id: str, table_names: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        normalized_group_id = str(group_id or "").strip()
        normalized_table_names = _unique_non_empty_texts(table_names)
        if not normalized_group_id or not normalized_table_names:
            return {}
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceTableGroupRel)
                .where(
                    DatasourceTableGroupRel.group_id == normalized_group_id,
                    DatasourceTableGroupRel.table_name.in_(normalized_table_names),
                    DatasourceTableGroupRel.delete_flag == 0,
                )
                .order_by(
                    DatasourceTableGroupRel.table_group_name.asc(),
                    DatasourceTableGroupRel.table_name.asc(),
                    DatasourceTableGroupRel.id.asc(),
                )
            )
            rows = list(query_result.scalars().all())
        result: Dict[str, List[Dict[str, Any]]] = {table_name: [] for table_name in normalized_table_names}
        seen: set[tuple[str, str]] = set()
        for row in rows:
            table_name = str(row.table_name or "").strip()
            table_group_name = str(row.table_group_name or "").strip()
            if not table_name or not table_group_name:
                continue
            key = (table_name, table_group_name)
            if key in seen:
                continue
            seen.add(key)
            result.setdefault(table_name, []).append({"table_group_name": table_group_name})
        return result

    async def get_table_group_relations_async(self, group_id: str) -> List[Dict[str, Any]]:
        if not group_id:
            return []
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceTableGroupRel)
                .where(
                    DatasourceTableGroupRel.group_id == group_id,
                    DatasourceTableGroupRel.delete_flag == 0,
                )
                .order_by(
                    DatasourceTableGroupRel.table_group_name.asc(),
                    DatasourceTableGroupRel.table_name.asc(),
                    DatasourceTableGroupRel.id.asc(),
                )
            )
            return [
                self._map_table_group_model(row)
                for row in query_result.scalars().all()
                if str(row.table_group_name or "").strip() and str(row.table_name or "").strip()
            ]

    async def replace_table_group_relations_async(self, group_id: str, groups: List[Dict[str, Any]], create_by: str = "", update_by: str = "", create_by_name: str = "", update_by_name: str = "") -> None:
        if not group_id:
            return
        async with self._session_scope() as session:
            await session.execute(
                update(DatasourceTableGroupRel)
                .where(
                    DatasourceTableGroupRel.group_id == group_id,
                    DatasourceTableGroupRel.delete_flag == 0,
                )
                .values(
                    delete_flag=1,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
            relation_rows = []
            seen: set[tuple[str, str]] = set()
            for group in groups or []:
                table_group_name = str((group or {}).get("table_group_name") or "").strip()
                if not table_group_name:
                    continue
                for table_name in _unique_non_empty_texts((group or {}).get("table_names") or []):
                    key = (table_group_name, table_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    relation_rows.append({
                        "group_id": group_id,
                        "table_group_name": table_group_name,
                        "table_name": table_name,
                        "create_by": create_by or "",
                        "create_by_name": create_by_name or "",
                        "update_by": update_by or "",
                        "update_by_name": update_by_name or "",
                        "delete_flag": 0,
                    })
            for batch in _batch_items(relation_rows):
                stmt = mysql_insert(DatasourceTableGroupRel).values(batch)
                await session.execute(
                    stmt.on_duplicate_key_update(
                        delete_flag=0,
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                    )
                )

    async def get_datasource_async(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        row = await self._get_group_row_by_id_async(datasource_id)
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["ds_tables"] = await self.get_datasource_tables_by_group_id_async(datasource.get("group_id"))
        datasource["member_datasource_ids"] = await self.get_group_member_datasource_ids_async(datasource.get("group_id"))
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    async def get_datasource_meta_async(self, datasource_id: str) -> Optional[Dict[str, Any]]:
        row = await self._get_group_row_by_id_async(datasource_id)
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["member_datasource_ids"] = _parse_member_sources_text(
            row.get("member_sources_text"),
            row.get("template_source"),
            row.get("source_type"),
            None,
        )
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    async def get_datasource_by_group_name_async(self, group_name: str) -> Optional[Dict[str, Any]]:
        row = await self._get_group_row_by_name_async(group_name)
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["ds_tables"] = await self.get_datasource_tables_by_group_id_async(datasource.get("group_id"))
        datasource["member_datasource_ids"] = await self.get_group_member_datasource_ids_async(datasource.get("group_id"))
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    async def get_datasource_meta_by_group_name_async(self, group_name: str) -> Optional[Dict[str, Any]]:
        row = await self._get_group_row_by_name_async(group_name)
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["member_datasource_ids"] = _parse_member_sources_text(
            row.get("member_sources_text"),
            row.get("template_source"),
            row.get("source_type"),
            None,
        )
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    async def get_default_datasource_async(self) -> Optional[Dict[str, Any]]:
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(DatasourceGroup)
                .where(
                    DatasourceGroup.is_default == 1,
                    DatasourceGroup.delete_flag == 0,
                )
                .order_by(DatasourceGroup.id.asc())
                .limit(1)
            )
            model = query_result.scalars().first()
        row = self._group_model_to_row(model) if model else None
        if not row:
            return None
        datasource = self._map_group_row(row)
        datasource["ds_tables"] = await self.get_datasource_tables_by_group_id_async(datasource.get("group_id"))
        datasource["member_datasource_ids"] = await self.get_group_member_datasource_ids_async(datasource.get("group_id"))
        datasource["member_sources"] = self._build_member_sources(row)
        return datasource

    async def get_semantic_configs_async(self, datasource_id: str) -> List[Dict[str, Any]]:
        row = await self._get_group_row_by_id_async(datasource_id)
        if not row:
            return []
        return await self.get_semantic_configs_by_group_id_async(row.get("group_id"))

    async def get_semantic_configs_by_group_id_async(self, group_id: str, user_id: str = "") -> List[Dict[str, Any]]:
        if not group_id:
            return []
        normalized_user_id = str(user_id or "").strip()
        conditions = [SemanticConfig.group_id == group_id, SemanticConfig.delete_flag == 0]
        if normalized_user_id:
            conditions.append(or_(SemanticConfig.scope_type == "public", and_(SemanticConfig.scope_type == "private", SemanticConfig.create_by == normalized_user_id)))
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(SemanticConfig)
                .where(*conditions)
                .order_by(
                    case((SemanticConfig.scope_type == "public", 0), else_=1).asc(),
                    SemanticConfig.semantic_config_name.asc(),
                    SemanticConfig.id.asc(),
                )
            )
            rows = [self._semantic_config_model_to_row(row) for row in query_result.scalars().all()]
        return [self._map_semantic_config_row(row) for row in rows]

    async def get_semantic_config_async(self, datasource_id: str, config_id: str) -> Optional[Dict[str, Any]]:
        datasource = await self.get_datasource_async(datasource_id)
        if not datasource:
            return None
        return await self.get_semantic_config_by_group_id_async(datasource.get("group_id"), config_id)

    async def get_semantic_config_by_group_id_async(self, group_id: str, config_id: str) -> Optional[Dict[str, Any]]:
        if not group_id:
            return None
        async with self._session_scope() as session:
            query_result = await session.execute(
                select(SemanticConfig)
                .where(
                    SemanticConfig.group_id == group_id,
                    SemanticConfig.semantic_config_id == config_id,
                    SemanticConfig.delete_flag == 0,
                )
                .limit(1)
            )
            model = query_result.scalars().first()
        row = self._semantic_config_model_to_row(model) if model else None
        if not row:
            return None
        return self._map_semantic_config_row(row)

    async def upsert_datasource_group_async(
        self,
        group_id: str,
        group_name: str,
        group_desc: Optional[str] = None,
        source_type: str = "",
        template_source: str = "",
        member_sources_text: Optional[str] = None,
        query_context: Optional[str] = None,
        recommended_questions: Optional[List[str]] = None,
        create_by: str = "",
        update_by: str = "",
        create_by_name: str = "",
        update_by_name: str = "",
    ) -> None:
        stmt = mysql_insert(DatasourceGroup).values(
            group_id=group_id,
            group_name=group_name,
            group_desc=group_desc,
            query_context=query_context,
            recommended_questions_json=json.dumps(recommended_questions or [], ensure_ascii=False),
            source_type=source_type,
            template_source=template_source,
            member_sources_text=member_sources_text or template_source,
            create_by=create_by,
            create_by_name=create_by_name,
            update_by=update_by,
            update_by_name=update_by_name,
            delete_flag=0,
        )
        async with self._session_scope() as session:
            await session.execute(
                stmt.on_duplicate_key_update(
                    group_name=stmt.inserted.group_name,
                    group_desc=stmt.inserted.group_desc,
                    query_context=stmt.inserted.query_context,
                    recommended_questions_json=stmt.inserted.recommended_questions_json,
                    source_type=stmt.inserted.source_type,
                    template_source=stmt.inserted.template_source,
                    member_sources_text=stmt.inserted.member_sources_text,
                    update_by=stmt.inserted.update_by,
                    update_by_name=stmt.inserted.update_by_name,
                    delete_flag=0,
                )
            )

    async def update_datasource_group_async(
        self,
        group_id: str,
        group_name: Any = _UNSET,
        group_desc: Any = _UNSET,
        query_context: Any = _UNSET,
        recommended_questions: Any = _UNSET,
        source_type: Any = _UNSET,
        template_source: Any = _UNSET,
        member_sources_text: Any = _UNSET,
        mapped_modules: Any = _UNSET,
        is_default: Any = _UNSET,
        update_by: str = "",
        update_by_name: str = "",
    ) -> None:
        values: Dict[str, Any] = {}

        if group_name is not _UNSET:
            values["group_name"] = group_name
        if group_desc is not _UNSET:
            values["group_desc"] = group_desc
        if query_context is not _UNSET:
            values["query_context"] = query_context
        if recommended_questions is not _UNSET:
            values["recommended_questions_json"] = json.dumps(recommended_questions or [], ensure_ascii=False)
        if source_type is not _UNSET:
            values["source_type"] = source_type
        if template_source is not _UNSET:
            values["template_source"] = template_source
        if member_sources_text is not _UNSET:
            values["member_sources_text"] = member_sources_text
        if mapped_modules is not _UNSET:
            values["mapped_modules_json"] = json.dumps(mapped_modules or [], ensure_ascii=False)
        if is_default is not _UNSET:
            values["is_default"] = 1 if is_default else 0

        values["update_by"] = update_by or ""
        values["update_by_name"] = update_by_name or ""
        values["delete_flag"] = 0

        async with self._session_scope() as session:
            await session.execute(
                update(DatasourceGroup)
                .where(DatasourceGroup.group_id == group_id)
                .values(**values)
            )

    async def clear_all_default_datasources_async(self, update_by: str = "", update_by_name: str = "") -> None:
        async with self._session_scope() as session:
            await session.execute(
                update(DatasourceGroup)
                .where(
                    DatasourceGroup.delete_flag == 0,
                    DatasourceGroup.is_default == 1,
                )
                .values(
                    is_default=0,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )

    async def replace_datasource_tables_async(
        self,
        group_id: str,
        tables: List[Dict[str, Any]],
        create_by: str = "",
        update_by: str = "",
        create_by_name: str = "",
        update_by_name: str = "",
    ) -> None:
        if not group_id:
            return

        table_list = tables or []
        active_table_names = [str((table or {}).get("table_name") or "").strip() for table in table_list]
        active_table_names = [name for name in active_table_names if name]

        async with self._session_scope() as session:
            delete_conditions = [DatasourceTable.group_id == group_id]
            if active_table_names:
                delete_conditions.append(~DatasourceTable.table_names.in_(active_table_names))
            await session.execute(
                update(DatasourceTable)
                .where(*delete_conditions)
                .values(
                    delete_flag=1,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
            if not active_table_names:
                return

            table_rows_by_name = {}
            for index, table in enumerate(table_list):
                table_name = str((table or {}).get("table_name") or "").strip()
                if not table_name:
                    continue
                table_rows_by_name[table_name] = {
                    "group_id": group_id,
                    "table_names": table_name,
                    "table_desc": table.get("description"),
                    "columns_json": json.dumps(table.get("columns") or [], ensure_ascii=False),
                    "sort_no": index,
                    "create_by": create_by,
                    "create_by_name": create_by_name,
                    "update_by": update_by,
                    "update_by_name": update_by_name,
                    "delete_flag": 0,
                }
            for batch in _batch_items(list(table_rows_by_name.values())):
                stmt = mysql_insert(DatasourceTable).values(batch)
                await session.execute(
                    stmt.on_duplicate_key_update(
                        table_desc=stmt.inserted.table_desc,
                        columns_json=stmt.inserted.columns_json,
                        sort_no=stmt.inserted.sort_no,
                        update_by=stmt.inserted.update_by,
                        update_by_name=stmt.inserted.update_by_name,
                        delete_flag=0,
                    )
                )

    async def update_datasource_table_business_desc_async(self, group_id: str, table_name: str, business_desc: str, update_by: str = "", update_by_name: str = "") -> None:
        if not group_id or not table_name:
            return
        async with self._session_scope() as session:
            await session.execute(
                update(DatasourceTable)
                .where(
                    DatasourceTable.group_id == group_id,
                    DatasourceTable.table_names == table_name,
                    DatasourceTable.delete_flag == 0,
                )
                .values(
                    table_business_desc=(business_desc or "")[:255],
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )

    async def get_column_dict_async(self, group_id: str, table_name: str, column_name: str) -> Dict[str, Any]:
        if not group_id or not table_name or not column_name:
            return _normalize_column_dict({})
        async with self._session_scope() as session:
            row = (await session.execute(
                select(DatasourceColumnDict)
                .where(
                    DatasourceColumnDict.group_id == group_id,
                    DatasourceColumnDict.table_name == table_name,
                    DatasourceColumnDict.column_name == column_name,
                    DatasourceColumnDict.delete_flag == 0,
                )
            )).scalars().first()
        if row is None:
            return _normalize_column_dict({})
        return _normalize_column_dict(_load_json(row.dict_json, {}))

    async def get_column_dicts_by_group_id_async(self, group_id: str) -> Dict[tuple[str, str], Dict[str, Any]]:
        if not group_id:
            return {}
        async with self._session_scope() as session:
            rows = (await session.execute(
                select(DatasourceColumnDict)
                .where(
                    DatasourceColumnDict.group_id == group_id,
                    DatasourceColumnDict.delete_flag == 0,
                )
            )).scalars().all()
        result: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            table_name = str(row.table_name or "").strip()
            column_name = str(row.column_name or "").strip()
            if not table_name or not column_name:
                continue
            column_dict = _normalize_column_dict(_load_json(row.dict_json, {}))
            if _column_dict_is_empty(column_dict):
                continue
            result[(table_name, column_name)] = column_dict
        return result

    async def get_column_dicts_by_table_async(self, group_id: str, table_name: str) -> Dict[str, Dict[str, Any]]:
        if not group_id or not table_name:
            return {}
        async with self._session_scope() as session:
            rows = (await session.execute(
                select(DatasourceColumnDict)
                .where(
                    DatasourceColumnDict.group_id == group_id,
                    DatasourceColumnDict.table_name == table_name,
                    DatasourceColumnDict.delete_flag == 0,
                )
            )).scalars().all()
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            column_name = str(row.column_name or "").strip()
            if not column_name:
                continue
            column_dict = _normalize_column_dict(_load_json(row.dict_json, {}))
            if _column_dict_is_empty(column_dict):
                continue
            result[column_name] = column_dict
        return result

    async def replace_column_dict_async(
        self,
        group_id: str,
        table_name: str,
        column_name: str,
        column_dict: Dict[str, Any],
        create_by: str = "",
        update_by: str = "",
        create_by_name: str = "",
        update_by_name: str = "",
    ) -> Dict[str, Any]:
        if not group_id or not table_name or not column_name:
            return _normalize_column_dict({})
        normalized_dict = _normalize_column_dict(column_dict)
        async with self._session_scope() as session:
            if _column_dict_is_empty(normalized_dict):
                await session.execute(
                    update(DatasourceColumnDict)
                    .where(
                        DatasourceColumnDict.group_id == group_id,
                        DatasourceColumnDict.table_name == table_name,
                        DatasourceColumnDict.column_name == column_name,
                    )
                    .values(
                        delete_flag=1,
                        update_by=update_by or "",
                        update_by_name=update_by_name or "",
                    )
                )
                return normalized_dict
            row = {
                "group_id": group_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict_json": json.dumps(normalized_dict, ensure_ascii=False),
                "delete_flag": 0,
                "create_by": create_by or "",
                "create_by_name": create_by_name or "",
                "update_by": update_by or "",
                "update_by_name": update_by_name or "",
            }
            stmt = mysql_insert(DatasourceColumnDict).values(row)
            await session.execute(
                stmt.on_duplicate_key_update(
                    dict_json=stmt.inserted.dict_json,
                    delete_flag=0,
                    update_by=stmt.inserted.update_by,
                    update_by_name=stmt.inserted.update_by_name,
                )
            )
        return normalized_dict

    async def soft_delete_group_async(self, group_id: str, update_by: str = "", update_by_name: str = "") -> None:
        async with self._session_scope() as session:
            await session.execute(
                update(DatasourceGroup)
                .where(DatasourceGroup.group_id == group_id)
                .values(
                    delete_flag=1,
                    is_default=0,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
            await session.execute(
                update(SemanticConfig)
                .where(SemanticConfig.group_id == group_id)
                .values(
                    delete_flag=1,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
            await session.execute(
                update(DatasourceTable)
                .where(DatasourceTable.group_id == group_id)
                .values(
                    delete_flag=1,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
            await session.execute(
                update(DatasourceTableGroupRel)
                .where(DatasourceTableGroupRel.group_id == group_id)
                .values(
                    delete_flag=1,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )

    async def upsert_semantic_config_async(self, datasource_id: str, config: Dict[str, Any]) -> bool:
        datasource = await self.get_datasource_async(datasource_id)
        group_id = config.get("group_id") or (datasource or {}).get("group_id")
        if not group_id:
            logger.warning("数据源不存在或缺少 group_id，无法写入语义配置: datasource_id=%s", datasource_id)
            return False

        stmt = mysql_insert(SemanticConfig).values(
            semantic_config_id=config.get("id") or config.get("semantic_config_id"),
            group_id=group_id,
            semantic_config_name=config.get("name") or config.get("semantic_config_name") or "",
            semantic_config_desc=config.get("description") or config.get("semantic_config_desc"),
            ontology_rules=config.get("ontology_rules"),
            enabled_flag=1 if config.get("enabled", True) else 0,
            metrics_json=json.dumps(config.get("metrics") or [], ensure_ascii=False),
            dimensions_json=json.dumps(config.get("dimensions") or [], ensure_ascii=False),
            relationships_json=json.dumps(config.get("relationships") or [], ensure_ascii=False),
            scope_type=config.get("scope_type") or "public",
            sort_no=int(config.get("sort_no") or 0),
            create_by=config.get("create_by") or "",
            create_by_name=config.get("create_by_name") or "",
            update_by=config.get("update_by") or "",
            update_by_name=config.get("update_by_name") or "",
            delete_flag=0,
        )
        async with self._session_scope() as session:
            await session.execute(
                stmt.on_duplicate_key_update(
                    group_id=stmt.inserted.group_id,
                    semantic_config_name=stmt.inserted.semantic_config_name,
                    semantic_config_desc=stmt.inserted.semantic_config_desc,
                    ontology_rules=stmt.inserted.ontology_rules,
                    enabled_flag=stmt.inserted.enabled_flag,
                    metrics_json=stmt.inserted.metrics_json,
                    dimensions_json=stmt.inserted.dimensions_json,
                    relationships_json=stmt.inserted.relationships_json,
                    scope_type=stmt.inserted.scope_type,
                    sort_no=stmt.inserted.sort_no,
                    update_by=stmt.inserted.update_by,
                    update_by_name=stmt.inserted.update_by_name,
                    delete_flag=0,
                )
            )
        return True

    async def soft_delete_semantic_config_async(self, semantic_config_id: str, update_by: str = "", update_by_name: str = "") -> None:
        async with self._session_scope() as session:
            await session.execute(
                update(SemanticConfig)
                .where(SemanticConfig.semantic_config_id == semantic_config_id)
                .values(
                    delete_flag=1,
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
