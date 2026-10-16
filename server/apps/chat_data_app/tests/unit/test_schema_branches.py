"""schema.py 分支补测。

覆盖 Pydantic 请求模型的校验分支（原 11 条缺失分支弧已全部触达）。

历史注记：`_normalize_json_object` 的 for 循环自然耗尽弧（曾记为 28->35，
现为 35->42）此前被标记为不可达，实际可达——传入「JSON 字符串的字符串」
（如 ``json.dumps("abc")``）时两轮解析结果都还是 str，循环自然耗尽后走
终检报错，见 test_normalize_json_object_double_encoded_string_eventually_raises。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from apps.chat_data_app.schema import (
    DbConnectionRequest,
    QueryTaskCreateRequest,
    SemanticMetric,
    SemanticQueryInput,
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


# ---------------------------------------------------------------------------
# 语义指标分型校验 / dashboard DSL 归一化 / check_yaml 迁移拒绝 / action 分发表
# ---------------------------------------------------------------------------


def _metric(**overrides):
    fields = {"name": "销售额", "expression": "SUM(amount)", "table": "orders"}
    fields.update(overrides)
    return fields


def test_derived_metric_requires_base_metric():
    """派生指标缺 base_metric → 报错并指明字段。"""
    with pytest.raises(ValidationError, match="缺少 base_metric"):
        SemanticMetric(**_metric(metric_kind="derived", expression="", base_metric=None))


def test_derived_metric_rejects_expression():
    """派生指标携带 expression → 拒绝。"""
    with pytest.raises(ValidationError, match="不能配置 expression"):
        SemanticMetric(**_metric(metric_kind="derived", expression="SUM(amount)", base_metric="销售额"))


def test_composite_metric_requires_formula():
    """复合指标缺 formula → 报错并指明字段。"""
    with pytest.raises(ValidationError, match="缺少 formula"):
        SemanticMetric(**_metric(metric_kind="composite", expression="", formula=None))


def test_composite_metric_rejects_expression():
    """复合指标携带 expression → 拒绝。"""
    with pytest.raises(ValidationError, match="不能配置 expression"):
        SemanticMetric(**_metric(metric_kind="composite", formula="{销售额} / {订单数}"))


def test_atomic_metric_requires_expression():
    """原子指标缺 expression → 报错并指明字段。"""
    with pytest.raises(ValidationError, match="缺少 expression"):
        SemanticMetric(**_metric(expression=""))


def test_atomic_metric_rejects_formula():
    """原子指标携带 formula → 拒绝（口径由 formula 决定是复合指标专属）。"""
    with pytest.raises(ValidationError) as exc_info:
        SemanticMetric(**_metric(formula="{销售额}"))
    assert "formula" in str(exc_info.value)


def test_validate_dashboard_dsl_none_hits_normalizer_then_type_check():
    """dsl=None 进入归一化器 None 分支后，被必填类型校验拒绝（dsl 为必填 dict/list）。"""
    from apps.chat_data_app.schema import _ValidateDashboardActionPayload

    with pytest.raises(ValidationError):
        _ValidateDashboardActionPayload(dsl=None, semantic_config_id="m1")


def test_validate_dashboard_dsl_rejects_non_string_non_mapping():
    """dsl 传数字等非法类型 → 归一化报错。"""
    from apps.chat_data_app.schema import _ValidateDashboardActionPayload

    with pytest.raises(ValidationError, match="dsl 必须是对象或数组"):
        _ValidateDashboardActionPayload(dsl=123)


def test_validate_dashboard_dsl_rejects_invalid_json():
    """dsl 传非法 JSON 字符串 → 归一化报错。"""
    from apps.chat_data_app.schema import _ValidateDashboardActionPayload

    with pytest.raises(ValidationError, match="有效的 JSON"):
        _ValidateDashboardActionPayload(dsl="not-json{")


def test_check_yaml_rejects_legacy_yaml_content():
    """旧习惯 yaml_content 全文参数 → 给出可执行改法的迁移报错。"""
    from apps.chat_data_app.schema import _CheckYamlActionPayload

    with pytest.raises(ValidationError, match="yaml_content"):
        _CheckYamlActionPayload.model_validate(
            {"yaml_content": "models: []", "file_path": "/mnt/user-data/workspace/a.yaml"}
        )


def test_validate_data_analyzing_payload_rejects_unknown_action():
    """未知 action → 明确报错（分发表 miss 分支）。"""
    from apps.chat_data_app.schema import validate_data_analyzing_payload

    with pytest.raises(ValueError, match="不支持的 action"):
        validate_data_analyzing_payload("no_such_action", {})


def test_normalize_json_object_rejects_invalid_json():
    """_normalize_json_object 收到非法 JSON 字符串 → ValueError（SemanticQueryInput.dsl 链路）。"""
    with pytest.raises(ValidationError, match="有效的 JSON 对象字符串"):
        SemanticQueryInput(dsl="not-json{", semantic_config_id="m1", member_datasource_ids=["ORG1"])


def test_query_input_dsl_none_hits_normalizer_none_branch():
    """dsl=None 显式传入 → 归一化器 None 分支直通（dsl 可选）。"""
    assert SemanticQueryInput(semantic_config_id="m1", member_datasource_ids=["ORG1"]).dsl is None


def test_query_input_dsl_mapping_breaks_loop_immediately():
    """dsl 传 dict → 归一化循环首轮 break 直通类型检查。"""
    payload = SemanticQueryInput(
        dsl={"metrics": [{"name": "销售额"}]}, semantic_config_id="m1", member_datasource_ids=["ORG1"]
    )
    assert payload.dsl == {"metrics": [{"name": "销售额"}]}


def test_validate_dashboard_dsl_double_encoded_json():
    """双层编码 JSON → 两轮解析后归一为 dict。"""
    import json as _json

    from apps.chat_data_app.schema import _ValidateDashboardActionPayload

    double = _json.dumps(_json.dumps({"dashboard_name": "d", "cards": []}))
    assert _ValidateDashboardActionPayload(dsl=double).dsl == {"dashboard_name": "d", "cards": []}


def test_validate_dsl_payload_dsl_none_hits_normalizer():
    """_ValidateDslActionPayload.dsl=None 显式传入 → 归一化器 None 分支后类型校验拒绝。"""
    from apps.chat_data_app.schema import _ValidateDslActionPayload

    with pytest.raises(ValidationError):
        _ValidateDslActionPayload(dsl=None, semantic_config_id="m1")


def test_normalize_json_object_double_encoded_string_eventually_raises():
    """JSON 字符串的字符串（双编码）→ 两轮解析仍是 str → 循环自然耗尽后报错。"""
    with pytest.raises(ValidationError, match="必须是对象或有效的 JSON 对象字符串"):
        SemanticQueryInput(dsl=json.dumps("abc"), semantic_config_id="m1", member_datasource_ids=["ORG1"])
