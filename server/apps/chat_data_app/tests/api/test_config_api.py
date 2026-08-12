from apps.chat_data_app.routers import config as config_routes


def test_runtime_config_api_contract(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        config_routes,
        "get_chat_data_runtime_config",
        lambda: {"enableDataAnalyze": True, "defaultRowLimit": 100},
    )

    response = client.get("/api/chatdata/config", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["enableDataAnalyze"] is True
    assert payload["data"]["defaultRowLimit"] == 100


def test_service_token_is_required_before_user_identity_for_chatdata_routes(client, auth_headers):
    service_only_headers = {"X-Service-Token": auth_headers["X-Service-Token"]}

    service_only_response = client.get("/api/chatdata/config", headers=service_only_headers)
    missing_token_response = client.get("/api/chatdata/config")
    invalid_token_response = client.get(
        "/api/chatdata/config",
        headers={"X-Service-Token": "invalid"},
    )

    assert service_only_response.status_code == 200
    assert missing_token_response.status_code == 401
    assert invalid_token_response.status_code == 401
