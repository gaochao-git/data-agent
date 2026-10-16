"""meta_admin.py 全局枚举管理路由单测（直接调用端点函数）。

覆盖 7 个端点的成功路径、统一错误兜底（未预期异常 → OPERATION_FAILED）、
HTTPException 原样抛出（不能被兜底吞掉）与 upsert/delete 的业务错误码映射。
service 以桩对象注入（鸭子类型），业务规则本身在 test_meta_global_enum.py 覆盖。
"""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.routers import meta_admin
from apps.chat_data_app.schema import MetaGlobalEnumGenerateRequest, MetaGlobalEnumItem, MetaGlobalEnumUpsertRequest
from utils.error_codes import ErrorCode


def _payload(response):
    return json.loads(response.body.decode("utf-8"))


class _StubService:
    """按构造参数决定行为：正常返回数据或抛指定异常，并记录调用。"""

    def __init__(self, **results):
        self.results = results
        self.calls: list[tuple] = []

    def _handle(self, name, *args, **kwargs):
        self.calls.append((name, *args, kwargs) if kwargs else (name, *args))
        outcome = self.results.get(name)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def list_enums_async(self, keyword="", business_domain=None):
        return self._handle("list_enums_async", keyword, business_domain)

    async def list_enum_domains_async(self):
        return self._handle("list_enum_domains_async")

    async def get_enum_async(self, enum_name, business_domain=""):
        return self._handle("get_enum_async", enum_name, business_domain)

    async def list_enum_refs_async(self, enum_name, business_domain=""):
        return self._handle("list_enum_refs_async", enum_name, business_domain)

    async def upsert_enum_async(self, **kwargs):
        return self._handle("upsert_enum_async", **kwargs)

    async def delete_enum_async(self, enum_name, business_domain="", operator="", operator_name=""):
        return self._handle("delete_enum_async", enum_name, business_domain, operator, operator_name)


@pytest.fixture()
def user(monkeypatch):
    monkeypatch.setattr(
        meta_admin, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="张三")
    )
    return SimpleNamespace(user_id="u1", username="张三")


def test_list_enums_success_and_filters(user, monkeypatch):
    stub = _StubService(list_enums_async=[{"enum_name": "pay_status", "ref_count": 0}])
    monkeypatch.setattr(meta_admin, "global_enum_service", stub)

    response = asyncio.run(meta_admin.list_meta_global_enums(None, keyword="pay", business_domain="订单"))
    assert _payload(response)["data"] == [{"enum_name": "pay_status", "ref_count": 0}]
    assert stub.calls == [("list_enums_async", "pay", "订单")]

    # keyword/business_domain 缺省时以空串/None 透传
    asyncio.run(meta_admin.list_meta_global_enums(None))
    assert stub.calls[-1] == ("list_enums_async", "", None)


def test_list_enums_error_fallback(user, monkeypatch):
    stub = _StubService(list_enums_async=RuntimeError("db down"))
    monkeypatch.setattr(meta_admin, "global_enum_service", stub)
    response = asyncio.run(meta_admin.list_meta_global_enums(None))
    body = _payload(response)
    assert body["status"] == "error" and body["code"] == ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED


def test_domains_and_get_and_refs(user, monkeypatch):
    stub = _StubService(
        list_enum_domains_async=["订单", ""],
        get_enum_async={"enum_name": "pay_status"},
        list_enum_refs_async=[{"group_id": "g1"}],
    )
    monkeypatch.setattr(meta_admin, "global_enum_service", stub)

    assert _payload(asyncio.run(meta_admin.list_meta_global_enum_domains(None)))["data"]["domains"] == ["订单", ""]
    assert (
        _payload(asyncio.run(meta_admin.get_meta_global_enum("pay_status", None)))["data"]["enum_name"] == "pay_status"
    )
    refs_response = asyncio.run(meta_admin.list_meta_global_enum_refs("pay_status", None, business_domain="订单"))
    refs_body = _payload(refs_response)
    assert refs_body["data"]["ref_count"] == 1 and refs_body["data"]["business_domain"] == "订单"

    # 详情不存在 → ENUM_NOT_FOUND 业务错误
    missing = _StubService(get_enum_async=None)
    monkeypatch.setattr(meta_admin, "global_enum_service", missing)
    body = _payload(asyncio.run(meta_admin.get_meta_global_enum("ghost", None)))
    assert body["code"] == ErrorCode.CHATDATA_META_ENUM_NOT_FOUND and body["status"] == "error"


def test_get_and_refs_error_fallback(user, monkeypatch):
    boom = _StubService(get_enum_async=RuntimeError("x"), list_enum_refs_async=RuntimeError("x"))
    monkeypatch.setattr(meta_admin, "global_enum_service", boom)
    assert (
        _payload(asyncio.run(meta_admin.get_meta_global_enum("pay_status", None)))["code"]
        == ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED
    )
    assert (
        _payload(asyncio.run(meta_admin.list_meta_global_enum_refs("pay_status", None)))["code"]
        == ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED
    )


def test_generate_items_success_and_fallback(user, monkeypatch):
    async def fake_generate(_text):
        return {"enum_values": [{"value": "1", "label": "成功"}], "other": "忽略"}

    monkeypatch.setattr(meta_admin, "generate_dict_candidates_from_text", fake_generate)
    payload = MetaGlobalEnumGenerateRequest(text="状态有：成功、失败")
    body = _payload(asyncio.run(meta_admin.generate_meta_global_enum_items(payload, None)))
    assert body["data"]["enum_items"] == [{"value": "1", "label": "成功"}]

    async def broken_generate(_text):
        raise RuntimeError("llm down")

    monkeypatch.setattr(meta_admin, "generate_dict_candidates_from_text", broken_generate)
    body = _payload(asyncio.run(meta_admin.generate_meta_global_enum_items(payload, None)))
    assert body["code"] == ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED


