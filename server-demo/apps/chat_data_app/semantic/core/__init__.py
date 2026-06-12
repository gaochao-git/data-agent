"""
核心处理层

提供语义层的核心处理逻辑。

模块结构：
- dsl.py: DSL 数据结构定义
- engine.py: 统一入口 (SemanticEngine)
- nl2dsl.py: NL → DSL 自然语言匹配
- dsl2sql.py: DSL → SQL 翻译
- dsl_builder.py: DSL 构建器
- param_validator.py: 参数验证
- dsl_validator.py: DSL 语义验证
- semantic_selector.py: 语义配置选择
"""

from .dsl import (
    SemanticQuery,
    QueryMode,
    DimensionSpec,
    FilterCondition,
    OrderBySpec,
    MatchResult,
    TranslateResult,
    QueryResult,
)
from .nl2dsl import SemanticMatcher
from .dsl2sql import SQLTranslator
from .dsl_validator import (
    SemanticValidator,
    ValidationResult,
    ValidationError,
)
from .engine import (
    SemanticEngine,
    create_engine,
    create_engine_from_dict,
)
from .param_validator import InputValidator
from .dsl_builder import DSLGenerator, LegacyMatchResult
from .semantic_selector import SemanticConfigSelector

__all__ = [
    # DSL 定义
    "SemanticQuery",
    "QueryMode",
    "DimensionSpec",
    "FilterCondition",
    "OrderBySpec",
    "MatchResult",
    "TranslateResult",
    "QueryResult",
    # NL → DSL
    "SemanticMatcher",
    # DSL → SQL
    "SQLTranslator",
    # 验证器
    "SemanticValidator",
    "ValidationResult",
    "ValidationError",
    "InputValidator",
    # 统一入口
    "SemanticEngine",
    "create_engine",
    "create_engine_from_dict",
    # DSL 生成
    "LegacyMatchResult",
    "DSLGenerator",
    "SemanticConfigSelector",
]
