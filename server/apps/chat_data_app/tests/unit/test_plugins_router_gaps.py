"""``routers/plugins.py`` 分支补齐：失败兜底、探测异常与操作人降级解析。

成功链路由 ``tests/api/test_plugins_api.py`` 覆盖，这里只针对各路由的
``except`` 分支和上传大小边界做直调，避免起完整应用。
"""

import json

import pytest

from apps.chat_data_app.agent.plugins.installer import (
    PluginInstallError,
    PluginNotFoundError,
)
from apps.chat_data_app.agent.plugins.manifest import PluginManifestError
from apps.chat_data_app.routers import plugins as plugins_router
from utils.error_codes import ErrorCode


def _payload(response):
    return json.loads(response.body)


class _FakeUpload:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self, _size: int = -1) -> bytes:
        return self._data


class _Requester:
    def __init__(self):
        self.headers = {}


@pytest.fixture()
def requester(monkeypatch):
    monkeypatch.setattr(
        plugins_router,
        "_get_current_user",
        lambda _request: type("_User", (), {"user_id": " u1 ", "username": " 张三 "})(),
    )
    return _Requester()


def test_resolve_operator_returns_stripped_identity(requester):
    assert plugins_router._resolve_operator(requester) == ("u1", "张三")


def test_resolve_operator_degrades_when_identity_missing(monkeypatch):
    def _boom(_request):
        raise RuntimeError("no token")

    monkeypatch.setattr(plugins_router, "_get_current_user", _boom)
    assert plugins_router._resolve_operator(object()) == ("", "")


@pytest.mark.asyncio
async def test_list_plugins_clamps_paging_and_returns_error(monkeypatch):
    seen = {}

    async def _list(limit, offset):
        seen.update(limit=limit, offset=offset)
        return {"plugins": []}

    monkeypatch.setattr(plugins_router.plugin_service, "list_plugins", _list)
    ok = _payload(await plugins_router.list_plugins(object(), limit=0, offset=-5))
    assert ok["status"] == "success"
    assert seen == {"limit": 1, "offset": 0}

    async def _boom(_limit, _offset):
        raise RuntimeError("db down")

    monkeypatch.setattr(plugins_router.plugin_service, "list_plugins", _boom)
    failed = _payload(await plugins_router.list_plugins(object(), limit=500, offset=0))
    assert failed["code"] == ErrorCode.CHATDATA_PLUGIN_LIST_FAILED


@pytest.mark.asyncio
async def test_plugin_template_failure_returns_error(monkeypatch):
    def _boom():
        raise RuntimeError("zip broken")

    monkeypatch.setattr(plugins_router, "build_plugin_template_zip", _boom)
    body = _payload(await plugins_router.download_plugin_template(object()))
    assert body["code"] == ErrorCode.CHATDATA_GENERIC_OPERATION_FAILED


@pytest.mark.asyncio
async def test_mcp_template_failure_returns_error(monkeypatch):
    def _boom():
        raise RuntimeError("zip broken")

    monkeypatch.setattr(plugins_router, "build_mcp_server_template_zip", _boom)
    body = _payload(await plugins_router.download_mcp_server_template(object()))
    assert body["code"] == ErrorCode.CHATDATA_GENERIC_OPERATION_FAILED


@pytest.mark.asyncio
async def test_probe_mcp_tools_value_error_and_generic_error(monkeypatch):
    async def _invalid(_plugin, _server):
        raise ValueError("server 不存在")

    monkeypatch.setattr(plugins_router.plugin_service, "probe_mcp_server_tools", _invalid)
    invalid = _payload(await plugins_router.probe_plugin_mcp_server_tools("p", "s", object()))
    assert invalid["msg"] == "server 不存在"
    assert invalid["code"] == ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED

    async def _boom(_plugin, _server):
        raise RuntimeError("connect timeout")

    monkeypatch.setattr(plugins_router.plugin_service, "probe_mcp_server_tools", _boom)
    failed = _payload(await plugins_router.probe_plugin_mcp_server_tools("p", "s", object()))
    assert failed["msg"] == "连接 MCP server 失败，请检查地址、认证和网络后重试"


@pytest.mark.asyncio
async def test_install_rejects_oversized_upload(monkeypatch, requester):
    monkeypatch.setattr(plugins_router, "_PLUGIN_UPLOAD_MAX_BYTES", 4)
    body = _payload(await plugins_router.install_plugin(_FakeUpload(b"12345"), requester))
    assert body["code"] == ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED
    assert "超过大小上限" in body["msg"]


@pytest.mark.asyncio
async def test_install_passes_operator_and_maps_manifest_error(monkeypatch, requester):
    seen = {}

    async def _install(data, *, operator_id, operator_name):
        seen.update(data=data, operator_id=operator_id, operator_name=operator_name)
        raise PluginManifestError("manifest 不合法")

    monkeypatch.setattr(plugins_router.plugin_service, "install_plugin", _install)
    body = _payload(await plugins_router.install_plugin(_FakeUpload(b"zip"), requester))
    assert seen == {"data": b"zip", "operator_id": "u1", "operator_name": "张三"}
    assert body["code"] == ErrorCode.CHATDATA_PLUGIN_MANIFEST_INVALID


@pytest.mark.asyncio
async def test_install_generic_failure_returns_error(monkeypatch, requester):
    async def _boom(_data, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(plugins_router.plugin_service, "install_plugin", _boom)
    body = _payload(await plugins_router.install_plugin(_FakeUpload(b"zip"), requester))
    assert body["code"] == ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED


@pytest.mark.asyncio
async def test_enable_maps_manifest_error_and_generic_failure(monkeypatch, requester):
    async def _invalid(*_args, **_kwargs):
        raise PluginManifestError("非法插件名")

    monkeypatch.setattr(plugins_router.plugin_service, "set_plugin_enabled", _invalid)
    invalid = _payload(
        await plugins_router.update_plugin_enabled(
            "bad name", plugins_router.PluginEnabledRequest(enabled=True), requester
        )
    )
    assert invalid["code"] == ErrorCode.CHATDATA_PLUGIN_MANIFEST_INVALID

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(plugins_router.plugin_service, "set_plugin_enabled", _boom)
    failed = _payload(
        await plugins_router.update_plugin_enabled(
            "ops-readonly", plugins_router.PluginEnabledRequest(enabled=False), requester
        )
    )
    assert failed["code"] == ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED


@pytest.mark.asyncio
async def test_remove_maps_install_error_and_generic_failure(monkeypatch, requester):
    async def _install_error(*_args, **_kwargs):
        raise PluginInstallError("目录不可写")

    monkeypatch.setattr(plugins_router.plugin_service, "uninstall_plugin", _install_error)
    failed_install = _payload(await plugins_router.remove_plugin("p", requester))
    assert failed_install["code"] == ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED
    assert failed_install["msg"] == "目录不可写"

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(plugins_router.plugin_service, "uninstall_plugin", _boom)
    failed = _payload(await plugins_router.remove_plugin("p", requester))
    assert failed["code"] == ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED


@pytest.mark.asyncio
async def test_remove_maps_not_found(monkeypatch, requester):
    async def _missing(*_args, **_kwargs):
        raise PluginNotFoundError("插件不存在")

    monkeypatch.setattr(plugins_router.plugin_service, "uninstall_plugin", _missing)
    body = _payload(await plugins_router.remove_plugin("ghost", requester))
    assert body["code"] == ErrorCode.CHATDATA_PLUGIN_NOT_FOUND
