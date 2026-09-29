"""semantic_model_repository 运行时语义模型读取/物化目录的单测。

原位于 test_semantic_tools.py；2026-09-18 semantic_search 工具与
semantic_model_search 检索器整体下线后，文件按幸存职责更名重整。
"""

import asyncio

from apps.chat_data_app.services.semantic.run.semantic_model_repository import (
    list_runtime_semantic_config_directory,
    load_runtime_semantic_config,
)


def test_list_runtime_semantic_config_directory_uses_semantic_service(monkeypatch):
    captured = {}

    class FakeSemanticService:
        async def get_directory_async(self, datasource_id, user_id=""):
            assert datasource_id == "ds_legacy"
            captured["user_id"] = user_id
            return [
                {
                    "id": "sc_legacy",
                    "name": "历史指标集",
                    "description": "历史描述",
                    "enabled": True,
                    "metrics_total": 0,
                    "dimensions_total": 0,
                },
            ]

    monkeypatch.setattr(
        "apps.chat_data_app.services.semantic.run.semantic_model_repository.get_semantic_config_service",
        lambda: FakeSemanticService(),
    )

    directory = asyncio.run(list_runtime_semantic_config_directory("ds_legacy", "u2"))

    assert captured["user_id"] == "u2"
    assert directory == [
        {
            "id": "sc_legacy",
            "name": "历史指标集",
            "description": "历史描述",
            "enabled": True,
            "metrics_total": 0,
            "dimensions_total": 0,
        }
    ]


def test_load_runtime_semantic_config_branches(monkeypatch):
    class RuntimeManager:
        async def get_datasource_async(self, datasource_id):
            if datasource_id == "missing":
                return None
            if datasource_id == "no_type":
                return {"id": datasource_id}
            return {"id": datasource_id, "source_type": "hive"}

        async def get_runtime_semantic_config_async(self, datasource_id, semantic_config_id):
            if semantic_config_id == "enabled":
                return {"id": semantic_config_id, "enabled": True, "relationships": [{"left_table": "orders"}]}
            if semantic_config_id == "disabled":
                return {"id": semantic_config_id, "enabled": False}
            if semantic_config_id == "plain":
                return {"id": semantic_config_id, "enabled": True}
            if semantic_config_id == "no_id":
                return {"enabled": True}
            return None

    class FallbackManager:
        async def get_datasource_async(self, datasource_id):
            return {"id": datasource_id, "source_type": "mysql"}

        async def get_semantic_config_async(self, datasource_id, semantic_config_id):
            return {"id": semantic_config_id, "enabled": True, "joins": [{"left_table": "kept"}]}

    runtime_manager = RuntimeManager()

    class RuntimeSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            if datasource_id == "missing":
                return None
            return await runtime_manager.get_runtime_semantic_config_async(datasource_id, config_id)

    monkeypatch.setattr(
        "apps.chat_data_app.services.semantic.run.semantic_model_repository.get_datasource_manager",
        lambda: runtime_manager,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.services.semantic.run.semantic_model_repository.get_semantic_config_service",
        lambda: RuntimeSemanticService(),
    )

    assert asyncio.run(load_runtime_semantic_config(None, "cfg")) is None
    assert asyncio.run(load_runtime_semantic_config("ds", None)) is None
    assert asyncio.run(load_runtime_semantic_config("missing", "enabled")) is None
    assert asyncio.run(load_runtime_semantic_config("ds", "missing")) is None
    assert asyncio.run(load_runtime_semantic_config("ds", "disabled")) is None

    enabled = asyncio.run(load_runtime_semantic_config("ds", "enabled"))
    plain = asyncio.run(load_runtime_semantic_config("ds", "plain"))
    no_source_type = asyncio.run(load_runtime_semantic_config("no_type", "plain"))
    no_id = asyncio.run(load_runtime_semantic_config("ds", "no_id"))
    assert enabled["joins"] == [{"left_table": "orders"}]
    assert enabled["source_type"] == "hive"
    assert "joins" not in plain
    assert "source_type" not in no_source_type
    assert no_id["id"] == "no_id"

    fallback_manager = FallbackManager()

    class FallbackSemanticService:
        async def get_runtime_config_async(self, datasource_id, config_id, user_id=""):
            return await fallback_manager.get_semantic_config_async(datasource_id, config_id)

    monkeypatch.setattr(
        "apps.chat_data_app.services.semantic.run.semantic_model_repository.get_datasource_manager",
        lambda: fallback_manager,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.services.semantic.run.semantic_model_repository.get_semantic_config_service",
        lambda: FallbackSemanticService(),
    )
    fallback = asyncio.run(load_runtime_semantic_config("ds", "cfg_b"))
    assert fallback["source_type"] == "mysql"
    assert fallback["joins"] == [{"left_table": "kept"}]
