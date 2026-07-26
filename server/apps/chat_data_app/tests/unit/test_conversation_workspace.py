import os
import sys
import uuid
import json
import importlib
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from apps.chat_data_app.services.conversation_workspace import (
    ConversationWorkspaceRuntime,
    ensure_conversation_workspace,
    list_workspace_files,
    read_workspace_text_file,
    resolve_workspace_path,
    to_visible_path,
)
from apps.chat_data_app.agent.data_agent import assemble_tools
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.agent.tools.dsl2sql import normalize_dsl_payload
from apps.chat_data_app.services.workspace_execution import _artifact_payload_from_bytes

sql_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.semantic_query")
file_edit_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.file_edit")
file_read_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.file_read")
present_artifacts_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.present_artifacts")
skill_load_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.skill_load")


def _create_local_workspace_tools(_monkeypatch, paths, **kwargs):
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="system",
        backend="local",
    )
    return {
        tool.name: tool
        for tool in assemble_tools(AgentRuntimeOptions(), runtime, **kwargs)
    }


def _analysis_query_state(
    paths,
    *,
    datasource_id: str | None = "ds_1",
    semantic_config_id: str | None = "sc_1",
    member_datasource_ids: list[str] | None = None,
    conversation_id: str | None = None,
    user_query: str = "统计销售额",
) -> dict:
    return {
        "execution_context": {
            "datasource_id": datasource_id,
            "available_member_datasource_ids": member_datasource_ids or ["m1"],
            "matched_member_datasource_ids": [],
        },
        "semantic_config_id": semantic_config_id,
        "user_id": "system",
        "conversation_id": conversation_id,
        "workspace": {"workspace_root": paths["root"], "workspace_backend": "local"},
        "messages": [HumanMessage(content=user_query)],
    }


def test_ensure_conversation_workspace_creates_expected_dirs():
    paths = ensure_conversation_workspace("conv_workspace_test", "user_a")

    assert Path(paths["root"]).exists()
    assert Path(paths["root"]).as_posix().endswith("users/user_a/threads/conv_workspace_test/user-data")
    assert Path(paths["workspace"]).exists()
    assert Path(paths["outputs"]).exists()
    assert Path(paths["uploads"]).exists()
    assert "runtime_skills" not in paths


def test_ensure_workspace_rejects_invalid_conversation_id():
    for conversation_id in ["", ".", "..", ".hidden", "../bad"]:
        with pytest.raises(ValueError):
            ensure_conversation_workspace(conversation_id)


def test_resolve_workspace_path_rejects_escape_and_symlink_escape(tmp_path):
    paths = ensure_conversation_workspace(f"conv_workspace_escape_{uuid.uuid4().hex}")

    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "../secret.txt")

    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "/tmp/secret.txt")

    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    (Path(paths["workspace"]) / "outside-link.txt").symlink_to(outside)
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "workspace/outside-link.txt")


def test_resolve_workspace_path_restricts_writes():
    paths = ensure_conversation_workspace("conv_workspace_writes")

    writable = resolve_workspace_path(paths["root"], "workspace/plan.md", writable=True)
    assert writable.name == "plan.md"
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "other-files/demo/SKILL.md", writable=True)


def test_resolve_workspace_path_accepts_short_absolute_aliases():
    paths = ensure_conversation_workspace(f"conv_workspace_alias_{uuid.uuid4().hex}")

    workspace_file = resolve_workspace_path(paths["root"], "/workspace/demo.json", writable=True)
    outputs_file = resolve_workspace_path(paths["root"], "/outputs/sales.chart.json", writable=True)

    assert workspace_file == Path(paths["workspace"]) / "demo.json"
    assert outputs_file == Path(paths["outputs"]) / "sales.chart.json"


def test_workspace_json_list_read_and_visible_paths():
    conversation_id = f"conv_workspace_file_ops_{uuid.uuid4().hex}"
    paths = ensure_conversation_workspace(conversation_id)
    context_file = Path(paths["workspace"]) / "context.json"
    context_file.write_text('{"用户": "张三"}', encoding="utf-8")
    visible_path = to_visible_path(paths["root"], context_file)

    assert visible_path == "/workspace/context.json"
    assert resolve_workspace_path(paths["root"], "././workspace/context.json") == Path(paths["workspace"]) / "context.json"
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "")
    (Path(paths["root"]) / "outputs").rmdir()
    (Path(paths["workspace"]) / "nested-dir").mkdir()
    files = list_workspace_files(conversation_id)
    assert any(item["path"] == "/workspace/context.json" and item["directory"] == "workspace" for item in files)

    payload = read_workspace_text_file(conversation_id, "/mnt/user-data/workspace/context.json")
    assert payload["path"] == "/workspace/context.json"
    assert payload["relative_path"] == "workspace/context.json"
    assert '"用户": "张三"' in payload["content"]

    outside_path = Path(paths["root"]).parent / "outside.txt"
    outside_path.write_text("outside", encoding="utf-8")
    assert to_visible_path(paths["root"], outside_path) == str(outside_path)


