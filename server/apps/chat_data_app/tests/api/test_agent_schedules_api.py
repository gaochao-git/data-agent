"""智能体定时任务路由（用户端 + 后台管理端）API 契约测试。

service 层逻辑由 unit 测试覆盖；本文件只验证路由层的
入参透传、错误码映射、鉴权联动与统一响应结构。
"""

import pytest

from apps.chat_data_app.routers import agent_schedule_admin as admin_routes
from apps.chat_data_app.routers import agent_schedules as schedule_routes
from utils.error_codes import ErrorCode


class FakeScheduleService:
    """记录调用参数的内存 service，按预先注入的异常/返回值走分支。"""

    def __init__(self):
        self.calls: dict[str, dict] = {}
        self.results: dict[str, object] = {}
        self.errors: dict[str, Exception] = {}

    def _record(self, name: str, kwargs: dict):
        self.calls[name] = kwargs
        if name in self.errors:
            raise self.errors[name]
        return self.results.get(name, {"ok": True})

    async def list_jobs(self, **kwargs):
        return self._record("list_jobs", kwargs)

    async def create_job(self, **kwargs):
        return self._record("create_job", kwargs)

    async def update_job(self, **kwargs):
        return self._record("update_job", kwargs)

    async def delete_job(self, **kwargs):
        return self._record("delete_job", kwargs)

    async def list_job_runs(self, **kwargs):
        return self._record("list_job_runs", kwargs)

    async def trigger_now(self, **kwargs):
        return self._record("trigger_now", kwargs)

    async def get_admin_overview(self):
        return self._record("get_admin_overview", {})

    async def set_paused(self, **kwargs):
        return self._record("set_paused", kwargs)

    async def list_all_jobs(self, **kwargs):
        return self._record("list_all_jobs", kwargs)

    async def admin_set_job_enabled(self, **kwargs):
        return self._record("admin_set_job_enabled", kwargs)

    async def admin_list_job_runs(self, **kwargs):
        return self._record("admin_list_job_runs", kwargs)


class FakeRuntime:
    def __init__(self, run_id="run_1"):
        self.run_id = run_id
        self.calls: list[str] = []

    async def dispatch_trigger(self, trigger_id: str):
        self.calls.append(trigger_id)
        return {"run_id": self.run_id, "queued": self.run_id is None}


@pytest.fixture()
def fake_service(monkeypatch):
    service = FakeScheduleService()
    monkeypatch.setattr(schedule_routes, "agent_schedule_service", service)
    monkeypatch.setattr(admin_routes, "agent_schedule_service", service)
    return service


