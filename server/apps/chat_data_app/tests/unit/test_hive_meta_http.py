import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.datasource import credentials as datasource_credentials
from apps.chat_data_app.services.datasource import metadata as hive_meta_http
from apps.chat_data_app.services.datasource.metadata import _build_payload


def test_hive_meta_payload_uses_internal_org_fields():
    payload = _build_payload({
        "org_code": "Z2002131000014",
        "org_name": "机构名称xx",
        "database": "default",
    }, ["orders"], user_id="user_a", username="测试用户")

    assert payload == {
        "source_type": "hive",
        "orgCode": "Z2002131000014",
        "orgName": "机构名称xx",
        "database": "default",
        "userId": "user_a",
        "username": "测试用户",
        "table_names": ["orders"],
    }
    legacy_payload = _build_payload({
        "org_code": "Z2002131000014",
        "org_name": "机构名称xx",
        "host": "localhost",
        "port": 10000,
        "database": "default",
    })
    assert legacy_payload["host"] == "localhost"
    assert legacy_payload["port"] == 10000


def test_hive_meta_payload_requires_internal_org_code():
    with pytest.raises(ValueError, match="缺少 orgCode"):
        _build_payload({"orgCode": "ORG_A", "orgName": "机构A"})


def test_hive_meta_payload_supports_blank_org_name():
    payload = _build_payload({"org_code": "ORG_A"})

    assert payload["orgCode"] == "ORG_A"
    assert payload["orgName"] == ""


def test_hive_meta_config_and_response_helpers(monkeypatch):
    monkeypatch.setenv("CHATDATA_META_TEST_INT", "")
    assert hive_meta_http._read_positive_int_env("CHATDATA_META_TEST_INT", 5) == 5
    monkeypatch.setenv("CHATDATA_META_TEST_INT", "bad")
    assert hive_meta_http._read_positive_int_env("CHATDATA_META_TEST_INT", 5) == 5
    monkeypatch.setenv("CHATDATA_META_TEST_INT", "0")
    assert hive_meta_http._read_positive_int_env("CHATDATA_META_TEST_INT", 5) == 5
    monkeypatch.setenv("CHATDATA_META_TEST_INT", "7")
    assert hive_meta_http._read_positive_int_env("CHATDATA_META_TEST_INT", 5) == 7

    monkeypatch.setattr(hive_meta_http, "META_TABLES_URL", "http://meta/tables")
    monkeypatch.setattr(hive_meta_http, "META_SCHEMA_URL", "http://meta/schema")
    hive_meta_http.validate_hive_meta_config()

    monkeypatch.setattr(hive_meta_http, "META_TABLES_URL", "")
    with pytest.raises(ValueError, match="需要配置"):
        hive_meta_http.validate_hive_meta_config()

    assert hive_meta_http._extract_response_data({"code": 20000, "data": {"tables": []}}) == {"tables": []}
    assert hive_meta_http._extract_response_data({"code": 20000, "data": []}) == {"code": 20000, "data": []}
    assert hive_meta_http._extract_response_data({"tables": []}) == {"tables": []}
    with pytest.raises(RuntimeError, match="返回非法 code"):
        hive_meta_http._extract_response_data({"code": "bad", "data": {}})
    with pytest.raises(RuntimeError, match="连接失败"):
        hive_meta_http._extract_response_data({"code": 50000, "msg": "连接失败"})

    with pytest.raises(ValueError, match="缺少 orgCode"):
        hive_meta_http._build_payload({"database": "default"})


def test_hive_meta_headers_include_auth_token(monkeypatch):
    monkeypatch.setattr(hive_meta_http, "HTTP_AUTH_TOKEN", "")
    assert hive_meta_http._build_headers() == {"Content-Type": "application/json"}
    monkeypatch.setattr(hive_meta_http, "HTTP_AUTH_TOKEN", "secret")
    assert hive_meta_http._build_headers() == {
        "Content-Type": "application/json",
        "Authorization": "Bearer secret",
    }


def test_hive_meta_log_summary_helpers():
    short_payload = hive_meta_http._meta_payload_summary({"orgCode": "org_1", "table_names": ["a", "b"]})
    assert short_payload["table_count"] == 2
    assert short_payload["table_names"] == ["a", "b"]

    long_names = [f"t{i}" for i in range(12)]
    long_payload = hive_meta_http._meta_payload_summary({"table_names": long_names})
    assert long_payload["table_count"] == 12
    assert long_payload["table_names"] == long_names[:10]

    assert hive_meta_http._meta_response_summary(["bad"]) == {"payload_type": "list"}
    assert hive_meta_http._meta_response_summary({"code": 20000, "data": {"tables": [1, 2]}}) == {
        "code": 20000,
        "table_count": 2,
    }
    assert hive_meta_http._meta_response_summary({"message": "ok", "tables": []}) == {
        "msg": "ok",
        "table_count": 0,
    }