def test_read_workspace_text_file_rejects_missing_and_large_files():
    conversation_id = f"conv_workspace_read_errors_{uuid.uuid4().hex}"
    paths = ensure_conversation_workspace(conversation_id)
    with pytest.raises(FileNotFoundError):
        read_workspace_text_file(conversation_id, "workspace/missing.txt")

    target = Path(paths["workspace"]) / "big.txt"
    target.write_text("abcdef", encoding="utf-8")
    with pytest.raises(ValueError):
        read_workspace_text_file(conversation_id, "workspace/big.txt", max_bytes=3)


@pytest.mark.asyncio
async def test_skill_load_tool_builds_payload_in_local_workspace(monkeypatch, tmp_path):
    skill_dir = tmp_path / "demo-skill"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "assets").mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ntitle: 演示技能\ndescription: 演示说明\n---\n# 演示技能\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "guide.md").write_text("# guide\n", encoding="utf-8")
    (skill_dir / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "assets" / "demo.txt").write_text("asset\n", encoding="utf-8")

    async def fake_resolve_skill_path_async(name, user_id=""):
        assert name == "demo-skill"
        assert user_id == "u1"
        return {"path": str(skill_dir / "SKILL.md"), "title": "演示技能", "description": "演示说明"}

    monkeypatch.setattr(skill_load_tool_module.skill_service, "resolve_skill_path_async", fake_resolve_skill_path_async)
    workspace_root = tmp_path / "workspace-root"
    workspace_root.mkdir()
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root), user_id="u1", backend="local",
    )
    tool = skill_load_tool_module.create_skill_load_tool(runtime, user_id="u1")
    result = await tool.ainvoke({"skill_name": "demo-skill"})

    assert result.startswith("[skill_load]\n")
    assert "skill_name=demo-skill" in result
    assert "title=演示技能" in result
    assert "runtime_path=/mnt/skills/public/demo-skill" in result
    assert 'references=["/mnt/skills/public/demo-skill/references/guide.md"]' in result
    assert 'scripts=["/mnt/skills/public/demo-skill/scripts/run.py"]' in result
    assert 'assets=["/mnt/skills/public/demo-skill/assets/demo.txt"]' in result
    assert "# 演示技能" in result


def test_normalize_dsl_payload_accepts_escaped_json_string():
    payload = {"metrics": ["订单金额"], "dimensions": [], "filters": [], "order_by": [], "limit": 100}

    assert normalize_dsl_payload(json.dumps(payload, ensure_ascii=False)) == payload
    assert normalize_dsl_payload(json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False)) == payload
    assert normalize_dsl_payload("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```") == payload


def test_outputs_chart_json_artifact_detected_by_suffix_and_schema():
    payload = {
        "version": "1.0",
        "title": "销售额趋势",
        "chart_type": "line",
        "data": [{"month": "2026-01", "sales": 120}],
        "encoding": {"x": "month", "y": ["sales"]},
    }

    artifact = _artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/sales.chart.json",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )

    assert artifact["artifact_type"] == "chart_spec"
    assert artifact["renderer"] == "chart"
    assert artifact["chart_spec"]["title"] == "销售额趋势"


def test_outputs_dashboard_json_artifact_detected_by_suffix():
    payload = {"dashboard_name": "订单看板", "cards": [{"title": "订单数"}]}

    artifact = _artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/order.dashboard.json",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )

    assert artifact["artifact_type"] == "dashboard_spec"
    assert artifact["renderer"] == "dashboard"
    assert artifact["dashboard_spec"]["dashboard_name"] == "订单看板"


def test_outputs_standard_file_artifact_detected_by_native_suffix():
    artifact = _artifact_payload_from_bytes(
        visible_path="/mnt/user-data/outputs/report.docx",
        data=b"",
    )

    assert artifact["artifact_type"] == "document"
    assert artifact["renderer"] == "document"