def test_upsert_error_code_mapping_and_operator(user, monkeypatch):
    stub = _StubService(upsert_enum_async={"enum_name": "pay_status"})
    monkeypatch.setattr(meta_admin, "global_enum_service", stub)
    payload = MetaGlobalEnumUpsertRequest(
        business_domain="订单",
        enum_desc="支付状态",
        enum_items=[MetaGlobalEnumItem(value="1", label="成功")],
        allow_overwrite=True,
    )
    response = asyncio.run(meta_admin.upsert_meta_global_enum("pay_status", payload, None))
    assert _payload(response)["data"] == {"enum_name": "pay_status"}
    call = stub.calls[-1]
    assert call[1]["operator"] == "u1" and call[1]["operator_name"] == "张三" and call[1]["allow_overwrite"] is True

    cases = [
        (meta_admin.MetaGlobalEnumNameInvalidError("名非法"), ErrorCode.CHATDATA_META_ENUM_NAME_INVALID),
        (meta_admin.MetaGlobalEnumInvalidError("内容非法"), ErrorCode.CHATDATA_META_ENUM_INVALID),
        (meta_admin.MetaGlobalEnumAlreadyExistsError("已存在"), ErrorCode.CHATDATA_META_ENUM_ALREADY_EXISTS),
        (RuntimeError("db down"), ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED),
    ]
    for error, expected_code in cases:
        monkeypatch.setattr(meta_admin, "global_enum_service", _StubService(upsert_enum_async=error))
        body = _payload(asyncio.run(meta_admin.upsert_meta_global_enum("pay_status", payload, None)))
        assert body["code"] == expected_code and body["status"] == "error"


def test_delete_error_code_mapping(user, monkeypatch):
    ok = _StubService(delete_enum_async=None)
    monkeypatch.setattr(meta_admin, "global_enum_service", ok)
    body = _payload(asyncio.run(meta_admin.delete_meta_global_enum("pay_status", None, business_domain="订单")))
    assert body["data"] == {"business_domain": "订单", "enum_name": "pay_status"}
    assert ok.calls[-1] == ("delete_enum_async", "pay_status", "订单", "u1", "张三")

    cases = [
        (meta_admin.MetaGlobalEnumNotFoundError("不存在"), ErrorCode.CHATDATA_META_ENUM_NOT_FOUND),
        (meta_admin.MetaGlobalEnumInUseError("被引用"), ErrorCode.CHATDATA_META_ENUM_IN_USE),
        (RuntimeError("db down"), ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED),
    ]
    for error, expected_code in cases:
        monkeypatch.setattr(meta_admin, "global_enum_service", _StubService(delete_enum_async=error))
        body = _payload(asyncio.run(meta_admin.delete_meta_global_enum("pay_status", None)))
        assert body["code"] == expected_code and body["status"] == "error"


def test_http_exception_passthrough(user, monkeypatch):
    """鉴权失败（HTTPException）必须原样抛出，不能被统一兜底吞成 200。"""

    def denied(_request):
        raise HTTPException(status_code=401, detail="未登录")

    monkeypatch.setattr(meta_admin, "_get_current_user", denied)
    stub = _StubService(list_enums_async=[])
    monkeypatch.setattr(meta_admin, "global_enum_service", stub)

    with pytest.raises(HTTPException):
        asyncio.run(meta_admin.list_meta_global_enums(None))
    with pytest.raises(HTTPException):
        asyncio.run(
            meta_admin.upsert_meta_global_enum(
                "pay_status",
                MetaGlobalEnumUpsertRequest(enum_items=[MetaGlobalEnumItem(value="1", label="成功")]),
                None,
            )
        )
    with pytest.raises(HTTPException):
        asyncio.run(meta_admin.delete_meta_global_enum("pay_status", None))
    assert stub.calls == []  # 未过鉴权不应触达 service


def test_domains_error_fallback(user, monkeypatch):
    stub = _StubService(list_enum_domains_async=RuntimeError("db down"))
    monkeypatch.setattr(meta_admin, "global_enum_service", stub)
    body = _payload(asyncio.run(meta_admin.list_meta_global_enum_domains(None)))
    assert body["code"] == ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED and body["status"] == "error"


def test_http_exception_passthrough_remaining_endpoints(user, monkeypatch):
    """get/refs/domains/generate 的鉴权失败同样原样抛出。"""

    def denied(_request):
        raise HTTPException(status_code=401, detail="未登录")

    monkeypatch.setattr(meta_admin, "_get_current_user", denied)
    stub = _StubService(get_enum_async={"enum_name": "x"}, list_enum_refs_async=[], list_enum_domains_async=[])
    monkeypatch.setattr(meta_admin, "global_enum_service", stub)

    with pytest.raises(HTTPException):
        asyncio.run(meta_admin.get_meta_global_enum("pay_status", None))
    with pytest.raises(HTTPException):
        asyncio.run(meta_admin.list_meta_global_enum_refs("pay_status", None))
    with pytest.raises(HTTPException):
        asyncio.run(meta_admin.list_meta_global_enum_domains(None))
    with pytest.raises(HTTPException):
        asyncio.run(meta_admin.generate_meta_global_enum_items(MetaGlobalEnumGenerateRequest(text="状态：成功"), None))
    assert stub.calls == []
