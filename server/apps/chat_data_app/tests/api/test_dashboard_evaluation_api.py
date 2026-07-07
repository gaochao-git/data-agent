from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import dashboard as dashboard_routes
from apps.chat_data_app.routers import evaluation as evaluation_routes


def test_list_dashboards_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    response = client.get("/api/chatdata/dashboards", params={"scope": "public"}, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["total"] == 1
    assert payload["data"]["dashboards"][0]["dashboard_id"] == "dash_a"
    assert payload["data"]["dashboards"][0]["scope"] == "public"
    assert payload["data"]["dashboards"][0]["can_manage"] is True


def test_create_dashboard_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    response = client.post(
        "/api/chatdata/dashboards",
        json={"name": "经营看板", "description": "核心指标", "datasource_id": "ds_a", "theme": "light"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["dashboard_id"] == "dash_new"
    assert payload["data"]["user_id"] == "user_a"
    assert fake_dashboard_service.created_dashboard["name"] == "经营看板"
    assert fake_dashboard_service.created_dashboard["user_id"] == "user_a"
    assert payload["data"]["can_manage"] is True


def test_delete_public_dashboard_denies_non_owner(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)
    fake_dashboard_service.dashboard["user_id"] = "other_user"
    fake_dashboard_service.dashboard["is_public"] = True

    response = client.delete("/api/chatdata/dashboards/dash_a", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_DASHBOARD_PERMISSION_DENIED
    assert payload["msg"] == "无权限操作该看板"


def test_add_panel_api_contract(client, auth_headers, fake_dashboard_service, monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: fake_dashboard_service)

    response = client.post(
        "/api/chatdata/dashboards/dash_a/panels",
        json={
            "name": "城市销售额",
            "sql": "select city, sum(amount) amount from orders group by city",
            "chart_type": "bar",
            "datasource_id": "ds_a",
            "layout_w": 6,
            "layout_h": 4,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["panel_id"] == "panel_new"
    assert payload["data"]["dashboard_id"] == "dash_a"
    assert payload["data"]["user_id"] == "user_a"
    assert fake_dashboard_service.added_panel["chart_type"] == "bar"
    assert fake_dashboard_service.added_panel["datasource_id"] == "ds_a"


def test_get_evaluation_cases_preserves_limit_offset_contract(client, auth_headers, fake_datasource_manager, monkeypatch):
    async def fake_load_scene_case_set(datasource_id, config_id):
        return {
            "business_domain": "订单分析",
            "cases": [
                {
                    "id": "case_1",
                    "nl": "查询销售额",
                    "gold_dsl": {
                        "metrics": ["销售额"],
                        "dimensions": [],
                        "filters": [],
                        "having": [],
                        "order_by": [],
                        "limit": None,
                        "offset": None,
                    },
                    "gold_sql": "select sum(amount) from orders",
                    "query_type": "aggregation",
                    "difficulty": "easy",
                    "source": "human",
                    "business_domain": "订单分析",
                }
            ],
        }

    monkeypatch.setattr(evaluation_routes, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(evaluation_routes, "_load_scene_case_set", fake_load_scene_case_set)

    response = client.get("/api/chatdata/datasources/ds_a/semantic-configs/cfg_a/evaluation-cases", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    case = payload["data"]["cases"][0]
    assert "limit" in case["gold_dsl"]
    assert "offset" in case["gold_dsl"]
    assert case["gold_dsl"]["limit"] is None
    assert case["gold_dsl"]["offset"] is None
    assert payload["data"]["scene_id"] == "ds_a__cfg_a"


def test_save_evaluation_cases_api_contract(client, auth_headers, fake_datasource_manager, monkeypatch):
    saved = {}

    async def fake_compile_scene_case_gold_sql(datasource_id, config_id, case_payload):
        compiled = dict(case_payload)
        compiled["gold_sql"] = "select sum(amount) from orders"
        return compiled

    async def fake_save_scene_case_set(datasource_id, config_id, case_set_name, business_domain, cases, operator_id=None, operator_name=None):
        saved.update({
            "datasource_id": datasource_id,
            "config_id": config_id,
            "case_set_name": case_set_name,
            "business_domain": business_domain,
            "cases": cases,
            "operator_id": operator_id,
            "operator_name": operator_name,
        })
        return "case_set_a"

    monkeypatch.setattr(evaluation_routes, "get_datasource_manager", lambda: fake_datasource_manager)
    monkeypatch.setattr(evaluation_routes, "_compile_scene_case_gold_sql", fake_compile_scene_case_gold_sql)
    monkeypatch.setattr(evaluation_routes, "_save_scene_case_set", fake_save_scene_case_set)

    response = client.put(
        "/api/chatdata/datasources/ds_a/semantic-configs/cfg_a/evaluation-cases",
        json={
            "business_domain": "订单分析",
            "cases": [
                {
                    "id": "case_1",
                    "nl": "查询销售额",
                    "gold_dsl": {
                        "metrics": ["销售额"],
                        "dimensions": [],
                        "filters": [],
                        "having": [],
                        "order_by": [],
                        "limit": None,
                        "offset": None,
                    },
                    "gold_sql": "",
                    "query_type": "aggregation",
                    "difficulty": "easy",
                    "source": "human",
                    "business_domain": "订单分析",
                }
            ],
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["total"] == 1
    assert payload["data"]["cases"][0]["gold_dsl"]["limit"] is None
    assert payload["data"]["cases"][0]["gold_dsl"]["offset"] is None
    assert saved["case_set_name"] == "订单分析"
    assert saved["operator_id"] == "user_a"
    assert saved["operator_name"] == "测试用户"
    assert saved["cases"][0]["gold_sql"] == "select sum(amount) from orders"
