import asyncio

from apps.chat_data_app.services import query_cache


def test_query_cache_identity_and_status_helpers():
    identity = query_cache.build_sql_result_cache_identity(
        {"datasource_id": "ds_a", "member_datasource_id": "m1", "query_executor_type": " HTTP ", "source_type": "HIVE"},
        " select 1 ",
    )

    assert identity["datasource_id"] == "ds_a"
    assert identity["member_datasource_id"] == "m1"
    assert identity["executor_type"] == "http"
    assert identity["source_type"] == "hive"
    assert query_cache.is_http_execution_target({"query_executor_type": " HTTP "}) is True
    assert query_cache.is_http_remote_ref_cache({"cache_type": "HTTP_REMOTE_REF", "remote_task_id": "rt1"}) is True
    assert query_cache.is_http_remote_ref_cache({"cache_type": "http_remote_ref", "remote_task_id": ""}) is False
    assert query_cache.normalize_sql_cache_text(" select 1 ") == "select 1"


def test_query_cache_remote_ref_result_branches():
    missing_id = asyncio.run(query_cache.fetch_http_remote_ref_cache_result(
        {"source_type": "mysql"},
        fetch_remote_result=lambda *_args, **_kwargs: asyncio.sleep(0, result={}),
    ))
    assert missing_id["_cache_ref_unavailable"] is True
    assert "缺少远端任务" in missing_id["error"]

    async def unavailable_fetch(*_args, **_kwargs):
        return {"success": False, "status": "failed", "error": "已过期", "columns": ["id"], "data": [{"id": 1}], "total_count": 1}

    unavailable = asyncio.run(query_cache.fetch_http_remote_ref_cache_result(
        {"remote_task_id": "rt2"},
        fetch_remote_result=unavailable_fetch,
    ))
    assert unavailable["_cache_ref_unavailable"] is True
    assert unavailable["status"] == "failed"
    assert unavailable["columns"] == ["id"]

    async def success_fetch(*_args, **_kwargs):
        return {"success": True, "columns": ["id"], "data": [{"id": 1}], "total_count": 1, "execution_time_ms": 8}

    success = asyncio.run(query_cache.fetch_http_remote_ref_cache_result(
        {
            "remote_task_id": "rt3",
            "source_type": "mysql",
            "cache_age_seconds": 3,
            "cache_expires_in_seconds": 9,
        },
        fetch_remote_result=success_fetch,
    ))
    assert success["status"] == "succeeded"
    assert "success" not in success
    assert success["remote_task_id"] == "rt3"
    assert success["cache_hit"] is True
    assert success["cache_age_seconds"] == 3
