import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import hive_sync, mysql_sync, query_metadata
from apps.chat_data_app.services.hive_sync import HiveSyncService
from apps.chat_data_app.services.mysql_sync import MySQLSyncService
from utils.models import ChatDataQueryTask, SqlQueryHistory


def test_hive_identifier_validation_and_socket_timeout():
    assert hive_sync._is_valid_hive_identifier("orders") is True
    assert hive_sync._is_valid_hive_identifier("dw.orders") is True
    assert hive_sync._is_valid_hive_identifier("") is False
    assert hive_sync._is_valid_hive_identifier("dw.orders.extra") is False
    assert hive_sync._is_valid_hive_identifier("bad-name") is False
    assert hive_sync._quote_hive_identifier("dw.orders") == "`dw`.`orders`"
    with pytest.raises(ValueError, match="非法表名"):
        hive_sync._quote_hive_identifier("bad-name")

    class Handle:
        def __init__(self):
            self.timeout = None

        def settimeout(self, value):
            self.timeout = value

    class Transport:
        def __init__(self):
            self.timeout_ms = None
            self.handle = Handle()
            self._trans = SimpleNamespace(setTimeout=lambda value: setattr(self, "nested_timeout_ms", value))

        def setTimeout(self, value):
            self.timeout_ms = value

    transport = Transport()
    hive_sync._apply_socket_timeout(transport, 3)

    assert transport.timeout_ms == 3000
    assert transport.handle.timeout == 3
    assert transport.nested_timeout_ms == 3000

    hive_sync._apply_socket_timeout(None, 3)
    hive_sync._apply_socket_timeout(transport, 0)


def test_hive_socket_timeout_ignores_nested_transport_failures():
    class BadHandle:
        def settimeout(self, _value):
            raise RuntimeError("socket fail")

    class BadTransport:
        def __init__(self):
            self.handle = BadHandle()
            self._transport = self

        def setTimeout(self, _value):
            raise RuntimeError("transport fail")

    hive_sync._apply_socket_timeout(BadTransport(), 2)


def test_hive_socket_timeout_skips_none_nested_and_handle_fallbacks():
    class Transport:
        def __init__(self):
            self._trans = None
            self._sock = None
            self.handle = SimpleNamespace(settimeout=lambda value: setattr(self, "handle_timeout", value))

    transport = Transport()
    hive_sync._apply_socket_timeout(transport, 5)
    assert transport.handle_timeout == 5


def test_hive_read_positive_int_env(monkeypatch):
    monkeypatch.delenv("CHATDATA_HIVE_TEST_INT", raising=False)
    assert hive_sync._read_positive_int_env("CHATDATA_HIVE_TEST_INT", 6) == 6
    monkeypatch.setenv("CHATDATA_HIVE_TEST_INT", "bad")
    assert hive_sync._read_positive_int_env("CHATDATA_HIVE_TEST_INT", 6) == 6
    monkeypatch.setenv("CHATDATA_HIVE_TEST_INT", "0")
    assert hive_sync._read_positive_int_env("CHATDATA_HIVE_TEST_INT", 6) == 6
    monkeypatch.setenv("CHATDATA_HIVE_TEST_INT", "8")
    assert hive_sync._read_positive_int_env("CHATDATA_HIVE_TEST_INT", 6) == 8


class FakeHiveCursor:
    def __init__(self, rows_by_sql):
        self.rows_by_sql = rows_by_sql
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)

    def fetchall(self):
        sql = self.executed[-1]
        return self.rows_by_sql[sql]


class FakeHiveConnection:
    def __init__(self, rows_by_sql):
        self.cursor_obj = FakeHiveCursor(rows_by_sql)
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.closed = True


