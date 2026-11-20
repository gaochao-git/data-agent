"""子智能体任务列表格式化测试：subagent_async 的任务汇总与 agent 列表输出。"""

from types import SimpleNamespace

from apps.chat_data_app.agent.middleware.subagent_async import (
    _format_task_status,
    format_subagent_agent_list,
    format_subagent_agent_tasks,
    format_subagent_task_list_summary,
)


def test_task_list_summary_reports_tasks_sessions_and_active_count():
    records = [
        SimpleNamespace(task_id="t1", agent_session_id="s1", status="completed"),
        SimpleNamespace(task_id="t2", agent_session_id="s1", status="running"),
    ]

    assert format_subagent_task_list_summary(records) == "子智能体（共 1 个，任务 2 条，活跃 1 条）："


def test_agent_list_shows_latest_task_without_dumping_all_task_ids():
    records = [
        SimpleNamespace(
            task_id="task-latest",
            agent_id="agent-stable",
            agent_session_id="agent-stable",
            status="running",
            subagent_type="general",
            label="检查磁盘空间",
            turn_index=3,
        ),
        SimpleNamespace(
            task_id="task-old",
            agent_id="agent-stable",
            agent_session_id="agent-stable",
            status="completed",
            subagent_type="general",
            label="检查磁盘空间",
            turn_index=2,
        ),
    ]

    output = format_subagent_agent_list(records)

    assert "agent_id=agent-stable" in output
    assert "最近 task_id=task-latest" in output
    assert "最近匹配 task_id" not in output
    assert "task-old" not in output
    assert "可操作=取消、插话" in output
    assert 'subagent({"manage":{"action":"list","agent_id":"<agent_id>"}})' in output

    filtered_output = format_subagent_agent_list(records, status_filtered=True)
    assert "最近匹配 task_id=task-latest" in filtered_output


def test_agent_task_list_drills_down_all_task_ids_for_one_agent():
    records = [
        SimpleNamespace(
            task_id="task-latest",
            agent_id="agent-stable",
            status="running",
            subagent_type="general",
            label="检查磁盘空间",
            turn_index=3,
        ),
        SimpleNamespace(
            task_id="task-old",
            agent_id="agent-stable",
            status="completed",
            subagent_type="general",
            label="检查磁盘空间",
            turn_index=2,
        ),
    ]

    output = format_subagent_agent_tasks("agent-stable", records)

    assert "agent_id=agent-stable" in output
    assert "task_id=task-latest" in output
    assert "task_id=task-old" in output
    assert "check/cancel/follow_up/steer/approve/reject 使用上面的 task_id" in output
    assert "agent_id 只用于识别子智能体" in output


def test_task_list_line_separates_agent_id_from_task_id():
    record = SimpleNamespace(
        task_id="task-current",
        agent_id="agent-stable",
        agent_session_id="agent-stable",
        status="completed",
        subagent_type="general",
        label="检查磁盘空间",
        is_terminal=True,
        turn_index=3,
    )

    line = _format_task_status(record)

    assert "agent_id=agent-stable" in line
    assert "task_id=task-current" in line
    assert "轮次=3" in line
    assert "可操作：追问" in line
