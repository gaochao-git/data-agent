"""语义配置生命周期服务与保存校验。"""

from __future__ import annotations

import re
from datetime import datetime
from hashlib import sha1
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from utils.logger import get_logger
from ....daos.semantic_config_dao import SemanticConfigDao
from ..core.common import build_table_columns_map, ensure_dimension_logical_type, normalize_relationship_type
from ..core.config_validator import expression_uses_physical_column, validate_semantic_fields

logger = get_logger(__name__)
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _scope_type(config: Dict[str, Any]) -> str:
    return str((config or {}).get("scope_type") or "public").strip().lower()


def is_public_semantic_config(config: Dict[str, Any]) -> bool:
    return bool(config and _scope_type(config) == "public")


def is_private_semantic_config_owner(config: Dict[str, Any], user_id: str) -> bool:
    return bool(
        config
        and _scope_type(config) == "private"
        and str((config or {}).get("create_by") or "").strip() == str(user_id or "").strip()
    )


def can_mutate_semantic_config(config: Dict[str, Any], user_id: str, admin_manage: bool = False) -> bool:
    return is_public_semantic_config(config) if admin_manage else is_private_semantic_config_owner(config, user_id)


def column_dict_has_enum_mapping(column_dict: Optional[Dict[str, Any]]) -> bool:
    enum_values = (column_dict or {}).get("enum_values") if isinstance(column_dict, dict) else []
    if not isinstance(enum_values, list):
        return False
    return any(
        str((item or {}).get("value") or "").strip()
        for item in enum_values
        if isinstance(item, dict)
    )


async def validate_semantic_logical_types(ds_manager: Any, datasource_id: str, dimensions: List[Dict]) -> Optional[str]:
    for dim in dimensions or []:
        if str((dim or {}).get("logical_type") or "").strip().lower() != "enum":
            continue
        table_name = str((dim or {}).get("table") or "").strip()
        column_name = str((dim or {}).get("column") or "").strip()
        column_dict = await ds_manager.get_column_dict_async(datasource_id, table_name, column_name)
        if not column_dict_has_enum_mapping(column_dict):
            return f"维度 {dim.get('name', '')} 选择了枚举逻辑类型，请先维护字段字典枚举值: {table_name}.{column_name}"
    return None


async def validate_semantic_config_for_save(
    ds_manager: Any,
    datasource_id: str,
    datasource: Dict,
    metrics: List[Dict],
    dimensions: List[Dict],
    relationships: Optional[List[Dict]] = None,
    *,
    table_columns_map: Optional[Dict[str, Set[str]]] = None,
) -> Optional[str]:
    validation_error = validate_semantic_fields(
        datasource,
        metrics,
        dimensions,
        relationships,
        table_columns_map=table_columns_map,
    )
    if validation_error:
        return validation_error
    return await validate_semantic_logical_types(ds_manager, datasource_id, dimensions)


