import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.services.dashboard import runtime as runtime_module
from apps.chat_data_app.services.dashboard.runtime import execute_dashboard_panel_sql_with_target
from apps.chat_data_app.services.query.contract import QuerySource


@pytest.mark.asyncio
async def test_dashboard_single_member_execution_passes_dashboard_source_and_row_limit(monkeypatch):
    """看板单成员执行：source=DASHBOARD + max_rows 必须透传，避免审计口径与缓存键漂移。"""
    captured = {}

    class FakeDashboardRuntime:
        async def execute_sql_for_member(self, **kwargs):
            captured.update(kwargs)
            return {
                "member_datasource_id": "ds-1",
                "member_name": "库1",
                "sql": kwargs["sql"],
                "status": "succeeded",
                "columns": [],
                "data": [],
                "total_count": 0,
            }

    async def fake_setting(_key):
        return 500

    async def fake_timeout(_source_type):
        return 60

    async def fake_operation(run):
        return await run()

    monkeypatch.setattr(runtime_module, "get_runtime_setting", fake_setting)
    monkeypatch.setattr(runtime_module, "resolve_query_timeout", fake_timeout)
    monkeypatch.setattr(runtime_module, "run_dashboard_sql_operation", fake_operation)
    monkeypatch.setattr(runtime_module, "dashboard_runtime", lambda: FakeDashboardRuntime())

    await execute_dashboard_panel_sql_with_target(
        {"sql": "select 1", "datasource_id": "ds-1"},
        execution_target={"source_type": "mysql"},
    )

    assert captured["source"] is QuerySource.DASHBOARD
    assert captured["max_rows"] == 500
