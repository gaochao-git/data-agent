"""本体 Action L2 真实 SQL 闭环测试（内存 SQLite，不走 fake DAO）。

与 `test_ontology_action_type.py` 的鸭子类型单测互补：这里把**整张表建出来**，
让执行审计、覆盖层、实例代际、撤销都跑真实 ORM/SQL，验证：

- 新增列（`baseline_json` / `conflict_check_level` / `revert_of_execution_id` /
  `object_generation` / `applied_at` / `conflict_resolution`）能被真实建表语句创建，
  且满足启动自检（`schema_check.missing_schema_issues`）；
- 部署文档 `next_version.md` 的 DDL 与模型列名一致（防止「文档写了、模型没写」或列宽不符）；
- 写读链路：代际过滤、latest edit、软删/恢复/重建、reverted 标记；
- 端到端：dry_run → 带 baseline 提交 → 读回覆盖值 → revert → 读回原值。

边界：业务数据源表用 SQLite `orders` 模拟，`read_object_projection` 的
Dataset/member binding 解析仍由轻量桩替代（真实链路需 MySQL + 数据源绑定配置）。
**所有本体侧的写与读都是真实 SQL。**
"""

from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.daos import dao_session
from apps.chat_data_app.daos.ontology_action_execution_dao import (
    ActionExecutionOptions,
    OntologyActionExecutionDao,
)
from apps.chat_data_app.daos.ontology_action_object_dao import OntologyActionObjectDao
from apps.chat_data_app.daos.ontology_object_instance_dao import OntologyObjectInstanceDao
from apps.chat_data_app.services.ontology import schema_check
from utils.models import Base, ChatDataOntologyObjectType

OBJECT_TYPE_ID = 7
OBJECT_KEY = "sales_order:ORD2024000001"


