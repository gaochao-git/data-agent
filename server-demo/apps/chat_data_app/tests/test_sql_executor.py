import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")

from apps.chat_data_app.services import sql_executor


def test_get_executor_returns_hive_http_executor():
    executor = sql_executor.get_executor({
        "source_type": "hive",
        "query_executor_type": "http",
        "member_name": "hive-localhost",
        "source_config": {"namespace": "hive-localhost"},
    })

    assert isinstance(executor, sql_executor.HiveHttpExecutor)
    assert executor.namespace == "hive-localhost"
    assert executor.source_type == "hive"


def test_get_executor_returns_mysql_http_executor():
    executor = sql_executor.get_executor({
        "source_type": "mysql",
        "query_executor_type": "http",
        "member_name": "mysql-http-a",
        "source_config": {"namespace": "mysql-http-a"},
    })

    assert isinstance(executor, sql_executor.HiveHttpExecutor)
    assert executor.namespace == "mysql-http-a"
    assert executor.source_type == "mysql"


def test_hive_http_executor_submits_sql_namespace_limit_and_user_id(monkeypatch):
    captured_requests = []
    captured_remote_tasks = []

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id=""):
        captured_requests.append({
            "method": method,
            "url": url,
            "json_payload": json_payload,
            "task_id": task_id,
        })
        if method == "POST":
            return {"code": 20000, "data": {"task_id": "task_001"}, "msg": "提交成功"}
        return {
            "code": 20000,
            "data": {
                "status": "succeeded",
                "result": {
                    "columns": ["a"],
                    "data": [{"a": 1}],
                    "total_count": 1,
                    "execution_time_ms": 12,
                },
            },
            "msg": "执行成功",
        }

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    executor = sql_executor.HiveHttpExecutor(namespace="hive-localhost")
    result = asyncio.run(executor.execute("select 1", limit=50, user_id="chatdata_user", remote_task_callback=captured_remote_tasks.append))

    assert result["success"] is True
    assert captured_requests[0]["json_payload"] == {
        "sql": "select 1 LIMIT 50",
        "namespace": "hive-localhost",
        "limit": 50,
        "user_id": "chatdata_user",
    }
    assert captured_remote_tasks == [{
        "remote_task_id": "task_001",
        "namespace": "hive-localhost",
        "source_type": "hive",
    }]


def test_mysql_http_executor_uses_mysql_timeout(monkeypatch):
    executor = sql_executor.HiveHttpExecutor(namespace="mysql-http-a", source_type="mysql")

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id=""):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://query/submit")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://query/status/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    result = asyncio.run(executor.execute("select 1"))

    assert result["success"] is False
    assert result["error"] == "查询超时（60秒）"


def test_hive_http_executor_returns_submit_msg_when_code_is_not_20000(monkeypatch):
    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id=""):
        return {"code": 50001, "task_id": "", "msg": "SQL 不合法"}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    executor = sql_executor.HiveHttpExecutor(namespace="hive-localhost")
    result = asyncio.run(executor.execute("select 1"))

    assert result["success"] is False
    assert result["error"] == "SQL 不合法"


def test_hive_http_executor_returns_status_msg_when_status_query_failed(monkeypatch):
    captured_calls = []

    async def fake_request_json(self, method: str, url: str, *, json_payload=None, task_id=""):
        captured_calls.append(method)
        if method == "POST":
            return {"code": 20000, "data": {"task_id": "task_001"}, "msg": "提交成功"}
        return {"code": 50003, "data": {"status": "failed"}, "msg": "无权限访问该表"}

    monkeypatch.setattr(sql_executor, "HTTP_SUBMIT_URL", "http://hive/query-tasks")
    monkeypatch.setattr(sql_executor, "HTTP_STATUS_URL", "http://hive/query-tasks/{task_id}")
    monkeypatch.setattr(sql_executor.HiveHttpExecutor, "_request_json", fake_request_json)

    executor = sql_executor.HiveHttpExecutor(namespace="hive-localhost")
    result = asyncio.run(executor.execute("select 1"))

    assert captured_calls == ["POST", "GET"]
    assert result["success"] is False
    assert result["error"] == "无权限访问该表"
