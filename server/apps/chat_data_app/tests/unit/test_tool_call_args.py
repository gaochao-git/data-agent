"""utils.tool_call_args.coerce_tool_call_args 单测。

背景：内网 Qwen3.6 经 vLLM 返回的 tool call args 是 JSON 字符串甚至双层编码
字符串，工具执行 / 直播 / 回放三处都要求 dict，规整逻辑统一收口本模块。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from utils.tool_call_args import coerce_tool_call_args


def test_dict_passthrough_returns_same_object():
    value = {"a": 1}

    assert coerce_tool_call_args(value) is value


def test_string_and_double_encoded_forms():
    assert coerce_tool_call_args('{"a": 1}') == {"a": 1}
    assert coerce_tool_call_args('  {"a": 1}  ') == {"a": 1}
    assert coerce_tool_call_args(json.dumps(json.dumps({"a": 1}))) == {"a": 1}


def test_unparseable_or_non_dict_returns_none():
    assert coerce_tool_call_args("raw-not-json") is None
    assert coerce_tool_call_args("{bad json") is None
    assert coerce_tool_call_args('["not_dict"]') is None
    assert coerce_tool_call_args("42") is None
    assert coerce_tool_call_args('"{\\"a\\": 1}"x') is None  # 双层但外层后缀非法
    assert coerce_tool_call_args("") is None
    assert coerce_tool_call_args(None) is None
    assert coerce_tool_call_args(42) is None


def test_triple_encoded_exceeds_max_unwrap_layers_returns_none():
    triple = json.dumps(json.dumps(json.dumps({"a": 1})))

    assert coerce_tool_call_args(triple) is None
    assert coerce_tool_call_args(triple, max_unwrap_layers=3) == {"a": 1}
