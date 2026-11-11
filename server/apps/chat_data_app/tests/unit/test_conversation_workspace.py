import importlib
import json
import os
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from apps.chat_data_app.agent.data_agent import DatasourceContext, assemble_tools
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.agent.tools.common import normalize_json_like_payload
from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module
from apps.chat_data_app.agent.workspace import (
    workspace_local_backend as workspace_local_backend_module,
)
from apps.chat_data_app.agent.workspace import (
    workspace_ops as we_ops,  # noqa: F401  # 测试按模块属性引用 workspace_ops，属有意重导出
)
from apps.chat_data_app.agent.workspace import (
    workspace_sandbox_api as we_api,  # noqa: F401  # 测试按模块属性引用 workspace_sandbox_api，属有意重导出
)
from apps.chat_data_app.agent.workspace import (
    workspace_sandbox_backend as workspace_sandbox_backend_module,
)
from apps.chat_data_app.agent.workspace import (
    workspace_sandbox_paths as we_paths,  # noqa: F401  # 测试按模块属性引用 workspace_sandbox_paths，属有意重导出
)
from apps.chat_data_app.agent.workspace import (
    workspace_search as we_search,  # noqa: F401  # 测试按模块属性引用 workspace_search，属有意重导出
)
from apps.chat_data_app.agent.workspace.runtime_skill_snapshot import runtime_skills_snapshot_dir_from_workspace
from apps.chat_data_app.agent.workspace.workspace_ops import _artifact_payload_from_bytes
from apps.chat_data_app.agent.workspace.workspace_special_services import (
    WorkspaceOfficePreviewService,
    stabilize_office_preview_html,
)
from apps.chat_data_app.services.agent_run import conversation_workspace as conversation_workspace_module
from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    _render_local_office_html,
    create_workspace_backend,
    ensure_conversation_workspace,
    list_workspace_directories,
    list_workspace_files,
    read_workspace_text_file,
    resolve_workspace_path,
    to_visible_path,
    workspace_runtime_from_state,
)
from apps.chat_data_app.services.semantic.run import semantic_query_service
from utils import storage_paths as storage_paths_module
from utils import tool_result_store
from utils.storage_paths import (
    conversation_semantic_dir_from_workspace,
    conversation_semantic_ref_path,
    semantic_cache_dir,
)

sql_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.data_analyzing")
present_artifacts_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.present_artifacts")
filesystem_tool_module = importlib.import_module("apps.chat_data_app.agent.tools.filesystem")
uploaded_file_service_module = importlib.import_module("apps.chat_data_app.services.agent_run.uploaded_file_service")


def _isolated_workspace(
    monkeypatch, tmp_path, conversation_id: str, user_id: str = "user_a", *, created_date: str | None = None
):
    """把会话工作区建在 tmp_path 下，避免用例之间共享 CHAT_DATA_HOME。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr("utils.storage_paths.CACHE_THREADS_DIR", tmp_path / "cache" / "threads")
    return ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)


def test_workspace_runtime_from_state_rebuilds_runtime_context(tmp_path):
    runtime = workspace_runtime_from_state(
        {
            "user_id": "u1",
            "workspace": {
                "workspace_root": str(tmp_path),
                "workspace_backend": "local",
            },
        }
    )

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
        await runtime.write_internal_text_files([("/mnt/user-data/workspace/semantic/model.yaml", "model")])
    with pytest.raises(ValueError, match="不支持隐藏工具结果存储"):
        await runtime.write_tool_result(
            file_path="/mnt/user-data/outputs/.tool-offload/result.txt",
            content="result",
        )
    with pytest.raises(ValueError, match="不支持产物读取"):
        await runtime.artifact_payload(file_path="/mnt/user-data/outputs/report.txt")
    with pytest.raises(ValueError, match="不支持Office 预览"):
        await runtime.render_office_html(file_path="/mnt/user-data/outputs/report.docx", max_chars=100)


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

    date_str = datetime.now().strftime("%Y-%m-%d")
    assert Path(paths["root"]).exists()
    assert Path(paths["root"]).as_posix().endswith(f"threads/user_a/{date_str}/conv_workspace_test/user-data")
    assert Path(paths["workspace"]).exists()
    assert Path(paths["outputs"]).exists()
    assert Path(paths["uploads"]).exists()
    assert (Path(paths["outputs"]) / ".tool-offload").exists()
    # 技能快照按用户/技能集合哈希共享，不在会话目录预建，也不进入 paths 字典
    assert not runtime_skills_snapshot_dir_from_workspace(paths["root"]).exists()
    assert not (Path(paths["root"]).parent / "runtime-skills").exists()
    assert "runtime_skills" not in paths


def test_ensure_conversation_workspace_date_layering(tmp_path, monkeypatch):
    """工作区按会话创建日期归档：threads/<uid>/<yyyy-MM-DD>/<cid>，幂等且互不串目录。"""
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")

    paths = ensure_conversation_workspace("conv_date_a", "user_a", created_date="2026-08-14")
    assert Path(paths["root"]).as_posix().endswith("threads/user_a/2026-08-14/conv_date_a/user-data")
    # 同日期重复 ensure 幂等：返回同一 root
    again = ensure_conversation_workspace("conv_date_a", "user_a", created_date="2026-08-14")
    assert again["root"] == paths["root"]
    # 不同日期不串目录
    other = ensure_conversation_workspace("conv_date_b", "user_a", created_date="2026-09-01")
    assert Path(other["root"]).as_posix().endswith("threads/user_a/2026-09-01/conv_date_b/user-data")


def test_ensure_conversation_workspace_rejects_invalid_date():
    for bad_date in ["2026-13-01", "2026-08-32", "2026-8-14", "20260901", "2026/09/01", "2026-09", "abc", ""]:
        with pytest.raises(ValueError, match="created_date"):
            ensure_conversation_workspace("conv_bad_date", "user_a", created_date=bad_date)


def test_resolve_workspace_date_variants():
    from apps.chat_data_app.services.agent_run.conversation_workspace import resolve_workspace_date

    assert resolve_workspace_date(datetime(2026, 8, 14, 10, 0, 0)) == "2026-08-14"
    assert resolve_workspace_date(None) == datetime.now().strftime("%Y-%m-%d")


def test_get_conversation_created_date_resolves_from_thread(monkeypatch):
    from apps.chat_data_app.services.agent_run.conversation_workspace import get_conversation_created_date

    class FakeDao:
        async def get_thread(self, conversation_id, user_id=None):
            if conversation_id == "conv_hit":
                return SimpleNamespace(create_time=datetime(2026, 8, 14, 10, 0, 0))
            return None

    monkeypatch.setattr(conversation_workspace_module, "conversation_dao", FakeDao())
    import asyncio

    assert asyncio.run(get_conversation_created_date("conv_hit", user_id="user_a")) == "2026-08-14"
    assert asyncio.run(get_conversation_created_date("conv_miss", user_id="user_a")) is None


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
        resolve_workspace_path(paths["root"], "/mnt/user-data/workspace/outside-link.txt")


def test_resolve_workspace_path_restricts_writes():
    paths = ensure_conversation_workspace("conv_workspace_writes")

    writable = resolve_workspace_path(paths["root"], "/mnt/user-data/workspace/plan.md", writable=True)
    assert writable.name == "plan.md"
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "other-files/demo/SKILL.md", writable=True)
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "/mnt/user-data/outputs/.tool-offload/private.json", writable=True)
    with pytest.raises(ValueError, match="语义模型只读快照"):
        resolve_workspace_path(paths["root"], "/mnt/user-data/workspace/semantic/model.yaml", writable=True)
    snapshot = resolve_workspace_path(
        paths["root"],
        "/mnt/user-data/workspace/semantic/model.yaml",
        writable=True,
        allow_managed_semantic_model_write=True,
    )
    assert snapshot.name == "model.yaml"


@pytest.mark.asyncio
async def test_internal_semantic_model_write_overwrites_only_selected_file(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = ensure_conversation_workspace("conv_semantic_replace", "user_a")
    semantic_root = conversation_semantic_dir_from_workspace(paths["root"])
    semantic_root.mkdir(parents=True, exist_ok=True)
    (semantic_root / "old.yaml").write_text("old", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    result = await runtime.write_internal_text_files(
        [
            ("/mnt/user-data/workspace/semantic/new.yaml", "new"),
        ]
    )

    assert (semantic_root / "old.yaml").read_text(encoding="utf-8") == "old"
    assert (semantic_root / "new.yaml").read_text(encoding="utf-8") == "new"
    assert result[0]["file_path"] == "/mnt/user-data/workspace/semantic/new.yaml"


@pytest.mark.asyncio
async def test_semantic_cache_shared_by_datasource_and_config(tmp_path, monkeypatch):
    """同一数据源/权限/配置的语义缓存跨会话共享。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    first_paths = ensure_conversation_workspace("conv_semantic_shared_a", "user_a")
    second_paths = ensure_conversation_workspace("conv_semantic_shared_b", "user_a")
    first = ConversationWorkspaceRuntime(workspace_root=first_paths["root"], user_id="user_a")
    second = ConversationWorkspaceRuntime(workspace_root=second_paths["root"], user_id="user_a")

    expected = first.activate_semantic_cache(datasource_id="ds_a")
    second.activate_semantic_cache(datasource_id="ds_a")
    await first.write_internal_text_files([("/mnt/user-data/workspace/semantic/shared.yaml", "shared: true")])

    assert conversation_semantic_dir_from_workspace(first_paths["root"]) == expected
    assert conversation_semantic_dir_from_workspace(second_paths["root"]) == expected
    assert (expected / "shared.yaml").read_text(encoding="utf-8") == "shared: true"
    assert (
        await second.read_text_file(
            file_path="/mnt/user-data/workspace/semantic/shared.yaml", offset=0, limit=10, max_chars=100
        )
    )["lines"] == ["shared: true"]


