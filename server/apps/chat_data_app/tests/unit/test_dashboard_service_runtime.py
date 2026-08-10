import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.dashboard import service as dashboard_service_module
from apps.chat_data_app.services.dashboard.service import DashboardService
from utils.models import Base


@pytest.fixture()
def dashboard_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(dashboard_service_module, "get_session", fake_get_session)
    yield
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_dashboard_service_crud_and_visibility(dashboard_db):
    service = DashboardService()

    dashboard = await service.create_dashboard(
        name="经营看板",
        user_id="user_a",
        description="核心指标",
        datasource_id="ds_a",
        theme="dark",
    )
    listed_mine = await service.list_dashboards(user_id="user_a", scope="mine")
    listed_other = await service.list_dashboards(user_id="user_b", scope="mine")
    published = await service.set_dashboard_public(dashboard["id"], user_id="user_a", is_public=True)
    listed_public = await service.list_dashboards(user_id="user_b", scope="public")
    detail_for_other = await service.get_dashboard(dashboard["id"], user_id="user_b", include_public=True)

    assert dashboard["name"] == "经营看板"
    assert dashboard["is_public"] is False
    assert len(listed_mine) == 1
    assert listed_other == []
    assert published["is_public"] is True
    assert listed_public[0]["id"] == dashboard["id"]
    assert detail_for_other["id"] == dashboard["id"]


@pytest.mark.asyncio
async def test_dashboard_service_panel_lifecycle_and_layout(dashboard_db):
    service = DashboardService()
    dashboard = await service.create_dashboard(name="经营看板", user_id="user_a", datasource_id="ds_a")

    first_panel = await service.add_panel(
        dashboard_id=dashboard["id"],
        name="城市销售额",
        sql="select city, sum(amount) amount from orders group by city",
        chart_type="bar",
        chart_config={"color": "#1677ff"},
        layout_w=6,
        layout_h=4,
        datasource_id="ds_a",
        user_id="user_a",
    )
    second_panel = await service.add_panel(
        dashboard_id=dashboard["id"],
        name="订单数",
        sql="select count(*) cnt from orders",
        chart_type="kpi",
        layout_w=3,
        layout_h=2,
        user_id="user_a",
    )
    updated_panel = await service.update_panel(
        dashboard_id=dashboard["id"],
        panel_id=first_panel["id"],
        name="城市销售额V2",
        chart_type="line",
        chart_config={"smooth": True},
        layout_x=1,
        layout_y=2,
        layout_w=8,
        layout_h=5,
        refresh_interval=60,
        user_id="user_a",
    )
    updated_count = await service.update_panels_layout(
        dashboard_id=dashboard["id"],
        layouts=[
            {"panel_id": first_panel["id"], "x": 0, "y": 0, "w": 12, "h": 6},
            {"i": second_panel["id"], "x": 0, "y": 6, "w": 6, "h": 4},
            {"panel_id": "missing", "x": 0, "y": 0, "w": 1, "h": 1},
            {"x": 0, "y": 0, "w": 1, "h": 1},
        ],
        user_id="user_a",
    )
    detail = await service.get_dashboard(dashboard["id"], user_id="user_a")
    panel_detail = await service.get_panel(first_panel["id"], dashboard_id=dashboard["id"], user_id="user_a")
    deleted_panel = await service.delete_panel(dashboard["id"], first_panel["id"], user_id="user_a")
    deleted_dashboard = await service.delete_dashboard(dashboard["id"], user_id="user_a")

    assert first_panel["layout"]["x"] == 0
    assert second_panel["layout"]["y"] == 4
    assert updated_panel["name"] == "城市销售额V2"
    assert updated_panel["chart_config"] == {"smooth": True}
    assert updated_count == 2
    assert len(detail["panels"]) == 2
    assert panel_detail["layout"]["w"] == 12
    assert deleted_panel is True
    assert deleted_dashboard is True
    assert await service.get_dashboard(dashboard["id"], user_id="user_a") is None


