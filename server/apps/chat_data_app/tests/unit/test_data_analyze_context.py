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


@pytest.mark.asyncio
async def test_build_data_analyze_context_without_datasource():
    context = await data_analyze_context.build_data_analyze_context(None)

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

    monkeypatch.setattr(data_analyze_context, "get_datasource_manager", lambda: FakeManager())

    context = await data_analyze_context.build_data_analyze_context("ds_1", "sem_1")

    assert context["datasource"]["name"] == "风险数据源"
    assert context["tables"][0]["table_name"] == "risk_event"
    assert context["tables"][0]["columns"][1]["name"] == "risk_level"
    assert context["semantic_config_directory_info"] == ""
    assert context["semantic_configs"][0]["selected"] is True
    assert context["semantic_configs"][0]["source_type"] == "hive"
    assert context["semantic_configs"][0]["metrics"][0]["name"] == "风险事件数"
    assert context["semantic_configs"][0]["relationships"][0]["right_table"] == "risk_user"


@pytest.mark.asyncio
async def test_build_data_analyze_context_uses_directory_only_before_semantic_selected(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "id": datasource_id,
                "name": "订单数据源",
                "source_type": "mysql",
                "ds_tables": [],
                "semantic_configs": [
                    {
                        "id": "sem_order",
                        "name": "订单指标集",
                        "source_type": "mysql",
                        "description": "订单分析",
                        "ontology_rules": "不应在未选中时注入",
                        "metrics": [{"name": "订单金额", "expression": "SUM(amount)"}],
                        "dimensions": [{"name": "订单状态", "column": "status"}],
                        "relationships": [{"left_table": "orders", "right_table": "users"}],
                    }
                ],
            }

        async def get_execution_datasources_async(self, datasource_id, member_datasource_ids=None):
            return [
                {"member_datasource_id": "m1", "member_name": "财付通", "org_code": "ORG_CODE1"},
                {"member_datasource_id": "m2", "member_name": "支付宝", "source_config": {"org_code": "ORG_CODE2"}},
            ]

    monkeypatch.setattr(data_analyze_context, "get_datasource_manager", lambda: FakeManager())

    context = await data_analyze_context.build_data_analyze_context("ds_1", member_datasource_ids=["m1", "m2"])

    assert context["tables"] == []
    assert context["semantic_configs"] == []
    assert "## 当前用户已选择的执行对象数据源" in context["semantic_config_directory_info"]
    assert "财付通（member_datasource_id: m1）；编码：ORG_CODE1" in context["semantic_config_directory_info"]
    assert "支付宝（member_datasource_id: m2）；编码：ORG_CODE2" in context["semantic_config_directory_info"]
    assert "## 当前可见指标集目录" in context["semantic_config_directory_info"]
    assert context["semantic_config_directory_info"].index("## 当前用户已选择的执行对象数据源") < context["semantic_config_directory_info"].index("## 当前可见指标集目录")
    assert "名称：订单指标集" in context["semantic_config_directory_info"]
    assert "指标数：1；维度数：1" in context["semantic_config_directory_info"]
    assert "semantic_config_id" not in context["semantic_config_directory_info"]
    assert any("仅注入指标集轻量目录" in note for note in context["notes"])


@pytest.mark.asyncio
async def test_build_data_analyze_context_keeps_directory_when_selected_id_missing(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "id": datasource_id,
                "name": "订单数据源",
                "source_type": "mysql",
                "ds_tables": [],
                "semantic_configs": [
                    {
                        "id": "sem_order",
                        "name": "订单指标集",
                        "metrics": [{"name": "订单金额"}],
                        "dimensions": [{"name": "订单状态"}],
                    }
                ],
            }

    monkeypatch.setattr(data_analyze_context, "get_datasource_manager", lambda: FakeManager())

    context = await data_analyze_context.build_data_analyze_context("ds_1", "missing_semantic")

    assert context["tables"] == []
    assert context["semantic_configs"] == []
    assert "## 当前可见指标集目录" in context["semantic_config_directory_info"]
    assert "名称：订单指标集" in context["semantic_config_directory_info"]
    assert any("仅注入指标集轻量目录" in note for note in context["notes"])


def test_data_analyze_context_truncate_short_and_long_text():
    assert data_analyze_context._truncate("短文本", 10) == "短文本"
    assert data_analyze_context._truncate("abcdef", 3) == "abc..."


@pytest.mark.asyncio
async def test_build_data_analyze_context_missing_datasource_and_fallback_configs(monkeypatch):
    class MissingManager:
        async def get_datasource_async(self, datasource_id):
            return None

    monkeypatch.setattr(data_analyze_context, "get_datasource_manager", lambda: MissingManager())
    missing = await data_analyze_context.build_data_analyze_context("missing_ds")
    assert missing["datasource"] == {"id": "missing_ds", "missing": True}

    class FallbackManager:
        async def get_datasource_async(self, datasource_id):
            return {
                "id": datasource_id,
                "name": "普通数据源",
                "description": "描述",
                "source_type": "mysql",
                "ds_tables": [],
                "semantic_configs": None,
            }

        async def get_semantic_configs_async(self, datasource_id):
            return [{"id": "cfg_a", "name": "指标集A", "metrics": [], "dimensions": []}]

    monkeypatch.setattr(data_analyze_context, "get_datasource_manager", lambda: FallbackManager())
    fallback = await data_analyze_context.build_data_analyze_context("ds_a")
    assert fallback["datasource"]["name"] == "普通数据源"
    assert fallback["tables"] == []
    assert "名称：指标集A" in fallback["semantic_config_directory_info"]
    assert fallback["semantic_configs"] == []
