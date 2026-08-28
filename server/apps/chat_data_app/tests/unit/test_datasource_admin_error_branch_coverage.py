"""datasource_admin.py 错误分支覆盖率补充测试。

该路由每个端点都带一段 `except HTTPException: raise` + `except Exception:
return business_response(...)` 的统一兜底，既有测试只覆盖了成功路径与
ValueError 分支。这里用"任意方法都抛异常"的 manager 替身逐端点触发兜底分支，
同时断言两件事：HTTPException 必须原样抛出（不能被兜底吞成 200），
未预期异常必须转成统一业务错误响应。
"""

import asyncio
import json
import os
import sys

import pytest
from fastapi import HTTPException
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.routers import datasource_admin as admin
from apps.chat_data_app.routers import dependencies as dependencies_routes
from utils.error_codes import ErrorCode


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


class _BoomManager:
    """任何方法被调用都抛未预期异常，并记录被调用的方法名。"""

    def __init__(self):
        self.called: list[str] = []

    def __getattr__(self, name):
        if name.startswith("__") or name == "called":
            raise AttributeError(name)

        async def raiser(*_args, **_kwargs):
            self.called.append(name)
            raise RuntimeError(f"数据源服务异常: {name}")

        return raiser


class _Any:
    """宽松值：可读属性、可迭代、可 strip、可 model_dump，避免请求体校验提前返回。"""

    def __getattr__(self, _name):
        return _ANY

    def __call__(self, *_args, **_kwargs):
        return _ANY

    def __iter__(self):
        return iter([_ANY])

    def __bool__(self):
        return True

    def __str__(self):
        return "占位值"

    def strip(self, *_args):
        return "占位值"

    def model_dump(self):
        return {"placeholder": "x"}

    def get(self, _key, default=None):
        return _ANY


_ANY = _Any()


class _AnyBody:
    """宽松请求体：所有字段都是可读可迭代的占位值。"""

    def __getattr__(self, _name):
        return _ANY


def _mock_user(monkeypatch):
    monkeypatch.setattr(
        dependencies_routes, "_get_current_user",
        lambda _request: SimpleNamespace(user_id="10001", username="测试用户"),
    )
    monkeypatch.setattr(admin, "_get_current_user", lambda _request: SimpleNamespace(user_id="10001", username="测试用户"))


def _mock_boom(monkeypatch):
    _mock_user(monkeypatch)
    manager = _BoomManager()
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(admin, "get_semantic_config_service", lambda: manager)
    return manager


# (端点, 位置参数, 关键字参数)
ENDPOINTS = [
    (admin.get_admin_datasources, (), {}),
    (admin.create_datasource_group, (_AnyBody(),), {}),
    (admin.update_datasource_meta, ("ds_a", _AnyBody()), {}),
    (admin.update_datasource_group_config, ("ds_a", _AnyBody()), {}),
    (admin.sync_datasource_group_metadata, ("ds_a", _AnyBody()), {}),
    (admin.update_mapped_modules, ("ds_a", {}), {}),
    (admin.get_datasource_table_groups, ("ds_a",), {}),
    (admin.get_datasource_semantic_health, ("ds_a",), {}),
    (admin.update_datasource_table_groups, ("ds_a", _AnyBody()), {}),
    (admin.update_datasource_table_business_desc, ("ds_a", "t_order", _AnyBody()), {}),
    (admin.update_datasource_column_dict, ("ds_a", "t_order", "amount", _AnyBody()), {}),
    (admin.get_admin_datasource_column_dict, ("ds_a", "t_order", "amount"), {}),
    (admin.get_admin_datasource_table_column_dicts, ("ds_a", "t_order"), {}),
    (admin.generate_datasource_column_dict, ("ds_a", "t_order", "amount", _AnyBody()), {}),
    (admin.get_datasource_table_indexes, ("ds_a", "t_order"), {}),
    (admin.update_datasource_table_indexes, ("ds_a", "t_order", _AnyBody()), {}),
    (admin.set_default_datasource, ("ds_a",), {}),
    (admin.clear_default_datasource, ("ds_a",), {}),
    (admin.delete_datasource, ("ds_a",), {}),
]


@pytest.mark.parametrize(
    ("endpoint", "args", "kwargs"),
    ENDPOINTS,
    ids=[endpoint.__name__ for endpoint, _, _ in ENDPOINTS],
)
def test_admin_endpoint_converts_unexpected_failure_to_business_error(
    monkeypatch, endpoint, args, kwargs
):
    manager = _mock_boom(monkeypatch)
    response = asyncio.run(endpoint(*args, SimpleNamespace(), **kwargs))
    payload = _payload(response)
    # 必须真的走到数据源服务，而不是被入参校验提前拦下
    assert manager.called, f"{endpoint.__name__} 未触达 manager，兜底分支未被验证"
    assert payload["status"] == "error"
    assert payload["code"] != 2000
    assert payload["msg"]