@pytest.mark.asyncio
async def test_file_edit_accepts_structured_content(monkeypatch):
    paths = ensure_conversation_workspace("conv_workspace_file_edit")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "file_path": "workspace/plan.json",
        "content": {"cards": [{"title": "风险总览"}]},
        "mode": "overwrite",
    })

    assert "[file_edit]" in result
    assert '"风险总览"' in (Path(paths["workspace"]) / "plan.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_file_edit_rejects_invalid_operation_and_mode(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_edit_invalid_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    bad_operation = await tools["file_edit"].ainvoke({"operation": "delete", "file_path": "workspace/a.txt"})
    bad_mode = await tools["file_edit"].ainvoke({"operation": "write", "mode": "bad", "file_path": "workspace/a.txt"})

    assert "operation 只支持 write / patch" in bad_operation
    assert "mode 只支持 overwrite / append" in bad_mode


@pytest.mark.asyncio
async def test_file_edit_append_uses_local_workspace(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_edit_append_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    await tools["file_edit"].ainvoke({"file_path": "workspace/a.txt", "content": "first\n"})
    await tools["file_edit"].ainvoke({"file_path": "workspace/a.txt", "content": "second\n", "mode": "append"})
    assert (Path(paths["workspace"]) / "a.txt").read_text(encoding="utf-8") == "first\nsecond\n"

    result = await tools["file_edit"].ainvoke({"file_path": "workspace/b.txt", "content": "x"})
    assert "[file_edit]" in result
    assert (Path(paths["workspace"]) / "b.txt").read_text(encoding="utf-8") == "x"


@pytest.mark.asyncio
async def test_present_artifacts_returns_outputs_artifact_payload(monkeypatch):
    paths = ensure_conversation_workspace("conv_present_artifacts")
    chart_path = Path(paths["outputs"]) / "sales.chart.json"
    chart_path.write_text(json.dumps({
        "version": "1.0", "title": "销售额趋势", "chart_type": "line",
        "data": [{"month": "2026-01", "sales": 120}],
        "encoding": {"x": "month", "y": ["sales"]},
    }, ensure_ascii=False), encoding="utf-8")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({"files": ["/outputs/sales.chart.json"]})

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 1
    assert payload["errors"] == []
    assert payload["artifacts"][0]["artifact_type"] == "chart_spec"
    assert payload["artifacts"][0]["file_path"] == "/outputs/sales.chart.json"
    assert payload["artifacts"][0]["name"] == "sales"
    assert payload["artifacts"][0]["mime_type"] == "application/json"
    assert "renderer" not in payload["artifacts"][0]
    assert "artifact_payload" not in payload["artifacts"][0]
    assert "chart_spec" not in payload["artifacts"][0]


@pytest.mark.asyncio
async def test_present_artifacts_rejects_non_artifact(monkeypatch):
    paths = ensure_conversation_workspace("conv_present_artifacts_reject")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({"files": ["workspace/draft.json"]})

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 0
    assert payload["artifacts"] == []
    assert payload["errors"][0]["file_path"] == "workspace/draft.json"


@pytest.mark.asyncio
async def test_present_artifacts_skips_empty_file_and_records_payload_errors(monkeypatch):
    paths = ensure_conversation_workspace("conv_present_artifacts_error")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({"files": ["", "outputs/bad.unknown"]})

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 0
    assert payload["artifacts"] == []
    assert payload["errors"] == [{"file_path": "outputs/bad.unknown", "error": "文件不是可展示的 outputs 产物"}]


def test_present_artifacts_display_name_and_mime_helpers():
    assert present_artifacts_tool_module._artifact_display_name("/mnt/user-data/outputs/sales.chart.json") == "sales"
    assert present_artifacts_tool_module._artifact_display_name("/mnt/user-data/outputs/dashboard.dashboard.json") == "dashboard"
    assert present_artifacts_tool_module._artifact_display_name("/mnt/user-data/outputs/report.pdf") == "report"
    assert present_artifacts_tool_module._artifact_display_name("/mnt/user-data/outputs/README") == "README"
    assert present_artifacts_tool_module._artifact_display_name("") == ""
    assert present_artifacts_tool_module._artifact_mime_type("report.pdf", "file") == "application/pdf"
    assert present_artifacts_tool_module._artifact_mime_type("unknown.bin", "dashboard_spec") == "application/json"


@pytest.mark.asyncio
async def test_file_read_default_limit_is_500(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "long.txt"
    target.write_text("\n".join(f"line {index}" for index in range(1, 602)), encoding="utf-8")

    result = await tools["file_read"].ainvoke({"file_path": "workspace/long.txt"})

    assert "[file_read]" in result
    assert "limit=500" in result
    assert "end_line=500" in result
    assert "total_lines=601" in result
    assert "has_more=true" in result


@pytest.mark.asyncio
async def test_file_read_outputs_graph_chart_spec_returns_content(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_graph_chart_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["outputs"]) / "chart_spec_graph.json"
    target.write_text(json.dumps({
        "version": "1.0",
        "title": "商品订单关系图",
        "chart_type": "graph",
        "nodes": [{"name": "商品A"}, {"name": "订单1"}],
        "links": [{"source": "商品A", "target": "订单1", "value": 1}],
    }, ensure_ascii=False), encoding="utf-8")

    result = await tools["file_read"].ainvoke({"file_path": "outputs/chart_spec_graph.json"})

    assert "[file_read]" in result
    assert '"chart_type": "graph"' in result
    assert "商品订单关系图" in result


@pytest.mark.asyncio
async def test_file_read_rejects_invalid_offset_and_limit(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_invalid_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    assert await tools["file_read"].ainvoke({"file_path": "workspace/demo.txt", "offset": -1}) == "offset 必须大于等于 0"
    assert await tools["file_read"].ainvoke({"file_path": "workspace/demo.txt", "limit": 0}) == "limit 必须在 1-1000 之间"
    assert await tools["file_read"].ainvoke({"file_path": "workspace/demo.txt", "limit": 1001}) == "limit 必须在 1-1000 之间"


@pytest.mark.asyncio
async def test_file_read_reports_missing_dir_binary_empty_and_offset_overflow(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_edges_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    (Path(paths["workspace"]) / "empty.txt").write_text("", encoding="utf-8")
    (Path(paths["workspace"]) / "one.txt").write_text("line1\n", encoding="utf-8")
    (Path(paths["workspace"]) / "bin.dat").write_bytes(b"a\x00b")

    missing = await tools["file_read"].ainvoke({"file_path": "workspace/missing.txt"})
    directory = await tools["file_read"].ainvoke({"file_path": "workspace"})
    binary = await tools["file_read"].ainvoke({"file_path": "workspace/bin.dat"})
    empty = await tools["file_read"].ainvoke({"file_path": "workspace/empty.txt"})
    overflow = await tools["file_read"].ainvoke({"file_path": "workspace/one.txt", "offset": 5})

    assert missing == "文件不存在: workspace/missing.txt"
    assert "目标是目录" in directory
    assert "暂不支持以文本方式读取二进制文件" in binary
    assert "status=empty" in empty
    assert "offset 5 超出文件总行数" in overflow


@pytest.mark.asyncio
async def test_file_read_rejects_local_path_escape(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_fail_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_read"].ainvoke({"file_path": "../demo.txt"})

    assert result.startswith("file_read 执行失败: 路径越界")


@pytest.mark.asyncio
async def test_file_edit_patch_updates_file(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_patch_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    patch = """*** Begin Patch
*** Add File: workspace/demo.txt
+hello
*** Update File: workspace/demo.txt
@@
-hello
+world
*** End Patch"""

    result = await tools["file_edit"].ainvoke({"patch": patch})

    assert "[file_edit]" in result
    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["operation"] == "patch"
    assert payload["action_count"] == 2
    assert (Path(paths["workspace"]) / "demo.txt").read_text(encoding="utf-8") == "world\n"


@pytest.mark.asyncio
async def test_file_edit_patch_dry_run_does_not_write_file(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_patch_dry_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    patch = """*** Begin Patch
*** Add File: workspace/dry.txt
+dry
*** End Patch"""

    result = await tools["file_edit"].ainvoke({"patch": patch, "dry_run": True})
    payload = json.loads(result.replace("[file_edit]\n", "", 1))

    assert payload["dry_run"] is True
    assert payload["actions"] == ["add workspace/dry.txt"]
    assert not (Path(paths["workspace"]) / "dry.txt").exists()


def test_parse_patch_rejects_malformed_inputs():
    with pytest.raises(file_edit_tool_module.PatchError, match="must start"):
        file_edit_tool_module.parse_patch("*** Add File: workspace/a.txt")
    with pytest.raises(file_edit_tool_module.PatchError, match="unexpected content"):
        file_edit_tool_module.parse_patch("*** Begin Patch\n*** End Patch\nextra")
    with pytest.raises(file_edit_tool_module.PatchError, match="add file lines"):
        file_edit_tool_module.parse_patch("*** Begin Patch\n*** Add File: workspace/a.txt\nbad\n*** End Patch")
    with pytest.raises(file_edit_tool_module.PatchError, match="missing '@@'"):
        file_edit_tool_module.parse_patch("*** Begin Patch\n*** Update File: workspace/a.txt\n+bad\n*** End Patch")
    with pytest.raises(file_edit_tool_module.PatchError, match="invalid update line"):
        file_edit_tool_module.parse_patch("*** Begin Patch\n*** Update File: workspace/a.txt\n@@\n?bad\n*** End Patch")
    with pytest.raises(file_edit_tool_module.PatchError, match="no hunks"):
        file_edit_tool_module.parse_patch("*** Begin Patch\n*** Update File: workspace/a.txt\n*** End Patch")
    with pytest.raises(file_edit_tool_module.PatchError, match="unexpected patch header"):
        file_edit_tool_module.parse_patch("*** Begin Patch\n*** Move File: workspace/a.txt\n*** End Patch")
    with pytest.raises(file_edit_tool_module.PatchError, match="missing"):
        file_edit_tool_module.parse_patch("*** Begin Patch\n*** Add File: workspace/a.txt\n+x")


def test_parse_patch_accepts_delete_eof_and_multiple_update_hunks():
    ops = file_edit_tool_module.parse_patch(
        "*** Begin Patch\n"
        "*** Delete File: workspace/remove.txt\n"
        "*** Update File: workspace/demo.txt\n"
        "@@\n"
        " old\n"
        "-a\n"
        "+b\n"
        "*** End of File\n"
        "@@ second\n"
        " b\n"
        "+c\n"
        "*** End Patch"
    )

    assert isinstance(ops[0], file_edit_tool_module.DeleteOp)
    assert isinstance(ops[1], file_edit_tool_module.UpdateOp)
    assert len(ops[1].hunks) == 2


def test_apply_patch_ops_rejects_existing_add_missing_delete_and_bad_context(tmp_path):
    root = tmp_path
    (root / "workspace").mkdir()
    (root / "workspace" / "exists.txt").write_text("old\n", encoding="utf-8")

    with pytest.raises(file_edit_tool_module.PatchError, match="existing file"):
        file_edit_tool_module.apply_patch_ops(
            [file_edit_tool_module.AddOp(path="workspace/exists.txt", lines=["new"])],
            root=str(root),
        )
    with pytest.raises(file_edit_tool_module.PatchError, match="missing file"):
        file_edit_tool_module.apply_patch_ops(
            [file_edit_tool_module.DeleteOp(path="workspace/missing.txt")],
            root=str(root),
        )
    with pytest.raises(file_edit_tool_module.PatchError, match="missing file"):
        file_edit_tool_module.apply_patch_ops(
            [file_edit_tool_module.UpdateOp(path="workspace/missing.txt", hunks=[file_edit_tool_module.Hunk(lines=[" old", "+new"])])],
            root=str(root),
        )
    with pytest.raises(file_edit_tool_module.PatchError, match="failed to locate"):
        file_edit_tool_module.apply_patch_ops(
            [file_edit_tool_module.UpdateOp(path="workspace/exists.txt", hunks=[file_edit_tool_module.Hunk(lines=[" missing", "+new"])])],
            root=str(root),
        )


def test_apply_patch_ops_delete_and_update_without_trailing_newline(tmp_path):
    root = tmp_path
    workspace = root / "workspace"
    workspace.mkdir()
    delete_target = workspace / "remove.txt"
    update_target = workspace / "exists.txt"
    delete_target.write_text("remove", encoding="utf-8")
    update_target.write_text("old", encoding="utf-8")

    actions = file_edit_tool_module.apply_patch_ops(
        [
            file_edit_tool_module.DeleteOp(path="workspace/remove.txt"),
            file_edit_tool_module.UpdateOp(
                path="workspace/exists.txt",
                hunks=[file_edit_tool_module.Hunk(lines=["-old", "+new"])],
            ),
        ],
        root=str(root),
    )

    assert actions == ["delete workspace/remove.txt", "update workspace/exists.txt"]
    assert not delete_target.exists()
    assert update_target.read_text(encoding="utf-8") == "new\n"


def test_file_edit_private_patch_helpers_cover_remaining_branches():
    assert file_edit_tool_module._find_hunk_start(["a", "b"], [], 1) == 1
    assert file_edit_tool_module._ops_to_sandbox_payload([
        file_edit_tool_module.DeleteOp(path="workspace/remove.txt")
    ]) == [{"kind": "delete", "path": "workspace/remove.txt", "visible_path": "workspace/remove.txt"}]


def test_file_edit_next_action_helpers_cover_path_variants():
    assert "草稿已更新" in file_edit_tool_module._dashboard_next_action_for_path(
        "/workspace/dashboard_draft/demo.json",
        "write",
    )
    assert "看板预览文件已更新" in file_edit_tool_module._dashboard_next_action_for_path(
        "/mnt/user-data/outputs/demo.dashboard.json",
        "write",
    )
    assert "文件已 patch 更新" in file_edit_tool_module._dashboard_next_action_for_path(
        "workspace/notes.txt",
        "patch",
    )
    assert file_edit_tool_module._dashboard_next_action_for_path("workspace/notes.txt", "write") is None

    payload = {}
    file_edit_tool_module._attach_dashboard_next_action(
        payload,
        operation="write",
        paths=["workspace/notes.txt", "outputs/demo.dashboard.json"],
    )
    assert "看板预览文件已更新" in payload["next_required_action"]

@pytest.mark.asyncio
async def test_file_edit_write_workspace_dashboard_spec_has_no_dashboard_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_dashboard_draft_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "operation": "write",
        "file_path": "workspace/dashboard_draft.json",
        "content": {"dashboard_name": "草稿看板", "cards": [{"title": "销售额"}]},
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/workspace/dashboard_draft.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_write_returns_chart_spec_payload(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "operation": "write",
        "file_path": "outputs/monthly_simple_bar.chart.json",
        "content": {
            "version": "1.0",
            "title": "销售额趋势",
            "chart_type": "line",
            "data": [{"month": "2026-01", "sales": 120}],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/outputs/monthly_simple_bar.chart.json"
    assert payload["artifact_type"] == "chart_spec"
    assert payload["artifact_version"] == "v1"
    assert payload["artifact_payload"]["title"] == "销售额趋势"
    assert payload["chart_spec"]["title"] == "销售额趋势"


@pytest.mark.asyncio
async def test_file_edit_write_workspace_chart_spec_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_chart_draft_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "operation": "write",
        "file_path": "workspace/chart_spec_sales_trend_draft.json",
        "content": {
            "version": "1.0",
            "title": "销售额趋势",
            "chart_type": "line",
            "data": [{"month": "2026-01", "sales": 120}],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/workspace/chart_spec_sales_trend_draft.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_write_non_chart_json_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_non_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "operation": "write",
        "file_path": "outputs/query_result.json",
        "content": {"rows": [{"month": "2026-01", "sales": 120}]},
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/outputs/query_result.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_write_empty_line_chart_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_empty_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "operation": "write",
        "file_path": "outputs/empty_chart.json",
        "content": {
            "version": "1.0",
            "title": "空趋势图",
            "chart_type": "line",
            "data": [],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/outputs/empty_chart.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_write_invalid_chart_spec_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_invalid_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "operation": "write",
        "file_path": "outputs/invalid_chart.json",
        "content": {
            "chart_type": "line",
            "data": [{"month": "2026-01", "sales": 120}],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/outputs/invalid_chart.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_write_chart_spec_with_missing_encoding_field_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_invalid_chart_encoding_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_edit"].ainvoke({
        "operation": "write",
        "file_path": "outputs/invalid_chart_encoding.json",
        "content": {
            "version": "1.0",
            "title": "同比趋势",
            "chart_type": "line",
            "data": [{"period": "2024-01", "sales_current": 87, "sales_previous": 71}],
            "encoding": {"x": "period", "y": ["current", "previous"]},
        },
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/outputs/invalid_chart_encoding.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_patch_returns_dashboard_spec_payload(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_dashboard_patch_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["outputs"]) / "dashboard.dashboard.json"
    target.write_text(json.dumps({
        "dashboard_name": "旧看板",
        "cards": [{
            "title": "订单金额",
            "chart_type": "kpi",
            "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
            "dsl": {"metrics": ["订单金额"], "dimensions": [], "filters": [], "order_by": [], "limit": 100},
        }],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    patch = """*** Begin Patch
*** Update File: outputs/dashboard.dashboard.json
@@
-  "dashboard_name": "旧看板",
+  "dashboard_name": "新看板",
*** End Patch"""

    result = await tools["file_edit"].ainvoke({"operation": "patch", "patch": patch})

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/outputs/dashboard.dashboard.json"
    assert payload["artifact_type"] == "dashboard_spec"
    assert payload["dashboard_spec"]["dashboard_name"] == "新看板"


@pytest.mark.asyncio
async def test_file_edit_patch_rejects_uploads_write(monkeypatch):
    paths = ensure_conversation_workspace("conv_workspace_file_patch_uploads")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    patch = """*** Begin Patch
*** Add File: uploads/demo.txt
+bad
*** End Patch"""

    result = await tools["file_edit"].ainvoke({"patch": patch})

    assert "file_edit 执行失败" in result


@pytest.mark.asyncio
async def test_semantic_query_tool_save_as_writes_query_result(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_save_{uuid.uuid4().hex}")
    writes = []
    input_dsl = {"metrics": ["销售额"], "dimensions": ["月份"]}

    async def fake_get_config(datasource_id, semantic_config_id, user_id=None):
        assert datasource_id == "ds_1"
        assert semantic_config_id == "sc_1"
        return {"id": "sc_1", "metrics": [], "dimensions": []}

    class FakeGenerator:
        def __init__(self, semantic_config):
            assert semantic_config["id"] == "sc_1"

        def generate_sql(self, **kwargs):
            assert kwargs["metrics"] == ["销售额"]
            assert kwargs["dimensions"] == ["月份"]
            return {"success": True, "sql": "select month, sales from t"}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids=None, user_id=None, semantic_config_id=None, query_text=None, **kwargs):
        assert sql == "select month, sales from t"
        assert query_text == "统计销售额"
        return {
            "status": "succeeded",
            "sql": sql,
            "results": [{
                "status": "succeeded",
                "member_datasource_id": "m1",
                "member_name": "机构1",
                "columns": ["month", "sales"],
                "data": [{"month": "2026-01", "sales": 120}],
                "total_count": 1,
            }],
            "total_count": 1,
            "execution_time_ms": 12,
        }

    monkeypatch.setattr(sql_tool_module, "load_runtime_semantic_config", fake_get_config)
    monkeypatch.setattr(sql_tool_module, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
        semantic_config_id="sc_1",
    )
    state = _analysis_query_state(paths, user_query="统计销售额")

    result = await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": input_dsl,
        "save_as": "workspace/current.json",
        "state": state,
    })

    payload = json.loads(result)
    assert payload["type"] == "query_result"
    assert payload["status"] == "succeeded"
    assert payload["data"] is None
    assert payload["meta"]["result_path"] == "/workspace/current.json"
    assert payload["meta"]["sql"] == "select month, sales from t"
    assert "dsl" not in payload["meta"]
    assert "datasource_id" not in payload["meta"]
    assert "member_datasource_ids" not in payload["meta"]
    saved = json.loads((Path(paths["workspace"]) / "current.json").read_text(encoding="utf-8"))
    assert saved["results"][0]["data"][0]["sales"] == 120


@pytest.mark.asyncio
async def test_semantic_query_tool_without_save_as_keeps_full_result(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_full_{uuid.uuid4().hex}")
    input_dsl = {"metrics": ["订单数"]}

    async def fake_get_config(*_args, **_kwargs):
        return {"id": "sc_1", "metrics": [{"name": "订单数"}], "dimensions": []}

    class FakeGenerator:
        def __init__(self, _semantic_config):
            pass

        def generate_sql(self, **kwargs):
            assert kwargs["metrics"] == ["订单数"]
            return {"success": True, "sql": "select count(*) cnt from t"}

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids=None, user_id=None, semantic_config_id=None, query_text=None, **kwargs):
        assert sql == "select count(*) cnt from t"
        return {
            "status": "succeeded",
            "sql": sql,
            "results": [{
                "status": "succeeded",
                "member_datasource_id": "m1",
                "member_name": "机构1",
                "columns": ["cnt"],
                "data": [{"cnt": 3}],
                "total_count": 1,
            }],
            "total_count": 1,
        }

    monkeypatch.setattr(sql_tool_module, "load_runtime_semantic_config", fake_get_config)
    monkeypatch.setattr(sql_tool_module, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
        semantic_config_id="sc_1",
    )
    state = _analysis_query_state(paths, user_query="查询订单数")

    result = await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": input_dsl,
        "state": state,
    })

    payload = json.loads(result)
    assert payload["type"] == "query_result"
    assert "result_path" not in payload["meta"]
    assert payload["data"]["results"][0]["data"] == [{"cnt": 3}]
    assert payload["data"]["sql"] == "select count(*) cnt from t"
    assert "dsl" not in payload["data"]

    blank_save_result = await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": input_dsl,
        "save_as": "   ",
        "state": state,
    })
    blank_save_payload = json.loads(blank_save_result)
    assert "result_path" not in blank_save_payload["meta"]
    assert blank_save_payload["data"]["results"][0]["data"] == [{"cnt": 3}]


@pytest.mark.asyncio
async def test_semantic_query_tool_without_save_as_offloads_large_result(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_offload_{uuid.uuid4().hex}")

    async def fake_get_config(*_args, **_kwargs):
        return {"id": "sc_1", "metrics": [], "dimensions": []}

    class FakeGenerator:
        def __init__(self, _semantic_config):
            pass

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select content from t"}

    async def fake_execute_sql_real(sql, *_args, **_kwargs):
        return {
            "status": "succeeded",
            "sql": sql,
            "results": [{
                "status": "succeeded",
                "member_datasource_id": "m1",
                "member_name": "机构1",
                "columns": ["content"],
                "data": [{"content": "x" * 200}],
                "total_count": 1,
            }],
            "total_count": 1,
        }

    captured = {}

    def fake_save_query_result(result, conversation_id=None):
        captured["result"] = result
        captured["conversation_id"] = conversation_id
        return "artifact_analyze_large"

    monkeypatch.setattr(sql_tool_module, "load_runtime_semantic_config", fake_get_config)
    monkeypatch.setattr(sql_tool_module, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    monkeypatch.setattr(sql_tool_module, "save_query_result", fake_save_query_result)
    monkeypatch.setattr(sql_tool_module, "TOOL_RESULT_OFFLOAD_SIZE", 100)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
        semantic_config_id="sc_1",
    )
    state = _analysis_query_state(paths, conversation_id="conv_large")

    result = await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"metrics": ["内容"]},
        "state": state,
    })

    payload = json.loads(result)
    assert payload["type"] == "query_result"
    assert payload["data"] is None
    assert payload["meta"]["artifact_id"] == "artifact_analyze_large"
    assert "x" * 120 not in result
    assert captured["result"]["results"][0]["data"][0]["content"] == "x" * 200
    assert captured["conversation_id"] == "conv_large"


@pytest.mark.asyncio
async def test_semantic_query_tool_rejects_missing_datasource_workspace_and_non_json_save(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_reject_{uuid.uuid4().hex}")
    tools_without_datasource = _create_local_workspace_tools(monkeypatch, paths, semantic_config_id="sc_1")
    state_without_datasource = _analysis_query_state(paths, datasource_id=None)

    assert await tools_without_datasource["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"metrics": ["订单数"]},
        "state": state_without_datasource,
    }) == "semantic_query_tool 执行失败: 当前会话未绑定数据源"

    async def fake_execute_sql_real(sql, datasource_id, member_datasource_ids=None, user_id=None, **kwargs):
        return {"success": True, "sql": sql, "columns": [], "data": []}

    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    async def fake_get_config(*_args, **_kwargs):
        return {"id": "sc_1", "metrics": [{"name": "订单数"}], "dimensions": []}

    monkeypatch.setattr(sql_tool_module, "load_runtime_semantic_config", fake_get_config)
    assert await sql_tool_module.semantic_query_tool.ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "sql": "select 1",
        "save_as": "workspace/a.json",
        "state": {
            "execution_context": {
                "datasource_id": "ds_1",
                "available_member_datasource_ids": ["m1"],
                "matched_member_datasource_ids": [],
            },
            "semantic_config_id": "sc_1",
            "workspace": {},
        },
    }) == "semantic_query_tool 执行失败: 当前会话未初始化工作目录，无法保存结果"

    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
        semantic_config_id="sc_1",
    )
    state = _analysis_query_state(paths)
    assert await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"metrics": ["订单数"]},
        "save_as": "workspace/a.txt",
        "state": state,
    }) == "semantic_query_tool 执行失败: save_as 只支持 .json 文件"
    assert await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"sql": "select 1"},
        "state": state,
    }) == "semantic_query_tool 执行失败: sql 必须放在顶层 sql 参数中，不要放入 dsl"

    with pytest.raises(ValueError, match="semantic_config_id"):
        await sql_tool_module.semantic_query_tool.ainvoke({
            "semantic_config_id": "",
            "member_datasource_ids": ["m1"],
            "dsl": {"metrics": ["订单数"]},
            "state": _analysis_query_state(paths),
        })
    with pytest.raises(ValueError, match="dsl"):
        await tools["semantic_query_tool"].ainvoke({
            "semantic_config_id": "sc_1",
            "member_datasource_ids": ["m1"],
            "dsl": [{"metrics": ["订单数"]}],
            "state": state,
        })
    assert await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"cards": []},
        "state": state,
    }) == "semantic_query_tool 执行失败: dsl 必须是单个查询对象"


