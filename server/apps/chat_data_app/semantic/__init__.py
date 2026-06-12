"""ChatData semantic package.

当前语义层只保留一条主链路：
DSL 输入 -> semantic.compiler -> semantic.engine 方言/过滤/JOIN -> SQL。
同比/环比不属于 DSL2SQL 能力；请拆成基础 DSL 查询，复杂同比/环比使用智能分析。
"""

__version__ = "1.0.0"

# 数据模型
from .models import (
    SemanticConfig,
    SemanticMetric,
    SemanticDimension,
    SemanticRelationship,
    AggregationType,
    DataType,
    DimensionType,
    TimeGrain,
    RelationshipType,
    JoinType,
)

# 当前 DSL2SQL 编译器
from .compiler import SemanticSQLGenerator, compile_builder_sql
from .validators import InputValidator, SemanticValidator

# SQL 引擎
from .engine import (
    SQLDialect,
    MySQLDialect,
    HiveDialect,
    PostgreSQLDialect,
    DialectRegistry,
    get_dialect,
    JoinBuilder,
    FilterBuilder,
    FilterValidationError,
)

# 工具函数
from .utils import (
    parse_llm_json,
    JSONParseError,
    TreeVisitor,
    TreeDepthExceededError,
    format_params_for_display,
)

# Prompt 模板
from .prompts import (
    build_intent_check_prompt,
    build_sql_check_prompt,
    ValidatePromptBuilder,
)

__all__ = [
    # 版本
    "__version__",
    # 数据模型
    "SemanticConfig",
    "SemanticMetric",
    "SemanticDimension",
    "SemanticRelationship",
    "AggregationType",
    "DataType",
    "DimensionType",
    "TimeGrain",
    "RelationshipType",
    "JoinType",
    # DSL2SQL 编译器
    "SemanticSQLGenerator",
    "compile_builder_sql",
    # 输入验证
    "InputValidator",
    "SemanticValidator",
    # SQL 引擎
    "SQLDialect",
    "MySQLDialect",
    "HiveDialect",
    "PostgreSQLDialect",
    "DialectRegistry",
    "get_dialect",
    "JoinBuilder",
    "FilterBuilder",
    "FilterValidationError",
    # 工具函数
    "parse_llm_json",
    "JSONParseError",
    "TreeVisitor",
    "TreeDepthExceededError",
    "format_params_for_display",
    # Prompt 模板
    "build_intent_check_prompt",
    "build_sql_check_prompt",
    "ValidatePromptBuilder",
]
