import os
import sys
import uuid
import json
import importlib
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from apps.chat_data_app.services.agent_run import conversation_workspace as conversation_workspace_module
from apps.chat_data_app.services.agent_run import (
    workspace_local_backend as workspace_local_backend_module,
)
from apps.chat_data_app.services.agent_run import (
    workspace_sandbox_backend as workspace_sandbox_backend_module,
)
from utils import tool_result_store
from apps.chat_data_app.agent.data_agent import DatasourceContext, assemble_tools
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.agent.tools.dsl2sql import normalize_dsl_payload
from apps.chat_data_app.agent.workspace.workspace_execution import _artifact_payload_from_bytes
from apps.chat_data_app.services.semantic.run import semantic_query_service

from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    create_workspace_backend,
    _render_local_office_html,
    ensure_conversation_workspace,
    list_workspace_files,
    read_workspace_text_file,
    resolve_workspace_path,
    to_visible_path,
    workspace_runtime_from_state,
)
from apps.chat_data_app.agent.workspace.workspace_special_services import (
    WorkspaceOfficePreviewService,
    stabilize_office_preview_html,
)

sql_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.semantic_query")
present_artifacts_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.present_artifacts")
filesystem_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.filesystem")


def test_workspace_runtime_from_state_rebuilds_runtime_context(tmp_path):
    runtime = workspace_runtime_from_state({
        "user_id": "u1",
        "workspace": {
            "workspace_root": str(tmp_path),
            "workspace_backend": "local",
        },
    })

    assert runtime.workspace_root == str(tmp_path)
    assert runtime.user_id == "u1"
    assert runtime.backend_name == "local"


def test_workspace_backend_factory_fails_for_unknown_backend():
    with pytest.raises(ValueError, match="不支持的工作区后端"):
        create_workspace_backend("unknown-backend")


@pytest.mark.asyncio
async def test_sandbox_backend_reuses_initialization_error_within_runtime(tmp_path, monkeypatch):
    attempts = 0

    async def no_op():
        return None

    async def fail_to_create_session(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("OpenSandbox 服务不可达")

    backend = conversation_workspace_module.SandboxWorkspaceBackend()
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(tmp_path),
        user_id="u1",
        backend=backend,
    )
    monkeypatch.setattr(runtime, "_ensure_snapshot_async", no_op)
    monkeypatch.setattr(
        workspace_sandbox_backend_module,
        "get_workspace_execution_session",
        fail_to_create_session,
    )

    with pytest.raises(ValueError, match="OpenSandbox 服务不可达"):
        await backend._session_ref(runtime)
    with pytest.raises(ValueError, match="OpenSandbox 服务不可达"):
        await backend._session_ref(runtime)

    assert attempts == 1


@pytest.mark.asyncio
async def test_runtime_rejects_execution_for_file_only_backend(tmp_path):
    class FileOnlyBackend:
        name = "file-only"

        async def read_text_file(self, *_args, **_kwargs):
            return {}

        async def write_text_file(self, *_args, **_kwargs):
            return {}

        async def replace_text_file(self, *_args, **_kwargs):
            return {}

        async def list_dir(self, *_args, **_kwargs):
            return []

        async def create_dir(self, *_args, **_kwargs):
            return {}

        async def delete_path(self, *_args, **_kwargs):
            return {}

        async def move_path(self, *_args, **_kwargs):
            return {}

        async def copy_path(self, *_args, **_kwargs):
            return {}

        async def search_files(self, *_args, **_kwargs):
            return []

        async def grep_text(self, *_args, **_kwargs):
            return {}

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(tmp_path),
        user_id="u1",
        backend=FileOnlyBackend(),
    )
    with pytest.raises(ValueError, match="不支持命令执行"):
        await runtime.run_bash(command="pwd", cwd="workspace", timeout_seconds=1)
    with pytest.raises(ValueError, match="不支持语义模型物料化"):
        await runtime.write_internal_text_files([("workspace/semantic/model.yaml", "model")])
    with pytest.raises(ValueError, match="不支持隐藏工具结果存储"):
        await runtime.write_tool_result(
            file_path="outputs/.tool-offload/result.txt",
            content="result",
        )
    with pytest.raises(ValueError, match="不支持产物读取"):
        await runtime.artifact_payload(file_path="outputs/report.txt")
    with pytest.raises(ValueError, match="不支持Office 预览"):
        await runtime.render_office_html(file_path="outputs/report.docx", max_chars=100)


def _create_local_workspace_tools(_monkeypatch, paths, **kwargs):
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="system",
        backend="local",
    )
    return {
        tool.name: tool
        for tool in assemble_tools(
            AgentRuntimeOptions(),
            runtime,
            datasource_context=DatasourceContext(datasource_id=kwargs.pop("datasource_id", None)),
            **kwargs,
        )
    }


def _analysis_query_state(
    paths,
    *,
    datasource_id: str | None = "ds_1",
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
        "user_id": "system",
        "conversation_id": conversation_id,
        "workspace": {"workspace_root": paths["root"], "workspace_backend": "local"},
        "messages": [HumanMessage(content=user_query)],
    }


def test_ensure_conversation_workspace_creates_expected_dirs():
    paths = ensure_conversation_workspace("conv_workspace_test", "user_a")

    assert Path(paths["root"]).exists()
    assert Path(paths["root"]).as_posix().endswith("threads/user_a/conv_workspace_test/user-data")
    assert Path(paths["workspace"]).exists()
    assert Path(paths["outputs"]).exists()
    assert Path(paths["uploads"]).exists()
    assert (Path(paths["outputs"]) / ".tool-offload").exists()
    # 运行域技能快照目录随工作区一并创建（扁平 /mnt/skills/<name> 快照），但不进入 paths 字典
    assert (Path(paths["root"]).parent / "runtime-skills").is_dir()
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
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "outputs/.tool-offload/private.json", writable=True)
    with pytest.raises(ValueError, match="语义模型只读快照"):
        resolve_workspace_path(paths["root"], "workspace/semantic/model.yaml", writable=True)
    snapshot = resolve_workspace_path(
        paths["root"],
        "workspace/semantic/model.yaml",
        writable=True,
        allow_managed_semantic_model_write=True,
    )
    assert snapshot.name == "model.yaml"