@pytest.fixture()
def fake_runtime(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(schedule_routes, "agent_schedule_runtime", runtime)
    return runtime


# ---------------------------------------------------------------------------
# 用户端路由
# ---------------------------------------------------------------------------


def test_list_agent_schedules_contract(client, auth_headers, fake_service):
    fake_service.results["list_jobs"] = {"items": [{"job_id": "job_a"}], "total": 1}

    response = client.get("/api/chatdata/agent-schedules?limit=500&offset=-1", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["items"][0]["job_id"] == "job_a"
    assert fake_service.calls["list_jobs"]["limit"] == 200
    assert fake_service.calls["list_jobs"]["offset"] == 0
    assert fake_service.calls["list_jobs"]["user_id"] == "user_a"


def test_list_agent_schedules_requires_user_identity(client, auth_headers):
    service_only_headers = {"X-Service-Token": auth_headers["X-Service-Token"]}
    response = client.get("/api/chatdata/agent-schedules", headers=service_only_headers)
    assert response.status_code == 401


def test_create_agent_schedule_contract(client, auth_headers, fake_service):
    fake_service.results["create_job"] = {"job_id": "job_new", "next_run_at": "2026-09-26 08:00:00"}
    body = {
        "title": "每日汇总",
        "prompt": "汇总昨日 GMV",
        "conversation_id": "conv_1",
        "schedule_type": "cron",
        "cron_expr": "0 8 * * *",
    }

    response = client.post("/api/chatdata/agent-schedules", json=body, headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["job_id"] == "job_new"
    kwargs = fake_service.calls["create_job"]
    assert kwargs["title"] == "每日汇总"
    assert kwargs["conversation_id"] == "conv_1"
    assert kwargs["user_id"] == "user_a"
    assert kwargs["username"] == "测试用户"


def test_create_agent_schedule_maps_service_errors(client, auth_headers, fake_service):
    cases = [
        (schedule_routes.AgentScheduleConversationInvalidError("会话无效"), ErrorCode.CHATDATA_SCHEDULE_CONVERSATION_INVALID),
        (schedule_routes.AgentScheduleValidationError("参数无效"), ErrorCode.CHATDATA_SCHEDULE_INVALID),
        (schedule_routes.AgentScheduleLimitExceededError("超出配额"), ErrorCode.CHATDATA_SCHEDULE_LIMIT_EXCEEDED),
    ]
    body = {"title": "t", "prompt": "p"}
    for error, code in cases:
        fake_service.errors["create_job"] = error
        response = client.post("/api/chatdata/agent-schedules", json=body, headers=auth_headers)
        payload = response.json()
        assert payload["status"] == "error"
        assert payload["code"] == code


def test_create_agent_schedule_unexpected_error_returns_business_error(client, auth_headers, fake_service):
    fake_service.errors["create_job"] = RuntimeError("boom")
    response = client.post("/api/chatdata/agent-schedules", json={"title": "t", "prompt": "p"}, headers=auth_headers)
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_SCHEDULE_CREATE_FAILED


def test_update_agent_schedule_clear_effective_end_time(client, auth_headers, fake_service):
    response = client.put(
        "/api/chatdata/agent-schedules/job_a",
        json={"effective_end_time": None},
        headers=auth_headers,
    )

    assert response.status_code == 200
    kwargs = fake_service.calls["update_job"]
    assert kwargs["job_id"] == "job_a"
    assert kwargs["clear_effective_end_time"] is True


def test_update_agent_schedule_not_found(client, auth_headers, fake_service):
    fake_service.errors["update_job"] = schedule_routes.AgentScheduleNotFoundError("任务不存在")
    response = client.put("/api/chatdata/agent-schedules/job_x", json={"title": "n"}, headers=auth_headers)
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND


def test_delete_agent_schedule_contract(client, auth_headers, fake_service):
    response = client.delete("/api/chatdata/agent-schedules/job_a", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert fake_service.calls["delete_job"]["job_id"] == "job_a"


def test_delete_agent_schedule_not_found(client, auth_headers, fake_service):
    fake_service.errors["delete_job"] = schedule_routes.AgentScheduleNotFoundError("任务不存在")
    response = client.delete("/api/chatdata/agent-schedules/job_x", headers=auth_headers)
    assert response.json()["code"] == ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND


def test_list_agent_schedule_runs_contract(client, auth_headers, fake_service):
    fake_service.results["list_job_runs"] = {"items": [], "total": 0}

    response = client.get("/api/chatdata/agent-schedules/job_a/runs?limit=10", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert fake_service.calls["list_job_runs"]["limit"] == 10


def test_trigger_agent_schedule_now_dispatches(client, auth_headers, fake_service, fake_runtime):
    fake_service.results["trigger_now"] = {"trigger_id": "trg_1", "status": "pending"}

    response = client.post("/api/chatdata/agent-schedules/job_a/trigger-now", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["run_id"] == "run_1"
    assert "已触发执行" in payload["msg"]
    assert fake_runtime.calls == ["trg_1"]


def test_trigger_agent_schedule_now_busy_queues(client, auth_headers, fake_service, monkeypatch):
    fake_service.results["trigger_now"] = {"trigger_id": "trg_1", "status": "pending"}
    monkeypatch.setattr(schedule_routes, "agent_schedule_runtime", FakeRuntime(run_id=None))
    response = client.post("/api/chatdata/agent-schedules/job_a/trigger-now", headers=auth_headers)
    assert "队列" in response.json()["msg"]


# ---------------------------------------------------------------------------
# 后台管理路由
# ---------------------------------------------------------------------------


def test_admin_overview_contract(client, auth_headers, fake_service):
    fake_service.results["get_admin_overview"] = {"paused": False, "job_total": 3}

    response = client.get("/api/chatdata/admin/agent-schedules/overview", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"]["job_total"] == 3


def test_admin_pause_contract(client, auth_headers, fake_service):
    response = client.put(
        "/api/chatdata/admin/agent-schedules/pause",
        json={"paused": True, "reason": "发布窗口"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert "已停止" in response.json()["msg"]
    kwargs = fake_service.calls["set_paused"]
    assert kwargs["paused"] is True
    assert kwargs["reason"] == "发布窗口"
    assert kwargs["user_id"] == "user_a"


def test_admin_list_jobs_filters_passthrough(client, auth_headers, fake_service):
    response = client.get(
        "/api/chatdata/admin/agent-schedules?keyword=gmv&enabled=true&expired=false",
        headers=auth_headers,
    )

    assert response.status_code == 200
    kwargs = fake_service.calls["list_all_jobs"]
    assert kwargs["keyword"] == "gmv"
    assert kwargs["enabled"] is True
    assert kwargs["expired"] is False


def test_admin_set_enabled_maps_errors(client, auth_headers, fake_service):
    fake_service.errors["admin_set_job_enabled"] = schedule_routes.AgentScheduleValidationError("参数无效")
    response = client.put(
        "/api/chatdata/admin/agent-schedules/job_a/enabled",
        json={"enabled_flag": 0},
        headers=auth_headers,
    )
    assert response.json()["code"] == ErrorCode.CHATDATA_SCHEDULE_INVALID


def test_admin_set_enabled_success(client, auth_headers, fake_service):
    response = client.put(
        "/api/chatdata/admin/agent-schedules/job_a/enabled",
        json={"enabled_flag": 1},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert fake_service.calls["admin_set_job_enabled"]["enabled_flag"] == 1


def test_admin_list_runs_not_found(client, auth_headers, fake_service):
    fake_service.errors["admin_list_job_runs"] = schedule_routes.AgentScheduleNotFoundError("任务不存在")
    response = client.get("/api/chatdata/admin/agent-schedules/job_x/runs", headers=auth_headers)
    assert response.json()["code"] == ErrorCode.CHATDATA_SCHEDULE_NOT_FOUND


def test_admin_list_runs_unexpected_error(client, auth_headers, fake_service):
    fake_service.errors["admin_list_job_runs"] = RuntimeError("boom")
    response = client.get("/api/chatdata/admin/agent-schedules/job_a/runs", headers=auth_headers)
    assert response.json()["code"] == ErrorCode.CHATDATA_SCHEDULE_HISTORY_FAILED


@pytest.mark.parametrize(
    "method,path,body,service_call,code",
    [
        ("get", "/api/chatdata/agent-schedules", None, "list_jobs", ErrorCode.CHATDATA_SCHEDULE_LIST_FAILED),
        ("put", "/api/chatdata/agent-schedules/job_a", {"title": "n"}, "update_job", ErrorCode.CHATDATA_SCHEDULE_UPDATE_FAILED),
        ("delete", "/api/chatdata/agent-schedules/job_a", None, "delete_job", ErrorCode.CHATDATA_SCHEDULE_DELETE_FAILED),
        ("get", "/api/chatdata/agent-schedules/job_a/runs", None, "list_job_runs", ErrorCode.CHATDATA_SCHEDULE_HISTORY_FAILED),
        ("post", "/api/chatdata/agent-schedules/job_a/trigger-now", None, "trigger_now", ErrorCode.CHATDATA_SCHEDULE_TRIGGER_FAILED),
        ("get", "/api/chatdata/admin/agent-schedules/overview", None, "get_admin_overview", ErrorCode.CHATDATA_SCHEDULE_LIST_FAILED),
        ("put", "/api/chatdata/admin/agent-schedules/pause", {"paused": False}, "set_paused", ErrorCode.CHATDATA_SCHEDULE_UPDATE_FAILED),
        ("get", "/api/chatdata/admin/agent-schedules", None, "list_all_jobs", ErrorCode.CHATDATA_SCHEDULE_LIST_FAILED),
        ("put", "/api/chatdata/admin/agent-schedules/job_a/enabled", {"enabled_flag": 1}, "admin_set_job_enabled", ErrorCode.CHATDATA_SCHEDULE_UPDATE_FAILED),
    ],
)
def test_endpoints_map_unexpected_errors_to_business_codes(client, auth_headers, fake_service, method, path, body, service_call, code):
    fake_service.errors[service_call] = RuntimeError("boom")
    kwargs = {"json": body} if body is not None else {}
    response = getattr(client, method)(path, headers=auth_headers, **kwargs)
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == code


def test_trigger_agent_schedule_now_maps_validation_error(client, auth_headers, fake_service):
    fake_service.results["trigger_now"] = {"trigger_id": "trg_1"}
    fake_service.errors["trigger_now"] = schedule_routes.AgentScheduleValidationError("会话无效")
    response = client.post("/api/chatdata/agent-schedules/job_a/trigger-now", headers=auth_headers)
    assert response.json()["code"] == ErrorCode.CHATDATA_SCHEDULE_INVALID


def test_endpoints_reraise_http_exception_from_auth(client, auth_headers):
    """认证失败抛出的 HTTPException 必须原样穿透（不做业务错误码包装）。"""
    service_only_headers = {"X-Service-Token": auth_headers["X-Service-Token"]}
    assert client.put("/api/chatdata/admin/agent-schedules/pause", json={"paused": True}, headers=service_only_headers).status_code == 401
    assert client.put("/api/chatdata/admin/agent-schedules/job_a/enabled", json={"enabled_flag": 1}, headers=service_only_headers).status_code == 401
    assert client.delete("/api/chatdata/agent-schedules/job_a", headers=service_only_headers).status_code == 401
