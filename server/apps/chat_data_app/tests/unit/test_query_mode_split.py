"""语义 / 本体查询能力二选一：服务层单一入口（query_mode）与消费方分支单测。

覆盖规则：
- 有本体（当前数据源组存在 certified/experimental 且绑定到本组 Dataset 的对象类型）
  → 只注入本体目录、只暴露 ontology_tool，禁止语义目录与语义查询工具；
- 无本体（含对象类型为空、对象类型无数据源绑定、对象类型只绑其它数据源组）
  → 保持原语义目录与 data_analyzing_tool 链路；
- 判定入口唯一：提示词 / 工具注册 / 目录物化全部读取 query_mode 的结论。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from apps.chat_data_app.agent.prompts import chat_data
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.agent.tools import registry
from apps.chat_data_app.services.ontology import query_mode

# ---------------------------------------------------------------------------
# 判定纯函数
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_mode_state():
    query_mode.clear_query_mode_cache()
    query_mode.reset_query_mode()
    yield
    query_mode.clear_query_mode_cache()
    query_mode.reset_query_mode()


def test_decide_query_mode_semantic_when_feature_disabled():
    decision = query_mode.decide_query_mode("ds_a", object_type_count=5, injection_enabled=False)
    assert decision.is_semantic is True
    assert decision.reason == "ontology_injection_disabled"


def test_decide_query_mode_semantic_without_datasource_group():
    decision = query_mode.decide_query_mode(None, object_type_count=5, injection_enabled=True)
    assert decision.is_semantic is True
    assert decision.reason == "datasource_group_missing"


def test_decide_query_mode_semantic_when_no_domain_object_type():
    decision = query_mode.decide_query_mode("ds_a", object_type_count=0, injection_enabled=True)
    assert decision.is_semantic is True
    assert decision.reason == "no_domain_object_type"


def test_decide_query_mode_ontology_when_domain_object_type_active():
    decision = query_mode.decide_query_mode("ds_a", object_type_count=1, injection_enabled=True)
    assert decision.is_ontology is True
    assert decision.datasource_group_id == "ds_a"
    assert decision.active_object_type_count == 1


# ---------------------------------------------------------------------------
# 域探测：数据源组 + Dataset source binding
# ---------------------------------------------------------------------------


def _object_type(object_type_id: int, name: str, status: str = "certified") -> dict[str, Any]:
    return {
        "id": object_type_id,
        "object_type_name": name,
        "display_name": name,
        "status": status,
        "description": "",
    }


def _dataset(dataset_id: int, name: str, group_id: str = "ds_a", member_id: str = "m1") -> dict[str, Any]:
    return {
        "id": dataset_id,
        "dataset_name": name,
        "display_name": name,
        "columns": [
            {"name": "order_key", "physical_column": "ORDER_ID"},
            {"name": "buyer_id", "physical_column": "USER_ID"},
        ],
        "bindings": [
            {
                "datasource_group_id": group_id,
                "member_datasource_id": member_id,
                "physical_table": f"T_{name.upper()}",
            }
        ],
    }


class _FakeDao:
    def __init__(
        self,
        *,
        object_types: list[dict[str, Any]],
        bindings: dict[int, dict[str, Any]],
        properties: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
    ) -> None:
        self._object_types = object_types
        self._bindings = bindings
        self._properties = properties or []
        self._relations = relations or []
        self.calls = 0

    async def load_injection_bundle_async(self, *args, **kwargs):
        self.calls += 1
        return {
            "object_types": list(self._object_types),
            "properties": list(self._properties),
            "relation_types": list(self._relations),
            "mappings": [],
        }

    async def get_source_binding_async(self, object_type_id):
        return self._bindings.get(int(object_type_id))


def _patch_domain(
    monkeypatch,
    *,
    dao: _FakeDao,
    datasets: dict[int, dict[str, Any]],
) -> None:
    monkeypatch.setattr(query_mode, "OntologyDao", lambda: dao)

    async def fake_dataset(dataset_id: int):
        return datasets.get(int(dataset_id))

    monkeypatch.setattr(query_mode.logical_dataset_service, "get_dataset_async_by_id_async", fake_dataset)


def _relation(from_id: int, to_id: int) -> dict[str, Any]:
    return {
        "relation_type_name": "order_belongs_to_user",
        "from_object_type_id": from_id,
        "to_object_type_id": to_id,
        "from_columns": ["buyer_id"],
        "to_columns": ["user_id"],
        "multiplicity": "many_to_one",
    }


@pytest.mark.parametrize("status", ["certified", "experimental"])
def test_resolve_ontology_mode_for_active_domain_object_type(monkeypatch, status):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")
    dao = _FakeDao(
        object_types=[_object_type(1, "order", status)],
        bindings={1: {"dataset_id": 11, "column_mappings": {"order_id": "order_key"}}},
    )
    _patch_domain(monkeypatch, dao=dao, datasets={11: _dataset(11, "order_dataset")})
    decision = asyncio.run(query_mode.resolve_query_mode_async("ds_a"))
    assert decision.is_ontology is True
    assert decision.active_object_type_count == 1


def test_resolve_semantic_mode_when_binding_missing(monkeypatch):
    """对象类型存在但没有 Dataset source binding → 查不了数，不算有本体。"""
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")
    dao = _FakeDao(object_types=[_object_type(1, "order")], bindings={})
    _patch_domain(monkeypatch, dao=dao, datasets={})
    decision = asyncio.run(query_mode.resolve_query_mode_async("ds_a"))
    assert decision.is_semantic is True
    assert decision.reason == "no_domain_object_type"


def test_resolve_semantic_mode_when_dataset_in_other_group(monkeypatch):
    """混合业务域：对象类型只绑定其它数据源组的 Dataset → 当前域无本体。"""
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")
    dao = _FakeDao(
        object_types=[_object_type(1, "order")],
        bindings={1: {"dataset_id": 11, "column_mappings": {"order_id": "order_key"}}},
    )
    _patch_domain(monkeypatch, dao=dao, datasets={11: _dataset(11, "order_dataset", group_id="ds_other")})
    assert asyncio.run(query_mode.resolve_query_mode_async("ds_a")).is_semantic is True
    # 同一对象类型在它所属的数据源组下判定为本体
    assert asyncio.run(query_mode.resolve_query_mode_async("ds_other")).is_ontology is True


def test_resolve_semantic_mode_when_feature_disabled(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", raising=False)
    dao = _FakeDao(
        object_types=[_object_type(1, "order")],
        bindings={1: {"dataset_id": 11, "column_mappings": {}}},
    )
    _patch_domain(monkeypatch, dao=dao, datasets={11: _dataset(11, "order_dataset")})
    decision = asyncio.run(query_mode.resolve_query_mode_async("ds_a"))
    assert decision.is_semantic is True
    assert dao.calls == 0, "部署开关关闭时不得触库"


def test_resolve_semantic_mode_on_probe_error(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")

    class _BrokenDao:
        async def load_injection_bundle_async(self, *args, **kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(query_mode, "OntologyDao", _BrokenDao)
    decision = asyncio.run(query_mode.resolve_query_mode_async("ds_a"))
    assert decision.is_semantic is True
    assert decision.reason == "probe_failed"
    assert decision.is_degraded is True
    assert "RuntimeError" in decision.degraded_reason


def test_load_domain_bundle_filters_and_translates_bindings(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")
    dao = _FakeDao(
        object_types=[_object_type(1, "order"), _object_type(2, "user", "experimental"), _object_type(3, "other")],
        bindings={
            1: {"dataset_id": 11, "column_mappings": {"order_id": "order_key", "buyer_id": "buyer_id"}},
            2: {"dataset_id": 12, "column_mappings": {"user_id": "buyer_id"}},
            3: {"dataset_id": 13, "column_mappings": {}},
        },
        properties=[
            {"object_type_id": 1, "property_name": "order_id"},
            {"object_type_id": 3, "property_name": "other_id"},
        ],
        relations=[_relation(1, 2), _relation(1, 3)],
    )
    _patch_domain(
        monkeypatch,
        dao=dao,
        datasets={
            11: _dataset(11, "order_dataset"),
            12: _dataset(12, "user_dataset", member_id="m2"),
            13: _dataset(13, "other_dataset", group_id="ds_other"),
        },
    )
    bundle = asyncio.run(query_mode.load_domain_ontology_bundle_async("ds_a"))
    assert [obj["object_type_name"] for obj in bundle["object_types"]] == ["order", "user"]
    # 属性/关系只保留两端都在域内的对象类型
    assert [prop["property_name"] for prop in bundle["properties"]] == ["order_id"]
    assert len(bundle["relation_types"]) == 1
    order_binding = bundle["object_types"][0]["source_binding"]
    assert order_binding["dataset_name"] == "order_dataset"
    assert order_binding["physical_table"] == "T_ORDER_DATASET"
    assert order_binding["column_mappings"]["order_id"] == "order_key"
    assert order_binding["columns"]["order_key"] == "ORDER_ID"
    assert order_binding["members"] == [{"member_datasource_id": "m1", "physical_table": "T_ORDER_DATASET"}]


def test_domain_bundle_is_cached_between_calls(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED", "1")
    dao = _FakeDao(
        object_types=[_object_type(1, "order")],
        bindings={1: {"dataset_id": 11, "column_mappings": {}}},
    )
    _patch_domain(monkeypatch, dao=dao, datasets={11: _dataset(11, "order_dataset")})
    asyncio.run(query_mode.load_domain_ontology_bundle_async("ds_a"))
    asyncio.run(query_mode.load_domain_ontology_bundle_async("ds_a"))
    assert dao.calls == 1


# ---------------------------------------------------------------------------
# 消费方：提示词 / 工具注册
# ---------------------------------------------------------------------------


def _publish_ontology_mode() -> None:
    query_mode.publish_query_mode(
        query_mode.QueryModeDecision(query_mode.QueryMode.ONTOLOGY, "ds_a", 2, "domain_object_type_active")
    )


def test_ontology_prompt_only_carries_ontology_workflow():
    _publish_ontology_mode()
    prompt = chat_data.build_unified_system_prompt(AgentRuntimeOptions())

    assert chat_data.ONTOLOGY_WORKFLOW_BLOCK in prompt
    assert "_ontology_catalog.yaml" in prompt
    assert "ontology_tool" in prompt
    assert "data_analyzing_tool" not in prompt
    assert chat_data.SKILLS_USAGE_BLOCK not in prompt
    assert chat_data.ERROR_HANDLING_BLOCK not in prompt
    assert chat_data.ENVIRONMENT_BLOCK not in prompt
    assert chat_data.ONTOLOGY_ENVIRONMENT_BLOCK in prompt


def test_semantic_prompt_does_not_carry_ontology_workflow():
    prompt = chat_data.build_unified_system_prompt(AgentRuntimeOptions())

    assert chat_data.SKILLS_USAGE_BLOCK in prompt
    assert chat_data.ERROR_HANDLING_BLOCK in prompt
    assert chat_data.ENVIRONMENT_BLOCK in prompt
    assert chat_data.ONTOLOGY_WORKFLOW_BLOCK not in prompt
    assert chat_data.ONTOLOGY_ENVIRONMENT_BLOCK not in prompt
    assert "ontology_tool" not in prompt


def test_subagent_prompt_gets_ontology_hint_only_in_ontology_mode():
    base = "子智能体提示词"
    assert chat_data.build_subagent_system_prompt(base) == base
    _publish_ontology_mode()
    assert chat_data.ONTOLOGY_SUBAGENT_HINT in chat_data.build_subagent_system_prompt(base)


def test_ontology_mode_registers_only_ontology_tool(monkeypatch, tmp_path):
    from apps.chat_data_app.agent.data_agent import DatasourceContext, IdentityContext, assemble_tools

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")
    runtime, options = _workspace(tmp_path)
    _publish_ontology_mode()
    names = {
        tool.name
        for tool in assemble_tools(
            options,
            runtime,
            identity_context=IdentityContext(user_id="u1"),
            datasource_context=DatasourceContext(datasource_id="ds_a"),
        )
    }
    assert "ontology_tool" in names
    assert "data_analyzing_tool" not in names


def test_registry_declares_both_query_tools_with_mode_notes():
    """注册表仍声明两个工具（子智能体排除集/审批表依赖），装配层二选一。"""
    names = registry.provided_tool_names()
    assert {"data_analyzing_tool", "ontology_tool"} <= names


def _workspace(tmp_path):
    from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
    from apps.chat_data_app.services.agent_run.conversation_workspace import ConversationWorkspaceRuntime

    runtime = ConversationWorkspaceRuntime(workspace_root=str(tmp_path), user_id="u1", backend="local")
    return runtime, AgentRuntimeOptions(workspace_backend="local")


def test_context_budget_consumes_mode_specific_prompt_and_tools(monkeypatch, tmp_path):
    """context budget 按当前模式的实际 prompt/工具计算固定输入（不按模式硬编码）。"""
    from apps.chat_data_app.agent.context.context_budget import ContextRequestBudget
    from apps.chat_data_app.agent.data_agent import DatasourceContext, IdentityContext, assemble_tools

    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED", "1")
    monkeypatch.setenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED", "1")
    runtime, options = _workspace(tmp_path)
    tools = assemble_tools(
        options,
        runtime,
        identity_context=IdentityContext(user_id="u1"),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
    )
    semantic_prompt = chat_data.build_unified_system_prompt(options)
    semantic_budget = ContextRequestBudget(None)
    semantic_budget.configure(semantic_prompt, tools)

    _publish_ontology_mode()
    ontology_tools = assemble_tools(
        options,
        runtime,
        identity_context=IdentityContext(user_id="u1"),
        datasource_context=DatasourceContext(datasource_id="ds_a"),
    )
    ontology_prompt = chat_data.build_unified_system_prompt(options)
    ontology_budget = ContextRequestBudget(None)
    ontology_budget.configure(ontology_prompt, ontology_tools)

    assert semantic_budget.static_input_tokens > 0
    assert ontology_budget.static_input_tokens > 0
    # 两种模式的固定输入都按实际内容计入（提示词/工具不同 → 预算不同），且都能通过静态容量门禁
    assert semantic_budget.static_input_tokens != ontology_budget.static_input_tokens
    semantic_budget.validate_static_input()
    ontology_budget.validate_static_input()


def test_hybrid_query_mode_flag(monkeypatch):
    from apps.chat_data_app.services.ontology import query_mode

    monkeypatch.delenv("CHAT_DATA_QUERY_MODE_HYBRID", raising=False)
    assert query_mode.hybrid_query_mode_enabled() is False
    monkeypatch.setenv("CHAT_DATA_QUERY_MODE_HYBRID", "1")
    assert query_mode.hybrid_query_mode_enabled() is True
