"""Convert Apache Ossie documents back to ChatData semantic config drafts.

导入方向：Ossie YAML → ChatData 语义配置草稿（与 chat_data_ossie_converter 的导出互为逆操作）。

核心约束：
- 产物永远是"待人工确认"的草稿：enabled 由路由层强制为 False；
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

    def bump(self, key: str, delta: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + delta

    @property
    def importable(self) -> bool:
        return not self.errors

    def to_config_payload(self) -> Dict[str, Any]:
        """转成 SemanticConfigCreateRequest 兼容的创建参数（enabled 由路由层强制 False）。"""
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
        document = parse_ossie_document(yaml_content)
        models = document.semantic_model or []
        if not models:
            raise ValueError("Ossie 文档不包含 semantic_model，没有可导入的内容")
        return [self._convert_model(model) for model in models]

    def import_document(self, document: OSIDocument) -> List[OssieSemanticModelImport]:
        models = document.semantic_model or []
        if not models:
            raise ValueError("Ossie 文档不包含 semantic_model，没有可导入的内容")
        return [self._convert_model(model) for model in models]

    def _convert_model(self, model: OSISemanticModel) -> OssieSemanticModelImport:
        result = OssieSemanticModelImport(
            name=str(model.name or "").strip(),
            description=str(model.description or "").strip() or None,
            business_notes=str(model.ai_context or "").strip() or None,
        )
        if not result.name:
            result.errors.append("semantic_model 缺少 name，无法导入")
            return result
        label = f"语义模型 {result.name}"

        model_extension = _extension_data(model.custom_extensions, result.warnings, label)
        result.source_config_id = str(model_extension.get("semantic_config_id") or "").strip() or None
        # snapshot_role=model_reference_only 不再产生 warning：来源配置提示
        # （source_config_id + "将创建新草稿，不更新原配置"）已覆盖同一信息

        dataset_names = [str(dataset.name or "").strip() for dataset in model.datasets or []]
        if len(set(dataset_names)) != len(dataset_names):
            result.errors.append(f"{label} 存在重名 dataset: {dataset_names}")
            return result

        for dataset in model.datasets or []:
            for ossie_field in dataset.fields or []:
                self._convert_field(ossie_field, dataset_names, result)
        for metric in model.metrics or []:
            self._convert_metric(metric, dataset_names, result)
        for index, relationship in enumerate(model.relationships or [], start=1):
            self._convert_relationship(relationship, index, result)
        return result

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

        column = str(extension.get("column") or "").strip()
        if not column:
            candidate = _strip_backticks(expression)
            if _PLAIN_IDENTIFIER_PATTERN.match(candidate):
                column = candidate
                result.warnings.append(f"{label} 缺少 CHAT_DATA 扩展的 column，按表达式推断物理字段: {column}")
            else:
                result.errors.append(f"{label} 缺少 CHAT_DATA 扩展的 column，且表达式不是单一物理字段（{expression}），无法确定")
                return

        entry: Dict[str, Any] = {"name": name, "table": table, "column": column, "expression": expression}
        # 逻辑字段名 / 表别名：导出侧写入扩展，原样还原，保证往返无损
        field_name = str(extension.get("field_name") or "").strip()
        if field_name:
            entry["field_name"] = field_name
        table_alias = str(extension.get("table_alias") or "").strip()
        if table_alias:
            entry["table_alias"] = table_alias
        # logical_type：扩展携带的存储值优先（enum 等无法由 datatype 还原），其余按 datatype 映射
        logical_type = str(extension.get("logical_type") or "").strip()
        if logical_type not in LOGICAL_TYPE_VALUES:
            logical_type = _OSIE_DATATYPE_TO_LOGICAL_TYPE.get(ossie_field.datatype)
        dimension = ossie_field.dimension
        if dimension is not None and dimension.is_time and logical_type not in ("date", "datetime"):
            logical_type = "date"
            result.warnings.append(f"{label} 标记为时间维度但类型不是日期/时间，默认按 date 处理，请人工确认")
        if logical_type:
            entry["logical_type"] = logical_type
        description = str(ossie_field.description or "").strip()
        if description:
            entry["description"] = description
        result.dimensions.append(entry)

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
        # 逻辑字段名 / 表别名：与维度同理，导出侧写入扩展，原样还原
        field_name = str(extension.get("field_name") or "").strip()
        if field_name:
            entry["field_name"] = field_name
        table_alias = str(extension.get("table_alias") or "").strip()
        if table_alias:
            entry["table_alias"] = table_alias
        # data_type：扩展携带的存储值优先（decimal(10,2) 等精度形态无法由 datatype 还原）
        data_type = str(extension.get("data_type") or "").strip()
        if not data_type:
            data_type = _OSIE_DATATYPE_TO_DATA_TYPE.get(ossie_metric.datatype)
        if data_type:
            entry["data_type"] = data_type
        description = str(ossie_metric.description or "").strip()
        if description:
            entry["description"] = description
        result.metrics.append(entry)
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
        from_table = str(getattr(ossie_relationship, "from_dataset", "") or "").strip()
        to_table = str(ossie_relationship.to or "").strip()
        from_columns = [str(column or "").strip() for column in ossie_relationship.from_columns or []]
        to_columns = [str(column or "").strip() for column in ossie_relationship.to_columns or []]
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
        join_type = str(extension.get("join_type") or "").strip().lower()
        if join_type not in ("inner", "left", "right"):
            if join_type:
                result.warnings.append(f"{label} 的 join_type 不支持（{join_type}），按 left 处理")
            join_type = "left"

        ext_left_table = str(extension.get("left_table") or "").strip()
        ext_right_table = str(extension.get("right_table") or "").strip()
        if ext_left_table and ext_right_table:
            if {ext_left_table, ext_right_table} != {from_table, to_table}:
                result.warnings.append(
                    f"{label} 的扩展表 ({ext_left_table}/{ext_right_table}) 与 from/to ({from_table}/{to_table}) 不一致，以扩展为准"
                )
            left_table, right_table = ext_left_table, ext_right_table
        else:
            # 无扩展（外部文件）：按 from→to 方向导入，from 侧为多端
            left_table, right_table = from_table, to_table
            result.warnings.append(f"{label} 缺少 CHAT_DATA 扩展的左右表方向，按 from→to 导入（基数默认 {relationship_type}），请人工确认")

        # 导出方向还原：one_to_many 导出时反转过方向（from=右表/to=左表）
        if relationship_type == "one_to_many" and ext_left_table and ext_right_table:
            left_columns, right_columns = to_columns, from_columns
        else:
            left_columns, right_columns = from_columns, to_columns

        result.relationships.append({
            "left_table": left_table,
            "right_table": right_table,
            "join_conditions": [
                {"left_column": left_column, "right_column": right_column}
                for left_column, right_column in zip(left_columns, right_columns)
            ],
            "relationship_type": relationship_type,
            "join_type": join_type,
        })


def build_model_checklist(
    item: OssieSemanticModelImport,
    validation_error: Optional[str] = None,
    quality_findings: Optional[List[Dict[str, Any]]] = None,
    inventory_available: bool = True,
) -> List[Dict[str, str]]:
    """把单个模型的既有判定结果映射为校验清单（四组，逐行状态）。

    纯展示层翻译：检查逻辑零改动，行状态由 stats 计数 / validation_error /
    quality findings 确定性推导，保证工具、导入预览与实际闸门同一口径。

    - validation_error：validate_semantic_config_for_save 的返回（None=通过）；
    - quality_findings：SemanticQualityChecker 报告的 findings（可选，None=未执行）；
    - inventory_available：数据源物理表结构是否可用（不可用时物理/清单类检查降级为 skip）。

    行结构：{group, name, status, message}，status ∈ pass / warn / fail / skip。
    不输出质量分与等级——分数无法指导动作，触发明细才有操作价值。
    """
    checklist: List[Dict[str, str]] = []
    metric_count = len(item.metrics)
    dimension_count = len(item.dimensions)
    relationship_count = len(item.relationships)

    # ① OSSI schema 校验：走到这里即解析成功（失败在更早的 parse 阶段整体报错）
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

    # ② 结构映射：表归属 + 派生/复合还原，状态由 stats 计数推导
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

    # 同名冲突提示：不属于失败，导入时可选择替换或跳过（见路由层 conflict_actions）
    if item.existing_config:
        state = "启用中" if item.existing_config.get("enabled") else "禁用中"
        checklist.append({
            "group": "mapping",
            "name": "模型名冲突",
            "status": "warn",
            "message": (
                f"已存在同名配置（config_id={item.existing_config.get('config_id')}，{state}），"
                "导入时可选择替换原配置或跳过该模型"
            ),
        })
    elif item.conflict_locked:
        # 同名配置不可操作（如他人私有配置）：不展示其元信息，导入时将强制跳过
        checklist.append({
            "group": "mapping",
            "name": "模型名冲突",
            "status": "warn",
            "message": "已存在同名配置但无操作权限，导入时将跳过该模型",
        })

    # ③ 物理校验：validate_semantic_config_for_save 的整体结论（表/列存在性 + 关系连通性）
    # 只有 stats 记录了 physical_checked 才真正执行过（结构映射失败的模型不会送检）
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
        checklist.append({
            "group": "physical",
            "name": "物理字段校验",
            "status": "pass",
            "message": f"指标 {metric_count} · 维度 {dimension_count} · 关系 {relationship_count} 全部命中数据源物理元数据",
        })

    # ④ 质量规则核对：只报触发项与计数，不输出评分
    if quality_findings is None:
        checklist.append({
            "group": "quality",
            "name": "质量规则核对",
            "status": "skip",
            "message": "仅对可导入模型执行质量检查，本模型未执行",
        })
    else:
        error_count = sum(1 for finding in quality_findings if finding.get("severity") == "error")
        warning_count = sum(1 for finding in quality_findings if finding.get("severity") == "warning")
        info_count = sum(1 for finding in quality_findings if finding.get("severity") == "info")
        if error_count or warning_count or info_count:
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
        else:
            checklist.append({
                "group": "quality",
                "name": "质量规则核对",
                "status": "pass",
                "message": f"{len(_QUALITY_RULES)} 条质量规则全部未触发",
            })
    return checklist


__all__ = [
    "CHAT_DATA_VENDOR_NAME",
    "OssieSemanticModelImport",
    "OssieToChatDataImporter",
    "build_model_checklist",
    "parse_ossie_document",
]
