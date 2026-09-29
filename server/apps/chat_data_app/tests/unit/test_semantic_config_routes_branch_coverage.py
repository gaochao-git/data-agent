"""routers/semantic_config 分支覆盖补测。

覆盖 2026-09-18 审计出的 15 条未覆盖分支弧：
列表路由数据源不存在出口、Ossie 预览错误透传、质量检查路由的 modeled_tables
收集循环各分支、导入路由 replace 写入失败回滚、历史学习权限拒绝。
"""

import json
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.routers import semantic_config
from apps.chat_data_app.services.semantic.run import semantic_config_service

_REQUEST = SimpleNamespace()
_USER = SimpleNamespace(user_id="u1", username="张三")


def _body(resp) -> dict:
    return json.loads(resp.body.decode("utf-8"))


def _patch_user(monkeypatch):
    monkeypatch.setattr(semantic_config, "_get_current_user", lambda _request: _USER)


# ---------------------------------------------------------------- 列表路由：数据源不存在

@pytest.mark.asyncio
@pytest.mark.parametrize("route", [
    semantic_config.list_my_semantic_configs,
    semantic_config.list_admin_semantic_configs,
])
async def test_list_routes_report_missing_datasource(route, monkeypatch):
    """has_datasource_async=False → NOT_FOUND 出口（494->495 / 512->513）。"""
    _patch_user(monkeypatch)

    class _Mgr:
        async def has_datasource_async(self, _ds_id):
            return False

    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: _Mgr())
    resp = await route("ds_missing", _REQUEST)
    assert _body(resp)["msg"] == "数据源不存在: ds_missing"


# ---------------------------------------------------------------- Ossie 预览：错误透传

@pytest.mark.asyncio
async def test_preview_route_returns_prepare_error(monkeypatch):
    """_prepare_ossie_import 返回 error → 直接透传（667->668）。"""
    _patch_user(monkeypatch)
    sentinel = {"status": "error"}

    async def _fake_prepare(_ds_id, _payload, _request):
        return None, sentinel

    monkeypatch.setattr(semantic_config, "_prepare_ossie_import", _fake_prepare)
    assert await semantic_config.preview_admin_ossie_semantic_import("ds_a", None, _REQUEST) is sentinel


# ---------------------------------------------------------------- 质量检查：modeled_tables 收集循环

def _quality_configs() -> list:
    return [{
        "name": "模型A",
        "metrics": [{"name": "销售额", "table": "orders"}],
        "dimensions": [{"name": "空表维度", "table": " "}],
        "relationships": [
            {"left_table": "orders", "right_table": "users"},
            {"left_table": "", "right_table": None},
            "junk-relationship",
        ],
    }]


@pytest.mark.asyncio
async def test_quality_route_collects_modeled_tables(monkeypatch):
    """覆盖 729->730/730->729/730->731/732->733/733->732/733->734/734->732/734->735/736->734/736->737。"""

    class _CheckerReport:
        def to_payload(self):
            return {"score": 90}

    class _Checker:
        def check_config(self, _config, physical_columns=None):
            return _CheckerReport()

    class _Mgr:
        async def get_datasource_async(self, _ds_id):
            return {"id": "ds_a"}

    class _Service:
        async def get_configs_async(self, _ds_id):
            return _quality_configs()

    _patch_user(monkeypatch)
    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: _Mgr())
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: _Service())
    monkeypatch.setattr(semantic_config, "is_public_semantic_config", lambda _config: True)

    async def _fake_columns(_mgr, _ds_id):
        return [("orders", "amount")]

    monkeypatch.setattr(semantic_config, "_load_physical_columns", _fake_columns)
    monkeypatch.setattr(semantic_config, "get_semantic_quality_checker", lambda: _Checker())

    resp = await semantic_config.check_admin_semantic_config_quality("ds_a", _REQUEST)
    body = _body(resp)
    assert body["status"] != "error"
    overall = body["data"]["overall"]
    # orders(指标) 与 users(关系右侧) 进 modeled_tables；空表维度不计
    assert overall["unmodeled_tables"] == []
    assert overall["table_modeled"] == 1


# ---------------------------------------------------------------- Ossie 导入：replace 写入失败回滚

@pytest.mark.asyncio
async def test_import_route_rolls_back_when_replace_write_fails(monkeypatch):
    """replace 路径 update_async 返回 None → _OssieImportWriteError → 整体回滚（833->834）。"""
    _patch_user(monkeypatch)

    class _Item:
        name = "产品分类模型"
        importable = True
        conflict_locked = False
        existing_config = {"config_id": "cfg_1", "name": "产品分类模型"}
        description = "d"
        business_notes = "b"
        metrics: list = []
        dimensions: list = []
        relationships: list = []

    async def _fake_prepare(_ds_id, _payload, _request):
        return [_Item()], None

    class _Service:
        async def update_async(self, *_args, **_kwargs):
            return None

    @asynccontextmanager
    async def _fake_scope():
        yield

    monkeypatch.setattr(semantic_config, "_prepare_ossie_import", _fake_prepare)
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: _Service())
    monkeypatch.setattr(semantic_config_service, "transaction_scope", _fake_scope)

    payload = SimpleNamespace(conflict_actions={"产品分类模型": "replace"})
    resp = await semantic_config.import_admin_ossie_semantic_configs("ds_a", payload, _REQUEST)
    assert "更新配置失败" in _body(resp)["msg"]


# ---------------------------------------------------------------- 历史学习：权限拒绝

@pytest.mark.asyncio
async def test_history_learning_rejects_without_permission(monkeypatch):
    """can_mutate_semantic_config=False → 权限拒绝出口（933->934）。"""
    _patch_user(monkeypatch)

    class _Mgr:
        async def get_datasource_async(self, _ds_id):
            return {"id": "ds_a"}

    class _Service:
        async def get_config_async(self, _ds_id, _cfg_id):
            return {"id": "cfg_a"}

    monkeypatch.setattr(semantic_config, "get_datasource_manager", lambda: _Mgr())
    monkeypatch.setattr(semantic_config, "get_semantic_config_service", lambda: _Service())
    monkeypatch.setattr(semantic_config, "can_mutate_semantic_config", lambda _c, _u, _admin: False)

    resp = await semantic_config._learn_semantic_config_from_history_route(
        "ds_a", "cfg_a", SimpleNamespace(), _REQUEST, admin_manage=True)
    assert _body(resp)["msg"] == "无权限操作该指标集"