@pytest.mark.parametrize(
    ("endpoint", "args", "kwargs"),
    ENDPOINTS,
    ids=[endpoint.__name__ for endpoint, _, _ in ENDPOINTS],
)
def test_admin_endpoint_reraises_http_exception_without_swallowing(
    monkeypatch, endpoint, args, kwargs
):
    """鉴权等 HTTPException 必须原样抛出，不能被 except Exception 转成 200 错误体。"""
    _mock_user(monkeypatch)

    class _HttpRaisingManager:
        def __getattr__(self, _name):
            async def raiser(*_args, **_kwargs):
                raise HTTPException(status_code=403, detail="无权限")
            return raiser

    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _HttpRaisingManager())
    monkeypatch.setattr(admin, "get_semantic_config_service", lambda: _HttpRaisingManager())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(endpoint(*args, SimpleNamespace(), **kwargs))
    assert exc_info.value.status_code == 403


def test_admin_endpoints_return_success_payload_when_manager_ok(monkeypatch):
    """兜底不能吃掉正常返回：manager 正常时应是 success。"""
    _mock_user(monkeypatch)

    class _OkManager:
        async def get_all_datasources_async(self, **_kwargs):
            return [{"id": "ds_a", "group_name": "数据源A"}]

        async def get_default_datasource_id(self):
            return "ds_a"

    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _OkManager())
    response = asyncio.run(admin.get_admin_datasources(SimpleNamespace()))
    payload = _payload(response)
    assert payload["status"] == "success"
    assert payload["data"]["datasources"] == [{"id": "ds_a", "group_name": "数据源A"}]


# ---------------------------------------------------------------------------
# 业务校验失败与超时分支
# ---------------------------------------------------------------------------


class _MethodManager:
    """按方法名注入返回值或异常。"""

    def __init__(self, **behaviours):
        self._behaviours = behaviours

    def __getattr__(self, name):
        behaviour = self._behaviours.get(name)

        async def call(*_args, **_kwargs):
            if isinstance(behaviour, Exception):
                raise behaviour
            return behaviour

        return call


def test_create_datasource_group_maps_value_error_to_bad_request(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _MethodManager(
        get_datasource_meta_by_group_name_async=None,
        create_datasource_group_from_sources_async=ValueError("模板数据源配置非法"),
    ))
    payload = _payload(asyncio.run(admin.create_datasource_group(_AnyBody(), SimpleNamespace())))
    # 契约：ValueError（含入参归一化阶段的）必须映射为参数错误码并透传原文，
    # 不能落到 500 语义的通用失败码
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert payload["msg"]


def test_update_group_config_maps_value_error_to_bad_request(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _MethodManager(
        get_datasource_meta_async={"id": "ds_a"},
        get_datasource_meta_by_group_name_async=None,
        update_datasource_group_from_sources_async=ValueError("成员数据源至少需要一个"),
    ))
    payload = _payload(asyncio.run(admin.update_datasource_group_config("ds_a", _AnyBody(), SimpleNamespace())))
    # 契约：ValueError 必须映射为参数错误码并透传原文，不能落到通用失败码
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert payload["msg"]


def test_update_group_config_reports_missing_datasource(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _MethodManager(
        update_datasource_group_from_sources_async=None,
    ))
    payload = _payload(asyncio.run(admin.update_datasource_group_config("ds_a", _AnyBody(), SimpleNamespace())))
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_sync_metadata_reports_timeout(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _MethodManager(
        sync_datasource_schema_from_source_async=asyncio.TimeoutError(),
    ))
    payload = _payload(asyncio.run(admin.sync_datasource_group_metadata("ds_a", _AnyBody(), SimpleNamespace())))
    assert payload["status"] == "error"
    assert "同步超时" in payload["msg"]


def test_sync_metadata_maps_value_error_to_bad_request(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _MethodManager(
        sync_datasource_schema_from_source_async=ValueError("模板数据源不存在"),
    ))
    payload = _payload(asyncio.run(admin.sync_datasource_group_metadata("ds_a", _AnyBody(), SimpleNamespace())))
    assert payload["status"] == "error"
    assert payload["msg"] == "模板数据源不存在"


def test_get_db_tables_reports_connection_timeout(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _MethodManager(
        get_physical_table_names_async=asyncio.TimeoutError(),
    ))
    payload = _payload(asyncio.run(admin.get_db_tables(_AnyBody(), SimpleNamespace())))
    assert payload["status"] == "error"
    assert "连接超时" in payload["msg"]


def test_get_db_tables_reports_generic_failure_with_safe_text(monkeypatch):
    _mock_user(monkeypatch)
    monkeypatch.setattr(admin, "get_datasource_manager", lambda: _MethodManager(
        get_physical_table_names_async=RuntimeError("Access denied for user 'root'@'10.0.0.5'"),
    ))
    payload = _payload(asyncio.run(admin.get_db_tables(_AnyBody(), SimpleNamespace())))
    assert payload["status"] == "error"
    # 该端点兜底是固定文案，不能把驱动层异常原文透给前端
    assert payload["msg"] == "连接失败，请检查数据源配置或稍后重试"
    assert "10.0.0.5" not in payload["msg"]
