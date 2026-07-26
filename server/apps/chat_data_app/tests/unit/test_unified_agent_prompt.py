import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.prompts.unified import build_unified_system_prompt
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.chart_types import get_chart_type_prompt_text


def _prompt(data_context: dict, *, bash_enabled: bool = False) -> str:
    return build_unified_system_prompt(
        AgentRuntimeOptions(workspace_backend="local", bash_enabled=bash_enabled),
        data_context=data_context,
        workspace_root="/tmp/workspace",
    )


def test_chart_type_prompt_text_uses_registry():
    prompt = get_chart_type_prompt_text()

    assert "| chart_type | 中文名 | 适用场景 | 数据形态 |" in prompt
    assert "`funnel` | 漏斗图 | 阶段转化、阶段递减、流程流失分析" in prompt
    assert "阶段字段 + 数值字段" in prompt
    assert "`graph` | 关系图 | 实体关系、依赖网络、起点终点连接" in prompt
    assert "起点字段 + 终点字段" in prompt


def test_unified_hive_prompt_injects_only_hive_dialect_examples():
    prompt = _prompt({"datasource": {"source_type": "hive"}, "semantic_configs": []})

    assert "当前数据库方言：Hive" in prompt
    assert "DATE_FORMAT(time_col, 'yyyy-MM')" in prompt
    assert "禁止使用 MySQL 的 `%Y-%m`" in prompt
    assert "Hive 方言示例" in prompt
    assert "MySQL 方言示例" not in prompt
    assert "当前数据库方言：MySQL" not in prompt


def test_unified_mysql_prompt_injects_only_mysql_dialect_examples():
    prompt = _prompt({"datasource": {"source_type": "mysql"}, "semantic_configs": []})

    assert "当前数据库方言：MySQL" in prompt
    assert "DATE_FORMAT(time_col, '%Y-%m')" in prompt
    assert "禁止使用 Hive 的 `yyyy-MM`" in prompt
    assert "MySQL 方言示例" in prompt
    assert "Hive 方言示例" not in prompt
    assert "当前数据库方言：Hive" not in prompt


def test_unified_prompt_only_exposes_bash_when_enabled():
    without_bash = _prompt({"datasource": {"source_type": "mysql"}, "semantic_configs": []})
    with_bash = _prompt({"datasource": {"source_type": "mysql"}, "semantic_configs": []}, bash_enabled=True)

    assert "`sandbox_run`" not in without_bash
    assert "`sandbox_run`" in with_bash
    assert "`semantic_query_tool`" in without_bash
    assert "`calculator_tool`" in without_bash
    assert "analysis_plan" not in without_bash
    assert "理解节点" not in without_bash
    assert "规划节点" not in without_bash


def test_unified_prompt_uses_chat_data_directory_before_semantic_selected():
    prompt = _prompt({
        "datasource": {"source_type": "mysql"},
        "tables": [],
        "datasource_group_context": "## 当前用户已选择的执行对象数据源\n本次查询必须只使用以下 member_datasource_ids：\n- 财付通（member_datasource_id: ORG_CODE1）；编码：ORG_CODE1",
        "semantic_config_directory_info": "## 当前可见指标集目录\n1. 名称：订单分析\n   描述：订单域\n   指标数：2；维度数：3",
        "semantic_configs": [],
    })

    marker = prompt.index("# 数据上下文")
    end = prompt.index("\n## 可用工具", marker)
    data_context_text = prompt[marker:end]
    assert "## 当前用户已选择的执行对象数据源" in data_context_text
    assert "财付通（member_datasource_id: ORG_CODE1）" in data_context_text
    assert "## 当前可见指标集目录" in data_context_text
    assert "1. 名称：订单分析" in data_context_text
    assert data_context_text.index("## 当前用户已选择的执行对象数据源") < data_context_text.index("## 当前可见指标集目录")
    assert "## 当前指标集" in data_context_text
    assert '"semantic_config_directory_info"' not in data_context_text
    assert '"tables"' not in data_context_text