def test_hive_sync_get_connection_applies_credentials_and_timeout(monkeypatch):
    captured = {}

    class Transport:
        def __init__(self):
            self.timeout_ms = None

        def setTimeout(self, value):
            self.timeout_ms = value

    class Connection:
        def __init__(self):
            self._transport = Transport()

    conn = Connection()

    def fake_build_params(**kwargs):
        captured["build_params"] = kwargs
        return {"host": kwargs["host"], "port": kwargs["port"], "database": kwargs["database"], "username": kwargs["username"]}

    def fake_connect(**kwargs):
        captured["connect_params"] = kwargs
        return conn

    monkeypatch.setattr(hive_sync, "build_hive_connect_params", fake_build_params)
    monkeypatch.setattr(hive_sync.hive, "connect", fake_connect)
    monkeypatch.setattr(hive_sync, "SOCKET_TIMEOUT_SECONDS", 4)

    service = HiveSyncService("localhost", port=10001, database="dw", username="hive_user", password="secret")

    assert service._get_connection() is conn
    assert captured["build_params"]["password"] == "secret"
    assert captured["connect_params"] == {"host": "localhost", "port": 10001, "database": "dw", "username": "hive_user"}
    assert conn._transport.timeout_ms == 4000


def test_hive_sync_parses_tables_metadata_and_schema():
    rows_by_sql = {
        "SHOW TABLES": [("orders",), ("users",)],
        "DESCRIBE FORMATTED `orders`": [
            ("order_id", "string", "订单ID"),
            ("amount", "decimal(10,2)", ""),
            ("# Partition Information", "", ""),
            ("dt", "string", "分区日期"),
            ("# Detailed Table Information", "", ""),
            ("", "comment", "订单表"),
        ],
        "DESCRIBE FORMATTED `users`": [
            ("user_id", "string", "用户ID"),
            ("# Detailed Table Information", "", ""),
            ("comment", "用户表", ""),
        ],
    }
    service = HiveSyncService("localhost")
    conn = FakeHiveConnection(rows_by_sql)
    service._get_connection = lambda: conn

    assert service._get_tables(conn) == ["orders", "users"]
    metadata = service._get_table_metadata("orders", conn)
    assert metadata["comment"] == "订单表"
    assert metadata["columns"][0]["description"] == "订单ID"
    assert metadata["columns"][1]["description"] == "amount"
    assert metadata["columns"][2]["is_partition"] is True

    schema = service._get_full_schema(["orders", "users"], max_workers=1)
    assert [table["table_name"] for table in schema] == ["orders", "users"]
    assert schema[0]["description"] == "订单表"
    assert schema[1]["description"] == "用户表"


def test_hive_sync_metadata_parser_skips_hash_and_comment_rows():
    rows_by_sql = {
        "DESCRIBE FORMATTED `orders`": [
            (None, "string", "skip"),
            ("# col_name", "data_type", "comment"),
            ("id", "string", "主键"),
            ("# Partition Information", "", ""),
            ("id", "string", "重复分区"),
            ("dt", "string", "分区"),
            ("# Detailed Table Information", "", ""),
            ("comment", "订单表", ""),
            ("# Storage Information", "", ""),
            ("Serde Library", "serde", ""),
        ],
    }
    conn = FakeHiveConnection(rows_by_sql)
    service = HiveSyncService("localhost")

    metadata = service._get_table_metadata("orders", conn)

    assert metadata["comment"] == "订单表"
    assert [column["name"] for column in metadata["columns"]] == ["id", "dt"]
    assert metadata["columns"][1]["is_partition"] is True


def test_hive_sync_fetches_tables_and_metadata_with_owned_connection():
    rows_by_sql = {
        "SHOW TABLES": [("orders",)],
        "DESCRIBE FORMATTED `orders`": [
            ("", "", ""),
            ("order_id", "string", "订单ID"),
            ("order_id", "string", "重复字段"),
            ("# Storage Information", "", ""),
            ("Serde Library", "x", ""),
        ],
    }
    conn = FakeHiveConnection(rows_by_sql)
    service = HiveSyncService("localhost")
    service._get_connection = lambda: conn

    assert service._get_tables() == ["orders"]
    assert conn.closed is True

    conn.closed = False
    metadata = service._get_table_metadata("orders")
    assert metadata["comment"] == ""
    assert [column["name"] for column in metadata["columns"]] == ["order_id"]
    assert metadata["columns"][0]["description"] == "订单ID"
    assert conn.closed is True


