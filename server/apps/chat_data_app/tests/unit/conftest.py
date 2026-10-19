"""单元测试共享 fixture。

模型注册表（CHAT_DATA_MODELS_FILE）采用 fail-fast 校验后，未显式配置的
测试需要一个默认注册表兜底；显式自建注册表的用例不受影响。
服务路径（事件循环内）冷缓存会拒绝回落文件（DB 唯一真值），因此默认
注册表夹具同时把缓存预热落位，模拟生产环境启动预热成功后的状态。
"""

import json
import time

import pytest

_DEFAULT_MODELS = [
    {
        "id": "gpt-4o-mini",
        "temperature": 0.2,
        "context_window": 128000,
        "vlm": False,
        "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
    },
    {
        "id": "qwen3.5-35b-a3b",
        "temperature": 0.2,
        "context_window": 256000,
        "vlm": False,
        "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
    },
    {
        "id": "qwen3.6-35b-a3b",
        "temperature": 0.2,
        "context_window": 128000,
        "vlm": False,
        "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
    },
    {"id": "budget-model", "temperature": 0.2, "context_window": 100000, "vlm": False, "thinking": {"mode": "none"}},
    {
        "id": "glm-5.1",
        "temperature": 0.2,
        "context_window": 128000,
        "vlm": False,
        "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
    },
    {
        "id": "kimi-k2.5",
        "temperature": 0.1,
        "context_window": 256000,
        "vlm": False,
        "thinking": {"mode": "optional", "dialect": "openrouter", "default_on": False},
    },
    {
        "id": "stealth/ox-alpha",
        "temperature": 0.2,
        "context_window": 256000,
        "vlm": True,
        "thinking": {
            "mode": "forced",
            "dialect": "openrouter",
            "efforts": ["low", "medium", "high"],
            "default_effort": "high",
        },
    },
]
# 各测试文件引用的其余模型 id（复刻旧"未知模型回退默认值"行为：128000/0.2/无视觉）
for _extra_id in (
    "qwen-test",
    "qwen",
    "m1",
    "primary-model",
    "summary-model",
    "context_model",
    "model-id-field",
    "bad_model",
    "fake-model",
):
    _DEFAULT_MODELS.append(
        {
            "id": _extra_id,
            "temperature": 0.2,
            "context_window": 128000,
            "vlm": False,
            "thinking": {"mode": "optional", "dialect": "chat_template", "default_on": False},
        }
    )


@pytest.fixture(autouse=True)
def _default_models_registry(monkeypatch, tmp_path_factory):
    """未配置模型注册表时提供默认清单并预热缓存；显式配置（含旧变量负例）的用例跳过。"""
    monkeypatch.delenv("CHAT_DATA_MODEL", raising=False)
    if str(__import__("os").getenv("CHAT_DATA_MODELS_FILE") or "").strip():
        yield
        return
    from apps.chat_data_app.services import llm_config

    path = tmp_path_factory.mktemp("registry") / "default_models.json"
    path.write_text(json.dumps(_DEFAULT_MODELS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(path))
    # 模拟启动预热成功：缓存落位并锚定来源（monkeypatch 自动还原，避免跨测试污染）
    monkeypatch.setattr(llm_config, "_registry_cache", llm_config.load_chat_data_model_configs())
    monkeypatch.setattr(llm_config, "_registry_cache_loaded_at", time.monotonic())
    monkeypatch.setattr(llm_config, "_registry_cache_origin", str(path))
    monkeypatch.setattr(llm_config, "_registry_refresh_task", None)
    yield


@pytest.fixture(autouse=True)
def _no_db_workspace_date(monkeypatch):
    """单元测试不连真实 DB：把工作区日期解析的消费方打桩为回退当前日期。

    仅覆盖 agent_run_support / 路由 / 上传服务三个导入方命名空间；定义模块
    ``conversation_workspace`` 内部直调（如 async wrapper 委托测试）与
    日期解析本身的专项测试不受影响，由各用例自行控制。
    """
    import apps.chat_data_app.agent_run_support as agent_run_support_module
    from apps.chat_data_app.routers import conversation_workspace as cw_routes
    from apps.chat_data_app.services.agent_run import uploaded_file_service

    async def _stub_date(conversation_id, *, user_id):
        return None

    monkeypatch.setattr(agent_run_support_module, "get_conversation_created_date", _stub_date)
    monkeypatch.setattr(cw_routes, "get_conversation_created_date", _stub_date)
    monkeypatch.setattr(uploaded_file_service, "get_conversation_created_date", _stub_date)


@pytest.fixture(autouse=True)
def _no_setting_table_db(monkeypatch):
    """单元测试内配置变量表（chat_data_setting）不连真实数据库。

    get_runtime_setting 的表读取统一走 setting_service.transaction_scope；
    这里将其替换为立即抛「no such table」的桩，命中 _load_raw_setting 的
    missing-table 降级路径（告警一次 + 回退 env/默认），既保持「表覆盖 > env >
    默认」语义可被 setenv 驱动，又让单元测试零真实 DB 依赖。
    test_setting_service / test_setting_admin_api 等自带 sqlite harness 的用例
    会在本桩之后再覆写 transaction_scope，不受影响。
    """
    from contextlib import asynccontextmanager

    from apps.chat_data_app.services import setting_service

    @asynccontextmanager
    async def _no_db_scope():
        raise RuntimeError("no such table: chat_data_setting (unit-test stub)")
        yield  # pragma: no cover

    monkeypatch.setattr(setting_service, "transaction_scope", _no_db_scope)
