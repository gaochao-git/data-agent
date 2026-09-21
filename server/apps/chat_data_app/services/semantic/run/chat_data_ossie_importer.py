"""Convert Apache Ossie documents back to ChatData semantic config drafts.

导入方向：Ossie YAML → ChatData 语义配置草稿（与 chat_data_ossie_converter 的导出互为逆操作）。

核心约束：
- 启用状态由路由层决定：新建默认启用，替换同名配置沿用原状态（enabled 不传参）；
- 物理正确性闸门复用 validate_semantic_config_for_save（表/列存在性、
  派生/复合分型、关系连通性），本模块只做结构映射与归属解析；
- CHAT_DATA custom_extensions 是 round-trip 锚：派生/复合指标口径只从扩展
  还原，禁止从代入后的 expression 反推（导出时复合指标表达式已是成员代入
  结果，反推必丢公式结构）；
- 无扩展（AI 从零生成/外部文件）时按"唯一 dataset 推断 > 记入 errors"处理，
  绝不猜测归属表。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, List, Optional, Tuple

import yaml
from ossie import OSIDataType, OSIDocument, OSISemanticModel
from pydantic import ValidationError

from ..core.common import LOGICAL_TYPE_VALUES, normalize_relationship_type
from .semantic_quality_checker import RULES as _QUALITY_RULES

CHAT_DATA_VENDOR_NAME = "CHAT_DATA"
_ANSI_SQL_DIALECT = "ANSI_SQL"
_PLAIN_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_OSIE_DATATYPE_TO_LOGICAL_TYPE = {
    OSIDataType.STRING: "text",
    OSIDataType.INTEGER: "number",
    OSIDataType.DECIMAL: "number",
    OSIDataType.FLOAT: "number",
    OSIDataType.DATE: "date",
    OSIDataType.TIME: "datetime",
    OSIDataType.DATE_TIME: "datetime",
    OSIDataType.DATE_TIME_TZ: "datetime",
}

_OSIE_DATATYPE_TO_DATA_TYPE = {
    OSIDataType.STRING: "string",
    OSIDataType.INTEGER: "int",
    OSIDataType.DECIMAL: "decimal",
    OSIDataType.FLOAT: "float",
    OSIDataType.BOOLEAN: "boolean",
    OSIDataType.DATE: "date",
    OSIDataType.TIME: "time",
    OSIDataType.DATE_TIME: "timestamp",
    OSIDataType.DATE_TIME_TZ: "timestamp",
}

# AI 生成/外部导出的 YAML 常用 SQL 类型名而非 OSI 枚举名（如 FLOAT、BIGINT、varchar），
# 解析前做确定性归一：能映射的改写为 OSI 枚举名，映射不到的原样保留交给 schema 校验报错。
_OSSIE_DATATYPE_ALIASES = {
    "string": "String", "str": "String", "varchar": "String", "char": "String", "text": "String",
    "integer": "Integer", "int": "Integer", "bigint": "Integer", "smallint": "Integer", "tinyint": "Integer",
    "decimal": "Decimal", "numeric": "Decimal",
    "float": "Float", "double": "Float", "real": "Float",
    "boolean": "Boolean", "bool": "Boolean",
    "date": "Date",
    "time": "Time",
    "datetime": "DateTime", "timestamp": "DateTime",
    "datetimetz": "DateTimeTz", "timestamptz": "DateTimeTz",
    "opaque": "Opaque", "unknown": "Opaque", "other": "Opaque",
}


@dataclass
class OssieSemanticModelImport:
    """单个 Ossie semantic_model 的导入结果：配置草稿 + 问题清单。"""

    name: str
    description: Optional[str] = None
    business_notes: Optional[str] = None
    metrics: List[Dict[str, Any]] = dataclass_field(default_factory=list)
    dimensions: List[Dict[str, Any]] = dataclass_field(default_factory=list)
    relationships: List[Dict[str, Any]] = dataclass_field(default_factory=list)
    errors: List[str] = dataclass_field(default_factory=list)
    warnings: List[str] = dataclass_field(default_factory=list)
    source_config_id: Optional[str] = None
    # 结构化计数：供校验清单（build_model_checklist）确定性推导各检查项状态，
    # 避免 checklist 依赖对 errors/warnings 文本做脆弱的字符串匹配。
    stats: Dict[str, int] = dataclass_field(default_factory=dict)
    # 物理校验（validate_semantic_config_for_save）的失败结论；None=未执行或通过
    validation_error: Optional[str] = None
    # 数据源组内同名活跃配置摘要（{config_id, name, enabled}）；None=无重名冲突。
    # 仅做提示与导入动作选择，不影响 importable（冲突可由 replace/skip 解决）。
    existing_config: Optional[Dict[str, Any]] = None
    # 存在同名但当前不可操作的配置（如他人私有配置，不进入 existing_config 以免泄露
    # 其元信息）：不允许 replace，导入时强制跳过。同样不影响 importable。
    conflict_locked: bool = False
    # 从 CHAT_DATA 扩展提取的字段字典条目 [{table, column, dict}]（仅含四项字典内容
    # 非空的字段）。导入落库时按「仅填空」策略回写 datasource_column_dict（拍板：
    # 不走草稿，只填充现有值为空的字段，已有值一律保留）；派生/复合指标无物理列，不提取。
    column_dicts: List[Dict[str, Any]] = dataclass_field(default_factory=list)

    def bump(self, key: str, delta: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + delta

    @property
    def importable(self) -> bool:
        return not self.errors

    def to_config_payload(self) -> Dict[str, Any]:
        """转成 SemanticConfigCreateRequest 兼容的创建参数（enabled 由路由层决定）。"""
        return {
            "name": self.name,
            "description": self.description,
            "business_notes": self.business_notes,
            "metrics": self.metrics,
            "dimensions": self.dimensions,
            "relationships": self.relationships,
        }

    def to_preview_payload(self) -> Dict[str, Any]:
        payload = self.to_config_payload()
        payload.update({
            "source_config_id": self.source_config_id,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "importable": self.importable,
            "existing_config": dict(self.existing_config) if self.existing_config else None,
            "conflict_locked": bool(self.conflict_locked),
            # 仅填空字典闭环：模型携带的原始字典条目（是否真的填充由预览端对照现有字典计算）
            "column_dicts": [dict(entry) for entry in self.column_dicts],
        })
        return payload


def parse_ossie_document(yaml_content: str) -> OSIDocument:
    """解析 Ossie YAML 全文为 OSIDocument；格式非法抛 ValueError（消息面向用户）。"""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Ossie 文档不是合法的 YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Ossie 文档必须是 YAML 映射（顶层为 version / semantic_model）")
    # yaml 会把 version: 1.0 解析成 float，而 OSI schema 要求字符串，做一次容错
    if "version" in data and not isinstance(data["version"], (str, type(None))):
        data["version"] = str(data["version"])
    _normalize_ossie_datatypes(data)
    try:
        return OSIDocument.model_validate(data)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        location = ".".join(str(part) for part in first_error.get("loc", []))
        raise ValueError(f"Ossie 文档结构校验失败（{location}）: {first_error.get('msg')}") from exc


def _normalize_ossie_datatypes(data: Dict[str, Any]) -> None:
    """原地归一 YAML 中各 datatype 节点的别名（仅接受确定映射，不做猜测）。

    OSSI 中 datatype 出现在 semantic_model[].metrics[] 与
    semantic_model[].datasets[].fields[]（维度即 dataset 字段）。
    """
    models = data.get("semantic_model")
    if not isinstance(models, list):
        return
    for model in models:
        if not isinstance(model, dict):
            continue
        item_lists = [model.get("metrics")]
        datasets = model.get("datasets")
        if isinstance(datasets, list):
            item_lists.extend(dataset.get("fields") for dataset in datasets if isinstance(dataset, dict))
        for items in item_lists:
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw = item.get("datatype")
                if not isinstance(raw, str):
                    continue
                mapped = _OSSIE_DATATYPE_ALIASES.get(raw.strip().lower())
                if mapped:
                    item["datatype"] = mapped


def _optional_str(value: Any) -> Optional[str]:
    """归一成可选字符串：去空白后为空串的一律按 None 处理。

    集中承担 ``str(x or "").strip() or None`` 这段模板，避免在转换函数里重复铺开
    ——它既是可读性问题，也是圈复杂度的主要来源（每个 ``or`` 都算一个分支）。
    """
    return str(value or "").strip() or None


def _extension_data(extensions: Any, warnings: List[str], label: str) -> Dict[str, Any]:
    """提取第一个可解析的 CHAT_DATA 扩展；无扩展返回空 dict。"""
    for ext in extensions or []:
        if str(getattr(ext, "vendor_name", "") or "") != CHAT_DATA_VENDOR_NAME:
            continue
        try:
            payload = json.loads(ext.data)
        except (TypeError, ValueError):
            warnings.append(f"{label} 的 CHAT_DATA 扩展不是合法 JSON，已忽略")
            continue
        if isinstance(payload, dict):
            return payload
        warnings.append(f"{label} 的 CHAT_DATA 扩展不是对象，已忽略")
    return {}


# 字段数据字典四项内容键（与 datasource_column_dict.dict_json 结构一致）
_COLUMN_DICT_FIELD_KEYS = ("enum_values", "synonyms", "storage_format", "unit")
# 仅填空填充明细的展示名（preview 清单 / checklist 消息共用）
_COLUMN_DICT_FIELD_LABELS = {
    "enum_values": "枚举值",
    "synonyms": "同义词",
    "storage_format": "存储格式",
    "unit": "单位",
}


def _enum_values_from_raw(raw_enums: Any) -> List[Dict[str, str]]:
    """清洗枚举值列表（value 去重截断，结构不合法的项忽略）。"""
    if not isinstance(raw_enums, list):
        return []
    enum_values: List[Dict[str, str]] = []
    seen_values: set = set()
    for item in raw_enums:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()[:128]
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        enum_values.append({"value": value, "label": str(item.get("label") or "").strip()[:255]})
    return enum_values[:100]


def _synonyms_from_raw(raw_synonyms: Any) -> List[str]:
    """清洗同义词列表（保序去重截断）。"""
    if not isinstance(raw_synonyms, list):
        return []
    synonyms: List[str] = []
    for value in raw_synonyms:
        text = str(value or "").strip()[:100]
        if text and text not in synonyms:
            synonyms.append(text)
    return synonyms[:50]


def _column_dict_from_extension(extension: Dict[str, Any]) -> Dict[str, Any]:
    """从 CHAT_DATA 扩展提取字段字典四项（宽松归一，结构不合法的键忽略）。

    只做提取与基础清洗；落库前的权威归一由 DAO 侧 _normalize_column_dict 兜底。
    """
    raw = extension.get("column_dict")
    if not isinstance(raw, dict):
        return {}
    payload: Dict[str, Any] = {}
    enum_values = _enum_values_from_raw(raw.get("enum_values"))
    if enum_values:
        payload["enum_values"] = enum_values
    synonyms = _synonyms_from_raw(raw.get("synonyms"))
    if synonyms:
        payload["synonyms"] = synonyms
    for key, max_length in (("storage_format", 255), ("unit", 100)):
        text = str(raw.get(key) or "").strip()[:max_length]
        if text:
            payload[key] = text
    return payload


def extract_metric_source_column(metric: Dict[str, Any]) -> str:
    """从指标配置提取来源物理列：聚合表达式 SUM(col) > 单一标识符 > field 字段。

    运行时字典合并（SemanticConfigService._column_dict_key）与本模块的导入侧
    推断共用同一实现，保证导出标注与导入回填定位同一物理列。
    """
    expression = str(metric.get("expression") or "").strip()
    match = re.match(r"^(?:COUNT|SUM|AVG|MAX|MIN)\s*\(\s*(?:DISTINCT\s+)?`?([^`(),\s]+)`?\s*\)$", expression, re.IGNORECASE)
    if match:
        return match.group(1).split(".")[-1].strip("`")
    match = re.match(r"^`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`?$", expression)
    if match:
        return match.group(1).split(".")[-1].strip("`")
    field_name = str(metric.get("field") or "").strip()
    return field_name.split(".")[-1].strip("`") if field_name else ""


def plan_column_dict_fills(
    item: "OssieSemanticModelImport",
    existing_column_dicts: Dict[Tuple[str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """对照现有字典计算「仅填空」计划：返回 [{table, column, fields}]，fields 为将填充的字段名。

    判空口径与 DAO 落库一致：现有字典该字段为空且导入值非空才填充；
    仅作预览确认面，落库时以写入瞬间的字典状态由 DAO 重新计算。
    """
    plan: List[Dict[str, Any]] = []
    for entry in item.column_dicts:
        existing = existing_column_dicts.get((entry["table"], entry["column"])) or {}
        fields = [
            key for key in _COLUMN_DICT_FIELD_KEYS
            if entry["dict"].get(key) and not existing.get(key)
        ]
        if fields:
            plan.append({"table": entry["table"], "column": entry["column"], "fields": fields})
    return plan


def _dialect_value(dialect: Any) -> str:
    return str(getattr(dialect, "value", dialect) or "")


def _ansi_sql_expression(expression_obj: Any) -> Optional[str]:
    """取 ANSI_SQL 方言表达式；只认 ANSI_SQL，其他方言不猜。"""
    for dialect_expression in getattr(expression_obj, "dialects", None) or []:
        if _dialect_value(getattr(dialect_expression, "dialect", None)) == _ANSI_SQL_DIALECT:
            return str(getattr(dialect_expression, "expression", "") or "").strip()
    return None


def _strip_backticks(value: str) -> str:
    return value.replace("`", "").strip()


def _resolve_table(
    ext_table: str,
    dataset_names: List[str],
    errors: List[str],
    warnings: List[str],
    label: str,
) -> Tuple[Optional[str], str]:
    """表归属解析：CHAT_DATA 扩展 > 唯一 dataset 推断 > 记入 errors。

    返回 (表名, 结果)：结果为 declared（扩展声明）/ inferred（推断）/ failed（失败），
    供校验清单统计，errors/warnings 行为保持不变。
    """
    table = str(ext_table or "").strip()
    if table:
        return table, "declared"
    if len(dataset_names) == 1:
        warnings.append(f"{label} 缺少 CHAT_DATA 扩展的 table，按唯一 dataset 推断归属: {dataset_names[0]}")
        return dataset_names[0], "inferred"
    errors.append(f"{label} 缺少 CHAT_DATA 扩展的 table，且模型包含多个 dataset，无法确定归属表")
    return None, "failed"


class OssieToChatDataImporter:
    """Build ChatData semantic config drafts from an Ossie document."""

    def import_yaml(self, yaml_content: str) -> List[OssieSemanticModelImport]:
        """解析 YAML 后复用 import_document（两者唯一差别在文档来源）。"""
        return self.import_document(parse_ossie_document(yaml_content))

    def import_document(self, document: OSIDocument) -> List[OssieSemanticModelImport]:
        models = document.semantic_model or []
        if not models:
            raise ValueError("Ossie 文档不包含 semantic_model，没有可导入的内容")
        return [self._convert_model(model) for model in models]

    def _convert_model(self, model: OSISemanticModel) -> OssieSemanticModelImport:
        result = self._new_import_result(model)
        if not result.name:
            result.errors.append("semantic_model 缺少 name，无法导入")
            return result
        label = f"语义模型 {result.name}"

        model_extension = _extension_data(model.custom_extensions, result.warnings, label)
        result.source_config_id = _optional_str(model_extension.get("semantic_config_id"))
        # snapshot_role=model_reference_only 不产生 warning：来源配置提示（source_config_id）按产品口径
        # 不在导入预览里展示，字段本身保留，供调用方与后续追溯使用。

        dataset_names = self._resolve_dataset_names(model, label, result)
        if dataset_names is None:
            return result

        for dataset in model.datasets or []:
            for ossie_field in dataset.fields or []:
                self._convert_field(ossie_field, dataset_names, result)
        self._restore_dimension_order(result, model_extension)
        for metric in model.metrics or []:
            self._convert_metric(metric, dataset_names, result)
        for index, relationship in enumerate(model.relationships or [], start=1):
            self._convert_relationship(relationship, index, result)
        return result

    @staticmethod
    def _restore_dimension_order(result: OssieSemanticModelImport, model_extension: Dict[str, Any]) -> None:
        """按导出时记录的平铺顺序还原 dimensions（就地稳定排序）。

        Ossie 的 dataset→field 结构天然把维度按表聚簇，与库中平铺顺序不一致；
        导出侧在模型扩展 dimension_order 里记录原始 `table.column` 顺序，这里还原。
        清单缺失（外部文件/旧快照）时不动；不在清单里的维度（手工新增）稳定排在已知维度之后。
        """
        keys = [key for key in model_extension.get("dimension_order") or [] if isinstance(key, str) and key]
        if not keys or not result.dimensions:
            return
        order_index: Dict[str, int] = {}
        for index, key in enumerate(keys):
            order_index.setdefault(key, index)
        result.dimensions.sort(
            key=lambda entry: order_index.get(f"{entry.get('table')}.{entry.get('column')}", len(order_index))
        )

    @staticmethod
    def _new_import_result(model: OSISemanticModel) -> OssieSemanticModelImport:
        """构造导入结果骨架；名称/描述/业务说明的空值与 None 归一集中在此。"""
        return OssieSemanticModelImport(
            name=str(model.name or "").strip(),
            description=_optional_str(model.description),
            business_notes=_optional_str(model.ai_context),
        )

    @staticmethod
    def _resolve_dataset_names(
        model: OSISemanticModel,
        label: str,
        result: OssieSemanticModelImport,
    ) -> Optional[List[str]]:
        """收集 dataset 名；存在重名时记录错误并返回 None（调用方据此放弃该模型）。"""
        names = [str(dataset.name or "").strip() for dataset in model.datasets or []]
        if len(set(names)) != len(names):
            result.errors.append(f"{label} 存在重名 dataset: {names}")
            return None
        return names

    def _convert_field(self, ossie_field: Any, dataset_names: List[str], result: OssieSemanticModelImport) -> None:
        name = str(ossie_field.name or "").strip()
        if not name:
            result.errors.append("dataset 存在缺少名称的字段，已跳过")
            return
        label = f"维度 {name}"
        extension = _extension_data(ossie_field.custom_extensions, result.warnings, label)

        expression = _ansi_sql_expression(ossie_field.expression)
        if expression is None:
            result.errors.append(f"{label} 缺少 ANSI_SQL 方言表达式，无法导入")
            return

        table, attribution = _resolve_table(extension.get("table"), dataset_names, result.errors, result.warnings, label)
        result.bump(f"attribution_{attribution}")
        if table is None:
            return

        column = self._field_column(extension, expression, label, result)
        if column is None:
            return
        self._record_column_dict(result, extension, table, column)

        entry: Dict[str, Any] = {"name": name, "table": table, "column": column, "expression": expression}
        # 逻辑字段名 / 表别名：导出侧写入扩展，原样还原，保证往返无损
        field_name = _optional_str(extension.get("field_name")) or ""
        if field_name:
            entry["field_name"] = field_name
        table_alias = _optional_str(extension.get("table_alias")) or ""
        if table_alias:
            entry["table_alias"] = table_alias
        # logical_type：扩展携带的存储值优先（enum 等无法由 datatype 还原），其余按 datatype 映射
        logical_type = self._field_logical_type(extension, ossie_field, label, result)
        if logical_type:
            entry["logical_type"] = logical_type
        description = _optional_str(ossie_field.description)
        if description:
            entry["description"] = description
        result.dimensions.append(entry)

    @staticmethod
    def _field_column(
        extension: Dict[str, Any],
        expression: str,
        label: str,
        result: OssieSemanticModelImport,
    ) -> Optional[str]:
        """确定物理字段：优先扩展 column，缺失时按单一标识符表达式推断。

        两者都拿不到时记录错误并返回 None（调用方据此跳过该维度）。
        """
        column = _optional_str(extension.get("column")) or ""
        if column:
            return column
        candidate = _strip_backticks(expression)
        if _PLAIN_IDENTIFIER_PATTERN.match(candidate):
            result.warnings.append(f"{label} 缺少 CHAT_DATA 扩展的 column，按表达式推断物理字段: {candidate}")
            return candidate
        result.errors.append(f"{label} 缺少 CHAT_DATA 扩展的 column，且表达式不是单一物理字段（{expression}），无法确定")
        return None

    @staticmethod
    def _field_logical_type(
        extension: Dict[str, Any],
        ossie_field: Any,
        label: str,
        result: OssieSemanticModelImport,
    ) -> str:
        """确定逻辑类型：扩展存储值优先，其余按 datatype 映射；时间维度做日期兜底。"""
        logical_type = _optional_str(extension.get("logical_type")) or ""
        if logical_type not in LOGICAL_TYPE_VALUES:
            logical_type = _OSIE_DATATYPE_TO_LOGICAL_TYPE.get(ossie_field.datatype) or ""
        dimension = ossie_field.dimension
        if dimension is not None and dimension.is_time and logical_type not in ("date", "datetime"):
            result.warnings.append(f"{label} 标记为时间维度但类型不是日期/时间，默认按 date 处理，请人工确认")
            return "date"
        return logical_type

    def _convert_metric(self, ossie_metric: Any, dataset_names: List[str], result: OssieSemanticModelImport) -> None:
        name = str(ossie_metric.name or "").strip()
        if not name:
            result.errors.append("semantic_model 存在缺少名称的指标，已跳过")
            return
        label = f"指标 {name}"
        extension = _extension_data(ossie_metric.custom_extensions, result.warnings, label)
        metric_kind = str(extension.get("metric_kind") or "").strip()

        if metric_kind == "derived":
            self._convert_derived_metric(ossie_metric, name, extension, result)
            return
        if metric_kind == "composite":
            self._convert_composite_metric(ossie_metric, name, extension, result)
            return

        # 原子指标（无 metric_kind 扩展，或扩展值无法识别时按原子处理）
        expression = _ansi_sql_expression(ossie_metric.expression)
        if not expression:
            result.errors.append(f"{label} 缺少 ANSI_SQL 方言表达式，无法导入")
            return
        table, attribution = _resolve_table(extension.get("table"), dataset_names, result.errors, result.warnings, label)
        result.bump(f"attribution_{attribution}")
        if table is None:
            return
        entry: Dict[str, Any] = {"name": name, "expression": expression, "table": table}
        self._apply_common_metric_fields(entry, extension, ossie_metric)
        self._record_column_dict(
            result, extension, table, self._metric_source_column(extension, expression),
        )
        result.metrics.append(entry)

    @staticmethod
    def _metric_source_column(extension: Dict[str, Any], expression: str) -> str:
        """指标字典回填的物理列：扩展声明优先，否则按指标源列规则提取；拿不到就返回空串（不回填）。"""
        column = _optional_str(extension.get("column")) or ""
        if column:
            return column
        return extract_metric_source_column({"expression": expression})

    @staticmethod
    def _record_column_dict(
        result: "OssieSemanticModelImport",
        extension: Dict[str, Any],
        table: str,
        column: str,
    ) -> None:
        """模型内去重（同字段取首见）后记录字典条目；无有效内容或定位不到物理列时跳过。"""
        column_dict = _column_dict_from_extension(extension)
        if not column_dict or not table or not column:
            return
        if any(entry["table"] == table and entry["column"] == column for entry in result.column_dicts):
            return
        result.column_dicts.append({"table": table, "column": column, "dict": column_dict})

    @staticmethod
    def _apply_common_metric_fields(
        entry: Dict[str, Any],
        extension: Dict[str, Any],
        ossie_metric: Any,
    ) -> None:
        """写入原子指标共有的可选字段：逻辑字段名 / 表别名 / data_type / 描述。"""
        # 逻辑字段名 / 表别名：与维度同理，导出侧写入扩展，原样还原
        field_name = _optional_str(extension.get("field_name"))
        if field_name:
            entry["field_name"] = field_name
        table_alias = _optional_str(extension.get("table_alias"))
        if table_alias:
            entry["table_alias"] = table_alias
        # data_type：扩展携带的存储值优先（decimal(10,2) 等精度形态无法由 datatype 还原）
        data_type = _optional_str(extension.get("data_type"))
        if not data_type:
            data_type = _OSIE_DATATYPE_TO_DATA_TYPE.get(ossie_metric.datatype)
        if data_type:
            entry["data_type"] = data_type
        description = _optional_str(ossie_metric.description)
        if description:
            entry["description"] = description

    def _convert_derived_metric(
        self,
        ossie_metric: Any,
        name: str,
        extension: Dict[str, Any],
        result: OssieSemanticModelImport,
    ) -> None:
        label = f"指标 {name}"
        base_metric = str(extension.get("base_metric") or "").strip()
        if not base_metric:
            result.errors.append(f"{label} 声明为派生指标但缺少扩展中的 base_metric，无法还原口径")
            result.bump("kind_failed")
            return
        entry: Dict[str, Any] = {
            "name": name,
            "metric_kind": "derived",
            "base_metric": base_metric,
            "expression": "",
            "table": "",
        }
        period = extension.get("period")
        if isinstance(period, dict) and period:
            entry["period"] = period
        filters = extension.get("filters")
        if isinstance(filters, list) and filters:
            entry["filters"] = filters
        description = str(ossie_metric.description or "").strip()
        if description:
            entry["description"] = description
        result.metrics.append(entry)
        result.bump("kind_restored")

    def _convert_composite_metric(
        self,
        ossie_metric: Any,
        name: str,
        extension: Dict[str, Any],
        result: OssieSemanticModelImport,
    ) -> None:
        label = f"指标 {name}"
        formula = str(extension.get("formula") or "").strip()
        if not formula:
            result.errors.append(f"{label} 声明为复合指标但缺少扩展中的 formula，无法还原口径")
            result.bump("kind_failed")
            return
        entry: Dict[str, Any] = {
            "name": name,
            "metric_kind": "composite",
            "formula": formula,
            "expression": "",
            "table": "",
        }
        description = str(ossie_metric.description or "").strip()
        if description:
            entry["description"] = description
        result.metrics.append(entry)
        result.bump("kind_restored")

    def _convert_relationship(self, ossie_relationship: Any, index: int, result: OssieSemanticModelImport) -> None:
        label = f"关系[{index}]"
        extension = _extension_data(ossie_relationship.custom_extensions, result.warnings, label)
        from_table, to_table, from_columns, to_columns = self._relation_endpoints(ossie_relationship)
        if not from_table or not to_table:
            result.errors.append(f"{label} 缺少 from/to 数据集，无法导入")
            return
        if not from_columns or len(from_columns) != len(to_columns):
            result.errors.append(f"{label} 关联字段为空或左右数量不一致，无法导入")
            return

        relationship_type_raw = str(extension.get("relationship_type") or "").strip()
        try:
            relationship_type = normalize_relationship_type(relationship_type_raw)
        except ValueError as exc:
            result.errors.append(f"{label} {exc}")
            return
        join_type = self._relation_join_type(extension, label, result)
        left_table, right_table, has_ext = self._resolve_relation_sides(
            extension, from_table, to_table, relationship_type, label, result
        )
        # 导出方向还原：one_to_many 导出时反转过方向（from=右表/to=左表）
        left_columns, right_columns = self._relation_columns(
            relationship_type, has_ext, from_columns, to_columns
        )

        join_conditions = [
            {"left_column": left_column, "right_column": right_column}
            for left_column, right_column in zip(left_columns, right_columns)
        ]
        entry = {
            "left_table": left_table,
            "right_table": right_table,
            "join_conditions": join_conditions,
            "relationship_type": relationship_type,
            "join_type": join_type,
        }
        # 顶层快捷列是 UI/DB 关系行的镜像字段（= join_conditions[0]）：还原以保证
        # 「导出 → 导入」往返与现有配置对齐，否则差异视图会把这两行显示为删除。
        if join_conditions:
            entry["left_column"] = join_conditions[0]["left_column"]
            entry["right_column"] = join_conditions[0]["right_column"]
        result.relationships.append(entry)

    @staticmethod
    def _relation_endpoints(
        ossie_relationship: Any,
    ) -> Tuple[str, str, List[str], List[str]]:
        """抽取关系两端表名与 join 字段列表（去空白，空集合归一为 []）。"""
        from_table = str(getattr(ossie_relationship, "from_dataset", "") or "").strip()
        to_table = str(ossie_relationship.to or "").strip()
        from_columns = [str(column or "").strip() for column in ossie_relationship.from_columns or []]
        to_columns = [str(column or "").strip() for column in ossie_relationship.to_columns or []]
        return from_table, to_table, from_columns, to_columns

    @staticmethod
    def _relation_join_type(
        extension: Dict[str, Any],
        label: str,
        result: OssieSemanticModelImport,
    ) -> str:
        """归一化 join_type：仅接受 inner/left/right，其余退化 left 并给出警告。"""
        join_type = str(extension.get("join_type") or "").strip().lower()
        if join_type in ("inner", "left", "right"):
            return join_type
        if join_type:
            result.warnings.append(f"{label} 的 join_type 不支持（{join_type}），按 left 处理")
        return "left"

    @staticmethod
    def _resolve_relation_sides(
        extension: Dict[str, Any],
        from_table: str,
        to_table: str,
        relationship_type: str,
        label: str,
        result: OssieSemanticModelImport,
    ) -> Tuple[str, str, bool]:
        """确定 left/right 表：优先 CHAT_DATA 扩展，缺失时按 from→to 兜底并告警。

        返回 ``(left_table, right_table, has_ext_tables)``：第三个值表示左右表方向
        是否由扩展给出，仅当它为 True 时才需要还原 one_to_many 的导出逆序。
        """
        ext_left = str(extension.get("left_table") or "").strip()
        ext_right = str(extension.get("right_table") or "").strip()
        if not (ext_left and ext_right):
            # 无扩展（外部文件）：按 from→to 方向导入，from 侧为多端
            result.warnings.append(
                f"{label} 缺少 CHAT_DATA 扩展的左右表方向，按 from→to 导入"
                f"（基数默认 {relationship_type}），请人工确认"
            )
            return from_table, to_table, False
        if {ext_left, ext_right} != {from_table, to_table}:
            result.warnings.append(
                f"{label} 的扩展表 ({ext_left}/{ext_right}) 与 from/to "
                f"({from_table}/{to_table}) 不一致，以扩展为准"
            )
        return ext_left, ext_right, True

    @staticmethod
    def _relation_columns(
        relationship_type: str,
        has_ext_tables: bool,
        from_columns: List[str],
        to_columns: List[str],
    ) -> Tuple[List[str], List[str]]:
        """导出时 one_to_many 反转过方向（from=右表/to=左表），有扩展时按此还原。"""
        if relationship_type == "one_to_many" and has_ext_tables:
            return to_columns, from_columns
        return from_columns, to_columns


def build_model_checklist(
    item: OssieSemanticModelImport,
    validation_error: Optional[str] = None,
    quality_findings: Optional[List[Dict[str, Any]]] = None,
    inventory_available: bool = True,
    dict_fill_plan: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """把单个模型的既有判定结果映射为校验清单（五组，逐行状态）。

    纯展示层翻译：检查逻辑零改动，行状态由 stats 计数 / validation_error /
    quality findings 确定性推导，保证工具、导入预览与实际闸门同一口径。

    - validation_error：validate_semantic_config_for_save 的返回（None=通过）；
    - quality_findings：SemanticQualityChecker 报告的 findings（可选，None=未执行）；
    - inventory_available：数据源物理表结构是否可用（不可用时物理/清单类检查降级为 skip）；
    - dict_fill_plan：仅填空字典填充计划（plan_column_dict_fills 的结果；None=未评估，
      如 agent check_yaml 工具路径未对照现有字典）。

    行结构：{group, name, status, message}，status ∈ pass / warn / fail / skip。
    不输出质量分与等级——分数无法指导动作，触发明细才有操作价值。
    """
    checklist: List[Dict[str, str]] = []

    _append_schema_rows(checklist)
    _append_mapping_rows(checklist, item)
    _append_physical_row(checklist, item, validation_error, inventory_available)
    _append_quality_row(checklist, quality_findings)
    _append_dict_row(checklist, item, dict_fill_plan)
    return checklist


def _append_schema_rows(checklist: List[Dict[str, str]]) -> None:
    """① OSSI schema 校验：走到这里即解析成功（失败在更早的 parse 阶段整体报错）。"""
    checklist.append({
        "group": "schema",
        "name": "文档结构合法性",
        "status": "pass",
        "message": "OSSI 官方 schema 校验通过（version、semantic_model 结构、表达式方言）",
    })
    checklist.append({
        "group": "schema",
        "name": "datatype 枚举合法性",
        "status": "pass",
        "message": "全部字段类型为 OSI 合法枚举（SQL 常见别名已自动归一）",
    })


def _append_mapping_rows(checklist: List[Dict[str, str]], item: OssieSemanticModelImport) -> None:
    """② 结构映射：表归属 + 派生/复合还原 + 模型名冲突，状态由 stats 计数推导。"""
    declared = item.stats.get("attribution_declared", 0)
    inferred = item.stats.get("attribution_inferred", 0)
    attribution_failed = item.stats.get("attribution_failed", 0)
    if attribution_failed:
        checklist.append({
            "group": "mapping",
            "name": "表归属确定",
            "status": "fail",
            "message": f"{attribution_failed} 个对象无法确定归属表（扩展声明 {declared} · 推断 {inferred} · 失败 {attribution_failed}）",
        })
    elif inferred:
        checklist.append({
            "group": "mapping",
            "name": "表归属确定",
            "status": "warn",
            "message": f"{inferred} 个对象未声明归属表，按唯一 dataset 推断（扩展声明 {declared}）",
        })
    else:
        checklist.append({
            "group": "mapping",
            "name": "表归属确定",
            "status": "pass",
            "message": f"{declared} 个对象均由 CHAT_DATA 扩展显式声明归属表",
        })

    derived_count = sum(1 for metric in item.metrics if metric.get("metric_kind") == "derived")
    composite_count = sum(1 for metric in item.metrics if metric.get("metric_kind") == "composite")
    kind_failed = item.stats.get("kind_failed", 0)
    if kind_failed:
        checklist.append({
            "group": "mapping",
            "name": "派生 / 复合指标还原",
            "status": "fail",
            "message": f"{kind_failed} 个派生/复合指标缺少 base_metric 或 formula，无法还原口径",
        })
    else:
        kind_message = "模型内无派生/复合指标" if derived_count + composite_count == 0 else (
            f"派生 {derived_count} 个、复合 {composite_count} 个，成员引用全部解析"
        )
        checklist.append({
            "group": "mapping",
            "name": "派生 / 复合指标还原",
            "status": "pass",
            "message": kind_message,
        })

    # 同名配置不再作为检查项：覆盖已有模型再导入是正常工作流，「替换/忽略」动作
    # 由前端模型行徽章与文件行冲突入口表达。仅保留无操作权限（如他人私有配置）的
    # 同名场景——导入会被强制跳过、用户预期落空，这才是需要警示的异常。
    if item.conflict_locked:
        checklist.append({
            "group": "mapping",
            "name": "模型名冲突",
            "status": "warn",
            "message": "已存在同名配置但无操作权限，导入时将忽略该模型",
        })


def _append_physical_row(
    checklist: List[Dict[str, str]],
    item: OssieSemanticModelImport,
    validation_error: Optional[str],
    inventory_available: bool,
) -> None:
    """③ 物理校验：validate_semantic_config_for_save 的整体结论（表/列存在性 + 关系连通性）。

    只有 stats 记录了 physical_checked 才真正执行过（结构映射失败的模型不会送检）。
    """
    if not inventory_available:
        checklist.append({
            "group": "physical",
            "name": "物理字段校验",
            "status": "skip",
            "message": "数据源表结构缓存不可用，物理存在性检查已跳过（不阻断导入预览）",
        })
    elif not item.stats.get("physical_checked"):
        checklist.append({
            "group": "physical",
            "name": "物理字段校验",
            "status": "skip",
            "message": "存在结构映射错误，物理校验未执行",
        })
    elif validation_error:
        checklist.append({
            "group": "physical",
            "name": "物理字段校验",
            "status": "fail",
            "message": validation_error,
        })
    else:
        metric_count = len(item.metrics)
        dimension_count = len(item.dimensions)
        relationship_count = len(item.relationships)
        checklist.append({
            "group": "physical",
            "name": "物理字段校验",
            "status": "pass",
            "message": f"指标 {metric_count} · 维度 {dimension_count} · 关系 {relationship_count} 全部命中数据源物理元数据",
        })


def _quality_severity_counts(quality_findings: List[Dict[str, Any]]) -> Dict[str, int]:
    """按 severity 统计质量 findings 的错误 / 警告 / 建议条数（其余等级忽略）。"""
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in quality_findings:
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def _append_quality_row(
    checklist: List[Dict[str, str]],
    quality_findings: Optional[List[Dict[str, Any]]],
) -> None:
    """④ 质量规则核对：只报触发项与计数，不输出评分。"""
    if quality_findings is None:
        checklist.append({
            "group": "quality",
            "name": "质量规则核对",
            "status": "skip",
            "message": "仅对可导入模型执行质量检查，本模型未执行",
        })
        return
    counts = _quality_severity_counts(quality_findings)
    error_count = counts["error"]
    warning_count = counts["warning"]
    info_count = counts["info"]
    if not (error_count or warning_count or info_count):
        checklist.append({
            "group": "quality",
            "name": "质量规则核对",
            "status": "pass",
            "message": f"{len(_QUALITY_RULES)} 条质量规则全部未触发",
        })
        return
    parts = []
    if error_count:
        parts.append(f"{error_count} 条错误")
    if warning_count:
        parts.append(f"{warning_count} 条警告")
    if info_count:
        parts.append(f"{info_count} 条建议")
    checklist.append({
        "group": "quality",
        "name": "质量规则核对",
        "status": "fail" if error_count else ("warn" if warning_count else "pass"),
        "message": f"{' · '.join(parts)}，明细见下方列表",
    })


_DICT_ROW_FILL_DETAIL_LIMIT = 5


def _append_dict_row(
    checklist: List[Dict[str, str]],
    item: OssieSemanticModelImport,
    dict_fill_plan: Optional[List[Dict[str, Any]]],
) -> None:
    """⑤ 数据字典：仅填空闭环的预览确认面（拍板：不走草稿，preview 清单即确认面）。

    模型未携带字典信息时不展示该行，避免对存量文件制造恒绿噪音。
    """
    if not item.column_dicts:
        return
    if dict_fill_plan is None:
        checklist.append({
            "group": "dict",
            "name": "数据字典填充",
            "status": "skip",
            "message": (
                f"携带 {len(item.column_dicts)} 个字段的字典信息，"
                "导入时按「仅填空」更新（本次未对照现有字典评估填充项）"
            ),
        })
        return
    if not dict_fill_plan:
        checklist.append({
            "group": "dict",
            "name": "数据字典填充",
            "status": "pass",
            "message": "携带的字典信息对应的空缺项均已被现有值覆盖，无需填充",
        })
        return
    details = "、".join(
        f"{entry['table']}.{entry['column']}"
        f"（{'/'.join(_COLUMN_DICT_FIELD_LABELS.get(field, field) for field in entry['fields'])}）"
        for entry in dict_fill_plan[:_DICT_ROW_FILL_DETAIL_LIMIT]
    )
    if len(dict_fill_plan) > _DICT_ROW_FILL_DETAIL_LIMIT:
        details += f" 等 {len(dict_fill_plan)} 项"
    checklist.append({
        "group": "dict",
        "name": "数据字典填充",
        "status": "pass",
        "message": f"导入时将填充字典空缺项，仅填空不覆盖已有值：{details}",
    })


__all__ = [
    "CHAT_DATA_VENDOR_NAME",
    "OssieSemanticModelImport",
    "OssieToChatDataImporter",
    "build_model_checklist",
    "extract_metric_source_column",
    "parse_ossie_document",
    "plan_column_dict_fills",
]