def test_hive_sync_rejects_invalid_table_names_and_metadata_errors():
    service = HiveSyncService("localhost")
    with pytest.raises(ValueError, match="存在非法表名"):
        service._get_full_schema(["bad-name"])

    conn = FakeHiveConnection({"DESCRIBE FORMATTED `orders`": RuntimeError("bad")})

    def bad_fetchall():
        raise RuntimeError("describe failed")

    conn.cursor_obj.fetchall = bad_fetchall
    with pytest.raises(RuntimeError, match="获取表 orders 元数据失败"):
        service._get_table_metadata("orders", conn)


def test_hive_sync_full_schema_empty_and_failed_tables():
    service = HiveSyncService("localhost")
    service._get_tables = lambda: []
    assert service._get_full_schema(None) == []

    service._get_tables = lambda: ["orders"]
    service._get_connection = lambda: FakeHiveConnection({})
    service._get_table_metadata = lambda table_name, conn: (_ for _ in ()).throw(RuntimeError("describe failed"))

    with pytest.raises(RuntimeError, match="失败表: orders"):
        service._get_full_schema(None, max_workers=1)


def test_hive_sync_async_methods_delegate_to_sync_methods():
    service = HiveSyncService("localhost")
    service._get_tables = lambda: ["orders"]
    service._get_full_schema = lambda table_names=None, max_workers=10: [{"table_name": table_names[0], "max_workers": max_workers}]

    assert asyncio.run(service.get_tables_async()) == ["orders"]
    assert asyncio.run(service.get_full_schema_async(["orders"], max_workers=3)) == [{"table_name": "orders", "max_workers": 3}]


@pytest.mark.asyncio
async def test_mysql_sync_positive_int_env_and_empty_schema_rows(monkeypatch):
    monkeypatch.delenv("CHATDATA_MYSQL_TEST_INT", raising=False)
    assert mysql_sync._read_positive_int_env("CHATDATA_MYSQL_TEST_INT", 6) == 6
    monkeypatch.setenv("CHATDATA_MYSQL_TEST_INT", "0")
    assert mysql_sync._read_positive_int_env("CHATDATA_MYSQL_TEST_INT", 6) == 6
    monkeypatch.setenv("CHATDATA_MYSQL_TEST_INT", "9")
    assert mysql_sync._read_positive_int_env("CHATDATA_MYSQL_TEST_INT", 6) == 9

    connections = [
        AsyncMySQLConnection([]),
        AsyncMySQLConnection([{"TABLE_NAME": "orders", "TABLE_COMMENT": "订单表", "COLUMN_NAME": ""}]),
    ]

    async def fake_connect(**_kwargs):
        return connections.pop(0)

    monkeypatch.setattr(mysql_sync.aiomysql, "connect", fake_connect)
    monkeypatch.setattr(mysql_sync.aiomysql, "DictCursor", object())

    service = MySQLSyncService("localhost", database="demo")
    assert await service.get_full_schema_async() == []
    assert await service.get_full_schema_async() == [
        {"table_name": "orders", "description": "订单表", "columns": []}
    ]


def test_lang_util_model_to_dict_json_branches():
    history = SqlQueryHistory(
        datasource_id="ds_a",
        sql_text="select 1",
        query_title="查询",
        query_type="sql",
        builder_config='{"metrics":["销售额"]}',
    )
    assert history.to_dict()["builder_config"] == {"metrics": ["销售额"]}
    history.builder_config = "{bad"
    assert history.to_dict()["builder_config"] is None
    history.builder_config = ""
    assert "builder_config" not in history.to_dict()

    task = ChatDataQueryTask(
        task_id="task_1",
        task_type="sql",
        datasource_id="ds_a",
        query_type="sql",
        task_status="succeeded",
        result_payload='{"success":true}',
        request_payload='{"sql":"select 1"}',
    )
    payload = task.to_dict()
    assert payload["result_payload"] == {"success": True}
    assert payload["request_payload"] == {"sql": "select 1"}
    task.result_payload = "{bad"
    task.request_payload = "{bad"
    bad_payload = task.to_dict()
    assert bad_payload["result_payload"] is None
    assert bad_payload["request_payload"] is None
    task.result_payload = ""
    task.request_payload = ""
    empty_payload = task.to_dict()
    assert empty_payload["result_payload"] is None
    assert empty_payload["request_payload"] is None


class AsyncCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchall(self):
        return self.rows


class AsyncMySQLConnection:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.ensure_closed_called = False
        self.cursor_obj = AsyncCursor(rows)

    def cursor(self, _cursor_type):
        return self.cursor_obj

    def close(self):
        self.closed = True

    async def ensure_closed(self):
        self.ensure_closed_called = True


@pytest.mark.asyncio
async def test_mysql_sync_fetches_tables_and_schema(monkeypatch):
    table_conn = AsyncMySQLConnection([{"Tables_in_demo": "orders"}, {"Tables_in_demo": "users"}])
    schema_conn = AsyncMySQLConnection([
        {
            "TABLE_NAME": "orders",
            "TABLE_COMMENT": "订单表",
            "COLUMN_NAME": "order_id",
            "COLUMN_TYPE": "bigint",
            "COLUMN_COMMENT": "订单ID",
            "COLUMN_KEY": "PRI",
            "IS_NULLABLE": "NO",
        },
        {
            "TABLE_NAME": "orders",
            "TABLE_COMMENT": "订单表",
            "COLUMN_NAME": "amount",
            "COLUMN_TYPE": "decimal(10,2)",
            "COLUMN_COMMENT": "",
            "COLUMN_KEY": "MUL",
            "IS_NULLABLE": "YES",
        },
        {
            "TABLE_NAME": "users",
            "TABLE_COMMENT": "",
            "COLUMN_NAME": "user_id",
            "COLUMN_TYPE": "bigint",
            "COLUMN_COMMENT": "用户ID",
            "COLUMN_KEY": "UNI",
            "IS_NULLABLE": "NO",
        },
    ])
    connections = [table_conn, schema_conn]

    async def fake_connect(**kwargs):
        assert kwargs["connect_timeout"] == mysql_sync.CONNECT_TIMEOUT_SECONDS
        return connections.pop(0)

    monkeypatch.setattr(mysql_sync.aiomysql, "connect", fake_connect)
    monkeypatch.setattr(mysql_sync.aiomysql, "DictCursor", object())
    service = MySQLSyncService("localhost", database="demo")

    assert await service.get_tables_async() == ["orders", "users"]
    schema = await service.get_full_schema_async(["orders"])

    assert schema == [{
        "table_name": "orders",
        "description": "订单表",
        "columns": [
            {
                "name": "order_id",
                "type": "bigint",
                "mysql_type": "bigint",
                "description": "订单ID",
                "primary_key": True,
                "unique_key": False,
                "index_key": False,
                "nullable": False,
            },
            {
                "name": "amount",
                "type": "decimal(10,2)",
                "mysql_type": "decimal(10,2)",
                "description": "amount",
                "primary_key": False,
                "unique_key": False,
                "index_key": True,
                "nullable": True,
            },
        ],
    }]
    assert table_conn.closed is True
    assert schema_conn.ensure_closed_called is True


@pytest.mark.asyncio
async def test_query_metadata_generates_json_and_falls_back(monkeypatch):
    class FakeLLM:
        def __init__(self, content):
            self.content = content

        async def ainvoke(self, prompt):
            assert "select count(*) from orders" in prompt
            return SimpleNamespace(content=self.content)

    monkeypatch.setattr(query_metadata, "get_chat_data_llm", lambda streaming=False: FakeLLM('```json\n{"title":"订单统计","description":"统计订单数量","category":"订单分析"}\n```'))
    metadata = await query_metadata.generate_query_metadata("select count(*) from orders")
    assert metadata == {"title": "订单统计", "description": "统计订单数量", "category": "订单分析"}
    assert query_metadata.assign_card_style("订单分析")["icon"] == "fa-shopping-cart"
    assert query_metadata.assign_card_style("不存在") == query_metadata.CATEGORY_STYLES["其他"]

    monkeypatch.setattr(query_metadata, "get_chat_data_llm", lambda streaming=False: FakeLLM('{"title":"缺字段"}'))
    assert await query_metadata.generate_query_metadata("select count(*) from orders") == {
        "title": "SQL查询",
        "description": "数据查询",
        "category": "其他",
    }
