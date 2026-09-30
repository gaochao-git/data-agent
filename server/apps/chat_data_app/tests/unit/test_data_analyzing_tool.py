"""data_analyzing_tool 单测：合并后的数据分析工具（validate_dsl / validate_dashboard / execute_query / check_yaml）。

execute_query 用例自原 test_agent_tools.py 的 semantic_query_tool 部分平移；
validate_dsl 工具入口用例自原 test_dsl2sql.py 平移；check_yaml 用例自原
test_semantic_yaml_check_tool.py 平移。入参统一为 action + action_payload。
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.tools import data_analyzing, filesystem
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.schema import (
    DATA_ANALYZING_ACTION_PAYLOAD_MODELS,
    DataAnalyzingInput,
    validate_data_analyzing_payload,
)
from utils.builtin_skills import DATA_ANALYZING_SKILL

RESULT_PREFIX = "[data_analyzing_tool]\n"


def _execute_payload(**payload):
    return {"action": "execute_query", "action_payload": payload}


def _query_result_payload(result: str) -> dict:
    payload = json.loads(result)
    assert payload["type"] == "chatdata_internal_tool_result"
    assert payload["version"] == 1
    return payload


def _internal_query_payload(result: str) -> dict:
    payload = _query_result_payload(result)
    assert payload["kind"] == "query_result"
    return payload["payload"]


def _semantic_query_state(
    *,
    datasource_id: str = "ds_a",
    available_member_datasource_ids: list[str] | None = None,
    matched_member_datasource_ids: list[str] | None = None,
    **state: object,
) -> dict:
    return {
        **state,
        "execution_context": {
            "datasource_id": datasource_id,
            "available_member_datasource_ids": available_member_datasource_ids or [],
            "matched_member_datasource_ids": matched_member_datasource_ids or [],
        },
    }


# ---------------------------------------------------------------------------
# payload 跨字段校验：在工具实现入口（validate_data_analyzing_payload），不在 args model_validator
# （langgraph 会把 model_validator 抛出的 loc=() 校验错误过滤成空白消息，模型看不到原因）
# ---------------------------------------------------------------------------


def test_payload_requires_matching_model_per_action():
    with pytest.raises(ValueError, match=r"action=validate_dsl 的 action_payload 校验失败：dsl: Field required"):
        validate_data_analyzing_payload("validate_dsl", {"semantic_config_id": "cfg_a"})
    with pytest.raises(ValueError, match=r"action=validate_dashboard 的 action_payload 校验失败：dsl: Field required"):
        validate_data_analyzing_payload("validate_dashboard", {})
    with pytest.raises(ValueError, match=r"action=check_yaml 的 action_payload 校验失败：file_path"):
        validate_data_analyzing_payload("check_yaml", {})


def test_payload_execute_query_requires_exactly_one_of_dsl_sql():
    with pytest.raises(ValueError, match="必须且只能传入 dsl 或 sql 其中一个"):
        validate_data_analyzing_payload(
            "execute_query",
            {"semantic_config_id": "cfg_a", "member_datasource_ids": ["m1"]},
        )
    with pytest.raises(ValueError, match="必须且只能传入 dsl 或 sql 其中一个"):
        validate_data_analyzing_payload(
            "execute_query",
            {
                "dsl": {"metrics": ["订单数"]},
                "sql": "select 1",
                "semantic_config_id": "cfg_a",
                "member_datasource_ids": ["m1"],
            },
        )


def test_payload_execute_query_reuses_semantic_query_input_rules():
    ok = validate_data_analyzing_payload(
        "execute_query",
        {
            "dsl": {"metrics": ["订单数"]},
            "semantic_config_id": "cfg_a",
            "member_datasource_ids": ["m1"],
        },
    )
    assert ok.dsl == {"metrics": ["订单数"]}

    with pytest.raises(ValueError):
        validate_data_analyzing_payload(
            "execute_query",
            {"dsl": {"metrics": ["订单数"]}, "member_datasource_ids": ["m1"]},
        )
    with pytest.raises(ValueError):
        validate_data_analyzing_payload(
            "execute_query",
            {
                "dsl": {"metrics": ["订单数"]},
                "semantic_config_id": "cfg_a",
                "member_datasource_ids": [],
            },
        )


def test_input_normalizes_dsl_json_string_and_allows_dashboard_list():
    normalized = DataAnalyzingInput.model_validate(
        {
            "action": "validate_dsl",
            "action_payload": {"dsl": '{"metrics": ["订单数"]}', "semantic_config_id": "cfg_a"},
        }
    )
    # action_payload 原始 dict 不被原地改写；归一化发生在 payload 模型（handler 亦复用同一模型）
    parsed = DATA_ANALYZING_ACTION_PAYLOAD_MODELS["validate_dsl"].model_validate(normalized.action_payload)
    assert parsed.dsl == {"metrics": ["订单数"]}

    listed = DataAnalyzingInput.model_validate(
        {
            "action": "validate_dashboard",
            "action_payload": {"dsl": [{"semantic_config_id": "cfg_a"}]},
        }
    )
    assert isinstance(listed.action_payload["dsl"], list)

    with pytest.raises(ValueError, match="dsl 必须是对象或有效的 JSON 对象字符串"):
        validate_data_analyzing_payload("validate_dsl", {"dsl": "not-json", "semantic_config_id": "cfg_a"})


def test_input_rejects_unknown_action():
    with pytest.raises(ValidationError):
        DataAnalyzingInput.model_validate({"action": "export_report", "action_payload": {}})


def test_tool_description_points_at_data_analyzing_skill():
    """工具描述是常量 docstring（f-string 会让 ``__doc__`` 变 ``None``），插不进常量，靠本例锁住。

    指向写**技能名**而不是显示名：显示名是 frontmatter ``title``，属展示层，
    改显示名不该让工具文案连带变红；技能名才是跨层契约。
    """
    description = data_analyzing.create_data_analyzing_tool(datasource_id="ds_a").description

    assert f"`{DATA_ANALYZING_SKILL}`" in description
    assert "validate_dsl" in description
    assert "execute_query" in description
    assert "check_yaml" in description


# ---------------------------------------------------------------------------
# execute_query（原 semantic_query_tool 用例平移）
# ---------------------------------------------------------------------------


def test_execute_query_returns_query_result_or_error(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **kwargs):
            assert kwargs["metrics"] == ["订单数"]
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(datasource_id, semantic_config_id, user_id=None):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "cfg_a"
        assert user_id == "user_a"
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fake_execute_sql_real(
        sql,
        datasource_id,
        member_datasource_ids,
        user_id,
        semantic_config_id,
        query_text,
        conversation_id,
        operator_name=None,
    ):
        assert sql == "select 1"
        assert datasource_id == "ds_a"
        assert member_datasource_ids == ["m1"]
        assert user_id == "user_a"
        assert semantic_config_id == "cfg_a"
        assert query_text == "查订单"
        assert conversation_id == "conv_a"
        assert operator_name == "测试用户"
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    async def fake_execute_sql_real_failed(*_args, **_kwargs):
        return {"success": False, "error": "表不存在"}

    state = _semantic_query_state(
        available_member_datasource_ids=["m1"],
        user_id="user_a",
        operator_name="测试用户",
        semantic_config_id="cfg_a",
        conversation_id="conv_a",
        messages=[HumanMessage(content="查订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fake_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    success = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    dsl={"metrics": ["订单数"]},
                    semantic_config_id="cfg_a",
                    member_datasource_ids=["m1"],
                ),
                "state": state,
            }
        )
    )

    json_string_success = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    dsl='{"metrics": ["订单数"]}',
                    semantic_config_id="cfg_a",
                    member_datasource_ids=["m1"],
                ),
                "state": state,
            }
        )
    )

    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fake_execute_sql_real_failed)
    failed = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    dsl={"metrics": ["订单数"]},
                    semantic_config_id="cfg_a",
                    member_datasource_ids=["m1"],
                ),
                "state": state,
            }
        )
    )

    success_payload = _internal_query_payload(success)
    json_string_success_payload = _internal_query_payload(json_string_success)
    assert success_payload["success"] is True
    assert success_payload["data"] == [{"id": 1}]
    assert success_payload["sql"] == "select 1"
    assert _query_result_payload(success)["meta"] == {"query_mode": "dsl"}
    assert json_string_success_payload["success"] is True
    failed_payload = _internal_query_payload(failed)
    assert failed_payload["success"] is False
    assert failed_payload["error"] == "表不存在"


def test_execute_query_allows_model_selected_member_ids(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    captured = {}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids, *_args, **_kwargs):
        captured["sql"] = sql
        captured["datasource_id"] = datasource_id
        captured["member_datasource_ids"] = member_datasource_ids
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    state = _semantic_query_state(
        available_member_datasource_ids=["org_b", "org_c"],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查指定机构订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fake_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    result = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    dsl={"metrics": ["订单数"]},
                    semantic_config_id="cfg_a",
                    member_datasource_ids=[" org_b ", "org_b", "", "org_c"],
                ),
                "state": state,
            }
        )
    )

    assert _internal_query_payload(result)["success"] is True
    assert captured["member_datasource_ids"] == ["org_b", "org_c"]


def test_execute_query_allows_member_ids_from_available_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    captured = {}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids, *_args, **_kwargs):
        captured["member_datasource_ids"] = member_datasource_ids
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    state = _semantic_query_state(
        available_member_datasource_ids=["org_a", "org_b"],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查 org_b 订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fake_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    result = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    dsl={"metrics": ["订单数"]},
                    semantic_config_id="cfg_a",
                    member_datasource_ids=["org_b"],
                ),
                "state": state,
            }
        )
    )

    assert _internal_query_payload(result)["success"] is True
    assert captured["member_datasource_ids"] == ["org_b"]


def test_execute_query_does_not_default_to_state_member_ids(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("member_datasource_ids must be a tool parameter")

    state = _semantic_query_state(
        available_member_datasource_ids=["org_a", "org_b"],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fail_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    with pytest.raises(ValueError, match="member_datasource_ids"):
        asyncio.run(
            tool.coroutine(
                action="execute_query",
                action_payload={"dsl": {"metrics": ["订单数"]}, "semantic_config_id": "cfg_a"},
                state=state,
            )
        )


def test_execute_query_rejects_member_ids_outside_available_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute unavailable member id")

    state = _semantic_query_state(
        available_member_datasource_ids=["org_a", "org_b"],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查指定机构订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fail_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    with pytest.raises(ValueError, match="本次查询包含不可用的执行对象 org_c"):
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"metrics": ["订单数"]},
                        semantic_config_id="cfg_a",
                        member_datasource_ids=["org_b", "org_c"],
                    ),
                    "state": state,
                }
            )
        )


def test_execute_query_rejects_member_ids_outside_available_member_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute unavailable member id")

    state = _semantic_query_state(
        available_member_datasource_ids=["org_a", "org_b"],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查指定机构订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fail_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    with pytest.raises(ValueError, match="本次查询包含不可用的执行对象 org_c"):
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"metrics": ["订单数"]},
                        semantic_config_id="cfg_a",
                        member_datasource_ids=["org_c"],
                    ),
                    "state": state,
                }
            )
        )


def test_execute_query_rejects_member_ids_conflicting_with_matched_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute member id that conflicts with exact query match")

    state = _semantic_query_state(
        available_member_datasource_ids=["org_a", "org_b"],
        matched_member_datasource_ids=["org_b"],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查机构B订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fail_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    with pytest.raises(ValueError, match="与用户问题中指定的机构不一致") as exc_info:
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"metrics": ["订单数"]},
                        semantic_config_id="cfg_a",
                        member_datasource_ids=["org_a"],
                    ),
                    "state": state,
                }
            )
        )
    assert "org_b" in str(exc_info.value)


def test_execute_query_requires_member_ids(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute without explicit member ids")

    state = _semantic_query_state(
        available_member_datasource_ids=["m1", "m2"],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fail_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    with pytest.raises(ValueError, match="member_datasource_ids"):
        asyncio.run(
            tool.coroutine(
                action="execute_query",
                action_payload={"dsl": {"metrics": ["订单数"]}, "semantic_config_id": "cfg_a"},
                state=state,
            )
        )


def test_execute_query_rejects_empty_available_member_scope(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fail_execute_sql_real(*_args, **_kwargs):
        raise AssertionError("should not execute without request-visible member ids")

    state = _semantic_query_state(
        available_member_datasource_ids=[],
        semantic_config_id="cfg_a",
        messages=[HumanMessage(content="查订单")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fail_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    with pytest.raises(ValueError, match="本次请求没有可用执行对象"):
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"metrics": ["订单数"]},
                        semantic_config_id="cfg_a",
                        member_datasource_ids=["org_a"],
                    ),
                    "state": state,
                }
            )
        )


def test_execute_query_rejects_sql_inside_dsl(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **kwargs):
            return {"success": True, "sql": "select latest"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fake_execute_sql_real(sql, *_args, **_kwargs):
        return {"success": True, "sql": sql, "columns": ["v"], "data": [{"v": 1}], "total_count": 1}

    state = _semantic_query_state(
        available_member_datasource_ids=["m1"],
        semantic_config_id="cfg_a",
        messages=[
            HumanMessage(content="上一轮问题"),
            AIMessage(content="上一轮回答"),
            HumanMessage(content="最新问题"),
        ],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fake_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    result = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    dsl={"metrics": ["订单数"]},
                    semantic_config_id="cfg_a",
                    member_datasource_ids=["m1"],
                ),
                "state": state,
            }
        )
    )
    assert '"sql": "select latest"' in result

    with pytest.raises(ValueError, match="sql 必须放在顶层 sql 参数中"):
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"sql": "select 1"},
                        semantic_config_id="cfg_a",
                        member_datasource_ids=["m1"],
                    ),
                    "state": state,
                }
            )
        )


def test_execute_query_executes_top_level_sql_in_sql_mode(monkeypatch):
    from langchain_core.messages import HumanMessage

    async def fake_load_config(datasource_id, semantic_config_id, user_id=None):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "cfg_a"
        assert user_id == "user_a"
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    class FailGenerator:
        def __init__(self, _config):
            raise AssertionError("top-level sql should not go through DSL2SQL")

    captured = {}

    async def fake_execute_sql_real(
        sql,
        datasource_id,
        member_datasource_ids,
        user_id,
        semantic_config_id,
        query_text,
        conversation_id,
        operator_name=None,
    ):
        captured.update(
            {
                "sql": sql,
                "datasource_id": datasource_id,
                "member_datasource_ids": member_datasource_ids,
                "user_id": user_id,
                "semantic_config_id": semantic_config_id,
                "query_text": query_text,
                "conversation_id": conversation_id,
                "operator_name": operator_name,
            }
        )
        return {"success": True, "columns": ["v"], "data": [{"v": 1}], "total_count": 1}

    state = _semantic_query_state(
        available_member_datasource_ids=["org_a"],
        semantic_config_id="cfg_a",
        user_id="user_a",
        operator_name="测试用户",
        conversation_id="conv_a",
        messages=[HumanMessage(content="用复杂 SQL 查询")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fake_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FailGenerator)

    result = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    sql="with t as (select 1 as v) select * from t",
                    semantic_config_id="cfg_a",
                    member_datasource_ids=["org_a"],
                ),
                "state": state,
            }
        )
    )
    payload = _internal_query_payload(result)

    assert captured == {
        "sql": "with t as (select 1 as v) select * from t",
        "datasource_id": "ds_a",
        "member_datasource_ids": ["org_a"],
        "user_id": "user_a",
        "semantic_config_id": "cfg_a",
        "query_text": "用复杂 SQL 查询",
        "conversation_id": "conv_a",
        "operator_name": "测试用户",
    }
    assert _query_result_payload(result)["meta"]["query_mode"] == "sql"
    assert payload["sql"] == "with t as (select 1 as v) select * from t"


def test_execute_query_validation_and_compile_error_branches(monkeypatch):
    assert data_analyzing._latest_human_query([type("Msg", (), {"type": "human", "content": "伪用户消息"})()]) == ""

    tool = data_analyzing.create_data_analyzing_tool()

    with pytest.raises(ValueError, match="semantic_config_id"):
        asyncio.run(
            tool.coroutine(
                action="execute_query",
                action_payload={"dsl": {"metrics": ["订单数"]}, "member_datasource_ids": ["m1"]},
                state={},
            )
        )

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    class FakeGenerator:
        def __init__(self, _config):
            pass

        def generate_sql(self, **kwargs):
            metrics = kwargs.get("metrics") or []
            if metrics == ["bad"]:
                return {"success": False, "error": "指标不存在"}
            return {"success": True, "sql": "   "}

    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    with pytest.raises(ValueError, match="指标不存在"):
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"metrics": ["bad"]},
                        semantic_config_id="cfg_a",
                        member_datasource_ids=["m1"],
                    ),
                    "state": _semantic_query_state(
                        available_member_datasource_ids=["m1"],
                        semantic_config_id="cfg_a",
                    ),
                }
            )
        )
    with pytest.raises(ValueError, match="DSL2SQL 未生成 SQL"):
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"metrics": ["empty"]},
                        semantic_config_id="cfg_a",
                        member_datasource_ids=["m1"],
                    ),
                    "state": _semantic_query_state(
                        available_member_datasource_ids=["m1"],
                        semantic_config_id="cfg_a",
                    ),
                }
            )
        )


def test_execute_query_reports_missing_runtime_config(monkeypatch):
    async def fake_load_config(*_args, **_kwargs):
        return None

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)

    with pytest.raises(ValueError, match="未找到可用指标集 cfg_missing"):
        asyncio.run(
            tool.ainvoke(
                {
                    **_execute_payload(
                        dsl={"metrics": ["订单数"]},
                        semantic_config_id="cfg_missing",
                        member_datasource_ids=["m1"],
                    ),
                    "state": _semantic_query_state(semantic_config_id="cfg_missing"),
                }
            )
        )


def test_execute_query_returns_large_internal_result_for_middleware(monkeypatch):
    from langchain_core.messages import HumanMessage

    class FakeGenerator:
        def __init__(self, config):
            assert config["id"] == "cfg_a"

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select content from big_table"}

    async def fake_load_config(*_args, **_kwargs):
        return {"id": "cfg_a", "metrics": [], "dimensions": []}

    async def fake_execute_sql_real(*_args, **_kwargs):
        return {
            "success": True,
            "columns": ["content"],
            "data": [{"content": "x" * 200}],
            "total_count": 1,
            "execution_time_ms": 12,
            "sql": "select content from big_table",
        }

    state = _semantic_query_state(
        available_member_datasource_ids=["m1"],
        user_id="user_a",
        operator_name="测试用户",
        semantic_config_id="cfg_a",
        conversation_id="conv_a",
        messages=[HumanMessage(content="查大结果")],
    )

    tool = data_analyzing.create_data_analyzing_tool()
    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)
    monkeypatch.setattr(data_analyzing, "_execute_sql_real", fake_execute_sql_real)
    from apps.chat_data_app.services.semantic.run import semantic_query_service

    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    result = asyncio.run(
        tool.ainvoke(
            {
                **_execute_payload(
                    dsl={"metrics": ["内容"]},
                    semantic_config_id="cfg_a",
                    member_datasource_ids=["m1"],
                ),
                "state": state,
            }
        )
    )

    payload = _internal_query_payload(result)
    assert payload["sql"] == "select content from big_table"
    assert payload["data"][0]["content"] == "x" * 200


def test_resolve_semantic_query_config_loads_explicit_config_id(monkeypatch):
    captured = {}

    async def fake_load_config(datasource_id, semantic_config_id, user_id=None):
        captured.update(
            {
                "datasource_id": datasource_id,
                "semantic_config_id": semantic_config_id,
                "user_id": user_id,
            }
        )
        return {"id": semantic_config_id, "metrics": [], "dimensions": []}

    monkeypatch.setattr(data_analyzing, "load_runtime_semantic_config", fake_load_config)

    config = asyncio.run(
        data_analyzing._resolve_semantic_query_config(
            {"user_id": "user_a"},
            datasource_id="ds_a",
            semantic_config_id="cfg_a",
        )
    )

    assert config["id"] == "cfg_a"
    assert captured == {
        "datasource_id": "ds_a",
        "semantic_config_id": "cfg_a",
        "user_id": "user_a",
    }


# ---------------------------------------------------------------------------
# validate_dsl / validate_dashboard（原 dsl2sql_tool 用例平移）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_dsl_rejects_compare(monkeypatch):
    """compare 拒绝策略由 validate_dsl 执行（adapter 不再收 compare 形参）。"""

    async def fake_load_semantic_model(*args, **kwargs):
        return {"source_type": "mysql", "metrics": [], "dimensions": [], "relationships": []}

    monkeypatch.setattr(data_analyzing, "load_semantic_model", fake_load_semantic_model)
    monkeypatch.setattr(
        data_analyzing,
        "remove_mixed_raw_metrics_for_aggregate_dsl",
        lambda dsl, config: (dsl, []),
    )

    tool = data_analyzing.create_data_analyzing_tool(datasource_id="ds-1", user_id="u-1")
    raw = await tool.ainvoke(
        {
            "action": "validate_dsl",
            "action_payload": {
                "dsl": {
                    "metrics": ["订单数"],
                    "dimensions": [{"name": "下单时间", "time_grain": "month"}],
                    "compare": {"type": "yoy", "mode": "trend", "strategy": "calc"},
                },
                "semantic_config_id": "sc-1",
            },
        }
    )
    payload = json.loads(raw.replace(RESULT_PREFIX, "", 1))
    assert payload["success"] is False
    assert "不再支持 compare" in payload["error"]


@pytest.mark.asyncio
async def test_validate_dsl_requires_bound_datasource():
    tool = data_analyzing.create_data_analyzing_tool(datasource_id="")
    with pytest.raises(ValueError, match="当前会话未绑定数据源"):
        await tool.ainvoke(
            {
                "action": "validate_dsl",
                "action_payload": {"dsl": {"metrics": ["订单数"]}, "semantic_config_id": "sc-1"},
            }
        )


@pytest.mark.asyncio
async def test_validate_dashboard_requires_bound_datasource():
    tool = data_analyzing.create_data_analyzing_tool(datasource_id="")
    with pytest.raises(ValueError, match="当前会话未绑定数据源"):
        await tool.ainvoke(
            {
                "action": "validate_dashboard",
                "action_payload": {"dsl": {"dashboard_name": "d", "cards": []}},
            }
        )


# ---------------------------------------------------------------------------
# check_yaml（原 semantic_yaml_check_tool 用例平移）
# ---------------------------------------------------------------------------

_DATASOURCE = {
    "ds_tables": [
        {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "status"}]},
    ]
}

_SCHEMA_TABLES = [
    {"table_name": "orders", "columns": [{"name": "amount"}, {"name": "status"}]},
]


class FakeManager:
    def __init__(self, datasource=_DATASOURCE):
        self._datasource = datasource

    async def get_datasource_async(self, datasource_id):
        return self._datasource

    async def get_cached_schema_async(self, datasource_id):
        return _SCHEMA_TABLES

    async def get_column_dict_async(self, datasource_id, table_name, column_name):
        return None


class FakeSemanticService:
    """同名冲突检测的数据侧桩：configs 可注入同名公共/他人私有配置。"""

    def __init__(self, configs=None):
        self._configs = configs or []

    async def get_configs_async(self, datasource_id):
        return self._configs


def _good_yaml() -> str:
    return """