@pytest.mark.asyncio
async def test_internal_semantic_model_write_overwrites_only_selected_file(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation_workspace_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(conversation_workspace_module, "THREADS_DIR", tmp_path / "threads")
    paths = ensure_conversation_workspace("conv_semantic_replace", "user_a")
    semantic_root = Path(paths["workspace"]) / "semantic"
    semantic_root.mkdir()
    (semantic_root / "old.yaml").write_text("old", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"], user_id="user_a", backend="local"
    )

    result = await runtime.write_internal_text_files([
        ("workspace/semantic/new.yaml", "new"),
    ])

    assert (semantic_root / "old.yaml").read_text(encoding="utf-8") == "old"
    assert (semantic_root / "new.yaml").read_text(encoding="utf-8") == "new"
    assert result[0]["file_path"] == "/workspace/semantic/new.yaml"


@pytest.mark.asyncio
async def test_tool_results_are_hidden_from_workspace_api_but_readable_by_file_read(monkeypatch, tmp_path):
    monkeypatch.setattr(conversation_workspace_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(conversation_workspace_module, "THREADS_DIR", tmp_path / "users")
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    paths = ensure_conversation_workspace("conv_private", "user_a")
    private_file = Path(paths["outputs"]) / ".tool-offload" / "result_1.json"
    private_file.write_text('{"rows":[1]}', encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    assert all(item["name"] != "result_1.json" for item in list_workspace_files("conv_private", "user_a"))
    with pytest.raises(ValueError):
        read_workspace_text_file("conv_private", "outputs/.tool-offload/result_1.json", user_id="user_a")
    result = await runtime.read_text_file(
        file_path="/mnt/user-data/outputs/.tool-offload/result_1.json",
        offset=0,
        limit=10,
        max_chars=100,
    )
    assert result["lines"] == ['{"rows":[1]}']
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "outputs/.tool-offload/result_1.json", writable=True)


@pytest.mark.asyncio
async def test_query_result_is_hidden_read_only_and_reusable_by_file_read(monkeypatch, tmp_path):
    monkeypatch.setattr(conversation_workspace_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(conversation_workspace_module, "THREADS_DIR", tmp_path / "users")
    paths = ensure_conversation_workspace("conv_query_result", "user_a")
    result_file = Path(paths["outputs"]) / "query_result" / "result_1.json"
    result_file.parent.mkdir()
    result_file.write_text('{\n  "rows": [1, 2]\n}', encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"], user_id="user_a", backend="local",
    )
    file_read = next(
        tool for tool in filesystem_tool_module.create_filesystem_tools(runtime)
        if tool.name == "file_read"
    )

    assert all(
        item["name"] != "result_1.json"
        for item in list_workspace_files("conv_query_result", "user_a")
    )
    content = await file_read.ainvoke({
        "file_path": "/mnt/user-data/outputs/query_result/result_1.json",
        "offset": 0,
        "limit": 2,
    })

    assert "total_lines=3" in content
    assert "     1 {" in content
    assert "has_more=true" in content
    with pytest.raises(ValueError, match="系统结果目录"):
        resolve_workspace_path(
            paths["root"], "outputs/query_result/result_1.json", writable=True,
        )


def test_resolve_workspace_path_accepts_short_absolute_aliases():
    paths = ensure_conversation_workspace(f"conv_workspace_alias_{uuid.uuid4().hex}")

    workspace_file = resolve_workspace_path(paths["root"], "/workspace/demo.json", writable=True)
    outputs_file = resolve_workspace_path(paths["root"], "/outputs/sales.chart.json", writable=True)

    assert workspace_file == Path(paths["workspace"]) / "demo.json"
    assert outputs_file == Path(paths["outputs"]) / "sales.chart.json"


@pytest.mark.asyncio
async def test_file_copy_copies_upload_into_workspace_and_keeps_source(monkeypatch, tmp_path):
    monkeypatch.setattr(conversation_workspace_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(conversation_workspace_module, "THREADS_DIR", tmp_path / "users")
    paths = ensure_conversation_workspace("conv_file_copy", "user_a")
    source = Path(paths["uploads"]) / "原文件.docx"
    source.write_bytes(b"\x00\x01docx-bytes")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    payload = await runtime.copy_path(
        source_path="/mnt/user-data/uploads/原文件.docx",
        destination_path="/mnt/user-data/workspace/修订稿.docx",
    )

    destination = Path(paths["workspace"]) / "修订稿.docx"
    assert payload["type"] == "file"
    assert payload["bytes"] == len(b"\x00\x01docx-bytes")
    assert destination.read_bytes() == b"\x00\x01docx-bytes"
    assert source.read_bytes() == b"\x00\x01docx-bytes"


@pytest.mark.asyncio
async def test_file_copy_rejects_invalid_source_and_destination(monkeypatch, tmp_path):
    monkeypatch.setattr(conversation_workspace_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(conversation_workspace_module, "THREADS_DIR", tmp_path / "users")
    paths = ensure_conversation_workspace("conv_file_copy_guard", "user_a")
    source = Path(paths["uploads"]) / "a.bin"
    source.write_bytes(b"a")
    existing = Path(paths["workspace"]) / "b.bin"
    existing.write_bytes(b"b")
    offload = Path(paths["outputs"]) / ".tool-offload" / "r.json"
    offload.write_text("{}", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    with pytest.raises(ValueError, match="目标路径已存在"):
        await runtime.copy_path(source_path="uploads/a.bin", destination_path="workspace/b.bin")
    with pytest.raises(ValueError, match="只允许写入"):
        await runtime.copy_path(source_path="uploads/a.bin", destination_path="uploads/c.bin")
    with pytest.raises(ValueError, match="只支持复制单个文件"):
        await runtime.copy_path(source_path="workspace", destination_path="workspace/d.bin")
    with pytest.raises(ValueError, match="tool-offload"):
        await runtime.copy_path(source_path="outputs/.tool-offload/r.json", destination_path="workspace/e.json")
    with pytest.raises(FileNotFoundError):
        await runtime.copy_path(source_path="uploads/missing.bin", destination_path="workspace/f.bin")


@pytest.mark.asyncio
async def test_file_copy_accepts_readonly_skill_source_and_keeps_skill_immutable(tmp_path):
    """/mnt/skills 只读技能文件可作为 file_copy 源，且技能路径不能作为复制目标。"""
    workspace_root = tmp_path / "threads" / "u1" / "conv_skill_copy" / "user-data"
    (workspace_root / "workspace").mkdir(parents=True)
    skill_dir = workspace_root.parent / "runtime-skills" / "demo-skill"
    template = skill_dir / "assets" / "template.pptx"
    template.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# demo", encoding="utf-8")
    template.write_bytes(b"\x00pptx-template")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root), user_id="u1", backend="local",
    )

    payload = await runtime.copy_path(
        source_path="/mnt/skills/demo-skill/assets/template.pptx",
        destination_path="/mnt/user-data/workspace/汇报.pptx",
    )

    assert payload["source_path"] == "/mnt/skills/demo-skill/assets/template.pptx"
    assert payload["destination_path"] == "/workspace/汇报.pptx"
    assert payload["type"] == "file"
    assert payload["bytes"] == len(b"\x00pptx-template")
    assert (workspace_root / "workspace" / "汇报.pptx").read_bytes() == b"\x00pptx-template"
    # 只读技能源文件不得被移动或改动
    assert template.read_bytes() == b"\x00pptx-template"

    # 技能路径只读，不能作为复制目标
    with pytest.raises(ValueError):
        await runtime.copy_path(
            source_path="/mnt/user-data/workspace/汇报.pptx",
            destination_path="/mnt/skills/demo-skill/assets/hacked.pptx",
        )


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
    (Path(paths["outputs"]) / ".tool-offload").rmdir()
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
async def test_runtime_resolves_system_skill_file_under_system_path(tmp_path):
    # 运行域技能统一为扁平 /mnt/skills/<name>：直接从本会话快照目录解析，
    # 不再按类别/远程解析 skill_service.resolve_skill_path_async。
    workspace_root = tmp_path / "threads" / "u1" / "conv_x" / "user-data"
    workspace_root.mkdir(parents=True)
    skill_dir = workspace_root.parent / "runtime-skills" / "skill-creator"
    script_path = skill_dir / "scripts" / "quick_validate.py"
    script_path.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: skill-creator\n---\n# Skill Creator", encoding="utf-8")
    script_path.write_text("print('ok')\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="local",
    )
    # 快照目录已存在则 _ensure_snapshot_async 直接复用，不触发远程构建
    assert (runtime.runtime_skills_dir / "skill-creator").is_dir()

    target, visible_path = await runtime.resolve_readonly_skill_path_async(
        "/mnt/skills/skill-creator/scripts/quick_validate.py"
    )

    assert target == script_path
    assert visible_path == "/mnt/skills/skill-creator/scripts/quick_validate.py"

    # 快照中不存在的技能名 → 无权限/不存在；隐藏名 → 路径无效
    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        await runtime.resolve_readonly_skill_path_async(
            "/mnt/skills/unknown/scripts/quick_validate.py"
        )
    with pytest.raises(ValueError, match="技能路径无效"):
        await runtime.resolve_readonly_skill_path_async(
            "/mnt/skills/.evil/scripts/quick_validate.py"
        )


@pytest.mark.asyncio
async def test_runtime_rejects_symlinked_skill_directory(tmp_path):
    workspace_root = tmp_path / "threads" / "u1" / "conv_x" / "user-data"
    workspace_root.mkdir(parents=True)
    skill_root = workspace_root.parent / "runtime-skills"
    valid_skill = skill_root / "skill-creator"
    valid_skill.mkdir(parents=True)
    (valid_skill / "SKILL.md").write_text("# Skill Creator", encoding="utf-8")
    outside_skill = tmp_path / "outside-skill"
    outside_skill.mkdir()
    (outside_skill / "SKILL.md").write_text("# Outside", encoding="utf-8")
    (skill_root / "evil-skill").symlink_to(outside_skill, target_is_directory=True)
    runtime = ConversationWorkspaceRuntime(workspace_root=str(workspace_root), user_id="u1")

    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        await runtime.resolve_readonly_skill_path_async("/mnt/skills/evil-skill/SKILL.md")
    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        await runtime.list_dir(path="/mnt/skills/evil-skill", max_depth=1)


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
async def test_file_write_accepts_structured_content(monkeypatch):
    paths = ensure_conversation_workspace("conv_workspace_file_edit")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "workspace/plan.json",
        "content": {"cards": [{"title": "风险总览"}]},
    })

    assert "[file_write]" in result
    assert '"风险总览"' in (Path(paths["workspace"]) / "plan.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_file_edit_requires_unique_existing_content(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_edit_unique_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "report.txt"
    target.write_text("title=old\nvalue=old\n", encoding="utf-8")

    duplicate = await tools["file_edit"].ainvoke({
        "file_path": "workspace/report.txt",
        "old_content": "old",
        "new_content": "new",
    })
    success = await tools["file_edit"].ainvoke({
        "file_path": "workspace/report.txt",
        "old_content": "title=old",
        "new_content": "title=new",
    })
    missing = await tools["file_edit"].ainvoke({
        "file_path": "workspace/report.txt",
        "old_content": "missing",
        "new_content": "new",
    })

    assert "匹配多处" in duplicate
    assert "[file_edit]" in success
    assert target.read_text(encoding="utf-8") == "title=new\nvalue=old\n"
    assert "未在文件中找到" in missing


@pytest.mark.asyncio
async def test_file_write_and_edit_reject_uploads(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_write_uploads_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    write_result = await tools["file_write"].ainvoke({"file_path": "uploads/a.txt", "content": "x"})
    edit_result = await tools["file_edit"].ainvoke({
        "file_path": "uploads/a.txt",
        "old_content": "x",
        "new_content": "y",
    })

    assert write_result.startswith("file_write 工具错误: 只允许写入 workspace/ 或 outputs/")
    assert edit_result.startswith("file_edit 工具错误: 只允许写入 workspace/ 或 outputs/")


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
    assert payload["artifacts"][0]["renderer"] == "chart"
    assert "artifact_payload" not in payload["artifacts"][0]
    assert "chart_spec" not in payload["artifacts"][0]


@pytest.mark.asyncio
async def test_present_artifacts_returns_office_document(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_docx_{uuid.uuid4().hex}")
    document_path = Path(paths["outputs"]) / "公文报告.docx"
    document_path.write_bytes(b"PK\x03\x04fake-docx")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({
        "files": ["/mnt/user-data/outputs/公文报告.docx"],
    })

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["errors"] == []
    assert payload["artifacts"] == [{
        "file_path": "/outputs/公文报告.docx",
        "artifact_type": "document",
        "renderer": "document",
        "artifact_version": "v1",
        "name": "公文报告",
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }]


@pytest.mark.asyncio
async def test_present_artifacts_returns_markdown_document(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_markdown_{uuid.uuid4().hex}")
    markdown_path = Path(paths["outputs"]) / "语义模型分析报告.md"
    markdown_path.write_text("# 语义模型分析报告\n", encoding="utf-8")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({
        "files": ["/mnt/user-data/outputs/语义模型分析报告.md"],
    })

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["errors"] == []
    assert payload["artifacts"] == [{
        "file_path": "/outputs/语义模型分析报告.md",
        "artifact_type": "markdown",
        "renderer": "markdown",
        "artifact_version": "v1",
        "name": "语义模型分析报告",
        "mime_type": "text/markdown",
    }]


@pytest.mark.asyncio
async def test_present_artifacts_returns_valid_output_skill(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_skill_{uuid.uuid4().hex}")
    skill_dir = Path(paths["outputs"]) / "sales-analysis"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: sales-analysis\n"
        "title: 销售分析\n"
        "description: 汇总并分析销售表现。\n"
        "---\n\n"
        "# 销售分析\n",
        encoding="utf-8",
    )
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({
        "files": ["/mnt/user-data/outputs/sales-analysis/SKILL.md"],
    })

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["errors"] == []
    assert payload["artifacts"] == [{
        "file_path": "/outputs/sales-analysis/SKILL.md",
        "artifact_type": "skill",
        "renderer": "skill",
        "artifact_version": "v1",
        "skill_title": "销售分析",
        "description": "汇总并分析销售表现。",
        "name": "sales-analysis",
        "mime_type": "text/markdown",
    }]


@pytest.mark.asyncio
async def test_present_artifacts_rejects_invalid_output_skill(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_bad_skill_{uuid.uuid4().hex}")
    bad_name_dir = Path(paths["outputs"]) / "sales-analysis"
    nested_dir = Path(paths["outputs"]) / "nested" / "sales-analysis"
    for skill_dir, name in ((bad_name_dir, "other-skill"), (nested_dir, "sales-analysis")):
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n# Skill\n",
            encoding="utf-8",
        )
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({
        "files": [
            "outputs/sales-analysis/SKILL.md",
            "outputs/nested/sales-analysis/SKILL.md",
        ],
    })

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 0
    assert [item["error"] for item in payload["errors"]] == [
        "文件不是可展示的 outputs 产物",
        "文件不是可展示的 outputs 产物",
    ]


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
async def test_present_artifacts_rejects_hidden_tool_result(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_private_{uuid.uuid4().hex}")
    private_file = Path(paths["outputs"]) / ".tool-offload" / "result_1.json"
    private_file.write_text('{"rows":[1]}', encoding="utf-8")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({
        "files": ["outputs/.tool-offload/result_1.json"],
    })

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 0
    assert payload["artifacts"] == []
    assert payload["errors"] == [{
        "file_path": "outputs/.tool-offload/result_1.json",
        "error": "文件不是可展示的 outputs 产物",
    }]


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
async def test_file_read_default_limit_is_200(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "long.txt"
    target.write_text("\n".join(f"line {index}" for index in range(1, 602)), encoding="utf-8")

    result = await tools["file_read"].ainvoke({"file_path": "workspace/long.txt"})

    assert "[file_read]" in result
    assert "limit=200" in result
    assert "end_line=200" in result
    assert "total_lines=601" in result
    assert "has_more=true" in result
    assert "next_offset=200" in result


@pytest.mark.asyncio
async def test_file_read_keeps_content_and_reports_unknown_when_local_line_count_fails(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_count_failure_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "report.txt"
    target.write_text("one\ntwo", encoding="utf-8")

    def fail_line_count(_target):
        raise OSError("awk unavailable")

    monkeypatch.setattr(workspace_local_backend_module, "_count_local_text_lines", fail_line_count)

    result = await tools["file_read"].ainvoke({"file_path": "workspace/report.txt"})

    assert "total_lines=unknown" in result
    assert "     1 one" in result
    assert "     2 two" in result
    assert "file_read 执行失败" not in result


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
    assert await tools["file_read"].ainvoke({"file_path": "workspace/demo.txt", "limit": 0}) == "limit 必须在 1-500 之间"
    assert await tools["file_read"].ainvoke({"file_path": "workspace/demo.txt", "limit": 501}) == "limit 必须在 1-500 之间"


@pytest.mark.asyncio
async def test_file_read_truncates_long_lines_without_char_continuation(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_budget_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "rows.jsonl"
    target.write_text("a" * 600 + "\n" + "b" * 600 + "\nnext", encoding="utf-8")
    monkeypatch.setattr(filesystem_tool_module, "FILE_READ_MAX_CHARS", 1000)

    first_page = await tools["file_read"].ainvoke({
        "file_path": "workspace/rows.jsonl",
        "limit": 3,
    })

    assert "truncated=true" in first_page
    assert "truncated_lines=1,2" in first_page
    assert "char_offset" not in first_page
    assert f"     1 {'a' * 500}" in first_page
    assert f"     2 {'b' * 499}" in first_page
    assert "next_offset=2" in first_page

    next_page = await tools["file_read"].ainvoke({
        "file_path": "workspace/rows.jsonl",
        "offset": 2,
    })

    assert "     3 next" in next_page
    assert "truncated=true" not in next_page


@pytest.mark.asyncio
async def test_file_read_delegates_to_runtime_backend():
    class RecordingBackend:
        name = "recording"

        async def read_text_file(self, _runtime, **kwargs):
            assert kwargs == {
                "file_path": "workspace/report.txt", "offset": 0, "limit": 200, "max_chars": 25000,
            }
            return {
                "exists": True,
                "is_dir": False,
                "is_binary": False,
                "path": "/mnt/user-data/workspace/report.txt",
                "lines": ["backend result"],
                "total_lines": 1,
                "start_line": 1,
                "end_line": 1,
                "has_more": False,
                "truncated_lines": [],
            }

        async def write_text_file(self, *_args, **_kwargs):
            raise AssertionError("unexpected write")

        async def write_internal_text_files(self, *_args, **_kwargs):
            raise AssertionError("unexpected internal semantic model write")

        async def write_tool_result(self, *_args, **_kwargs):
            raise AssertionError("unexpected tool result")

        async def replace_text_file(self, *_args, **_kwargs):
            raise AssertionError("unexpected replace")

        async def artifact_payload(self, *_args, **_kwargs):
            raise AssertionError("unexpected artifact")

        async def render_office_html(self, *_args, **_kwargs):
            raise AssertionError("unexpected office preview")

        async def list_dir(self, *_args, **_kwargs):
            raise AssertionError("unexpected list")

        async def validate_bash_paths(self, *_args, **_kwargs):
            raise AssertionError("unexpected bash validation")

        async def run_bash(self, *_args, **_kwargs):
            raise AssertionError("unexpected bash run")

        async def run_officecli(self, *_args, **_kwargs):
            raise AssertionError("unexpected officecli run")

        async def create_dir(self, *_args, **_kwargs):
            raise AssertionError("unexpected create dir")

        async def delete_path(self, *_args, **_kwargs):
            raise AssertionError("unexpected delete")

        async def move_path(self, *_args, **_kwargs):
            raise AssertionError("unexpected move")

        async def copy_path(self, *_args, **_kwargs):
            raise AssertionError("unexpected copy")

        async def search_files(self, *_args, **_kwargs):
            raise AssertionError("unexpected search")

        async def grep_text(self, *_args, **_kwargs):
            raise AssertionError("unexpected grep")

    runtime = ConversationWorkspaceRuntime(
        workspace_root="/unused",
        user_id="u1",
        backend=RecordingBackend(),
    )
    tool = next(tool for tool in filesystem_tool_module.create_filesystem_tools(runtime) if tool.name == "file_read")

    result = await tool.ainvoke({"file_path": "workspace/report.txt"})

    assert "backend result" in result


def test_text_page_content_body_respects_budget_and_preserves_source_order(tmp_path):
    target = tmp_path / "rows.jsonl"
    target.write_text("ab\nabcdefghij\nxyz\nnext", encoding="utf-8")

    result = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/workspace/rows.jsonl",
        offset=0,
        limit=3,
        max_chars=1000,
    )

    assert result["lines"] == ["ab", "abcdefghij", "xyz"]
    assert len("\n".join(result["lines"])) == 17
    assert result["truncated_lines"] == []
    assert result["total_lines"] == 4
    assert result["has_more_lines"] is True


def test_text_page_caps_each_line_and_total_body_chars(tmp_path):
    target = tmp_path / "rows.jsonl"
    target.write_text("a" * 600 + "\n" + "b" * 600 + "\nnext", encoding="utf-8")

    result = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/workspace/rows.jsonl",
        offset=0,
        limit=3,
        max_chars=1000,
    )

    assert result["lines"] == ["a" * 500, "b" * 499]
    assert len("\n".join(result["lines"])) == 1000
    assert result["truncated_lines"] == [
        {"line_number": 1, "original_chars": 600, "returned_chars": 500},
        {"line_number": 2, "original_chars": 600, "returned_chars": 499},
    ]
    assert result["next_offset"] == 2


@pytest.mark.parametrize("separator", ["\n", "\r\n"])
def test_text_page_uses_standard_physical_line_semantics(tmp_path, separator):
    target = tmp_path / "separators.txt"
    content = separator.join(["first", "second", "third"])
    target.write_text(content, encoding="utf-8", newline="")

    result = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/workspace/separators.txt",
        offset=1,
        limit=1,
        max_chars=100,
    )

    assert result["lines"] == content.splitlines()[1:2]
    assert result["start_line"] == 2
    assert result["end_line"] == 2
    assert result["has_more_lines"] is True


def test_text_page_does_not_treat_control_characters_as_physical_lines(tmp_path):
    target = tmp_path / "control-separator.txt"
    target.write_text("first\u2028second\vthird", encoding="utf-8")

    result = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/workspace/control-separator.txt",
        offset=0,
        limit=10,
        max_chars=100,
    )

    assert result["lines"] == ["first\u2028second\vthird"]
    assert result["total_lines"] == 1


def test_local_text_line_count_is_cached_and_uses_fixed_argv(monkeypatch, tmp_path):
    target = tmp_path / "report with spaces.txt"
    target.write_text("first\nsecond\nthird", encoding="utf-8")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        line_count = target.read_text(encoding="utf-8").count("\n") + 1
        return SimpleNamespace(returncode=0, stdout=f"{line_count}\n", stderr="")

    workspace_local_backend_module._count_local_text_lines_cached.cache_clear()
    monkeypatch.setattr(conversation_workspace_module.subprocess, "run", fake_run)

    first = workspace_local_backend_module._read_text_page_from_path(
        target, "/workspace/report with spaces.txt", offset=0, limit=1, max_chars=100,
    )
    second = workspace_local_backend_module._read_text_page_from_path(
        target, "/workspace/report with spaces.txt", offset=1, limit=1, max_chars=100,
    )

    assert first["total_lines"] == second["total_lines"] == 3
    assert len(calls) == 1
    assert calls[0][0][1:] == ["END { print NR }", str(target)]
    assert calls[0][1]["timeout"] == 30
    assert calls[0][1]["check"] is False

    target.write_text("first\nsecond\nthird\nfourth", encoding="utf-8")
    third = workspace_local_backend_module._read_text_page_from_path(
        target, "/workspace/report with spaces.txt", offset=2, limit=1, max_chars=100,
    )

    assert third["total_lines"] == 4
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_file_read_rejects_workspace_root_outside_chat_data_home(monkeypatch, tmp_path):
    workspace_root = tmp_path / "outside" / "user-data"
    (workspace_root / "workspace").mkdir(parents=True)
    (workspace_root / "outputs").mkdir()
    (workspace_root / "uploads").mkdir()
    (workspace_root / "workspace" / "secret.txt").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(conversation_workspace_module, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="sandbox",
    )
    tool = next(tool for tool in filesystem_tool_module.create_filesystem_tools(runtime) if tool.name == "file_read")

    result = await tool.ainvoke({"file_path": "workspace/secret.txt"})

    assert result.startswith("file_read 工具错误: 会话工作区不在 CHAT_DATA_HOME 内")


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

    assert result.startswith("file_read 工具错误: 路径越界")


@pytest.mark.asyncio
async def test_file_glob_recursively_matches_filenames_and_includes_tool_results(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_glob_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    (Path(paths["workspace"]) / "reports" / "daily").mkdir(parents=True)
    (Path(paths["workspace"]) / "reports" / "daily" / "sales.json").write_text("{}", encoding="utf-8")
    (Path(paths["workspace"]) / "summary.json").write_text("{}", encoding="utf-8")
    hidden = Path(paths["outputs"]) / ".tool-offload" / "query.json"
    hidden.write_text("{}", encoding="utf-8")

    result = await tools["file_glob"].ainvoke({
        "path": "workspace",
        "pattern": "*.json",
    })

    payload = json.loads(result.replace("[file_glob]\n", "", 1))
    assert payload["entries"] == [
        {"path": "/workspace/reports/daily/sales.json", "type": "file"},
        {"path": "/workspace/summary.json", "type": "file"},
    ]

    offload_result = await tools["file_glob"].ainvoke({
        "path": "outputs",
        "pattern": "*.json",
    })
    offload_payload = json.loads(offload_result.replace("[file_glob]\n", "", 1))
    assert {"path": "/outputs/.tool-offload/query.json", "type": "file"} in offload_payload["entries"]


@pytest.mark.asyncio
async def test_file_glob_rejects_path_patterns(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_glob_pattern_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_glob"].ainvoke({"pattern": "**/*.json"})

    assert result.startswith("file_glob 工具错误: pattern 只能匹配文件名")


@pytest.mark.asyncio
async def test_file_write_returns_only_file_change_payload(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_dashboard_draft_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "workspace/dashboard_draft.json",
        "content": {"dashboard_name": "草稿看板", "cards": [{"title": "销售额"}]},
    })

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/workspace/dashboard_draft.json"
    assert payload["bytes"] > 0
    assert payload["operation"] == "write"
    assert payload["mode"] == "overwrite"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_does_not_classify_output_artifact(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "outputs/monthly_simple_bar.chart.json",
        "content": {
            "version": "1.0",
            "title": "销售额趋势",
            "chart_type": "line",
            "data": [{"month": "2026-01", "sales": 120}],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/outputs/monthly_simple_bar.chart.json"
    assert payload["bytes"] > 0
    assert payload["operation"] == "write"
    assert payload["mode"] == "overwrite"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_workspace_chart_spec_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_chart_draft_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "workspace/chart_spec_sales_trend_draft.json",
        "content": {
            "version": "1.0",
            "title": "销售额趋势",
            "chart_type": "line",
            "data": [{"month": "2026-01", "sales": 120}],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/workspace/chart_spec_sales_trend_draft.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_non_chart_json_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_non_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "outputs/query_result.json",
        "content": {"rows": [{"month": "2026-01", "sales": 120}]},
    })

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/outputs/query_result.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_empty_line_chart_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_empty_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "outputs/empty_chart.json",
        "content": {
            "version": "1.0",
            "title": "空趋势图",
            "chart_type": "line",
            "data": [],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/outputs/empty_chart.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_invalid_chart_spec_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_invalid_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "outputs/invalid_chart.json",
        "content": {
            "chart_type": "line",
            "data": [{"month": "2026-01", "sales": 120}],
            "encoding": {"x": "month", "y": ["sales"]},
        },
    })

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/outputs/invalid_chart.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_chart_spec_with_missing_encoding_field_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_invalid_chart_encoding_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke({
        "file_path": "outputs/invalid_chart_encoding.json",
        "content": {
            "version": "1.0",
            "title": "同比趋势",
            "chart_type": "line",
            "data": [{"period": "2024-01", "sales_current": 87, "sales_previous": 71}],
            "encoding": {"x": "period", "y": ["current", "previous"]},
        },
    })

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/outputs/invalid_chart_encoding.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_does_not_classify_output_artifact(monkeypatch):
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
    result = await tools["file_edit"].ainvoke({
        "file_path": "outputs/dashboard.dashboard.json",
        "old_content": '  "dashboard_name": "旧看板",',
        "new_content": '  "dashboard_name": "新看板",',
    })

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/outputs/dashboard.dashboard.json"
    assert payload["operation"] == "edit"
    assert payload["replaced_count"] == 1
    assert payload["bytes"] > 0
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_rejects_uploads_write(monkeypatch):
    paths = ensure_conversation_workspace("conv_workspace_file_edit_uploads")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["uploads"]) / "demo.txt"
    target.write_text("old", encoding="utf-8")

    result = await tools["file_edit"].ainvoke({
        "file_path": "uploads/demo.txt",
        "old_content": "old",
        "new_content": "new",
    })

    assert "file_edit 工具错误" in result


@pytest.mark.asyncio
async def test_semantic_query_tool_returns_internal_result_envelope(monkeypatch):
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
    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
    )
    state = _analysis_query_state(paths, user_query="统计销售额")

    result = await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": input_dsl,
        "state": state,
    })

    payload = json.loads(result)
    assert payload["type"] == "chatdata_internal_tool_result"
    assert payload["kind"] == "query_result"
    assert payload["payload"]["results"][0]["data"][0]["sales"] == 120
    assert payload["meta"] == {
        "query_mode": "dsl",
        "confidence": 0.9,
    }


@pytest.mark.asyncio
async def test_semantic_query_tool_keeps_full_result(monkeypatch):
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
    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
    )
    state = _analysis_query_state(paths, user_query="查询订单数")

    result = await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": input_dsl,
        "state": state,
    })

    payload = json.loads(result)
    assert payload["type"] == "chatdata_internal_tool_result"
    assert payload["payload"]["results"][0]["data"] == [{"cnt": 3}]
    assert payload["payload"]["sql"] == "select count(*) cnt from t"


@pytest.mark.asyncio
async def test_semantic_query_tool_leaves_large_result_for_middleware(monkeypatch):
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

    monkeypatch.setattr(sql_tool_module, "load_runtime_semantic_config", fake_get_config)
    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
    )
    state = _analysis_query_state(paths, conversation_id="conv_large")

    result = await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"metrics": ["内容"]},
        "state": state,
    })

    payload = json.loads(result)
    assert payload["type"] == "chatdata_internal_tool_result"
    assert payload["payload"]["results"][0]["data"][0]["content"] == "x" * 200


@pytest.mark.asyncio
async def test_semantic_query_tool_rejects_missing_datasource_and_invalid_requests(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_reject_{uuid.uuid4().hex}")
    tools_without_datasource = _create_local_workspace_tools(monkeypatch, paths)
    state_without_datasource = _analysis_query_state(paths, datasource_id=None)

    assert await tools_without_datasource["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"metrics": ["订单数"]},
        "state": state_without_datasource,
    }) == "semantic_query_tool 执行失败: 当前会话未绑定数据源"

    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
    )
    state = _analysis_query_state(paths)
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
    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
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
    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)
    monkeypatch.setattr(sql_tool_module, "_execute_sql_real", fake_execute_sql_real)
    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
    )
    state = _analysis_query_state(paths)

    assert await tools["semantic_query_tool"].ainvoke({
        "semantic_config_id": "sc_1",
        "member_datasource_ids": ["m1"],
        "dsl": {"metrics": ["订单数"]},
        "state": state,
    }) == "semantic_query_tool 执行失败: 执行失败"


@pytest.mark.asyncio
async def test_file_read_reads_session_skill_snapshot(tmp_path):
    """技能正文只能从当前会话的 runtime-skills 快照读取。"""
    workspace_root = tmp_path / "threads" / "u1" / "conv_a" / "user-data"
    workspace_root.mkdir(parents=True)
    snapshot_skill = workspace_root.parent / "runtime-skills" / "demo-skill"
    snapshot_skill.mkdir(parents=True)
    (snapshot_skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ntitle: 快照版\ndescription: 来自快照\n---\n# 快照版正文\n",
        encoding="utf-8",
    )
    (snapshot_skill / "references" / "guide.md").parent.mkdir(parents=True)
    (snapshot_skill / "references" / "guide.md").write_text("# guide from snapshot\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root), user_id="u1", backend="local",
    )
    tool = {item.name: item for item in filesystem_tool_module.create_filesystem_tools(runtime)}["file_read"]
    result = await tool.ainvoke({"file_path": "/mnt/skills/demo-skill/SKILL.md"})

    assert "file_path=/mnt/skills/demo-skill/SKILL.md" in result
    assert "# 快照版正文" in result


@pytest.mark.asyncio
async def test_file_read_rejects_skill_not_in_snapshot(tmp_path):
    workspace_root = tmp_path / "threads" / "u1" / "conv_b" / "user-data"
    workspace_root.mkdir(parents=True)
    # 预置另一个技能，使快照目录非空（_ensure_snapshot_async 直接复用、不触发管理域构建）；
    # 但不放入 demo-skill，模拟其被禁用/草稿而排除在快照外。
    other = workspace_root.parent / "runtime-skills" / "other-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: other-skill\ntitle: 其他\n---\n# 其他\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root), user_id="u1", backend="local",
    )
    tool = {item.name: item for item in filesystem_tool_module.create_filesystem_tools(runtime)}["file_read"]
    result = await tool.ainvoke({"file_path": "/mnt/skills/demo-skill/SKILL.md"})

    assert "技能不存在或无权限访问" in result


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_file_grep_single_file_returns_matches(monkeypatch, tmp_path):
    """file_grep on a single file must return matches, not silently drop them."""
    monkeypatch.setattr(conversation_workspace_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(conversation_workspace_module, "THREADS_DIR", tmp_path / "users")
    paths = ensure_conversation_workspace("conv_grep_file", "user_a")
    target = Path(paths["outputs"]) / "order.dashboard.json"
    target.write_text(
        json.dumps({"title": "订单核心指标", "metrics": ["订单数量", "销售额"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"], user_id="user_a", backend="local",
    )
    result = await runtime.grep_text(
        path="/mnt/user-data/outputs/order.dashboard.json",
        patterns=["订单数量", "销售额"],
        glob="**/*",
        max_results=200,
        max_chars=50000,
    )
    matches = result["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "/outputs/order.dashboard.json"
    assert "订单数量" in matches[0]["line"]
    assert "销售额" in matches[0]["line"]


@pytest.mark.asyncio
async def test_file_grep_single_skill_file_returns_visible_path(monkeypatch, tmp_path):
    """file_grep on a single skill file must return the /mnt/skills visible path."""
    workspace_root = tmp_path / "threads" / "u1" / "conv_grep_skill" / "user-data"
    workspace_root.mkdir(parents=True)
    skill_dir = workspace_root.parent / "runtime-skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    guide = skill_dir / "references" / "guide.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("# Demo\n关键词：订单数量\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root), user_id="u1", backend="local",
    )
    result = await runtime.grep_text(
        path="/mnt/skills/demo-skill/references/guide.md",
        patterns=["订单数量"],
        glob="**/*",
        max_results=200,
        max_chars=50000,
    )
    matches = result["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "/mnt/skills/demo-skill/references/guide.md"
    assert "订单数量" in matches[0]["line"]


def test_render_local_office_html_invokes_host_officecli(monkeypatch):
    workspace = ensure_conversation_workspace("conv_local_preview", "user_preview")
    root = Path(workspace["root"])
    docx = root / "outputs" / "report.docx"
    docx.write_text("PK\x03\x04 fake docx bytes", encoding="utf-8")

    calls = {}

    def fake_which(_name):
        return "/usr/local/bin/officecli"

    def fake_run(args, **kwargs):
        calls["args"] = list(args)
        preview_path = args[5]
        Path(preview_path).write_text("<main>local preview</main>", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(conversation_workspace_module.shutil, "which", fake_which)
    monkeypatch.setattr(conversation_workspace_module.subprocess, "run", fake_run)

    result = _render_local_office_html(str(root), "outputs/report.docx", 12 * 1024 * 1024)

    assert result["renderer"] == "officecli-html"
    assert result["html"] == "<main>local preview</main>"
    assert result["truncated"] is False
    assert calls["args"][0] == "/usr/local/bin/officecli"
    assert calls["args"][1:4] == ["view", str(docx), "html"]
    assert calls["args"][4] == "--out"


def test_render_local_office_html_missing_binary_raises(monkeypatch):
    workspace = ensure_conversation_workspace("conv_local_missing", "user_missing")
    root = Path(workspace["root"])
    docx = root / "outputs" / "report.docx"
    docx.write_text("PK fake", encoding="utf-8")

    monkeypatch.setattr(conversation_workspace_module.shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError, match="未安装 OfficeCLI"):
        _render_local_office_html(str(root), "outputs/report.docx", 1024)


def test_stabilize_office_preview_html_only_targets_word_pages():
    word_html = '<html><head></head><body><div class="page" data-page="1"></div></body></html>'
    word_result = stabilize_office_preview_html(word_html)

    assert "chatdata-office-preview-pending" in word_result
    assert "setInterval(reveal, 50)" in word_result
    assert word_result.count("chatdata-office-preview-bootstrap") == 1

    non_word_html = '<html><head></head><body><div class="slide"></div></body></html>'
    assert stabilize_office_preview_html(non_word_html) == non_word_html


@pytest.mark.asyncio
async def test_workspace_office_preview_service_stabilizes_and_rechecks_size():
    class PreviewBackend:
        async def render_office_html(self, _runtime, *, file_path, max_chars):
            assert file_path == "outputs/report.docx"
            assert max_chars in {100, 10000}
            return {
                "renderer": "officecli-html",
                "html": '<html><head></head><body><div class="page" data-page="1"></div></body></html>',
                "truncated": False,
            }

    service = WorkspaceOfficePreviewService()
    runtime = SimpleNamespace(backend=PreviewBackend())
    result = await service.render_html(
        runtime,
        file_path="outputs/report.docx",
        max_chars=10000,
    )
    assert "chatdata-office-preview-bootstrap" in result["html"]
    assert result["truncated"] is False

    oversized = await service.render_html(
        runtime,
        file_path="outputs/report.docx",
        max_chars=100,
    )
    assert oversized["html"] == ""
    assert oversized["truncated"] is True
