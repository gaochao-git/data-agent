"""语义配置生命周期服务与保存校验。"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha1
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from utils.logger import get_logger
from ....daos.dao_session import transaction_scope
from ....daos.semantic_config_dao import SemanticConfigDao
from ..core.common import build_table_columns_map, ensure_dimension_logical_type, normalize_relationship_type
from ..core.config_validator import expression_uses_physical_column  # noqa: F401  # 测试按模块属性引用 expression_uses_physical_column，属有意重导出
from .chat_data_ossie_importer import (
    OssieSemanticModelImport,
    OssieToChatDataImporter,
    extract_metric_source_column,
    merge_carried_column_dicts,
)
from .semantic_quality_checker import check_cross_model_metrics, first_structure_error, get_semantic_quality_checker

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


def dedupe_semantic_config_ids(config_ids: Optional[List[str]]) -> List[str]:
    """清洗并去重指标集 ID，保持传入顺序，空值与重复项丢弃。"""
    result: List[str] = []
    seen: Set[str] = set()
    for config_id in config_ids or []:
        normalized = str(config_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def empty_batch_toggle_result() -> Dict[str, Any]:
    """批量切换启用状态的空结果，保证路由返回字段口径恒定。"""
    return {"updated_ids": [], "skipped_ids": [], "not_found_ids": [], "updated_count": 0}


def classify_batch_toggle_ids(
    requested: List[str],
    current_map: Dict[str, Dict[str, Any]],
    enabled: bool,
    user_id: str,
    admin_manage: bool,
) -> Dict[str, List[str]]:
    """把请求 ID 按「不存在 / 无权限 / 已处于目标状态 / 需要变更」分类，全部保持请求顺序。

    ``settled`` 指已处于目标状态的 ID：算成功但不写库。MySQL 的 UPDATE rowcount
    只统计「值真的变了」的行，把它们一起发到库里会让整批全选时得到 rowcount=0，
    前端成功数与 updated_ids 对不上，后端还会刷误导性的行数不一致告警。
    ``updated`` = updatable + settled，按请求顺序合并，便于前端逐条对照。
    """
    updatable: List[str] = []
    settled: List[str] = []
    skipped: List[str] = []
    not_found: List[str] = []
    for config_id in requested:
        config = current_map.get(config_id)
        if not config:
            not_found.append(config_id)
        elif not can_mutate_semantic_config(config, user_id, admin_manage):
            skipped.append(config_id)
        elif bool(config.get("enabled", True)) == enabled:
            settled.append(config_id)
        else:
            updatable.append(config_id)
    excluded = set(skipped) | set(not_found)
    return {
        "updatable": updatable,
        "settled": settled,
        "skipped": skipped,
        "not_found": not_found,
        "updated": [config_id for config_id in requested if config_id not in excluded],
    }


def column_dict_has_enum_mapping(column_dict: Optional[Dict[str, Any]]) -> bool:
    enum_values = (column_dict or {}).get("enum_values") if isinstance(column_dict, dict) else []
    if not isinstance(enum_values, list):
        return False
    return any(
        str((item or {}).get("value") or "").strip()
        for item in enum_values
        if isinstance(item, dict)
    )


async def validate_semantic_logical_types(
    ds_manager: Any,
    datasource_id: str,
    dimensions: List[Dict],
    *,
    column_dicts: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
) -> Optional[str]:
    """校验枚举维度都已在字段字典维护枚举值。

    ``column_dicts`` 传入数据源级字典映射（键为 (table, column)）时本地匹配，
    不再逐维度回源——批量校验（Ossie 导入预览/导入）下逐维度查询会放大成
    N×(整份数据源重载+单字典查询) 的数据库压力；不传时保持逐条查询（单模型保存链路）。
    """
    for dim in dimensions or []:
        if str((dim or {}).get("logical_type") or "").strip().lower() != "enum":
            continue
        table_name = str((dim or {}).get("table") or "").strip()
        column_name = str((dim or {}).get("column") or "").strip()
        if column_dicts is not None:
            column_dict = column_dicts.get((table_name, column_name))
        else:
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
    column_dicts: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
) -> Optional[str]:
    structure_error = first_structure_error(
        metrics,
        dimensions,
        relationships or [],
        table_columns_map=table_columns_map if table_columns_map is not None else build_table_columns_map(datasource),
    )
    if structure_error:
        return structure_error
    return await validate_semantic_logical_types(ds_manager, datasource_id, dimensions, column_dicts=column_dicts)


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
        """委托 importer 模块的同名实现（导出标注与导入回填定位同一物理列）。"""
        return extract_metric_source_column(metric)

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
    def _column_dict_key(cls, item: Dict[str, Any], *, metric: bool) -> tuple[str, str]:
        """条目的字典定位键 (table, column)；与运行时合并使用同一口径。"""
        table = str(item.get("table") or "").strip()
        if metric:
            return table, cls._extract_metric_source_column(item)
        return table, str(item.get("column") or item.get("field_name") or "").strip()

    @classmethod
    def annotate_export_column_dicts(
        cls,
        config: Dict[str, Any],
        column_dicts: Dict[tuple[str, str], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """导出专用：把命中的字段字典原文挂到各条目的 ``_column_dict`` 键（返回副本，不改原配置）。

        供物化导出链路把字典四项（enum_values/synonyms/storage_format/unit）写入
        Ossie CHAT_DATA 扩展，导入侧据此按「仅填空」策略回写字典，形成闭环；
        只在导出副本上挂载，运行时查询配置不受影响。
        """
        if not (config and column_dicts):
            return config
        result = dict(config)
        result["dimensions"] = [
            cls._with_export_column_dict(raw, column_dicts, metric=False)
            for raw in result.get("dimensions") or []
        ]
        result["metrics"] = [
            cls._with_export_column_dict(raw, column_dicts, metric=True)
            for raw in result.get("metrics") or []
        ]
        return result

    @classmethod
    def _with_export_column_dict(
        cls,
        raw_item: Dict[str, Any],
        column_dicts: Dict[tuple[str, str], Dict[str, Any]],
        *,
        metric: bool,
    ) -> Dict[str, Any]:
        item = dict(raw_item or {})
        column_dict = column_dicts.get(cls._column_dict_key(item, metric=metric))
        if column_dict:
            item["_column_dict"] = column_dict
        return item

    @classmethod
    def _merge_column_dict(cls, config: Dict[str, Any], column_dicts: Dict[tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
        result = dict(config or {})
        dimensions = []
        for raw_dimension in result.get("dimensions") or []:
            dimension = dict(raw_dimension or {})
            key = cls._column_dict_key(dimension, metric=False)
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

    async def get_runtime_bundle_async(
        self,
        datasource_id: str,
        user_id: str = "",
    ) -> tuple[List[Dict[str, Any]], Dict[tuple[str, str], Dict[str, Any]]]:
        """一次加载「全量可见配置 + 字段字典」，供批量链路复用。

        批量物化（预物化/按需物化）既要配置也要字典；分开取会让字典查库次数
        随调用次数翻倍。字典读取失败降级为空 dict（与 get_runtime_config_async
        的容错口径一致：字典只做补充标注，缺了不影响模型可用）。

        返回的 configs **已完成字段字典合并**，与逐条 get_runtime_config_async
        的结果同构：批量链路直接复用它们物化，产出的 YAML 才不会丢掉枚举取值、
        logical_type 与补充说明（这些由 _merge_column_dict 填充，导出标注用的
        annotate_export_column_dicts 只挂 _column_dict 原文，不能替代）。
        """
        configs = await self.get_configs_async(datasource_id, user_id)
        if not configs:
            return [], {}
        column_dicts = await self.get_column_dicts_async(datasource_id)
        return [self._merge_column_dict(config, column_dicts) for config in configs], column_dicts

    async def get_runtime_configs_async(self, datasource_id: str, user_id: str = "") -> List[Dict[str, Any]]:
        configs, _ = await self.get_runtime_bundle_async(datasource_id, user_id)
        return configs

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
        不会撞上墓碑行，判重只需覆盖活跃记录。实际生效的唯一索引是三列
        ``(group_id, semantic_config_name, delete_flag)``（生产 2026-09-19 核对）；
        upsert 的 ON DUPLICATE KEY UPDATE 仅作并发兜底，同名活跃记录应在此处被
        ValueError 拦截，而不是落到 IntegrityError。
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
        """为尚未建模的物理表创建公共基础单表模型（默认启用）。"""
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
            "business_notes": kwargs.get("business_notes") or "",
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
        suffix = " 自动生成，建议复核"
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
            "business_notes": "",
            # 产品决策：基础模型默认启用。生成物只是字段骨架（metrics/relationships 为空），
            # 业务口径仍需人工补充，但不再阻塞会话引用。
            "enabled": True,
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
            "business_notes": cls._resolve_updated_value(kwargs, current, "business_notes"),
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

    async def batch_toggle_enabled_async(
        self,
        datasource_id: str,
        config_ids: List[str],
        enabled: bool,
        user_id: str = "",
        username: str = "",
        admin_manage: bool = False,
    ) -> Dict[str, Any]:
        """批量切换指标集启用状态。

        越权与不存在的 ID 分别计入 skipped_ids / not_found_ids 并跳过，不中断整批；
        可维护的 ID 走一次批量 UPDATE，避免逐条往返。

        已处于目标状态的 ID 视为幂等成功（计入 updated_ids 与 updated_count 但不写库）：
        MySQL 的 UPDATE rowcount 只统计「值真的变了」的行，若把它们也发到库里，
        整批全选同态模型时会得到 rowcount=0 —— 前端据此判定的成功数会和
        updated_ids 对不上，后端也会刷一条误导性的行数不一致告警。
        """
        datasource = await self.datasource_manager.get_datasource_async(datasource_id)
        group_id = str((datasource or {}).get("group_id") or "").strip()
        if not datasource or not group_id:
            return empty_batch_toggle_result()
        requested = dedupe_semantic_config_ids(config_ids)
        if not requested:
            return empty_batch_toggle_result()
        current_map = {
            str(config.get("id") or ""): config
            for config in await self.dao.get_by_group_id_async(group_id, user_id)
        }
        classified = classify_batch_toggle_ids(requested, current_map, enabled, user_id, admin_manage)
        updatable = classified["updatable"]
        updated_count = len(classified["settled"])
        if updatable:
            changed = await self.dao.batch_update_enabled_async(group_id, updatable, enabled, user_id, username)
            if changed != len(updatable):
                logger.warning(f"批量切换启用状态实际行数与预期不一致: 预期={len(updatable)}, 实际={changed}")
            updated_count += changed
        return {
            "updated_ids": classified["updated"],
            "skipped_ids": classified["skipped"],
            "not_found_ids": classified["not_found"],
            "updated_count": updated_count,
        }


_semantic_config_service: Optional[SemanticConfigService] = None


def get_semantic_config_service() -> SemanticConfigService:
    global _semantic_config_service
    if _semantic_config_service is None:
        from ...datasource.manager import get_datasource_manager
        _semantic_config_service = SemanticConfigService(get_datasource_manager())
    return _semantic_config_service


def _add_table_physical_columns(columns: Set[Tuple[str, str]], table: Any) -> None:
    """把单个物理表的列登记进 columns 集合（{(table, column)}）。"""
    if not isinstance(table, dict):
        return
    table_name = str(table.get("table_name") or "").strip()
    for column in table.get("columns") or []:
        if not isinstance(column, dict):
            continue
        column_name = str(column.get("name") or "").strip()
        if table_name and column_name:
            columns.add((table_name, column_name))


async def load_semantic_physical_columns(ds_manager: Any, datasource_id: str) -> Optional[Set[Tuple[str, str]]]:
    """加载数据源物理列清单 {(table, column)}（get_cached_schema_async 的全量表结构）。

    导入预览 / 质量检查 / semantic_yaml_check 工具共用，保证物理清单口径一致。
    注意不能用 get_column_dicts_async——那是只含枚举/单位注释的稀疏字段字典，
    当作物理清单会把正常列全部误判为 STR_METRIC_INVALID 未知列。
    失败时返回 None（调用方质量检查跳过依赖清单的检查项）。
    """
    try:
        tables = await ds_manager.get_cached_schema_async(datasource_id)
    except Exception as e:
        logger.warning("加载物理表结构失败，质量检查跳过清单类检查项: datasource_id=%s error=%s", datasource_id, e)
        return None
    columns: Set[Tuple[str, str]] = set()
    for table in tables or []:
        _add_table_physical_columns(columns, table)
    return columns


async def prepare_ossie_import_models(
    ds_manager: Any,
    datasource: Dict,
    datasource_id: str,
    yaml_content: str,
    semantic_service: Optional["SemanticConfigService"] = None,
    user_id: str = "",
) -> Tuple[List[OssieSemanticModelImport], Optional[str]]:
    """Ossie 模型导入与 check_yaml 工具共用的校验前置（唯一权威实现，两侧各不再单独维护）。

    步骤：
    1. YAML 解析为模型草稿（格式非法抛 ValueError，调用方转成各自错误形态）；
    2. 请求内重名模型整体拒绝（避免「全有或全无」落库时互相踩踏）；
    3. 同名冲突标注：admin 口径下可操作的公共同名配置写 existing_config（可替换），
       他人私有等不可操作同名标记 conflict_locked（导入时强制跳过）；
    4. 逐模型物理校验（复用 validate_semantic_config_for_save，错误写入 item.errors
       并使 importable=False）。

    返回 (models, duplicate_error)；duplicate_error 非 None 时 models 为空列表。
    semantic_service 不传时用全局单例（仅同名冲突检测需要）。
    """
    imports = OssieToChatDataImporter().import_yaml(yaml_content)
    importable_names = [item.name for item in imports if item.importable]
    if len(set(importable_names)) != len(importable_names):
        duplicated = sorted({name for name in importable_names if importable_names.count(name) > 1})
        return [], f"文件内存在重名模型: {'、'.join(duplicated)}，请修正后重试"
    # 同名冲突检测：同组内模型名全局唯一（不区分 scope/create_by）。冲突表只收录
    # admin_manage 口径下可操作的配置（即公共配置）；他人私有配置等不可操作的同名
    # 配置不进入冲突表（不允许 replace，也不泄露其元信息），改为标记强制跳过。
    service = semantic_service or get_semantic_config_service()
    existing_configs = await service.get_configs_async(datasource_id)
    configs_by_name, locked_conflict_names = _build_existing_config_index(existing_configs, user_id)
    await _apply_physical_validation(
        imports,
        ds_manager,
        datasource_id,
        datasource,
        configs_by_name,
        locked_conflict_names,
    )
    return imports, None


def _build_existing_config_index(
    existing_configs: Any,
    user_id: str,
) -> Tuple[Dict[str, Dict[str, Any]], Set[str]]:
    """构建同名配置索引。

    同组内模型名全局唯一（不区分 scope/create_by）。冲突表只收录 admin_manage 口径下
    可操作的配置（即公共配置）；他人私有配置等不可操作的同名配置不进入冲突表
    （不允许 replace，也不泄露其元信息），改为收集到 locked 集合以便标记强制跳过。
    """
    configs_by_name: Dict[str, Dict[str, Any]] = {}
    locked_conflict_names: Set[str] = set()
    for config in existing_configs or []:
        config_name = str(config.get("name") or "").strip()
        config_id = str(config.get("id") or "").strip()
        if not config_name or not config_id:
            continue
        if config_name in configs_by_name:
            continue
        if can_mutate_semantic_config(config, user_id, admin_manage=True):
            configs_by_name[config_name] = {
                "config_id": config_id,
                "name": config_name,
                "enabled": bool(config.get("enabled")),
            }
        else:
            locked_conflict_names.add(config_name)
    return configs_by_name, locked_conflict_names


async def _apply_physical_validation(
    imports: List[OssieSemanticModelImport],
    ds_manager: Any,
    datasource_id: str,
    datasource: Dict,
    configs_by_name: Dict[str, Dict[str, Any]],
    locked_conflict_names: Set[str],
) -> None:
    """逐个可导入模型做物理校验（复用 validate_semantic_config_for_save），并落同名标记。

    错误写入 item.errors 并使 importable=False。枚举维度的字典校验用整批一次
    拉取的字段映射在本地匹配，并按「仅填空」并入 YAML 携带的字典条目
    （merge_carried_column_dicts）——新枚举维度随 YAML 带枚举值时首次导入即可通过；
    逐维度回源会让每个枚举维度都重载整份数据源
    （get_column_dict_async 每次都 get_datasource_async），模型多时是校验慢的主因。
    读取失败降级为 None（回落逐维度查询），与字典只做补充标注的容错口径一致。
    """
    try:
        column_dicts = await ds_manager.get_datasource_column_dicts_async(datasource_id)
    except Exception as exc:
        logger.warning("批量读取字段数据字典失败，枚举校验降级逐维度查询: datasource_id=%s error=%s", datasource_id, exc)
        column_dicts = None
    for item in imports:
        if not item.importable:
            continue
        item.bump("physical_checked")
        validation_error = await validate_semantic_config_for_save(
            ds_manager,
            datasource_id,
            datasource,
            item.metrics,
            item.dimensions,
            item.relationships,
            # YAML 携带的字典条目按「仅填空」并入：新枚举维度首次导入即可自足通过，
            # 与落库时仅填空回填的最终状态一致（既有字典值不覆盖）
            column_dicts=merge_carried_column_dicts(column_dicts, item.column_dicts),
        )
        if validation_error:
            item.errors.append(validation_error)
            item.validation_error = validation_error
        item.existing_config = configs_by_name.get(item.name)
        item.conflict_locked = not item.existing_config and item.name in locked_conflict_names


def _cross_model_comparison_pool(existing_configs: List[Dict[str, Any]], user_id: str) -> List[Dict[str, Any]]:
    """跨模型比较池：公共配置 + 当前用户自己的私有配置。

    他人私有模型的指标名属业务元信息，不得泄进报告；自己的私有模型
    返回给自己无泄露，且个人模型之间的口径冲突同样需要提示。
    user_id 未知（空）时只收录公共配置。
    """
    normalized_user_id = str(user_id or "").strip()
    return [
        config
        for config in existing_configs
        if is_public_semantic_config(config)
        or (normalized_user_id and is_private_semantic_config_owner(config, normalized_user_id))
    ]


async def cross_model_findings_for_imports(
    imports: List[OssieSemanticModelImport],
    datasource_id: str,
    semantic_service: Optional["SemanticConfigService"] = None,
    user_id: str = "",
) -> Dict[str, List[Dict[str, Any]]]:
    """导入链路的跨模型指标口径检查（ossie-import/preview 与 check_yaml 工具共用）。

    对每个可导入模型，计算它与「组内其他公共配置 + 同批其他导入模型」之间的
    指标口径冲突，返回 {模型名: findings}。要点：
    - 比较池=公共配置 + 当前用户自己的私有配置（user_id 已知时）：他人私有模型的
      指标名不得泄进报告，自己的私有模型参与比较（个人模型间冲突同样要提示）；
    - 排除与导入模型同名的存量配置：replace 场景下旧配置会被整体覆盖，
      新模型与它比较没有意义（会误报同名冲突）；
    - findings 不影响 importable（提示不阻断），与 quality-rules.md 跨模型节同源。
    """
    service = semantic_service or get_semantic_config_service()
    importable = [item for item in imports if item.importable]
    if not importable:
        return {}
    try:
        existing_configs = await service.get_configs_async(datasource_id, user_id) or []
    except Exception as exc:
        logger.warning("跨模型口径检查读取存量配置失败，已跳过: datasource_id=%s error=%s", datasource_id, exc)
        return {}
    import_names = {item.name for item in importable}
    comparison_pool: List[Dict[str, Any]] = [
        {**item.to_config_payload(), "enabled": False}
        for item in importable
    ]
    comparison_pool.extend(
        config
        for config in _cross_model_comparison_pool(existing_configs, user_id)
        if str(config.get("name") or "").strip() not in import_names
    )
    return {
        item.name: check_cross_model_metrics(comparison_pool, focus_config_name=item.name)
        for item in importable
    }


async def quality_findings_for_imports(
    imports: List[OssieSemanticModelImport],
    datasource_id: str,
    *,
    physical_columns: Optional[Set[Tuple[str, str]]] = None,
    semantic_service: Optional["SemanticConfigService"] = None,
    user_id: str = "",
) -> Dict[str, List[Dict[str, Any]]]:
    """导入链路逐模型质量检查编排（ossie-import/preview 与 check_yaml 工具共用）。

    单模型 5 维规则（check_config）与跨模型口径（cross_model_findings_for_imports）
    并入同一 findings 清单，靠 CON_CROSS_MODEL_* 编码区分层级。物理列由调用方加载
    并传入（调用方的 checklist 还需要 inventory_available 口径，避免重复加载）；
    不可导入模型跳过（其 checklist 由调用方基于 validation_error 生成）。
    提示不阻断：存量读取失败已在 cross_model_findings_for_imports 内降级为空。
    """
    cross_by_model = await cross_model_findings_for_imports(
        imports, datasource_id, semantic_service=semantic_service, user_id=user_id
    )
    checker = get_semantic_quality_checker()
    return {
        item.name: checker.check_config(
            # enabled/scope_type 仅占位，不参与检查项（与保存链路占位口径一致）
            {**item.to_config_payload(), "enabled": False, "scope_type": "public"},
            physical_columns=physical_columns,
        ).to_payload()["findings"] + cross_by_model.get(item.name, [])
        for item in imports
        if item.importable
    }


async def quality_findings_for_save(
    ds_manager: Any,
    datasource_id: str,
    *,
    name: str,
    metrics: List[Dict[str, Any]],
    dimensions: List[Dict[str, Any]],
    relationships: Optional[List[Dict[str, Any]]] = None,
    exclude_config_id: Optional[str] = None,
    semantic_service: Optional["SemanticConfigService"] = None,
    user_id: str = "",
) -> List[Dict[str, Any]]:
    """编辑器保存链路的质量检查（单模型 5 维规则 + 跨模型口径，与导入/工具同源）。

    校验方法多处统一的单一入口：导入 preview、check_yaml 工具、admin 质量报告
    与本函数共用同一套 RULES 与跨模型比较逻辑。要点：
    - 单模型层与 preview/check_yaml 完全同一 check_config（enabled/scope_type 仅占位）；
    - 跨模型层比较池=公共配置 + 自己的私有配置，update 场景按 exclude_config_id
      排除自身（不与自己比较）；
    - 提示不阻断：返回 findings 列表由端点附进保存响应，任一读取失败降级为空列表，
      质量提示不因基础设施抖动挡保存。
    """
    service = semantic_service or get_semantic_config_service()
    try:
        physical_columns = await load_semantic_physical_columns(ds_manager, datasource_id)
    except Exception as exc:
        logger.warning("保存链路质量检查加载物理列失败，已跳过: datasource_id=%s error=%s", datasource_id, exc)
        return []
    try:
        checker = get_semantic_quality_checker()
        single_findings = checker.check_config(
            {
                "name": name,
                "metrics": metrics,
                "dimensions": dimensions,
                "relationships": relationships or [],
                "enabled": False,
                "scope_type": "public",
            },
            physical_columns=physical_columns,
        ).to_payload()["findings"]
    except Exception as exc:
        logger.warning("保存链路单模型质量检查失败，已跳过: name=%s error=%s", name, exc)
        single_findings = []

    cross_findings: List[Dict[str, Any]] = []
    try:
        existing_configs = await service.get_configs_async(datasource_id, user_id) or []
        comparison_pool = [
            config
            for config in _cross_model_comparison_pool(existing_configs, user_id)
            if str(config.get("id") or "") != str(exclude_config_id or "")
        ]
        comparison_pool.append(
            {"name": name, "metrics": metrics, "dimensions": dimensions, "relationships": relationships or []}
        )
        cross_findings = check_cross_model_metrics(comparison_pool, focus_config_name=name)
    except Exception as exc:
        logger.warning("保存链路跨模型口径检查失败，已跳过: name=%s error=%s", name, exc)

    return _strip_self_name_from_messages(single_findings + cross_findings, name)


def _strip_self_name_from_messages(findings: List[Dict[str, Any]], config_name: str) -> List[Dict[str, Any]]:
    """单模型保存上下文里，目标是配置名本身的提示去掉消息尾部的配置名。

    保存前校验弹窗就挂在被编辑的模型之下，「…: auth_group_permissions基础模型」这类
    尾缀是重复信息；但指标/维度级 target（如「指标缺少描述: 集群数量1」）与跨模型的
    「模型.指标」目标仍然保留——导入预览等多模型场景不加此处理，名称在那里是有用定位。
    """
    suffix = f": {config_name}"
    stripped: List[Dict[str, Any]] = []
    for item in findings:
        if list(item.get("targets") or []) == [config_name] and str(item.get("message") or "").endswith(suffix):
            item = {**item, "message": item["message"][: -len(suffix)]}
        stripped.append(item)
    return stripped


# ---------------------------------------------------------------------------
# Ossie 导入落库（自 routers/semantic_config.py 下沉：事务边界归属 service 层）
# ---------------------------------------------------------------------------


class OssieImportWriteError(Exception):
    """Ossie 导入落库失败：用于触发整批回滚，并携带面向用户的具体原因。"""


async def persist_one_ossie_model(
    item: Any,
    conflict_actions: Dict[str, str],
    semantic_service: Any,
    datasource_id: str,
    user_id: str,
    username: str,
) -> Tuple[str, Dict[str, Any]]:
    """落库单个模型，返回 ``(kind, payload)``，kind ∈ created / replaced / skipped。"""
    if item.conflict_locked:
        # 同名配置不可操作（如他人私有配置）：无论 conflict_actions 是什么都跳过，
        # 也不透出其 config_id 等元信息
        return "skipped", {"name": item.name, "reason": "存在同名配置且无操作权限，已忽略"}
    if not item.existing_config:
        config = await semantic_service.create_async(
            datasource_id=datasource_id,
            scope_type="public",
            create_by=user_id,
            create_by_name=username,
            **item.to_config_payload(),
        )
        if not config:
            raise OssieImportWriteError(f"创建配置失败: {item.name}")
        return "created", {"config_id": config.get("id"), "name": config.get("name")}

    existing_config_id = str(item.existing_config["config_id"])
    if conflict_actions.get(item.name, "skip") != "replace":
        return "skipped", {
            "name": item.name,
            "config_id": existing_config_id,
            "reason": "同名配置已存在，未导入",
        }
    # 替换：保留原 config_id，全量覆盖内容；enabled 不传参 → service 沿用原启用状态
    # （description/business_notes 为空时传空串清空——替换语义以导入内容为准，不沿用旧值）
    config = await semantic_service.update_async(
        datasource_id,
        existing_config_id,
        description=item.description or "",
        business_notes=item.business_notes or "",
        metrics=item.metrics,
        dimensions=item.dimensions,
        relationships=item.relationships,
        update_by=user_id,
        update_by_name=username,
    )
    if not config:
        raise OssieImportWriteError(f"更新配置失败: {item.name}")
    return "replaced", {"config_id": config.get("id"), "name": config.get("name")}


async def fill_ossie_column_dicts(
    item: Any,
    ds_manager: Any,
    datasource_id: str,
    user_id: str,
    username: str,
) -> List[Dict[str, Any]]:
    """把模型携带的字典条目按「仅填空」写入 datasource_column_dict（须在事务内调用）。

    只处理实际落库（created/replaced）的模型：被跳过的模型不写字典，与用户
    「不导入该模型」的动作语义一致。写入失败（含物理列不存在）抛错触发整批回滚。
    """
    filled: List[Dict[str, Any]] = []
    for entry in item.column_dicts:
        fields = await ds_manager.fill_column_dict_async(
            datasource_id,
            entry["table"],
            entry["column"],
            entry["dict"],
            operator=user_id,
            operator_name=username,
        )
        if fields is None:
            raise OssieImportWriteError(f"写入数据字典失败: {entry['table']}.{entry['column']}（物理字段不存在）")
        if fields:
            filled.append({"table": entry["table"], "column": entry["column"], "fields": fields})
    return filled


async def persist_ossie_imports(
    imports: List[Any],
    conflict_actions: Dict[str, str],
    semantic_service: Any,
    ds_manager: Any,
    datasource_id: str,
    user_id: str,
    username: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """整批落库（须在 transaction_scope 内调用），返回 (created, replaced, skipped)。

    created/replaced 的模型随后在同一事务内执行字典仅填空写入，
    实际填充明细挂在各自 payload 的 column_dict_filled 上。
    """
    created: List[Dict[str, Any]] = []
    replaced: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    buckets = {"created": created, "replaced": replaced, "skipped": skipped}
    for item in imports:
        kind, payload = await persist_one_ossie_model(
            item, conflict_actions, semantic_service, datasource_id, user_id, username
        )
        if kind in ("created", "replaced") and item.column_dicts:
            payload["column_dict_filled"] = await fill_ossie_column_dicts(
                item, ds_manager, datasource_id, user_id, username,
            )
        buckets[kind].append(payload)
    return created, replaced, skipped


async def persist_ossie_imports_in_transaction(
    imports: List[Any],
    conflict_actions: Dict[str, str],
    semantic_service: Any,
    ds_manager: Any,
    datasource_id: str,
    user_id: str,
    username: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """整批落库的事务边界（全有或全无）：任一模型写入失败即整体回滚，不留半批草稿。

    字典仅填空写入与模型写入在同一事务内，模型与字典要么一起落库、要么一起回滚。
    OssieImportWriteError / ValueError 原样上抛，由路由层翻译为对应错误响应。
    """
    async with transaction_scope():
        return await persist_ossie_imports(
            imports,
            conflict_actions,
            semantic_service,
            ds_manager,
            datasource_id,
            user_id,
            username,
        )
