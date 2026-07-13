import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.data_analyze_graph import build_data_analyze_system_prompt
from apps.chat_data_app.chart_types import get_chart_type_prompt_text


def test_chart_type_prompt_text_uses_registry():
    prompt = get_chart_type_prompt_text()

    assert "| chart_type | 中文名 | 适用场景 | 数据形态 |" in prompt
    assert "`funnel` | 漏斗图 | 阶段转化、阶段递减、流程流失分析" in prompt
    assert "阶段字段 + 数值字段" in prompt
    assert "`graph` | 关系图 | 实体关系、依赖网络、起点终点连接" in prompt
    assert "起点字段 + 终点字段" in prompt


def test_data_analyze_hive_prompt_injects_only_hive_dialect_examples():
    prompt = build_data_analyze_system_prompt(
        skill=None,
        data_context={"datasource": {"source_type": "hive"}, "semantic_configs": []},
        workspace_root="/tmp/workspace",
    )

    assert "当前数据库方言：Hive" in prompt
    assert "DATE_FORMAT(time_col, 'yyyy-MM')" in prompt
    assert "禁止使用 MySQL 的 `%Y-%m`" in prompt
    assert "Hive 方言示例" in prompt
    assert "MySQL 方言示例" not in prompt
    assert "当前数据库方言：MySQL" not in prompt


def test_data_analyze_mysql_prompt_injects_only_mysql_dialect_examples():
    prompt = build_data_analyze_system_prompt(
        skill=None,
        data_context={"datasource": {"source_type": "mysql"}, "semantic_configs": []},
        workspace_root="/tmp/workspace",
    )

    assert "当前数据库方言：MySQL" in prompt
    assert "DATE_FORMAT(time_col, '%Y-%m')" in prompt
    assert "禁止使用 Hive 的 `yyyy-MM`" in prompt
    assert "MySQL 方言示例" in prompt
    assert "Hive 方言示例" not in prompt
    assert "当前数据库方言：Hive" not in prompt


def test_data_analyze_prompt_uses_single_agent_tool_chain():
    prompt = build_data_analyze_system_prompt(
        skill=None,
        data_context={"datasource": {"source_type": "mysql"}, "semantic_configs": []},
        workspace_root="/tmp/workspace",
    )

    assert "`sandbox_run`" in prompt
    assert "`semantic_query_tool`" in prompt
    assert "analysis_plan" not in prompt
    assert "理解节点" not in prompt
    assert "规划节点" not in prompt