@pytest.mark.asyncio
async def test_dashboard_service_returns_none_or_false_for_missing_or_wrong_owner(dashboard_db):
    service = DashboardService()
    dashboard = await service.create_dashboard(name="经营看板", user_id="user_a")

    assert await service.set_dashboard_public("missing", user_id="user_a", is_public=True) is None
    assert await service.update_dashboard(dashboard["id"], name="无权", user_id="user_b") is None
    assert await service.add_panel("missing", name="x", sql="select 1", user_id="user_a") is None
    assert await service.update_panel(dashboard["id"], "missing", name="x", user_id="user_a") is None
    assert await service.update_panels_layout("missing", [], user_id="user_a") is None
    assert await service.delete_panel(dashboard["id"], "missing", user_id="user_a") is False
    assert await service.delete_dashboard(dashboard["id"], user_id="user_b") is False


@pytest.mark.asyncio
async def test_dashboard_service_update_and_access_branches(dashboard_db):
    service = DashboardService()
    dashboard = await service.create_dashboard(name="经营看板", user_id="user_a", description="", theme="light")

    updated = await service.update_dashboard(
        dashboard["id"],
        name="经营看板V2",
        description="更新描述",
        theme="dark",
        background_id="bg_a",
        user_id="user_a",
    )
    hidden_before_publish = await service.get_dashboard(dashboard["id"], user_id="user_b", include_public=False)
    public_dashboard = await service.set_dashboard_public(dashboard["id"], user_id="user_a", is_public=True)
    hidden_when_public_not_included = await service.get_dashboard(dashboard["id"], user_id="user_b", include_public=False)
    no_user_detail = await service.get_dashboard(dashboard["id"])
    all_dashboards = await service.list_dashboards(scope="all")
    no_user_mine_scope = await service.list_dashboards(scope="mine")

    assert updated["name"] == "经营看板V2"
    assert updated["description"] == "更新描述"
    assert updated["theme"] == "dark"
    assert updated["background_id"] == "bg_a"
    assert hidden_before_publish is None
    assert public_dashboard["is_public"] is True
    assert hidden_when_public_not_included is None
    assert no_user_detail["id"] == dashboard["id"]
    assert [item["id"] for item in all_dashboards] == [dashboard["id"]]
    assert [item["id"] for item in no_user_mine_scope] == [dashboard["id"]]


@pytest.mark.asyncio
async def test_dashboard_service_panel_explicit_layout_and_no_user_branches(dashboard_db):
    service = DashboardService()
    dashboard = await service.create_dashboard(name="经营看板", user_id="user_a")

    explicit_panel = await service.add_panel(
        dashboard_id=dashboard["id"],
        name="明细表",
        sql="select * from orders",
        chart_type="table",
        chart_config=None,
        layout_x=3,
        layout_y=4,
        layout_w=5,
        layout_h=6,
        refresh_interval=30,
        source_thread_id="thread_a",
        datasource_id="ds_a",
        member_datasource_id="member_a",
    )
    updated_panel = await service.update_panel(
        dashboard_id=dashboard["id"],
        panel_id=explicit_panel["id"],
        name="明细表V2",
        sql="select order_id from orders",
        chart_type="bar",
        chart_config={"x": "order_id"},
        layout_x=1,
        layout_y=2,
        layout_w=7,
        layout_h=8,
        refresh_interval=60,
    )
    layout_count = await service.update_panels_layout(
        dashboard_id=dashboard["id"],
        layouts=[{"panel_id": explicit_panel["id"], "x": 2, "y": 3, "w": 4, "h": 5}],
    )
    panel_detail = await service.get_panel(explicit_panel["id"], include_public=False)
    missing_panel = await service.get_panel("missing")
    deleted_panel = await service.delete_panel(dashboard["id"], explicit_panel["id"])
    deleted_again = await service.delete_panel(dashboard["id"], explicit_panel["id"])

    assert explicit_panel["layout"] == {"i": explicit_panel["id"], "x": 3, "y": 4, "w": 5, "h": 6}
    assert explicit_panel["chart_config"] is None
    assert explicit_panel["refresh_interval"] == 30
    assert explicit_panel["source_thread_id"] == "thread_a"
    assert explicit_panel["member_datasource_id"] == "member_a"
    assert updated_panel["sql"] == "select order_id from orders"
    assert updated_panel["chart_type"] == "bar"
    assert updated_panel["chart_config"] == {"x": "order_id"}
    assert updated_panel["layout"]["h"] == 8
    assert layout_count == 1
    assert panel_detail["layout"] == {"i": explicit_panel["id"], "x": 2, "y": 3, "w": 4, "h": 5}
    assert missing_panel is None
    assert deleted_panel is True
    assert deleted_again is False