def test_workspace_path_mapping_translates_semantic_cache():
    """bash 通道的 semantic 虚拟路径必须映射到本地缓存，而不是共享工作区。"""
    workspace_root = "/tmp/chat_cache_map/threads/u1/2026-11-11/c1/user-data"
    runtime = ConversationWorkspaceRuntime(workspace_root=workspace_root, user_id="u1")
    runtime.activate_semantic_cache(datasource_id="ds_a")
    mapping = workspace_runtime_module.WorkspacePathMapping(workspace_root=workspace_root)

    translated = mapping.translate("cat /mnt/user-data/workspace/semantic/a.yaml")
    expected = str(conversation_semantic_dir_from_workspace(workspace_root) / "a.yaml")

    assert expected in translated
    assert mapping.mask(translated) == "cat /mnt/user-data/workspace/semantic/a.yaml"


def test_cache_relative_from_workspace_handles_symlinked_cache_root(tmp_path, monkeypatch):
    """缓存根经符号链接配置时，反推相对路径不能抛 ValueError（会让 bash 校验 500）。"""
    real_root = tmp_path / "real-cache"
    real_root.mkdir()
    linked_root = tmp_path / "cache-link"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持目录符号链接")
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    monkeypatch.setattr(storage_paths_module, "CACHE_THREADS_DIR", linked_root / "threads")
    workspace_root = tmp_path / "threads" / "user_a" / "2026-11-11" / "c1" / "user-data"
    workspace_root.mkdir(parents=True)

    assert conversation_semantic_dir_from_workspace(str(workspace_root)).is_absolute()


def test_to_visible_path_semantic_root_is_canonical(tmp_path, monkeypatch):
    """语义缓存目录本身的可见路径不能含 '.' 段（会被 _assert_canonical 拒绝）。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = ensure_conversation_workspace("conv_visible_semantic_root", "user_a")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a")
    semantic_root = runtime.activate_semantic_cache(datasource_id="ds_a")

    assert to_visible_path(paths["root"], semantic_root) == "/mnt/user-data/workspace/semantic"
    assert (
        to_visible_path(paths["root"], semantic_root / "model.yaml") == "/mnt/user-data/workspace/semantic/model.yaml"
    )


@pytest.mark.asyncio
async def test_semantic_cache_isolated_between_users(tmp_path, monkeypatch):
    """同一数据源组下不同用户的语义缓存不能互相共享。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    user_a_paths = ensure_conversation_workspace("conv_semantic_user_a", "user_a")
    user_b_paths = ensure_conversation_workspace("conv_semantic_user_b", "user_b")
    user_a = ConversationWorkspaceRuntime(workspace_root=user_a_paths["root"], user_id="user_a")
    user_b = ConversationWorkspaceRuntime(workspace_root=user_b_paths["root"], user_id="user_b")

    a_dir = user_a.activate_semantic_cache(datasource_id="ds_a")
    b_dir = user_b.activate_semantic_cache(datasource_id="ds_a")
    await user_a.write_internal_text_files([("/mnt/user-data/workspace/semantic/private.yaml", "private: true")])

    assert a_dir != b_dir
    assert (a_dir / "private.yaml").read_text(encoding="utf-8") == "private: true"
    assert not (b_dir / "private.yaml").exists()


