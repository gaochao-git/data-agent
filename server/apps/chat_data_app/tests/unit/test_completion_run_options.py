"""无人值守标记的 completion options 解析与评测构造护栏。

定时轮派发在 runtime_context 直打标记（不走本解析）；评测/批量等内部调用
方经 options 显式携带 ``disable_user_interaction``，由
``parse_completion_run_options`` 解析后写入 runtime_context，工具装配据此
剔除 ask_user_question。任何一环丢失标记 = 无人值守提问悬挂回归。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.run.completion_options import (  # noqa: E402
    parse_approval_mode,
    parse_completion_run_options,
)
from apps.chat_data_app.evaluate.accuracy.runner import AccuracyRunConfig, build_completion_options  # noqa: E402


def test_parse_defaults_to_attended_without_flag():
    """chat 入口不传标记：恒 False，工具面保持完整。"""
    assert parse_completion_run_options({}).disable_user_interaction is False
    assert parse_completion_run_options(None).disable_user_interaction is False


def test_parse_unattended_flag_passthrough():
    options = parse_completion_run_options({"datasource_id": "ds_a", "disable_user_interaction": True})
    assert options.disable_user_interaction is True


def test_accuracy_runner_builds_unattended_options():
    """评测批量跑必须携带无人值守标记：模型提问只会悬挂在 waiting_user 拖死批量。"""
    options = build_completion_options(AccuracyRunConfig(), datasource_id="ds_a", semantic_config_id="sc_1")
    assert options["disable_user_interaction"] is True
    # 解析回读闭环：options → CompletionRunOptions → runtime_context 消费口径
    assert parse_completion_run_options(options).disable_user_interaction is True


def test_parse_approval_mode_whitelist():
    assert parse_approval_mode("ask") == "ask"
    assert parse_approval_mode(" AUTO ") == "auto"
    assert parse_approval_mode("full") == ""  # 未开放的模式回落系统默认
    assert parse_approval_mode(None) == ""


def test_parse_completion_options_carries_approval_mode():
    options = parse_completion_run_options({"approval_mode": "ask"})
    assert options.approval_mode == "ask"
    assert parse_completion_run_options({}).approval_mode == ""


def test_accuracy_runner_forces_auto_approval():
    options = build_completion_options(AccuracyRunConfig(), datasource_id="ds_a", semantic_config_id="sc_1")
    assert options["approval_mode"] == "auto"
    assert parse_completion_run_options(options).approval_mode == "auto"
