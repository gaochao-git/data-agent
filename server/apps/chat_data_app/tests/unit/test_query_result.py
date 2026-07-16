from apps.chat_data_app.services.query_result import (
    build_query_result_payload,
    query_result_status,
    query_result_succeeded,
)


def test_query_result_status_normalizes_legacy_success_and_cancelled_alias():
    assert query_result_status({"success": True}) == "succeeded"
    assert query_result_status({"status": " CANCELED "}) == "cancelled"
    assert query_result_status({"status": "running"}) == "running"
    assert query_result_succeeded({"status": "succeeded"}) is True
    assert query_result_succeeded({"status": "failed"}) is False


def test_build_query_result_payload_keeps_data_only_in_member_results():
    payload = build_query_result_payload(
        sql="select 1",
        member_results=[
            {
                "success": True,
                "member_datasource_id": "m1",
                "member_name": "机构1",
                "columns": ["a"],
                "data": [{"a": 1}],
                "total_count": 1,
                "execution_time_ms": 12,
            },
            {
                "status": "failed",
                "member_datasource_id": "m2",
                "error": "执行失败",
            },
        ],
    )

    assert payload["status"] == "succeeded"
    assert payload["total_count"] == 1
    assert payload["execution_time_ms"] == 12
    assert payload["error"] is None
    assert "data" not in payload
    assert "columns" not in payload
    assert payload["results"][0]["status"] == "succeeded"
    assert "success" not in payload["results"][0]


def test_build_query_result_payload_uses_member_errors_for_failed_result():
    payload = build_query_result_payload(
        sql="select 1",
        member_results=[
            {"status": "failed", "error": "错误A"},
            {"status": "cancelled", "error": "已停止"},
        ],
        fallback_error="兜底错误",
    )

    assert payload["status"] == "failed"
    assert payload["error"] == "错误A；已停止"
    assert payload["total_count"] == 0