class SemanticConfigService:
    """语义配置业务服务，负责权限范围内的查询、运行时增强和 CRUD。"""

    def __init__(self, datasource_manager: Any, dao: Optional[SemanticConfigDao] = None) -> None:
        self.datasource_manager = datasource_manager
        self.dao = dao or SemanticConfigDao()

    @staticmethod
    def _normalize_relationships(config: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(config or {})
        relationships = []
        for relation in normalized.get("relationships") or []:
            item = dict(relation or {})
            conditions = [
                {
                    "left_column": condition.get("left_column"),
                    "right_column": condition.get("right_column"),
                }
                for condition in item.get("join_conditions") or []
                if (condition or {}).get("left_column") and (condition or {}).get("right_column")
            ]
            if not conditions and item.get("left_column") and item.get("right_column"):
                conditions = [{"left_column": item["left_column"], "right_column": item["right_column"]}]
            if not conditions:
                continue
            item["join_conditions"] = conditions
            item["left_column"] = conditions[0]["left_column"]
            item["right_column"] = conditions[0]["right_column"]
            item["relationship_type"] = normalize_relationship_type(item.get("relationship_type"))
            relationships.append(item)
        normalized["relationships"] = relationships
        return normalized

    @staticmethod
    def _enrich_model_context(config: Dict[str, Any], datasource: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(config or {})
        result["source_type"] = result.get("source_type") or datasource.get("source_type") or "mysql"
        result["tables"] = result.get("tables") or datasource.get("ds_tables") or []
        if result.get("relationships") and not result.get("joins"):
            result["joins"] = result["relationships"]
        return result

    async def _group_id_async(self, datasource_id: str) -> Optional[str]:
        datasource = await self.datasource_manager.get_datasource_meta_async(datasource_id)
        return str((datasource or {}).get("group_id") or "").strip() or None

    async def get_configs_async(self, datasource_id: str, user_id: str = "") -> List[Dict[str, Any]]:
        datasource = await self.datasource_manager.get_datasource_async(datasource_id)
        group_id = str((datasource or {}).get("group_id") or "").strip()
        if not datasource or not group_id:
            return []
        return [
            self._enrich_model_context(self._normalize_relationships(item), datasource)
            for item in await self.dao.get_by_group_id_async(group_id, user_id)
        ]

    async def get_directory_async(self, datasource_id: str, user_id: str = "") -> List[Dict[str, Any]]:
        group_id = await self._group_id_async(datasource_id)
        return await self.dao.get_directory_by_group_id_async(group_id or "", user_id)

    async def get_config_async(self, datasource_id: str, config_id: str, user_id: str = "") -> Optional[Dict[str, Any]]:
        datasource = await self.datasource_manager.get_datasource_async(datasource_id)
        group_id = str((datasource or {}).get("group_id") or "").strip()
        if not datasource or not group_id:
            return None
        config = await self.dao.get_by_id_async(group_id, config_id, user_id)
        return self._enrich_model_context(self._normalize_relationships(config), datasource) if config else None

    @staticmethod
    def _extract_metric_source_column(metric: Dict[str, Any]) -> str:
        expression = str(metric.get("expression") or "").strip()
        match = re.match(r"^(?:COUNT|SUM|AVG|MAX|MIN)\s*\(\s*(?:DISTINCT\s+)?`?([^`(),\s]+)`?\s*\)$", expression, re.IGNORECASE)
        if match:
            return match.group(1).split(".")[-1].strip("`")
        match = re.match(r"^`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`?$", expression)
        if match:
            return match.group(1).split(".")[-1].strip("`")
        field_name = str(metric.get("field") or "").strip()
        return field_name.split(".")[-1].strip("`") if field_name else ""

    @staticmethod
    def _format_column_dict_supplement(column_dict: Dict[str, Any]) -> str:
        parts: List[str] = []
        enum_values = [
            f"{item.get('value')}={item.get('label')}" if item.get("label") else str(item.get("value"))
            for item in column_dict.get("enum_values") or []
            if item.get("value")
        ]
        if enum_values:
            parts.append(f"枚举值：{'；'.join(enum_values[:30])}")
        for label, key in (("存储格式", "storage_format"), ("单位", "unit")):
            value = str(column_dict.get(key) or "").strip()
            if value:
                parts.append(f"{label}：{value}")
        return "\n".join(parts)

    @staticmethod
    def _merge_supplement(current: Any, addition: str) -> str:
        current_text = str(current or "").strip()
        addition = str(addition or "").strip()
        if not current_text:
            return addition
        return current_text if not addition or addition in current_text else f"{current_text}\n{addition}"

    @staticmethod
    def _enum_values(column_dict: Dict[str, Any]) -> List[str]:
        return [
            str(item.get("label") or item.get("value")).strip()
            for item in column_dict.get("enum_values") or []
            if str(item.get("label") or item.get("value") or "").strip()
        ]

    @staticmethod
    def _enum_mapping(column_dict: Dict[str, Any]) -> Dict[str, str]:
        return {
            str(item.get("label") or item.get("value")).strip(): str(item.get("value")).strip()
            for item in column_dict.get("enum_values") or []
            if str(item.get("label") or item.get("value") or "").strip() and str(item.get("value") or "").strip()
        }

    @staticmethod
    def _enum_mapping_covers_values(values: List[str], mapping: Dict[str, str]) -> bool:
        value_set = {str(value).strip() for value in values if str(value or "").strip()}
        mapping_keys = {
            str(key).strip()
            for key, value in mapping.items()
            if str(key or "").strip() and str(value or "").strip()
        }
        return bool(value_set and value_set <= mapping_keys)

    @classmethod
    def _merge_dimension_column_dict(cls, dimension: Dict[str, Any], column_dict: Dict[str, Any]) -> Dict[str, Any]:
        """把字段数据字典信息合并进单个维度：枚举值/映射/逻辑类型/补充说明。"""
        values = cls._enum_values(column_dict)
        mapping = cls._enum_mapping(column_dict)
        if values and not dimension.get("allowed_values"):
            dimension["allowed_values"] = values
        if mapping and not dimension.get("mapping"):
            dimension["mapping"] = mapping
        if not dimension.get("logical_type") and cls._enum_mapping_covers_values(
            dimension.get("allowed_values") or [],
            dimension.get("mapping") or {},
        ):
            dimension["logical_type"] = "enum"
        supplement = cls._format_column_dict_supplement(column_dict)
        if supplement:
            dimension["metadata_supplement"] = cls._merge_supplement(dimension.get("metadata_supplement"), supplement)
        return dimension

    @classmethod
    def _merge_metric_column_dict(cls, metric: Dict[str, Any], column_dict: Dict[str, Any]) -> Dict[str, Any]:
        supplement = cls._format_column_dict_supplement(column_dict)
        if supplement:
            metric["metadata_supplement"] = cls._merge_supplement(metric.get("metadata_supplement"), supplement)
        return metric

    @classmethod
    def _merge_column_dict(cls, config: Dict[str, Any], column_dicts: Dict[tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
        result = dict(config or {})
        dimensions = []
        for raw_dimension in result.get("dimensions") or []:
            dimension = dict(raw_dimension or {})
            key = (str(dimension.get("table") or "").strip(), str(dimension.get("column") or dimension.get("field_name") or "").strip())
            column_dict = column_dicts.get(key)
            if column_dict:
                dimension = cls._merge_dimension_column_dict(dimension, column_dict)
            dimensions.append(ensure_dimension_logical_type(dimension))
        metrics = []
        for raw_metric in result.get("metrics") or []:
            metric = dict(raw_metric or {})
            key = (str(metric.get("table") or "").strip(), cls._extract_metric_source_column(metric))
            column_dict = column_dicts.get(key)
            if column_dict:
                metric = cls._merge_metric_column_dict(metric, column_dict)
            metrics.append(metric)
        result["dimensions"] = dimensions
        result["metrics"] = metrics
        return result

    async def get_runtime_configs_async(self, datasource_id: str, user_id: str = "") -> List[Dict[str, Any]]:
        configs = await self.get_configs_async(datasource_id, user_id)
        if not configs:
            return []
        try:
            column_dicts = await self.datasource_manager.get_datasource_column_dicts_async(datasource_id)
        except Exception as exc:
            logger.warning("读取字段数据字典失败，指标集选择降级使用原始配置: datasource_id=%s error=%s", datasource_id, exc)
            column_dicts = {}
        return [self._merge_column_dict(config, column_dicts) for config in configs]

    async def get_column_dicts_async(self, datasource_id: str) -> Dict[tuple[str, str], Dict[str, Any]]:
        """读取数据源字段字典；失败降级为空 dict（与 get_runtime_config_async 的容错口径一致）。"""
        try:
            return await self.datasource_manager.get_datasource_column_dicts_async(datasource_id)
        except Exception as exc:
            logger.warning("读取字段数据字典失败，使用原始语义配置: datasource_id=%s error=%s", datasource_id, exc)
            return {}

    async def get_runtime_config_async(
        self,
        datasource_id: str,
        config_id: str,
        user_id: str = "",
        *,
        column_dicts: Optional[Dict[tuple[str, str], Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        config = await self.get_config_async(datasource_id, config_id, user_id)
        if not config:
            return None
        if column_dicts is None:
            column_dicts = await self.get_column_dicts_async(datasource_id)
        return self._merge_column_dict(config, column_dicts)

    async def _name_exists_async(self, group_id: str, name: str, exclude_id: str = "") -> bool:
        configs = await self.dao.get_by_group_id_async(group_id)
        return self._name_exists(configs, name, exclude_id=exclude_id)

    @staticmethod
    def _name_exists(
        configs: List[Dict[str, Any]],
        name: str,
        *,
        exclude_id: str = "",
    ) -> bool:
        """同组内的模型名判重。

        **同组内模型名全局唯一，不区分 scope_type、不区分 create_by。**

        入参 configs 来自 ``get_by_group_id_async``（只含 delete_flag == 0 的活跃记录），
        这是刻意的：软删除会把 semantic_config_name 改写为 ``原名__d<id>_<日期>``
        （见 dao._DELETED_NAME_EXPR），墓碑不再占用原名，因此「删掉旧模型再建同名」
        不会撞上墓碑行，判重只需覆盖活跃记录。实际生效的唯一索引是两列
        ``(group_id, semantic_config_name)``；upsert 的 ON DUPLICATE KEY UPDATE
        仅作并发兜底，同名活跃记录应在此处被 ValueError 拦截，而不是落到
        IntegrityError。
        """
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return False
        for config in configs:
            current_id = str(config.get("id") or "").strip()
            if exclude_id and current_id == exclude_id:
                continue
            if str(config.get("name") or "").strip() == normalized_name:
                return True
        return False

    async def create_async(self, datasource_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        group_id = await self._group_id_async(datasource_id)
        if not group_id:
            return None
        name = str(kwargs.get("name") or "").strip()
        if not name:
            raise ValueError("语义配置名称不能为空")
        scope_type = str(kwargs.get("scope_type") or "private").strip().lower()
        if scope_type not in {"public", "private"}:
            raise ValueError("语义配置范围只能是 public 或 private")
        if await self._name_exists_async(group_id, name):
            raise ValueError(f"语义配置名称已存在: {name}")
        config = self._build_persisted_config(group_id, **kwargs)
        if not await self.dao.upsert_async(group_id, config):
            return None
        return await self.get_config_async(datasource_id, config["id"])

    async def _scan_existing_single_table_state(self, group_id: str) -> tuple[set, set, list]:
        """扫描数据源组现有公共模型，返回 (已配置物理表, 已占用名称, 全部配置)。"""
        all_configs = await self.dao.get_by_group_id_async(group_id)
        visible_configs = [config for config in all_configs if is_public_semantic_config(config)]
        configured_tables = {
            table
            for config in visible_configs
            for table in self._config_physical_tables(config)
        }
        existing_names = {
            str(config.get("name") or "").strip()
            for config in visible_configs
            if str(config.get("name") or "").strip()
        }
        return configured_tables, existing_names, all_configs

    async def _process_basic_single_table(
        self,
        table: Dict[str, Any],
        *,
        group_id: str,
        datasource: Dict[str, Any],
        datasource_id: str,
        table_columns_map: Dict[str, Set[str]],
        configured_tables: set,
        existing_names: set,
        all_configs: list,
        create_by: str,
        create_by_name: str,
    ) -> tuple[str, Any]:
        """处理单张物理表的基础模型生成，返回 (status, payload)。

        status ∈ {"skipped", "failed", "pending"}；skipped/failed 携带原因文案，
        仅 pending 携带待持久化配置。
        """
        table_name = str((table or {}).get("table_name") or "").strip()
        generated_name = self._basic_single_table_name(table_name)
        if (
            table_name in configured_tables
            or generated_name in existing_names
            or self._name_exists(all_configs, generated_name)
        ):
            return "skipped", None
        try:
            config = self._build_basic_single_table_config(
                table,
                name=generated_name,
                create_by=create_by,
                create_by_name=create_by_name,
            )
            validation_error = await validate_semantic_config_for_save(
                self.datasource_manager,
                datasource_id,
                datasource,
                config["metrics"],
                config["dimensions"],
                config["relationships"],
                table_columns_map=table_columns_map,
            )
            if validation_error:
                logger.warning("批量生成基础语义模型字段校验失败: table=%s error=%s", table_name, validation_error)
                return "failed", "字段校验失败"
            persisted = self._build_persisted_config(group_id, **config)
            configured_tables.add(table_name)
            existing_names.add(generated_name)
            return "pending", (table_name, persisted)
        except Exception as exc:
            logger.warning("批量生成基础语义模型失败: table=%s error=%s", table_name, exc, exc_info=True)
            return "failed", "模型生成失败"

    async def _persist_basic_single_table_configs(
        self,
        group_id: str,
        datasource_id: str,
        pending_configs: List[tuple[str, Dict[str, Any]]],
        result: Dict[str, Any],
        item_order: List[str],
    ) -> None:
        """批量写入基础模型；批量失败回退单表写入。"""
        persisted_configs = [config for _, config in pending_configs]
        try:
            if not await self.dao.upsert_many_async(group_id, persisted_configs):
                raise RuntimeError("批量写入失败")
            for table_name, config in pending_configs:
                result["created"] += 1
                self._set_generation_item(
                    result,
                    table_name,
                    {"table": table_name, "status": "created", "semantic_config_id": config["id"]},
                    item_order,
                )
        except Exception as exc:
            logger.warning("批量写入基础语义模型失败，回退到单表写入: datasource_id=%s error=%s", datasource_id, exc, exc_info=True)
            for table_name, config in pending_configs:
                try:
                    if not await self.dao.upsert_async(group_id, config):
                        raise RuntimeError("创建失败")
                    result["created"] += 1
                    self._set_generation_item(
                        result,
                        table_name,
                        {"table": table_name, "status": "created", "semantic_config_id": config["id"]},
                        item_order,
                    )
                except Exception as item_exc:
                    logger.warning("单表写入基础语义模型失败: table=%s error=%s", table_name, item_exc, exc_info=True)
                    result["failed"] += 1
                    self._set_generation_item(
                        result,
                        table_name,
                        {"table": table_name, "status": "failed", "reason": "写入失败"},
                        item_order,
                    )

    async def generate_basic_public_single_table_configs_async(
        self,
        datasource_id: str,
        *,
        create_by: str = "",
        create_by_name: str = "",
    ) -> Dict[str, Any]:
        """为尚未建模的物理表创建禁用的公共基础单表模型。"""
        datasource = await self.datasource_manager.get_datasource_async(datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {datasource_id}")

        group_id = str(datasource.get("group_id") or "").strip()
        configured_tables, existing_names, all_configs = await self._scan_existing_single_table_state(group_id)
        result = {"created": 0, "skipped": 0, "failed": 0, "items": []}
        pending_configs = []
        item_order: List[str] = []
        seen_tables: set[str] = set()
        table_columns_map = build_table_columns_map(datasource)
        for table in datasource.get("ds_tables") or []:
            table_name = str((table or {}).get("table_name") or "").strip()
            if not table_name or table_name in seen_tables:
                continue
            seen_tables.add(table_name)
            item_order.append(table_name)
            status, payload = await self._process_basic_single_table(
                table,
                group_id=group_id,
                datasource=datasource,
                datasource_id=datasource_id,
                table_columns_map=table_columns_map,
                configured_tables=configured_tables,
                existing_names=existing_names,
                all_configs=all_configs,
                create_by=create_by,
                create_by_name=create_by_name,
            )
            if status == "skipped":
                result["skipped"] += 1
                self._set_generation_item(result, table_name, {"table": table_name, "status": "skipped", "reason": "已有语义模型"}, item_order)
            elif status == "failed":
                result["failed"] += 1
                self._set_generation_item(result, table_name, {"table": table_name, "status": "failed", "reason": payload or "模型生成失败"}, item_order)
            else:
                pending_configs.append(payload)
        if not pending_configs:
            return result
        await self._persist_basic_single_table_configs(group_id, datasource_id, pending_configs, result, item_order)
        return result

    @staticmethod
    def _set_generation_item(result: Dict[str, Any], table_name: str, item: Dict[str, Any], item_order: List[str]) -> None:
        for index, current in enumerate(result["items"]):
            if current.get("table") == table_name:
                result["items"][index] = item
                return
        existing_by_table = {current.get("table"): current for current in result["items"]}
        existing_by_table[table_name] = item
        result["items"] = [existing_by_table[table] for table in item_order if table in existing_by_table]

    @staticmethod
    def _build_persisted_config(group_id: str, **kwargs: Any) -> Dict[str, Any]:
        """构造统一的持久化配置，供单条和批量创建复用。"""
        operator = str(kwargs.get("create_by") or "").strip()
        now = datetime.now().strftime(TIME_FORMAT)
        config = {
            "id": kwargs.get("id") or f"sc_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:12]}",
            "group_id": group_id,
            "name": str(kwargs.get("name") or "").strip(),
            "description": kwargs.get("description") or "",
            "ontology_rules": kwargs.get("ontology_rules") or "",
            "enabled": kwargs.get("enabled", True),
            "metrics": kwargs.get("metrics") or [],
            "dimensions": kwargs.get("dimensions") or [],
            "relationships": kwargs.get("relationships") or [],
            "scope_type": str(kwargs.get("scope_type") or "private").strip().lower(),
            "create_time": now,
            "update_time": now,
            "create_by": operator,
            "create_by_name": kwargs.get("create_by_name") or "",
            "update_by": operator,
            "update_by_name": kwargs.get("create_by_name") or "",
        }
        return SemanticConfigService._normalize_relationships(config)

    @staticmethod
    def _basic_single_table_name(table_name: str) -> str:
        suffix = "基础模型"
        raw_name = f"{table_name}{suffix}"
        if len(raw_name) <= 128:
            return raw_name
        digest = sha1(table_name.encode("utf-8")).hexdigest()[:8]
        stable_suffix = f"_{digest}{suffix}"
        return f"{table_name[:128 - len(stable_suffix)]}{stable_suffix}"

    @staticmethod
    def _basic_single_table_description(table_name: str) -> str:
        prefix = "基于物理表 "
        suffix = " 自动生成，待人工复核后启用"
        available = max(128 - len(prefix) - len(suffix), 0)
        return f"{prefix}{table_name[:available]}{suffix}"[:128]

    @staticmethod
    def _config_physical_tables(config: Dict[str, Any]) -> set[str]:
        tables: set[str] = set()
        for item in [*(config.get("metrics") or []), *(config.get("dimensions") or [])]:
            if isinstance(item, dict) and item.get("table"):
                tables.add(str(item["table"]).strip())
        for relationship in config.get("relationships") or []:
            if isinstance(relationship, dict):
                tables.update(
                    str(relationship.get(key) or "").strip()
                    for key in ("left_table", "right_table")
                    if str(relationship.get(key) or "").strip()
                )
        return tables

    @classmethod
    def _build_basic_single_table_config(
        cls,
        table: Dict[str, Any],
        *,
        name: str,
        create_by: str,
        create_by_name: str,
    ) -> Dict[str, Any]:
        table_name = str(table.get("table_name") or "").strip()
        dimensions = []
        used_names: set[str] = set()
        for column in table.get("columns") or []:
            if not isinstance(column, dict):
                continue
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            display_name = str(column.get("description") or "").strip() or column_name
            display_name = cls._unique_dimension_name(display_name, used_names)
            used_names.add(display_name)
            physical_type = str(
                column.get("mysql_type") or column.get("hive_type")
                or column.get("type") or column.get("data_type") or ""
            ).strip().lower()
            dimensions.append({
                "name": display_name,
                "field_name": column_name,
                "column": column_name,
                "expression": f"`{column_name.replace('`', '``')}`",
                "table": table_name,
                "table_alias": table_name,
                "logical_type": cls._infer_basic_logical_type(physical_type),
                "description": "",
            })
        return {
            "name": name,
            "description": cls._basic_single_table_description(table_name),
            "ontology_rules": "",
            "enabled": False,
            "metrics": [],
            "dimensions": dimensions,
            "relationships": [],
            "scope_type": "public",
            "create_by": create_by,
            "create_by_name": create_by_name,
        }

    @staticmethod
    def _unique_dimension_name(name: str, used_names: set[str]) -> str:
        if name not in used_names:
            return name
        suffix = 2
        while f"{name}{suffix}" in used_names:
            suffix += 1
        return f"{name}{suffix}"

    @staticmethod
    def _infer_basic_logical_type(physical_type: str) -> str:
        normalized = str(physical_type or "").strip().lower()
        if any(token in normalized for token in ("timestamp", "datetime")):
            return "datetime"
        if any(token in normalized for token in ("date", "time")):
            return "date"
        if any(token in normalized for token in ("int", "decimal", "numeric", "float", "double", "real")):
            return "number"
        return "text"

    async def update_async(self, datasource_id: str, config_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        current = await self.get_config_async(datasource_id, config_id)
        if not current:
            return None
        name = str(kwargs["name"]).strip() if kwargs.get("name") is not None else str(current.get("name") or "").strip()
        if not name:
            raise ValueError("语义配置名称不能为空")
        if await self._name_exists_async(str(current.get("group_id") or ""), name, exclude_id=config_id):
            raise ValueError(f"语义配置名称已存在: {name}")
        next_config = self._build_update_payload(current, kwargs, name)
        next_config = self._normalize_relationships(next_config)
        if not await self.dao.upsert_async(str(current.get("group_id") or ""), next_config):
            return None
        return await self.get_config_async(datasource_id, config_id)

    @staticmethod
    def _resolve_updated_value(kwargs: Dict[str, Any], current: Dict[str, Any], key: str, fallback: Any = None) -> Any:
        """显式传参（非 None）优先，否则沿用当前值；fallback 为当前值缺省。"""
        value = kwargs.get(key)
        if value is not None:
            return value
        return current.get(key, fallback)

    @classmethod
    def _build_update_payload(cls, current: Dict[str, Any], kwargs: Dict[str, Any], name: str) -> Dict[str, Any]:
        return {
            **current,
            "name": name,
            "description": cls._resolve_updated_value(kwargs, current, "description"),
            "ontology_rules": cls._resolve_updated_value(kwargs, current, "ontology_rules"),
            "enabled": cls._resolve_updated_value(kwargs, current, "enabled", True),
            "metrics": cls._resolve_updated_value(kwargs, current, "metrics", []),
            "dimensions": cls._resolve_updated_value(kwargs, current, "dimensions", []),
            "relationships": cls._resolve_updated_value(kwargs, current, "relationships", []),
            "update_time": datetime.now().strftime(TIME_FORMAT),
            "update_by": str(kwargs.get("update_by") or current.get("update_by") or "").strip(),
            "update_by_name": str(kwargs.get("update_by_name") or current.get("update_by_name") or "").strip(),
        }

    async def delete_async(self, datasource_id: str, config_id: str, update_by: str = "", update_by_name: str = "") -> bool:
        config = await self.get_config_async(datasource_id, config_id)
        if not config:
            return False
        await self.dao.soft_delete_async(config_id, update_by=update_by or config.get("update_by") or "", update_by_name=update_by_name or config.get("update_by_name") or "")
        return True


_semantic_config_service: Optional[SemanticConfigService] = None


def get_semantic_config_service() -> SemanticConfigService:
    global _semantic_config_service
    if _semantic_config_service is None:
        from ...datasource.manager import get_datasource_manager
        _semantic_config_service = SemanticConfigService(get_datasource_manager())
    return _semantic_config_service
