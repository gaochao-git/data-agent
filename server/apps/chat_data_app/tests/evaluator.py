"""
NL2SQL/NL2DSL 评估器

支持的评估指标：
- Execution Accuracy (EX): 执行准确率
- Component Match (CM): 组件匹配率
- Exact Match (EM): 精确匹配率
- LLM Judge: 基于大模型的语义等价性评估
"""

import re
import json
from typing import Any
from difflib import SequenceMatcher


class NL2SQLEvaluator:
    """NL2SQL 评估器"""

    def __init__(self, use_llm_judge: bool = True):
        """
        初始化评估器

        Args:
            use_llm_judge: 是否使用 LLM 作为评判器（推荐）
        """
        self.use_llm_judge = use_llm_judge

    async def evaluate_with_llm(
        self,
        question: str,
        predicted_sql: str,
        expected_sql: str,
        table_schemas: str = "",
    ) -> dict:
        """
        使用 LLM 评估 SQL 的语义正确性

        Args:
            question: 用户的自然语言问题
            predicted_sql: 生成的 SQL
            expected_sql: 标准答案 SQL
            table_schemas: 表结构信息（可选）

        Returns:
            LLM 评估结果：{"is_correct": bool, "confidence": float, "reason": str}
        """
        from langchain_core.messages import HumanMessage
        from utils.llm import get_llm

        prompt = f"""你是一个 SQL 专家，需要评估生成的 SQL 是否能正确回答用户的问题。

## 用户问题
{question}

## 标准 SQL
```sql
{expected_sql}
```

## 生成的 SQL
```sql
{predicted_sql}
```

{f"## 表结构{chr(10)}{table_schemas}" if table_schemas else ""}

## 评估标准
1. **语义等价**：生成的 SQL 是否能得到与标准 SQL 相同或等价的查询结果？
2. **合理扩展**：即使 SQL 不完全相同，只要额外的条件/排序/限制是合理的就算正确
3. **常见差异**（不影响正确性）：
   - 字段顺序不同
   - JOIN 方式不同（INNER JOIN vs WHERE）
   - 子查询 vs CTE vs JOIN
   - 添加了合理的 is_active=1、ORDER BY、LIMIT 等
   - 使用了别名或不同的表达式但语义相同

## 输出格式（JSON）
{{
  "is_correct": true/false,
  "confidence": 0.0-1.0,
  "reason": "简短说明判断理由"
}}

请只返回 JSON，不要其他内容。"""

        from apps.chat_data_app.services.llm_config import get_chat_data_llm
        llm = get_chat_data_llm(streaming=False, temperature=0)
        response = await llm.ainvoke([HumanMessage(content=prompt)])

        # 解析 LLM 返回的 JSON
        content = response.content.strip()

        # 提取 JSON（可能被 ```json 包裹）
        json_match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        try:
            result = json.loads(content)
            return {
                "is_correct": result.get("is_correct", False),
                "confidence": result.get("confidence", 0.0),
                "reason": result.get("reason", ""),
            }
        except json.JSONDecodeError:
            # 解析失败，返回默认值
            return {
                "is_correct": False,
                "confidence": 0.0,
                "reason": f"LLM 返回格式错误: {content[:100]}",
            }

    @staticmethod
    def normalize_sql(sql: str) -> str:
        """规范化 SQL 语句，用于比较"""
        # 转小写
        sql = sql.lower()
        # 移除多余空白
        sql = re.sub(r'\s+', ' ', sql)
        # 移除末尾分号
        sql = sql.rstrip(';').strip()
        return sql

    @staticmethod
    def extract_components(sql: str) -> set[str]:
        """提取 SQL 组件（SELECT, FROM, WHERE, JOIN, GROUP BY, etc.）"""
        sql = sql.upper()
        components = set()

        if 'SELECT' in sql:
            components.add('SELECT')
        if 'FROM' in sql:
            components.add('FROM')
        if 'WHERE' in sql:
            components.add('WHERE')
        if 'JOIN' in sql or 'INNER JOIN' in sql or 'LEFT JOIN' in sql or 'RIGHT JOIN' in sql:
            components.add('JOIN')
        if 'GROUP BY' in sql:
            components.add('GROUP BY')
        if 'HAVING' in sql:
            components.add('HAVING')
        if 'ORDER BY' in sql:
            components.add('ORDER BY')
        if 'LIMIT' in sql:
            components.add('LIMIT')
        if 'DISTINCT' in sql:
            components.add('DISTINCT')
        if 'COUNT(' in sql:
            components.add('COUNT')
        if 'SUM(' in sql:
            components.add('SUM')
        if 'AVG(' in sql:
            components.add('AVG')
        if 'MAX(' in sql:
            components.add('MAX')
        if 'MIN(' in sql:
            components.add('MIN')
        if 'IS NULL' in sql or 'IS NOT NULL' in sql:
            components.add('IS NULL')
        if 'LIKE' in sql:
            components.add('LIKE')
        if re.search(r'\bIN\s*\(', sql):
            components.add('IN')
        # 检测子查询
        if sql.count('SELECT') > 1:
            components.add('SUBQUERY')

        return components

    def evaluate_exact_match(self, predicted_sql: str, ground_truth_sql: str) -> bool:
        """精确匹配评估"""
        return self.normalize_sql(predicted_sql) == self.normalize_sql(ground_truth_sql)

    def evaluate_component_match(
        self, predicted_sql: str, ground_truth_components: list[str]
    ) -> dict[str, Any]:
        """组件匹配评估"""
        predicted_components = self.extract_components(predicted_sql)
        expected_components = set(ground_truth_components)

        # 计算匹配的组件
        matched = predicted_components & expected_components
        missing = expected_components - predicted_components
        extra = predicted_components - expected_components

        # 计算匹配率
        if len(expected_components) == 0:
            match_rate = 1.0 if len(predicted_components) == 0 else 0.0
        else:
            match_rate = len(matched) / len(expected_components)

        return {
            "match_rate": match_rate,
            "matched": list(matched),
            "missing": list(missing),
            "extra": list(extra),
            "predicted_components": list(predicted_components),
            "expected_components": list(expected_components),
        }

    def calculate_similarity(self, predicted_sql: str, ground_truth_sql: str) -> float:
        """计算 SQL 相似度（基于字符串相似度）"""
        norm_predicted = self.normalize_sql(predicted_sql)
        norm_ground_truth = self.normalize_sql(ground_truth_sql)
        return SequenceMatcher(None, norm_predicted, norm_ground_truth).ratio()

    def evaluate(
        self, predicted_sql: str, ground_truth: dict[str, Any]
    ) -> dict[str, Any]:
        """综合评估"""
        ground_truth_sql = ground_truth.get("sql", "")
        ground_truth_components = ground_truth.get("components", [])

        # 精确匹配
        exact_match = self.evaluate_exact_match(predicted_sql, ground_truth_sql)

        # 组件匹配
        component_result = self.evaluate_component_match(
            predicted_sql, ground_truth_components
        )

        # 相似度
        similarity = self.calculate_similarity(predicted_sql, ground_truth_sql)

        return {
            "exact_match": exact_match,
            "component_match_rate": component_result["match_rate"],
            "similarity": similarity,
            "component_details": component_result,
            "predicted_sql": predicted_sql,
            "ground_truth_sql": ground_truth_sql,
        }