@pytest.fixture()
def db(monkeypatch):
    """一个真实的内存 SQLite 库；把 dao_session.get_session 指过去，事务语义保持原样。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                text("CREATE TABLE orders (order_no TEXT PRIMARY KEY, pay_status TEXT, amount TEXT)")
            )
            await connection.execute(text("INSERT INTO orders VALUES ('ORD2024000001', 'unpaid', '100')"))

    asyncio.run(create_tables())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_session():
        active = dao_session.get_active_session()
        if active is not None:  # 事务内复用同一会话（与生产 get_session 行为一致）
            yield active
            return
        async with factory() as session:
            tokens = dao_session._ACTIVE_SESSION.set(session), None
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                dao_session._ACTIVE_SESSION.reset(tokens[0])

    monkeypatch.setattr(dao_session, "get_session", get_session)
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED", "1")
    return engine, factory


def _seed_object_type(factory):
    async def _run():
        async with factory() as session:
            session.add(
                ChatDataOntologyObjectType(
                    id=OBJECT_TYPE_ID,
                    object_type_name="sales_order",
                    display_name="销售订单",
                    lifecycle_status="certified",
                    write_mode="action_writable",
                    conflict_resolution="apply_user_edits",
                )
            )
            await session.commit()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# DDL / 启动自检一致性
# ---------------------------------------------------------------------------


def _sqlite_columns(engine) -> dict[str, set[str]]:
    def _read(sync_conn):
        inspector = inspect(sync_conn)
        return {table: {col["name"] for col in inspector.get_columns(table)} for table in inspector.get_table_names()}

    return asyncio.run(_inspect(engine, _read))


async def _inspect(engine, reader):
    async with engine.begin() as connection:
        return await connection.run_sync(reader)


def test_created_schema_passes_startup_check(db):
    """真实建表后的列集合能通过启动自检（本体开启 + Action 执行开启）。"""
    engine, _ = db
    columns = _sqlite_columns(engine)
    assert schema_check.missing_schema_issues(columns, action_execute_enabled=True) == []
    assert {"object_generation", "applied_at"} <= columns["chat_data_ontology_object_edit"]
    assert {"baseline_json", "conflict_check_level", "revert_of_execution_id"} <= columns[
        "chat_data_ontology_action_execution"
    ]
    assert {"conflict_resolution", "conflict_timestamp_property"} <= columns["chat_data_ontology_object_type"]


def _ddl_text() -> str:
    return (Path(__file__).resolve().parents[2] / "next_version.md").read_text(encoding="utf-8")


def _ddl_declared_columns(table: str, text_ddl: str) -> set[str]:
    """从「CREATE TABLE + ADD COLUMN + CHANGE COLUMN」推导出文档声明的最终列集合。"""
    declared: set[str] = set()
    for block in re.finditer(r"CREATE TABLE `" + re.escape(table) + r"` \((.*?)\n\) ENGINE", text_ddl, re.S):
        declared |= set(re.findall(r"^\s*`(\w+)`", block.group(1), re.M))
    for stmt in re.finditer(r"ALTER TABLE `" + re.escape(table) + r"`(.*?);", text_ddl, re.S):
        declared |= set(re.findall(r"ADD COLUMN `(\w+)`", stmt.group(1)))
        for old, new in re.findall(r"CHANGE COLUMN `(\w+)`\s+`(\w+)`", stmt.group(1)):
            declared.discard(old)
            declared.add(new)
    return declared


def test_action_ddl_columns_match_orm_exactly(db):
    """全量比对：文档 DDL 声明的列必须覆盖 ORM 映射的每一列。

    ORM 默认 SELECT 全部映射列，任何一列在部署库缺失都会在**运行期**才炸；
    历史事故正是「代码依赖 write_mode、环境没执行 DDL、静默降级到运行时」。
    本用例把「文档写了没」变成可回归的断言，曾被它抓出 applied_at /
    isolation_level / reverted_by / reverted_at 四处漏写。
    """
    engine, _ = db
    runtime_columns = _sqlite_columns(engine)
    text_ddl = _ddl_text()
    for table in (
        "chat_data_ontology_object_edit",
        "chat_data_ontology_action_execution",
        "chat_data_ontology_object_instance",
        "chat_data_ontology_action_type",
        "chat_data_ontology_action_object",
    ):
        declared = _ddl_declared_columns(table, text_ddl)
        missing = set(runtime_columns[table]) - declared
        assert not missing, f"部署 DDL 缺列 {table}: {sorted(missing)}"
        assert "CREATE TABLE `" + table + "`" in text_ddl, f"文档缺 {table} 建表语句"


def test_new_object_type_columns_and_width_in_ddl(db):
    """对象类型表历史迁移多（domain_id→ontology_id 走 CHANGE），只校验本轮新增两列与列宽。"""
    engine, _ = db
    columns = _sqlite_columns(engine)
    text_ddl = _ddl_text()
    for column in ("conflict_resolution", "conflict_timestamp_property"):
        assert column in columns["chat_data_ontology_object_type"]
        assert re.search(rf"ADD COLUMN `{column}`", text_ddl), f"部署 DDL 缺 ADD COLUMN {column}"
    # 列宽必须与模型一致（曾出现 DDL varchar(64) vs ORM String(128)）
    width = re.search(r"ADD COLUMN `conflict_timestamp_property` varchar\((\d+)\)", text_ddl)
    assert width and int(width.group(1)) == 128, "DDL 列宽与 ORM String(128) 不一致"


# ---------------------------------------------------------------------------
# 执行审计 / 覆盖层 DAO：真实 SQL
# ---------------------------------------------------------------------------


def _execution_dao():
    return OntologyActionExecutionDao()


def _instance_dao():
    return OntologyObjectInstanceDao()


def test_execution_round_trip_persists_baseline_and_fields(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _execution_dao()
    baseline = {
        "object_key": OBJECT_KEY,
        "object_generation": 1,
        "row_hash": "sha256:object:abc",
        "row_hash_scope": "object",
        "effective_values": {"pay_status": "unpaid"},
    }
    created = asyncio.run(
        dao.create_execution_async(
            action_type_name="update_order_status",
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            parameters={"status": "paid"},
            edits=[{"operation": "set_property", "property": "pay_status", "resolved_value": "paid"}],
            operator="u1",
            action_type_version=3,
            baseline=baseline,
            options=ActionExecutionOptions(conflict_check_level="object"),
        )
    )
    fetched = asyncio.run(dao.get_execution_async(created["id"]))
    assert fetched["baseline"] == baseline
    assert fetched["conflict_check_level"] == "object"
    assert fetched["action_type_version"] == 3
    assert fetched["create_by"] == "u1"
    assert fetched["revert_of_execution_id"] == 0
    assert fetched["status"] == "succeeded"


def test_append_edit_and_latest_values_respect_generation(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _execution_dao()
    execution = asyncio.run(
        dao.create_execution_async(
            action_type_name="update_order_status",
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            parameters={},
            edits=[],
        )
    )
    eid = execution["id"]
    asyncio.run(
        dao.append_edit_async(
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            property_name="pay_status",
            value="paid",
            action_execution_id=eid,
            action_type_name="update_order_status",
            object_generation=1,
        )
    )
    assert asyncio.run(dao.latest_object_values_async(OBJECT_TYPE_ID, OBJECT_KEY, 1)) == {"pay_status": "paid"}
    # 旧代际不参与合并
    assert asyncio.run(dao.latest_object_values_async(OBJECT_TYPE_ID, OBJECT_KEY, 2)) == {}
    edits = asyncio.run(dao.latest_object_edits_async(OBJECT_TYPE_ID, OBJECT_KEY, 1))
    assert edits["pay_status"]["value"] == "paid"
    assert edits["pay_status"]["execution_id"] == eid
    assert edits["pay_status"]["applied_at"] is not None
    latest = asyncio.run(dao.latest_edit_async(OBJECT_TYPE_ID, OBJECT_KEY, 1))
    assert latest["execution_id"] == eid
    assert latest["property_name"] == "pay_status"


def test_latest_edit_picks_last_write_for_revert_guard(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _execution_dao()
    ids = []
    for status in ("paid", "shipped"):
        execution = asyncio.run(
            dao.create_execution_async(
                action_type_name="update_order_status",
                object_type_id=OBJECT_TYPE_ID,
                object_key=OBJECT_KEY,
                parameters={},
                edits=[],
            )
        )
        ids.append(execution["id"])
        asyncio.run(
            dao.append_edit_async(
                object_type_id=OBJECT_TYPE_ID,
                object_key=OBJECT_KEY,
                property_name="pay_status",
                value=status,
                action_execution_id=execution["id"],
                action_type_name="update_order_status",
            )
        )
    assert asyncio.run(dao.latest_object_values_async(OBJECT_TYPE_ID, OBJECT_KEY, 1)) == {"pay_status": "shipped"}
    latest = asyncio.run(dao.latest_edit_async(OBJECT_TYPE_ID, OBJECT_KEY, 1))
    assert latest["execution_id"] == ids[-1], "撤销必须用最后一条编辑判定，不能按属性取"


def test_overridden_keys_ignores_stale_generation(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _execution_dao()
    execution = asyncio.run(
        dao.create_execution_async(
            action_type_name="update_order_status",
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            parameters={},
            edits=[],
        )
    )
    asyncio.run(
        dao.append_edit_async(
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            property_name="pay_status",
            value="paid",
            action_execution_id=execution["id"],
            action_type_name="update_order_status",
            object_generation=1,
        )
    )
    assert asyncio.run(dao.overridden_keys_async(OBJECT_TYPE_ID, [OBJECT_KEY])) == {OBJECT_KEY}
    assert asyncio.run(dao.overridden_keys_async(OBJECT_TYPE_ID, [OBJECT_KEY], {OBJECT_KEY: 2})) == set()


def test_mark_reverted_keeps_row_and_records_operator(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _execution_dao()
    execution = asyncio.run(
        dao.create_execution_async(
            action_type_name="update_order_status",
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            parameters={},
            edits=[],
        )
    )
    assert asyncio.run(dao.mark_reverted_async(execution["id"], operator="u1")) is True
    fetched = asyncio.run(dao.get_execution_async(execution["id"]))
    assert fetched is not None and fetched["status"] == "reverted"
    assert fetched["reverted_by"] == "u1"
    assert fetched["reverted_at"]
    assert asyncio.run(dao.mark_reverted_async(999999)) is False


def test_revert_execution_row_links_back(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _execution_dao()
    original = asyncio.run(
        dao.create_execution_async(
            action_type_name="update_order_status",
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            parameters={},
            edits=[],
        )
    )
    revert = asyncio.run(
        dao.create_execution_async(
            action_type_name="update_order_status",
            object_type_id=OBJECT_TYPE_ID,
            object_key=OBJECT_KEY,
            parameters={},
            edits=[{"operation": "set_property", "property": "pay_status", "resolved_value": "unpaid"}],
            options=ActionExecutionOptions(conflict_check_level="off", revert_of_execution_id=original["id"]),
        )
    )
    fetched = asyncio.run(dao.get_execution_async(revert["id"]))
    assert fetched["revert_of_execution_id"] == original["id"]
    listed = asyncio.run(dao.list_executions_async(action_type_name="update_order_status", object_key=OBJECT_KEY))
    assert [row["id"] for row in listed] == [revert["id"], original["id"]]


# ---------------------------------------------------------------------------
# 实例生命周期 DAO：真实 SQL
# ---------------------------------------------------------------------------


def test_instance_lifecycle_delete_restore_revive(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _instance_dao()
    created = asyncio.run(dao.ensure_active_async(object_type_id=OBJECT_TYPE_ID, object_key=OBJECT_KEY))
    assert created["generation"] == 1 and created["lifecycle_status"] == "active"
    deleted = asyncio.run(dao.mark_deleted_async(object_type_id=OBJECT_TYPE_ID, object_key=OBJECT_KEY, execution_id=11))
    assert deleted["lifecycle_status"] == "deleted" and deleted["generation"] == 1
    assert asyncio.run(dao.deleted_keys_async(OBJECT_TYPE_ID, [OBJECT_KEY])) == {OBJECT_KEY}
    restored = asyncio.run(dao.restore_async(object_type_id=OBJECT_TYPE_ID, object_key=OBJECT_KEY, execution_id=12))
    assert restored["lifecycle_status"] == "active"
    assert restored["generation"] == 1, "撤销删除不是重建，代际不能变"
    revived = asyncio.run(dao.revive_async(object_type_id=OBJECT_TYPE_ID, object_key=OBJECT_KEY, execution_id=13))
    assert revived["generation"] == 2 and revived["lifecycle_status"] == "active"
    assert asyncio.run(dao.deleted_keys_async(OBJECT_TYPE_ID, [OBJECT_KEY])) == set()
    instances = asyncio.run(dao.instances_async(OBJECT_TYPE_ID, [OBJECT_KEY]))
    assert instances[OBJECT_KEY]["last_execution_id"] == 13


def test_instance_unique_key_prevents_duplicate_rows(db):
    _, factory = db
    _seed_object_type(factory)
    dao = _instance_dao()
    first = asyncio.run(dao.ensure_active_async(object_type_id=OBJECT_TYPE_ID, object_key=OBJECT_KEY))
    second = asyncio.run(dao.ensure_active_async(object_type_id=OBJECT_TYPE_ID, object_key=OBJECT_KEY, execution_id=42))
    assert first["id"] == second["id"], "同一 (type, key) 只能有一行实例"


def test_action_object_soft_delete_and_restore(db):
    _, factory = db
    _seed_object_type(factory)
    dao = OntologyActionObjectDao()
    created = asyncio.run(
        dao.create_object_async(
            object_type_id=OBJECT_TYPE_ID,
            object_key="SO-001",
            properties={"memo": "待处理"},
            title_value="SO-001",
            action_execution_id=1,
            action_type_name="create_supervision",
        )
    )
    assert created is not None
    assert asyncio.run(dao.get_object_async(OBJECT_TYPE_ID, "SO-001")) is not None
    assert asyncio.run(dao.soft_delete_object_async(object_type_id=OBJECT_TYPE_ID, object_key="SO-001")) is True
    assert asyncio.run(dao.get_object_async(OBJECT_TYPE_ID, "SO-001")) is None
    assert asyncio.run(dao.get_object_including_deleted_async(OBJECT_TYPE_ID, "SO-001")) is not None
    assert asyncio.run(dao.restore_object_async(object_type_id=OBJECT_TYPE_ID, object_key="SO-001")) is True
    assert asyncio.run(dao.get_object_async(OBJECT_TYPE_ID, "SO-001")) is not None


# ---------------------------------------------------------------------------
# 端到端：定义 → 试运行 → 带基线提交 → 读回 → 撤销 → 读回
# ---------------------------------------------------------------------------


def _detail():
    return {
        "id": OBJECT_TYPE_ID,
        "object_type_name": "sales_order",
        "write_mode": "action_writable",
        "conflict_resolution": "apply_user_edits",
        "conflict_timestamp_property": "",
        "status": "certified",
        "properties": [
            {"property_name": "order_no", "value_type": "String", "is_identifier": True},
            {"property_name": "pay_status", "value_type": "String", "is_title": False},
            {"property_name": "amount", "value_type": "Decimal"},
        ],
    }


def _action_payload():
    return {
        "object_type_name": "sales_order",
        "display_name": "更新支付状态",
        "status": "certified",
        "parameters": [{"name": "status", "type": "String", "required": True}],
        "edits": [
            {
                "operation": "set_property",
                "property": "pay_status",
                "value": {"kind": "parameter", "parameter": "status"},
            }
        ],
    }


def _install_e2e(monkeypatch, factory):
    """只替换「如何定位业务表」；覆盖层合并仍走生产函数 + 真实 SQL。"""
    from apps.chat_data_app.services.ontology import action_type_service as service_module
    from apps.chat_data_app.services.ontology.object_errors import ObjectProjectionError
    from apps.chat_data_app.services.ontology.object_projection import _merge_conflict_values

    detail = _detail()
    execution_dao = OntologyActionExecutionDao()
    instance_dao = OntologyObjectInstanceDao()

    async def fake_target(_name):
        return detail

    async def fake_read_object_projection(object_type_name, object_key, *, with_source_values=False, **_kwargs):
        local_key = str(object_key).split(":")[-1]
        async with dao_session.get_session() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT order_no, pay_status, amount FROM orders WHERE order_no = :key"),
                        {"key": local_key},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ObjectProjectionError(f"对象不存在: {object_key}")
        source = dict(row)
        instance = await instance_dao.get_instance_async(OBJECT_TYPE_ID, OBJECT_KEY)
        if instance and instance.get("lifecycle_status") != "active":
            raise ObjectProjectionError(f"对象不存在: {object_key}")
        generation = int((instance or {}).get("generation") or 1)
        edits = await execution_dao.latest_object_edits_async(OBJECT_TYPE_ID, OBJECT_KEY, generation)
        merged = _merge_conflict_values(detail, source, edits)
        result = {"object_key": OBJECT_KEY, "properties": merged, "object_generation": generation}
        if with_source_values:
            result["source_properties"] = source
        return result

    monkeypatch.setattr(service_module, "_target_object_type", fake_target)
    monkeypatch.setattr(service_module, "read_object_projection", fake_read_object_projection)
    return service_module, fake_read_object_projection


def _count(factory, table):
    async def _run():
        async with factory() as session:
            return (await session.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()

    return asyncio.run(_run())


def _service():
    from apps.chat_data_app.services.ontology.action_type_service import OntologyActionTypeService

    return OntologyActionTypeService()


def test_e2e_dry_run_execute_read_back_revert_read_back(db, monkeypatch):
    """完整闭环：试运行出基线 → 带基线提交 → 覆盖值生效 → 撤销 → 回到源值。

    除了「业务表怎么定位」用 SQLite 桩，Action 定义、执行审计、覆盖层、实例、
    读时合并、撤销全部是真实 SQL 与生产代码路径。
    """
    _, factory = db
    _seed_object_type(factory)
    service_module, read_object = _install_e2e(monkeypatch, factory)
    service = _service()

    asyncio.run(service.upsert_action_type_async("update_order_status", _action_payload(), operator="u1"))
    assert _count(factory, "chat_data_ontology_action_execution") == 0

    # 1) 试运行：只算不写，给出 proposed_changes 与基线
    preview = asyncio.run(
        service.test_run_action_type_async(
            "update_order_status",
            {"status": "paid"},
            object_key="ORD2024000001",
            operator="u1",
        )
    )
    assert preview["executed"] is False
    assert (preview.get("baseline") or {}).get("row_hash"), "试运行必须返回 baseline.row_hash"
    assert preview["proposed_changes"]["objects"][0]["property_changes"][0]["current"] == "unpaid"
    assert _count(factory, "chat_data_ontology_action_execution") == 0
    assert _count(factory, "chat_data_ontology_object_edit") == 0

    # 2) 带基线提交
    result = asyncio.run(
        service.execute_action_type_async(
            "update_order_status",
            object_key="ORD2024000001",
            parameters={"status": "paid"},
            operator="u1",
            expected_baseline=preview["baseline"]["row_hash"],
        )
    )
    execution_id = result["execution"]["id"]
    assert _count(factory, "chat_data_ontology_action_execution") == 1
    assert _count(factory, "chat_data_ontology_object_edit") == 1
    stored = asyncio.run(OntologyActionExecutionDao().get_execution_async(execution_id))
    assert stored["baseline"]["effective_values"] == {"pay_status": "unpaid"}
    assert stored["conflict_check_level"] == "object"

    # 3) 读回覆盖值（走生产 _merge_conflict_values + 真实覆盖层 SQL）
    after_write = asyncio.run(read_object("sales_order", "ORD2024000001"))
    assert after_write["properties"]["pay_status"] == "paid"
    assert after_write["object_generation"] == 1

    # 4) 撤销：追加反向编辑
    revert = asyncio.run(service.revert_execution_async("update_order_status", execution_id, operator="u1"))
    assert revert["reverted_execution_id"] == execution_id
    assert revert["reversed_edits"][0]["resolved_value"] == "unpaid"
    assert _count(factory, "chat_data_ontology_action_execution") == 2
    assert _count(factory, "chat_data_ontology_object_edit") == 2, "撤销必须追加而非删除"
    original = asyncio.run(OntologyActionExecutionDao().get_execution_async(execution_id))
    assert original["status"] == "reverted" and original["reverted_by"] == "u1"
    revert_row = asyncio.run(OntologyActionExecutionDao().get_execution_async(revert["execution"]["id"]))
    assert revert_row["revert_of_execution_id"] == execution_id

    # 5) 读回执行前的值
    after_revert = asyncio.run(read_object("sales_order", "ORD2024000001"))
    assert after_revert["properties"]["pay_status"] == "unpaid"


def test_e2e_stale_baseline_cancels_whole_edit_without_writes(db, monkeypatch):
    """试运行后源行被改 → 带旧基线提交整单取消，零执行、零覆盖写入。"""
    _, factory = db
    _seed_object_type(factory)
    service_module, _ = _install_e2e(monkeypatch, factory)
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _action_payload(), operator="u1"))
    preview = asyncio.run(
        service.test_run_action_type_async(
            "update_order_status", {"status": "paid"}, object_key="ORD2024000001", operator="u1"
        )
    )

    async def bump_source():
        async with factory() as session:
            await session.execute(text("UPDATE orders SET amount = '200' WHERE order_no = 'ORD2024000001'"))
            await session.commit()

    asyncio.run(bump_source())

    from apps.chat_data_app.services.ontology.errors import OntologyInvalidError

    with pytest.raises(OntologyInvalidError, match="写写冲突"):
        asyncio.run(
            service.execute_action_type_async(
                "update_order_status",
                object_key="ORD2024000001",
                parameters={"status": "paid"},
                operator="u1",
                expected_baseline=preview["baseline"]["row_hash"],
            )
        )
    assert _count(factory, "chat_data_ontology_action_execution") == 0
    assert _count(factory, "chat_data_ontology_object_edit") == 0
    assert _count(factory, "chat_data_ontology_object_instance") == 0


def test_e2e_revert_rejects_second_attempt(db, monkeypatch):
    """撤销后原执行已是 reverted，不能对同一执行再撤一次。"""
    _, factory = db
    _seed_object_type(factory)
    _, read_object = _install_e2e(monkeypatch, factory)
    service = _service()
    asyncio.run(service.upsert_action_type_async("update_order_status", _action_payload(), operator="u1"))
    preview = asyncio.run(
        service.test_run_action_type_async(
            "update_order_status", {"status": "paid"}, object_key="ORD2024000001", operator="u1"
        )
    )
    executed = asyncio.run(
        service.execute_action_type_async(
            "update_order_status",
            object_key="ORD2024000001",
            parameters={"status": "paid"},
            operator="u1",
            expected_baseline=preview["baseline"]["row_hash"],
        )
    )
    eid = executed["execution"]["id"]
    asyncio.run(service.revert_execution_async("update_order_status", eid, operator="u1"))

    from apps.chat_data_app.services.ontology.errors import OntologyInvalidError

    with pytest.raises(OntologyInvalidError, match="只有 succeeded"):
        asyncio.run(service.revert_execution_async("update_order_status", eid, operator="u1"))
    assert asyncio.run(read_object("sales_order", "ORD2024000001"))["properties"]["pay_status"] == "unpaid"


def test_startup_self_check_passes_on_real_schema(db, monkeypatch):
    """模拟服务启动：本体与 Action 开关全开时，真实 schema 能通过启动自检。

    这是对「DDL 没执行 → 探测失败静默降级」历史事故的正面回归。
    """
    engine, _ = db
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")
    monkeypatch.setattr(schema_check, "get_engine", lambda: engine)
    asyncio.run(schema_check.require_ontology_schema_async())


def _delete_action_payload():
    return {
        "object_type_name": "sales_order",
        "display_name": "删除订单",
        "status": "certified",
        "parameters": [{"name": "reason", "type": "String", "required": False}],
        "edits": [{"operation": "delete_object"}],
    }


def test_e2e_delete_two_step_then_revert_restores_visibility(db, monkeypatch):
    """删除也走两步提交：基线生效、对象不可见、撤销后恢复可见且代际不变。"""
    _, factory = db
    _seed_object_type(factory)
    _, read_object = _install_e2e(monkeypatch, factory)
    service = _service()
    asyncio.run(service.upsert_action_type_async("delete_order", _delete_action_payload(), operator="u1"))
    preview = asyncio.run(
        service.test_run_action_type_async("delete_order", {}, object_key="ORD2024000001", operator="u1")
    )
    assert (preview.get("baseline") or {}).get("row_hash"), "删除的试运行也要返回基线"
    executed = asyncio.run(
        service.execute_action_type_async(
            "delete_order",
            object_key="ORD2024000001",
            parameters={},
            operator="u1",
            expected_baseline=preview["baseline"]["row_hash"],
        )
    )
    execution_id = executed["execution"]["id"]
    instance = asyncio.run(OntologyObjectInstanceDao().get_instance_async(OBJECT_TYPE_ID, OBJECT_KEY))
    assert instance["lifecycle_status"] == "deleted" and instance["generation"] == 1

    from apps.chat_data_app.services.ontology.object_errors import ObjectProjectionError

    with pytest.raises(ObjectProjectionError):
        asyncio.run(read_object("sales_order", "ORD2024000001"))
    assert _count(factory, "chat_data_ontology_object_edit") == 0, "删除不是编辑，不写覆盖值"

    revert = asyncio.run(service.revert_execution_async("delete_order", execution_id, operator="u1"))
    assert revert["operation"] == "delete_object"
    restored = asyncio.run(read_object("sales_order", "ORD2024000001"))
    assert restored["properties"]["pay_status"] == "unpaid"
    instance = asyncio.run(OntologyObjectInstanceDao().get_instance_async(OBJECT_TYPE_ID, OBJECT_KEY))
    assert instance["lifecycle_status"] == "active" and instance["generation"] == 1


def test_e2e_delete_stale_baseline_is_rejected(db, monkeypatch):
    """删除提交同样受写写检测保护：试运行后源行被改则整单取消。"""
    _, factory = db
    _seed_object_type(factory)
    _install_e2e(monkeypatch, factory)
    service = _service()
    asyncio.run(service.upsert_action_type_async("delete_order", _delete_action_payload(), operator="u1"))
    preview = asyncio.run(
        service.test_run_action_type_async("delete_order", {}, object_key="ORD2024000001", operator="u1")
    )

    async def bump_source():
        async with factory() as session:
            await session.execute(text("UPDATE orders SET amount = '999' WHERE order_no = 'ORD2024000001'"))
            await session.commit()

    asyncio.run(bump_source())

    from apps.chat_data_app.services.ontology.errors import OntologyInvalidError

    with pytest.raises(OntologyInvalidError, match="写写冲突"):
        asyncio.run(
            service.execute_action_type_async(
                "delete_order",
                object_key="ORD2024000001",
                parameters={},
                operator="u1",
                expected_baseline=preview["baseline"]["row_hash"],
            )
        )
    assert _count(factory, "chat_data_ontology_object_instance") == 0
