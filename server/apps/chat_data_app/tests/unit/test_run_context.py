"""入口期 RunContext 契约：字段集合、默认值与继承顺序。"""

import pytest

from apps.chat_data_app.agent.run.completion_options import parse_completion_run_options
from apps.chat_data_app.agent.run.run_context import (
    RunSource,
    build_run_context,
    inherit_run_context,
    validate_run_context,
)


def _options(**overrides):
    payload = {
        "datasource_id": "ds-1",
        "semantic_config_id": "sc-1",
        "model_id": "model-a",
        "enable_thinking": "true",
        "thinking_effort": "high",
        "member_datasource_ids": ["ds-2"],
        "file_selections": ["outputs/a.csv"],
        "skill_names": "officecli",
        "quoted_snippets": [{"text": "引用"}],
        "annotations": [
            {
                "file_path": "uploads/report.docx",
                "doc_path": "/body/p[1]",
                "quote": "原文",
                "comment": "改这里",
            }
        ],
        "disable_user_interaction": False,
    }
    payload.update(overrides)
    return parse_completion_run_options(payload)


def test_user_context_contains_all_entry_fields_and_source():
    context = build_run_context(
        source=RunSource.USER,
        run_options=_options(),
        query="查一下订单",
        operator_name="用户一",
        raw_options={"model_id": "model-a"},
        file_ids=["uploads/a.csv"],
    )

    assert context["task_source"] == "user"
    assert context["query"] == "查一下订单"
    assert context["datasource_id"] == "ds-1"
    assert context["model_id"] == "model-a"
    assert context["skill_names"] == ["officecli"]
    assert context["operator_name"] == "用户一"
    assert context["file_ids"] == ["uploads/a.csv"]
    assert context["disable_user_interaction"] is False
    assert context["annotations"][0]["quote"] == "原文"
    validate_run_context(context)


def test_schedule_context_omits_annotations_and_forces_unattended():
    context = build_run_context(
        source=RunSource.SCHEDULE,
        run_options=_options(),
        query="汇总昨日数据",
        operator_name="用户一",
        raw_options={"model_id": "model-a"},
        disable_user_interaction=True,
        include_annotations=False,
    )

    assert context["task_source"] == "schedule"
    assert context["disable_user_interaction"] is True
    assert "annotations" not in context
    assert context["file_ids"] == []
    validate_run_context(context)


def test_inherit_context_keeps_parent_choices_and_overrides_source():
    parent = {
        "task_source": "user",
        "query": "原始问题",
        "model_id": "model-a",
        "datasource_id": "ds-1",
        "annotations": [{"quote": "父引用"}],
        "disable_user_interaction": False,
    }
    context = inherit_run_context(
        parent,
        source=RunSource.SUBAGENT,
        overrides={
            "query": "",
            "disable_user_interaction": True,
            "subagent_notification_event_id": "evt-1",
        },
    )

    assert context["task_source"] == "subagent"
    assert context["query"] == ""
    assert context["disable_user_interaction"] is True
    assert context["model_id"] == "model-a"
    assert context["datasource_id"] == "ds-1"
    assert context["annotations"] == [{"quote": "父引用"}]
    assert context["subagent_notification_event_id"] == "evt-1"
    validate_run_context(context)


def test_inherit_context_without_source_keeps_parent_task_source():
    parent = {
        "task_source": "schedule",
        "query": "定时问题",
        "disable_user_interaction": True,
    }
    context = inherit_run_context(parent, overrides={"query": ""})

    assert context["task_source"] == "schedule"
    assert context["query"] == ""
    validate_run_context(context)


def test_inherit_context_without_parent_task_source_keeps_it_absent():
    parent = {
        "query": "旧契约问题",
        "disable_user_interaction": False,
    }
    context = inherit_run_context(parent, overrides={"query": ""})

    assert "task_source" not in context
    assert context["query"] == ""
    assert context["disable_user_interaction"] is False


def test_validate_context_rejects_missing_required_keys():
    with pytest.raises(ValueError, match="disable_user_interaction"):
        validate_run_context({"task_source": "user", "query": "问题"})
