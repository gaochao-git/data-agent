"""Convert ChatData semantic configurations to Apache Ossie documents.

The generated document is a model-facing description only. SQL compilation
continues to use the authoritative ChatData configuration stored in the DB.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from .semantic_core.common import normalize_relationship_type


_AGGREGATE_RE = re.compile(r"^(?:COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE)
_DATATYPE_MAP = {
    "string": "String",
    "varchar": "String",
    "char": "String",
    "text": "String",
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


def _extension(data: dict[str, Any]) -> list[dict[str, str]] | None:
    if not data:
        return None
    return [{"vendor_name": "CHAT_DATA", "data": json.dumps(data, ensure_ascii=False, sort_keys=True)}]


def _dialect_expression(expression: str) -> dict[str, Any]:
    # Ossie 0.2 currently has no MYSQL/HIVE enum. ANSI_SQL is the portable
    # representation; the original source type is retained in extensions.
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}


def _relationship_type(value: Any) -> str:
    return normalize_relationship_type(value)


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
        return super().increase_indent(flow, False)


def _represent_flow_style_list(dumper: yaml.Dumper, value: _FlowStyleList) -> Any:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=True)


_OssieYamlDumper.add_representer(_FlowStyleList, _represent_flow_style_list)


class ChatDataToOssieConverter:
    """Build an Ossie document from one ChatData semantic config."""

    def convert(self, config: dict[str, Any], *, source_type: str = "") -> str:
        normalized = dict(config or {})
        config_id = _config_id(normalized)
        if not config_id:
            raise ValueError("语义配置缺少 semantic_config_id，无法生成 Ossie 快照")
        try:
            from ossie import (
                OSICustomExtension,
                OSIDataset,
                OSIDataType,
                OSIDimension,
                OSIDocument,
                OSIField,
                OSIExpression,
                OSIDialectExpression,
                OSIMetric,
                OSIRelationship,
                OSISemanticModel,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Apache Ossie Python 包未安装，无法生成语义模型快照；请安装 apache-ossie"
            ) from exc

        tables = self._table_names(normalized)
        datasets = [
            self._dataset(
                table,
                normalized,
                OSIDataset,
                OSIField,
                OSIExpression,
                OSIDialectExpression,
                OSIDimension,
                OSICustomExtension,
                OSIDataType,
            )
            for table in tables
        ]
        relationships = self._relationships(
            normalized,
            OSIRelationship,
            OSICustomExtension,
        )
        metrics = self._metrics(normalized, OSIMetric, OSIExpression, OSIDialectExpression, OSICustomExtension, OSIDataType)
        extension = _extension({
            "semantic_config_id": config_id,
            "source_type": _text(source_type),
            "update_time": _text(normalized.get("update_time")),
            "snapshot_role": "model_reference_only",
        })
        model = OSISemanticModel(
            name=_text(normalized.get("name") or config_id),
            description=_text(normalized.get("description")) or None,
            ai_context=_text(normalized.get("ontology_rules")) or None,
            datasets=datasets,
            relationships=relationships or None,
            metrics=metrics or None,
            custom_extensions=[OSICustomExtension(**item) for item in extension or []],
        )
        # The current core JSON schema is strict at the document root and
        # allows only version and semantic_model. Keep optional model metadata
        # out of the root so the serialized snapshot passes that schema too.
        document = OSIDocument(semantic_model=[model])
        data = document.model_dump(by_alias=True, exclude_none=True, mode="json")
        for semantic_model in data.get("semantic_model") or []:
            for dataset in semantic_model.get("datasets") or []:
                if "primary_key" in dataset:
                    dataset["primary_key"] = _FlowStyleList(dataset["primary_key"])
                if "unique_keys" in dataset:
                    dataset["unique_keys"] = [
                        _FlowStyleList(columns)
                        for columns in dataset["unique_keys"]
                    ]
        return yaml.dump(
            data,
            Dumper=_OssieYamlDumper,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    def _table_names(self, config: dict[str, Any]) -> list[str]:
        result: list[str] = []
        for item in [*(config.get("metrics") or []), *(config.get("dimensions") or [])]:
            if isinstance(item, dict):
                table = _table_name(item)
                if table and table not in result:
                    result.append(table)
        for item in config.get("relationships") or []:
            if not isinstance(item, dict):
                continue
            for key in ("left_table", "right_table"):
                table = _text(item.get(key))
                if table and table not in result:
                    result.append(table)
        if not result:
            result.append(_config_id(config))
        return result

    def _dataset(self, table: str, config: dict[str, Any], dataset_type: Any, field_type: Any, expression_type: Any, dialect_type: Any, dimension_type: Any, extension_type: Any, datatype_type: Any) -> Any:
        fields = []
        for item in config.get("dimensions") or []:
            if not isinstance(item, dict) or _table_name(item) != table:
                continue
            name = _text(item.get("name") or _column_name(item))
            expression = _expression(item, fallback=name)
            if not name or not expression:
                continue
            fields.append(field_type(
                name=name,
                expression=expression_type(dialects=[dialect_type(dialect="ANSI_SQL", expression=expression)]),
                dimension=dimension_type(is_time=_text(item.get("logical_type")).lower() in {"date", "datetime", "timestamp", "time"}) if item.get("logical_type") else None,
                label=_text(item.get("label")) or None,
                description=_text(item.get("description") or item.get("metadata_supplement")) or None,
                datatype=datatype_type(_datatype(item.get("logical_type"))) if _datatype(item.get("logical_type")) else None,
                custom_extensions=[extension_type(**x) for x in (_extension(self._private_item_fields(item)) or [])],
            ))
        primary_key, unique_keys = self._dataset_keys(table, config)
        return dataset_type(
            name=table,
            source=table,
            primary_key=primary_key,
            unique_keys=unique_keys or [[]],
            fields=fields,
            custom_extensions=[extension_type(**x) for x in (_extension({"semantic_config_id": _config_id(config)}) or [])],
        )

    def _dataset_keys(self, table: str, config: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
        table_config = next((
            item for item in config.get("tables") or []
            if isinstance(item, dict) and _table_name(item) == table
        ), {})
        physical_primary_keys = [
            columns for value in table_config.get("primary_keys") or []
            if (columns := _key_columns(value))
        ]
        manual_indexes = [
            item for item in table_config.get("manual_indexes") or []
            if isinstance(item, dict)
        ]
        manual_primary_keys = [
            columns for item in manual_indexes
            if _text(item.get("index_type")).lower() == "primary"
            and (columns := _key_columns(item.get("columns")))
        ]
        primary_key = (physical_primary_keys or manual_primary_keys or [[]])[0]

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
        return primary_key, unique_keys

    def _metrics(self, config: dict[str, Any], metric_type: Any, expression_type: Any, dialect_type: Any, extension_type: Any, datatype_type: Any) -> list[Any]:
        result = []
        for item in config.get("metrics") or []:
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name"))
            expression = _expression(item, fallback=name)
            if not name or not expression:
                continue
            result.append(metric_type(
                name=name,
                expression=expression_type(dialects=[dialect_type(dialect="ANSI_SQL", expression=expression)]),
                description=_text(item.get("description") or item.get("metadata_supplement")) or None,
                datatype=datatype_type(_datatype(item.get("logical_type"))) if _datatype(item.get("logical_type")) else None,
                custom_extensions=[extension_type(**x) for x in (_extension({**self._private_item_fields(item), "table": _table_name(item), "role": "aggregate" if _AGGREGATE_RE.match(expression) else "raw"}) or [])],
            ))
        return result

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
            rel_type = _relationship_type(item.get("relationship_type"))
            if rel_type == "one_to_many":
                from_table, to_table = right, left
                from_columns = [_text(c.get("right_column")) for c in conditions]
                to_columns = [_text(c.get("left_column")) for c in conditions]
            else:
                from_table, to_table = left, right
                from_columns = [_text(c.get("left_column")) for c in conditions]
                to_columns = [_text(c.get("right_column")) for c in conditions]
            if not all(from_columns) or not all(to_columns):
                continue
            result.append(relationship_type(
                name=f"{from_table}__{to_table}__{index}",
                **{"from": from_table},
                to=to_table,
                from_columns=from_columns,
                to_columns=to_columns,
                custom_extensions=[extension_type(**x) for x in (_extension({
                    "relationship_type": rel_type,
                    "join_type": _text(item.get("join_type")),
                    "left_table": left,
                    "right_table": right,
                }) or [])],
            ))
        return result

    @staticmethod
    def _private_item_fields(item: dict[str, Any]) -> dict[str, Any]:
        keys = ("table", "column", "field", "field_name", "logical_type", "mapping", "allowed_values", "allowed_operators")
        return {key: item[key] for key in keys if key in item and item[key] not in (None, "", [], {})}


__all__ = ["ChatDataToOssieConverter"]
