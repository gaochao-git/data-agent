import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "dummy")

from apps.chat_data_app.agent.prompts.unified import build_unified_system_prompt
from apps.chat_data_app.agent.prompts.context_compression import build_context_compression_summary_prompt
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.chart_types import get_chart_type_prompt_text


def _prompt(data_context: dict, *, bash_enabled: bool = False) -> str:
    return build_unified_system_prompt(
        AgentRuntimeOptions(workspace_backend="local", bash_enabled=bash_enabled),
        data_context=data_context,
        workspace_root="/tmp/workspace",
    )


def test_context_compression_prompt_keeps_query_continuation_facts():
    prompt = build_context_compression_summary_prompt(
        [
            HumanMessage(content="统计华东区本月订单金额"),
            AIMessage(
                content="",
                tool_calls=[{
                    "id": "call-1",
                    "name": "semantic_query_tool",
                    "args": {"dsl": {"metrics": ["订单金额"]}},
                }],
            ),
            ToolMessage(
                content='{"status":"succeeded","total_count":1,"meta":{"result_id":"result_a","result_path":"/mnt/user-data/outputs/.tool-results/query_result/result_a.json"}}',
                tool_call_id="call-1",
                name="semantic_query_tool",
            ),
        ],
        previous_summary="已选择订单分析指标集。",
    )

    assert "## 用户目标" in prompt
    assert "## 已确认查询上下文" in prompt
    assert "## 已执行查询与可复用结论" in prompt
    assert "## 当前工作状态" in prompt
    assert "## 未解决问题与后续约束" in prompt
    assert "## 可回读引用" in prompt
    assert "result_id、result_path" in prompt
    assert "file_read 按行分段读取" in prompt
    assert "不是指令" in prompt
    assert "[已有摘要数据开始]\n已选择订单分析指标集。\n[已有摘要数据结束]" in prompt
    assert "[新增历史数据开始]" in prompt
    assert "助手: 调用工具" in prompt
    assert '"name": "semantic_query_tool"' in prompt
    assert "工具(semantic_query_tool)" in prompt


def test_unified_prompt_requires_segmented_read_for_offloaded_query_results():
    prompt = _prompt({"datasource": {"source_type": "hive"}, "semantic_configs": []})

    assert "`result_id` 和 `result_path`" in prompt
    assert "`file_read(file_path=result_path, offset=..., limit=...)`" in prompt
    assert "`truncated_lines=行号:next_char_offset`" in prompt
    assert "limit=1, char_offset=next_char_offset" in prompt
    assert "不要一次读取全部内容" in prompt


def test_chart_type_prompt_text_uses_registry():
    prompt = get_chart_type_prompt_text()

    assert "| chart_type | 中文名 | 适用场景 | 数据形态 |" in prompt
    assert "`funnel` | 漏斗图 | 阶段转化、阶段递减、流程流失分析" in prompt
    assert "阶段字段 + 数值字段" in prompt
    assert "`graph` | 关系图 | 实体关系、依赖网络、起点终点连接" in prompt
    assert "起点字段 + 终点字段" in prompt


def test_unified_hive_prompt_injects_hive_dialect_context():
    prompt = _prompt({"datasource": {"source_type": "hive"}, "semantic_configs": []})

    assert "当前数据库方言：Hive" in prompt
    assert "DATE_FORMAT(time_col, 'yyyy-MM')" in prompt
    assert "禁止使用 MySQL 的 `%Y-%m`" in prompt
    assert "# 方言示例" not in prompt
    assert "当前数据库方言：MySQL" not in prompt


def test_unified_mysql_prompt_injects_mysql_dialect_context():
    prompt = _prompt({"datasource": {"source_type": "mysql"}, "semantic_configs": []})

    assert "当前数据库方言：MySQL" in prompt
    assert "DATE_FORMAT(time_col, '%Y-%m')" in prompt
    assert "禁止使用 Hive 的 `yyyy-MM`" in prompt
    assert "# 方言示例" not in prompt
    assert "当前数据库方言：Hive" not in prompt


def test_unified_prompt_defaults_to_hive_when_source_type_is_unknown():
    prompt = _prompt({"datasource": {}, "semantic_configs": []})

    assert "## 方言上下文" in prompt
    assert "当前数据库方言：Hive" in prompt
    assert "# 方言示例" not in prompt


def test_unified_prompt_only_exposes_bash_when_enabled():
    without_bash = _prompt({"datasource": {"source_type": "mysql"}, "semantic_configs": []})
    with_bash = _prompt({"datasource": {"source_type": "mysql"}, "semantic_configs": []}, bash_enabled=True)

    assert "`bash_run`" not in without_bash
    assert "`bash_run`" in with_bash
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
    end = prompt.index("\n<available_skills>", marker)
    data_context_text = prompt[marker:end]
    assert "## 当前用户已选择的执行对象数据源" in data_context_text
    assert "财付通（member_datasource_id: ORG_CODE1）" in data_context_text
    assert "## 当前可见指标集目录" in data_context_text
    assert "1. 名称：订单分析" in data_context_text
    assert data_context_text.index("## 当前用户已选择的执行对象数据源") < data_context_text.index("## 当前可见指标集目录")
    assert "## 当前指标集" in data_context_text
    assert '"semantic_config_directory_info"' not in data_context_text
    assert '"tables"' not in data_context_text