def test_hive_meta_table_and_schema_normalization():
    tables = hive_meta_http._normalize_table_items([
        {"table_name": "orders", "table_description": "订单表"},
        {"table_name": "users"},
    ])
    assert tables == [
        {"table_name": "orders", "description": "订单表"},
        {"table_name": "users", "description": ""},
    ]

    schema = hive_meta_http._normalize_schema_tables([
        {
            "table_name": "orders",
            "columns": [
                {"name": "order_id", "type": "string", "description": "订单ID", "is_partition": False},
                {"name": "dt", "type": "string", "is_partition": True},
            ],
        }
    ], {"orders": "订单表"})

    assert schema[0]["description"] == "订单表"
    assert schema[0]["columns"][0] == {
        "name": "order_id",
        "type": "string",
        "hive_type": "string",
        "description": "订单ID",
        "primary_key": False,
        "nullable": True,
        "is_partition": False,
    }
    assert schema[0]["columns"][1]["description"] == "dt"

    invalid_payloads = [
        ([1], "第 1 项不是对象"),
        ([{"table_name": "", "columns": []}], "缺少 table_name"),
        ([{"table_name": "orders", "columns": {}}], "缺少 columns"),
        ([{"table_name": "orders", "columns": [1]}], "字段不是对象"),
        ([{"table_name": "orders", "columns": [{"name": "", "type": "string", "is_partition": False}]}], "缺少 name"),
        ([{"table_name": "orders", "columns": [{"name": "id", "type": "", "is_partition": False}]}], "缺少 type"),
        ([{"table_name": "orders", "columns": [{"name": "id", "type": "string"}]}], "缺少 is_partition"),
        ([{"table_name": "orders", "columns": [{"name": "id", "type": "string", "is_partition": "false"}]}], "必须是布尔值"),
    ]
    for payload, expected in invalid_payloads:
        with pytest.raises(RuntimeError, match=expected):
            hive_meta_http._normalize_schema_tables(payload)

    with pytest.raises(RuntimeError, match="表列表第 1 项不是对象"):
        hive_meta_http._normalize_table_items([1])
    with pytest.raises(RuntimeError, match="缺少 table_name"):
        hive_meta_http._normalize_table_items([{}])


@pytest.mark.asyncio
async def test_hive_meta_http_fetch_tables_and_schema(monkeypatch):
    calls = []

    async def fake_post_json(url, payload):
        calls.append((url, payload))
        if url == "http://meta/tables":
            return {
                "tables": [
                    {"table_name": "orders", "table_description": "订单表"},
                    {"table_name": "users", "table_description": "用户表"},
                ]
            }
        assert url == "http://meta/schema"
        table_name = payload["table_names"][0]
        return {
            "tables": [
                {
                    "table_name": table_name,
                    "columns": [
                        {"name": f"{table_name}_id", "type": "string", "description": "主键", "is_partition": False},
                    ],
                }
            ]
        }

    monkeypatch.setattr(hive_meta_http, "META_TABLES_URL", "http://meta/tables")
    monkeypatch.setattr(hive_meta_http, "META_SCHEMA_URL", "http://meta/schema")
    monkeypatch.setattr(hive_meta_http, "_post_json", fake_post_json)
    source_config = {"org_code": "org_1", "host": "127.0.0.1", "database": "default"}

    assert await hive_meta_http.get_hive_meta_tables_async(source_config, user_id="user_a", username="测试用户") == ["orders", "users"]
    schema = await hive_meta_http.get_hive_meta_schema_async(source_config, ["users"], user_id="user_a", username="测试用户")

    assert schema[0]["table_name"] == "users"
    assert schema[0]["description"] == "用户表"
    assert schema[0]["columns"][0]["name"] == "users_id"
    assert calls[0][0] == "http://meta/tables"
    assert calls[0][1]["userId"] == "user_a"
    assert calls[0][1]["username"] == "测试用户"
    assert calls[-1][1]["table_names"] == ["users"]
    assert calls[-1][1]["userId"] == "user_a"
    assert calls[-1][1]["username"] == "测试用户"


