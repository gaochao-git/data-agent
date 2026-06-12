import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
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
                        "metrics": [{"name": "风险事件数", "table": "risk_event"}],
                        "dimensions": [{"name": "风险等级", "table": "risk_event", "type": "categorical"}],
                    }
                ],
            }

    monkeypatch.setattr(data_analyze_context, "get_datasource_manager", lambda: FakeManager())

    context = await data_analyze_context.build_data_analyze_context("ds_1", "sem_1")

    assert context["datasource"]["name"] == "风险数据源"
    assert context["tables"][0]["table_name"] == "risk_event"
    assert context["tables"][0]["columns"][1]["name"] == "risk_level"
    assert context["semantic_configs"][0]["selected"] is True
    assert context["semantic_configs"][0]["source_type"] == "hive"
    assert context["semantic_configs"][0]["metrics"][0]["name"] == "风险事件数"
