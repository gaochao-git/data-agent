import asyncio
import os
from pathlib import Path

from apps.chat_data_app.services.conversation_workspace import (
    ConversationWorkspaceRuntime,
    RuntimeSkillMount,
    ensure_conversation_workspace,
)
from apps.chat_data_app.agent.data_agent import assemble_tools
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions


def _build_tools(paths, *, skills=None):
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="system",
        backend="local",
    )
    if skills:
        for category, name, source_dir in skills:
            runtime.runtime_skills[f"{category}/{name}"] = RuntimeSkillMount(
                name=name, category=category, source_dir=Path(source_dir).resolve()
            )
    return runtime, {t.name: t for t in assemble_tools(AgentRuntimeOptions(), runtime)}


def _call(tool, **kwargs):
    return asyncio.run(tool.ainvoke(kwargs))


def test_list_dir_default_workspace(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["workspace"]) / "a.json").write_text("{}")
    (Path(paths["workspace"]) / "note.txt").write_text("hi")
    _, tools = _build_tools(paths)
    out = _call(tools["list_dir"], path="workspace")
    assert "count=2" in out
    assert "type=file  path=/mnt/user-data/workspace/a.json" in out
    assert "type=file  path=/mnt/user-data/workspace/note.txt" in out


def test_list_dir_alias_and_recursion(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    ws = Path(paths["workspace"])
    (ws / "sub").mkdir()
    (ws / "sub" / "deep.json").write_text("{}")
    _, tools = _build_tools(paths)
    out = _call(tools["list_dir"], path="/workspace")
    assert "type=dir  path=/mnt/user-data/workspace/sub/" in out
    out2 = _call(tools["list_dir"], path="/workspace", max_depth=2)
    assert "type=file  path=/mnt/user-data/workspace/sub/deep.json" in out2


def test_list_dir_outputs_and_uploads(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["outputs"]) / "final.chart.json").write_text("{}")
    (Path(paths["uploads"]) / "in.csv").write_text("a,b")
    _, tools = _build_tools(paths)
    out = _call(tools["list_dir"], path="outputs")
    assert "type=file  path=/mnt/user-data/outputs/final.chart.json" in out
    out2 = _call(tools["list_dir"], path="/mnt/user-data/uploads")
    assert "type=file  path=/mnt/user-data/uploads/in.csv" in out2


def test_list_dir_rejects_out_of_session(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    _, tools = _build_tools(paths)
    out = _call(tools["list_dir"], path="/etc/passwd")
    assert "只允许访问当前会话目录" in out
    out2 = _call(tools["list_dir"], path="../../escape")
    assert "路径越界" in out2 or "只允许访问" in out2


def test_list_dir_not_a_dir(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["workspace"]) / "file.txt").write_text("x")
    _, tools = _build_tools(paths)
    out = _call(tools["list_dir"], path="workspace/file.txt")
    assert "目标不是目录" in out


def test_list_dir_empty_dir(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    (Path(paths["workspace"]) / "empty").mkdir()
    _, tools = _build_tools(paths)
    out = _call(tools["list_dir"], path="workspace/empty")
    assert "count=0" in out


def test_list_dir_max_depth_limits(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    ws = Path(paths["workspace"])
    (ws / "sub" / "deep").mkdir(parents=True)
    (ws / "sub" / "deep" / "leaf.txt").write_text("x")
    _, tools = _build_tools(paths)
    # max_depth=1: 仅直接子项（sub/），不展开 deep、不列 leaf
    shallow = _call(tools["list_dir"], path="workspace", max_depth=1)
    assert "type=dir  path=/mnt/user-data/workspace/sub/" in shallow
    assert "deep/" not in shallow
    assert "leaf.txt" not in shallow
    # max_depth=2: 展开一层子目录（sub/deep/），仍不列 leaf（第三层）
    mid = _call(tools["list_dir"], path="workspace", max_depth=2)
    assert "type=dir  path=/mnt/user-data/workspace/sub/deep/" in mid
    assert "leaf.txt" not in mid
    # max_depth=3: 列到 leaf
    full = _call(tools["list_dir"], path="workspace", max_depth=3)
    assert "type=file  path=/mnt/user-data/workspace/sub/deep/leaf.txt" in full


def test_list_dir_skill_path(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    skill_ref = tmp_path / "skills" / "demo" / "references"
    skill_ref.mkdir(parents=True)
    (skill_ref / "guide.md").write_text("# guide")
    _, tools = _build_tools(paths, skills=[("system", "demo", str(skill_ref.parent))])
    out = _call(tools["list_dir"], path="/mnt/skills/system/demo/references")
    assert "type=file  path=/mnt/skills/system/demo/references/guide.md" in out


def test_list_dir_unauthorized_skill_rejected(tmp_path):
    paths = ensure_conversation_workspace(str(tmp_path), user_id="system")
    _, tools = _build_tools(paths)
    out = _call(tools["list_dir"], path="/mnt/skills/system/unknown/references")
    # 无注册门控：解析改为按 user_id 权限从磁盘/数据库校验，未知技能返回「不存在/无权限」
    assert "技能不存在" in out or "无权限" in out or "技能路径无效" in out
