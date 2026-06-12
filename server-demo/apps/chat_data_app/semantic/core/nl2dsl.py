"""
语义匹配器

将自然语言查询转换为 DSL。
"""

from typing import List, Dict, Set, Optional, Any, Callable
import logging

from ..models import SemanticConfig, SemanticDimension, SemanticMetric
from ..utils import parse_llm_json, JSONParseError
from .dsl import SemanticQuery, MatchResult, DimensionSpec, QueryMode

logger = logging.getLogger(__name__)

FILTER_VALUE_TYPES = {"literal", "sql_function"}


class SemanticMatcher:
    """
    语义匹配器

    使用 LLM 将自然语言查询转换为结构化的 DSL。

    Example:
        matcher = SemanticMatcher(config, llm=my_llm)
        result = matcher.match("按产品类别统计销售额")
        if result.success:
            print(result.dsl)
    """

    def __init__(
        self,
        config: SemanticConfig,
        llm: Optional[Any] = None,
        prompt_builder: Optional[Callable] = None
    ):
        """
        初始化匹配器

        Args:
            config: 语义配置
            llm: LLM 实例（可选，支持延迟注入）
            prompt_builder: 自定义 Prompt 构建器（可选）
        """
        self.config = config
        self._llm = llm
        self._prompt_builder = prompt_builder

        # 构建索引
        self._metrics_index = {m.name: m for m in config.metrics}
        self._dimensions_index = {d.name: d for d in config.dimensions}

        # 构建相关性映射（用于快速查找）
        self._metrics_by_table: Dict[str, List[SemanticMetric]] = {}
        self._dimensions_by_table: Dict[str, List[SemanticDimension]] = {}
        self._build_table_mappings()

    def _build_table_mappings(self) -> None:
        """构建表到字段的映射"""
        for metric in self.config.metrics:
            if metric.table not in self._metrics_by_table:
                self._metrics_by_table[metric.table] = []
            self._metrics_by_table[metric.table].append(metric)

        for dim in self.config.dimensions:
            if dim.table not in self._dimensions_by_table:
                self._dimensions_by_table[dim.table] = []
            self._dimensions_by_table[dim.table].append(dim)

    @staticmethod
    def _has_detail_intent(query: str) -> bool:
        """识别用户是否明确表达明细/列表查询意图。"""
        text = (query or "").strip().lower()
        if not text:
            return False
        keywords = ("明细", "详情", "详细", "列表", "list", "detail", "drill", "下钻")
        return any(keyword in text for keyword in keywords)

    def set_llm(self, llm: Any) -> None:
        """设置 LLM 实例"""
        self._llm = llm

    def set_prompt_builder(self, builder: Callable) -> None:
        """设置 Prompt 构建器"""
        self._prompt_builder = builder

    def match(self, query: str) -> MatchResult:
        """
        匹配自然语言查询

        Args:
            query: 自然语言查询

        Returns:
            匹配结果
        """
        if not self._llm:
            return MatchResult(
                success=False,
                error="未配置 LLM，无法进行语义匹配"
            )

        try:
            # 1. 构建 Prompt
            prompt = self._build_prompt(query)

            # 2. 调用 LLM
            response = self._llm.invoke(prompt)
            content = response.content if hasattr(response, 'content') else str(response)

            # 3. 解析响应
            parsed = parse_llm_json(content)

            # 4. 构建 DSL
            dsl = self._build_dsl_from_response(parsed, query)

            # 5. 计算置信度
            confidence = self._calculate_confidence(parsed, dsl)

            return MatchResult(
                success=True,
                dsl=dsl,
                confidence=confidence,
                raw_response=parsed
            )

        except JSONParseError as e:
            logger.error(f"解析 LLM 响应失败: {e}")
            return MatchResult(
                success=False,
                error=f"无法解析 LLM 响应: {e}"
            )
        except Exception as e:
            logger.error(f"语义匹配失败: {e}", exc_info=True)
            return MatchResult(
                success=False,
                error=str(e)
            )

    def match_with_prompt(
        self,
        query: str,
        custom_prompt: str
    ) -> MatchResult:
        """
        使用自定义 Prompt 进行匹配

        Args:
            query: 自然语言查询
            custom_prompt: 自定义 Prompt

        Returns:
            匹配结果
        """
        if not self._llm:
            return MatchResult(
                success=False,
                error="未配置 LLM，无法进行语义匹配"
            )

        try:
            # 调用 LLM
            response = self._llm.invoke(custom_prompt)
            content = response.content if hasattr(response, 'content') else str(response)

            # 解析响应
            parsed = parse_llm_json(content)

            # 构建 DSL
            dsl = self._build_dsl_from_response(parsed, query)

            # 计算置信度
            confidence = self._calculate_confidence(parsed, dsl)

            return MatchResult(
                success=True,
                dsl=dsl,
                confidence=confidence,
                raw_response=parsed
            )

        except Exception as e:
            logger.error(f"语义匹配失败: {e}", exc_info=True)
            return MatchResult(
                success=False,
                error=str(e)
            )

    def _build_prompt(self, query: str) -> str:
        """构建 Prompt"""
        if self._prompt_builder:
            return self._prompt_builder(query, self.config)

        # 使用默认 Prompt
        return self._build_default_prompt(query)

    def _build_default_prompt(self, query: str) -> str:
        """构建默认 Prompt"""
        # 构建指标描述
        metrics_desc = "\n".join([
            f"  - {m.name}: {m.description or m.expression}"
            for m in self.config.metrics
        ])

        # 构建维度描述
        dimensions_desc = "\n".join([
            f"  - {d.name}: {d.description or d.column}"
            + (f" (时间类型，支持粒度: year/quarter/month/week/day/hour)"
               if d.type.value == 'time' else "")
            for d in self.config.dimensions
        ])

        prompt = f"""你是一个数据分析助手，负责将用户的自然语言查询转换为结构化的查询请求。

## 可用的指标：
{metrics_desc}

## 可用的维度：
{dimensions_desc}

## 用户查询：
{query}

## 任务：
请先判断查询属于“明细查询”还是“聚合分析查询”，再返回一个 JSON 对象，包含：
- metrics: 要输出的字段名称列表
  - 聚合分析查询：填写可用指标
  - 明细查询：也可填写可用维度/原始字段
- dimensions: 分组维度列表（可选，仅用于 GROUP BY，不承担明细展示职责）
  - 如果是时间维度，可以指定粒度，格式为 {{"name": "维度名", "time_grain": "粒度"}}
- filters: 过滤条件列表（可选），每个条件包含 dimension/operator/value（可选 value_type: literal/sql_function）
- having: 聚合后过滤条件（可选），每个条件包含 metric/operator/value
- order_by: 排序条件（可选）
- limit: 返回数量限制（可选）

## 注意事项：
1. 只使用上面列出的指标和维度
2. 明细查询时，优先把原始字段放入 metrics，不要误放到 dimensions
3. dimensions 的职责是分组，不是明细展示字段
4. 时间粒度只支持：year, quarter, month, week, day, hour
5. 过滤条件支持的操作符：=, !=, >, >=, <, <=, IN, NOT IN, LIKE, BETWEEN
6. 如果查询提到"前N个"或"TopN"，请设置 limit 和相应的排序

## 返回格式：
```json
{{
  "metrics": ["指标1", "指标2"],
  "dimensions": ["维度1", {{"name": "时间维度", "time_grain": "month"}}],
  "filters": [{{"dimension": "维度名", "operator": "=", "value": "值", "value_type": "literal"}}],
  "order_by": [{{"field": "字段名", "direction": "DESC"}}],
  "limit": 10
}}
```

请只返回 JSON，不要包含其他内容。"""

        return prompt

    def _infer_query_mode(self, query: str, response: Dict, valid_metrics: List[str], raw_field_metrics: List[str], valid_dimensions: List[DimensionSpec]) -> QueryMode:
        """根据查询文本和解析结果推断明细/聚合模式。"""
        requested_mode = str(response.get("mode") or "").strip().lower()
        if requested_mode == QueryMode.DETAIL.value:
            return QueryMode.DETAIL
        if requested_mode == QueryMode.AGGREGATE.value:
            return QueryMode.AGGREGATE
        if self._has_detail_intent(query):
            return QueryMode.DETAIL
        if valid_dimensions or response.get("having"):
            return QueryMode.AGGREGATE
        has_aggregate_metric = any(metric_name in self._metrics_index for metric_name in valid_metrics)
        if raw_field_metrics and not has_aggregate_metric:
            return QueryMode.DETAIL
        return QueryMode.AGGREGATE

    def _build_dsl_from_response(self, response: Dict, query: str = "") -> SemanticQuery:
        """从 LLM 响应构建 DSL"""
        # 提取指标
        metrics = response.get('metrics', [])
        if not metrics:
            raise ValueError("未识别到任何指标")

        # 验证指标
        valid_metrics = []
        raw_field_metrics = []
        for m in metrics:
            if m in self._metrics_index:
                valid_metrics.append(m)
            elif m in self._dimensions_index:
                valid_metrics.append(m)
                raw_field_metrics.append(m)
            else:
                logger.warning(f"忽略未知指标或字段: {m}")

        if not valid_metrics:
            raise ValueError("所有指标或原始字段都无效")

        # 提取维度
        dimensions = response.get('dimensions', [])
        valid_dimensions = []
        for d in dimensions:
            dim_name = d.get('name', d) if isinstance(d, dict) else d
            if dim_name in self._dimensions_index:
                time_grain = d.get('time_grain', d.get('grain')) if isinstance(d, dict) else None
                if time_grain:
                    valid_dimensions.append(DimensionSpec(
                        name=dim_name,
                        grain=time_grain
                    ))
                else:
                    valid_dimensions.append(DimensionSpec(name=dim_name))
            else:
                logger.warning(f"忽略未知维度: {dim_name}")

        # 提取过滤条件
        filters = self._normalize_filters(response.get('filters', []))

        # 提取 HAVING 条件
        having = self._normalize_having(response.get('having', []))

        # 提取排序
        order_by = response.get('order_by', [])

        # 提取分页
        limit = response.get('limit')
        offset = response.get('offset')
        mode = self._infer_query_mode(query, response, valid_metrics, raw_field_metrics, valid_dimensions)

        return SemanticQuery(
            metrics=valid_metrics,
            dimensions=valid_dimensions,
            filters=filters,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
            mode=mode,
        )

    def _normalize_filters(self, filters: List[Dict]) -> List[Dict]:
        """规范化过滤条件"""
        normalized = []
        for f in filters:
            # 处理树形结构
            if 'and' in f or 'or' in f:
                normalized.append(f)
                continue

            # 处理叶子节点
            dim_name = f.get('dimension')
            if not dim_name:
                continue

            # 验证维度
            if dim_name not in self._dimensions_index:
                logger.warning(f"过滤条件使用了未知维度: {dim_name}")
                continue

            # 应用同义词映射
            dim = self._dimensions_index[dim_name]
            value = f.get('value')
            value_type = self._normalize_value_type(f.get('value_type'))
            if isinstance(value, str) and dim.mapping and not self._contains_sql_function_type(value_type):
                value = dim.normalize_value(value)

            item = {
                'dimension': dim_name,
                'operator': f.get('operator', '='),
                'value': value
            }
            if value_type:
                item['value_type'] = value_type
            normalized.append(item)

        return normalized

    def _normalize_value_type(self, value_type: Any) -> Optional[Any]:
        """规范化过滤值类型，仅支持 literal/sql_function（字符串或数组）。"""
        if isinstance(value_type, str):
            normalized = value_type.strip().lower()
            if normalized in FILTER_VALUE_TYPES:
                return normalized
            return None

        if isinstance(value_type, list):
            normalized_list: List[str] = []
            for item in value_type:
                if not isinstance(item, str):
                    return None
                normalized_item = item.strip().lower()
                if normalized_item not in FILTER_VALUE_TYPES:
                    return None
                normalized_list.append(normalized_item)
            return normalized_list or None

        return None

    def _contains_sql_function_type(self, value_type: Any) -> bool:
        """判断 value_type 是否包含 sql_function。"""
        if value_type == "sql_function":
            return True
        if isinstance(value_type, list):
            return "sql_function" in value_type
        return False

    def _normalize_having(self, having: List[Dict]) -> List[Dict]:
        """规范化 HAVING 条件"""
        normalized = []
        for h in having:
            # 处理树形结构
            if 'and' in h or 'or' in h:
                normalized.append(h)
                continue

            # 处理叶子节点
            metric_name = h.get('metric')
            if not metric_name:
                continue

            # 验证指标
            if metric_name not in self._metrics_index:
                logger.warning(f"HAVING 条件使用了未知指标: {metric_name}")
                continue

            normalized.append({
                'metric': metric_name,
                'operator': h.get('operator', '='),
                'value': h.get('value')
            })

        return normalized

    def _calculate_confidence(
        self,
        response: Dict,
        dsl: SemanticQuery
    ) -> float:
        """计算匹配置信度"""
        # 基础置信度
        confidence = 0.5

        # 有有效指标加分
        if dsl.metrics:
            confidence += 0.2

        # 有有效维度加分
        if dsl.dimensions:
            confidence += 0.1

        # LLM 返回了置信度
        if 'confidence' in response:
            try:
                llm_confidence = float(response['confidence'])
                confidence = (confidence + llm_confidence) / 2
            except (ValueError, TypeError):
                pass

        return min(confidence, 1.0)

    def get_schema_summary(self) -> str:
        """获取 Schema 摘要（用于 Prompt）"""
        metrics_count = len(self.config.metrics)
        dimensions_count = len(self.config.dimensions)
        tables = set()

        for m in self.config.metrics:
            tables.add(m.table)
        for d in self.config.dimensions:
            tables.add(d.table)

        return (
            f"语义配置: {self.config.name}\n"
            f"- 指标: {metrics_count} 个\n"
            f"- 维度: {dimensions_count} 个\n"
            f"- 涉及表: {len(tables)} 个 ({', '.join(tables)})"
        )

    def suggest_metrics(self, query: str) -> List[str]:
        """
        根据查询建议相关指标

        Args:
            query: 查询文本

        Returns:
            建议的指标名称列表
        """
        suggestions = []
        query_lower = query.lower()

        for metric in self.config.metrics:
            # 检查名称匹配
            if metric.name.lower() in query_lower:
                suggestions.append(metric.name)
                continue

            # 检查描述匹配
            if metric.description and metric.description.lower() in query_lower:
                suggestions.append(metric.name)

        return suggestions

    def suggest_dimensions(self, query: str) -> List[str]:
        """
        根据查询建议相关维度

        Args:
            query: 查询文本

        Returns:
            建议的维度名称列表
        """
        suggestions = []
        query_lower = query.lower()

        for dim in self.config.dimensions:
            # 检查名称匹配
            if dim.name.lower() in query_lower:
                suggestions.append(dim.name)
                continue

            # 检查描述匹配
            if dim.description and dim.description.lower() in query_lower:
                suggestions.append(dim.name)

        return suggestions
