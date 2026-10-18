"""llm_config 分支补测（模型注册表校验 + 思考能力方言序列化）。

覆盖目标：`services/llm_config.py` 中未被触达的校验分支（原 8 条缺失分支弧 + 7 行）。

思路：注册表加载没有缓存（每次调用重新读文件），因此用 monkeypatch 把
`CHAT_DATA_MODELS_FILE` 指向 tmp_path 里的自定义 JSON 就能驱动整条加载链路，
不需要真的改 conf/ 下的文件。

不可达分支说明：`_get_chat_data_extra_body` 的 `310->314`（方言既不是
chat_template / reasoning_effort / openrouter）在注册表校验通过的前提下不会发生 ——
`_parse_thinking` 第 123 行已把方言限制在这三个之内。这里直接构造
`ThinkingCapability` 触达该兜底路径，固化"未知方言不产生任何思考键"的行为。
"""

from __future__ import annotations

import json

import pytest

from apps.chat_data_app.services import llm_config


def _write_registry(tmp_path, monkeypatch, payload) -> str:
    """把任意注册表内容写到临时文件并指向它。"""
    path = tmp_path / "models.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(path))
    return str(path)


# ---------------------------------------------------------------------------
# _parse_thinking 入参校验（107->108 / 110->111）
# ---------------------------------------------------------------------------


def test_parse_thinking_rejects_non_dict():
    for bad in ("optional", None, ["mode"], 42):
        with pytest.raises(ValueError, match="应为对象"):
            llm_config._parse_thinking(bad, path="conf/models.json", model_id="m1")


def test_parse_thinking_rejects_unknown_fields():
    with pytest.raises(ValueError, match="未知字段"):
        llm_config._parse_thinking(
            {"mode": "none", "unknown_flag": True},
            path="conf/models.json",
            model_id="m1",
        )


def test_parse_thinking_accepts_all_declared_fields():
    capability = llm_config._parse_thinking(
        {"mode": "none"},
        path="conf/models.json",
        model_id="m1",
    )
    assert capability.mode == llm_config.THINKING_MODE_NONE
    assert capability.efforts == ()


# ---------------------------------------------------------------------------
# efforts 校验（132->133 / 135->136）
# ---------------------------------------------------------------------------


def test_parse_thinking_rejects_non_list_efforts():
    with pytest.raises(ValueError, match="efforts 应为数组"):
        llm_config._parse_thinking(
            {
                "mode": "optional",
                "dialect": "openrouter",
                "efforts": "high",
                "default_on": True,
                "default_effort": "high",
            },
            path="conf/models.json",
            model_id="m1",
        )


def test_parse_thinking_rejects_mixed_type_efforts_list():
    with pytest.raises(ValueError, match="档位应为字符串或"):
        llm_config._parse_thinking(
            {
                "mode": "optional",
                "dialect": "openrouter",
                "efforts": ["low", 1, None],
                "default_on": True,
                "default_effort": "low",
            },
            path="conf/models.json",
            model_id="m1",
        )


def test_parse_thinking_rejects_illegal_effort_level_name():
    """档位名自由但受字符集约束：自定义 ultra 合法，含空格/非法字符拒绝。"""
    with pytest.raises(ValueError, match="档位名"):
        llm_config._parse_thinking(
            {
                "mode": "optional",
                "dialect": "openrouter",
                "efforts": ["low", "bad name!"],
                "default_on": True,
                "default_effort": "low",
            },
            path="conf/models.json",
            model_id="m1",
        )


# ---------------------------------------------------------------------------
# efforts 与 default_effort 必须成对（144->145）
# ---------------------------------------------------------------------------


def test_parse_thinking_rejects_efforts_without_default_effort():
    with pytest.raises(ValueError, match="必须成对出现"):
        llm_config._parse_thinking(
            {
                "mode": "optional",
                "dialect": "openrouter",
                "efforts": ["low", "high"],
                "default_on": True,
            },
            path="conf/models.json",
            model_id="m1",
        )


def test_parse_thinking_rejects_default_effort_without_efforts():
    with pytest.raises(ValueError, match="必须成对出现"):
        llm_config._parse_thinking(
            {
                "mode": "optional",
                "dialect": "chat_template",
                "default_on": True,
                "default_effort": "high",
            },
            path="conf/models.json",
            model_id="m1",
        )


# ---------------------------------------------------------------------------
# _parse_model_entry / load_chat_data_model_configs（175->176 / 214->215）
# ---------------------------------------------------------------------------


def test_parse_model_entry_rejects_non_dict_entry():
    with pytest.raises(ValueError, match="存在非对象条目"):
        llm_config._parse_model_entry("not-a-dict", path="conf/models.json")

    with pytest.raises(ValueError, match="存在非对象条目"):
        llm_config._parse_model_entry(["id"], path="conf/models.json")


def test_load_model_configs_rejects_non_list_registry(tmp_path, monkeypatch):
    _write_registry(tmp_path, monkeypatch, {"id": "m1"})

    with pytest.raises(ValueError, match="应为非空数组"):
        llm_config.load_chat_data_model_configs()


def test_load_model_configs_rejects_empty_registry(tmp_path, monkeypatch):
    _write_registry(tmp_path, monkeypatch, [])

    with pytest.raises(ValueError, match="应为非空数组"):
        llm_config.load_chat_data_model_configs()


def test_load_model_configs_rejects_non_dict_member(tmp_path, monkeypatch):
    """数组里的非对象条目由 _parse_model_entry 拦截。"""
    _write_registry(tmp_path, monkeypatch, ["m1"])

    with pytest.raises(ValueError, match="存在非对象条目"):
        llm_config.load_chat_data_model_configs()


# ---------------------------------------------------------------------------
# _get_chat_data_extra_body：未知方言兜底（310->314）
# ---------------------------------------------------------------------------


def test_extra_body_is_empty_for_undeclared_dialect_when_on():
    capability = llm_config.ThinkingCapability(
        mode=llm_config.THINKING_MODE_OPTIONAL,
        dialect="some-future-dialect",
        efforts=(llm_config.EffortLevel(level="low"), llm_config.EffortLevel(level="high")),
        default_on=True,
        default_effort="low",
    )

    assert llm_config._get_chat_data_extra_body(capability) == {}


def test_extra_body_is_empty_for_undeclared_dialect_when_off():
    capability = llm_config.ThinkingCapability(
        mode=llm_config.THINKING_MODE_OPTIONAL,
        dialect="some-future-dialect",
        default_on=False,
    )

    assert llm_config._get_chat_data_extra_body(capability, enable_thinking=False) == {}
