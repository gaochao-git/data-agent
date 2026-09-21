import pytest

from utils.llm_json import JSONParseError, parse_llm_json


def test_parse_llm_json_handles_supported_formats():
    assert parse_llm_json('{"metrics":["订单数"]}') == {"metrics": ["订单数"]}
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json("说明：{'a': 1,}") == {"a": 1}
    assert parse_llm_json("说明 {a: 1, b: 2}") == {"a": 1, "b": 2}
    assert parse_llm_json("不是 json") == {}


def test_parse_llm_json_strict_mode_raises_on_invalid_content():
    assert parse_llm_json("```json\n{bad}\n```") == {}
    with pytest.raises(JSONParseError):
        parse_llm_json("", strict=True)
    with pytest.raises(JSONParseError):
        parse_llm_json("not json", strict=True)
