"""语义模型质量检查器（L1 确定性检查）。

定位：回答"这份语义模型的质量到底怎么样"，与 chat_data_ossie_importer 的
"能不能导入"（四道闸门）互补——闸门管合法性，本模块管好坏。

设计约束：
- 只做确定性检查（覆盖度/可执行性/一致性/来源/文档五类），规则与
  agent/skills/data-analyzing/references/governance/quality-rules.md 保持同一套
  编码与口径；会话内技能按该清单分析，导入 preview 按本模块机器把关；
- 不依赖数据库：config 是 SemanticConfigService 返回的配置字典，
  物理列清单 physical_columns 为可选的 {(table, column)} 集合
  （来自数据源管理器的 get_cached_schema_async 全量表结构；注意不是
  get_column_dicts_async——那是稀疏字段字典，只含带注释的列），
  缺省时跳过依赖物理清单的检查项；
- 严重度扣分：error -8 / warning -3 / info -1，得分下限 0；
- 同码问题聚合为一条 finding（最多列 5 个目标 + 计数），避免报告噪音。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, List, Optional, Set, Tuple

SCORE_DEDUCTION = {"error": 8, "warning": 3, "info": 1}
MAX_TARGET_EXAMPLES = 5

# 规则目录：code -> (severity, category, label)。与技能 references/governance/quality-rules.md 同源。
RULES: Dict[str, Tuple[str, str, str]] = {
    # 来源完整性 provenance
    "PROV_METRIC_MISSING_TABLE": ("error", "provenance", "原子指标缺少归属表 table"),
    "PROV_DIMENSION_MISSING_TABLE": ("error", "provenance", "维度缺少归属表 table"),
    "PROV_DIMENSION_MISSING_COLUMN": ("warning", "provenance", "维度缺少物理字段 column"),
    # 可执行性 executability
    "EXE_UNKNOWN_COLUMN": ("error", "executability", "引用的物理列在数据源中不存在"),
    "EXE_DERIVED_BASE_MISSING": ("error", "executability", "派生指标的 base_metric 在当前配置中不存在"),
    "EXE_DERIVED_NO_CONSTRAINT": ("info", "executability", "派生指标未设置时间窗 period 或过滤 filters，口径可能与基础指标重复"),
    "EXE_COMPOSITE_FORMULA_MISSING": ("error", "executability", "复合指标缺少 formula"),
    "EXE_COMPOSITE_UNRESOLVED": ("warning", "executability", "复合指标 formula 存在无法对应到已知指标的片段"),
    # 覆盖度 coverage
    "COV_NO_METRICS": ("warning", "coverage", "配置只有维度没有指标，无法回答聚合类问题"),
    "COV_NO_DIMENSIONS": ("warning", "coverage", "配置只有指标没有维度，无法分组或过滤"),
    "COV_DUPLICATE_DIMENSION_COLUMN": ("warning", "coverage", "同一物理列被建模为多个维度"),
    "COV_UNMODELED_COLUMNS": ("info", "coverage", "已建模表存在未纳入任何维度/指标的物理列"),
    # 一致性 consistency
    "CON_DUPLICATE_METRIC_EXPRESSION": ("info", "consistency", "多个原子指标使用相同表达式"),
    "CON_RELATIONSHIP_DUPLICATE": ("warning", "consistency", "表关系重复定义"),
    "CON_RELATIONSHIP_SELF": ("info", "consistency", "表关系左右为同一张表"),
    "CON_RELATIONSHIP_TABLE_UNREFERENCED": ("warning", "consistency", "关系引用的表没有任何维度或指标建模"),
    "CON_RELATIONSHIP_CYCLE": ("warning", "consistency", "表关系存在循环依赖"),
    # 文档完备性 documentation
    "DOC_METRIC_NO_DESCRIPTION": ("info", "documentation", "指标缺少描述"),
    "DOC_DIMENSION_NO_DESCRIPTION": ("info", "documentation", "维度缺少描述"),
    "DOC_NO_BUSINESS_NOTES": ("info", "documentation", "配置缺少业务说明 business_notes"),
}

_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*")
_SIMPLE_AGG_COLUMN = re.compile(
    r"^(?:COUNT|SUM|AVG|MAX|MIN)\s*\(\s*(?:DISTINCT\s+)?`?([^`(),\s]+)`?\s*\)$",
    re.IGNORECASE,
)
_PLAIN_COLUMN = re.compile(r"^`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`?$")


def _clean(value: Any) -> str:
    return str(value or "").replace("`", "").strip()


@dataclass
class QualityFinding:
    """聚合后的一条质量问题。"""

    code: str
    severity: str
    category: str
    message: str
    targets: List[str] = dataclass_field(default_factory=list)


@dataclass
class ConfigQualityReport:
    """单个语义配置的质量报告。"""

    config_id: str
    config_name: str
    enabled: bool
    score: int
    grade: str
    stats: Dict[str, Any]
    findings: List[QualityFinding]

    def to_payload(self) -> Dict[str, Any]:
        return {
            "config_id": self.config_id,
            "config_name": self.config_name,
            "enabled": self.enabled,
            "score": self.score,
            "grade": self.grade,
            "stats": self.stats,
            "findings": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "category": item.category,
                    "message": item.message,
                    "targets": list(item.targets),
                }
                for item in self.findings
            ],
        }


def grade_for_score(score: int) -> str:
    """得分 -> 等级：>=90 优 / >=75 良 / >=60 中 / 其余 差。"""
    if score >= 90:
        return "优"
    if score >= 75:
        return "良"
    if score >= 60:
        return "中"
    return "差"


_grade = grade_for_score


def _metric_source_column(metric: Dict[str, Any]) -> str:
    """解析原子指标可对应的物理列；复杂表达式返回空串（无法可靠解析则不判）。"""
    expression = str(metric.get("expression") or "").strip()
    match = _SIMPLE_AGG_COLUMN.match(expression)
    if match:
        return match.group(1).split(".")[-1].strip()
    match = _PLAIN_COLUMN.match(expression)
    if match:
        return match.group(1).split(".")[-1].strip()
    return ""


class SemanticQualityChecker:
    """对单个语义配置执行确定性质量检查。"""

    def check_config(
        self,
        config: Dict[str, Any],
        *,
        physical_columns: Optional[Set[Tuple[str, str]]] = None,
    ) -> ConfigQualityReport:
        raw: List[Tuple[str, str]] = []  # (code, target)
        metrics = [item for item in config.get("metrics") or [] if isinstance(item, dict)]
        dimensions = [item for item in config.get("dimensions") or [] if isinstance(item, dict)]
        relationships = [item for item in config.get("relationships") or [] if isinstance(item, dict)]

        self._check_provenance(metrics, dimensions, raw)
        self._check_executability(config, metrics, raw, physical_columns)
        self._check_coverage(metrics, dimensions, raw, physical_columns)
        self._check_consistency(metrics, dimensions, relationships, raw, physical_columns)
        self._check_documentation(config, metrics, dimensions, raw)

        findings = self._aggregate(raw)
        score = max(0, 100 - sum(SCORE_DEDUCTION.get(item.severity, 0) for item in findings))
        return ConfigQualityReport(
            config_id=str(config.get("id") or ""),
            config_name=str(config.get("name") or "").strip(),
            enabled=bool(config.get("enabled")),
            score=score,
            grade=_grade(score),
            stats=self._stats(metrics, dimensions, relationships),
            findings=findings,
        )

    # ---- 检查项 ----

    def _check_provenance(self, metrics: List[Dict[str, Any]], dimensions: List[Dict[str, Any]], raw: List[Tuple[str, str]]) -> None:
        for metric in metrics:
            if metric.get("metric_kind"):
                continue  # 派生/复合指标不落物理表，table 为空是正常形态
            if not _clean(metric.get("table")):
                raw.append(("PROV_METRIC_MISSING_TABLE", _clean(metric.get("name")) or "<未命名>"))
        for dimension in dimensions:
            name = _clean(dimension.get("name")) or "<未命名>"
            if not _clean(dimension.get("table")):
                raw.append(("PROV_DIMENSION_MISSING_TABLE", name))
            elif not _clean(dimension.get("column")):
                raw.append(("PROV_DIMENSION_MISSING_COLUMN", name))

    def _check_executability(
        self,
        config: Dict[str, Any],
        metrics: List[Dict[str, Any]],
        raw: List[Tuple[str, str]],
        physical_columns: Optional[Set[Tuple[str, str]]],
    ) -> None:
        metric_names = {_clean(metric.get("name")) for metric in metrics if _clean(metric.get("name"))}
        for metric in metrics:
            name = _clean(metric.get("name")) or "<未命名>"
            kind = str(metric.get("metric_kind") or "").strip()
            if kind == "derived":
                base = _clean(metric.get("base_metric"))
                if not base or base not in metric_names:
                    raw.append(("EXE_DERIVED_BASE_MISSING", name))
                elif not (metric.get("period") or metric.get("filters")):
                    raw.append(("EXE_DERIVED_NO_CONSTRAINT", name))
            elif kind == "composite":
                formula = str(metric.get("formula") or "").strip()
                if not formula:
                    raw.append(("EXE_COMPOSITE_FORMULA_MISSING", name))
                    continue
                unresolved = self._unresolved_formula_tokens(formula, metric_names)
                if unresolved:
                    raw.append(("EXE_COMPOSITE_UNRESOLVED", f"{name}({','.join(unresolved[:3])})"))

            if physical_columns is not None:
                table = _clean(metric.get("table"))
                column = _metric_source_column(metric) if not kind else _clean(metric.get("column"))
                if table and column and (table, column) not in physical_columns:
                    raw.append(("EXE_UNKNOWN_COLUMN", f"{name}({table}.{column})"))

        if physical_columns is not None:
            for dimension in [item for item in config.get("dimensions") or [] if isinstance(item, dict)]:
                table = _clean(dimension.get("table"))
                column = _clean(dimension.get("column"))
                if table and column and (table, column) not in physical_columns:
                    raw.append(("EXE_UNKNOWN_COLUMN", f"{_clean(dimension.get('name'))}({table}.{column})"))

    @staticmethod
    def _unresolved_formula_tokens(formula: str, metric_names: Set[str]) -> List[str]:
        """把 formula 中已知指标名剥掉后，剩余标识符即无法解析的片段。"""
        remaining = formula
        for name in sorted(metric_names, key=len, reverse=True):
            if name:
                remaining = remaining.replace(name, " ")
        tokens = [token for token in _IDENTIFIER_TOKEN.findall(remaining) if token not in metric_names]
        unique: List[str] = []
        for token in tokens:
            if token not in unique:
                unique.append(token)
        return unique

    def _check_coverage(
        self,
        metrics: List[Dict[str, Any]],
        dimensions: List[Dict[str, Any]],
        raw: List[Tuple[str, str]],
        physical_columns: Optional[Set[Tuple[str, str]]],
    ) -> None:
        if dimensions and not metrics:
            raw.append(("COV_NO_METRICS", _clean(dimensions[0].get("table")) or "-"))
        if metrics and not dimensions:
            raw.append(("COV_NO_DIMENSIONS", _clean(metrics[0].get("table")) or "-"))

        seen: Dict[Tuple[str, str], List[str]] = {}
        for dimension in dimensions:
            key = (_clean(dimension.get("table")), _clean(dimension.get("column")))
            if key[0] and key[1]:
                seen.setdefault(key, []).append(_clean(dimension.get("name")))
        for (table, column), names in seen.items():
            if len(names) > 1:
                raw.append(("COV_DUPLICATE_DIMENSION_COLUMN", f"{table}.{column}({len(names)}个)"))

        if physical_columns is not None:
            referenced = {key for key in seen}
            for metric in metrics:
                if metric.get("metric_kind"):
                    continue
                table = _clean(metric.get("table"))
                column = _metric_source_column(metric)
                if table and column:
                    referenced.add((table, column))
            tables = {key[0] for key in referenced}
            unmodeled = sorted(
                f"{table}.{column}"
                for table, column in physical_columns
                if table in tables and (table, column) not in referenced
            )
            if unmodeled:
                raw.append(("COV_UNMODELED_COLUMNS", f"{unmodeled[0]}({len(unmodeled)}列)"))

    def _check_consistency(
        self,
        metrics: List[Dict[str, Any]],
        dimensions: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        raw: List[Tuple[str, str]],
        physical_columns: Optional[Set[Tuple[str, str]]],
    ) -> None:
        expression_owner: Dict[str, str] = {}
        for metric in metrics:
            if metric.get("metric_kind"):
                continue
            expression = " ".join(str(metric.get("expression") or "").split()).upper()
            name = _clean(metric.get("name")) or "<未命名>"
            if not expression:
                continue
            if expression in expression_owner:
                raw.append(("CON_DUPLICATE_METRIC_EXPRESSION", f"{name}={expression_owner[expression]}"))
            else:
                expression_owner[expression] = name

        modeled_tables = {_clean(item.get("table")) for item in [*metrics, *dimensions] if _clean(item.get("table"))}
        seen_pairs: Dict[Tuple[str, str, str], int] = {}
        edges: Dict[str, Set[str]] = {}
        for relationship in relationships:
            left = _clean(relationship.get("left_table"))
            right = _clean(relationship.get("right_table"))
            conditions = relationship.get("join_conditions") or []
            columns_key = ",".join(
                f"{_clean(condition.get('left_column'))}={_clean(condition.get('right_column'))}"
                for condition in conditions
                if isinstance(condition, dict)
            )
            pair = (left, right, columns_key)
            seen_pairs[pair] = seen_pairs.get(pair, 0) + 1
            if left and right:
                if left == right:
                    raw.append(("CON_RELATIONSHIP_SELF", left))
                else:
                    edges.setdefault(left, set()).add(right)
                    edges.setdefault(right, set()).add(left)
                for table in (left, right):
                    if modeled_tables and table not in modeled_tables:
                        raw.append(("CON_RELATIONSHIP_TABLE_UNREFERENCED", table))
        for (left, right, _columns), count in seen_pairs.items():
            if count > 1:
                raw.append(("CON_RELATIONSHIP_DUPLICATE", f"{left}->{right}"))

        cycle = self._find_cycle(edges)
        if cycle:
            raw.append(("CON_RELATIONSHIP_CYCLE", "->".join(cycle)))

    @staticmethod
    def _find_cycle(edges: Dict[str, Set[str]]) -> List[str]:
        """无向图找一条环路径（表关系方向不构成 DAG 语义，按连通环判定）。"""
        for start in sorted(edges):
            path = [start]
            visited = {start}
            if SemanticQualityChecker._dfs(start, start, None, edges, visited, path):
                return path
        return []

    @staticmethod
    def _dfs(node: str, start: str, parent: Optional[str], edges: Dict[str, Set[str]], visited: Set[str], path: List[str]) -> bool:
        for neighbor in sorted(edges.get(node, set())):
            if neighbor == parent:
                continue
            if neighbor == start:
                path.append(start)
                return True
            if neighbor in visited:
                continue
            visited.add(neighbor)
            path.append(neighbor)
            if SemanticQualityChecker._dfs(neighbor, start, node, edges, visited, path):
                return True
            path.pop()
        return False

    def _check_documentation(
        self,
        config: Dict[str, Any],
        metrics: List[Dict[str, Any]],
        dimensions: List[Dict[str, Any]],
        raw: List[Tuple[str, str]],
    ) -> None:
        for metric in metrics:
            if not str(metric.get("description") or "").strip():
                raw.append(("DOC_METRIC_NO_DESCRIPTION", _clean(metric.get("name")) or "<未命名>"))
        for dimension in dimensions:
            if not str(dimension.get("description") or "").strip():
                raw.append(("DOC_DIMENSION_NO_DESCRIPTION", _clean(dimension.get("name")) or "<未命名>"))
        if not str(config.get("business_notes") or "").strip():
            raw.append(("DOC_NO_BUSINESS_NOTES", _clean(config.get("name")) or "-"))

    # ---- 聚合与统计 ----

    def _aggregate(self, raw: List[Tuple[str, str]]) -> List[QualityFinding]:
        grouped: Dict[str, List[str]] = {}
        for code, target in raw:
            grouped.setdefault(code, []).append(target)
        findings: List[QualityFinding] = []
        # 按 category -> severity 稳定排序，报告可读性优先
        category_order = {name: index for index, name in enumerate(("provenance", "executability", "coverage", "consistency", "documentation"))}
        severity_order = {"error": 0, "warning": 1, "info": 2}
        for code, targets in grouped.items():
            severity, category, label = RULES[code]
            examples = targets[:MAX_TARGET_EXAMPLES]
            suffix = f" 等 {len(targets)} 处" if len(targets) > MAX_TARGET_EXAMPLES else ""
            message = f"{label}: {', '.join(examples)}{suffix}" if targets and targets != ["-"] else label
            findings.append(QualityFinding(code=code, severity=severity, category=category, message=message, targets=targets))
        findings.sort(key=lambda item: (category_order.get(item.category, 99), severity_order.get(item.severity, 99), item.code))
        return findings

    def _stats(self, metrics: List[Dict[str, Any]], dimensions: List[Dict[str, Any]], relationships: List[Dict[str, Any]]) -> Dict[str, Any]:
        atomic = sum(1 for metric in metrics if not metric.get("metric_kind"))
        derived = sum(1 for metric in metrics if metric.get("metric_kind") == "derived")
        composite = sum(1 for metric in metrics if metric.get("metric_kind") == "composite")
        described_metrics = sum(1 for metric in metrics if str(metric.get("description") or "").strip())
        described_dimensions = sum(
            1 for dimension in dimensions if str(dimension.get("description") or "").strip()
        )
        return {
            "metric_count": len(metrics),
            "atomic_metric_count": atomic,
            "derived_metric_count": derived,
            "composite_metric_count": composite,
            "dimension_count": len(dimensions),
            "relationship_count": len(relationships),
            "described_metric_count": described_metrics,
            "described_dimension_count": described_dimensions,
        }


def get_semantic_quality_checker() -> SemanticQualityChecker:
    return SemanticQualityChecker()


__all__ = [
    "ConfigQualityReport",
    "QualityFinding",
    "SemanticQualityChecker",
    "get_semantic_quality_checker",
    "grade_for_score",
]