@pytest.mark.asyncio
async def test_hive_meta_http_fetch_schema_defaults_to_all_tables_and_filters_blank_names(monkeypatch):
    calls = []

    async def fake_post_json(url, payload):
        calls.append((url, payload))
        if url == "http://meta/tables":
            return {"tables": [{"table_name": "orders", "table_description": ""}, {"table_name": "users", "table_description": ""}]}
        table_name = payload["table_names"][0]
        return {
            "tables": [
                {
                    "table_name": table_name,
                    "description": f"{table_name}表",
                    "columns": [{"name": "id", "type": "string", "is_partition": False}],
                }
            ]
        }

    monkeypatch.setattr(hive_meta_http, "META_TABLES_URL", "http://meta/tables")
    monkeypatch.setattr(hive_meta_http, "META_SCHEMA_URL", "http://meta/schema")
    monkeypatch.setattr(hive_meta_http, "_post_json", fake_post_json)

    all_schema = await hive_meta_http.get_hive_meta_schema_async({"org_code": "org_1"}, None)
    assert [table["table_name"] for table in all_schema] == ["orders", "users"]

    calls.clear()
    assert await hive_meta_http.get_hive_meta_schema_async({"org_code": "org_1"}, ["", "  "]) == []
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_hive_meta_http_fetch_rejects_missing_data_tables(monkeypatch):
    async def fake_post_json(_url, _payload):
        return {}

    monkeypatch.setattr(hive_meta_http, "META_TABLES_URL", "http://meta/tables")
    monkeypatch.setattr(hive_meta_http, "META_SCHEMA_URL", "http://meta/schema")
    monkeypatch.setattr(hive_meta_http, "_post_json", fake_post_json)

    with pytest.raises(RuntimeError, match="表列表接口返回缺少"):
        await hive_meta_http.get_hive_meta_tables_async({"org_code": "org_1"})
    with pytest.raises(RuntimeError, match="表列表接口返回缺少"):
        await hive_meta_http.get_hive_meta_schema_async({"org_code": "org_1"}, ["orders"])


@pytest.mark.asyncio
async def test_hive_meta_http_fetch_rejects_schema_missing_data_tables(monkeypatch):
    async def fake_post_json(url, _payload):
        if url == "http://meta/tables":
            return {"tables": [{"table_name": "orders"}]}
        return {}

    monkeypatch.setattr(hive_meta_http, "META_TABLES_URL", "http://meta/tables")
    monkeypatch.setattr(hive_meta_http, "META_SCHEMA_URL", "http://meta/schema")
    monkeypatch.setattr(hive_meta_http, "_post_json", fake_post_json)

    with pytest.raises(RuntimeError, match="表结构接口返回缺少"):
        await hive_meta_http.get_hive_meta_schema_async({"org_code": "org_1"}, ["orders"])


@pytest.mark.asyncio
async def test_hive_meta_http_fetch_rejects_missing_tables(monkeypatch):
    async def fake_post_json(url, _payload):
        if url == "http://meta/tables":
            return {"tables": [{"table_name": "orders"}]}
        return {"tables": []}

    monkeypatch.setattr(hive_meta_http, "META_TABLES_URL", "http://meta/tables")
    monkeypatch.setattr(hive_meta_http, "META_SCHEMA_URL", "http://meta/schema")
    monkeypatch.setattr(hive_meta_http, "_post_json", fake_post_json)

    with pytest.raises(RuntimeError, match="未返回表结构"):
        await hive_meta_http.get_hive_meta_schema_async({"org_code": "org_1"}, ["orders"])


@pytest.mark.asyncio
async def test_hive_meta_post_json_uses_http_client(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            calls.append("raise_for_status")

        def json(self):
            return {"code": 20000, "data": {"tables": []}}

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def __aenter__(self):
            calls.append("enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            calls.append("exit")

        async def post(self, url, json):
            calls.append(("post", url, json))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(hive_meta_http, "HTTP_AUTH_TOKEN", "secret")
    monkeypatch.setattr(hive_meta_http, "META_TIMEOUT_SECONDS", 12)

    result = await hive_meta_http._post_json("http://meta/tables", {"org_code": "org_1"})

    assert result == {"tables": []}
    # 构造层：verify 单点 + timeout + headers；post 层只带 json 载荷
    assert calls[0] == ("init", {
        "verify": False,
        "timeout": 12,
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer secret"},
    })
    assert calls[2] == ("post", "http://meta/tables", {"org_code": "org_1"})
    assert "raise_for_status" in calls


@pytest.mark.asyncio
async def test_hive_meta_post_json_passes_verify_tls_switch(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 20000, "data": {"tables": []}}

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(hive_meta_http, "HTTP_AUTH_TOKEN", "secret")
    monkeypatch.setattr(datasource_credentials, "HTTP_VERIFY_TLS", True)

    await hive_meta_http._post_json("http://meta/tables", {"org_code": "org_1"})

    assert calls[0][1]["verify"] is True


@pytest.mark.asyncio
async def test_hive_meta_post_json_failure_writes_external_api_alarm(monkeypatch):
    alarms = []

    class ErrorClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json):
            raise RuntimeError("meta down")

    monkeypatch.setattr(httpx, "AsyncClient", ErrorClient)
    monkeypatch.setattr(hive_meta_http, "emit_external_api_alarm", lambda *args, **kwargs: alarms.append((args, kwargs)))

    with pytest.raises(RuntimeError, match="meta down"):
        await hive_meta_http._post_json("http://meta/tables", {"org_code": "org_1"})

    assert alarms[0][0][0] == "hive_meta_http"
    assert alarms[0][1]["url"] == "http://meta/tables"
