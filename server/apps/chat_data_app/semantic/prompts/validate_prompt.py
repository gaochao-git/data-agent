"""
验证 Prompt 模板

提供 DSL 和 SQL 验证的 Prompt 模板。
"""

from typing import List, Dict, Any, Optional
from ..models import SemanticConfig


class ValidatePromptBuilder:
    """
    验证 Prompt 构建器

    生成用于验证 DSL 和 SQL 的 Prompt。
    """

    def __init__(self, config: SemanticConfig):
        """
        初始化构建器

        Args:
            config: 语义配置
        """
        self.config = config

    def build_intent_check(
        self,
        original_query: str,
        dsl: Dict[str, Any]
    ) -> str:
        """
        构建意图检查 Prompt

        Args:
            original_query: 原始查询
            dsl: 生成的 DSL

        Returns:
            Prompt 字符串
        """
        return INTENT_CHECK_PROMPT.format(
            original_query=original_query,
            dsl_json=str(dsl),
            metrics_list=", ".join([m.name for m in self.config.metrics]),
            dimensions_list=", ".join([d.name for d in self.config.dimensions])
        )

    def build_sql_check(
        self,
        original_query: str,
        sql: str
    ) -> str:
        """
        构建 SQL 检查 Prompt

        Args:
            original_query: 原始查询
            sql: 生成的 SQL

        Returns:
            Prompt 字符串
        """
        return SQL_CHECK_PROMPT.format(
            original_query=original_query,
            generated_sql=sql
        )

    def build_ambiguity_check(
        self,
        query: str
    ) -> str:
        """
        构建歧义检查 Prompt

        Args:
            query: 用户查询

        Returns:
            Prompt 字符串
        """
        return AMBIGUITY_CHECK_PROMPT.format(
            user_query=query,
            metrics_list=", ".join([m.name for m in self.config.metrics]),
            dimensions_list=", ".join([d.name for d in self.config.dimensions])
        )


# 意图检查 Prompt
INTENT_CHECK_PROMPT = """请验证以下 DSL 是否正确表达了用户的查询意图。

## 原始查询：
{original_query}

## 生成的 DSL：
{dsl_json}

## 可用指标：
{metrics_list}

## 可用维度：
{dimensions_list}

## 验证要点：
1. 指标是否正确？是否遗漏了用户想要的指标？
2. 维度是否正确？用户是否想按某个维度分组？
3. 过滤条件是否正确？是否正确理解了用户的筛选意图？
4. 时间粒度是否正确？（如果涉及时间）
5. 排序和限制是否符合用户意图？

## 返回格式：
```json
{{
  "is_valid": true/false,
  "confidence": 0.0-1.0,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1", "建议2"]
}}
```
"""

# SQL 检查 Prompt
SQL_CHECK_PROMPT = """请检查以下 SQL 是否能正确回答用户的问题。

## 原始查询：
{original_query}

## 生成的 SQL：
```sql
{generated_sql}
```

## 检查要点：
1. SQL 语法是否正确？
2. 表连接是否正确？
3. 聚合函数是否正确？
4. GROUP BY 是否与 SELECT 匹配？
5. WHERE 和 HAVING 是否使用正确？
6. 是否可能返回用户期望的结果？

## 返回格式：
```json
{{
  "is_valid": true/false,
  "issues": ["问题1", "问题2"],
  "fixed_sql": "如果需要修复，提供修复后的 SQL，否则为 null"
}}
```
"""

# 歧义检查 Prompt
AMBIGUITY_CHECK_PROMPT = """分析以下查询是否存在歧义或不明确的地方。

## 用户查询：
{user_query}

## 可用指标：
{metrics_list}

## 可用维度：
{dimensions_list}

## 分析要点：
1. 查询中的指标/维度是否明确？
2. 时间范围是否明确？
3. 过滤条件是否明确？
4. 是否需要用户澄清？

## 返回格式：
```json
{{
  "is_ambiguous": true/false,
  "ambiguous_parts": ["不明确的部分1", "不明确的部分2"],
  "clarification_questions": ["澄清问题1", "澄清问题2"],
  "default_interpretation": {{
    "metrics": ["推荐的指标"],
    "dimensions": ["推荐的维度"],
    "explanation": "默认解释说明"
  }}
}}
```
"""


def build_intent_check_prompt(
    original_query: str,
    dsl: Dict[str, Any],
    config: SemanticConfig
) -> str:
    """
    构建意图检查 Prompt（便捷函数）

    Args:
        original_query: 原始查询
        dsl: 生成的 DSL
        config: 语义配置

    Returns:
        Prompt 字符串
    """
    builder = ValidatePromptBuilder(config)
    return builder.build_intent_check(original_query, dsl)


def build_sql_check_prompt(
    original_query: str,
    sql: str,
    config: SemanticConfig
) -> str:
    """
    构建 SQL 检查 Prompt（便捷函数）

    Args:
        original_query: 原始查询
        sql: 生成的 SQL
        config: 语义配置

    Returns:
        Prompt 字符串
    """
    builder = ValidatePromptBuilder(config)
    return builder.build_sql_check(original_query, sql)


def build_ambiguity_check_prompt(
    query: str,
    config: SemanticConfig
) -> str:
    """
    构建歧义检查 Prompt（便捷函数）

    Args:
        query: 用户查询
        config: 语义配置

    Returns:
        Prompt 字符串
    """
    builder = ValidatePromptBuilder(config)
    return builder.build_ambiguity_check(query)
