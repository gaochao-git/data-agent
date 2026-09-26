import pytest

from utils.llm_json import JSONParseError, parse_llm_json


def test_parse_llm_json_handles_supported_formats():
    assert parse_llm_json('{"metrics":["订单数"]}') == {"metrics": ["订单数"]}
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json("说明：{'a': 1,}") == {"a": 1}
    assert parse_llm_json("说明 {a: 1, b: 2}") == {"a": 1, "b": 2}


def test_parse_llm_json_lenient_mode_returns_empty_dict_on_failure():
    """显式宽松口径：解析失败静默回退空 dict（后台任务/可选生成类功能使用）。"""
    assert parse_llm_json("不是 json", strict=False) == {}
    assert parse_llm_json("", strict=False) == {}
    assert parse_llm_json("```json\n{bad}\n```", strict=False) == {}


def test_parse_llm_json_strict_mode_raises_on_invalid_content():
    with pytest.raises(JSONParseError):
        parse_llm_json("")
    with pytest.raises(JSONParseError):
        parse_llm_json("not json")
    with pytest.raises(JSONParseError):
        parse_llm_json("```json\n{bad}\n```")
