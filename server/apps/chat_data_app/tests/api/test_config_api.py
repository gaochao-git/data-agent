from apps.chat_data_app.routers import config as config_routes


def test_models_api_contract(client, monkeypatch):
    monkeypatch.setattr(
        config_routes,
        "get_chat_data_model_options",
        lambda: [{"label": "Kimi", "value": "kimi-k2"}],
    )

    response = client.get("/api/chatdata/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"] == [{"label": "Kimi", "value": "kimi-k2"}]


def test_runtime_config_api_contract(client, monkeypatch):
    monkeypatch.setattr(
        config_routes,
        "get_chat_data_runtime_config",
        lambda: {"enableDataAnalyze": True, "defaultRowLimit": 100},
    )

    response = client.get("/api/chatdata/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["enableDataAnalyze"] is True
    assert payload["data"]["defaultRowLimit"] == 100
