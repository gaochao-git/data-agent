"""绑定守护（binding_guard）单测：纯函数逻辑 + ensure_* 接线（DAO 用 fake 替身）。"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.services.semantic.run import binding_guard
from apps.chat_data_app.services.semantic.run.binding_guard import (
    SemanticOntologyBoundError,
    filter_bindings_by_dimensions,
    raise_if_bound,
    raise_if_dimensions_referenced,
    removed_dimension_names,
)

_BINDING = {
    "semantic_config_id": "sc_a",
    "object_type_id": 1,
    "object_type_name": "订单",
    "column_mappings": {"order_id": "订单ID", "status": "订单状态"},
}


def test_removed_dimension_names_diff_treats_rename_as_removal():
    old = [{"name": "订单ID"}, {"name": "状态"}, {"name": "城市"}]
    new = [{"name": "状态"}, {"name": "金额"}]
    # 订单ID/城市 消失、金额新增；改名 = 旧名消失 + 新名新增
    assert removed_dimension_names(old, new) == {"订单ID", "城市"}
    assert removed_dimension_names(old, old) == set()
    assert removed_dimension_names(None, [{"name": "x"}]) == set()


def test_filter_bindings_by_dimensions_intersects_mapping_values():
    affected = filter_bindings_by_dimensions([_BINDING], {"订单ID", "其他"})
    assert len(affected) == 1
    assert affected[0]["referenced_dimensions"] == ["订单ID"]
    assert affected[0]["binding"] is _BINDING
    assert filter_bindings_by_dimensions([_BINDING], {"无关维度"}) == []
    assert filter_bindings_by_dimensions([], {"订单ID"}) == []


def test_raise_if_bound_lists_owners_and_guides_unbind():
    with pytest.raises(SemanticOntologyBoundError) as exc:
        raise_if_bound([_BINDING], action="删除")
    message = str(exc.value)
    assert "订单(sc_a)" in message
    assert "悬空" in message
    assert "解除" in message
    raise_if_bound([], action="删除")  # 无绑定不抛


def test_raise_if_dimensions_referenced_is_precise():
    with pytest.raises(SemanticOntologyBoundError) as exc:
        raise_if_dimensions_referenced([_BINDING], {"订单状态"}, "sc_a", action="更新")
    message = str(exc.value)
    assert "sc_a" in message
    assert "订单状态" in message
    assert "订单" in message
    # 引用维度与被删维度无交集 → 不抛
    raise_if_dimensions_referenced([_BINDING], {"无关"}, "sc_a", action="更新")
    raise_if_dimensions_referenced([], {"订单ID"}, "sc_a", action="更新")


class _FakeOntologyDao:
    def __init__(self, bindings, domains=None):
        self._bindings = bindings
        self._domains = domains or []
        self.config_calls = []
        self.group_calls = []
        self.domain_calls = []

    async def find_bindings_for_configs_async(self, config_ids):
        self.config_calls.append(list(config_ids))
        return list(self._bindings)

    async def find_bindings_for_group_async(self, group_id):
        self.group_calls.append(group_id)
        return list(self._bindings)

    async def list_active_domain_names_by_group_async(self, group_id):
        self.domain_calls.append(group_id)
        return list(self._domains)


def test_ensure_configs_unbound_async_raises_and_passes(monkeypatch):
    fake = _FakeOntologyDao([_BINDING])
    monkeypatch.setattr(binding_guard, "OntologyDao", lambda: fake)
    with pytest.raises(SemanticOntologyBoundError):
        asyncio.run(binding_guard.ensure_configs_unbound_async(["sc_a"], action="删除"))
    assert fake.config_calls == [["sc_a"]]

    fake = _FakeOntologyDao([])
    monkeypatch.setattr(binding_guard, "OntologyDao", lambda: fake)
    asyncio.run(binding_guard.ensure_configs_unbound_async(["sc_a"]))


def test_ensure_config_dimensions_unreferenced_async_skips_query_without_removals(monkeypatch):
    fake = _FakeOntologyDao([_BINDING])
    monkeypatch.setattr(binding_guard, "OntologyDao", lambda: fake)
    # 无被删维度：不查库直接放行（toggle 等高频更新路径零开销）
    asyncio.run(binding_guard.ensure_config_dimensions_unreferenced_async("sc_a", set()))
    assert fake.config_calls == []
    with pytest.raises(SemanticOntologyBoundError):
        asyncio.run(binding_guard.ensure_config_dimensions_unreferenced_async("sc_a", {"订单ID"}))
    assert fake.config_calls == [["sc_a"]]


def test_ensure_group_unbound_async_raises(monkeypatch):
    fake = _FakeOntologyDao([_BINDING])
    monkeypatch.setattr(binding_guard, "OntologyDao", lambda: fake)
    with pytest.raises(SemanticOntologyBoundError):
        asyncio.run(binding_guard.ensure_group_unbound_async("group_a", action="删除数据源组"))
    assert fake.group_calls == ["group_a"]
    # 有映射绑定时不继续查领域（先命中的阻断优先）
    assert fake.domain_calls == []


def test_ensure_group_unbound_async_blocks_ontology_domain(monkeypatch):
    """数据源组被本体领域引用时同样阻断（领域绑定组，删组会让领域悬空）。"""
    fake = _FakeOntologyDao([], domains=["交易域", "供应链域"])
    monkeypatch.setattr(binding_guard, "OntologyDao", lambda: fake)
    with pytest.raises(SemanticOntologyBoundError, match="本体领域") as exc:
        asyncio.run(binding_guard.ensure_group_unbound_async("group_a", action="删除数据源组"))
    assert "交易域" in str(exc.value)
    assert "供应链域" in str(exc.value)
    assert fake.domain_calls == ["group_a"]

    # 无映射绑定且无领域引用 → 放行
    fake = _FakeOntologyDao([], domains=[])
    monkeypatch.setattr(binding_guard, "OntologyDao", lambda: fake)
    asyncio.run(binding_guard.ensure_group_unbound_async("group_a"))
