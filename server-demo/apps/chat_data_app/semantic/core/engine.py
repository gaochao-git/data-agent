"""
语义引擎

提供语义层的统一入口。
"""

from typing import Dict, Optional, Any, List
import logging

from ..models import SemanticConfig
from .dsl import SemanticQuery, QueryResult, MatchResult, TranslateResult
from .nl2dsl import SemanticMatcher
from .dsl2sql import SQLTranslator
from .dsl_validator import SemanticValidator, ValidationResult

logger = logging.getLogger(__name__)


class SemanticEngine:
    """
    语义引擎 - 统一入口

    提供从自然语言到 SQL 的完整转换能力。

    Example:
        from semantic import SemanticEngine, SemanticConfig

        config = SemanticConfig.model_validate(config_dict)
        engine = SemanticEngine(config, dialect='mysql')

        # 自然语言查询
        result = engine.query("按产品类别统计销售额")
        print(result.sql)

        # 直接执行 DSL
        dsl = SemanticQuery(metrics=["销售额"], dimensions=["产品类别"])
        result = engine.execute_dsl(dsl)
        print(result.sql)
    """

    def __init__(
        self,
        config: SemanticConfig,
        dialect: str = "mysql",
        llm: Optional[Any] = None
    ):
        """
        初始化引擎

        Args:
            config: 语义配置
            dialect: SQL 方言名称 (mysql/hive/postgresql)
            llm: LLM 实例（可选，用于自然语言匹配）
        """
        self.config = config
        self.dialect_name = dialect

        # 初始化组件
        self.matcher = SemanticMatcher(config, llm)
        self.translator = SQLTranslator(config, dialect)
        self.validator = SemanticValidator(config)

        logger.info(f"语义引擎初始化完成: {config.name}, 方言: {dialect}")

    def set_llm(self, llm: Any) -> None:
        """设置 LLM 实例"""
        self.matcher.set_llm(llm)

    def query(self, nl_query: str) -> QueryResult:
        """
        自然语言查询入口

        Args:
            nl_query: 自然语言查询

        Returns:
            查询结果
        """
        logger.info(f"处理查询: {nl_query[:100]}...")

        try:
            # 1. NL → DSL
            match_result = self.matcher.match(nl_query)
            if not match_result.success:
                return QueryResult(
                    success=False,
                    error=match_result.error
                )

            # 2. 验证 DSL
            validation = self.validator.validate(match_result.dsl)
            if not validation.is_valid:
                error_messages = [e.message for e in validation.errors]
                return QueryResult(
                    success=False,
                    error="; ".join(error_messages),
                    dsl=match_result.dsl,
                    confidence=match_result.confidence
                )

            # 3. DSL → SQL
            translate_result = self.translator.translate(match_result.dsl)
            if not translate_result.success:
                return QueryResult(
                    success=False,
                    error=translate_result.error,
                    dsl=match_result.dsl,
                    confidence=match_result.confidence
                )

            return QueryResult(
                success=True,
                sql=translate_result.sql,
                dsl=match_result.dsl,
                explanation=translate_result.explanation,
                confidence=match_result.confidence,
                tables_used=translate_result.tables_used,
                summary=translate_result.summary
            )

        except Exception as e:
            logger.error(f"查询处理失败: {e}", exc_info=True)
            return QueryResult(
                success=False,
                error=str(e)
            )

    def execute_dsl(self, dsl: SemanticQuery) -> QueryResult:
        """
        直接执行 DSL（跳过 NL 解析）

        Args:
            dsl: 语义查询 DSL

        Returns:
            查询结果
        """
        logger.info(f"执行 DSL: metrics={dsl.metrics}")

        try:
            # 1. 验证 DSL
            validation = self.validator.validate(dsl)
            if not validation.is_valid:
                error_messages = [e.message for e in validation.errors]
                return QueryResult(
                    success=False,
                    error="; ".join(error_messages),
                    dsl=dsl
                )

            # 2. DSL → SQL
            translate_result = self.translator.translate(dsl)
            if not translate_result.success:
                return QueryResult(
                    success=False,
                    error=translate_result.error,
                    dsl=dsl
                )

            return QueryResult(
                success=True,
                sql=translate_result.sql,
                dsl=dsl,
                explanation=translate_result.explanation,
                confidence=1.0,  # DSL 直接执行，置信度为 1
                tables_used=translate_result.tables_used,
                summary=translate_result.summary
            )

        except Exception as e:
            logger.error(f"DSL 执行失败: {e}", exc_info=True)
            return QueryResult(
                success=False,
                error=str(e),
                dsl=dsl
            )

    def execute_dsl_dict(self, dsl_dict: Dict[str, Any]) -> QueryResult:
        """
        从字典执行 DSL

        Args:
            dsl_dict: DSL 字典

        Returns:
            查询结果
        """
        try:
            dsl = SemanticQuery.from_dict(dsl_dict)
            return self.execute_dsl(dsl)
        except Exception as e:
            logger.error(f"DSL 解析失败: {e}", exc_info=True)
            return QueryResult(
                success=False,
                error=f"DSL 格式错误: {e}"
            )

    def validate_dsl(self, dsl: SemanticQuery) -> ValidationResult:
        """
        验证 DSL

        Args:
            dsl: 语义查询

        Returns:
            验证结果
        """
        return self.validator.validate(dsl)

    def translate_only(self, dsl: SemanticQuery) -> TranslateResult:
        """
        仅翻译 DSL（不验证）

        Args:
            dsl: 语义查询

        Returns:
            翻译结果
        """
        return self.translator.translate(dsl)

    def match_only(self, nl_query: str) -> MatchResult:
        """
        仅匹配自然语言（不翻译）

        Args:
            nl_query: 自然语言查询

        Returns:
            匹配结果
        """
        return self.matcher.match(nl_query)

    def get_available_metrics(self) -> List[str]:
        """获取可用的指标列表"""
        return [m.name for m in self.config.metrics]

    def get_available_dimensions(self) -> List[str]:
        """获取可用的维度列表"""
        return [d.name for d in self.config.dimensions]

    def get_metric_info(self, name: str) -> Optional[Dict]:
        """
        获取指标信息

        Args:
            name: 指标名称

        Returns:
            指标信息字典
        """
        for m in self.config.metrics:
            if m.name == name:
                return m.model_dump()
        return None

    def get_dimension_info(self, name: str) -> Optional[Dict]:
        """
        获取维度信息

        Args:
            name: 维度名称

        Returns:
            维度信息字典
        """
        for d in self.config.dimensions:
            if d.name == name:
                return d.model_dump()
        return None

    def get_schema_summary(self) -> Dict:
        """
        获取 Schema 摘要

        Returns:
            Schema 摘要信息
        """
        tables = set()
        for m in self.config.metrics:
            tables.add(m.table)
        for d in self.config.dimensions:
            tables.add(d.table)

        return {
            'config_name': self.config.name,
            'config_id': self.config.id,
            'dialect': self.dialect_name,
            'metrics_count': len(self.config.metrics),
            'dimensions_count': len(self.config.dimensions),
            'relationships_count': len(self.config.relationships),
            'tables': list(tables)
        }

    def suggest(self, query: str) -> Dict:
        """
        根据查询提供建议

        Args:
            query: 查询文本

        Returns:
            建议信息
        """
        return {
            'metrics': self.matcher.suggest_metrics(query),
            'dimensions': self.matcher.suggest_dimensions(query)
        }


def create_engine(
    config: SemanticConfig,
    dialect: str = "mysql",
    llm: Optional[Any] = None
) -> SemanticEngine:
    """
    创建语义引擎（工厂函数）

    Args:
        config: 语义配置
        dialect: SQL 方言
        llm: LLM 实例

    Returns:
        语义引擎实例
    """
    return SemanticEngine(config, dialect, llm)


def create_engine_from_dict(
    config_dict: Dict,
    dialect: str = "mysql",
    llm: Optional[Any] = None
) -> SemanticEngine:
    """
    从字典创建语义引擎

    Args:
        config_dict: 配置字典
        dialect: SQL 方言
        llm: LLM 实例

    Returns:
        语义引擎实例
    """
    config = SemanticConfig.model_validate(config_dict)
    return SemanticEngine(config, dialect, llm)
