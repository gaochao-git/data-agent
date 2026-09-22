"""
数据源数据库存储。

当前运行时以 datasource_group 作为唯一数据源入口：
- datasource_group: 组即数据源
- datasource_table: 数据源表结构缓存
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from utils.models import DatasourceColumnDict, DatasourceGroup, DatasourceTable, DatasourceTableGroupRel
from utils.logger import get_logger
from .datasource_dao_helpers import (
    _batch_items,
    _column_dict_is_empty,
    _format_time,
    _load_json,
    _merge_table_indexes_for_sync,
    _normalize_table_indexes_payload,
    _normalize_column_dict,
    _parse_member_source_entries,
    _parse_member_source_entry,
    _parse_member_sources_text,
    _parse_source_line,
    _unique_non_empty_texts,
)
from .dao_session import _ORM_SESSION, session_scope, transaction_scope
from .semantic_config_dao import SemanticConfigDao

logger = get_logger(__name__)

_UNSET = object()
_MAX_RECOMMENDED_QUESTION_COUNT = 50
_MAX_RECOMMENDED_QUESTION_LENGTH = 50


def _or_value(value: Any, default: Any) -> Any:
    """等价于 `value or default` 的显式兜底，供映射字典复用。"""
    return value or default


def _append_group_relation_rows(
    relation_rows: List[Dict[str, Any]],
    seen: set,
    group_id: str,
    table_group_name: str,
    table_names: Any,
    *,
    create_by: str,
    create_by_name: str,
    update_by: str,
    update_by_name: str,
) -> None:
    """按 (table_group_name, table_name) 去重追加单个分组的关系行。"""
    for table_name in _unique_non_empty_texts(table_names or []):
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


class DataSourceDao:
    """基于 MySQL 的数据源数据访问层。"""

    def __init__(self) -> None:
        self.semantic_config_dao = SemanticConfigDao()

    _session_scope = staticmethod(session_scope)
    transaction_async = staticmethod(transaction_scope)

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

    def _datasource_table_model_to_row(self, model: DatasourceTable) -> Dict[str, Any]:
        return {
            "group_id": model.group_id,
            "table_name": model.table_names,
            "table_desc": model.table_desc,
            "table_business_desc": model.table_business_desc,
            "columns_json": model.columns_json,
            "indexes_json": getattr(model, "indexes_json", None),
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

    @staticmethod
    def _resolve_group_template_info(row: Dict[str, Any]) -> tuple[str, Dict[str, Any], str]:
        """解析分组模板来源；返回 (template_source, 模板解析结果, 模板成员 ID)。"""
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
        return template_source, parsed_template, template_member_id

    def _map_group_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        group_id = row.get("group_id") or ""
        template_source, parsed_template, template_member_id = self._resolve_group_template_info(row)
        return {
            "id": group_id,
            "datasource_id": group_id,
            "group_id": group_id,
            "group_ids": [group_id] if group_id else [],
            "template_datasource_id": template_member_id,
            "template_source": template_source,
            "member_sources_text": _or_value(row.get("member_sources_text"), ""),
            "group_name": _or_value(row.get("group_name"), ""),
            "ds_description": _or_value(row.get("group_desc"), ""),
            "query_context": _or_value(row.get("query_context"), ""),
            "recommended_questions": self._normalize_recommended_questions(row.get("recommended_questions_json")),
            "source_type": _or_value(row.get("source_type"), ""),
            "query_executor_type": _or_value(parsed_template.get("query_executor_type"), "direct"),
            "create_time": _or_value(_format_time(row.get("create_time")), ""),
            "update_time": _or_value(_format_time(row.get("update_time")), ""),
            "create_by": _or_value(row.get("create_by"), ""),
            "create_by_name": _or_value(row.get("create_by_name"), ""),
            "update_by": _or_value(row.get("update_by"), ""),
            "update_by_name": _or_value(row.get("update_by_name"), ""),
            "ds_tables": [],
            "mapped_modules": _load_json(row.get("mapped_modules_json"), []),
            "is_default": bool(row.get("is_default", 0)),
            "source_config": {
                "host": parsed_template.get("host"),
                "port": parsed_template.get("port"),
                "database": parsed_template.get("database"),
            },
        }

    def _map_datasource_table_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        columns = _load_json(row.get("columns_json"), [])
        indexes = _normalize_table_indexes_payload(row.get("indexes_json"), columns)
        physical_indexes = indexes["physical"]
        manual_indexes = indexes["manual"]
        return {
            "table_name": row.get("table_name") or "",
            "description": row.get("table_desc") or row.get("table_name") or "",
            "business_desc": row.get("table_business_desc") or "",
            "columns": columns,
            "primary_keys": [item["columns"] for item in physical_indexes if item["index_type"] == "primary"],
            "unique_indexes": [
                {"name": item["index_name"], "columns": item["columns"], "source": "physical"}
                for item in physical_indexes if item["index_type"] == "unique"
            ],
            "indexes": [
                {"name": item["index_name"], "columns": item["columns"], "source": "physical"}
                for item in physical_indexes if item["index_type"] == "secondary"
            ],
            "manual_indexes": [
                {**item, "source": "manual"}
                for item in manual_indexes
            ],
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
            await self._mark_group_relations_deleted(session, group_id, update_by=update_by, update_by_name=update_by_name)
            relation_rows = self._build_group_relation_rows(group_id, groups, create_by=create_by, create_by_name=create_by_name, update_by=update_by, update_by_name=update_by_name)
            await self._insert_group_relation_rows(session, relation_rows, update_by=update_by, update_by_name=update_by_name)

    @staticmethod
    async def _mark_group_relations_deleted(session, group_id: str, *, update_by: str, update_by_name: str) -> None:
        """软删该分组现有表关系。"""
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

    @staticmethod
    def _build_group_relation_rows(
        group_id: str,
        groups: List[Dict[str, Any]],
        *,
        create_by: str,
        create_by_name: str,
        update_by: str,
        update_by_name: str,
    ) -> List[Dict[str, Any]]:
        """按 (table_group_name, table_name) 去重构建关系行。"""
        relation_rows = []
        seen: set[tuple[str, str]] = set()
        for group in groups or []:
            table_group_name = str((group or {}).get("table_group_name") or "").strip()
            if not table_group_name:
                continue
            _append_group_relation_rows(
                relation_rows,
                seen,
                group_id,
                table_group_name,
                (group or {}).get("table_names"),
                create_by=create_by,
                create_by_name=create_by_name,
                update_by=update_by,
                update_by_name=update_by_name,
            )
        return relation_rows

    @staticmethod
    async def _insert_group_relation_rows(session, relation_rows: List[Dict[str, Any]], *, update_by: str, update_by_name: str) -> None:
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

    @staticmethod
    def _build_table_sync_rows(
        group_id: str,
        tables: List[Dict[str, Any]],
        existing_by_name: Dict[str, Any],
        *,
        create_by: str,
        create_by_name: str,
        update_by: str,
        update_by_name: str,
    ) -> List[Dict[str, Any]]:
        # 与拆分前的 dict 构建保持一致：同名表 last-wins 去重，
        # 保留最后一次出现的行（含其 sort_no）。
        rows_by_name: Dict[str, Dict[str, Any]] = {}
        for index, table in enumerate(tables):
            table_name = str((table or {}).get("table_name") or "").strip()
            if not table_name:
                continue
            existing = existing_by_name.get(table_name)
            indexes = _merge_table_indexes_for_sync(
                table,
                getattr(existing, "indexes_json", None) if existing is not None else None,
            )
            rows_by_name[table_name] = {
                "group_id": group_id,
                "table_names": table_name,
                "table_desc": table.get("description"),
                "columns_json": json.dumps(table.get("columns") or [], ensure_ascii=False),
                "indexes_json": json.dumps(indexes, ensure_ascii=False),
                "sort_no": index,
                "create_by": create_by,
                "create_by_name": create_by_name,
                "update_by": update_by,
                "update_by_name": update_by_name,
                "delete_flag": 0,
            }
        return list(rows_by_name.values())

    async def _upsert_table_rows(self, session, rows: List[Dict[str, Any]]) -> None:
        for batch in _batch_items(rows):
            stmt = mysql_insert(DatasourceTable).values(batch)
            await session.execute(
                stmt.on_duplicate_key_update(
                    table_desc=stmt.inserted.table_desc,
                    columns_json=stmt.inserted.columns_json,
                    indexes_json=stmt.inserted.indexes_json,
                    sort_no=stmt.inserted.sort_no,
                    update_by=stmt.inserted.update_by,
                    update_by_name=stmt.inserted.update_by_name,
                    delete_flag=0,
                )
            )

    @staticmethod
    async def _load_existing_table_rows(session, group_id: str, active_table_names: List[str]) -> Dict[str, Any]:
        existing_result = await session.execute(
            select(DatasourceTable).where(
                DatasourceTable.group_id == group_id,
                DatasourceTable.table_names.in_(active_table_names),
            ).with_for_update()
        )
        return {
            str(row.table_names or "").strip(): row
            for row in existing_result.scalars().all()
            if str(row.table_names or "").strip()
        }

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

            existing_by_name = await self._load_existing_table_rows(session, group_id, active_table_names)

            table_rows = self._build_table_sync_rows(
                group_id,
                table_list,
                existing_by_name,
                create_by=create_by,
                create_by_name=create_by_name,
                update_by=update_by,
                update_by_name=update_by_name,
            )
            await self._upsert_table_rows(session, table_rows)

    async def get_datasource_table_indexes_async(self, group_id: str, table_name: str) -> Optional[Dict[str, Any]]:
        """读取单表索引 JSON，供管理服务只更新 manual 分区。"""
        if not group_id or not table_name:
            return None
        async with self._session_scope() as session:
            result = await session.execute(
                select(DatasourceTable).where(
                    DatasourceTable.group_id == group_id,
                    DatasourceTable.table_names == table_name,
                    DatasourceTable.delete_flag == 0,
                )
            )
            row = result.scalars().first()
        if row is None:
            return None
        columns = _load_json(row.columns_json, [])
        return _normalize_table_indexes_payload(getattr(row, "indexes_json", None), columns)

    async def update_datasource_table_indexes_async(
        self,
        group_id: str,
        table_name: str,
        manual_indexes: List[Dict[str, Any]],
        update_by: str = "",
        update_by_name: str = "",
    ) -> Optional[List[Dict[str, Any]]]:
        """只更新单表索引 JSON 的 manual 分区。"""
        if not group_id or not table_name:
            return None
        async with self._session_scope() as session:
            result = await session.execute(
                select(DatasourceTable).where(
                    DatasourceTable.group_id == group_id,
                    DatasourceTable.table_names == table_name,
                    DatasourceTable.delete_flag == 0,
                ).with_for_update()
            )
            row = result.scalars().first()
            if row is None:
                return None
            columns = _load_json(row.columns_json, [])
            payload = _normalize_table_indexes_payload(getattr(row, "indexes_json", None), columns)
            payload["manual"] = manual_indexes
            await session.execute(
                update(DatasourceTable)
                .where(
                    DatasourceTable.group_id == group_id,
                    DatasourceTable.table_names == table_name,
                    DatasourceTable.delete_flag == 0,
                )
                .values(
                    indexes_json=json.dumps(payload, ensure_ascii=False),
                    update_by=update_by or "",
                    update_by_name=update_by_name or "",
                )
            )
        return manual_indexes

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

    async def fill_column_dict_async(
        self,
        group_id: str,
        table_name: str,
        column_name: str,
        column_dict: Dict[str, Any],
        create_by: str = "",
        update_by: str = "",
        create_by_name: str = "",
        update_by_name: str = "",
    ) -> List[str]:
        """仅填空写单个字段数据字典：现值为空的字段用导入值填充，已有值一律保留。

        返回实际填充的字段名列表；无可填充内容时不产生任何写入。
        与 replace 的语义差异：replace 以传入内容全量覆盖（含清空），fill 只补空缺，
        供 Ossie 导入闭环使用（拍板：冲突策略=仅填空，不走草稿）。
        """
        if not group_id or not table_name or not column_name:
            return []
        normalized_incoming = _normalize_column_dict(column_dict)
        if _column_dict_is_empty(normalized_incoming):
            return []
        async with self._session_scope() as session:
            row = (await session.execute(
                select(DatasourceColumnDict).where(
                    DatasourceColumnDict.group_id == group_id,
                    DatasourceColumnDict.table_name == table_name,
                    DatasourceColumnDict.column_name == column_name,
                )
            )).scalars().first()
            existing = _normalize_column_dict(_load_json(row.dict_json, {}) if row else {})
            filled = [
                key for key in ("enum_values", "synonyms", "storage_format", "unit")
                if normalized_incoming[key] and not existing[key]
            ]
            if not filled:
                return []
            merged = dict(existing)
            for key in filled:
                merged[key] = normalized_incoming[key]
            row_values = {
                "group_id": group_id,
                "table_name": table_name,
                "column_name": column_name,
                "dict_json": json.dumps(_normalize_column_dict(merged), ensure_ascii=False),
                "delete_flag": 0,
                "create_by": create_by or "",
                "create_by_name": create_by_name or "",
                "update_by": update_by or "",
                "update_by_name": update_by_name or "",
            }
            stmt = mysql_insert(DatasourceColumnDict).values(row_values)
            await session.execute(
                stmt.on_duplicate_key_update(
                    dict_json=stmt.inserted.dict_json,
                    delete_flag=0,
                    update_by=stmt.inserted.update_by,
                    update_by_name=stmt.inserted.update_by_name,
                )
            )
            return filled

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
            semantic_config_dao = getattr(self, "semantic_config_dao", None) or SemanticConfigDao()
            await semantic_config_dao.soft_delete_by_group_id_async(
                group_id,
                update_by=update_by,
                update_by_name=update_by_name,
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
