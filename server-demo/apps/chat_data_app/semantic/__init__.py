"""
Semantic Layer - 语义层核心模块

提供从自然语言到 SQL 的语义转换能力。

## 功能特性

- **语义模型定义**：支持指标、维度、关系的完整定义
- **多 SQL 方言**：支持 MySQL、Hive、PostgreSQL
- **自然语言理解**：通过 LLM 将自然语言转换为 DSL
- **树形过滤**：支持 AND/OR 嵌套的复杂过滤条件
- **时间粒度**：支持 year/quarter/month/week/day/hour 时间聚合
- **HAVING 支持**：区分 WHERE 和 HAVING 条件

## 快速开始

```python
from semantic import SemanticEngine, SemanticConfig

# 1. 加载配置
config = SemanticConfig.model_validate({
    "id": "sales_analysis",
    "name": "销售分析",
    "metrics": [
        {"name": "销售额", "expression": "SUM(amount)", "table": "orders"}
    ],
    "dimensions": [
        {"name": "产品类别", "column": "category", "table": "products", "type": "categorical"}
    ],
    "relationships": [
        {"left_table": "orders", "right_table": "products",
         "left_column": "product_id", "right_column": "id",
         "relationship_type": "many_to_one"}
    ]
})

# 2. 创建引擎
engine = SemanticEngine(config, dialect='mysql', llm=my_llm)

# 3. 自然语言查询
result = engine.query("按产品类别统计销售额")
print(result.sql)

# 4. 直接执行 DSL
from semantic import SemanticQuery
dsl = SemanticQuery(
    metrics=["销售额"],
    dimensions=["产品类别"]
)
result = engine.execute_dsl(dsl)
print(result.sql)
```

## 模块结构

```
semantic/
├── models/      # 数据模型（Metric, Dimension, Relationship, Config）
├── core/        # 核心逻辑（Matcher, Translator, Validator, Engine）
├── engine/      # SQL 引擎（Dialect, JoinBuilder, FilterBuilder）
├── utils/       # 工具函数（JSON 解析, 树形访问器）
└── prompts/     # Prompt 模板
```

## 版本
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

# 核心组件
from .core import (
    SemanticEngine,
    SemanticQuery,
    SemanticMatcher,
    SQLTranslator,
    SemanticValidator,
    QueryMode,
    DimensionSpec,
    MatchResult,
    TranslateResult,
    QueryResult,
    ValidationResult,
    create_engine,
    create_engine_from_dict,
    # 输入验证
    InputValidator,
    # DSL 生成
    LegacyMatchResult,
    DSLGenerator,
    SemanticConfigSelector,
)

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
    build_match_prompt,
    build_concise_match_prompt,
    build_intent_check_prompt,
    build_sql_check_prompt,
    MatchPromptBuilder,
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
    # 核心组件
    "SemanticEngine",
    "SemanticQuery",
    "SemanticMatcher",
    "SQLTranslator",
    "SemanticValidator",
    "QueryMode",
    "DimensionSpec",
    "MatchResult",
    "TranslateResult",
    "QueryResult",
    "ValidationResult",
    "create_engine",
    "create_engine_from_dict",
    # 输入验证
    "InputValidator",
    # DSL 生成
    "LegacyMatchResult",
    "DSLGenerator",
    "SemanticConfigSelector",
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
    "build_match_prompt",
    "build_concise_match_prompt",
    "build_intent_check_prompt",
    "build_sql_check_prompt",
    "MatchPromptBuilder",
    "ValidatePromptBuilder",
]