version: "1.0"
semantic_model:
  - name: 订单模型
    business_notes: 电商订单核心口径
    datasets:
      - name: orders
        source: orders
        fields:
          - name: 销售额
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: '`amount`'
            dimension:
              is_time: false
            datatype: DECIMAL
            custom_extensions:
              - vendor_name: CHAT_DATA
                data: '{"column": "amount", "table": "orders"}'
    metrics:
      - name: 销售合计
        description: 订单金额求和
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: SUM(amount)
        datatype: DECIMAL
        custom_extensions:
          - vendor_name: CHAT_DATA
            data: '{"table": "orders"}'
"""


class _FakeWorkspaceRuntime:
    """模拟 ConversationWorkspaceRuntime.read_text_file 的分页契约（本地后端行为子集）。

    与真实 backend 对齐：offset 0 起始、返回 lines 数组、has_more/next_offset 翻页、
    超出 max_chars 时给出 truncated_lines（行级预算截断标记）。
    """

    def __init__(self, root: Path, page_size: int | None = None):
        self.root = root
        self.page_size = page_size

    async def read_text_file(self, *, file_path, offset, limit, max_chars):
        # 可见路径 /mnt/user-data/<相对路径> 映射到临时根下同名相对路径
        relative = str(file_path).replace("/mnt/user-data/", "").lstrip("/")
        target = self.root / relative
        if not target.exists():
            return {"exists": False, "path": file_path}
        if target.is_dir():
            return {"exists": True, "is_dir": True, "path": file_path}
        content = target.read_text(encoding="utf-8")
        lines = content.splitlines()
        effective_limit = self.page_size or limit
        page_lines = lines[offset : offset + effective_limit]
        has_more = offset + effective_limit < len(lines)
        truncated_lines = []
        if len(content) > max_chars:
            # 简化：整体超预算时把最后一页最后一行标记为截断
            truncated_lines = [{"line_number": len(lines), "original_chars": len(content), "returned_chars": 0}]
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": file_path,
            "total_lines": len(lines),
            "empty": content == "",
            "offset_out_of_range": content != "" and offset >= len(lines),
            "lines": page_lines,
            "has_more": has_more,
            "next_offset": offset + effective_limit if has_more else None,
            "truncated_lines": truncated_lines,
        }


def _invoke_check_yaml(
    monkeypatch,
    yaml_text: str,
    datasource_id: str = "ds_a",
    existing_configs: list | None = None,
    file_name: str = "model.yaml",
    runtime_page_size: int | None = None,
) -> dict:
    manager = FakeManager()
    monkeypatch.setattr(data_analyzing, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(
        data_analyzing,
        "get_semantic_config_service",
        lambda: FakeSemanticService(existing_configs),
    )
    with tempfile.TemporaryDirectory() as tmp:
        workspace_dir = Path(tmp) / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / file_name).write_text(yaml_text, encoding="utf-8")
        runtime = cast("ConversationWorkspaceRuntime", _FakeWorkspaceRuntime(Path(tmp), page_size=runtime_page_size))
        raw = asyncio.run(
            data_analyzing.create_data_analyzing_tool(datasource_id, workspace_runtime=runtime).ainvoke(
                {
                    "action": "check_yaml",
                    "action_payload": {"file_path": f"/mnt/user-data/workspace/{file_name}"},
                }
            )
        )
    return json.loads(raw.replace(RESULT_PREFIX, "", 1))


def test_check_yaml_requires_bound_datasource():
    with pytest.raises(ValueError, match="未绑定数据源"):
        asyncio.run(
            data_analyzing.create_data_analyzing_tool("").ainvoke(
                {
                    "action": "check_yaml",
                    "action_payload": {"file_path": "/mnt/user-data/workspace/model.yaml"},
                }
            )
        )


def test_check_yaml_missing_file_raises(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        runtime = _FakeWorkspaceRuntime(Path(tmp))
        monkeypatch.setattr(data_analyzing, "get_datasource_manager", lambda: FakeManager())
        with pytest.raises(ValueError, match="文件不存在"):
            asyncio.run(
                data_analyzing.create_data_analyzing_tool("ds_a", workspace_runtime=runtime).ainvoke(
                    {
                        "action": "check_yaml",
                        "action_payload": {"file_path": "/mnt/user-data/workspace/missing.yaml"},
                    }
                )
            )


def test_check_yaml_missing_datasource_raises(monkeypatch, tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "model.yaml").write_text("version: '1'", encoding="utf-8")
    manager = FakeManager(datasource=None)
    monkeypatch.setattr(data_analyzing, "get_datasource_manager", lambda: manager)
    runtime = _FakeWorkspaceRuntime(tmp_path)
    with pytest.raises(ValueError, match="数据源不存在"):
        asyncio.run(
            data_analyzing.create_data_analyzing_tool("ds_x", workspace_runtime=runtime).ainvoke(
                {
                    "action": "check_yaml",
                    "action_payload": {"file_path": "/mnt/user-data/workspace/model.yaml"},
                }
            )
        )


def test_check_yaml_without_workspace_runtime_raises():
    """工作区运行时缺失时直接报错，不允许退化为要求模型复述 YAML 全文。"""
    with pytest.raises(ValueError, match="工作区不可用"):
        asyncio.run(
            data_analyzing.create_data_analyzing_tool("ds_a").ainvoke(
                {
                    "action": "check_yaml",
                    "action_payload": {"file_path": "/mnt/user-data/workspace/model.yaml"},
                }
            )
        )


def test_check_yaml_reads_full_file_across_pages(monkeypatch):
    """read_text_file 是分页契约：页大小小于文件行数时必须翻页拉全，不能丢行。"""
    monkeypatch.setattr(filesystem, "_FULL_READ_PAGE_LINES", 2)
    result = _invoke_check_yaml(
        monkeypatch,
        _good_yaml(),
        existing_configs=[],
        file_name="paged.yaml",
        runtime_page_size=2,
    )
    assert result["success"] is True
    assert result["stage"] == "checked"
    assert result["models"][0]["importable"] is True


def test_check_yaml_empty_file_raises(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        workspace_dir = Path(tmp) / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "empty.yaml").write_text("", encoding="utf-8")
        runtime = _FakeWorkspaceRuntime(Path(tmp))
        monkeypatch.setattr(data_analyzing, "get_datasource_manager", lambda: FakeManager())
        with pytest.raises(ValueError, match="文件为空"):
            asyncio.run(
                data_analyzing.create_data_analyzing_tool("ds_a", workspace_runtime=runtime).ainvoke(
                    {
                        "action": "check_yaml",
                        "action_payload": {"file_path": "/mnt/user-data/workspace/empty.yaml"},
                    }
                )
            )


def test_check_yaml_parse_failure_returns_structured_result(monkeypatch):
    payload = _invoke_check_yaml(monkeypatch, "not: [valid: ossie")
    assert payload["success"] is False
    assert payload["stage"] == "parse"
    assert payload["error"]
    assert payload["all_importable"] is False
    assert payload["models"] == []


def test_check_yaml_passes_good_yaml_with_quality_report(monkeypatch):
    payload = _invoke_check_yaml(monkeypatch, _good_yaml())
    assert payload["success"] is True
    assert payload["stage"] == "checked"
    assert payload["all_importable"] is True
    model = payload["models"][0]
    assert model["importable"] is True
    assert model["errors"] == []
    assert model["metric_count"] == 1
    assert model["dimension_count"] == 1
    # datatype DECIMAL 经别名归一为 Decimal，schema 校验通过；只透出 findings，不输出评分
    assert "score" not in model["quality"]
    assert "grade" not in model["quality"]
    assert isinstance(model["quality"]["findings"], list)
    checklist = {row["name"]: row for row in model["checklist"]}
    assert checklist["物理字段校验"]["status"] == "pass"
    assert checklist["质量规则核对"]["status"] in ("pass", "warn")


def test_check_yaml_blocks_unknown_physical_column(monkeypatch):
    broken = _good_yaml().replace("SUM(amount)", "SUM(amount_missing)")
    payload = _invoke_check_yaml(monkeypatch, broken)
    assert payload["all_importable"] is False
    model = payload["models"][0]
    assert model["importable"] is False
    assert any("必须引用表 orders 的物理字段" in error for error in model["errors"])
    assert "quality" not in model


def test_check_yaml_marks_replaceable_same_name_conflict(monkeypatch):
    """与导入预览同口径：公共同名标注 existing_config（可替换），清单不再出现「模型名冲突」。"""
    payload = _invoke_check_yaml(
        monkeypatch,
        _good_yaml(),
        existing_configs=[
            {"id": "sc_1", "name": "订单模型", "scope_type": "public", "enabled": True},
        ],
    )
    assert payload["success"] is True
    model = payload["models"][0]
    assert model["importable"] is True
    assert model["existing_config"] == {"config_id": "sc_1", "name": "订单模型", "enabled": True}
    assert model["conflict_locked"] is False
    assert all(row["name"] != "模型名冲突" for row in model["checklist"])


def test_check_yaml_marks_locked_same_name_conflict(monkeypatch):
    """他人私有同名：conflict_locked=True，清单保留「模型名冲突」警告行（导入会被强制忽略）。"""
    payload = _invoke_check_yaml(
        monkeypatch,
        _good_yaml(),
        existing_configs=[
            {"id": "sc_2", "name": "订单模型", "scope_type": "private", "create_by": "someone_else", "enabled": True},
        ],
    )
    model = payload["models"][0]
    assert model["importable"] is True
    assert model["existing_config"] is None
    assert model["conflict_locked"] is True
    by_name = {row["name"]: row for row in model["checklist"]}
    assert by_name["模型名冲突"]["status"] == "warn"
    assert "无操作权限" in by_name["模型名冲突"]["message"]


def test_check_yaml_rejects_duplicate_names_in_request(monkeypatch):
    """请求内重名整体拒绝，与导入预览同口径（stage=duplicate_names）。"""
    good = _good_yaml()
    _, sep, entry = good.partition("  - name: 订单模型")
    assert sep
    # 复制完整模型条目（同名「订单模型」），构造单文档内的请求内重名
    duplicated = good + "  - name: 订单模型" + entry
    payload = _invoke_check_yaml(monkeypatch, duplicated)
    assert payload["success"] is False
    assert payload["stage"] == "duplicate_names"
    assert "重名模型" in payload["error"]
    assert payload["models"] == []


# ---------------------------------------------------------------------------
# list_execution_targets（只读发现通道）
# ---------------------------------------------------------------------------

_TARGETS_FIXTURE = [
    {
        "member_datasource_id": "m1",
        "member_name": "机构A",
        "org_code": "ORG_A",
        "source_type": "hive",
        "query_executor_type": "http",
        "source_config": {
            "host": "10.0.0.1",
            "port": 10000,
            "database": "db_a",
            "org_code": "ORG_A",
            "org_name": "机构A",
        },
    },
    {
        "member_datasource_id": "m2",
        "member_name": "ORG_B",
        "org_code": "ORG_B",
        "source_type": "mysql",
        "query_executor_type": "direct",
        "source_config": {"host": "10.0.0.2", "port": 3306, "database": "db_b"},
    },
]


class _FakeTargetsManager:
    def __init__(self, targets, meta=None):
        self._targets = targets
        self._meta = meta
        self.captured = None
        self.meta_calls = []

    async def list_authorized_execution_targets_async(self, datasource_id, user_id=None, username=None):
        self.captured = {"datasource_id": datasource_id, "user_id": user_id, "username": username}
        return self._targets

    async def get_datasource_meta_async(self, datasource_id):
        self.meta_calls.append(datasource_id)
        return self._meta


_DATASOURCE_META_FIXTURE = {
    "group_name": "支付机构库",
    "source_type": "hive",
    "query_context": "本数据源组按机构分库存储交易数据。",
}


def _invoke_list_targets(monkeypatch, *, tool_kwargs=None, state=None, meta=_DATASOURCE_META_FIXTURE):
    manager = _FakeTargetsManager(_TARGETS_FIXTURE, meta=meta)
    monkeypatch.setattr(data_analyzing, "get_datasource_manager", lambda: manager)
    raw = asyncio.run(
        data_analyzing.create_data_analyzing_tool(**(tool_kwargs or {})).ainvoke(
            {
                "action": "list_execution_targets",
                "action_payload": {},
                "state": state if state is not None else _semantic_query_state(),
            }
        )
    )
    return json.loads(raw.replace(RESULT_PREFIX, "", 1)), manager


def test_input_list_execution_targets_accepts_empty_payload():
    parsed = DataAnalyzingInput.model_validate({"action": "list_execution_targets", "action_payload": {}})
    assert parsed.action == "list_execution_targets"


def test_list_execution_targets_returns_sanitized_entries(monkeypatch):
    """清单只透出名称/ID/机构编码，不透出连接信息；身份取自 state 会话上下文。"""
    payload, manager = _invoke_list_targets(
        monkeypatch,
        state=_semantic_query_state(datasource_id="ds_a", user_id="user_a", operator_name="测试用户"),
    )
    assert payload["success"] is True
    assert payload["count"] == 2
    assert payload["execution_targets"] == [
        {"member_name": "机构A", "member_datasource_id": "m1", "org_code": "ORG_A"},
        {"member_name": "ORG_B", "member_datasource_id": "m2", "org_code": "ORG_B"},
    ]
    assert "host" not in json.dumps(payload, ensure_ascii=False)
    assert "10.0.0.1" not in json.dumps(payload, ensure_ascii=False)
    assert manager.captured == {"datasource_id": "ds_a", "user_id": "user_a", "username": "测试用户"}


def test_list_execution_targets_returns_datasource_overview(monkeypatch):
    """方言/类型/智能问数说明随清单返回，替代系统提示词静态注入。

    datasource_id（服务端绑定，模型用不上）、datasource_name、usage_hint
    均不透出，保持返回最小。
    """
    payload, manager = _invoke_list_targets(
        monkeypatch,
        state=_semantic_query_state(datasource_id="ds_a"),
    )
    assert payload["source_type"] == "hive"
    assert payload["dialect"] == "Hive"
    assert payload["query_context"] == "本数据源组按机构分库存储交易数据。"
    assert manager.meta_calls == ["ds_a"]
    assert "datasource_id" not in payload
    assert "datasource_name" not in payload
    assert "usage_hint" not in payload


def test_list_execution_targets_overview_defaults_when_meta_missing(monkeypatch):
    """meta 缺失（组刚被删等）时概览字段留空，方言回落 Hive，不报错。"""
    payload, _ = _invoke_list_targets(
        monkeypatch,
        state=_semantic_query_state(datasource_id="ds_a"),
        meta=None,
    )
    assert payload["source_type"] == ""
    assert payload["dialect"] == "Hive"
    assert payload["query_context"] == ""


def test_list_execution_targets_falls_back_to_bound_context(monkeypatch):
    """state 缺数据源/身份时回落到工具绑定的会话上下文。"""
    payload, manager = _invoke_list_targets(
        monkeypatch,
        tool_kwargs={"datasource_id": "ds_bound", "user_id": "bound_u", "operator_name": "绑定用户"},
        state={"execution_context": {}},
    )
    # datasource_id 不透出，但清单按绑定数据源解析（captured 可见）
    assert manager.captured == {"datasource_id": "ds_bound", "user_id": "bound_u", "username": "绑定用户"}


def test_list_execution_targets_requires_datasource():
    with pytest.raises(ValueError, match="当前会话未绑定数据源"):
        asyncio.run(
            data_analyzing.create_data_analyzing_tool("").ainvoke(
                {
                    "action": "list_execution_targets",
                    "action_payload": {},
                    "state": {},
                }
            )
        )


def test_validate_member_scope_error_includes_available_list():
    """member 范围报错必须带当前可用执行对象清单，模型可自行纠正而不是再问一轮。"""
    with pytest.raises(ValueError, match="当前可用执行对象: m1, m2"):
        data_analyzing._validate_member_scope(
            requested_member_datasource_ids=[],
            available_member_datasource_ids=["m1", "m2"],
            matched_member_datasource_ids=[],
        )
    with pytest.raises(ValueError, match="当前可用执行对象: m1, m2"):
        data_analyzing._validate_member_scope(
            requested_member_datasource_ids=["m3"],
            available_member_datasource_ids=["m1", "m2"],
            matched_member_datasource_ids=[],
        )
