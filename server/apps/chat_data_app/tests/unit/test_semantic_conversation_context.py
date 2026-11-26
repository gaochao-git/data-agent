import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.services.semantic.run import semantic_conversation_context


class _FakeDatasourceManager:
    def __init__(self):
        self.meta_calls: list[str] = []

    async def get_datasource_meta_async(self, datasource_id: str):
        self.meta_calls.append(datasource_id)
        return {"id": datasource_id, "query_context": "按机构分库查询", "ds_tables": []}


@pytest.mark.asyncio
async def test_prepare_context_loads_datasource_meta_without_authorizing(monkeypatch):
    manager = _FakeDatasourceManager()

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id=None,
        user_id="u_1",
    )

    assert manager.meta_calls == ["ds_1"]
    assert context.datasource_meta is not None
    assert context.semantic_config is None


@pytest.mark.asyncio
async def test_prepare_context_loads_selected_config_for_prompt(monkeypatch):
    manager = _FakeDatasourceManager()
    calls = {"config": []}

    async def fake_config(datasource_id, semantic_config_id, user_id):
        calls["config"].append((datasource_id, semantic_config_id, user_id))
        return {"id": "cfg_1", "name": "订单指标集", "enabled": True, "metrics": [], "dimensions": []}

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_conversation_context, "load_runtime_semantic_config", fake_config)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id="cfg_1",
        user_id="u_1",
    )

    assert calls["config"] == [("ds_1", "cfg_1", "u_1")]
    assert context.semantic_config_id == "cfg_1"
    assert context.semantic_config["name"] == "订单指标集"


@pytest.mark.asyncio
async def test_prepare_context_returns_neutral_error_for_invisible_selected_config(monkeypatch):
    manager = _FakeDatasourceManager()

    async def missing_config(*_args):
        return None

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_conversation_context, "load_runtime_semantic_config", missing_config)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id="private_cfg",
        user_id="u_2",
    )

    assert context.semantic_config is None
    assert context.semantic_config_error == "未找到可用指标集: private_cfg"