class NL2DSLEvaluator:
    """NL2DSL 评估器"""

    @staticmethod
    def normalize_dsl(dsl: dict) -> dict:
        """规范化 DSL 结构"""
        return {
            "metrics": sorted(dsl.get("metrics", [])),
            "dimensions": sorted(dsl.get("dimensions", [])),
            "filters": sorted(
                [
                    {
                        "dimension": f.get("dimension", ""),
                        "operator": f.get("operator", ""),
                        "value": f.get("value"),
                    }
                    for f in dsl.get("filters", [])
                ],
                key=lambda x: x["dimension"],
            ),
            "having": sorted(
                [
                    {
                        "metric": h.get("metric", ""),
                        "operator": h.get("operator", ""),
                        "value": h.get("value"),
                    }
                    for h in dsl.get("having", [])
                ],
                key=lambda x: x["metric"],
            ),
        }

    def evaluate_exact_match(self, predicted_dsl: dict, ground_truth_dsl: dict) -> bool:
        """精确匹配评估"""
        return self.normalize_dsl(predicted_dsl) == self.normalize_dsl(ground_truth_dsl)

    def evaluate_component_match(
        self, predicted_dsl: dict, ground_truth_components: list[str]
    ) -> dict[str, Any]:
        """组件匹配评估"""
        predicted_components = set()

        if predicted_dsl.get("metrics"):
            predicted_components.add("METRIC")
        if predicted_dsl.get("dimensions"):
            predicted_components.add("DIMENSION")
        if predicted_dsl.get("filters"):
            predicted_components.add("FILTER")
        if predicted_dsl.get("having"):
            predicted_components.add("HAVING")

        expected_components = set(ground_truth_components)

        # 计算匹配的组件
        matched = predicted_components & expected_components
        missing = expected_components - predicted_components
        extra = predicted_components - expected_components

        # 计算匹配率
        if len(expected_components) == 0:
            match_rate = 1.0 if len(predicted_components) == 0 else 0.0
        else:
            match_rate = len(matched) / len(expected_components)

        return {
            "match_rate": match_rate,
            "matched": list(matched),
            "missing": list(missing),
            "extra": list(extra),
            "predicted_components": list(predicted_components),
            "expected_components": list(expected_components),
        }

    def calculate_field_accuracy(
        self, predicted_dsl: dict, ground_truth_dsl: dict
    ) -> dict[str, float]:
        """计算各字段准确率"""
        norm_predicted = self.normalize_dsl(predicted_dsl)
        norm_ground_truth = self.normalize_dsl(ground_truth_dsl)

        accuracy = {}

        # Metrics 准确率
        predicted_metrics = set(norm_predicted.get("metrics", []))
        ground_truth_metrics = set(norm_ground_truth.get("metrics", []))
        if ground_truth_metrics:
            accuracy["metrics"] = (
                len(predicted_metrics & ground_truth_metrics)
                / len(ground_truth_metrics)
            )
        else:
            accuracy["metrics"] = 1.0 if not predicted_metrics else 0.0

        # Dimensions 准确率
        predicted_dims = set(norm_predicted.get("dimensions", []))
        ground_truth_dims = set(norm_ground_truth.get("dimensions", []))
        if ground_truth_dims:
            accuracy["dimensions"] = (
                len(predicted_dims & ground_truth_dims) / len(ground_truth_dims)
            )
        else:
            accuracy["dimensions"] = 1.0 if not predicted_dims else 0.0

        # Filters 准确率（简化比较，只比较数量）
        predicted_filters = norm_predicted.get("filters", [])
        ground_truth_filters = norm_ground_truth.get("filters", [])
        if ground_truth_filters:
            accuracy["filters"] = (
                1.0 if predicted_filters == ground_truth_filters else 0.0
            )
        else:
            accuracy["filters"] = 1.0 if not predicted_filters else 0.0

        # Having 准确率
        predicted_having = norm_predicted.get("having", [])
        ground_truth_having = norm_ground_truth.get("having", [])
        if ground_truth_having:
            accuracy["having"] = (
                1.0 if predicted_having == ground_truth_having else 0.0
            )
        else:
            accuracy["having"] = 1.0 if not predicted_having else 0.0

        return accuracy

    def evaluate(
        self, predicted_dsl: dict, ground_truth: dict[str, Any]
    ) -> dict[str, Any]:
        """综合评估"""
        ground_truth_dsl = ground_truth.get("dsl", {})
        ground_truth_components = ground_truth.get("components", [])

        # 精确匹配
        exact_match = self.evaluate_exact_match(predicted_dsl, ground_truth_dsl)

        # 组件匹配
        component_result = self.evaluate_component_match(
            predicted_dsl, ground_truth_components
        )

        # 字段准确率
        field_accuracy = self.calculate_field_accuracy(predicted_dsl, ground_truth_dsl)

        # 综合准确率（各字段准确率的平均值）
        overall_accuracy = sum(field_accuracy.values()) / len(field_accuracy)

        return {
            "exact_match": exact_match,
            "component_match_rate": component_result["match_rate"],
            "overall_accuracy": overall_accuracy,
            "field_accuracy": field_accuracy,
            "component_details": component_result,
            "predicted_dsl": predicted_dsl,
            "ground_truth_dsl": ground_truth_dsl,
        }