@pytest.mark.asyncio
async def test_semantic_query_tool_returns_compile_errors(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_compile_{uuid.uuid4().hex}")

    async def fake_get_config(*_args, **_kwargs):
        return {"id": "sc_1", "metrics": [], "dimensions": []}

    class FakeGenerator:
        def __init__(self, _semantic_config):
            pass

        def generate_sql(self, **kwargs):
            metrics = kwargs.get("metrics") or []
            if metrics == ["bad"]:
                return {"success": False, "error": "指标不存在"}
            if metrics == ["empty"]:
                return {"success": True, "sql": "   "}
            return {"success": True, "sql": "select ok"}

    async def fake_execute_sql_real(sql, *_args, **_kwargs):
        return {"success": True, "columns": ["ok"], "data": [{"ok": 1}], "total_count": 1}

    monkeypatch.setattr(sql_tool_module, "load_runtime_semantic_config", fake_get_config)
    monkeypatch.setattr(sql_tool_module, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
        semantic_config_id="sc_1",
    )
    state = _analysis_query_state(paths)

    args = {"semantic_config_id": "sc_1", "member_datasource_ids": ["m1"], "state": state}
    compile_error = await tools["semantic_query_tool"].ainvoke({**args, "dsl": {"metrics": ["bad"]}})
    empty_sql = await tools["semantic_query_tool"].ainvoke({**args, "dsl": {"metrics": ["empty"]}})

    assert compile_error == "semantic_query_tool 执行失败: 指标不存在"
    assert empty_sql == "semantic_query_tool 执行失败: DSL2SQL 未生成 SQL"


@pytest.mark.asyncio
async def test_semantic_query_tool_returns_dependency_error(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_error_{uuid.uuid4().hex}")

    async def fake_get_config(*_args, **_kwargs):
        return {"id": "sc_1", "metrics": [], "dimensions": []}

    class FakeGenerator:
        def __init__(self, _semantic_config):
            pass

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_execute_sql_real(*_args, **_kwargs):
        raise RuntimeError("执行失败")

    monkeypatch.setattr(sql_tool_module, "load_runtime_semantic_config", fake_get_config)
    monkeypatch.setattr(sql_tool_module, "SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
        semantic_config_id="sc_1",
    )
    state = _analysis_query_state(paths)

    assert await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"metrics": ["订单数"]},
        "state": state,
    }) == "semantic_query_tool 执行失败: 执行失败"
