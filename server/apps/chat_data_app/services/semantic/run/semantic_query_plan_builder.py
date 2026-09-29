"""AI/BI 语义查询适配器共享的查询计划组装原语。

只做查询计划节点的形状组装与无校验的树遍历，不做输入校验、不产生用户可见
错误文案；各入口的校验与报错契约由适配器自己保留（AI 侧上游已过
InputValidator 校验故宽松，BI 侧直面原始前端输入故严格）。
任何影响两条入口的横切语义（新过滤操作符、行级权限注入、派生指标展开等）
应优先落在本模块与 core/semantic_sql 编译层，而不是在单个适配器里各改一份。
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ..core.common import is_time_logical_dimension
from ..core.query_plan import (
    FORMULA_REF_PATTERN,
    is_composite_metric,
    is_derived_metric,
    parse_aggregate_expression,
    parse_formula_refs,
    semantic_table_alias,
)

logger = logging.getLogger(__name__)


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def metric_func_column(expression: Any) -> Tuple[str, str] | None:
    """解析 `SUM(col)` / `COUNT_DISTINCT(col)` 形态的聚合表达式。

    返回 (聚合函数, 列名)：列名取最后一个列段并去除反引号与空白，`*` 原样保留；
    无法解析时返回 None。
    """
    parsed = parse_aggregate_expression(normalize_text(expression))
    if not parsed:
        return None
    func, column_expr = parsed
    column = "*" if column_expr == "*" else column_expr.split(".")[-1].replace("`", "").strip()
    return func, column


def builder_node_alias(item: Any) -> str:
    """查询计划节点别名：优先 builder 驼峰 tableAlias，回退语义配置别名。"""
    camel_alias = normalize_text((item or {}).get("tableAlias"))
    if camel_alias:
        return camel_alias
    return semantic_table_alias(item)


def assemble_dimension_filter_node(
    dim_cfg: Dict[str, Any],
    *,
    node_id: str,
    operator: Any,
    logic: Any = "AND",
    expression: Any = None,
) -> Dict[str, Any]:
    """组装维度过滤计划节点的身份字段（表定位/操作符/逻辑）。

    值字段（value/valueType/rightExpression 等）语义两条入口不同，由各适配器
    在返回节点上自行补充。
    """
    config = dim_cfg or {}
    table = config.get("table")
    return {
        "id": node_id,
        "table": table,
        "tableAlias": semantic_table_alias(config) or table,
        "column": config.get("column"),
        "expression": expression if expression is not None else config.get("expression"),
        "operator": operator,
        "logic": logic,
    }


def collect_filter_node_tables(
    node: Any,
    tables_needed: Set[str],
    leaf_table_extractor: Callable[[Dict[str, Any], Set[str]], None],
) -> None:
    """递归收集 and/or 过滤树涉及的表。

    叶子表提取交给调用方；非 dict 子节点静默跳过（两条入口现有行为一致）。
    """
    if not isinstance(node, dict):
        return
    if "and" in node:
        for child in node.get("and") or []:
            collect_filter_node_tables(child, tables_needed, leaf_table_extractor)
        return
    if "or" in node:
        for child in node.get("or") or []:
            collect_filter_node_tables(child, tables_needed, leaf_table_extractor)
        return
    leaf_table_extractor(node, tables_needed)


def collect_operand_tables(
    operand: Any,
    tables_needed: Set[str],
    metric_table_extractor: Callable[[Dict[str, Any], Set[str]], None],
) -> None:
    """递归收集 HAVING 操作数树（metric/binary）涉及的表。"""
    if not isinstance(operand, dict):
        return
    operand_type = normalize_text(operand.get("type")).lower()
    if operand_type == "metric":
        metric_table_extractor(operand, tables_needed)
        return
    if operand_type == "binary":
        collect_operand_tables(operand.get("left"), tables_needed, metric_table_extractor)
        collect_operand_tables(operand.get("right"), tables_needed, metric_table_extractor)


# ---------------------------------------------------------------------------
# 派生指标展开（原子指标 + 统计周期 + 修饰词）
# ---------------------------------------------------------------------------

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_PERIOD_LABELS = {
    "today": "今日",
    "yesterday": "昨日",
    "this_week": "本周",
    "this_month": "本月",
}


def _timestamp(value: datetime) -> str:
    return value.strftime(_TIMESTAMP_FORMAT)


def _day_start(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def derive_period_date_range(period: Dict[str, Any], now: datetime) -> tuple[str, str]:
    """把派生指标周期翻译为半开时间区间 [start, end)，与时间维度等值过滤的规范化语义一致。"""
    kind = normalize_text(period.get("kind")).lower()
    if kind == "today":
        start = _day_start(now)
        return _timestamp(start), _timestamp(start + timedelta(days=1))
    if kind == "yesterday":
        start = _day_start(now - timedelta(days=1))
        return _timestamp(start), _timestamp(start + timedelta(days=1))
    if kind == "last_n_days":
        n = int(period.get("n") or 0)
        if n < 1:
            raise ValueError("派生指标周期 last_n_days 的 n 必须是正整数")
        start = _day_start(now - timedelta(days=n - 1))
        return _timestamp(start), _timestamp(_day_start(now) + timedelta(days=1))
    if kind == "this_week":
        start = _day_start(now - timedelta(days=now.weekday()))
        return _timestamp(start), _timestamp(start + timedelta(weeks=1))
    if kind == "this_month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = (start + timedelta(days=32)).replace(day=1)
        return _timestamp(start), _timestamp(next_month)
    if kind == "between":
        return _between_period_range(period)
    raise ValueError(f"不支持的派生指标周期类型: {kind or '(空)'}")


def _between_period_range(period: Dict[str, Any]) -> tuple[str, str]:
    """自定义区间：end 为日期（含当日）时转次日零点半开，为日期时间时原样半开。"""
    raw_start = normalize_text(period.get("start"))
    raw_end = normalize_text(period.get("end"))
    if not raw_start or not raw_end:
        raise ValueError("派生指标周期 between 缺少 start/end")
    try:
        start = datetime.strptime(raw_start, "%Y-%m-%d")
    except ValueError:
        start = datetime.strptime(raw_start, _TIMESTAMP_FORMAT)
    if len(raw_end) == 10:
        end = datetime.strptime(raw_end, "%Y-%m-%d") + timedelta(days=1)
    else:
        end = datetime.strptime(raw_end, _TIMESTAMP_FORMAT)
    if end < start:
        raise ValueError("派生指标周期 between 的 end 不能早于 start")
    return _timestamp(_day_start(start)), _timestamp(end)


def _describe_period(period: Dict[str, Any]) -> str:
    kind = normalize_text(period.get("kind")).lower()
    if kind == "last_n_days":
        return f"近{period.get('n')}天"
    if kind == "between":
        return f"{normalize_text(period.get('start'))}~{normalize_text(period.get('end'))}"
    return _PERIOD_LABELS.get(kind, kind)


def _mapped_dimension_value(dim_cfg: Dict[str, Any], value: Any) -> Any:
    """按维度 mapping 把过滤值规范到枚举值（键忽略大小写），与用户过滤链路口径一致。"""
    mapping = dim_cfg.get("mapping") if isinstance(dim_cfg.get("mapping"), dict) else None
    if not mapping or value is None:
        return value
    if isinstance(value, list):
        return [_mapped_dimension_value(dim_cfg, item) for item in value]
    value_text = str(value)
    for key, mapped_value in mapping.items():
        if str(key).lower() == value_text.lower():
            return mapped_value
    return value


def derived_metric_filter_leaves(
    metric_cfg: Dict[str, Any],
    dimensions_map: Dict[str, Dict[str, Any]],
    *,
    now: datetime | None = None,
) -> List[Dict[str, Any]]:
    """把派生指标的周期与修饰词翻译成语义过滤叶子（AI DSL 形态：dimension 键）。

    叶子走各适配器既有的过滤映射链路（时间区间规范化/操作符校验/值拷贝），
    因此这里只产出 {dimension, operator, value} 形状，不直接产出 builder 节点。
    """
    leaves: List[Dict[str, Any]] = []
    period = (metric_cfg or {}).get("period") or {}
    if period:
        dim_name = normalize_text(period.get("time_dimension"))
        dim_cfg = dimensions_map.get(dim_name) or {}
        if not dim_cfg or not is_time_logical_dimension(dim_cfg):
            raise ValueError(f"派生指标 '{metric_cfg.get('name')}' 的周期维度不是时间维度: {dim_name or '(空)'}")
        start, end = derive_period_date_range(period, now or datetime.now())
        leaves.append({"dimension": dim_name, "operator": ">=", "value": start})
        leaves.append({"dimension": dim_name, "operator": "<", "value": end})
    for item in (metric_cfg or {}).get("filters") or []:
        item = item or {}
        dim_name = normalize_text(item.get("dimension"))
        dim_cfg = dimensions_map.get(dim_name) or {}
        leaves.append(
            {
                "dimension": dim_name,
                "operator": normalize_text(item.get("operator")) or "=",
                "value": _mapped_dimension_value(dim_cfg, item.get("value")),
            }
        )
    return leaves


def resolve_derived_metric(
    metric_cfg: Dict[str, Any],
    metrics_map: Dict[str, Dict[str, Any]],
    dimensions_map: Dict[str, Dict[str, Any]],
    *,
    now: datetime | None = None,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """解析指标配置：派生指标返回 (基础原子指标配置副本, 固定过滤叶子)；原子指标原样返回。

    基础指标缺失、仍是派生指标或为复合指标时抛 ValueError（保存侧 config_validator
    已拦截，查询侧遇到即配置数据被绕过校验修改）。
    """
    cfg = metric_cfg or {}
    if not is_derived_metric(cfg):
        return cfg, []
    base_name = normalize_text(cfg.get("base_metric"))
    base_cfg = metrics_map.get(base_name) or {}
    if not base_cfg:
        raise ValueError(f"派生指标 '{cfg.get('name')}' 的基础指标不存在: {base_name or '(空)'}")
    if is_derived_metric(base_cfg):
        raise ValueError(f"派生指标 '{cfg.get('name')}' 的基础指标不能是派生指标: {base_name}")
    if is_composite_metric(base_cfg):
        raise ValueError(f"派生指标 '{cfg.get('name')}' 的基础指标不能是复合指标: {base_name}")
    return dict(base_cfg), derived_metric_filter_leaves(cfg, dimensions_map, now=now)


def _append_derived_metric_filter_part(parts: List[str], item: Any) -> None:
    """把派生指标的单个修饰词过滤翻译成人类可读片段并追加到 parts。"""
    dimension = normalize_text((item or {}).get("dimension"))
    operator = normalize_text((item or {}).get("operator")) or "="
    value = (item or {}).get("value")
    value_text = "、".join(str(entry) for entry in value) if isinstance(value, list) else str(value or "")
    if dimension:
        parts.append(f"{dimension}{operator}{value_text}")


def derived_metric_verbalization(metric_cfg: Dict[str, Any]) -> str:
    """派生指标的人类可读口径描述，供指标集摘要（AI 检索）与 Ossie 导出使用。"""
    cfg = metric_cfg or {}
    parts: List[str] = []
    base_name = normalize_text(cfg.get("base_metric"))
    if base_name:
        parts.append(f"指标[{base_name}]")
    period = cfg.get("period") or {}
    if period:
        parts.append(_describe_period(period))
    for item in cfg.get("filters") or []:
        _append_derived_metric_filter_part(parts, item)
    return " · ".join(parts)


def _resolve_composite_member(
    name: Any,
    ref: str,
    member_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """校验单个复合指标成员并返回其编译形态 {name, expression, table}；配置无效抛 ValueError。"""
    if is_composite_metric(member_cfg):
        raise ValueError(f"复合指标 '{name}' 不能引用其他复合指标: {ref}")
    if is_derived_metric(member_cfg):
        raise ValueError(f"复合指标 '{name}' 的成员暂不支持派生指标: {ref}")
    member_expression = normalize_text(member_cfg.get("expression"))
    if not parse_aggregate_expression(member_expression):
        raise ValueError(f"复合指标 '{name}' 引用的指标不是聚合指标: {ref}")
    ref_table = normalize_text(member_cfg.get("table"))
    if not ref_table:
        raise ValueError(f"复合指标 '{name}' 引用的指标缺少物理表: {ref}")
    return {"name": ref, "expression": member_expression, "table": ref_table}


def resolve_composite_metric(
    metric_cfg: Dict[str, Any],
    metrics_map: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """解析复合指标：返回 {formula, members, table}；非复合指标返回 None。

    v1 约束（保存侧 config_validator 同步校验）：
    - 成员必须是同表的聚合原子指标，暂不支持派生成员（条件聚合编译属 v2）；
    - 不允许引用其他复合指标（防环，天然单层）。
    配置无效时抛 ValueError（适配器侧捕获后降级为原始配置）。
    """
    cfg = metric_cfg or {}
    if not is_composite_metric(cfg):
        return None
    formula = normalize_text(cfg.get("formula"))
    refs = parse_formula_refs(formula)
    if not refs:
        raise ValueError(f"复合指标 '{cfg.get('name')}' 的公式没有任何 {{指标}} 引用")
    members: List[Dict[str, Any]] = []
    member_table = ""
    for ref in refs:
        member_cfg = metrics_map.get(ref) or {}
        if not member_cfg:
            raise ValueError(f"复合指标 '{cfg.get('name')}' 引用的指标不存在: {ref}")
        member = _resolve_composite_member(cfg.get("name"), ref, member_cfg)
        if member_table and member["table"] != member_table:
            raise ValueError(
                f"复合指标 '{cfg.get('name')}' 的成员指标跨表（{member_table} / {member['table']}），暂不支持"
            )
        member_table = member["table"]
        members.append({"name": ref, "expression": member["expression"]})
    return {"formula": formula, "members": members, "table": member_table}


def substitute_composite_formula(formula: str, members: List[Dict[str, Any]]) -> str:
    """把公式中的 {指标引用} 代入成员聚合表达式，除号后的引用自动包 NULLIF 防除零。

    仅处理 `{a} / {b}` 形态的直接除法；除数是括号组合（如 `{a} / ({b} + {c})`）时
    需用户在公式中显式写 NULLIF。
    """
    expr_map = {member["name"]: member["expression"] for member in members or []}
    text = str(formula or "")

    def replace_ref(match: "re.Match[str]") -> str:
        fragment = expr_map.get(match.group(1).strip())
        if not fragment:
            return match.group(0)
        if text[: match.start()].rstrip().endswith("/"):
            return f"NULLIF({fragment}, 0)"
        return fragment

    return FORMULA_REF_PATTERN.sub(replace_ref, text)


def composite_metric_verbalization(
    metric_cfg: Dict[str, Any],
    metrics_map: Dict[str, Dict[str, Any]],
) -> str:
    """复合指标的人类可读口径描述：公式 + 各成员指标表达式；非复合指标返回空串。"""
    cfg = metric_cfg or {}
    if not is_composite_metric(cfg):
        return ""
    formula = normalize_text(cfg.get("formula"))
    if not formula:
        return ""
    try:
        members = resolve_composite_metric(cfg, metrics_map)["members"]
    except ValueError:
        members = [
            {"name": ref, "expression": normalize_text((metrics_map.get(ref) or {}).get("expression"))}
            for ref in parse_formula_refs(formula)
        ]
    descriptions: List[str] = []
    for member in members:
        expression = member.get("expression")
        if expression:
            descriptions.append(f"{member['name']} = {expression}")
    formula_text = formula
    for member in members:
        formula_text = formula_text.replace(f"{{{member['name']}}}", member["name"])
    if descriptions:
        return f"{formula_text}（{'；'.join(descriptions)}）"
    return formula_text


def _composite_base_cfg(
    metric_name: str,
    base_cfg: Dict[str, Any],
    metrics_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """复合指标预解析：formula 代入成员聚合表达式，产出 RAW 形态的编译配置。

    编译期直接复用既有 RAW 表达式通道（select_compiler 原样直出），
    绑定成员物理表供 JOIN 规划；配置无效时回退原配置（保存侧校验已拦截）。
    """
    metric_cfg = metrics_map.get(metric_name) or {}
    try:
        composite_plan = resolve_composite_metric(metric_cfg, metrics_map)
    except ValueError as exc:
        logger.warning("复合指标配置无效，查询时回退为原始配置: %s", exc)
        return base_cfg
    if not composite_plan:
        return base_cfg
    return {
        **base_cfg,
        "expression": substitute_composite_formula(composite_plan["formula"], composite_plan["members"]),
        "table": composite_plan["table"],
        "table_alias": composite_plan["table"],
    }


def build_metric_base_configs(
    metrics_map: Dict[str, Dict[str, Any]],
    dimensions_map: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """派生/复合指标统一预解析（AI/BI 适配器共享入口）。

    返回 (metric_base_cfgs, derived_fixed_leaves)：
    - metric_base_cfgs: 每个指标的编译配置（派生换基础原子配置、复合代入 RAW 表达式）；
    - derived_fixed_leaves: 派生指标的周期/修饰词固定过滤叶子。
    配置无效时回退原始配置并记录告警（保存侧 config_validator 已拦截）。
    """
    base_cfgs: Dict[str, Dict[str, Any]] = {}
    fixed_leaves_map: Dict[str, List[Dict[str, Any]]] = {}
    for metric_name, metric_cfg in metrics_map.items():
        try:
            base_cfg, fixed_leaves = resolve_derived_metric(
                metric_cfg,
                metrics_map,
                dimensions_map,
            )
        except ValueError as exc:
            logger.warning("派生指标配置无效，查询时回退为原始配置: %s", exc)
            base_cfg, fixed_leaves = metric_cfg, []
        base_cfgs[metric_name] = _composite_base_cfg(metric_name, base_cfg, metrics_map)
        fixed_leaves_map[metric_name] = fixed_leaves
    return base_cfgs, fixed_leaves_map


def check_mixed_derived_periods(
    metric_names: List[str],
    metrics_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """检测同一查询是否混用了不同周期的派生指标。

    v1 固定过滤注入整条 WHERE，多个不同周期的派生指标取交集会得到错误结果
    （如 今日成交额 vs 近7天成交额 同查会被卡成同值）。周期相同的派生指标
    可安全混用；返回错误描述或 None。
    """
    period_groups: Dict[str, List[str]] = {}
    for name in metric_names:
        cfg = metrics_map.get(name) or {}
        if not is_derived_metric(cfg):
            continue
        period = cfg.get("period") or {}
        # 口径签名：周期类型 + 时间维度 + 参数；修饰词过滤不影响周期窗口交集
        key = "|".join(
            [
                normalize_text(period.get("kind")),
                normalize_text(period.get("time_dimension")),
                normalize_text(period.get("n")),
                normalize_text(period.get("start")),
                normalize_text(period.get("end")),
            ]
        )
        period_groups.setdefault(key, []).append(name)
    if len(period_groups) > 1:
        names = [name for names_list in period_groups.values() for name in names_list]
        return f"同一查询暂不支持混用不同周期的派生指标（{'、'.join(names)}），请分开查询或改用同一周期口径"
    return None
