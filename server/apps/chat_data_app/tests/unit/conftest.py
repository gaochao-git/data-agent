"""单元测试共享 fixture。

模型注册表（CHAT_DATA_MODELS_FILE）采用 fail-fast 校验后，未显式配置的
测试需要一个默认注册表兜底；显式自建注册表的用例不受影响。
"""

import json

import pytest


_DEFAULT_MODELS = [
    {"id": "gpt-4o-mini", "temperature": 0.2, "context_window": 128000, "vlm": False,
     "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False}},
    {"id": "qwen3.5-35b-a3b", "temperature": 0.2, "context_window": 256000, "vlm": False,
     "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False}},
    {"id": "qwen3.6-35b-a3b", "temperature": 0.2, "context_window": 128000, "vlm": False,
     "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False}},
    {"id": "budget-model", "temperature": 0.2, "context_window": 100000, "vlm": False,
     "thinking": {"mode": "none"}},
    {"id": "glm-5.1", "temperature": 0.2, "context_window": 128000, "vlm": False,
     "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False}},
    {"id": "kimi-k2.5", "temperature": 0.1, "context_window": 256000, "vlm": False,
     "thinking": {"mode": "optional", "dialect": "openrouter", "default_on": False}},
    {"id": "stealth/ox-alpha", "temperature": 0.2, "context_window": 256000, "vlm": True,
     "thinking": {"mode": "forced", "dialect": "openrouter",
                  "efforts": ["low", "medium", "high"], "default_effort": "high"}},
]
# 各测试文件引用的其余模型 id（复刻旧"未知模型回退默认值"行为：128000/0.2/无视觉）
for _extra_id in ("qwen-test", "qwen", "m1", "primary-model", "summary-model",
                  "context_model", "model-id-field", "bad_model", "fake-model"):
    _DEFAULT_MODELS.append({
        "id": _extra_id,
        "temperature": 0.2,
        "context_window": 128000,
        "vlm": False,
        "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
    })


@pytest.fixture(autouse=True)
def _default_models_registry(monkeypatch, tmp_path_factory):
    """未配置模型注册表时提供默认清单；显式配置（含旧变量负例）的用例跳过。"""
    monkeypatch.delenv("CHAT_DATA_MODEL", raising=False)
    if str(__import__("os").getenv("CHAT_DATA_MODELS_FILE") or "").strip():
        yield
        return
    path = tmp_path_factory.mktemp("registry") / "default_models.json"
    path.write_text(json.dumps(_DEFAULT_MODELS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(path))
    yield
