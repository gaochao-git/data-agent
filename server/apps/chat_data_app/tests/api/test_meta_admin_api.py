"""元数据管理（Admin）接口契约测试：全局枚举值 AI 填充生成。"""

import pytest

from apps.chat_data_app.routers import meta_admin as meta_admin_routes
from utils.error_codes import ErrorCode


def test_generate_meta_enum_items_api_contract(client, auth_headers, monkeypatch):
    async def fake_generate(text, **kwargs):
        assert text == "订单状态：0 待支付、1 已支付"
        return {"enum_values": [{"value": "0", "label": "待支付"}, {"value": "1", "label": "已支付"}], "unit": ""}

    monkeypatch.setattr(meta_admin_routes, "generate_dict_candidates_from_text", fake_generate)

    response = client.post(
        "/api/chatdata/admin/meta/enums/generate",
        json={"text": "订单状态：0 待支付、1 已支付"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["enum_items"] == [{"value": "0", "label": "待支付"}, {"value": "1", "label": "已支付"}]


def test_generate_meta_enum_items_api_error(client, auth_headers, monkeypatch):
    async def fake_generate(text, **kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(meta_admin_routes, "generate_dict_candidates_from_text", fake_generate)

    response = client.post(
        "/api/chatdata/admin/meta/enums/generate",
        json={"text": "任意文本"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == int(ErrorCode.CHATDATA_META_ENUM_OPERATION_FAILED)


def test_generate_meta_enum_items_requires_text(client, auth_headers):
    response = client.post(
        "/api/chatdata/admin/meta/enums/generate",
        json={"text": ""},
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize("missing_header", [True])
def test_generate_meta_enum_items_requires_auth(client, missing_header):
    response = client.post(
        "/api/chatdata/admin/meta/enums/generate",
        json={"text": "任意文本"},
    )

    assert response.status_code in (401, 403)
