import asyncio
import os
import shutil
from pathlib import Path

from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    ensure_conversation_workspace,
)
from apps.chat_data_app.agent.data_agent import assemble_tools
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions


def _build_tools(paths, *, snapshot_skills=None):
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="system",
        backend="local",
    )
    # 运行域技能直接来自本会话 runtime-skills 快照目录；把源目录复制进快照即可被列出。
    if snapshot_skills:
        snapshot_dir = runtime.runtime_skills_dir
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for name, source_dir in snapshot_skills.items():
            dest = snapshot_dir / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source_dir, dest)
    return runtime, {t.name: t for t in assemble_tools(AgentRuntimeOptions(), runtime)}


def _call(tool, **kwargs):
    return asyncio.run(tool.ainvoke(kwargs))


def test_dir_list_default_workspace(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["workspace"]) / "a.json").write_text("{}")
    (Path(paths["workspace"]) / "note.txt").write_text("hi")
    _, tools = _build_tools(paths)
    out = _call(tools["dir_list"], path="workspace")
    assert "count=2" in out
    assert "type=file  path=/mnt/user-data/workspace/a.json" in out
    assert "type=file  path=/mnt/user-data/workspace/note.txt" in out


def test_dir_list_alias_and_recursion(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    ws = Path(paths["workspace"])
    (ws / "sub").mkdir()
    (ws / "sub" / "deep.json").write_text("{}")
    _, tools = _build_tools(paths)
    out = _call(tools["dir_list"], path="/workspace")
    assert "type=dir  path=/mnt/user-data/workspace/sub/" in out
    out2 = _call(tools["dir_list"], path="/workspace", max_depth=2)
    assert "type=file  path=/mnt/user-data/workspace/sub/deep.json" in out2


def test_dir_list_outputs_and_uploads(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["outputs"]) / "final.chart.json").write_text("{}")
    (Path(paths["uploads"]) / "in.csv").write_text("a,b")
    _, tools = _build_tools(paths)
    out = _call(tools["dir_list"], path="outputs")
    assert "type=file  path=/mnt/user-data/outputs/final.chart.json" in out
    out2 = _call(tools["dir_list"], path="/mnt/user-data/uploads")
    assert "type=file  path=/mnt/user-data/uploads/in.csv" in out2


def test_dir_list_shows_tool_results(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    private_file = Path(paths["outputs"]) / ".tool-offload" / "result_1.json"
    private_file.write_text('{"rows":[1]}')
    _, tools = _build_tools(paths)

    output = _call(tools["dir_list"], path="outputs", max_depth=3)
    direct = _call(tools["dir_list"], path="outputs/.tool-offload")

    assert "type=dir  path=/mnt/user-data/outputs/.tool-offload/" in output
    assert "result_1.json" in output
    assert "type=file  path=/mnt/user-data/outputs/.tool-offload/result_1.json" in direct


def test_dir_list_rejects_out_of_session(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    _, tools = _build_tools(paths)
    out = _call(tools["dir_list"], path="/etc/passwd")
    assert "只允许访问当前会话目录" in out
    out2 = _call(tools["dir_list"], path="../../escape")
    assert "路径越界" in out2 or "只允许访问" in out2


def test_dir_list_not_a_dir(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["workspace"]) / "file.txt").write_text("x")
    _, tools = _build_tools(paths)
    out = _call(tools["dir_list"], path="workspace/file.txt")
    assert "目标不是目录" in out


def test_dir_list_empty_dir(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["workspace"]) / "empty").mkdir()
    _, tools = _build_tools(paths)
    out = _call(tools["dir_list"], path="workspace/empty")
    assert "count=0" in out


def test_dir_list_max_depth_limits(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    ws = Path(paths["workspace"])
    (ws / "sub" / "deep").mkdir(parents=True)
    (ws / "sub" / "deep" / "leaf.txt").write_text("x")
    _, tools = _build_tools(paths)
    # max_depth=1: 仅直接子项（sub/），不展开 deep、不列 leaf
    shallow = _call(tools["dir_list"], path="workspace", max_depth=1)
    assert "type=dir  path=/mnt/user-data/workspace/sub/" in shallow
    assert "deep/" not in shallow
    assert "leaf.txt" not in shallow
    # max_depth=2: 展开一层子目录（sub/deep/），仍不列 leaf（第三层）
    mid = _call(tools["dir_list"], path="workspace", max_depth=2)
    assert "type=dir  path=/mnt/user-data/workspace/sub/deep/" in mid
    assert "leaf.txt" not in mid
    # max_depth=3: 列到 leaf
    full = _call(tools["dir_list"], path="workspace", max_depth=3)
    assert "type=file  path=/mnt/user-data/workspace/sub/deep/leaf.txt" in full


def test_dir_list_skill_path(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    skill_src = tmp_path / "skills" / "demo"
    (skill_src / "references").mkdir(parents=True)
    (skill_src / "references" / "guide.md").write_text("# guide")
    # 运行域技能为扁平 /mnt/skills/<name>，来自本会话快照。
    _, tools = _build_tools(paths, snapshot_skills={"demo": str(skill_src)})
    out = _call(tools["dir_list"], path="/mnt/skills/demo/references")
    assert "type=file  path=/mnt/skills/demo/references/guide.md" in out


def test_dir_list_unauthorized_skill_rejected(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    _, tools = _build_tools(paths)
    out = _call(tools["dir_list"], path="/mnt/skills/unknown/references")
    # 快照内无此技能：resolve_readonly_skill_path_async 抛「技能不存在或无权限访问」
    assert "技能不存在" in out or "无权限" in out or "技能路径无效" in out
