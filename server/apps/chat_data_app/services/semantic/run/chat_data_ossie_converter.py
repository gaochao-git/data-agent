"""Convert ChatData semantic configurations to Apache Ossie documents.

The generated document is a model-facing description only. SQL compilation
continues to use the authoritative ChatData configuration stored in the DB.
"""

from __future__ import annotations

import json
from typing import Any, cast

import yaml

from ..core.common import normalize_logical_type, normalize_relationship_type
from ..core.query_plan import is_composite_metric as _is_composite_metric
from ..core.query_plan import is_derived_metric as _is_derived_metric
from ..core.query_plan import parse_formula_refs
from .semantic_query_plan_builder import (
    derived_metric_verbalization,
    resolve_composite_metric,
    substitute_composite_formula,
)

_DATATYPE_MAP = {
    "string": "String",
    "varchar": "String",
    "char": "String",
    "text": "String",
    # MySQL TEXT 家族变体与 JSON：不映射会把物理列导出成 Opaque，导入侧无法还原 logical_type
    "longtext": "String",
    "mediumtext": "String",
    "tinytext": "String",
    "json": "String",
    "int": "Integer",
    "integer": "Integer",
    "bigint": "Integer",
    "smallint": "Integer",
    "tinyint": "Integer",
    "decimal": "Decimal",
    "numeric": "Decimal",
    "number": "Decimal",
    "float": "Float",
    "double": "Float",
    "boolean": "Boolean",
    "bool": "Boolean",
    "date": "Date",
    "time": "Time",
    "datetime": "DateTime",
    "timestamp": "DateTime",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _config_id(config: dict[str, Any]) -> str:
    return _text(config.get("id") or config.get("semantic_config_id"))


def _table_name(item: dict[str, Any]) -> str:
    return _text(item.get("table") or item.get("table_name"))


def _column_name(item: dict[str, Any]) -> str:
    return _text(item.get("column") or item.get("field") or item.get("field_name"))


def _expression(item: dict[str, Any], *, fallback: str = "") -> str:
    return _text(item.get("expression") or _column_name(item) or fallback)


def _datatype(value: Any) -> str | None:
    raw = _text(value).lower()
    if not raw:
        return None
    for key, result in _DATATYPE_MAP.items():
        if raw == key or raw.startswith(f"{key}("):
            return result
    return "Opaque"


def _physical_column_name(item: dict[str, Any]) -> str:
    return _text(item.get("column") or item.get("field"))


def _source_reference(item: dict[str, Any]) -> dict[str, str]:
    """物理来源引用：写入 CHAT_DATA 扩展，供导入侧无损还原。

    field_name / table_alias 仅在与默认值（物理列 / 所属表）不同时写入：
    等于默认值时是冗余信息（table_alias 缺省即等于所属表名），写入只会产生噪音。
    """
    column = _physical_column_name(item)
    table = _table_name(item)
    field_name = _text(item.get("field_name"))
    table_alias = _text(item.get("table_alias"))
    return {
        key: value
        for key, value in {
            "table": table,
            "column": column,
            "field_name": field_name if field_name and field_name != column else "",
            "table_alias": table_alias if table_alias and table_alias != table else "",
        }.items()
        if value
    }


# OSSI datatype → 存储侧类型的导入还原值（与 importer 的还原表保持一致）。
# 存储值不在还原结果内时写入扩展，由导入侧优先读取，保证导出 → 导入往返无损。
_OSSI_DATATYPE_TO_LOGICAL_TYPE = {
    "String": "text",
    "Integer": "number",
    "Decimal": "number",
    "Float": "number",
    "Date": "date",
    "Time": "datetime",
    "DateTime": "datetime",
}

_OSSI_DATATYPE_TO_DATA_TYPE = {
    "String": "string",
    "Integer": "int",
    "Decimal": "decimal",
    "Float": "float",
    "Boolean": "boolean",
    "Date": "date",
    "Time": "time",
    "DateTime": "timestamp",
}


def _column_dict_extension_data(item: dict[str, Any]) -> dict[str, Any]:
    """字段数据字典（enum_ref 之外的 enum_values/storage_format/unit）的导出形态。

    物化链路在配置副本上预挂 ``_column_dict``（见 SemanticConfigService.annotate_export_column_dicts，
    键提取口径与运行时合并一致）；此处只做防御性结构过滤后原样透传，导入侧据此
    按「仅填空」策略回写 datasource_column_dict，形成字典导出 → 导入闭环。
    enum_ref 是环境内引用（跨环境不保证存在），不随导出携带。
    """
    raw = item.get("_column_dict")
    if not isinstance(raw, dict):
        return {}
    payload: dict[str, Any] = {}
    enum_values = [
        {"value": _text(entry.get("value")), "label": _text(entry.get("label"))}
        for entry in raw.get("enum_values") or []
        if isinstance(entry, dict) and _text(entry.get("value"))
    ]
    if enum_values:
        payload["enum_values"] = enum_values
    for key in ("storage_format", "unit"):
        if _text(raw.get(key)):
            payload[key] = _text(raw.get(key))
    return payload


def _dimension_extension_data(item: dict[str, Any], datatype: str | None) -> dict[str, Any]:
    """维度扩展描述：物理来源引用 + datatype 通道无法无损还原的 logical_type。

    logical_type 的合法值里只有 enum（及 datatype 缺失等边界）不能从 OSSI datatype
    还原（String→text 等其余映射是确定的），此时写入扩展，避免导入替换后静默丢失。
    """
    data: dict[str, Any] = dict(_source_reference(item))
    logical_type = normalize_logical_type(item.get("logical_type"))
    if logical_type and _OSSI_DATATYPE_TO_LOGICAL_TYPE.get(datatype or "") != logical_type:
        data["logical_type"] = logical_type
    column_dict = _column_dict_extension_data(item)
    if column_dict:
        data["column_dict"] = column_dict
    return data


def _metric_extension_data(item: dict[str, Any], datatype: str | None) -> dict[str, Any]:
    """指标扩展描述：物理来源引用 + datatype 通道无法无损还原的 data_type；
    派生/复合指标附加结构化口径定义供 Ossie 侧识别。"""
    data: dict[str, Any] = dict(_source_reference(item))
    # data_type 是自由文本（如 decimal(10,2)），datatype 只能还原 int/timestamp 等基础形态，
    # 不一致时写入扩展，导入侧优先读取，避免精度/形态丢失
    data_type = _text(item.get("data_type"))
    if data_type and _OSSI_DATATYPE_TO_DATA_TYPE.get(datatype or "") != data_type:
        data["data_type"] = data_type
    column_dict = _column_dict_extension_data(item)
    if column_dict:
        data["column_dict"] = column_dict
    if _is_derived_metric(item):
        data.update(
            {
                "metric_kind": "derived",
                "base_metric": _text(item.get("base_metric")),
                "definition": derived_metric_verbalization(item),
                "period": item.get("period") or {},
                "filters": item.get("filters") or [],
            }
        )
    elif _is_composite_metric(item):
        data.update(
            {
                "metric_kind": "composite",
                "formula": _text(item.get("formula")),
                # formula 已由上方 _text/分支保证非空（_is_composite_metric 要求 formula 存在）
                "metric_refs": parse_formula_refs(cast(str, item.get("formula"))),
            }
        )
    return data


def _metrics_by_name(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """指标名 → 配置：派生/复合指标解析成员时复用同一份索引。"""
    return {
        cast(str, item.get("name")): item
        for item in config.get("metrics") or []
        if isinstance(item, dict) and item.get("name")
    }


def _extension(data: dict[str, Any]) -> list[dict[str, str]] | None:
    if not data:
        return None
    return [{"vendor_name": "CHAT_DATA", "data": json.dumps(data, ensure_ascii=False, sort_keys=True)}]


def _relationship_type(value: Any) -> str:
    return normalize_relationship_type(value)


def _relationship_extension_data(item: dict[str, Any], rel_type: str) -> dict[str, str]:
    """关系方向元数据与查询别名，写入 CHAT_DATA 扩展供导入侧无损还原。

    left_alias / right_alias 仅在与所属表名不同时写入：查询层别名缺省即等于表名，
    等于表名时写入是冗余噪音（与字段 table_alias 的去噪口径一致）。
    """
    left = _text(item.get("left_table"))
    right = _text(item.get("right_table"))
    left_alias = _text(item.get("left_alias"))
    right_alias = _text(item.get("right_alias"))
    return {
        key: value
        for key, value in {
            "relationship_type": rel_type,
            "join_type": _text(item.get("join_type")),
            "left_table": left,
            "right_table": right,
            "left_alias": left_alias if left_alias and left_alias != left else "",
            "right_alias": right_alias if right_alias and right_alias != right else "",
        }.items()
        if value
    }


def _key_columns(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for column in value:
        name = _text(column)
        if name and name not in result:
            result.append(name)
    return result


class _FlowStyleList(list):
    """Marker for short Ossie arrays that are clearer in inline YAML form."""


class _OssieYamlDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
        # 保持默认缩进（序列整体缩进两格）。官方示例是 indentless 风格，但 pyyaml
        # 强制 indentless 会连嵌套 mapping 一起顶格、产出非法 YAML（实测），不改。
        return super().increase_indent(flow, False)


def _represent_flow_style_list(dumper: yaml.Dumper, value: _FlowStyleList) -> Any:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=True)


_OssieYamlDumper.add_representer(_FlowStyleList, _represent_flow_style_list)


def _load_ossie_types() -> tuple[Any, ...]:
    """按 convert 解包顺序加载 Ossie 类型；未安装时抛 RuntimeError。

    2026-10 起对齐 ossie main（#551/#552）：类型更名 Ossie*（旧 OSI* 已删除），
    文档根形状由 `version + semantic_model 列表` 改为「根即单模型」。
    """
    try:
        from ossie import (
            OssieCustomExtension,
            OssieDataset,
            OssieDataType,
            OssieDialectExpression,
            OssieDimension,
            OssieDocument,
            OssieExpression,
            OssieField,
            OssieMetric,
            OssieRelationship,
        )
    except ImportError as exc:
        raise RuntimeError("Apache Ossie Python 包未安装，无法生成语义模型快照；请安装 apache-ossie") from exc
    return (
        OssieCustomExtension,
        OssieDataset,
        OssieDataType,
        OssieDimension,
        OssieDocument,
        OssieField,
        OssieExpression,
        OssieDialectExpression,
        OssieMetric,
        OssieRelationship,
    )


def _wrap_ossie_flow_style_lists(data: dict[str, Any]) -> None:
    """把短数组包成 YAML flow style（`[col]`），对齐官方示例的书写形态：数据集
    主键/唯一键 + 关系两端关联列（from_columns/to_columns）。就地修改 dump 结果。"""
    for dataset in data.get("datasets") or []:
        if "primary_key" in dataset:
            dataset["primary_key"] = _FlowStyleList(dataset["primary_key"])
        if "unique_keys" in dataset:
            dataset["unique_keys"] = [_FlowStyleList(columns) for columns in dataset["unique_keys"]]
    for relationship in data.get("relationships") or []:
        if "from_columns" in relationship:
            relationship["from_columns"] = _FlowStyleList(relationship["from_columns"])
        if "to_columns" in relationship:
            relationship["to_columns"] = _FlowStyleList(relationship["to_columns"])


class ChatDataToOssieConverter:
    """Build an Ossie document from one ChatData semantic config."""

    def convert(self, config: dict[str, Any], *, source_type: str = "") -> str:
        normalized = dict(config or {})
        config_id = _config_id(normalized)
        if not config_id:
            raise ValueError("语义配置缺少 semantic_config_id，无法生成 Ossie 快照")
        (
            OssieCustomExtension,
            OssieDataset,
            OssieDataType,
            OssieDimension,
            OssieDocument,
            OssieField,
            OssieExpression,
            OssieDialectExpression,
            OssieMetric,
            OssieRelationship,
        ) = _load_ossie_types()

        tables = self._table_names(normalized)
        datasets = [
            self._dataset(
                table,
                normalized,
                OssieDataset,
                OssieField,
                OssieExpression,
                OssieDialectExpression,
                OssieDimension,
                OssieCustomExtension,
                OssieDataType,
            )
            for table in tables
        ]
        relationships = self._relationships(
            normalized,
            OssieRelationship,
            OssieCustomExtension,
        )
        metrics = self._metrics(
            normalized, OssieMetric, OssieExpression, OssieDialectExpression, OssieCustomExtension, OssieDataType
        )
        extension = _extension(
            {
                "semantic_config_id": config_id,
                "source_type": _text(source_type),
                "update_time": _text(normalized.get("update_time")),
                "snapshot_role": "model_reference_only",
            }
        )
        # 最新规范（ossie main 2026-10）：文档根即单模型（version/name/datasets/...），
        # 官方 schema required=[version, name, datasets]，version 由 Literal 钉死默认值。
        document = OssieDocument(
            name=_text(normalized.get("name") or config_id),
            description=_text(normalized.get("description")) or None,
            ai_context=_text(normalized.get("business_notes")) or None,
            datasets=datasets,
            relationships=relationships or None,
            metrics=metrics or None,
            custom_extensions=[OssieCustomExtension(**item) for item in extension or []],
        )
        data = document.model_dump(by_alias=True, exclude_none=True, mode="json")
        _wrap_ossie_flow_style_lists(data)
        return yaml.dump(
            data,
            Dumper=_OssieYamlDumper,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    @staticmethod
    def _add_metric_or_dimension_table(item: Any, result: list[str]) -> None:
        """把单个指标/维度条目命中的表名去重加入 result。"""
        if not isinstance(item, dict):
            return
        table = _table_name(item)
        if table and table not in result:
            result.append(table)

    @staticmethod
    def _add_relationship_tables(item: Any, result: list[str]) -> None:
        """把单个关系条目涉及的 left/right 表名去重加入 result。"""
        if not isinstance(item, dict):
            return
        for key in ("left_table", "right_table"):
            table = _text(item.get(key))
            if table and table not in result:
                result.append(table)

    def _table_names(self, config: dict[str, Any]) -> list[str]:
        result: list[str] = []
        for item in [*(config.get("metrics") or []), *(config.get("dimensions") or [])]:
            self._add_metric_or_dimension_table(item, result)
        for item in config.get("relationships") or []:
            self._add_relationship_tables(item, result)
        if not result:
            result.append(_config_id(config))
        return result

    def _dataset(
        self,
        table: str,
        config: dict[str, Any],
        dataset_type: Any,
        field_type: Any,
        expression_type: Any,
        dialect_type: Any,
        dimension_type: Any,
        extension_type: Any,
        datatype_type: Any,
    ) -> Any:
        fields = self._dataset_fields(
            config, table, field_type, expression_type, dialect_type, dimension_type, extension_type, datatype_type
        )
        primary_key, unique_keys = self._dataset_keys(table, config)
        return dataset_type(
            name=table,
            source=table,
            # 官方示例与规范里 primary_key/unique_keys 均可选且未知时省略；不输出空键噪音。
            primary_key=primary_key or None,
            unique_keys=unique_keys or None,
            fields=fields,
            custom_extensions=[
                extension_type(**x) for x in (_extension({"semantic_config_id": _config_id(config)}) or [])
            ],
        )

    def _append_dataset_field(
        self,
        fields: list[Any],
        item: dict[str, Any],
        config: dict[str, Any],
        table: str,
        field_type: Any,
        expression_type: Any,
        dialect_type: Any,
        dimension_type: Any,
        extension_type: Any,
        datatype_type: Any,
    ) -> None:
        """把单个属于当前表的维度转换为 OSI 字段并追加到 fields。"""
        if not isinstance(item, dict) or _table_name(item) != table:
            return
        name = _text(item.get("name") or _column_name(item))
        expression = _expression(item, fallback=name)
        if not name or not expression:
            return
        datatype = self._dimension_datatype(config, item)
        fields.append(
            field_type(
                name=name,
                expression=expression_type(dialects=[dialect_type(dialect="ANSI_SQL", expression=expression)]),
                dimension=dimension_type(
                    is_time=_text(item.get("logical_type")).lower() in {"date", "datetime", "timestamp", "time"}
                )
                if item.get("logical_type")
                else None,
                label=_text(item.get("label")) or None,
                description=_text(item.get("description") or item.get("metadata_supplement")) or None,
                datatype=datatype_type(datatype) if datatype else None,
                custom_extensions=[
                    extension_type(**x) for x in (_extension(_dimension_extension_data(item, datatype)) or [])
                ],
            )
        )

    def _dataset_fields(
        self,
        config: dict[str, Any],
        table: str,
        field_type: Any,
        expression_type: Any,
        dialect_type: Any,
        dimension_type: Any,
        extension_type: Any,
        datatype_type: Any,
    ) -> list[Any]:
        """把属于当前表的维度转换为 OSI 字段。"""
        fields: list[Any] = []
        for item in config.get("dimensions") or []:
            self._append_dataset_field(
                fields,
                item,
                config,
                table,
                field_type,
                expression_type,
                dialect_type,
                dimension_type,
                extension_type,
                datatype_type,
            )
        return fields

    @staticmethod
    def _find_table_config(config: dict[str, Any], table: str) -> dict[str, Any]:
        return next(
            (item for item in config.get("tables") or [] if isinstance(item, dict) and _table_name(item) == table), {}
        )

    @staticmethod
    def _physical_primary_keys(table_config: dict[str, Any]) -> list[list[str]]:
        return [columns for value in table_config.get("primary_keys") or [] if (columns := _key_columns(value))]

    @staticmethod
    def _manual_indexes(table_config: dict[str, Any]) -> list[dict[str, Any]]:
        return [item for item in table_config.get("manual_indexes") or [] if isinstance(item, dict)]

    @staticmethod
    def _manual_primary_keys(manual_indexes: list[dict[str, Any]]) -> list[list[str]]:
        return [
            columns
            for item in manual_indexes
            if _text(item.get("index_type")).lower() == "primary" and (columns := _key_columns(item.get("columns")))
        ]

    @staticmethod
    def _collect_unique_keys(table_config: dict[str, Any], manual_indexes: list[dict[str, Any]]) -> list[list[str]]:
        unique_keys: list[list[str]] = []
        for item in table_config.get("unique_indexes") or []:
            columns = _key_columns(item.get("columns")) if isinstance(item, dict) else []
            if columns and columns not in unique_keys:
                unique_keys.append(columns)
        for item in manual_indexes:
            if _text(item.get("index_type")).lower() != "unique":
                continue
            columns = _key_columns(item.get("columns"))
            if columns and columns not in unique_keys:
                unique_keys.append(columns)
        return unique_keys

    def _dataset_keys(self, table: str, config: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
        table_config = self._find_table_config(config, table)
        manual_indexes = self._manual_indexes(table_config)
        physical_primary_keys = self._physical_primary_keys(table_config)
        manual_primary_keys = self._manual_primary_keys(manual_indexes)
        primary_key = (physical_primary_keys or manual_primary_keys or [[]])[0]
        return primary_key, self._collect_unique_keys(table_config, manual_indexes)

    @staticmethod
    def _resolve_column_physical_type(columns: Any, column_name: str) -> str:
        """在单个表的列列表里按列名（casefold）定位物理类型；找不到返回空串。"""
        for column in columns or []:
            if not isinstance(column, dict):
                continue
            if _text(column.get("name")).casefold() != column_name:
                continue
            physical_type = next(
                (
                    _text(column.get(key))
                    for key in ("mysql_type", "hive_type", "type", "data_type")
                    if _text(column.get(key))
                ),
                "",
            )
            if physical_type:
                return physical_type
        return ""

    @staticmethod
    def _resolve_dimension_physical_type(
        config: dict[str, Any],
        table_name: str,
        column_name: str,
    ) -> str:
        """在 config 的 tables 里按表名/别名与列名定位物理类型；找不到返回空串。"""
        for table in config.get("tables") or []:
            if not isinstance(table, dict):
                continue
            table_names = {
                _table_name(table).casefold(),
                _text(table.get("table_alias")).casefold(),
            }
            if table_name not in table_names:
                continue
            physical_type = ChatDataToOssieConverter._resolve_column_physical_type(table.get("columns"), column_name)
            if physical_type:
                return _datatype(physical_type) or ""
        return ""

    @staticmethod
    def _dimension_datatype(config: dict[str, Any], dimension: dict[str, Any]) -> str | None:
        table_name = _table_name(dimension).casefold()
        column_name = _physical_column_name(dimension).casefold()
        if table_name and column_name:
            physical_type = ChatDataToOssieConverter._resolve_dimension_physical_type(config, table_name, column_name)
            if physical_type:
                return _datatype(physical_type)
        return _datatype(dimension.get("logical_type"))

    def _metrics(
        self,
        config: dict[str, Any],
        metric_type: Any,
        expression_type: Any,
        dialect_type: Any,
        extension_type: Any,
        datatype_type: Any,
    ) -> list[Any]:
        metrics_map = _metrics_by_name(config)
        result = []
        for item in config.get("metrics") or []:
            if not isinstance(item, dict) or not _text(item.get("name")):
                continue
            metric = self._build_metric(
                item,
                metrics_map,
                metric_type,
                expression_type,
                dialect_type,
                extension_type,
                datatype_type,
            )
            if metric is not None:
                result.append(metric)
        return result

    def _build_metric(
        self,
        item: dict[str, Any],
        metrics_map: dict[str, dict[str, Any]],
        metric_type: Any,
        expression_type: Any,
        dialect_type: Any,
        extension_type: Any,
        datatype_type: Any,
    ) -> Any:
        """单个指标实体；表达式缺失（配置不完整）时返回 None 跳过导出。"""
        expression = self._metric_expression(item, metrics_map)
        if not expression:
            return None
        datatype = _datatype(item.get("data_type") or item.get("logical_type"))
        return metric_type(
            name=_text(item.get("name")),
            expression=expression_type(dialects=[dialect_type(dialect="ANSI_SQL", expression=expression)]),
            description=_text(item.get("description") or item.get("metadata_supplement")) or None,
            datatype=datatype_type(datatype) if datatype else None,
            custom_extensions=[extension_type(**x) for x in (_extension(_metric_extension_data(item, datatype)) or [])],
        )

    @staticmethod
    def _metric_expression(item: dict[str, Any], metrics_map: dict[str, dict[str, Any]]) -> str:
        """指标表达式：派生指标复用基础原子指标表达式；复合指标代入成员表达式；配置缺失时跳过导出。"""
        if _is_derived_metric(item):
            base_cfg = metrics_map.get(_text(item.get("base_metric"))) or {}
            return _expression(base_cfg)
        if _is_composite_metric(item):
            try:
                composite_plan = resolve_composite_metric(item, metrics_map)
            except ValueError:
                return ""
            if not composite_plan:
                return ""
            return substitute_composite_formula(composite_plan["formula"], composite_plan["members"])
        return _expression(item, fallback=_text(item.get("name")))

    def _relationships(self, config: dict[str, Any], relationship_type: Any, extension_type: Any) -> list[Any]:
        result = []
        for index, item in enumerate(config.get("relationships") or [], start=1):
            if not isinstance(item, dict):
                continue
            left = _text(item.get("left_table"))
            right = _text(item.get("right_table"))
            conditions = [condition for condition in item.get("join_conditions") or [] if isinstance(condition, dict)]
            if not left or not right or not conditions:
                continue
            from_table, to_table, from_columns, to_columns, rel_type = self._relationship_direction(
                item, conditions, left, right
            )
            if not all(from_columns) or not all(to_columns):
                continue
            result.append(
                relationship_type(
                    name=f"{from_table}__{to_table}__{index}",
                    **{"from": from_table},
                    to=to_table,
                    from_columns=from_columns,
                    to_columns=to_columns,
                    custom_extensions=[
                        extension_type(**x) for x in (_extension(_relationship_extension_data(item, rel_type)) or [])
                    ],
                )
            )
        return result

    @staticmethod
    def _relationship_direction(
        item: dict[str, Any],
        conditions: list[dict[str, Any]],
        left: str,
        right: str,
    ) -> tuple[str, str, list[str], list[str], str]:
        """按关系类型决定 from/to 方向：one_to_many 反转，其余保持 left→right；一并返回 rel_type 供扩展字段复用。"""
        rel_type = _relationship_type(item.get("relationship_type"))
        if rel_type == "one_to_many":
            return (
                right,
                left,
                [_text(c.get("right_column")) for c in conditions],
                [_text(c.get("left_column")) for c in conditions],
                rel_type,
            )
        return (
            left,
            right,
            [_text(c.get("left_column")) for c in conditions],
            [_text(c.get("right_column")) for c in conditions],
            rel_type,
        )


__all__ = ["ChatDataToOssieConverter"]
