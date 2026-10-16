"""全局枚举 service/DAO 单元测试。

覆盖核心业务规则：upsert 查-判-写（新建/复活/拒绝覆盖）、二选一写入校验、
引用两步法计数、resolve 统一解析与降级、删除保护，以及枚举维度校验对
enum_ref 引用态的兼容（semantic_config_service 修复回归）。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from apps.chat_data_app.daos.meta_global_enum_dao import (
    MetaGlobalEnumDao,
    _split_enum_ref,
)
from apps.chat_data_app.services.meta.global_enum_service import (
    GlobalEnumService,
    MetaGlobalEnumAlreadyExistsError,
    MetaGlobalEnumInUseError,
    MetaGlobalEnumInvalidError,
    MetaGlobalEnumNameInvalidError,
    MetaGlobalEnumNotFoundError,
)

# ---------------------------------------------------------------------------
# 测试基建：最小化 Fake session（只覆盖 DAO 用到的 execute/add/scalars 形态）
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows: list):
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)

    def all(self) -> list:
        return list(self._rows)


class _FakeSession:
    def __init__(self, select_rows: list | None = None):
        self.select_rows = select_rows or []
        self.added: list = []
        self.executed: list = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult(self.select_rows)

    def add(self, obj) -> None:
        self.added.append(obj)


def _patch_meta_session(monkeypatch, select_rows: list | None = None) -> _FakeSession:
    """把 meta_global_enum_dao.session_scope 指向 Fake session（返回同一 session 便于断言）。"""
    session = _FakeSession(select_rows)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr("apps.chat_data_app.daos.meta_global_enum_dao.session_scope", fake_scope)
    return session


def _enum_row(domain: str = "", name: str = "pay_status", delete_flag: int = 0, enum_json: str = "[]"):
    return SimpleNamespace(
        business_domain=domain,
        enum_name=name,
        enum_desc="",
        enum_json=enum_json,
        delete_flag=delete_flag,
        update_by="",
        update_by_name="",
    )


def _dict_row(group_id: str, table: str, column: str, payload: dict):
    return SimpleNamespace(
        group_id=group_id, table_name=table, column_name=column, dict_json=json.dumps(payload, ensure_ascii=False)
    )


class _FakeGroupRow:
    def __init__(self, group_id: str, group_name: str):
        self.group_id = group_id
        self.group_name = group_name


# ---------------------------------------------------------------------------
# DAO 层
# ---------------------------------------------------------------------------


def test_split_enum_ref_handles_domain_and_colon():
    assert _split_enum_ref("pay_status") == ("", "pay_status")
    assert _split_enum_ref("支付业务:pay_status") == ("支付业务", "pay_status")
    # 业务域本身可含冒号：取最后一个冒号分段
    assert _split_enum_ref("a:b:pay_status") == ("a:b", "pay_status")
    assert _split_enum_ref("  ") == ("", "")


def test_count_column_dict_refs_batch_single_like_and_exact_count(monkeypatch):
    rows = [
        _dict_row("g1", "orders", "status", {"enum_ref": "pay_status"}),
        _dict_row("g2", "orders", "status", {"enum_ref": "支付业务:pay_status"}),
        # 名称出现在其他键中、enum_ref 缺失 → 不计数
        _dict_row("g3", "orders", "memo", {"enum_values": [{"value": "pay_status", "label": "x"}]}),
        # 非法 JSON → 忽略
        _dict_row("g4", "orders", "bad", None),
    ]
    rows[3].dict_json = "{not-json"
    session = _patch_meta_session(monkeypatch, rows)
    dao = MetaGlobalEnumDao()

    counts = asyncio.run(dao.count_column_dict_refs_batch_async([("", "pay_status"), ("支付业务", "pay_status")]))

    assert counts == {("", "pay_status"): 1, ("支付业务", "pay_status"): 1}
    # 禁止数据库原生 JSON：同批目标只做一次 LIKE 粗筛
    assert len(session.executed) == 1
    assert asyncio.run(dao.count_column_dict_refs_batch_async([("", "")])) == {}


def test_find_column_dict_refs_maps_group_names(monkeypatch):
    """引用方返回组名映射；组缺失/已删回退 group_id。"""
    dict_rows = [
        _dict_row("g1", "orders", "status", {"enum_ref": "pay_status"}),
        _dict_row("gone", "orders", "status", {"enum_ref": "pay_status"}),
        _dict_row("g2", "orders", "status", {"enum_ref": "other"}),
    ]

    session = _patch_meta_session(monkeypatch, dict_rows)
    # 第二次查询（组名映射）返回组表行
    original_execute = session.execute

    async def execute(stmt):
        stmt_text = str(stmt)
        if "datasource_group" in stmt_text.lower():
            return _FakeResult([_FakeGroupRow("g1", "订单系统")])
        return await original_execute(stmt)

    session.execute = execute
    dao = MetaGlobalEnumDao()
    refs = asyncio.run(dao.find_column_dict_refs_async("pay_status", ""))
    assert refs == [
        {"group_id": "g1", "table_name": "orders", "column_name": "status", "group_name": "订单系统"},
        {"group_id": "gone", "table_name": "orders", "column_name": "status", "group_name": "gone"},
    ]


def test_upsert_enum_insert_revive_and_overwrite_rules(monkeypatch):
    dao = MetaGlobalEnumDao()

    # 1) 无行 → 插入
    session = _patch_meta_session(monkeypatch, [])
    view = asyncio.run(
        dao.upsert_enum_async("", "pay_status", "支付状态", [{"value": "1", "label": "已支付"}], allow_overwrite=False)
    )
    assert view["enum_name"] == "pay_status" and view["enum_items"] == [{"value": "1", "label": "已支付"}]
    assert len(session.added) == 1

    # 2) 软删行 → 复活复用（不算覆盖，False 也允许）
    row = _enum_row(delete_flag=1)
    _patch_meta_session(monkeypatch, [row])
    asyncio.run(dao.upsert_enum_async("", "pay_status", "新说明", [{"value": "2", "label": "x"}], allow_overwrite=False))
    assert row.delete_flag == 0 and row.enum_desc == "新说明" and json.loads(row.enum_json) == [{"value": "2", "label": "x"}]

    # 3) 活跃行 + 不允许覆盖 → None（service 层拒绝）
    row = _enum_row()
    _patch_meta_session(monkeypatch, [row])
    result = asyncio.run(
        dao.upsert_enum_async("", "pay_status", "x", [{"value": "3", "label": "y"}], allow_overwrite=False)
    )
    assert result is None and row.enum_desc == ""

    # 4) 活跃行 + 允许覆盖 → 更新
    row = _enum_row()
    _patch_meta_session(monkeypatch, [row])
    asyncio.run(dao.upsert_enum_async("", "pay_status", "覆盖说明", [{"value": "3", "label": "y"}], allow_overwrite=True))
    assert row.enum_desc == "覆盖说明" and json.loads(row.enum_json) == [{"value": "3", "label": "y"}]


# ---------------------------------------------------------------------------
# Service 层（注入 Fake DAO）
# ---------------------------------------------------------------------------


class _FakeDao:
    def __init__(self, enums: list | None = None, refs: list | None = None, counts: dict | None = None):
        self.enums = enums or []
        self.refs = refs or []
        self.counts = counts or {}
        self.deleted: list[tuple[str, str]] = []
        self.upserts: list[dict] = []

    async def list_enums_async(self, keyword="", business_domain=None):
        return [dict(item) for item in self.enums]

    async def count_column_dict_refs_batch_async(self, targets):
        return {target: self.counts.get(target, 0) for target in targets}

    async def get_enum_by_name_async(self, enum_name, business_domain=""):
        for item in self.enums:
            if item.get("enum_name") == enum_name and (item.get("business_domain") or "") == (business_domain or ""):
                return dict(item)
        return None

    async def find_column_dict_refs_async(self, enum_name, business_domain=""):
        return list(self.refs)

    async def soft_delete_enum_async(self, enum_name, business_domain="", **_kwargs):
        self.deleted.append((business_domain, enum_name))

    async def upsert_enum_async(self, business_domain, enum_name, enum_desc, enum_items, *, allow_overwrite, **kwargs):
        self.upserts.append(
            {
                "business_domain": business_domain,
                "enum_name": enum_name,
                "enum_desc": enum_desc,
                "enum_items": enum_items,
                "allow_overwrite": allow_overwrite,
            }
        )
        if allow_overwrite is False and any(
            item.get("enum_name") == enum_name and (item.get("business_domain") or "") == business_domain
            for item in self.enums
        ):
            return None
        return {"business_domain": business_domain, "enum_name": enum_name, "enum_desc": enum_desc, "enum_items": enum_items}


def _service(dao: _FakeDao) -> GlobalEnumService:
    return GlobalEnumService(dao=dao)


def test_service_upsert_validation_and_already_exists():
    dao = _FakeDao(enums=[{"business_domain": "", "enum_name": "pay_status"}])
    service = _service(dao)

    with pytest.raises(MetaGlobalEnumInvalidError):
        asyncio.run(service.upsert_enum_async("pay_status", "x", [], ""))
    with pytest.raises(MetaGlobalEnumNameInvalidError):
        asyncio.run(service.upsert_enum_async("非法名!", "x", [{"value": "1", "label": ""}], ""))
    with pytest.raises(MetaGlobalEnumAlreadyExistsError):
        asyncio.run(service.upsert_enum_async("pay_status", "x", [{"value": "1", "label": ""}], ""))

    # 新建成功：items 原样透传 DAO（strip 归一是 DAO 层职责），allow_overwrite 透传
    result = asyncio.run(
        service.upsert_enum_async(" pay_status ", " 支付状态 ", [{"value": " 1 ", "label": "已支付"}], "", allow_overwrite=True)
    )
    assert result["enum_name"] == "pay_status"
    assert dao.upserts[-1]["enum_items"] == [{"value": " 1 ", "label": "已支付"}]
    assert dao.upserts[-1]["allow_overwrite"] is True


def test_service_delete_protection():
    dao = _FakeDao(
        enums=[{"business_domain": "", "enum_name": "pay_status"}],
        refs=[
            {"group_id": "g1", "table_name": "orders", "column_name": "status", "group_name": "订单系统"},
        ],
    )
    service = _service(dao)

    with pytest.raises(MetaGlobalEnumNotFoundError):
        asyncio.run(service.delete_enum_async("missing", ""))
    with pytest.raises(MetaGlobalEnumInUseError) as exc_info:
        asyncio.run(service.delete_enum_async("pay_status", ""))
    # 错误信息包含引用位置，便于用户解除引用
    assert "orders/status" in str(exc_info.value)

    dao.refs = []
    asyncio.run(service.delete_enum_async("pay_status", ""))
    assert dao.deleted == [("", "pay_status")]


def test_service_validate_column_dict_write_rules():
    service = _service(_FakeDao(enums=[{"business_domain": "", "enum_name": "pay_status"}]))

    # 无引用直接放行
    asyncio.run(service.validate_column_dict_for_write_async({"enum_values": [{"value": "1", "label": ""}]}))
    # 二选一：引用态不允许本地枚举值
    with pytest.raises(MetaGlobalEnumInvalidError):
        asyncio.run(
            service.validate_column_dict_for_write_async({"enum_ref": "pay_status", "enum_values": [{"value": "1", "label": ""}]})
        )
    # 引用名非法 / 引用不存在
    with pytest.raises(MetaGlobalEnumNameInvalidError):
        asyncio.run(service.validate_column_dict_for_write_async({"enum_ref": "非法!"}))
    with pytest.raises(MetaGlobalEnumNotFoundError):
        asyncio.run(service.validate_column_dict_for_write_async({"enum_ref": "missing_ref"}))
    # 合法引用放行
    asyncio.run(service.validate_column_dict_for_write_async({"enum_ref": "pay_status", "enum_values": []}))


def test_service_resolve_merges_and_copies_enum_items():
    global_items = [{"value": "1", "label": "已支付"}]
    dao = _FakeDao(enums=[{"business_domain": "", "enum_name": "pay_status", "enum_items": global_items}])
    service = _service(dao)

    local_payload = {"enum_ref": "pay_status", "enum_values": [], "unit": "元"}
    plain_payload = {"enum_ref": "", "enum_values": [{"value": "9", "label": "本地"}]}
    resolved = asyncio.run(
        service.resolve_column_dicts_async({("orders", "status"): local_payload, ("orders", "memo"): plain_payload})
    )

    # 引用态：以全局枚举内容覆盖，且为拷贝（改副本不得污染 DAO 缓存数据）
    merged = resolved[("orders", "status")]
    assert merged["enum_values"] == global_items and merged["unit"] == "元"
    merged["enum_values"][0]["label"] = "篡改"
    assert global_items[0]["label"] == "已支付"
    # 本地态原样保留
    assert resolved[("orders", "memo")] == plain_payload


def test_service_resolve_missing_enum_degrades_to_local(monkeypatch):
    dao = _FakeDao(enums=[])
    service = _service(dao)
    payload = {"enum_ref": "missing_ref", "enum_values": [{"value": "2", "label": "本地"}]}
    resolved = asyncio.run(service.resolve_column_dicts_async({("t", "c"): payload}))
    assert resolved[("t", "c")]["enum_values"] == [{"value": "2", "label": "本地"}]


def test_service_list_injects_ref_count():
    dao = _FakeDao(
        enums=[{"business_domain": "", "enum_name": "pay_status"}, {"business_domain": "支付业务", "enum_name": "order_type"}],
        counts={("", "pay_status"): 3},
    )
    service = _service(dao)
    items = asyncio.run(service.list_enums_async())
    assert items[0]["ref_count"] == 3
    assert items[1]["ref_count"] == 0


# ---------------------------------------------------------------------------
# 枚举维度校验对 enum_ref 的兼容（semantic_config_service 修复回归）
# ---------------------------------------------------------------------------


def _patch_singleton_dao(monkeypatch, enums: list) -> _FakeDao:
    """semantic_config_service 内引用的是全局单例，patch 其 _dao。"""
    from apps.chat_data_app.services.meta import global_enum_service as singleton_module

    fake = _FakeDao(enums=enums)
    monkeypatch.setattr(singleton_module.global_enum_service, "_dao", fake)
    return fake


def test_validate_logical_types_accepts_enum_ref_dimension(monkeypatch):
    from apps.chat_data_app.services.semantic.run.semantic_config_service import validate_semantic_logical_types

    _patch_singleton_dao(
        monkeypatch, enums=[{"business_domain": "", "enum_name": "pay_status", "enum_items": [{"value": "1", "label": "已支付"}]}]
    )

    class _DsManager:
        async def get_column_dict_async(self, _ds, _table, _column):
            return {"enum_ref": "pay_status", "enum_values": []}

    dim = {"name": "支付状态", "table": "orders", "column": "status", "logical_type": "enum"}
    # 引用态字典（本地枚举为空）经解析后有值 → 校验通过
    assert asyncio.run(validate_semantic_logical_types(_DsManager(), "ds_a", [dim])) is None

    # 引用的全局枚举缺失 → 解析降级保留本地空值 → 仍判不达标
    _patch_singleton_dao(monkeypatch, enums=[])
    error = asyncio.run(validate_semantic_logical_types(_DsManager(), "ds_a", [dim]))
    assert error and "请先维护字段字典枚举值" in error

    # 非枚举维度不校验
    assert asyncio.run(validate_semantic_logical_types(_DsManager(), "ds_a", [{**dim, "logical_type": "cat"}])) is None
