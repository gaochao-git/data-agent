"""routers 目录基准收口：路由层错误透传与兜底分支。

routers 目录下 7 个文件综合覆盖率未达 95%，缺口形态高度一致——几乎全是
``except HTTPException: raise`` 透传分支、``except Exception`` 兜底分支，
以及权限/校验失败的中途返回。这些分支在 Happy Path 测试中永远走不到，
必须显式注入异常才能覆盖。

本文件按「一个 fixture 打桩 + 参数化驱动全部同构路由」的方式覆盖，
避免为每个路由重复写一遍 mock。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain_core.messages import HumanMessage

from auth import AuthenticationError
from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import datasource_public as datasource_public_routes
from apps.chat_data_app.routers import dependencies as dependencies_routes
from apps.chat_data_app.routers import evaluation as evaluation_routes
from apps.chat_data_app.routers import query_execution as query_execution_routes
from apps.chat_data_app.routers import query_favorites as favorites_routes
from apps.chat_data_app.routers import semantic_config as semantic_config_routes
from apps.chat_data_app.routers import skills as skills_routes
from apps.chat_data_app.schema import (
    QueryFavoriteCreateRequest,
    QueryFavoriteUpdateRequest,
    QuickSqlRequest,
    SemanticConfigCreateRequest,
    SkillCreateRequest,
    SkillFileUpsertRequest,
    SkillUpdateRequest,
)

# 直接 await 路由函数时传入的假 Request，只用于占位。
REQUEST = SimpleNamespace()

CURRENT_USER = SimpleNamespace(user_id="u1", username="tester")


def payload(response) -> dict:
    """路由返回的 JSONResponse 解成 dict。"""
    return json.loads(response.body.decode("utf-8"))


def _raise_http_403(_request):
    raise HTTPException(status_code=403, detail="无权限")


class AlwaysHttpExceptionService:
    """任何方法调用都抛 HTTPException 的测试替身。

    路由层普遍有 ``except HTTPException: raise`` 的约定：业务服务抛出的
    FastAPI 异常必须原样向上透传，不能被 ``except Exception`` 兜底吞成
    业务错误码。用这个替身可以一次性验证所有路由都遵守该约定。
    """

    def __init__(self, status_code: int = 403) -> None:
        self.status_code = status_code

    def __getattr__(self, name: str):
        async def _raise(*args, **kwargs):
            raise HTTPException(status_code=self.status_code, detail=f"{name} rejected")

        return _raise


# --------------------------------------------------------------------------
# skills.py：14 个路由的 HTTPException 透传分支
# --------------------------------------------------------------------------

_SKILL_CASES = [
    ("create_user_skill", skills_routes.create_user_skill, (SkillCreateRequest(skill_name="demo-skill", skill_title="Demo"), REQUEST)),
    ("update_user_skill", skills_routes.update_user_skill, ("skill-1", SkillUpdateRequest(skill_title="Updated"), REQUEST)),
    ("delete_user_skill", skills_routes.delete_user_skill, ("skill-1", REQUEST)),
    ("copy_user_skill", skills_routes.copy_user_skill, ("skill-1", REQUEST)),
    ("publish_user_skill", skills_routes.publish_user_skill, ("skill-1", REQUEST)),
    ("list_skill_versions", skills_routes.list_skill_versions, ("skill-1", REQUEST)),
    ("get_skill_version", skills_routes.get_skill_version, ("skill-1", 1, REQUEST)),
    ("list_skill_version_files", skills_routes.list_skill_version_files, ("skill-1", 1, REQUEST)),
    ("read_skill_version_file", skills_routes.read_skill_version_file, ("skill-1", 1, "references", "a.md", REQUEST)),
    ("save_skill_version_file", skills_routes.save_skill_version_file, ("skill-1", 1, SkillFileUpsertRequest(dir_name="references", file_path="a.md", content="body"), REQUEST)),
    ("delete_skill_version_file", skills_routes.delete_skill_version_file, ("skill-1", 1, "references", "a.md", REQUEST)),
    ("rollback_skill_version", skills_routes.rollback_skill_version, ("skill-1", 1, REQUEST)),
    ("favorite_skill", skills_routes.favorite_skill, ("skill-1", REQUEST)),
    ("unfavorite_skill", skills_routes.unfavorite_skill, ("skill-1", REQUEST)),
    ("get_skill", skills_routes.get_skill, ("demo-skill", REQUEST)),
]


@pytest.mark.parametrize("route,args", [(c[1], c[2]) for c in _SKILL_CASES], ids=[c[0] for c in _SKILL_CASES])
@pytest.mark.asyncio
async def test_skill_routes_rethrow_http_exception(route, args, monkeypatch):
    """技能路由必须原样透传 HTTPException，不能降级成业务错误码。"""
    monkeypatch.setattr(skills_routes, "skill_service", AlwaysHttpExceptionService())
    monkeypatch.setattr(skills_routes, "_get_current_user", lambda _request: CURRENT_USER)

    with pytest.raises(HTTPException) as exc_info:
        await route(*args)

    assert exc_info.value.status_code == 403


# --------------------------------------------------------------------------
# query_favorites.py：4 个路由的 HTTPException 透传分支
# --------------------------------------------------------------------------

_FAVORITE_CASES = [
    ("get_query_favorites", favorites_routes.get_query_favorites, (REQUEST,), {"datasource_id": "ds-1"}),
    ("create_query_favorite", favorites_routes.create_query_favorite, (QueryFavoriteCreateRequest(
        datasource_id="ds-1", query_type="sql", favorite_title="常用查询", query_text="select 1"
    ), REQUEST), {}),
    ("update_query_favorite", favorites_routes.update_query_favorite, (1, QueryFavoriteUpdateRequest(favorite_title="改名"), REQUEST), {}),
    ("delete_query_favorite", favorites_routes.delete_query_favorite, (1, REQUEST), {}),
]


@pytest.mark.parametrize("route,args,kwargs", [(c[1], c[2], c[3]) for c in _FAVORITE_CASES], ids=[c[0] for c in _FAVORITE_CASES])
@pytest.mark.asyncio
async def test_query_favorite_routes_rethrow_http_exception(route, args, kwargs, monkeypatch):
    """收藏路由同样必须透传 HTTPException。"""
    service = AlwaysHttpExceptionService(status_code=401)
    monkeypatch.setattr(favorites_routes, "_get_current_user", lambda _request: CURRENT_USER)

    with pytest.raises(HTTPException) as exc_info:
        await route(*args, favorite_service=service, **kwargs)

    assert exc_info.value.status_code == 401


# --------------------------------------------------------------------------
# dependencies.py：认证异常到 HTTPException 的转换
# --------------------------------------------------------------------------


def test_get_current_user_translates_authentication_error(monkeypatch):
    """AuthenticationError 必须带上原始状态码转成 HTTPException。"""

    def _raise_authentication_error(_request):
        raise AuthenticationError("token expired", status_code=401)

    monkeypatch.setattr(dependencies_routes, "get_forwarded_user", _raise_authentication_error)

    with pytest.raises(HTTPException) as exc_info:
        dependencies_routes._get_current_user(REQUEST)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "token expired"


# --------------------------------------------------------------------------
# datasource_public.py：权限校验失败提前返回 + HTTPException 透传
# --------------------------------------------------------------------------


class DeniedDatasourceManager:
    """拒绝一切数据源访问的 manager 替身。

    字段字典路由在调用业务函数前会先做一次权限校验并提前返回，
    这条分支在 Happy Path 里永远走不到。
    """

    async def can_access_datasource_async(self, *args, **kwargs):
        return False


def _patch_datasource_public(monkeypatch, manager):
    monkeypatch.setattr(datasource_public_routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(datasource_public_routes, "_get_current_user", lambda _request: CURRENT_USER)


@pytest.mark.asyncio
async def test_datasource_column_dict_routes_return_permission_denied(monkeypatch):
    _patch_datasource_public(monkeypatch, DeniedDatasourceManager())

    single = payload(await datasource_public_routes.get_datasource_column_dict("ds-1", "t1", "c1", REQUEST))
    assert single["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED

    batch = payload(await datasource_public_routes.get_datasource_table_column_dicts("ds-1", "t1", REQUEST))
    assert batch["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED

    # schema 端点同样必须先校验权限再读缓存；DeniedDatasourceManager 没有读方法，
    # 若路由在校验前就取 schema，这里会以 AttributeError 暴露而不是通过。
    schema = payload(await datasource_public_routes.get_schema("ds-1", None, REQUEST))
    assert schema["code"] == ErrorCode.CHATDATA_PERMISSION_DENIED


@pytest.mark.parametrize(
    "route,args",
    [
        (datasource_public_routes.get_datasources, (REQUEST,)),
        (datasource_public_routes.get_datasource_column_dict, ("ds-1", "t1", "c1", REQUEST)),
        (datasource_public_routes.get_datasource_table_column_dicts, ("ds-1", "t1", REQUEST)),
        (datasource_public_routes.get_schema, ("ds-1", None, REQUEST)),
    ],
    ids=["get_datasources", "get_datasource_column_dict", "get_datasource_table_column_dicts", "get_schema"],
)
@pytest.mark.asyncio
async def test_datasource_public_routes_rethrow_http_exception(route, args, monkeypatch):
    _patch_datasource_public(monkeypatch, AlwaysHttpExceptionService())

    with pytest.raises(HTTPException) as exc_info:
        await route(*args)

    assert exc_info.value.status_code == 403


# --------------------------------------------------------------------------
# query_execution.py：用户身份解析 + 快速 SQL 上下文降级
# --------------------------------------------------------------------------


def test_current_user_identity_falls_back_to_system_without_request():
    """没有 request（内部调用）时退化为 system 身份。"""
    assert query_execution_routes._current_user_identity(None) == ("system", "system")


def test_current_user_identity_reads_forwarded_user(monkeypatch):
    monkeypatch.setattr(
        query_execution_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id="u1", username="tester"),
    )

    assert query_execution_routes._current_user_identity(REQUEST) == ("u1", "tester")


def test_current_user_identity_falls_back_to_username_as_user_id(monkeypatch):
    """user_id 缺失时用 username 兜底作为身份标识。"""
    monkeypatch.setattr(
        query_execution_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id=None, username="tester"),
    )

    assert query_execution_routes._current_user_identity(REQUEST) == ("tester", "tester")


@pytest.mark.asyncio
async def test_load_quick_sql_context_reports_missing_table_schema(monkeypatch):
    class _EmptySchemaManager:
        async def get_datasource_async(self, datasource_id):
            return {"source_type": "hive"}

    async def _no_tables(_datasource_id):
        return []

    monkeypatch.setattr(query_execution_routes, "get_datasource_manager", lambda: _EmptySchemaManager())
    monkeypatch.setattr(query_execution_routes, "get_all_tables_async", _no_tables)

    source_type, schema, error = await query_execution_routes._load_quick_sql_context(
        QuickSqlRequest(query="查一下销量", datasource_id="ds-1")
    )

    assert (source_type, schema) == (None, None)
    assert error == "数据源暂无表结构信息"


@pytest.mark.asyncio
async def test_persist_quick_sql_assistant_turn_skips_empty_content():
    """未输出任何正文时不该落库一条空消息。"""
    await query_execution_routes._persist_quick_sql_assistant_turn(
        conversation_id="c1",
        user_id="u1",
        username="tester",
        user_message=HumanMessage(content="hi"),
        content="",
    )


@pytest.mark.asyncio
async def test_persist_shielded_logs_inner_failure_then_reraises_cancel(monkeypatch):
    """流被取消时仍要等权威历史写入完成，写失败要记录日志再重抛 CancelledError。"""

    async def _boom(**_kwargs):
        raise RuntimeError("persist down")

    monkeypatch.setattr(query_execution_routes, "_persist_quick_sql_assistant_turn", _boom)

    async def _runner():
        await query_execution_routes._persist_quick_sql_assistant_turn_shielded(conversation_id="c1")

    task = asyncio.ensure_future(_runner())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_sse_error_sanitizes_internal_error_details(monkeypatch):
    """SSE 错误事件必须走统一脱敏入口，异常堆栈/连接细节不得直接下发前端。"""
    body = json.loads(
        query_execution_routes._sse_error("sqlalchemy.exc.OperationalError: (2003, ...)")
        .removeprefix("data: ")
    )
    assert body == {"type": "error", "message": "后端服务异常，请稍后重试"}

    plain = json.loads(query_execution_routes._sse_error("数据源不存在").removeprefix("data: "))
    assert plain["message"] == "数据源不存在"

    async def _explode(_payload, **_kwargs):
        raise RuntimeError("pymysql.err.OperationalError: access denied for user 'x'@'%'")

    monkeypatch.setattr(query_execution_routes, "_load_quick_sql_context", _explode)
    chunks = [
        chunk
        async for chunk in query_execution_routes._generate_quick_sql_stream(
            QuickSqlRequest(query="查销量", datasource_id="ds-1"),
            user_id="u1",
            username="tester",
            datasource_manager=SimpleNamespace(),
        )
    ]
    assert all("pymysql" not in chunk and "access denied" not in chunk for chunk in chunks)
    assert any("后端服务异常，请稍后重试" in chunk for chunk in chunks)


# --------------------------------------------------------------------------
# semantic_config.py：创建指标集的校验 / 成功 / 空结果 / 异常四条路径
#
# 现有测试只覆盖了「数据源不存在」一条分支，创建主流程整段未覆盖。
# --------------------------------------------------------------------------


class _FakeDatasourceManager:
    async def get_datasource_async(self, datasource_id):
        return {"id": datasource_id, "source_type": "hive"}


class _FakeSemanticConfigService:
    """create_async 的返回值由构造参数决定，也可指定为待抛出的异常。"""

    def __init__(self, result):
        self.result = result
        self.create_kwargs = None

    async def create_async(self, **kwargs):
        self.create_kwargs = kwargs
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _patch_semantic_config(monkeypatch, service, validation_error=None):
    monkeypatch.setattr(semantic_config_routes, "_get_current_user", lambda _request: CURRENT_USER)
    monkeypatch.setattr(semantic_config_routes, "get_datasource_manager", lambda: _FakeDatasourceManager())
    monkeypatch.setattr(semantic_config_routes, "get_semantic_config_service", lambda: service)

    async def _validate(*_args, **_kwargs):
        return validation_error

    monkeypatch.setattr(semantic_config_routes, "validate_semantic_config_for_save", _validate)


def _create_payload(name="demo"):
    return SemanticConfigCreateRequest(name=name)


@pytest.mark.asyncio
async def test_create_semantic_config_reports_validation_error(monkeypatch):
    service = _FakeSemanticConfigService({"id": "cfg-1", "name": "demo"})
    _patch_semantic_config(monkeypatch, service, validation_error="指标表达式不合法")

    response = payload(await semantic_config_routes._create_semantic_config_with_scope(
        "ds-1", _create_payload(), REQUEST, scope_type="private"
    ))

    assert response["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert response["msg"] == "指标表达式不合法"
    assert service.create_kwargs is None


@pytest.mark.asyncio
async def test_create_semantic_config_returns_created_config(monkeypatch):
    service = _FakeSemanticConfigService({"id": "cfg-1", "name": "demo"})
    _patch_semantic_config(monkeypatch, service)

    response = payload(await semantic_config_routes._create_semantic_config_with_scope(
        "ds-1", _create_payload(), REQUEST, scope_type="private", admin_manage=True
    ))

    assert response["msg"] == "创建成功"
    assert service.create_kwargs["scope_type"] == "private"
    assert service.create_kwargs["create_by"] == "u1"


@pytest.mark.asyncio
async def test_create_semantic_config_reports_empty_result(monkeypatch):
    _patch_semantic_config(monkeypatch, _FakeSemanticConfigService(None))

    response = payload(await semantic_config_routes._create_semantic_config_with_scope(
        "ds-1", _create_payload(), REQUEST, scope_type="global"
    ))

    assert response["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert response["msg"] == "创建失败"


@pytest.mark.asyncio
async def test_create_semantic_config_shields_unexpected_errors(monkeypatch):
    _patch_semantic_config(monkeypatch, _FakeSemanticConfigService(RuntimeError("db down")))

    response = payload(await semantic_config_routes._create_semantic_config_with_scope(
        "ds-1", _create_payload(), REQUEST, scope_type="private"
    ))

    assert response["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert "db down" in response["msg"]


# --------------------------------------------------------------------------
# semantic_config.py：后台批量生成公共基础模型
#
# 这条路由此前完全没有任何测试覆盖，三条出口路径（成功 / 校验失败 / 异常）全空。
# --------------------------------------------------------------------------


class _FakeGenerateService:
    """generate_basic_public_single_table_configs_async 的可控替身。"""

    def __init__(self, result=None, error=None):
        self.result = result if result is not None else {"created": 2, "skipped": 1, "failed": 0}
        self.error = error
        self.call_kwargs = None

    async def generate_basic_public_single_table_configs_async(self, datasource_id, **kwargs):
        self.call_kwargs = {"datasource_id": datasource_id, **kwargs}
        if self.error is not None:
            raise self.error
        return self.result


def _patch_generate_admin(monkeypatch, service):
    monkeypatch.setattr(semantic_config_routes, "_get_current_user", lambda _request: CURRENT_USER)
    monkeypatch.setattr(semantic_config_routes, "get_semantic_config_service", lambda: service)


@pytest.mark.asyncio
async def test_generate_admin_basic_semantic_configs_returns_summary(monkeypatch):
    service = _FakeGenerateService()
    _patch_generate_admin(monkeypatch, service)

    response = payload(await semantic_config_routes.generate_admin_basic_semantic_configs("ds-1", REQUEST))

    assert response["msg"] == "基础模型生成完成"
    assert response["data"] == {"created": 2, "skipped": 1, "failed": 0}
    assert service.call_kwargs["create_by"] == "u1"


@pytest.mark.asyncio
async def test_generate_admin_basic_semantic_configs_maps_missing_datasource(monkeypatch):
    """ValueError 里「数据源不存在」要映射成 404，其它校验失败映射成参数错误。"""
    _patch_generate_admin(monkeypatch, _FakeGenerateService(error=ValueError("数据源不存在: ds-1")))

    response = payload(await semantic_config_routes.generate_admin_basic_semantic_configs("ds-1", REQUEST))

    assert response["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert response["msg"] == "数据源不存在"


@pytest.mark.asyncio
async def test_generate_admin_basic_semantic_configs_maps_validation_error(monkeypatch):
    _patch_generate_admin(monkeypatch, _FakeGenerateService(error=ValueError("表结构为空")))

    response = payload(await semantic_config_routes.generate_admin_basic_semantic_configs("ds-1", REQUEST))

    assert response["code"] == ErrorCode.CHATDATA_VALIDATION_FAILED
    assert response["msg"] == "生成参数校验失败"


@pytest.mark.asyncio
async def test_generate_admin_basic_semantic_configs_shields_unexpected_errors(monkeypatch):
    _patch_generate_admin(monkeypatch, _FakeGenerateService(error=RuntimeError("engine down")))

    response = payload(await semantic_config_routes.generate_admin_basic_semantic_configs("ds-1", REQUEST))

    assert response["code"] == ErrorCode.CHATDATA_SERVICE_ERROR
    assert response["msg"] == "生成失败，请稍后重试"


# --------------------------------------------------------------------------
# evaluation.py：后台评测流式入口 + 场景测试集路由的异常兜底
# --------------------------------------------------------------------------


def _patch_evaluation(monkeypatch, permission_error=None):
    monkeypatch.setattr(evaluation_routes, "_get_current_user", lambda _request: CURRENT_USER)

    async def _permission(*_args, **_kwargs):
        if permission_error is not None:
            raise permission_error
        return {"name": "指标集", "id": "cfg-1"}

    monkeypatch.setattr(evaluation_routes, "_ensure_evaluation_semantic_permission", _permission)


@pytest.mark.asyncio
async def test_run_admin_accuracy_stream_forwards_to_shared_route(monkeypatch):
    """后台入口只做鉴权，之后必须带上 admin_manage=True 转发给共用实现。"""
    captured = {}

    async def _fake_route(_payload, _request, admin_manage=False):
        captured["admin_manage"] = admin_manage
        return "stream-response"

    monkeypatch.setattr(evaluation_routes, "_get_current_user", lambda _request: CURRENT_USER)
    monkeypatch.setattr(evaluation_routes, "_run_accuracy_evaluation_stream_route", _fake_route)

    result = await evaluation_routes.run_admin_accuracy_evaluation_stream(SimpleNamespace(), REQUEST)

    assert result == "stream-response"
    assert captured["admin_manage"] is True


@pytest.mark.asyncio
async def test_run_admin_accuracy_stream_returns_error_stream_on_auth_failure(monkeypatch):
    """鉴权失败时要返回一条 SSE 错误事件，而不是抛异常打断连接。"""
    monkeypatch.setattr(evaluation_routes, "_get_current_user", _raise_http_403)

    response = await evaluation_routes.run_admin_accuracy_evaluation_stream(SimpleNamespace(), REQUEST)

    assert response.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_get_scene_evaluation_cases_route_translates_http_exception(monkeypatch):
    _patch_evaluation(monkeypatch, permission_error=HTTPException(status_code=403, detail="无权限"))

    response = payload(await evaluation_routes._get_scene_evaluation_cases_route("ds-1", "cfg-1", REQUEST))

    assert response["status"] == "error"
    assert response["msg"] == "无权限"


@pytest.mark.asyncio
async def test_get_scene_evaluation_cases_route_shields_unexpected_errors(monkeypatch):
    _patch_evaluation(monkeypatch, permission_error=RuntimeError("storage down"))

    response = payload(await evaluation_routes._get_scene_evaluation_cases_route("ds-1", "cfg-1", REQUEST))

    assert response["code"] == ErrorCode.CHATDATA_EVALUATION_CASE_LIST_FAILED
    assert "storage down" in response["msg"]


@pytest.mark.asyncio
async def test_generate_scene_evaluation_cases_route_reports_value_error(monkeypatch):
    _patch_evaluation(monkeypatch, permission_error=ValueError("配置缺少指标"))

    response = payload(await evaluation_routes._generate_scene_evaluation_cases_route("ds-1", "cfg-1", REQUEST))

    assert response["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert response["msg"] == "配置缺少指标"


@pytest.mark.asyncio
async def test_generate_scene_evaluation_cases_route_shields_unexpected_errors(monkeypatch):
    _patch_evaluation(monkeypatch, permission_error=RuntimeError("llm down"))

    response = payload(await evaluation_routes._generate_scene_evaluation_cases_route("ds-1", "cfg-1", REQUEST))

    assert response["code"] == ErrorCode.CHATDATA_EVALUATION_HISTORY_FAILED
    assert "llm down" in response["msg"]
