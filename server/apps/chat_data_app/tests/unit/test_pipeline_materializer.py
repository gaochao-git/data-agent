"""Pipeline 输出物化：DDL/INSERT 生成、Hive 写入和成员选择。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from apps.chat_data_app.services.dataset import pipeline_materializer as materializer


def test_sql_helpers_cover_types_and_literals():
    columns = [
        {"name": "id", "data_type": "Integer"},
        {"name": "amount", "data_type": "Decimal"},
        {"name": "ok", "data_type": "Boolean"},
        {"name": "at", "data_type": "Timestamp"},
        {"name": "name", "data_type": "String"},
    ]
    create_sql = materializer.build_create_table_sql("db", "out", columns)
    assert "`id` int" in create_sql
    assert "`amount` decimal(18,4)" in create_sql
    assert "`at` timestamp" in create_sql
    assert materializer._sql_literal(None) == "NULL"
    assert materializer._sql_literal(True) == "1"
    assert materializer._sql_literal(datetime(2026, 1, 2, 3, 4, 5)) == "'2026-01-02 03:04:05'"
    assert materializer._sql_literal("O'Reilly") == "'O''Reilly'"
    insert_sql = materializer.build_insert_sql(
        "db", "out", [{"name": "id"}, {"name": "name"}], {"id": 1, "name": "张三"}
    )
    assert insert_sql == "INSERT INTO `db`.`out` VALUES (1, '张三')"


def test_identifiers_are_validated():
    with pytest.raises(materializer.PipelineBuildError, match="database"):
        materializer.build_create_table_sql("bad-db", "out", [{"name": "id"}])
    with pytest.raises(materializer.PipelineBuildError, match="column"):
        materializer.build_create_table_sql("db", "out", [{"name": "bad-column"}])
    with pytest.raises(materializer.PipelineBuildError, match="可物化列"):
        materializer.build_create_table_sql("db", "out", [])


def test_materialize_hive_output_rejects_input_table_and_writes(monkeypatch):
    class Cursor:
        def __init__(self):
            self.sql = []

        def execute(self, sql):
            self.sql.append(sql)

    class Connection:
        def __init__(self):
            self.cur = Cursor()
            self.closed = False

        def cursor(self):
            return self.cur

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(materializer, "_connection", lambda _target: connection)
    with pytest.raises(materializer.PipelineBuildError, match="输入 Dataset"):
        materializer.materialize_hive_output(
            {"source_config": {"database": "db"}},
            table="shipments",
            columns=[{"name": "id"}],
            rows=[],
            input_tables={"shipments"},
        )
    table = materializer.materialize_hive_output(
        {"source_config": {"database": "db"}},
        table="out",
        columns=[{"name": "id"}],
        rows=[{"id": 1}],
        input_tables={"shipments"},
    )
    assert table == "out"
    assert connection.closed is True
    assert any(sql.startswith("DROP TABLE IF EXISTS") for sql in connection.cur.sql)
    assert any(sql.startswith("INSERT INTO") for sql in connection.cur.sql)


def test_materialize_output_async_builds_binding(monkeypatch):
    class Manager:
        async def list_authorized_execution_targets_async(self, *_args, **_kwargs):
            return [{"member_datasource_id": "m1", "source_type": "hive", "source_config": {"database": "db"}}]

    monkeypatch.setattr(materializer, "get_datasource_manager", lambda: Manager())
    monkeypatch.setattr(materializer, "materialize_hive_output", lambda *_args, **_kwargs: "out")
    dataset = {"bindings": [{"datasource_group_id": "g1", "member_datasource_id": "m1", "physical_table": "shipments"}]}
    bindings = asyncio.run(
        materializer.materialize_output_async(
            "out",
            [{"name": "id"}],
            [{"id": 1}],
            dataset,
            member_datasource_ids=["m1"],
            user_id="u",
            username="n",
        )
    )
    assert bindings == [{"datasource_group_id": "g1", "member_datasource_id": "m1", "physical_table": "out"}]

    dataset["bindings"][0]["member_datasource_id"] = "m1"
    with pytest.raises(materializer.PipelineBuildError, match="只支持 Hive"):
        monkeypatch.setattr(
            materializer,
            "get_datasource_manager",
            lambda: type(
                "M",
                (),
                {
                    "list_authorized_execution_targets_async": lambda *_a, **_k: _targets(
                        [{"member_datasource_id": "m1", "source_type": "mysql"}]
                    )
                },
            )(),
        )
        asyncio.run(
            materializer.materialize_output_async(
                "out", [{"name": "id"}], [], dataset, member_datasource_ids=["m1"], user_id="u", username="n"
            )
        )


async def _targets(items):
    return items


def test_hive_type_and_date_literal_branches():
    assert materializer._hive_type("bigint") == "bigint"
    assert materializer._hive_type("date") == "date"
    assert materializer._hive_type("boolean") == "boolean"
    assert materializer._hive_type("double") == "double"
    assert materializer._hive_type("weird") == "string"
    assert materializer._sql_literal(__import__("datetime").date(2026, 1, 2)) == "'2026-01-02'"


def test_connection_uses_source_config(monkeypatch):
    captured = {}

    class FakeHive:
        @staticmethod
        def Connection(**kwargs):
            captured.update(kwargs)
            return object()

    import pyhive.hive

    monkeypatch.setattr(pyhive.hive, "Connection", FakeHive.Connection)
    materializer._connection(
        {"source_config": {"host": "h", "port": 10001, "username": "u", "database": "db", "password": "p"}}
    )
    assert captured == {"host": "h", "port": 10001, "username": "u", "database": "db", "password": "p"}


def test_materialize_output_async_rejects_incomplete_binding_and_missing_target(monkeypatch):
    dataset = {"bindings": [{"datasource_group_id": "", "member_datasource_id": "m1"}]}
    with pytest.raises(materializer.PipelineBuildError, match="缺少数据源组"):
        asyncio.run(
            materializer.materialize_output_async(
                "out", [{"name": "id"}], [], dataset, member_datasource_ids=["m1"], user_id="u", username="n"
            )
        )

    class Manager:
        async def list_authorized_execution_targets_async(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(materializer, "get_datasource_manager", lambda: Manager())
    dataset = {"bindings": [{"datasource_group_id": "g1", "member_datasource_id": "m1"}]}
    with pytest.raises(materializer.PipelineBuildError, match="无权执行"):
        asyncio.run(
            materializer.materialize_output_async(
                "out", [{"name": "id"}], [], dataset, member_datasource_ids=["m1"], user_id="u", username="n"
            )
        )
