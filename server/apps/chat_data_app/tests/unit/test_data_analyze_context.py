import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import pytest

from apps.chat_data_app.services import data_analyze_context
from apps.chat_data_app.services.semantic_conversation_context import (
    SemanticConversationContext,
    build_agent_prompt_context,
)


def _semantic_context(
    *,
    datasource_id: str | None = "ds_1",
    datasource_meta: dict | None = None,
    semantic_config: dict | None = None,
    directory_info: str = "",
    datasource_group_context: str = "",
    execution_targets: list[dict] | None = None,
) -> SemanticConversationContext:
    return SemanticConversationContext(
        datasource_id=datasource_id,
        datasource_meta=datasource_meta,
        semantic_config_id=(semantic_config or {}).get("id"),
        semantic_config=semantic_config,
        semantic_config_directory_info=directory_info,
        execution_targets=execution_targets or [],
        available_member_datasource_ids=[
            target["member_datasource_id"]
            for target in execution_targets or []
            if target.get("member_datasource_id")
        ],
        matched_member_datasource_ids=[],
        selected_member_datasource_ids=[],
        datasource_group_context=datasource_group_context,
    )


def _prompt_context(**kwargs):
    return build_agent_prompt_context(_semantic_context(**kwargs))


@pytest.mark.asyncio
async def test_build_data_analyze_context_without_datasource():
    context = await data_analyze_context.build_data_analyze_context(
        _prompt_context(datasource_id=None),
    )

    assert context["datasource"] is None
    assert context["tables"] == []
    assert all(item["name"] not in {"analysis_plan", "query_design"} for item in context["tool_chain"])
    assert any(item["name"] == "file_edit" for item in context["tool_chain"])
    assert any(item["name"] == "present_artifacts" for item in context["tool_chain"])
    assert any(item["name"] == "sandbox_run" for item in context["tool_chain"])
    assert all(item["name"] != "file_move" for item in context["tool_chain"])
    assert context["notes"]


@pytest.mark.asyncio
async def test_build_data_analyze_context_summarizes_datasource(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            assert datasource_id == "ds_1"
            return {
                "group_name": "风险数据源",
                "ds_description": "用于风险分析",
                "source_type": "mysql",
                "ds_tables": [
                    {
                        "table_name": "risk_event",
                        "description": "风险事件表",
                        "columns": [
                            {"name": "event_id", "type": "varchar", "description": "事件ID"},
                            {"name": "risk_level", "type": "varchar", "description": "风险等级"},
                        ],
                    }
                ],
                "semantic_configs": [
                    {
                        "id": "sem_1",
                        "name": "风险语义层",
                        "source_type": "hive",
                        "ontology_rules": "风险分析业务口径",
                        "metrics": [{"name": "风险事件数", "table": "risk_event", "expression": "COUNT(*)"}],
                        "dimensions": [{"name": "风险等级", "table": "risk_event", "logical_type": "text"}],
                        "relationships": [{"left_table": "risk_event", "right_table": "risk_user"}],
                    }
                ],
            }

    semantic_config = {
        "id": "sem_1",
        "name": "风险语义层",
        "source_type": "hive",
        "ontology_rules": "风险分析业务口径",
        "metrics": [{"name": "风险事件数", "table": "risk_event", "expression": "COUNT(*)"}],
        "dimensions": [{"name": "风险等级", "table": "risk_event", "logical_type": "text"}],
        "relationships": [{"left_table": "risk_event", "right_table": "risk_user"}],
    }
    datasource_meta = {
        "group_name": "风险数据源",
        "ds_description": "用于风险分析",
        "source_type": "mysql",
    }
    monkeypatch.setattr(data_analyze_context, "get_datasource_manager", lambda: FakeManager())

    context = await data_analyze_context.build_data_analyze_context(
        _prompt_context(
            datasource_meta=datasource_meta,
            semantic_config=semantic_config,
        ),
    )

    assert context["datasource"]["name"] == "风险数据源"
    assert context["tables"][0]["table_name"] == "risk_event"
    assert context["tables"][0]["columns"][1]["name"] == "risk_level"
    assert context["semantic_config_directory_info"] == ""
    assert context["semantic_configs"][0]["selected"] is True
    assert context["semantic_configs"][0]["source_type"] == "hive"
    assert context["semantic_configs"][0]["metrics"][0]["name"] == "风险事件数"
    assert context["semantic_configs"][0]["relationships"][0]["right_table"] == "risk_user"


@pytest.mark.asyncio
async def test_build_data_analyze_context_adapts_shared_directory_and_execution_context():
    context = await data_analyze_context.build_data_analyze_context(
        _prompt_context(
            datasource_meta={"name": "订单数据源", "source_type": "mysql"},
            datasource_group_context=(
                "## 当前用户已选择的执行对象数据源\n"
                "本次查询必须只使用以下 member_datasource_ids：\n"
                "- 财付通（member_datasource_id: m1）；编码：ORG_CODE1\n"
                "- 支付宝（member_datasource_id: m2）；编码：ORG_CODE2"
            ),
            directory_info=(
                "## 当前可见指标集目录\n1. 名称：订单指标集\n"
                "   描述：订单分析\n   指标数：1；维度数：1"
            ),
            execution_targets=[
                {"member_datasource_id": "m1", "member_name": "财付通", "org_code": "ORG_CODE1"},
                {"member_datasource_id": "m2", "member_name": "支付宝", "org_code": "ORG_CODE2"},
            ],
        ),
    )

    assert context["tables"] == []
    assert context["semantic_configs"] == []
    assert "## 当前用户已选择的执行对象数据源" in context["datasource_group_context"]
    assert "财付通（member_datasource_id: m1）；编码：ORG_CODE1" in context["datasource_group_context"]
    assert "支付宝（member_datasource_id: m2）；编码：ORG_CODE2" in context["datasource_group_context"]
    assert "## 当前可见指标集目录" in context["semantic_config_directory_info"]
    assert "名称：订单指标集" in context["semantic_config_directory_info"]
    assert "指标数：1；维度数：1" in context["semantic_config_directory_info"]
    assert "semantic_config_id" not in context["semantic_config_directory_info"]
    assert any("仅注入指标集轻量目录" in note for note in context["notes"])


def test_data_analyze_context_truncate_short_and_long_text():
    assert data_analyze_context._truncate("短文本", 10) == "短文本"
    assert data_analyze_context._truncate("abcdef", 3) == "abc..."


@pytest.mark.asyncio
async def test_build_data_analyze_context_handles_missing_datasource():
    missing = await data_analyze_context.build_data_analyze_context(
        _prompt_context(
            datasource_id="missing_ds",
            datasource_meta=None,
        ),
    )
    assert missing["datasource"] == {"id": "missing_ds", "missing": True}