@pytest.mark.asyncio
async def test_hash_ref_resolves_only_new_layout(tmp_path, monkeypatch):
    """旧版带 hash 的 ref 不再解析旧共享路径，只按 user + datasource 落到新布局。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = ensure_conversation_workspace("conv_semantic_old_ref", "user_a")
    ref_path = conversation_semantic_ref_path(paths["root"])
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text('{"datasource_id":"ds_a","scope_hash":"scope_a","config_hash":"cfg_a"}', encoding="utf-8")

    resolved = conversation_semantic_dir_from_workspace(paths["root"])

    assert resolved == semantic_cache_dir("user_a", "ds_a").resolve()
    assert "scope_a" not in resolved.parts
    assert "cfg_a" not in resolved.parts


@pytest.mark.asyncio
async def test_legacy_shared_semantic_is_not_readable_or_listed(tmp_path, monkeypatch):
    """历史会话共享 semantic 目录不再兜底读取、不进入工作区文件列表。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = ensure_conversation_workspace("conv_semantic_legacy", "user_a")
    legacy_root = Path(paths["workspace"]) / "semantic"
    legacy_root.mkdir()
    (legacy_root / "legacy.yaml").write_text("legacy: true", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    target = resolve_workspace_path(paths["root"], "/mnt/user-data/workspace/semantic/legacy.yaml")
    assert not target.exists()
    assert legacy_root not in target.parents
    read = await runtime.read_text_file(
        file_path="/mnt/user-data/workspace/semantic/legacy.yaml", offset=0, limit=10, max_chars=100
    )
    assert read["exists"] is False
    assert not any(
        item["path"].endswith("legacy.yaml") for item in list_workspace_files("conv_semantic_legacy", "user_a")
    )


@pytest.mark.asyncio
async def test_internal_semantic_model_writes_cache_not_shared_workspace(tmp_path, monkeypatch):
    """语义模型物化只落本地缓存，不进入共享 user-data。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = ensure_conversation_workspace("conv_semantic_cache", "user_a")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    await runtime.write_internal_text_files([("/mnt/user-data/workspace/semantic/cache-only.yaml", "a: 1")])

    cache_file = conversation_semantic_dir_from_workspace(paths["root"]) / "cache-only.yaml"
    assert cache_file.read_text(encoding="utf-8") == "a: 1"
    assert not (Path(paths["workspace"]) / "semantic" / "cache-only.yaml").exists()
    assert any(
        item["path"] == "/mnt/user-data/workspace/semantic/cache-only.yaml"
        for item in list_workspace_files("conv_semantic_cache", "user_a")
    )


@pytest.mark.asyncio
async def test_internal_semantic_model_delete_cleans_residual_file(tmp_path, monkeypatch):
    """系统残留清理通道：允许删 semantic 下 YAML；语义目录外的路径仍被拒。"""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = ensure_conversation_workspace("conv_semantic_delete", "user_a")
    semantic_root = conversation_semantic_dir_from_workspace(paths["root"])
    semantic_root.mkdir(parents=True, exist_ok=True)
    stale = semantic_root / "已停用模型__deadbeef.yaml"
    stale.write_text("stale", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    result = await runtime.delete_internal_text_files(
        path="/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"
    )

    assert not stale.exists()
    assert result["path"] == "/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"
    assert result["type"] == "file"

    # 通道限定语义 YAML：semantic 之外的文件即使走内部通道也必须被拒
    with pytest.raises(ValueError, match="内部语义模型写入"):
        await runtime.delete_internal_text_files(path="/mnt/user-data/workspace/plan.md")


@pytest.mark.asyncio
async def test_tool_results_are_listed_but_read_only_and_reusable_by_file_read(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "users")
    monkeypatch.setattr(tool_result_store, "CHAT_DATA_HOME", str(tmp_path))
    paths = ensure_conversation_workspace("conv_private", "user_a")
    private_file = Path(paths["outputs"]) / ".tool-offload" / "result_1.json"
    private_file.write_text('{"rows":[1]}', encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    assert any(item["name"] == "result_1.json" for item in list_workspace_files("conv_private", "user_a"))
    preview = read_workspace_text_file(
        "conv_private", "/mnt/user-data/outputs/.tool-offload/result_1.json", user_id="user_a"
    )
    assert preview["content"] == '{"rows":[1]}'
    result = await runtime.read_text_file(
        file_path="/mnt/user-data/outputs/.tool-offload/result_1.json",
        offset=0,
        limit=10,
        max_chars=100,
    )
    assert result["lines"] == ['{"rows":[1]}']
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "/mnt/user-data/outputs/.tool-offload/result_1.json", writable=True)


def test_get_workspace_total_size_sums_all_four_directories(tmp_path):
    from apps.chat_data_app.services.agent_run.conversation_workspace import get_workspace_total_size

    root = tmp_path / "user-data"
    for name in ("workspace", "outputs", "uploads", "memory"):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "a.txt").write_bytes(b"123")
        (directory / "nested").mkdir()
        (directory / "nested" / "b.txt").write_bytes(b"45")
    assert get_workspace_total_size(root) == 20


def test_get_workspace_total_size_ignores_legacy_semantic(tmp_path):
    """历史会话共享 semantic YAML 不再计入工作区占用（也不再上传沙箱）。"""
    from apps.chat_data_app.services.agent_run.conversation_workspace import get_workspace_total_size

    root = tmp_path / "user-data"
    for name in ("workspace", "outputs", "uploads", "memory"):
        (root / name).mkdir(parents=True)
        (root / name / "a.txt").write_bytes(b"123")
    legacy_semantic = root / "workspace" / "semantic"
    legacy_semantic.mkdir()
    (legacy_semantic / "stale.yaml").write_bytes(b"1234567890")

    assert get_workspace_total_size(root) == 12


@pytest.mark.asyncio
async def test_query_result_is_listed_read_only_and_reusable_by_file_read(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "users")
    paths = ensure_conversation_workspace("conv_query_result", "user_a")
    result_file = Path(paths["outputs"]) / "query_result" / "result_1.json"
    result_file.parent.mkdir()
    result_file.write_text('{\n  "rows": [1, 2]\n}', encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="user_a",
        backend="local",
    )
    file_read = next(
        tool for tool in filesystem_tool_module.create_filesystem_tools(runtime) if tool.name == "file_read"
    )

    listed = list_workspace_files("conv_query_result", "user_a")
    assert any(item["name"] == "result_1.json" for item in listed)
    content = await file_read.ainvoke(
        {
            "file_path": "/mnt/user-data/outputs/query_result/result_1.json",
            "offset": 0,
            "limit": 2,
        }
    )

    assert "total_lines=3" in content
    assert "     1 {" in content
    assert "has_more=true" in content
    with pytest.raises(ValueError, match="系统结果目录"):
        resolve_workspace_path(
            paths["root"],
            "/mnt/user-data/outputs/query_result/result_1.json",
            writable=True,
        )


def test_resolve_workspace_path_rejects_non_canonical_forms():
    paths = ensure_conversation_workspace(f"conv_workspace_alias_{uuid.uuid4().hex}")

    for path in (
        "/workspace/demo.json",
        "/outputs/sales.chart.json",
        "/uploads/source.csv",
        "workspace/demo.json",
        "outputs/sales.chart.json",
        "./workspace/demo.json",
    ):
        with pytest.raises(ValueError, match="/mnt/user-data/"):
            resolve_workspace_path(paths["root"], path, writable=True)

    workspace_file = resolve_workspace_path(paths["root"], "/mnt/user-data/workspace/demo.json", writable=True)
    outputs_file = resolve_workspace_path(paths["root"], "/mnt/user-data/outputs/sales.chart.json", writable=True)

    assert workspace_file == Path(paths["workspace"]) / "demo.json"
    assert outputs_file == Path(paths["outputs"]) / "sales.chart.json"


@pytest.mark.asyncio
async def test_file_copy_copies_upload_into_workspace_and_keeps_source(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "users")
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
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "users")
    paths = ensure_conversation_workspace("conv_file_copy_guard", "user_a")
    source = Path(paths["uploads"]) / "a.bin"
    source.write_bytes(b"a")
    existing = Path(paths["workspace"]) / "b.bin"
    existing.write_bytes(b"b")
    offload = Path(paths["outputs"]) / ".tool-offload" / "r.json"
    offload.write_text("{}", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=paths["root"], user_id="user_a", backend="local")

    with pytest.raises(ValueError, match="目标路径已存在"):
        await runtime.copy_path(
            source_path="/mnt/user-data/uploads/a.bin", destination_path="/mnt/user-data/workspace/b.bin"
        )
    with pytest.raises(ValueError, match="只允许写入"):
        await runtime.copy_path(
            source_path="/mnt/user-data/uploads/a.bin", destination_path="/mnt/user-data/uploads/c.bin"
        )
    with pytest.raises(ValueError, match="只支持复制单个文件"):
        await runtime.copy_path(
            source_path="/mnt/user-data/workspace", destination_path="/mnt/user-data/workspace/d.bin"
        )
    with pytest.raises(ValueError, match="tool-offload"):
        await runtime.copy_path(
            source_path="/mnt/user-data/outputs/.tool-offload/r.json",
            destination_path="/mnt/user-data/workspace/e.json",
        )
    with pytest.raises(FileNotFoundError):
        await runtime.copy_path(
            source_path="/mnt/user-data/uploads/missing.bin", destination_path="/mnt/user-data/workspace/f.bin"
        )


@pytest.mark.asyncio
async def test_file_copy_accepts_readonly_skill_source_and_keeps_skill_immutable(tmp_path):
    """/mnt/skills 只读技能文件可作为 file_copy 源，且技能路径不能作为复制目标。"""
    workspace_root = tmp_path / "threads" / "u1" / "conv_skill_copy" / "user-data"
    (workspace_root / "workspace").mkdir(parents=True)
    skill_dir = runtime_skills_snapshot_dir_from_workspace(str(workspace_root)) / "demo-skill"
    template = skill_dir / "assets" / "template.pptx"
    template.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# demo", encoding="utf-8")
    template.write_bytes(b"\x00pptx-template")
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="local",
    )

    payload = await runtime.copy_path(
        source_path="/mnt/skills/demo-skill/assets/template.pptx",
        destination_path="/mnt/user-data/workspace/汇报.pptx",
    )

    assert payload["source_path"] == "/mnt/skills/demo-skill/assets/template.pptx"
    assert payload["destination_path"] == "/mnt/user-data/workspace/汇报.pptx"
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

    assert visible_path == "/mnt/user-data/workspace/context.json"
    with pytest.raises(ValueError, match="/mnt/user-data/"):
        resolve_workspace_path(paths["root"], "././workspace/context.json")
    with pytest.raises(ValueError):
        resolve_workspace_path(paths["root"], "")
    (Path(paths["outputs"]) / ".tool-offload").rmdir()
    (Path(paths["root"]) / "outputs").rmdir()
    (Path(paths["workspace"]) / "nested-dir").mkdir()
    empty_dir = Path(paths["outputs"]) / "123"
    empty_dir.mkdir(parents=True)
    files = list_workspace_files(conversation_id)
    assert any(
        item["path"] == "/mnt/user-data/workspace/context.json" and item["directory"] == "workspace" for item in files
    )
    directories = list_workspace_directories(conversation_id)
    assert any(
        item["path"] == "/mnt/user-data/outputs/123"
        and item["relative_path"] == "outputs/123"
        and item["directory"] == "outputs"
        for item in directories
    )

    payload = read_workspace_text_file(conversation_id, "/mnt/user-data/workspace/context.json")
    assert payload["path"] == "/mnt/user-data/workspace/context.json"
    assert payload["relative_path"] == "workspace/context.json"
    assert '"用户": "张三"' in payload["content"]

    outside_path = Path(paths["root"]).parent / "outside.txt"
    outside_path.write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError, match="不在当前会话工作区"):
        to_visible_path(paths["root"], outside_path)


def test_read_workspace_text_file_rejects_missing_and_large_files():
    conversation_id = f"conv_workspace_read_errors_{uuid.uuid4().hex}"
    paths = ensure_conversation_workspace(conversation_id)
    with pytest.raises(FileNotFoundError):
        read_workspace_text_file(conversation_id, "/mnt/user-data/workspace/missing.txt")

    target = Path(paths["workspace"]) / "big.txt"
    target.write_text("abcdef", encoding="utf-8")
    with pytest.raises(ValueError):
        read_workspace_text_file(conversation_id, "/mnt/user-data/workspace/big.txt", max_bytes=3)


@pytest.mark.asyncio
async def test_runtime_resolves_system_skill_file_under_system_path(tmp_path):
    # 运行域技能统一为扁平 /mnt/skills/<name>：直接从本会话快照目录解析，
    # 不再按类别/远程解析 skill_service.resolve_skill_path_async。
    workspace_root = tmp_path / "threads" / "u1" / "conv_x" / "user-data"
    workspace_root.mkdir(parents=True)
    skill_dir = runtime_skills_snapshot_dir_from_workspace(str(workspace_root)) / "skill-creator"
    doc_path = skill_dir / "references" / "guide.md"
    doc_path.parent.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: skill-creator\n---\n# Skill Creator", encoding="utf-8")
    doc_path.write_text("# Guide\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="local",
    )
    # 快照目录已存在则 _ensure_snapshot_async 直接复用，不触发远程构建
    assert (runtime.runtime_skills_dir / "skill-creator").is_dir()

    target, visible_path = await runtime.resolve_readonly_skill_path_async(
        "/mnt/skills/skill-creator/references/guide.md"
    )

    assert target == doc_path
    assert visible_path == "/mnt/skills/skill-creator/references/guide.md"

    # 快照中不存在的技能名 → 无权限/不存在；隐藏名 → 路径无效
    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        await runtime.resolve_readonly_skill_path_async("/mnt/skills/unknown/references/guide.md")
    with pytest.raises(ValueError, match="技能路径无效"):
        await runtime.resolve_readonly_skill_path_async("/mnt/skills/.evil/references/guide.md")


@pytest.mark.asyncio
async def test_runtime_rejects_symlinked_skill_directory(tmp_path):
    workspace_root = tmp_path / "threads" / "u1" / "conv_x" / "user-data"
    workspace_root.mkdir(parents=True)
    skill_root = runtime_skills_snapshot_dir_from_workspace(str(workspace_root))
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


def test_normalize_json_like_payload_accepts_escaped_json_string():
    payload = {"metrics": ["订单金额"], "dimensions": [], "filters": [], "order_by": [], "limit": 100}

    assert normalize_json_like_payload(json.dumps(payload, ensure_ascii=False)) == payload
    assert (
        normalize_json_like_payload(json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False)) == payload
    )
    assert normalize_json_like_payload("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```") == payload


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

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/workspace/plan.json",
            "content": {"cards": [{"title": "风险总览"}]},
        }
    )

    assert "[file_write]" in result
    assert '"风险总览"' in (Path(paths["workspace"]) / "plan.json").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_file_edit_requires_unique_existing_content(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_edit_unique_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "report.txt"
    target.write_text("title=old\nvalue=old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="匹配多处"):
        await tools["file_edit"].ainvoke(
            {
                "file_path": "/mnt/user-data/workspace/report.txt",
                "old_content": "old",
                "new_content": "new",
            }
        )
    success = await tools["file_edit"].ainvoke(
        {
            "file_path": "/mnt/user-data/workspace/report.txt",
            "old_content": "title=old",
            "new_content": "title=new",
        }
    )
    with pytest.raises(ValueError, match="未在文件中找到"):
        await tools["file_edit"].ainvoke(
            {
                "file_path": "/mnt/user-data/workspace/report.txt",
                "old_content": "missing",
                "new_content": "new",
            }
        )

    assert "[file_edit]" in success
    assert target.read_text(encoding="utf-8") == "title=new\nvalue=old\n"


@pytest.mark.asyncio
async def test_file_edit_rejects_identical_content(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_edit_identical_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "report.txt"
    target.write_text("title=old\n", encoding="utf-8")

    with pytest.raises(ValueError, match="old_content 与 new_content 相同"):
        await tools["file_edit"].ainvoke(
            {
                "file_path": "/mnt/user-data/workspace/report.txt",
                "old_content": "title=old",
                "new_content": "title=old",
            }
        )

    assert target.read_text(encoding="utf-8") == "title=old\n"


@pytest.mark.asyncio
async def test_file_write_and_edit_reject_uploads(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_write_uploads_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    with pytest.raises(ValueError, match="只允许写入 workspace/、outputs/ 或 memory/"):
        await tools["file_write"].ainvoke({"file_path": "/mnt/user-data/uploads/a.txt", "content": "x"})
    with pytest.raises(ValueError, match="只允许写入 workspace/、outputs/ 或 memory/"):
        await tools["file_edit"].ainvoke(
            {
                "file_path": "/mnt/user-data/uploads/a.txt",
                "old_content": "x",
                "new_content": "y",
            }
        )


@pytest.mark.asyncio
async def test_present_artifacts_returns_outputs_artifact_payload(monkeypatch):
    paths = ensure_conversation_workspace("conv_present_artifacts")
    chart_path = Path(paths["outputs"]) / "sales.chart.json"
    chart_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "title": "销售额趋势",
                "chart_type": "line",
                "data": [{"month": "2026-01", "sales": 120}],
                "encoding": {"x": "month", "y": ["sales"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({"files": ["/mnt/user-data/outputs/sales.chart.json"]})

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 1
    assert payload["errors"] == []
    assert payload["artifacts"][0]["artifact_type"] == "chart_spec"
    assert payload["artifacts"][0]["file_path"] == "/mnt/user-data/outputs/sales.chart.json"
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

    result = await tools["present_artifacts"].ainvoke(
        {
            "files": ["/mnt/user-data/outputs/公文报告.docx"],
        }
    )

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["errors"] == []
    assert payload["artifacts"] == [
        {
            "file_path": "/mnt/user-data/outputs/公文报告.docx",
            "artifact_type": "document",
            "renderer": "document",
            "artifact_version": "v1",
            "name": "公文报告",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ]


@pytest.mark.asyncio
async def test_present_artifacts_returns_markdown_document(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_markdown_{uuid.uuid4().hex}")
    markdown_path = Path(paths["outputs"]) / "语义模型分析报告.md"
    markdown_path.write_text("# 语义模型分析报告\n", encoding="utf-8")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke(
        {
            "files": ["/mnt/user-data/outputs/语义模型分析报告.md"],
        }
    )

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["errors"] == []
    assert payload["artifacts"] == [
        {
            "file_path": "/mnt/user-data/outputs/语义模型分析报告.md",
            "artifact_type": "markdown",
            "renderer": "markdown",
            "artifact_version": "v1",
            "name": "语义模型分析报告",
            "mime_type": "text/markdown",
        }
    ]


@pytest.mark.asyncio
async def test_present_artifacts_returns_valid_output_skill(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_skill_{uuid.uuid4().hex}")
    skill_dir = Path(paths["outputs"]) / "sales-analysis"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sales-analysis\ntitle: 销售分析\ndescription: 汇总并分析销售表现。\n---\n\n# 销售分析\n",
        encoding="utf-8",
    )
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke(
        {
            "files": ["/mnt/user-data/outputs/sales-analysis/SKILL.md"],
        }
    )

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["errors"] == []
    assert payload["artifacts"] == [
        {
            "file_path": "/mnt/user-data/outputs/sales-analysis/SKILL.md",
            "artifact_type": "skill",
            "renderer": "skill",
            "artifact_version": "v1",
            "skill_title": "销售分析",
            "description": "汇总并分析销售表现。",
            "name": "sales-analysis",
            "mime_type": "text/markdown",
        }
    ]


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

    result = await tools["present_artifacts"].ainvoke(
        {
            "files": [
                "/mnt/user-data/outputs/sales-analysis/SKILL.md",
                "/mnt/user-data/outputs/nested/sales-analysis/SKILL.md",
            ],
        }
    )

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

    result = await tools["present_artifacts"].ainvoke({"files": ["/mnt/user-data/workspace/draft.json"]})

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 0
    assert payload["artifacts"] == []
    assert payload["errors"][0]["file_path"] == "/mnt/user-data/workspace/draft.json"


@pytest.mark.asyncio
async def test_present_artifacts_rejects_hidden_tool_result(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_present_private_{uuid.uuid4().hex}")
    private_file = Path(paths["outputs"]) / ".tool-offload" / "result_1.json"
    private_file.write_text('{"rows":[1]}', encoding="utf-8")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke(
        {
            "files": ["/mnt/user-data/outputs/.tool-offload/result_1.json"],
        }
    )

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 0
    assert payload["artifacts"] == []
    assert payload["errors"] == [
        {
            "file_path": "/mnt/user-data/outputs/.tool-offload/result_1.json",
            "error": "文件不是可展示的 outputs 产物",
        }
    ]


@pytest.mark.asyncio
async def test_present_artifacts_skips_empty_file_and_records_payload_errors(monkeypatch):
    paths = ensure_conversation_workspace("conv_present_artifacts_error")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["present_artifacts"].ainvoke({"files": ["", "/mnt/user-data/outputs/bad.unknown"]})

    payload = json.loads(result.replace("[present_artifacts]\n", "", 1))
    assert payload["artifact_count"] == 0
    assert payload["artifacts"] == []
    assert payload["errors"] == [
        {"file_path": "/mnt/user-data/outputs/bad.unknown", "error": "文件不是可展示的 outputs 产物"}
    ]


def test_present_artifacts_display_name_and_mime_helpers():
    assert present_artifacts_tool_module._artifact_display_name("/mnt/user-data/outputs/sales.chart.json") == "sales"
    assert (
        present_artifacts_tool_module._artifact_display_name("/mnt/user-data/outputs/dashboard.dashboard.json")
        == "dashboard"
    )
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

    result = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/long.txt"})

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

    result = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/report.txt"})

    assert "total_lines=unknown" in result
    assert "     1 one" in result
    assert "     2 two" in result
    assert "file_read 执行失败" not in result


@pytest.mark.asyncio
async def test_file_read_outputs_graph_chart_spec_returns_content(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_graph_chart_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["outputs"]) / "chart_spec_graph.json"
    target.write_text(
        json.dumps(
            {
                "version": "1.0",
                "title": "商品订单关系图",
                "chart_type": "graph",
                "nodes": [{"name": "商品A"}, {"name": "订单1"}],
                "links": [{"source": "商品A", "target": "订单1", "value": 1}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/outputs/chart_spec_graph.json"})

    assert "[file_read]" in result
    assert '"chart_type": "graph"' in result
    assert "商品订单关系图" in result


@pytest.mark.asyncio
async def test_file_read_rejects_invalid_offset_and_limit(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_invalid_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    # 参数校验失败统一 raise，由 ToolErrorMiddleware 转成 error ToolMessage
    with pytest.raises(ValueError, match="offset 必须大于等于 0"):
        await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/demo.txt", "offset": -1})
    with pytest.raises(ValueError, match="limit 必须在 1-500 之间"):
        await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/demo.txt", "limit": 0})
    with pytest.raises(ValueError, match="limit 必须在 1-500 之间"):
        await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/demo.txt", "limit": 501})


@pytest.mark.asyncio
async def test_file_read_truncates_long_lines_without_char_continuation(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_budget_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["workspace"]) / "rows.jsonl"
    target.write_text("a" * 600 + "\n" + "b" * 600 + "\nnext", encoding="utf-8")
    monkeypatch.setattr(filesystem_tool_module, "FILE_READ_MAX_CHARS", 1000)

    first_page = await tools["file_read"].ainvoke(
        {
            "file_path": "/mnt/user-data/workspace/rows.jsonl",
            "limit": 3,
        }
    )

    assert "truncated=true" in first_page
    assert "truncated_lines=1,2" in first_page
    assert "char_offset" not in first_page
    assert f"     1 {'a' * 500}" in first_page
    assert f"     2 {'b' * 499}" in first_page
    assert "next_offset=2" in first_page

    next_page = await tools["file_read"].ainvoke(
        {
            "file_path": "/mnt/user-data/workspace/rows.jsonl",
            "offset": 2,
        }
    )

    assert "     3 next" in next_page
    assert "truncated=true" not in next_page


@pytest.mark.asyncio
async def test_file_read_delegates_to_runtime_backend():
    class RecordingBackend:
        name = "recording"

        async def read_text_file(self, _runtime, **kwargs):
            assert kwargs == {
                "file_path": "/mnt/user-data/workspace/report.txt",
                "offset": 0,
                "limit": 200,
                "max_chars": 25000,
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

        async def delete_internal_text_files(self, *_args, **_kwargs):
            raise AssertionError("unexpected internal semantic model delete")

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

    result = await tool.ainvoke({"file_path": "/mnt/user-data/workspace/report.txt"})

    assert "backend result" in result


def test_text_page_content_body_respects_budget_and_preserves_source_order(tmp_path):
    target = tmp_path / "rows.jsonl"
    target.write_text("ab\nabcdefghij\nxyz\nnext", encoding="utf-8")

    result = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/mnt/user-data/workspace/rows.jsonl",
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
        "/mnt/user-data/workspace/rows.jsonl",
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
        "/mnt/user-data/workspace/separators.txt",
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
        "/mnt/user-data/workspace/control-separator.txt",
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
    monkeypatch.setattr(workspace_runtime_module.subprocess, "run", fake_run)

    first = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/mnt/user-data/workspace/report with spaces.txt",
        offset=0,
        limit=1,
        max_chars=100,
    )
    second = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/mnt/user-data/workspace/report with spaces.txt",
        offset=1,
        limit=1,
        max_chars=100,
    )

    assert first["total_lines"] == second["total_lines"] == 3
    assert len(calls) == 1
    assert calls[0][0][1:] == ["END { print NR }", str(target)]
    assert calls[0][1]["timeout"] == 30
    assert calls[0][1]["check"] is False

    target.write_text("first\nsecond\nthird\nfourth", encoding="utf-8")
    third = workspace_local_backend_module._read_text_page_from_path(
        target,
        "/mnt/user-data/workspace/report with spaces.txt",
        offset=2,
        limit=1,
        max_chars=100,
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
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path / "chat-data"))
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="sandbox",
    )
    tool = next(tool for tool in filesystem_tool_module.create_filesystem_tools(runtime) if tool.name == "file_read")

    with pytest.raises(ValueError, match="会话工作区不在 CHAT_DATA_HOME 内"):
        await tool.ainvoke({"file_path": "/mnt/user-data/workspace/secret.txt"})


@pytest.mark.asyncio
async def test_file_read_reports_missing_dir_binary_empty_and_offset_overflow(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_edges_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    (Path(paths["workspace"]) / "empty.txt").write_text("", encoding="utf-8")
    (Path(paths["workspace"]) / "one.txt").write_text("line1\n", encoding="utf-8")
    (Path(paths["workspace"]) / "bin.dat").write_bytes(b"a\x00b")

    missing = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/missing.txt"})
    directory = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace"})
    binary = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/bin.dat"})
    empty = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/empty.txt"})
    overflow = await tools["file_read"].ainvoke({"file_path": "/mnt/user-data/workspace/one.txt", "offset": 5})

    assert missing == "文件不存在: /mnt/user-data/workspace/missing.txt"
    assert "目标是目录" in directory
    assert "暂不支持以文本方式读取二进制文件" in binary
    assert "status=empty" in empty
    assert "offset 5 超出文件总行数" in overflow


@pytest.mark.asyncio
async def test_file_read_rejects_local_path_escape(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_read_fail_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    with pytest.raises(ValueError, match="路径必须以 /mnt/user-data/ 或 /mnt/skills/ 开头"):
        await tools["file_read"].ainvoke({"file_path": "../demo.txt"})


@pytest.mark.asyncio
async def test_file_glob_recursively_matches_filenames_and_includes_tool_results(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_glob_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    (Path(paths["workspace"]) / "reports" / "daily").mkdir(parents=True)
    (Path(paths["workspace"]) / "reports" / "daily" / "sales.json").write_text("{}", encoding="utf-8")
    (Path(paths["workspace"]) / "summary.json").write_text("{}", encoding="utf-8")
    hidden = Path(paths["outputs"]) / ".tool-offload" / "query.json"
    hidden.write_text("{}", encoding="utf-8")

    result = await tools["file_glob"].ainvoke(
        {
            "path": "/mnt/user-data/workspace",
            "pattern": "*.json",
        }
    )

    payload = json.loads(result.replace("[file_glob]\n", "", 1))
    assert payload["entries"] == [
        {"path": "/mnt/user-data/workspace/reports/daily/sales.json", "type": "file"},
        {"path": "/mnt/user-data/workspace/summary.json", "type": "file"},
    ]

    offload_result = await tools["file_glob"].ainvoke(
        {
            "path": "/mnt/user-data/outputs",
            "pattern": "*.json",
        }
    )
    offload_payload = json.loads(offload_result.replace("[file_glob]\n", "", 1))
    assert {"path": "/mnt/user-data/outputs/.tool-offload/query.json", "type": "file"} in offload_payload["entries"]


@pytest.mark.asyncio
async def test_file_glob_rejects_path_patterns(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_file_glob_pattern_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    with pytest.raises(ValueError, match="pattern 只能匹配文件名"):
        await tools["file_glob"].ainvoke({"pattern": "**/*.json"})


@pytest.mark.asyncio
async def test_file_write_returns_only_file_change_payload(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_dashboard_draft_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/workspace/dashboard_draft.json",
            "content": {"dashboard_name": "草稿看板", "cards": [{"title": "销售额"}]},
        }
    )

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/workspace/dashboard_draft.json"
    assert payload["bytes"] > 0
    assert payload["operation"] == "write"
    assert payload["mode"] == "overwrite"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_does_not_classify_output_artifact(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/outputs/monthly_simple_bar.chart.json",
            "content": {
                "version": "1.0",
                "title": "销售额趋势",
                "chart_type": "line",
                "data": [{"month": "2026-01", "sales": 120}],
                "encoding": {"x": "month", "y": ["sales"]},
            },
        }
    )

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/outputs/monthly_simple_bar.chart.json"
    assert payload["bytes"] > 0
    assert payload["operation"] == "write"
    assert payload["mode"] == "overwrite"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_workspace_chart_spec_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_chart_draft_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/workspace/chart_spec_sales_trend_draft.json",
            "content": {
                "version": "1.0",
                "title": "销售额趋势",
                "chart_type": "line",
                "data": [{"month": "2026-01", "sales": 120}],
                "encoding": {"x": "month", "y": ["sales"]},
            },
        }
    )

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/workspace/chart_spec_sales_trend_draft.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_non_chart_json_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_non_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/outputs/query_result.json",
            "content": {"rows": [{"month": "2026-01", "sales": 120}]},
        }
    )

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/outputs/query_result.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_empty_line_chart_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_empty_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/outputs/empty_chart.json",
            "content": {
                "version": "1.0",
                "title": "空趋势图",
                "chart_type": "line",
                "data": [],
                "encoding": {"x": "month", "y": ["sales"]},
            },
        }
    )

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/outputs/empty_chart.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_invalid_chart_spec_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_invalid_chart_write_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/outputs/invalid_chart.json",
            "content": {
                "chart_type": "line",
                "data": [{"month": "2026-01", "sales": 120}],
                "encoding": {"x": "month", "y": ["sales"]},
            },
        }
    )

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/outputs/invalid_chart.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_write_chart_spec_with_missing_encoding_field_has_no_chart_artifact_type(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_invalid_chart_encoding_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)

    result = await tools["file_write"].ainvoke(
        {
            "file_path": "/mnt/user-data/outputs/invalid_chart_encoding.json",
            "content": {
                "version": "1.0",
                "title": "同比趋势",
                "chart_type": "line",
                "data": [{"period": "2024-01", "sales_current": 87, "sales_previous": 71}],
                "encoding": {"x": "period", "y": ["current", "previous"]},
            },
        }
    )

    payload = json.loads(result.replace("[file_write]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/outputs/invalid_chart_encoding.json"
    assert "artifact_type" not in payload


@pytest.mark.asyncio
async def test_file_edit_does_not_classify_output_artifact(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_dashboard_patch_{uuid.uuid4().hex}")
    tools = _create_local_workspace_tools(monkeypatch, paths)
    target = Path(paths["outputs"]) / "dashboard.dashboard.json"
    target.write_text(
        json.dumps(
            {
                "dashboard_name": "旧看板",
                "cards": [
                    {
                        "title": "订单金额",
                        "chart_type": "kpi",
                        "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
                        "dsl": {"metrics": ["订单金额"], "dimensions": [], "filters": [], "order_by": [], "limit": 100},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    result = await tools["file_edit"].ainvoke(
        {
            "file_path": "/mnt/user-data/outputs/dashboard.dashboard.json",
            "old_content": '  "dashboard_name": "旧看板",',
            "new_content": '  "dashboard_name": "新看板",',
        }
    )

    payload = json.loads(result.replace("[file_edit]\n", "", 1))
    assert payload["file_path"] == "/mnt/user-data/outputs/dashboard.dashboard.json"
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

    with pytest.raises(ValueError, match="只允许写入 workspace/、outputs/ 或 memory/"):
        await tools["file_edit"].ainvoke(
            {
                "file_path": "/mnt/user-data/uploads/demo.txt",
                "old_content": "old",
                "new_content": "new",
            }
        )


@pytest.mark.asyncio
async def test_semantic_query_tool_returns_internal_result_envelope(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_save_{uuid.uuid4().hex}")
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

    async def fake_execute_sql_real(
        sql, datasource_id, member_datasource_ids=None, user_id=None, semantic_config_id=None, query_text=None, **kwargs
    ):
        assert sql == "select month, sales from t"
        assert query_text == "统计销售额"
        return {
            "status": "succeeded",
            "sql": sql,
            "results": [
                {
                    "status": "succeeded",
                    "member_datasource_id": "m1",
                    "member_name": "机构1",
                    "columns": ["month", "sales"],
                    "data": [{"month": "2026-01", "sales": 120}],
                    "total_count": 1,
                }
            ],
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

    result = await tools["data_analyzing_tool"].ainvoke(
        {
            "action": "execute_query",
            "action_payload": {"semantic_config_id": "sc_1", "member_datasource_ids": ["m1"], "dsl": input_dsl},
            "state": state,
        }
    )

    payload = json.loads(result)
    assert payload["type"] == "chatdata_internal_tool_result"
    assert payload["kind"] == "query_result"
    assert payload["payload"]["results"][0]["data"][0]["sales"] == 120
    assert payload["meta"] == {
        "query_mode": "dsl",
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

    async def fake_execute_sql_real(
        sql, datasource_id, member_datasource_ids=None, user_id=None, semantic_config_id=None, query_text=None, **kwargs
    ):
        assert sql == "select count(*) cnt from t"
        return {
            "status": "succeeded",
            "sql": sql,
            "results": [
                {
                    "status": "succeeded",
                    "member_datasource_id": "m1",
                    "member_name": "机构1",
                    "columns": ["cnt"],
                    "data": [{"cnt": 3}],
                    "total_count": 1,
                }
            ],
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

    result = await tools["data_analyzing_tool"].ainvoke(
        {
            "action": "execute_query",
            "action_payload": {"semantic_config_id": "sc_1", "member_datasource_ids": ["m1"], "dsl": input_dsl},
            "state": state,
        }
    )

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
            "results": [
                {
                    "status": "succeeded",
                    "member_datasource_id": "m1",
                    "member_name": "机构1",
                    "columns": ["content"],
                    "data": [{"content": "x" * 200}],
                    "total_count": 1,
                }
            ],
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

    result = await tools["data_analyzing_tool"].ainvoke(
        {
            "action": "execute_query",
            "action_payload": {
                "semantic_config_id": "sc_1",
                "member_datasource_ids": ["m1"],
                "dsl": {"metrics": ["内容"]},
            },
            "state": state,
        }
    )

    payload = json.loads(result)
    assert payload["type"] == "chatdata_internal_tool_result"
    assert payload["payload"]["results"][0]["data"][0]["content"] == "x" * 200


@pytest.mark.asyncio
async def test_semantic_query_tool_rejects_missing_datasource_and_invalid_requests(monkeypatch):
    paths = ensure_conversation_workspace(f"conv_workspace_exec_reject_{uuid.uuid4().hex}")
    tools_without_datasource = _create_local_workspace_tools(monkeypatch, paths)
    state_without_datasource = _analysis_query_state(paths, datasource_id=None)

    with pytest.raises(ValueError, match="当前会话未绑定数据源"):
        await tools_without_datasource["data_analyzing_tool"].ainvoke(
            {
                "action": "execute_query",
                "action_payload": {
                    "semantic_config_id": "sc_1",
                    "member_datasource_ids": ["m1"],
                    "dsl": {"metrics": ["订单数"]},
                },
                "state": state_without_datasource,
            }
        )

    tools = _create_local_workspace_tools(
        monkeypatch,
        paths,
        datasource_id="ds_1",
    )
    state = _analysis_query_state(paths)
    with pytest.raises(ValueError, match="sql 必须放在顶层 sql 参数中"):
        await tools["data_analyzing_tool"].ainvoke(
            {
                "action": "execute_query",
                "action_payload": {
                    "semantic_config_id": "sc_1",
                    "member_datasource_ids": ["m1"],
                    "dsl": {"sql": "select 1"},
                },
                "state": state,
            }
        )

    with pytest.raises(ValueError, match="semantic_config_id"):
        await sql_tool_module.create_data_analyzing_tool().ainvoke(
            {
                "action": "execute_query",
                "action_payload": {
                    "semantic_config_id": "",
                    "member_datasource_ids": ["m1"],
                    "dsl": {"metrics": ["订单数"]},
                },
                "state": _analysis_query_state(paths),
            }
        )
    with pytest.raises(ValueError, match="dsl"):
        await tools["data_analyzing_tool"].ainvoke(
            {
                "action": "execute_query",
                "action_payload": {
                    "semantic_config_id": "sc_1",
                    "member_datasource_ids": ["m1"],
                    "dsl": [{"metrics": ["订单数"]}],
                },
                "state": state,
            }
        )
    with pytest.raises(ValueError, match="dsl 必须是单个查询对象"):
        await tools["data_analyzing_tool"].ainvoke(
            {
                "action": "execute_query",
                "action_payload": {"semantic_config_id": "sc_1", "member_datasource_ids": ["m1"], "dsl": {"cards": []}},
                "state": state,
            }
        )


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

    args = {"semantic_config_id": "sc_1", "member_datasource_ids": ["m1"]}
    with pytest.raises(ValueError, match="指标不存在"):
        await tools["data_analyzing_tool"].ainvoke(
            {
                "action": "execute_query",
                "action_payload": {**args, "dsl": {"metrics": ["bad"]}},
                "state": state,
            }
        )
    with pytest.raises(ValueError, match="DSL2SQL 未生成 SQL"):
        await tools["data_analyzing_tool"].ainvoke(
            {
                "action": "execute_query",
                "action_payload": {**args, "dsl": {"metrics": ["empty"]}},
                "state": state,
            }
        )


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

    with pytest.raises(RuntimeError, match="执行失败"):
        await tools["data_analyzing_tool"].ainvoke(
            {
                "action": "execute_query",
                "action_payload": {
                    "semantic_config_id": "sc_1",
                    "member_datasource_ids": ["m1"],
                    "dsl": {"metrics": ["订单数"]},
                },
                "state": state,
            }
        )


@pytest.mark.asyncio
async def test_file_read_reads_session_skill_snapshot(tmp_path):
    """技能正文只能从当前会话的 runtime-skills 快照读取。"""
    workspace_root = tmp_path / "threads" / "u1" / "conv_a" / "user-data"
    workspace_root.mkdir(parents=True)
    snapshot_skill = runtime_skills_snapshot_dir_from_workspace(str(workspace_root)) / "demo-skill"
    snapshot_skill.mkdir(parents=True)
    (snapshot_skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ntitle: 快照版\ndescription: 来自快照\n---\n# 快照版正文\n",
        encoding="utf-8",
    )
    (snapshot_skill / "references" / "guide.md").parent.mkdir(parents=True)
    (snapshot_skill / "references" / "guide.md").write_text("# guide from snapshot\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="local",
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
    other = runtime_skills_snapshot_dir_from_workspace(str(workspace_root)) / "other-skill"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: other-skill\ntitle: 其他\n---\n# 其他\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="local",
    )
    tool = {item.name: item for item in filesystem_tool_module.create_filesystem_tools(runtime)}["file_read"]
    with pytest.raises(ValueError, match="技能不存在或无权限访问"):
        await tool.ainvoke({"file_path": "/mnt/skills/demo-skill/SKILL.md"})


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_file_grep_single_file_returns_matches(monkeypatch, tmp_path):
    """file_grep on a single file must return matches, not silently drop them."""
    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "users")
    paths = ensure_conversation_workspace("conv_grep_file", "user_a")
    target = Path(paths["outputs"]) / "order.dashboard.json"
    target.write_text(
        json.dumps({"title": "订单核心指标", "metrics": ["订单数量", "销售额"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime = ConversationWorkspaceRuntime(
        workspace_root=paths["root"],
        user_id="user_a",
        backend="local",
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
    assert matches[0]["path"] == "/mnt/user-data/outputs/order.dashboard.json"
    assert "订单数量" in matches[0]["line"]
    assert "销售额" in matches[0]["line"]


@pytest.mark.asyncio
async def test_file_grep_single_skill_file_returns_visible_path(monkeypatch, tmp_path):
    """file_grep on a single skill file must return the /mnt/skills visible path."""
    workspace_root = tmp_path / "threads" / "u1" / "conv_grep_skill" / "user-data"
    workspace_root.mkdir(parents=True)
    skill_dir = runtime_skills_snapshot_dir_from_workspace(str(workspace_root)) / "demo-skill"
    skill_dir.mkdir(parents=True)
    guide = skill_dir / "references" / "guide.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("# Demo\n关键词：订单数量\n", encoding="utf-8")

    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(workspace_root),
        user_id="u1",
        backend="local",
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

    monkeypatch.setattr(workspace_runtime_module.shutil, "which", fake_which)
    monkeypatch.setattr(workspace_runtime_module.subprocess, "run", fake_run)

    result = _render_local_office_html(str(root), "/mnt/user-data/outputs/report.docx", 12 * 1024 * 1024)

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

    monkeypatch.setattr(workspace_runtime_module.shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError, match="未安装 OfficeCLI"):
        _render_local_office_html(str(root), "/mnt/user-data/outputs/report.docx", 1024)


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
            assert file_path == "/mnt/user-data/outputs/report.docx"
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
        file_path="/mnt/user-data/outputs/report.docx",
        max_chars=10000,
    )
    assert "chatdata-office-preview-bootstrap" in result["html"]
    assert result["truncated"] is False

    oversized = await service.render_html(
        runtime,
        file_path="/mnt/user-data/outputs/report.docx",
        max_chars=100,
    )
    assert oversized["html"] == ""
    assert oversized["truncated"] is True


def test_resolve_skill_snapshot_path_security_rules(tmp_path):
    """本地/沙箱共用的技能路径解析:隐藏名、穿越、前缀越界、symlink 全部拒绝。"""
    import pytest

    from utils.skill_paths import resolve_skill_snapshot_path

    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("demo", encoding="utf-8")

    target, visible = resolve_skill_snapshot_path(tmp_path, "/mnt/skills/demo/SKILL.md")
    assert target == (skill_dir / "SKILL.md").resolve()
    assert visible == "/mnt/skills/demo/SKILL.md"

    with pytest.raises(ValueError, match="技能路径无效"):
        resolve_skill_snapshot_path(tmp_path, "/mnt/skills/.hidden/x")
    with pytest.raises(ValueError, match="技能路径越界"):
        resolve_skill_snapshot_path(tmp_path, "/mnt/skills/demo/../../escape")
    with pytest.raises(ValueError, match="只允许访问技能路径"):
        resolve_skill_snapshot_path(tmp_path, "/mnt/user-data/x")
    with pytest.raises(ValueError, match="技能不存在"):
        resolve_skill_snapshot_path(tmp_path, "/mnt/skills/missing/x")


def test_resolve_workspace_path_opens_uploads_only_for_explicit_switch(monkeypatch, tmp_path):
    """uploads 写权限只能由人侧删除接口显式打开，模型工具链路始终被拒。"""
    paths = _isolated_workspace(monkeypatch, tmp_path, f"conv_uploads_switch_{uuid.uuid4().hex}")

    with pytest.raises(ValueError, match="只允许写入 workspace/、outputs/ 或 memory/"):
        resolve_workspace_path(paths["root"], "/mnt/user-data/uploads/a.txt", writable=True)

    target = resolve_workspace_path(
        paths["root"],
        "/mnt/user-data/uploads/a.txt",
        writable=True,
        allow_uploads_write=True,
    )

    assert target == Path(paths["uploads"]) / "a.txt"
    # 审计目录的保护不随开关放宽
    for audit_path in (
        "/mnt/user-data/outputs/query_result/result_1.json",
        "/mnt/user-data/outputs/.tool-offload/result_1.json",
    ):
        with pytest.raises(ValueError, match="系统结果目录"):
            resolve_workspace_path(
                paths["root"],
                audit_path,
                writable=True,
                allow_uploads_write=True,
            )


def test_delete_local_workspace_file_removes_single_file_in_all_three_roots(monkeypatch, tmp_path):
    paths = _isolated_workspace(monkeypatch, tmp_path, f"conv_delete_local_{uuid.uuid4().hex}")

    for directory in ("workspace", "outputs", "uploads"):
        target = Path(paths[directory]) / f"{directory}.txt"
        target.write_text("payload", encoding="utf-8")

        payload = workspace_local_backend_module.delete_local_workspace_file(
            paths["root"],
            f"/mnt/user-data/{directory}/{target.name}",
            allow_uploads=True,
        )

        assert payload == {"path": f"/mnt/user-data/{directory}/{target.name}", "type": "file"}
        assert not target.exists()


def test_delete_local_workspace_file_rejects_dirs_roots_and_audit_paths(monkeypatch, tmp_path):
    paths = _isolated_workspace(monkeypatch, tmp_path, f"conv_delete_guard_{uuid.uuid4().hex}")
    nested = Path(paths["workspace"]) / "reports"
    nested.mkdir()
    (nested / "a.txt").write_text("x", encoding="utf-8")
    audit = Path(paths["outputs"]) / "query_result" / "result_1.json"
    audit.parent.mkdir()
    audit.write_text("{}", encoding="utf-8")
    uploaded = Path(paths["uploads"]) / "a.txt"
    uploaded.write_text("x", encoding="utf-8")

    # 目录不通过人侧接口删除（递归删除需要独立确认交互）
    with pytest.raises(ValueError, match="不能作为file删除"):
        workspace_local_backend_module.delete_local_workspace_file(
            paths["root"],
            "/mnt/user-data/workspace/reports",
            allow_uploads=True,
        )
    for root_path in (
        "/mnt/user-data/workspace",
        "/mnt/user-data/outputs",
        "/mnt/user-data/uploads",
    ):
        with pytest.raises(ValueError, match="不允许操作工作区根目录"):
            workspace_local_backend_module.delete_local_workspace_file(
                paths["root"],
                root_path,
                allow_uploads=True,
            )
    with pytest.raises(ValueError, match="系统结果目录"):
        workspace_local_backend_module.delete_local_workspace_file(
            paths["root"],
            "/mnt/user-data/outputs/query_result/result_1.json",
            allow_uploads=True,
        )
    # 不开开关时（模型工具链路）uploads 同样不可删
    with pytest.raises(ValueError, match="只允许写入 workspace/、outputs/ 或 memory/"):
        workspace_local_backend_module.delete_local_workspace_file(
            paths["root"],
            "/mnt/user-data/uploads/a.txt",
        )

    assert audit.exists()
    assert nested.exists()
    assert uploaded.exists()


@pytest.mark.asyncio
async def test_has_running_agent_run_blocks_only_running_statuses(monkeypatch):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(conversation_workspace_module, "get_session", lambda: FakeSession())

    for status, expected in (
        ("pending", True),
        ("active", True),
        ("cancelling", True),
        # waiting_user 是挂起等待用户输入，Run 不在写盘，前端 runBusy 此刻也为 false
        ("waiting_user", False),
        ("finished", False),
    ):

        async def snapshot(_session, _conversation_id, _user_id, _status=status):
            return SimpleNamespace(status=_status)

        monkeypatch.setattr(
            conversation_workspace_module.agent_inbox_dao,
            "get_active_run_snapshot",
            snapshot,
        )
        assert await conversation_workspace_module._has_running_agent_run("conv_a", "user_a") is expected

    async def no_run(_session, _conversation_id, _user_id):
        return None

    monkeypatch.setattr(
        conversation_workspace_module.agent_inbox_dao,
        "get_active_run_snapshot",
        no_run,
    )
    assert await conversation_workspace_module._has_running_agent_run("conv_a", "user_a") is False


@pytest.mark.asyncio
async def test_delete_conversation_workspace_file_deletes_output_and_keeps_audit_result(monkeypatch, tmp_path):
    conversation_id = f"conv_delete_outputs_{uuid.uuid4().hex}"
    paths = _isolated_workspace(monkeypatch, tmp_path, conversation_id, created_date="2026-09-14")
    report = Path(paths["outputs"]) / "report.docx"
    report.write_bytes(b"docx")
    audit = Path(paths["outputs"]) / "query_result" / "result_1.json"
    audit.parent.mkdir()
    audit.write_text('{"rows":[1]}', encoding="utf-8")

    async def no_running_run(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_workspace_module, "_has_running_agent_run", no_running_run)

    async def fixed_date(*_args, **_kwargs):
        return "2026-09-14"

    monkeypatch.setattr(conversation_workspace_module, "get_conversation_created_date", fixed_date)

    payload = await conversation_workspace_module.delete_conversation_workspace_file(
        conversation_id,
        user_id="user_a",
        path="/mnt/user-data/outputs/report.docx",
    )

    assert payload == {
        "path": "/mnt/user-data/outputs/report.docx",
        "type": "file",
        "record_cleaned": False,
    }
    assert not report.exists()

    # 查询结果是审计留存：既不删文件，也不给「删了」的成功返回
    with pytest.raises(ValueError, match="留存审计"):
        await conversation_workspace_module.delete_conversation_workspace_file(
            conversation_id,
            user_id="user_a",
            path="/mnt/user-data/outputs/query_result/result_1.json",
        )
    assert audit.exists()


@pytest.mark.asyncio
async def test_delete_conversation_workspace_file_rejects_running_run(monkeypatch, tmp_path):
    conversation_id = f"conv_delete_running_{uuid.uuid4().hex}"
    paths = _isolated_workspace(monkeypatch, tmp_path, conversation_id)
    draft = Path(paths["workspace"]) / "draft.md"
    draft.write_text("draft", encoding="utf-8")

    async def running_run(*_args, **_kwargs):
        return True

    monkeypatch.setattr(conversation_workspace_module, "_has_running_agent_run", running_run)

    with pytest.raises(conversation_workspace_module.WorkspaceFileDeleteConflictError, match="正在执行"):
        await conversation_workspace_module.delete_conversation_workspace_file(
            conversation_id,
            user_id="user_a",
            path="/mnt/user-data/workspace/draft.md",
        )

    assert draft.exists()


@pytest.mark.asyncio
async def test_delete_conversation_workspace_file_cleans_uploaded_file_record(monkeypatch, tmp_path):
    conversation_id = f"conv_delete_uploads_{uuid.uuid4().hex}"
    paths = _isolated_workspace(monkeypatch, tmp_path, conversation_id)
    uploaded = Path(paths["uploads"]) / "月报.xlsx"
    uploaded.write_bytes(b"xlsx")
    parsed = Path(paths["uploads"]) / "月报.parsed.txt"
    parsed.write_text("parsed", encoding="utf-8")
    untouched = Path(paths["uploads"]) / "其他.xlsx"
    untouched.write_bytes(b"other")

    async def no_running_run(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_workspace_module, "_has_running_agent_run", no_running_run)
    deleted: list[tuple[str, str, str]] = []

    class FakeUploadedFileService:
        async def get_files_by_thread(self, thread_id, user_id="system"):
            assert (thread_id, user_id) == (conversation_id, "user_a")
            return [
                {"id": "file_1", "storage_dir": "月报.xlsx"},
                {"id": "file_2", "storage_dir": "其他.xlsx"},
            ]

        async def delete_file(self, file_id, user_id="system", thread_id=""):
            # 复刻真实实现：删原文件与解析产物，再软删记录
            deleted.append((file_id, user_id, thread_id))
            uploaded.unlink(missing_ok=True)
            parsed.unlink(missing_ok=True)
            return True

    monkeypatch.setattr(
        uploaded_file_service_module,
        "get_uploaded_file_service",
        lambda: FakeUploadedFileService(),
    )

    payload = await conversation_workspace_module.delete_conversation_workspace_file(
        conversation_id,
        user_id="user_a",
        path="/mnt/user-data/uploads/月报.xlsx",
    )

    assert deleted == [("file_1", "user_a", conversation_id)]
    assert payload == {
        "path": "/mnt/user-data/uploads/月报.xlsx",
        "type": "file",
        "record_cleaned": True,
    }
    assert not uploaded.exists()
    assert not parsed.exists()
    assert untouched.exists()


@pytest.mark.asyncio
async def test_delete_conversation_workspace_file_falls_back_when_upload_record_is_absent(monkeypatch, tmp_path):
    """uploads 里没有上传记录的条目（解析产物、子目录文件）走纯物理删除。"""
    conversation_id = f"conv_delete_uploads_fallback_{uuid.uuid4().hex}"
    paths = _isolated_workspace(monkeypatch, tmp_path, conversation_id, created_date="2026-09-14")
    orphan = Path(paths["uploads"]) / "parse-cache.txt"
    orphan.write_text("cache", encoding="utf-8")
    nested_dir = Path(paths["uploads"]) / "2026"
    nested_dir.mkdir()
    nested = nested_dir / "nested.csv"
    nested.write_text("a,b", encoding="utf-8")

    async def no_running_run(*_args, **_kwargs):
        return False

    monkeypatch.setattr(conversation_workspace_module, "_has_running_agent_run", no_running_run)

    async def fixed_date(*_args, **_kwargs):
        return "2026-09-14"

    monkeypatch.setattr(conversation_workspace_module, "get_conversation_created_date", fixed_date)

    class FakeUploadedFileService:
        async def get_files_by_thread(self, _thread_id, user_id="system"):
            return [{"id": "file_1", "storage_dir": "月报.xlsx"}]

        async def delete_file(self, *_args, **_kwargs):
            raise AssertionError("没有匹配记录时不应触碰上传记录")

    monkeypatch.setattr(
        uploaded_file_service_module,
        "get_uploaded_file_service",
        lambda: FakeUploadedFileService(),
    )

    orphan_payload = await conversation_workspace_module.delete_conversation_workspace_file(
        conversation_id,
        user_id="user_a",
        path="/mnt/user-data/uploads/parse-cache.txt",
    )
    nested_payload = await conversation_workspace_module.delete_conversation_workspace_file(
        conversation_id,
        user_id="user_a",
        path="/mnt/user-data/uploads/2026/nested.csv",
    )

    assert orphan_payload["record_cleaned"] is False
    assert nested_payload["record_cleaned"] is False
    assert not orphan.exists()
    assert not nested.exists()


# ---------------------------------------------------------------------------
# 目录打包下载（archive）
# ---------------------------------------------------------------------------


def test_archive_workspace_directory_packages_outputs(monkeypatch, tmp_path):
    paths = _isolated_workspace(monkeypatch, tmp_path, "conv_zip_a")
    outputs = Path(paths["root"]) / "outputs"
    (outputs / "模型").mkdir(parents=True, exist_ok=True)
    (outputs / "模型.yaml").write_text("name: 模型\n", encoding="utf-8")
    (outputs / "模型" / "明细.csv").write_text("id,amount\n1,10\n", encoding="utf-8")

    payload = conversation_workspace_module._archive_workspace_directory("conv_zip_a", "outputs", "user_a")

    assert payload["filename"] == "outputs.zip"
    assert payload["file_count"] == 2
    with zipfile.ZipFile(payload["temp_path"]) as zf:
        assert sorted(zf.namelist()) == ["outputs/模型.yaml", "outputs/模型/明细.csv"]
        assert zf.read("outputs/模型.yaml").decode("utf-8") == "name: 模型\n"
    os.unlink(payload["temp_path"])


def test_archive_excludes_system_result_and_internal_files(monkeypatch, tmp_path):
    paths = _isolated_workspace(monkeypatch, tmp_path, "conv_zip_b")
    root = Path(paths["root"])
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / "result.csv").write_text("visible", encoding="utf-8")
    (root / "outputs" / "query_result").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / "query_result" / "secret.csv").write_text("audit", encoding="utf-8")
    (root / "outputs" / ".tool-offload").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / ".tool-offload" / "blob.bin").write_bytes(b"\x00\x01")
    (root / "workspace" / "data_context.json").write_text("{}", encoding="utf-8")
    (root / "workspace" / "note.txt").write_text("other dir", encoding="utf-8")

    payload = conversation_workspace_module._archive_workspace_directory("conv_zip_b", "outputs", "user_a")

    with zipfile.ZipFile(payload["temp_path"]) as zf:
        assert zf.namelist() == ["outputs/result.csv"]
    os.unlink(payload["temp_path"])


def test_archive_rejects_unknown_and_empty_directory(monkeypatch, tmp_path):
    _isolated_workspace(monkeypatch, tmp_path, "conv_zip_c")

    with pytest.raises(ValueError, match="目录不支持打包"):
        conversation_workspace_module._archive_workspace_directory("conv_zip_c", "../uploads")

    with pytest.raises(FileNotFoundError, match="没有可打包的文件"):
        conversation_workspace_module._archive_workspace_directory("conv_zip_c", "outputs", "user_a")


def test_normalize_archive_directory_rejects_escape_variants():
    """目录参数是可控路径，逃逸写法必须全部拦在服务边界内（首段白名单 + 拒绝 ..）。"""
    normalize = conversation_workspace_module.normalize_archive_directory

    assert normalize("outputs") == "outputs"
    assert normalize("outputs/月报") == "outputs/月报"
    assert normalize("/outputs/月报/") == "outputs/月报"
    assert normalize("outputs\\月报") == "outputs/月报"

    for escaped in ("../uploads", "outputs/../../uploads", "..", "/../etc", "not_a_workspace_dir"):
        with pytest.raises(ValueError, match="目录不支持打包|目录不能为空"):
            normalize(escaped)


def test_archive_supports_subdirectory_and_rejects_escape(monkeypatch, tmp_path):
    """打包放开到 outputs 下的子目录：只收该子目录的文件，同时拒绝路径逃逸。

    目录参数变成用户可控的路径后，边界只能靠后端自己守：首段必须属于工作区目录，
    且不允许用 `..` 把打包范围指到 outputs 之外（否则 uploads 的原件会被打进 outputs 的包）。
    """
    paths = _isolated_workspace(monkeypatch, tmp_path, "conv_zip_sub")
    root = Path(paths["root"])
    (root / "outputs" / "月报").mkdir(parents=True, exist_ok=True)
    (root / "outputs" / "月报" / "明细.csv").write_text("id\n1\n", encoding="utf-8")
    (root / "outputs" / "other.txt").write_text("outside", encoding="utf-8")
    (root / "uploads" / "raw.txt").write_text("uploaded", encoding="utf-8")

    payload = conversation_workspace_module._archive_workspace_directory("conv_zip_sub", "outputs/月报", "user_a")

    assert payload["filename"] == "月报.zip", "zip 名取末段，不能把斜杠带进文件名"
    assert payload["file_count"] == 1
    with zipfile.ZipFile(payload["temp_path"]) as zf:
        assert zf.namelist() == ["outputs/月报/明细.csv"]
    os.unlink(payload["temp_path"])

    # 首尾斜杠与 Windows 分隔符归一到同一口径
    payload = conversation_workspace_module._archive_workspace_directory("conv_zip_sub", "/outputs\\月报/", "user_a")
    assert payload["filename"] == "月报.zip"
    os.unlink(payload["temp_path"])

    with pytest.raises(ValueError, match="目录不支持打包"):
        conversation_workspace_module._archive_workspace_directory("conv_zip_sub", "outputs/../uploads", "user_a")
    with pytest.raises(ValueError, match="目录不能为空"):
        conversation_workspace_module._archive_workspace_directory("conv_zip_sub", "  ", "user_a")


def test_archive_aborts_and_cleans_up_when_zip_exceeds_limit(monkeypatch, tmp_path):
    _isolated_workspace(monkeypatch, tmp_path, "conv_zip_d", "user_a")
    outputs = (
        Path(conversation_workspace_module.ensure_conversation_workspace("conv_zip_d", "user_a")["root"]) / "outputs"
    )
    outputs.mkdir(parents=True, exist_ok=True)
    # 随机字节不可压缩，8KB 源必然压出超过 512B 上限的产物
    (outputs / "raw.bin").write_bytes(os.urandom(8192))
    monkeypatch.setattr(conversation_workspace_module, "WORKSPACE_ARCHIVE_MAX_BYTES", 512)

    # 记录 mkstemp 实际拿到的临时文件路径，断言中止后被清理（不扫全局临时目录，
    # 避免混入其它运行的残留文件造成误判）
    created = {}
    original_mkstemp = tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        fd, path = original_mkstemp(*args, **kwargs)
        created["path"] = path
        return fd, path

    monkeypatch.setattr(conversation_workspace_module.tempfile, "mkstemp", recording_mkstemp)

    with pytest.raises(conversation_workspace_module.WorkspaceArchiveTooLargeError, match="已中止打包"):
        conversation_workspace_module._archive_workspace_directory("conv_zip_d", "outputs", "user_a")

    assert created["path"] and not os.path.exists(created["path"])


def test_archive_skips_files_deleted_between_listing_and_packaging(monkeypatch, tmp_path):
    paths = _isolated_workspace(monkeypatch, tmp_path, "conv_zip_e")
    outputs = Path(paths["root"]) / "outputs"
    (outputs.mkdir(parents=True, exist_ok=True))
    (outputs / "real.txt").write_text("real", encoding="utf-8")

    original_list = conversation_workspace_module.list_workspace_files

    def list_with_phantom(*args, **kwargs):
        files = original_list(*args, **kwargs)
        return files + [
            {
                "path": "outputs/ghost.txt",
                "relative_path": "outputs/ghost.txt",
                "directory": "outputs",
                "name": "ghost.txt",
                "size": 10,
                "mtime": 0,
                "source": "outputs",
            }
        ]

    monkeypatch.setattr(conversation_workspace_module, "list_workspace_files", list_with_phantom)

    payload = conversation_workspace_module._archive_workspace_directory("conv_zip_e", "outputs", "user_a")

    assert payload["file_count"] == 1
    with zipfile.ZipFile(payload["temp_path"]) as zf:
        assert zf.namelist() == ["outputs/real.txt"]
    os.unlink(payload["temp_path"])
