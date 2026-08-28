"""schema.py 分支补测。

覆盖 Pydantic 请求模型的校验分支（原 11 条缺失分支弧中的 10 条）。

不可达说明：`_normalize_json_object` 的 ``28->35``（for 循环自然耗尽）无法触达。
循环体只有两条出口 —— current 不是 str 时 ``break`` 跳出（记为 30->35），
是 str 则继续解析；第二轮若解析结果仍是 str 会在 35 抛错，若是 dict 则 break。
任何输入都不会让 ``for _ in range(2)`` 走满两轮后自然退出，故该弧恒定缺失。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.chat_data_app.schema import (
    DbConnectionRequest,
    QueryTaskCreateRequest,
    SemanticQueryInput,
    SemanticToolInput,
    _normalize_recommended_questions,
)


# ---------------------------------------------------------------------------
# 推荐问题列表归一化
# ---------------------------------------------------------------------------


def test_recommended_questions_none_returns_empty_list():
    """不传推荐问题时返回空列表（原缺失分支 41->42）。"""
    assert _normalize_recommended_questions(None) == []


def test_recommended_questions_rejects_non_list_payload():
    """既不是字符串也不是数组的输入被拒绝（原缺失分支 44->45）。"""
    with pytest.raises(ValueError, match="recommended_questions 必须是数组"):
        _normalize_recommended_questions({"unexpected": "dict"})


# ---------------------------------------------------------------------------
# QueryTaskCreateRequest：SQL 批次与查询模式的互斥校验
# ---------------------------------------------------------------------------


def test_builder_query_rejects_multiple_sql_statements():
    """builder 模式只允许一条 SQL（原缺失分支 136->137）。"""
    with pytest.raises(ValidationError, match="SQL 批次不支持语义可视化查询"):
        QueryTaskCreateRequest(
            datasource_id="ds-1",
            query_type="builder",
            sql=["select 1", "select 2"],
        )


def test_sql_batch_rejects_semantic_builder_config():
    """多语句 SQL 批次不能同时携带可视化查询 DSL（原缺失分支 141->142）。"""
    with pytest.raises(ValidationError, match="SQL 批次不支持语义可视化查询"):
        QueryTaskCreateRequest(
            datasource_id="ds-1",
            sql=["select 1", "select 2"],
            semantic_builder_config={"metrics": []},
        )


# ---------------------------------------------------------------------------
# DbConnectionRequest：直连 / HTTP 两种执行方式的字段约束
# ---------------------------------------------------------------------------


def test_direct_connection_requires_host():
    """直连模式必须填写 host（原缺失分支 230->231）。"""
    with pytest.raises(ValidationError, match="host 不能为空"):
        DbConnectionRequest(source_type="mysql", host="", database="db")


def test_http_connection_requires_org_code():
    """HTTP 模式必须携带机构编码（原缺失分支 232->233）。"""
    with pytest.raises(ValidationError, match="HTTP 模式缺少机构编码"):
        DbConnectionRequest(
            source_type="hive",
            query_executor_type="http",
            org_code=None,
            database="db",
        )


@pytest.mark.parametrize("extra", [{"host": "10.0.0.1"}, {"port": 10000}])
def test_http_connection_rejects_host_and_port(extra):
    """HTTP 模式只认数据库名，填 IP 或端口即拒绝（原缺失分支 234->235）。"""
    with pytest.raises(ValidationError, match="HTTP 模式只填写数据库名"):
        DbConnectionRequest(
            source_type="hive",
            query_executor_type="http",
            org_code="ORG1",
            database="db",
            **extra,
        )


def test_connection_requires_database():
    """任何模式都必须填写数据库名（原缺失分支 236->237）。"""
    with pytest.raises(ValidationError, match="database 不能为空"):
        DbConnectionRequest(source_type="mysql", host="10.0.0.1", database="")


def test_connection_accepts_valid_payloads():
    """合法输入不被误伤：直连填 host，HTTP 只填 database + org_code。"""
    direct = DbConnectionRequest(source_type="mysql", host="10.0.0.1", database="db")
    assert direct.query_executor_type == "direct"
    assert direct.org_code is None

    http = DbConnectionRequest(
        source_type="hive",
        query_executor_type="http",
        org_code="ORG1",
        database="db",
    )
    assert http.host == "" and http.port is None


# ---------------------------------------------------------------------------
# 语义工具入参
# ---------------------------------------------------------------------------


def test_list_fields_requires_semantic_config_id():
    """list_fields 必须指定语义模型 ID（原缺失分支 658->659）。"""
    with pytest.raises(ValidationError, match="必须传 semantic_config_id"):
        SemanticToolInput(operation="list_fields")


def test_search_models_skips_semantic_config_id_check():
    """search_models 不需要 semantic_config_id，直接放行（覆盖 656->657 的对照分支）。"""
    payload = SemanticToolInput(operation="search_models", query="最近成交额")
    assert payload.semantic_config_id is None


def test_normalize_dsl_passes_none_through():
    """dsl 为 None 时直接放行，不做 JSON 解析（原缺失分支 715->716）。"""
    payload = SemanticQueryInput(
        dsl=None,
        semantic_config_id="cfg-1",
        member_datasource_ids=["ds-1"],
    )
    assert payload.dsl is None


def test_normalize_dsl_accepts_json_string():
    """模型把 dsl 序列化成 JSON 字符串时自动反序列化（对照分支）。"""
    payload = SemanticQueryInput(
        dsl='{"metrics": ["gmv"]}',
        semantic_config_id="cfg-1",
        member_datasource_ids=["ds-1"],
    )
    assert payload.dsl == {"metrics": ["gmv"]}
