"""
语义匹配 Prompt 模板

提供自然语言到 DSL 转换的 Prompt 模板。
"""

from typing import List, Dict, Any, Optional
from ..models import SemanticConfig, SemanticDimension, SemanticMetric


class MatchPromptBuilder:
    """
    语义匹配 Prompt 构建器

    根据语义配置动态生成 Prompt。
    """

    def __init__(self, config: SemanticConfig):
        """
        初始化构建器

        Args:
            config: 语义配置
        """
        self.config = config

    def build(self, query: str) -> str:
        """
        构建完整的 Prompt

        Args:
            query: 用户查询

        Returns:
            Prompt 字符串
        """
        return MATCH_PROMPT_TEMPLATE.format(
            config_name=self.config.name,
            config_description=self.config.description or "",
            metrics_section=self._build_metrics_section(),
            dimensions_section=self._build_dimensions_section(),
            relationships_section=self._build_relationships_section(),
            user_query=query
        )

    def _build_metrics_section(self) -> str:
        """构建指标部分"""
        if not self.config.metrics:
            return "（无可用指标）"

        lines = []
        for m in self.config.metrics:
            desc = m.description or m.expression
            unit_info = f"，单位：{m.unit}" if m.unit else ""
            lines.append(f"  - {m.name}: {desc}{unit_info}")

        return "\n".join(lines)

    def _build_dimensions_section(self) -> str:
        """构建维度部分"""
        if not self.config.dimensions:
            return "（无可用维度）"

        lines = []
        for d in self.config.dimensions:
            desc = d.description or d.column
            type_info = f" [{d.type.value}]"

            # 时间维度特殊处理
            if d.type.value == 'time':
                type_info += " (支持时间粒度: year/quarter/month/week/day/hour)"

            # 添加允许值信息
            if d.allowed_values and len(d.allowed_values) <= 10:
                values_str = ", ".join([str(v) for v in d.allowed_values])
                type_info += f" (可选值: {values_str})"

            lines.append(f"  - {d.name}: {desc}{type_info}")

        return "\n".join(lines)

    def _build_relationships_section(self) -> str:
        """构建关系部分"""
        if not self.config.relationships:
            return "（无表关联关系）"

        lines = []
        for r in self.config.relationships:
            cond_parts = []
            for cond in r.join_conditions:
                left_col = (cond or {}).get("left_column")
                right_col = (cond or {}).get("right_column")
                if left_col and right_col:
                    cond_parts.append(f"{r.left_table}.{left_col} = {r.right_table}.{right_col}")
            if cond_parts:
                lines.append(f"  - {' AND '.join(cond_parts)}")

        return "\n".join(lines)

    def build_concise(self, query: str) -> str:
        """
        构建简洁版 Prompt（减少 token 使用）

        Args:
            query: 用户查询

        Returns:
            Prompt 字符串
        """
        return MATCH_PROMPT_CONCISE_TEMPLATE.format(
            metrics_list=", ".join([m.name for m in self.config.metrics]),
            dimensions_list=", ".join([d.name for d in self.config.dimensions]),
            time_dimensions=", ".join([
                d.name for d in self.config.dimensions
                if d.type.value == 'time'
            ]),
            user_query=query
        )


# 完整版 Prompt 模板
MATCH_PROMPT_TEMPLATE = """你是一个数据分析助手，负责将用户的自然语言查询转换为结构化的查询请求。

## 数据模型：{config_name}
{config_description}

## 可用的指标（Metrics）：
{metrics_section}

## 可用的维度（Dimensions）：
{dimensions_section}

## 表关联关系：
{relationships_section}

## 用户查询：
{user_query}

## 你的任务：
分析用户的查询意图，识别：
1. 用户想查询哪些指标（必须从上面的指标列表中选择）
2. 用户想按什么维度分组（可选，从上面的维度列表中选择）
3. 用户的过滤条件是什么（可选）
4. 用户是否需要对聚合结果进行过滤（HAVING 条件）
5. 用户是否需要排序和限制返回数量

## 返回格式：
请返回一个 JSON 对象，包含以下字段：
```json
{{
  "metrics": ["指标名称1", "指标名称2"],
  "dimensions": [
    "普通维度名称",
    {{"name": "时间维度名称", "time_grain": "month"}}
  ],
  "filters": [
    {{"dimension": "维度名称", "operator": "=", "value": "值", "value_type": "literal"}},
    {{
      "or": [
        {{"dimension": "维度1", "operator": "=", "value": "值1", "value_type": "literal"}},
        {{"dimension": "维度2", "operator": "IN", "value": ["值2", "值3"], "value_type": "literal"}}
      ]
    }}
  ],
  "having": [
    {{"metric": "指标名称", "operator": ">", "value": 100}}
  ],
  "order_by": [
    {{"field": "指标或维度名称", "direction": "DESC"}}
  ],
  "limit": 10
}}
```

## 重要规则：
1. **只使用上面列出的指标和维度**，不要生成不存在的字段
2. 先判断是“明细查询”还是“聚合分析查询”：
   - 明细查询：`metrics` 也可填写可用维度/原始字段；`dimensions` 通常为空
   - 聚合分析查询：`metrics` 填可用指标；`dimensions` 仅用于分组
3. `dimensions` 的职责是分组，不是明细展示字段
4. 时间维度可以指定粒度（time_grain）：year, quarter, month, week, day, hour
5. 过滤条件支持树形嵌套（and/or），操作符支持：=, !=, >, >=, <, <=, IN, NOT IN, LIKE, BETWEEN
5.1 filters 的叶子节点可选 value_type：literal 或 sql_function（函数调用必须标记 sql_function）
5.2 当 operator=BETWEEN 且 value 为数组时，value_type 应按位数组标注（如 ["literal","sql_function"]）
6. 如果用户说"前N个"、"TopN"、"最高的N个"，请设置 limit 和相应的降序排序
7. 如果用户说"最低的N个"、"最少的N个"，请设置 limit 和相应的升序排序
8. 如果查询中包含"大于"、"超过"等词语且针对指标，应该放在 having 中而非 filters 中

## 请只返回 JSON，不要包含其他内容。
"""

# 简洁版 Prompt 模板
MATCH_PROMPT_CONCISE_TEMPLATE = """分析查询意图，返回 JSON：

指标：{metrics_list}
维度：{dimensions_list}
时间维度：{time_dimensions}

查询：{user_query}

返回格式：
{{"metrics": [], "dimensions": [], "filters": [], "having": [], "order_by": [], "limit": null}}

规则：
- 只用列出的指标和维度
- 明细查询时，metrics 也可填写原始字段；dimensions 仅用于分组
- 时间维度可加 time_grain: year/quarter/month/week/day/hour
- filters 用于维度过滤，having 用于指标过滤
- filters 叶子节点 value_type 只允许 literal/sql_function
- TopN 用 limit + order_by DESC
"""


def build_match_prompt(
    query: str,
    config: SemanticConfig
) -> str:
    """
    构建匹配 Prompt（便捷函数）

    Args:
        query: 用户查询
        config: 语义配置

    Returns:
        Prompt 字符串
    """
    builder = MatchPromptBuilder(config)
    return builder.build(query)


def build_concise_match_prompt(
    query: str,
    config: SemanticConfig
) -> str:
    """
    构建简洁匹配 Prompt（便捷函数）

    Args:
        query: 用户查询
        config: 语义配置

    Returns:
        Prompt 字符串
    """
    builder = MatchPromptBuilder(config)
    return builder.build_concise(query)
