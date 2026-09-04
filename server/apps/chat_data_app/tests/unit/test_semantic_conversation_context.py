import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.agent_run import conversation_context
from apps.chat_data_app.services.semantic.run import semantic_conversation_context
from apps.chat_data_app.services.datasource.target_context import append_selected_execution_targets_to_context


class _FakeDatasourceManager:
    def __init__(self, *, authorize_error: Exception | None = None):
        self.authorize_error = authorize_error
        self.authorize_calls: list[tuple[dict, list[str], str, str]] = []

    async def get_datasource_meta_async(self, datasource_id: str):
        assert datasource_id == "ds_1"
        return {"id": datasource_id, "query_context": "按机构分库查询", "ds_tables": []}

    async def authorize_execution_datasources_async(
        self,
        datasource: dict,
        member_datasource_ids: list[str],
        user_id: str,
        username: str,
    ):
        self.authorize_calls.append((datasource, member_datasource_ids, user_id, username))
        if self.authorize_error:
            raise self.authorize_error
        target_map = {
            "m_hz": {"member_datasource_id": "m_hz", "member_name": "杭州分公司", "org_code": "ORG_HZ"},
            "m_hz_west": {"member_datasource_id": "m_hz_west", "member_name": "杭州西湖分公司", "org_code": "ORG_HZ_WEST"},
            "m_nb": {"member_datasource_id": "m_nb", "member_name": "宁波分公司", "org_code": "ORG_NB"},
        }
        return [target_map[item] for item in member_datasource_ids if item in target_map]


@pytest.mark.asyncio
async def test_prepare_context_only_loads_selected_authorized_members(monkeypatch):
    manager = _FakeDatasourceManager()

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id=None,
        member_datasource_ids=["m_hz", "m_hz", "m_nb"],
        user_id="u_1",
        username="测试用户",
        user_query="查询杭州西湖分公司和宁波分公司的订单",
    )

    assert manager.authorize_calls == [(
        {"id": "ds_1", "query_context": "按机构分库查询", "ds_tables": []},
        ["m_hz", "m_nb"],
        "u_1",
        "测试用户",
    )]
    assert context.semantic_config is None
    assert context.available_member_datasource_ids == ["m_hz", "m_nb"]
    assert context.matched_member_datasource_ids == ["m_nb"]
    assert "按机构分库查询" in context.datasource_group_context
    assert "杭州分公司" in context.datasource_group_context
    assert "宁波分公司" in context.datasource_group_context


@pytest.mark.asyncio
async def test_build_agent_prompt_context_reuses_semantic_context_without_loading_data(monkeypatch):
    manager = _FakeDatasourceManager()

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)
    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id=None,
        member_datasource_ids=["m_hz"],
        user_id="u_1",
        username="测试用户",
        user_query="查询杭州分公司订单",
    )

    prompt_context = semantic_conversation_context.build_agent_prompt_context(context)

    assert prompt_context["datasource"] == {
        "source_type": "",
    }
    assert prompt_context["datasource_group_context"] == context.datasource_group_context
    assert "semantic_config_directory_info" not in prompt_context
    assert prompt_context["selected_semantic_config"] is None


@pytest.mark.asyncio
async def test_prepare_context_loads_selected_config_for_prompt(monkeypatch):
    manager = _FakeDatasourceManager()
    calls = {"config": []}

    async def fake_config(datasource_id, semantic_config_id, user_id):
        calls["config"].append((datasource_id, semantic_config_id, user_id))
        return {"id": "cfg_1", "name": "订单指标集", "enabled": True, "metrics": [], "dimensions": []}

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_conversation_context, "load_runtime_semantic_config", fake_config)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id="cfg_1",
        member_datasource_ids=["m_hz"],
        user_id="u_1",
        username="测试用户",
        user_query="查询所有机构订单",
    )

    assert calls["config"] == [("ds_1", "cfg_1", "u_1")]
    assert context.semantic_config_id == "cfg_1"
    assert context.semantic_config["name"] == "订单指标集"
    assert context.matched_member_datasource_ids == ["m_hz"]

    data_context = await conversation_context.build_conversation_prompt_context(
        semantic_conversation_context.build_agent_prompt_context(context),
    )

    assert data_context["semantic_configs"] == [{
        "id": "cfg_1",
        "name": "订单指标集",
        "selected": True,
    }]


@pytest.mark.asyncio
async def test_prepare_context_returns_neutral_error_for_invisible_selected_config(monkeypatch):
    manager = _FakeDatasourceManager()

    async def missing_config(*_args):
        return None

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(semantic_conversation_context, "load_runtime_semantic_config", missing_config)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id="private_cfg",
        member_datasource_ids=[],
        user_id="u_2",
        username="其他用户",
        user_query="查询订单",
    )

    assert context.semantic_config is None
    assert context.semantic_config_error == "未找到可用指标集: private_cfg"


@pytest.mark.asyncio
async def test_prepare_context_does_not_authorize_or_expand_empty_member_scope(monkeypatch):
    manager = _FakeDatasourceManager()

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id=None,
        member_datasource_ids=[],
        user_id="u_1",
        username="测试用户",
        user_query="查询所有机构",
    )

    assert manager.authorize_calls == []
    assert context.execution_targets == []
    assert context.available_member_datasource_ids == []
    assert context.matched_member_datasource_ids == []
    assert context.datasource_group_context == "按机构分库查询"


@pytest.mark.asyncio
async def test_prepare_context_handles_execution_authorization_failure(monkeypatch):
    manager = _FakeDatasourceManager(authorize_error=ValueError("无机构权限"))

    monkeypatch.setattr(semantic_conversation_context, "get_datasource_manager", lambda: manager)

    context = await semantic_conversation_context.prepare_semantic_conversation_context(
        datasource_id="ds_1",
        semantic_config_id=None,
        member_datasource_ids=["m_hz"],
        user_id="u_1",
        username="测试用户",
        user_query="查询杭州分公司",
    )

    assert context.execution_targets == []
    assert context.available_member_datasource_ids == []
    assert context.matched_member_datasource_ids == []


def test_execution_target_context_formats_only_selected_resolvable_members():
    prompt = append_selected_execution_targets_to_context(
        "",
        [
            {"member_datasource_id": "m1", "member_name": "投诉举报1", "org_code": "ORG_1"},
            {"member_datasource_id": "m2", "name": "投诉举报2", "org_code": "ORG_2"},
            {"member_name": "无稳定标识"},
            {"member_name": "只有机构编码", "org_code": "ORG_ONLY"},
        ],
        ["m1", "m2", "missing"],
    )

    assert "当前用户已选择的执行对象数据源" in prompt
    assert "投诉举报1" in prompt
    assert "投诉举报2" in prompt
    assert "无稳定标识" not in prompt
    assert "只有机构编码" not in prompt


def test_match_execution_targets_prefers_longer_overlapping_organization_name():
    targets = [
        {"member_datasource_id": "m_hz", "member_name": "杭州分公司", "org_code": "ORG_HZ"},
        {"member_datasource_id": "m_hz_west", "member_name": "杭州西湖分公司", "org_code": "ORG_HZ_WEST"},
        {"member_datasource_id": "m_nb", "member_name": "宁波分公司", "org_code": "ORG_NB"},
    ]

    assert semantic_conversation_context.match_execution_targets_from_query(
        targets,
        "对杭州西湖分公司和宁波分公司分别统计订单",
    ) == ["m_hz_west", "m_nb"]
    assert semantic_conversation_context.match_execution_targets_from_query(targets, "查询所有机构订单") == [
        "m_hz",
        "m_hz_west",
        "m_nb",
    ]