@pytest.mark.asyncio
async def test_dashboard_service_delete_without_user_and_singleton(monkeypatch, dashboard_db):
    service = DashboardService()
    dashboard = await service.create_dashboard(name="经营看板", user_id="user_a")

    assert await service.delete_dashboard(dashboard["id"]) is True
    assert await service.delete_dashboard(dashboard["id"]) is False

    monkeypatch.setattr(dashboard_service_module, "_dashboard_service", None)
    first = dashboard_service_module.get_dashboard_service()
    second = dashboard_service_module.get_dashboard_service()
    assert first is second


def test_dashboard_service_dict_helpers_handle_invalid_chart_config():
    service = DashboardService()
    panel = type("Panel", (), {
        "panel_id": "panel_a",
        "dashboard_id": "dash_a",
        "panel_name": "坏配置",
        "panel_sql": "select 1",
        "chart_type": "table",
        "chart_config": "{bad json",
        "layout_x": 0,
        "layout_y": 0,
        "layout_w": 6,
        "layout_h": 4,
        "refresh_interval": None,
        "source_thread_id": None,
        "datasource_id": "ds_a",
        "create_time": None,
        "update_time": None,
        "create_by": "u1",
        "update_by": "u1",
    })()

    payload = service._panel_to_dict(panel)

    assert payload["chart_config"] is None
    assert payload["layout"] == {"i": "panel_a", "x": 0, "y": 0, "w": 6, "h": 4}


@pytest.mark.asyncio
async def test_dashboard_service_optional_false_and_audit_branches(dashboard_db):
    service = DashboardService()
    dashboard = await service.create_dashboard(name="经营看板", user_id="user_a")
    panel = await service.add_panel(
        dashboard_id=dashboard["id"],
        name="明细表",
        sql="select 1",
        user_id="user_a",
    )

    unchanged_dashboard = await service.update_dashboard(dashboard["id"])
    unchanged_panel = await service.update_panel(dashboard["id"], panel["id"])
    empty_layout_count = await service.update_panels_layout(dashboard["id"], layouts=[])
    missing_dashboard_panel_update = await service.update_panel("missing", panel["id"], name="x", user_id="user_a")
    wrong_owner_panel_update = await service.update_panel(dashboard["id"], panel["id"], name="x", user_id="user_b")
    missing_dashboard_panel_delete = await service.delete_panel("missing", panel["id"], user_id="user_a")
    deleted_panel = await service.delete_panel(dashboard["id"], panel["id"], user_id="user_a", operator_name="操作员")

    dashboard_for_delete = await service.create_dashboard(name="待删看板", user_id="user_a")
    await service.add_panel(dashboard_id=dashboard_for_delete["id"], name="面板", sql="select 1", user_id="user_a")
    deleted_dashboard = await service.delete_dashboard(dashboard_for_delete["id"], user_id="user_a", operator_name="操作员")

    assert unchanged_dashboard["id"] == dashboard["id"]
    assert unchanged_panel["id"] == panel["id"]
    assert empty_layout_count == 0
    assert missing_dashboard_panel_update is None
    assert wrong_owner_panel_update is None
    assert missing_dashboard_panel_delete is False
    assert deleted_panel is True
    assert deleted_dashboard is True


@pytest.mark.asyncio
async def test_dashboard_service_public_panel_access_branch(dashboard_db):
    service = DashboardService()
    dashboard = await service.create_dashboard(name="公共看板", user_id="user_a")
    panel = await service.add_panel(dashboard_id=dashboard["id"], name="公共面板", sql="select 1", user_id="user_a")

    assert await service.get_panel(panel["id"], user_id="user_b", include_public=True) is None
    await service.set_dashboard_public(dashboard["id"], user_id="user_a", is_public=True)
    public_panel = await service.get_panel(panel["id"], user_id="user_b", include_public=True)
    hidden_panel = await service.get_panel(panel["id"], user_id="user_b", include_public=False)

    assert public_panel["id"] == panel["id"]
    assert hidden_panel is None
