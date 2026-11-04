"""统一错误响应：业务文案透传、基础设施错误统一兜底（详细异常只进日志）。"""

from __future__ import annotations

import json

from utils.error_codes import ErrorCode
from utils.response import SAFE_INTERNAL_ERROR_MESSAGE, error_response


def _payload(response) -> dict:
    return json.loads(response.body)


def test_infra_error_uses_fallback_without_leaking_raw_error():
    payload = _payload(
        error_response(
            RuntimeError("Table 'demo.orders' doesn't exist"),
            code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED,
            fallback="操作失败",
        )
    )
    assert payload["status"] == "error"
    assert payload["msg"] == "操作失败"
    assert "detail" not in payload
    assert "trace_id" not in payload


def test_business_value_error_message_is_passed_through():
    payload = _payload(
        error_response(
            ValueError("数据源不存在: ds_1"),
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            fallback="操作失败",
        )
    )
    assert payload["msg"] == "数据源不存在: ds_1"


def test_internal_markers_are_masked_in_fallback():
    payload = _payload(
        error_response(
            RuntimeError("pymysql.err.OperationalError: boom"),
            code=ErrorCode.CHATDATA_DATASOURCE_OPERATION_FAILED,
            fallback="操作失败",
        )
    )
    assert payload["msg"] == "操作失败"
    assert SAFE_INTERNAL_ERROR_MESSAGE not in payload["msg"]
