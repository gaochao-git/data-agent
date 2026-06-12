import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.semantic.models import SemanticConfig
from apps.chat_data_app.semantic.prompts.validate_prompt import (
    ValidatePromptBuilder,
    build_ambiguity_check_prompt,
    build_intent_check_prompt,
    build_sql_check_prompt,
)


def _config() -> SemanticConfig:
    return SemanticConfig.from_dict({
        "id": "cfg_orders",
        "name": "订单分析",
        "metrics": [{"name": "销售额", "expression": "SUM(amount)", "table": "orders"}],
        "dimensions": [{"name": "城市", "column": "city", "table": "orders", "type": "categorical"}],
        "relationships": [],
    })


def test_validate_prompt_builder_renders_intent_sql_and_ambiguity_prompts():
    builder = ValidatePromptBuilder(_config())

    intent_prompt = builder.build_intent_check("按城市查销售额", {"metrics": ["销售额"], "dimensions": ["城市"]})
    sql_prompt = builder.build_sql_check("按城市查销售额", "select city, sum(amount) from orders group by city")
    ambiguity_prompt = builder.build_ambiguity_check("查销售额")

    assert "按城市查销售额" in intent_prompt
    assert "销售额" in intent_prompt
    assert "城市" in intent_prompt
    assert "{'metrics': ['销售额'], 'dimensions': ['城市']}" in intent_prompt
    assert "select city, sum(amount)" in sql_prompt
    assert "查销售额" in ambiguity_prompt
    assert "clarification_questions" in ambiguity_prompt


def test_validate_prompt_convenience_functions_delegate_to_builder():
    config = _config()

    assert "DSL" in build_intent_check_prompt("查销售额", {"metrics": ["销售额"]}, config)
    assert "生成的 SQL" in build_sql_check_prompt("查销售额", "select 1", config)
    assert "是否存在歧义" in build_ambiguity_check_prompt("查销售额", config)
