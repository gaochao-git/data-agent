"""全局枚举 DAO 层单测（内存 SQLite，真实 SQL 路径）。

覆盖 list/get/domains 过滤排序、upsert 三态（插入/复活软删行/拒绝覆盖）、
软删、引用两步法（LIKE 粗筛 + 应用层精确确认）与批量计数。service 层
鸭子类型单测见 test_meta_global_enum.py。
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.daos import meta_global_enum_dao as dao_module
from apps.chat_data_app.daos.meta_global_enum_dao import MetaGlobalEnumDao
from utils.models import Base, ChatDataMetaGlobalEnum, DatasourceColumnDict, DatasourceGroup


@pytest.fixture()
def harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_test_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(dao_module, "session_scope", get_test_session)

    def seed(*objects):
        """同步播种：在事件循环里写库并提交。"""

        async def run():
            async with session_factory() as session:
                for obj in objects:
                    session.add(obj)
                await session.commit()

        asyncio.run(run())

    return dao_module, MetaGlobalEnumDao(), seed


def _seed_enum(domain="", name="pay_status", desc="支付状态", items=None, delete_flag=0):
    return ChatDataMetaGlobalEnum(
        business_domain=domain,
        enum_name=name,
        enum_desc=desc,
        enum_json=json.dumps(items if items is not None else [{"value": "1", "label": "成功"}], ensure_ascii=False),
        delete_flag=delete_flag,
        create_time=datetime.now(),
        update_time=datetime.now(),
    )


def _column_dict(group_id, table, column, enum_ref, delete_flag=0):
    return DatasourceColumnDict(
        group_id=group_id,
        table_name=table,
        column_name=column,
        dict_json=json.dumps({"enum_ref": enum_ref}, ensure_ascii=False) if enum_ref is not None else "not-json{",
        delete_flag=delete_flag,
    )


def test_list_enums_filters_and_orders(harness):
    dao_module, dao, seed = harness
    seed(
        _seed_enum(domain="订单", name="order_status"),
        _seed_enum(domain="", name="pay_status"),
        _seed_enum(domain="订单", name="deleted_one", delete_flag=1),
    )

    rows = asyncio.run(dao.list_enums_async())
    assert [(r["business_domain"], r["enum_name"]) for r in rows] == [("", "pay_status"), ("订单", "order_status")]
    assert rows[0]["enum_items"] == [{"value": "1", "label": "成功"}]

    filtered = asyncio.run(dao.list_enums_async(keyword="pay"))
    assert [r["enum_name"] for r in filtered] == ["pay_status"]

    by_domain = asyncio.run(dao.list_enums_async(business_domain="订单"))
    assert [r["enum_name"] for r in by_domain] == ["order_status"]


def test_get_enum_by_name_hit_miss_and_blank(harness):
    dao_module, dao, seed = harness
    seed(_seed_enum(domain="订单", name="order_status"))

    assert asyncio.run(dao.get_enum_by_name_async("order_status", "订单"))["enum_desc"] == "支付状态"
    assert asyncio.run(dao.get_enum_by_name_async("order_status", "")) is None
    assert asyncio.run(dao.get_enum_by_name_async("", "订单")) is None


def test_list_enum_domains_dedupes_and_skips_blank(harness):
    dao_module, dao, seed = harness
    seed(
        _seed_enum(domain="订单", name="a"),
        _seed_enum(domain="订单", name="b"),
        _seed_enum(domain="", name="c"),
        _seed_enum(domain="已删域", name="d", delete_flag=1),
    )

    # 空串（通用域）被 DAO 有意过滤，仅返回非空业务域的去重排序清单
    assert asyncio.run(dao.list_enum_domains_async()) == ["订单"]


def test_upsert_enum_insert_revive_and_reject(harness):
    dao_module, dao, seed = harness
    items = [{"value": "2", "label": "失败"}]

    inserted = asyncio.run(
        dao.upsert_enum_async("订单", "order_status", "新描述", items, allow_overwrite=False, create_by="u1")
    )
    assert inserted["enum_desc"] == "新描述" and inserted["enum_items"] == items

    # 活跃行 + 不允许覆盖 → None
    assert asyncio.run(dao.upsert_enum_async("订单", "order_status", "改", [], allow_overwrite=False)) is None

    # 覆盖更新
    updated = asyncio.run(dao.upsert_enum_async("订单", "order_status", "改描述", items, allow_overwrite=True))
    assert updated["enum_desc"] == "改描述"
    refreshed = asyncio.run(dao.get_enum_by_name_async("order_status", "订单"))
    assert refreshed["enum_desc"] == "改描述" and refreshed["update_by"] == ""

    # 软删后 upsert → 复活复用原行（不新增）
    asyncio.run(dao.soft_delete_enum_async("order_status", "订单"))
    revived = asyncio.run(dao.upsert_enum_async("订单", "order_status", "复活", items, allow_overwrite=False))
    assert revived["enum_desc"] == "复活"
    rows = asyncio.run(dao.list_enums_async(business_domain="订单"))
    assert [r["enum_name"] for r in rows] == ["order_status"] and len(rows) == 1


def test_soft_delete_noop_on_blank_name(harness):
    dao_module, dao, seed = harness
    asyncio.run(dao.soft_delete_enum_async("", "订单"))  # 不抛异常即可


def test_find_column_dict_refs_two_step(harness):
    dao_module, dao, seed = harness
    seed(
        _column_dict("g1", "orders", "status", "订单:order_status"),
        _column_dict("g2", "refunds", "status", "pay_status"),
        _column_dict("g3", "logs", "level", "订单:other_enum"),
        _column_dict("g4", "trash", "status", "订单:order_status", delete_flag=1),
        DatasourceGroup(group_id="g1", group_name="订单组", template_source="", delete_flag=0),
    )

    refs = asyncio.run(dao.find_column_dict_refs_async("order_status", "订单"))
    assert len(refs) == 1
    assert refs[0]["group_id"] == "g1" and refs[0]["group_name"] == "订单组"

    generic = asyncio.run(dao.find_column_dict_refs_async("pay_status"))
    assert [(r["group_id"], r["table_name"]) for r in generic] == [("g2", "refunds")]
    assert generic[0]["group_name"] == "g2"  # 组不存在回退为 group_id

    assert asyncio.run(dao.find_column_dict_refs_async("")) == []


def test_count_column_dict_refs_batch(harness):
    dao_module, dao, seed = harness
    seed(
        _column_dict("g1", "orders", "status", "订单:order_status"),
        _column_dict("g2", "refunds", "status", "pay_status"),
        _column_dict("g3", "broken", "x", None),  # dict_json 非法 JSON，计数时跳过
    )

    counts = asyncio.run(
        dao.count_column_dict_refs_batch_async([("订单", "order_status"), ("", "pay_status"), ("", "missing")])
    )
    assert counts == {("订单", "order_status"): 1, ("", "pay_status"): 1, ("", "missing"): 0}
    assert asyncio.run(dao.count_column_dict_refs_batch_async([("", "")])) == {}


# ---------------------------------------------------------------------------
# 纯函数错误分支 + 空集短路（覆盖率补充）
# ---------------------------------------------------------------------------


def test_parse_enum_json_error_branches():
    """list 直返 / 空串 / 非法 JSON / 非 list JSON / 其它类型 → 空列表。"""
    parse = dao_module._parse_enum_json
    assert parse([{"value": "1", "label": "成功"}]) == [{"value": "1", "label": "成功"}]
    assert parse("   ") == []
    assert parse("not-json{") == []
    assert parse('{"a": 1}') == []  # 合法 JSON 但非 list
    assert parse(123) == []


def test_row_refs_enum_error_branches():
    """dict_json 非法 / 非 dict 载荷 → 视为不引用。"""
    row = SimpleNamespace(dict_json="not-json{")
    assert dao_module._row_refs_enum(row, "pay_status", "") is False
    row2 = SimpleNamespace(dict_json='{"k": "v"}')
    assert dao_module._row_refs_enum(row2, "k", "") is False  # payload 非 enum_ref 结构也算不引用
    assert dao_module._row_refs_enum(SimpleNamespace(dict_json="[1,2]"), "1", "") is False


def test_find_refs_no_match_short_circuits_group_load(harness):
    dao_module, dao, seed = harness
    seed(_column_dict("g9", "orders", "status", "订单:other_enum"))
    assert asyncio.run(dao.find_column_dict_refs_async("pay_status")) == []


def test_count_batch_skips_non_dict_payload(harness):
    dao_module, dao, seed = harness
    seed(_column_dict("g1", "orders", "status", None))  # dict_json 非法
    seed(DatasourceColumnDict(group_id="g2", table_name="t", column_name="c", dict_json="[1,2]"))  # 合法 JSON 非对象
    assert asyncio.run(dao.count_column_dict_refs_batch_async([("", "pay_status")])) == {("", "pay_status"): 0}


def test_count_batch_ignores_refs_outside_target_set(harness):
    """粗筛命中但解析后 enum_ref 不在目标集 → 不计数（else 弧）。"""
    dao_module, dao, seed = harness
    seed(
        _column_dict("g1", "orders", "status", "订单:order_status"),
        _column_dict(
            "g2", "refunds", "status", "订单:other_enum"
        ),  # 粗筛命中 order_status 子串? 否——命中靠 LIKE %name%
    )
    # other_enum 不含 order_status，不会被 LIKE 粗筛选中；改用包含目标的行触发 else 弧：
    # dict_json 同时含两个枚举名时 LIKE 命中但解析只取一个 enum_ref。
    seed(
        DatasourceColumnDict(
            group_id="g3",
            table_name="mixed",
            column_name="x",
            dict_json='{"enum_ref": "订单:other_status", "note": "order_status 出现在备注里"}',
        )
    )
    counts = asyncio.run(dao.count_column_dict_refs_batch_async([("订单", "order_status")]))
    assert counts == {("订单", "order_status"): 1}


def test_count_batch_skips_valid_json_non_dict_payload(harness):
    """LIKE 命中且 JSON 合法但非对象（如数组）→ 应用层 continue 不计数。"""
    dao_module, dao, seed = harness
    seed(DatasourceColumnDict(group_id="g1", table_name="t", column_name="c", dict_json='["pay_status"]'))
    assert asyncio.run(dao.count_column_dict_refs_batch_async([("", "pay_status")])) == {("", "pay_status"): 0}
