"""conversation_workspace.py 覆盖率补充测试。

聚焦既有 test_conversation_workspace.py 未触达的分支：技能快照解析异常、
输出技能目录导入校验、Office 预览（本地渲染 + 会话入口）、原始字节读取、
路径映射遮蔽、命令路径 token 校验与向后兼容 re-export。
全部使用 tmp_path / monkeypatch，不访问真实数据库与外部服务。
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REAL_MKSTEMP = tempfile.mkstemp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services.agent_run import conversation_workspace as cw
from apps.chat_data_app.agent.workspace import runtime as cw_runtime_module
from apps.chat_data_app.agent.workspace import runtime as cw_runtime_module
from apps.chat_data_app.services.agent_run.conversation_workspace import (
    ConversationWorkspaceRuntime,
    WorkspacePathMapping,
    _absolute_command_path_tokens,
    _ensure_workspace_root_in_chat_data_home,
    _officecli_selector_token_indices,
    _render_local_office_html,
    _validate_office_preview_source,
    can_access_conversation_workspace,
    create_workspace_backend,
    ensure_conversation_workspace,
    is_managed_semantic_model_workspace_path,
    list_conversation_workspace_files,
    list_workspace_files,
    read_conversation_query_result,
    read_conversation_workspace_file,
    read_conversation_workspace_raw_file,
    read_workspace_raw_file,
    render_conversation_workspace_office_preview,
    resolve_output_skill_directory,
    resolve_uploads_root,
    to_visible_path,
    workspace_runtime_from_state,
)


@pytest.fixture(autouse=True)
def _chat_data_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cw_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")


async def _stub_created_date_none(_conversation_id, *, user_id):
    """日期解析打桩：回退当前日期，避免单测触达真实数据库。"""
    del user_id
    return None


def _runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "threads" / "u1" / "conv_x" / "user-data"
    for name in ("workspace", "outputs", "uploads"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


class StubBackend:
    """满足 runtime_checkable WorkspaceBackend 协议的最小替身。"""

    name = "stub"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def read_text_file(self, runtime, **kwargs):
        self.calls.append(("read_text_file", kwargs))
        return {"content": "stub"}

    async def write_text_file(self, runtime, **kwargs):
        self.calls.append(("write_text_file", kwargs))
        return {"written": True}

    async def replace_text_file(self, runtime, **kwargs):
        self.calls.append(("replace_text_file", kwargs))
        return {"replaced": True}

    async def list_dir(self, runtime, **kwargs):
        self.calls.append(("list_dir", kwargs))
        return [{"path": "/mnt/user-data/workspace"}]

    async def create_dir(self, runtime, **kwargs):
        self.calls.append(("create_dir", kwargs))
        return {"path": kwargs["path"], "type": "dir"}

    async def delete_path(self, runtime, **kwargs):
        self.calls.append(("delete_path", kwargs))
        return {"deleted": kwargs["path"]}

    async def move_path(self, runtime, **kwargs):
        self.calls.append(("move_path", kwargs))
        return {"moved": kwargs["destination_path"]}

    async def copy_path(self, runtime, **kwargs):
        self.calls.append(("copy_path", kwargs))
        return {"copied": kwargs["destination_path"]}

    async def search_files(self, runtime, **kwargs):
        self.calls.append(("search_files", kwargs))
        return []

    async def glob_files(self, runtime, **kwargs):
        self.calls.append(("glob_files", kwargs))
        return []

    async def grep_text(self, runtime, **kwargs):
        self.calls.append(("grep_text", kwargs))
        return {"matches": []}

    async def read_bytes(self, runtime, **kwargs):
        self.calls.append(("read_bytes", kwargs))
        return b""

    async def write_internal_text_files(self, runtime, **kwargs):
        self.calls.append(("write_internal_text_files", kwargs))
        return []

    async def delete_internal_text_files(self, runtime, **kwargs):
        self.calls.append(("delete_internal_text_files", kwargs))
        return {"path": kwargs["path"], "type": "file"}

    async def run_command(self, runtime, **kwargs):
        self.calls.append(("run_command", kwargs))
        return (0, "", "")


def _stub_runtime(tmp_path: Path) -> tuple[ConversationWorkspaceRuntime, StubBackend]:
    backend = StubBackend()
    runtime = ConversationWorkspaceRuntime(
        workspace_root=str(_runtime_root(tmp_path)),
        user_id="u1",
        backend=backend,
    )
    return runtime, backend


# ---------------------------------------------------------------------------
# 运行时构造与技能快照
# ---------------------------------------------------------------------------


def test_runtime_rejects_non_backend_object(tmp_path):
    with pytest.raises(TypeError, match="backend 必须是"):
        ConversationWorkspaceRuntime(
            workspace_root=str(_runtime_root(tmp_path)),
            user_id="u1",
            backend=object(),
        )


def test_create_workspace_backend_returns_registered_backends():
    assert create_workspace_backend("local").name == "local"
    assert create_workspace_backend("sandbox").name == "sandbox"
    assert create_workspace_backend("  LOCAL ").name == "local"


@pytest.mark.asyncio
async def test_ensure_snapshot_async_swallows_build_failure(tmp_path, monkeypatch):
    root = _runtime_root(tmp_path)
    runtime = ConversationWorkspaceRuntime(workspace_root=str(root), user_id="u1", backend="local")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("snapshot build failed")

    monkeypatch.setattr(cw_runtime_module, "build_runtime_skill_snapshot_async", boom)
    # 构建失败只记日志，不向上抛出
    await runtime._ensure_snapshot_async()


@pytest.mark.asyncio
async def test_resolve_readonly_skill_path_validation_branches(tmp_path):
    root = _runtime_root(tmp_path)
    skill_dir = root.parent / "runtime-skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# Demo", encoding="utf-8")
    runtime = ConversationWorkspaceRuntime(workspace_root=str(root), user_id="u1", backend="local")

    with pytest.raises(ValueError, match="只允许访问技能路径"):
        await runtime.resolve_readonly_skill_path_async("/etc/passwd")
    with pytest.raises(ValueError, match="技能路径无效"):
        await runtime.resolve_readonly_skill_path_async("/mnt/skills/")
    with pytest.raises(ValueError, match="技能路径越界"):
        await runtime.resolve_readonly_skill_path_async("/mnt/skills/demo-skill/../escape.txt")


# ---------------------------------------------------------------------------
# 后端委托
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_delegates_directory_and_preview_operations(tmp_path, monkeypatch):
    runtime, backend = _stub_runtime(tmp_path)

    assert await runtime.list_dir(path="/mnt/user-data/workspace", max_depth=1) == [
        {"path": "/mnt/user-data/workspace"}
    ]
    assert await runtime.create_dir(path="/mnt/user-data/workspace/new") == {
        "path": "/mnt/user-data/workspace/new", "type": "dir",
    }
    assert await runtime.delete_path(path="/mnt/user-data/workspace/a.txt", recursive=False, expected_type="file") == {
        "deleted": "/mnt/user-data/workspace/a.txt"
    }
    assert await runtime.move_path(source_path="/mnt/user-data/workspace/a.txt", destination_path="/mnt/user-data/workspace/b.txt") == {
        "moved": "/mnt/user-data/workspace/b.txt"
    }
    assert await runtime.copy_path(source_path="/mnt/user-data/uploads/a.csv", destination_path="/mnt/user-data/workspace/a.csv") == {
        "copied": "/mnt/user-data/workspace/a.csv"
    }
    assert await runtime.search_files(path="/mnt/user-data/workspace", pattern="*.py", max_results=5) == []
    assert await runtime.read_text_file(file_path="/mnt/user-data/workspace/a.txt", offset=0, limit=10, max_chars=100) == {
        "content": "stub"
    }
    assert await runtime.write_text_file(file_path="/mnt/user-data/workspace/a.txt", content="x", mode="overwrite") == {
        "written": True
    }
    assert await runtime.replace_text_file(
        file_path="/mnt/user-data/workspace/a.txt", old_content="a", new_content="b",
    ) == {"replaced": True}
    assert await runtime.write_internal_text_files(entries=[("/mnt/user-data/workspace/semantic/m.yaml", "a: 1")]) == []
    assert await runtime.delete_internal_text_files(path="/mnt/user-data/workspace/semantic/m.yaml") == {
        "path": "/mnt/user-data/workspace/semantic/m.yaml", "type": "file",
    }
    assert await runtime.grep_text(
        path="/mnt/user-data/workspace", patterns=["x"], glob="**/*", max_results=5, max_chars=100,
    ) == {"matches": []}
    called = [name for name, _ in backend.calls]
    assert called == [
        "list_dir", "create_dir", "delete_path", "move_path", "copy_path",
        "search_files", "read_text_file", "write_text_file", "replace_text_file",
        "write_internal_text_files", "delete_internal_text_files", "grep_text",
    ]


@pytest.mark.asyncio
async def test_runtime_render_office_html_delegates_to_preview_service(tmp_path, monkeypatch):
    runtime, _backend = _stub_runtime(tmp_path)

    async def fake_render_html(_runtime, **kwargs):
        return {"renderer": "officecli-html", "html": "<html/>", "truncated": False, "echo": kwargs}

    monkeypatch.setattr(cw_runtime_module.workspace_office_preview_service, "render_html", fake_render_html)
    result = await runtime.render_office_html(file_path="/mnt/user-data/workspace/a.docx", max_chars=100)
    assert result["html"] == "<html/>"
    assert result["echo"] == {"file_path": "/mnt/user-data/workspace/a.docx", "max_chars": 100}


def test_workspace_runtime_from_state_requires_root():
    with pytest.raises(ValueError, match="缺少会话工作区"):
        workspace_runtime_from_state(None)
    with pytest.raises(ValueError, match="缺少会话工作区"):
        workspace_runtime_from_state({"workspace": {"workspace_root": "  "}})


# ---------------------------------------------------------------------------
# 路径解析与可见路径
# ---------------------------------------------------------------------------


def test_resolve_uploads_root_creates_workspace(tmp_path):
    uploads = resolve_uploads_root("user_a", "conv_upload")
    assert uploads.is_dir()
    assert uploads.name == "uploads"


def test_to_visible_path_outside_root_returns_physical(tmp_path):
    root = _runtime_root(tmp_path)
    inside = root / "workspace" / "a.txt"
    assert to_visible_path(str(root), inside) == "/mnt/user-data/workspace/a.txt"
    assert to_visible_path(str(root), Path("/somewhere/else/x.txt")) == "/somewhere/else/x.txt"


def test_is_managed_semantic_model_workspace_path(tmp_path):
    root = _runtime_root(tmp_path)
    semantic = root / "workspace" / "semantic"
    assert is_managed_semantic_model_workspace_path(str(root), semantic) is True
    assert is_managed_semantic_model_workspace_path(str(root), semantic / "m.yaml") is True
    assert is_managed_semantic_model_workspace_path(str(root), root / "workspace" / "a.txt") is False


def test_ensure_workspace_root_in_chat_data_home_rejects_outside(tmp_path, monkeypatch):
    monkeypatch.setattr(cw_runtime_module, "CHAT_DATA_HOME", str(tmp_path / "home"))
    _ensure_workspace_root_in_chat_data_home(str(tmp_path / "home" / "threads" / "u" / "c"))
    with pytest.raises(ValueError, match="不在 CHAT_DATA_HOME"):
        _ensure_workspace_root_in_chat_data_home("/tmp/definitely-outside-root/user-data")


# ---------------------------------------------------------------------------
# 输出技能目录导入
# ---------------------------------------------------------------------------


def _write_output_skill(root: Path, skill_name: str, metadata_name: str) -> Path:
    skill_dir = root / "outputs" / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {metadata_name}\ntitle: 演示技能\ndescription: 用于测试\n---\n# Demo\n",
        encoding="utf-8",
    )
    return skill_dir


def test_resolve_output_skill_directory_success(tmp_path):
    root = _runtime_root(tmp_path)
    skill_dir = _write_output_skill(root, "demo-skill", "demo-skill")
    assert cw_runtime_module.resolve_output_skill_directory(str(root), "/mnt/user-data/outputs/demo-skill") == skill_dir


def test_resolve_output_skill_directory_rejects_system_results(tmp_path):
    root = _runtime_root(tmp_path)
    offload = root / "outputs" / cw_runtime_module.TOOL_OFFLOAD_DIR_NAME / "demo-skill"
    _write_output_skill(offload, "demo-skill", "demo-skill")
    with pytest.raises(ValueError, match="不允许导入系统结果目录"):
        cw_runtime_module.resolve_output_skill_directory(str(root), f"/mnt/user-data/outputs/{cw_runtime_module.TOOL_OFFLOAD_DIR_NAME}/demo-skill")


def test_resolve_output_skill_directory_rejects_non_output_paths(tmp_path):
    root = _runtime_root(tmp_path)
    _write_output_skill(root, "demo-skill", "demo-skill")
    with pytest.raises(ValueError, match="只允许导入 /mnt/user-data/outputs"):
        cw_runtime_module.resolve_output_skill_directory(str(root), "/mnt/user-data/workspace/demo-skill")
    with pytest.raises(ValueError, match="只允许导入 /mnt/user-data/outputs"):
        cw_runtime_module.resolve_output_skill_directory(str(root), "/mnt/user-data/outputs/.hidden-skill")
    nested = _write_output_skill(root / "outputs" / "demo-skill", "nested", "nested")
    with pytest.raises(ValueError, match="只允许导入 /mnt/user-data/outputs"):
        cw_runtime_module.resolve_output_skill_directory(str(root), f"/mnt/user-data/outputs/demo-skill/{nested.name}")


def test_resolve_output_skill_directory_requires_valid_skill_md(tmp_path):
    root = _runtime_root(tmp_path)
    empty_dir = root / "outputs" / "no-skill"
    empty_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="必须包含有效的 SKILL.md"):
        cw_runtime_module.resolve_output_skill_directory(str(root), "/mnt/user-data/outputs/no-skill")

    mismatched = _write_output_skill(root, "other-skill", "different-name")
    with pytest.raises(ValueError, match="元数据无效或与技能目录名不一致"):
        cw_runtime_module.resolve_output_skill_directory(str(root), f"/mnt/user-data/outputs/{mismatched.name}")

    symlinked = root / "outputs" / "link-skill"
    symlinked.mkdir()
    (symlinked / "SKILL.md").symlink_to(mismatched / "SKILL.md")
    with pytest.raises(ValueError, match="必须包含有效的 SKILL.md"):
        cw_runtime_module.resolve_output_skill_directory(str(root), "/mnt/user-data/outputs/link-skill")


# ---------------------------------------------------------------------------
# WorkspacePathMapping 边界
# ---------------------------------------------------------------------------


def test_path_mapping_translate_and_mask_empty_text(tmp_path):
    mapping = WorkspacePathMapping(workspace_root=str(_runtime_root(tmp_path)))
    assert mapping.translate("") == ""
    assert mapping.mask("") == ""


def test_path_mapping_mask_skips_empty_skill_physical_path(tmp_path):
    mapping = WorkspacePathMapping(workspace_root=str(_runtime_root(tmp_path)))
    mapping._skill_physical["/mnt/skills/empty-skill"] = ""
    masked = mapping.mask("see /mnt/skills/empty-skill/SKILL.md")
    # 空物理路径不参与替换，避免把整段文本切碎
    assert "/mnt/skills/empty-skill/SKILL.md" in masked


def test_path_mapping_rejects_virtual_prefix_in_physical_root(tmp_path):
    with pytest.raises(ValueError, match="物理根不应包含 /mnt/user-data/"):
        WorkspacePathMapping(workspace_root="/mnt/user-data/workspace/chat_data/user-data")


# ---------------------------------------------------------------------------
# 工作区文件列表
# ---------------------------------------------------------------------------


def test_list_workspace_files_skips_missing_and_unresolvable(tmp_path, monkeypatch):
    root = ensure_conversation_workspace("conv_list", "user_a")["root"]
    (Path(root) / "workspace" / "ok.txt").write_text("ok", encoding="utf-8")

    monkeypatch.setattr(cw_runtime_module, "WORKSPACE_DIR_NAMES", ("workspace", "missing-dir"))

    original_resolve = cw_runtime_module.resolve_workspace_path

    def flaky_resolve(workspace_root, path, **kwargs):
        if str(path).endswith("bad.txt"):
            raise ValueError("boom")
        return original_resolve(workspace_root, path, **kwargs)

    (Path(root) / "workspace" / "bad.txt").write_text("bad", encoding="utf-8")
    monkeypatch.setattr(cw, "resolve_workspace_path", flaky_resolve)

    listed = list_workspace_files("conv_list", "user_a")
    assert [item["name"] for item in listed] == ["ok.txt"]
    assert listed[0]["path"] == "/mnt/user-data/workspace/ok.txt"


@pytest.mark.asyncio
async def test_async_workspace_wrappers_delegate(tmp_path, monkeypatch):
    root = ensure_conversation_workspace("conv_async", "user_a")["root"]
    (Path(root) / "workspace" / "a.txt").write_text("hello", encoding="utf-8")
    (Path(root) / "workspace" / "b.pdf").write_bytes(b"%PDF-1.4")
    # async wrapper 内部会解析会话创建月份，单测打桩避免触达真实 DB
    monkeypatch.setattr(cw, "get_conversation_created_date", _stub_created_date_none)

    files = await list_conversation_workspace_files("conv_async", user_id="user_a")
    assert {item["name"] for item in files} == {"a.txt", "b.pdf"}

    text = await read_conversation_workspace_file("conv_async", user_id="user_a", path="/mnt/user-data/workspace/a.txt")
    assert text["content"] == "hello"

    raw = await read_conversation_workspace_raw_file(
        "conv_async", user_id="user_a", path="/mnt/user-data/workspace/b.pdf",
    )
    assert raw["mime"] == "application/pdf"
    assert raw["data"] == b"%PDF-1.4"
    assert raw["relative_path"] == "workspace/b.pdf"


# ---------------------------------------------------------------------------
# 原始字节读取
# ---------------------------------------------------------------------------


def test_read_workspace_raw_file_branches(tmp_path):
    root = ensure_conversation_workspace("conv_raw", "user_a")["root"]
    target = Path(root) / "workspace" / "big.bin"
    target.write_bytes(b"x" * 100)

    with pytest.raises(ValueError, match="不在工作区文件预览范围内"):
        read_workspace_raw_file("conv_raw", f"/mnt/user-data/outputs/{cw_runtime_module.TOOL_OFFLOAD_DIR_NAME}/r.json", 100, "user_a")
    with pytest.raises(FileNotFoundError, match="文件不存在"):
        read_workspace_raw_file("conv_raw", "/mnt/user-data/workspace/missing.bin", 100, "user_a")
    with pytest.raises(ValueError, match="文件过大"):
        read_workspace_raw_file("conv_raw", "/mnt/user-data/workspace/big.bin", 10, "user_a")

    payload = read_workspace_raw_file("conv_raw", "/mnt/user-data/workspace/big.bin", 1024, "user_a")
    assert payload["size"] == 100
    assert payload["path"] == "/mnt/user-data/workspace/big.bin"


# ---------------------------------------------------------------------------
# Office 预览
# ---------------------------------------------------------------------------


def test_validate_office_preview_source_branches(tmp_path):
    root = str(_runtime_root(tmp_path))
    docx = Path(root) / "workspace" / "report.docx"
    docx.write_bytes(b"doc")
    _validate_office_preview_source(root, "/mnt/user-data/workspace/report.docx")

    with pytest.raises(ValueError, match="不支持 Office 预览"):
        _validate_office_preview_source(root, f"/mnt/user-data/outputs/{cw_runtime_module.TOOL_OFFLOAD_DIR_NAME}/r.docx")
    with pytest.raises(ValueError, match="仅支持 .docx"):
        _validate_office_preview_source(root, "/mnt/user-data/workspace/notes.txt")
    with pytest.raises(FileNotFoundError, match="文件不存在"):
        _validate_office_preview_source(root, "/mnt/user-data/workspace/missing.docx")

    huge = Path(root) / "workspace" / "huge.xlsx"
    with huge.open("wb") as handle:
        handle.truncate(51 * 1024 * 1024)
    with pytest.raises(ValueError, match="文件过大"):
        _validate_office_preview_source(root, "/mnt/user-data/workspace/huge.xlsx")


def _patch_officecli(monkeypatch, *, which_result, returncode, write_preview, stderr=""):
    created: list[str] = []

    def fake_mkstemp(**kwargs):
        # 始终使用标准库原始实现：cw_runtime_module.tempfile 就是全局 tempfile 模块，
        # 同一用例内多次 patch 时若从模块取会拿到上一轮的替身。
        fd, path = _REAL_MKSTEMP(**kwargs)
        os.close(fd)
        # 立刻删掉真实临时文件：让"OfficeCLI 未生成预览文件"分支可测，
        # 并返回一个可被被测代码安全 close 的替身 fd。
        os.unlink(path)
        created.append(path)
        return os.open(os.devnull, os.O_WRONLY), path

    class CompletedRun:
        def __init__(self):
            self.returncode = returncode
            self.stderr = stderr
            self.stdout = ""

    def fake_run(_argv, **_kwargs):
        path = created[-1]
        if write_preview:
            Path(path).write_text("<html>preview</html>", encoding="utf-8")
        return CompletedRun()

    monkeypatch.setattr(cw_runtime_module.shutil, "which", lambda _name: which_result)
    monkeypatch.setattr(cw_runtime_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cw_runtime_module.tempfile, "mkstemp", fake_mkstemp)
    return created


def test_render_local_office_html_requires_officecli(tmp_path, monkeypatch):
    root = str(_runtime_root(tmp_path))
    (Path(root) / "workspace" / "report.docx").write_bytes(b"doc")
    _patch_officecli(monkeypatch, which_result=None, returncode=0, write_preview=False)
    with pytest.raises(RuntimeError, match="未安装 OfficeCLI"):
        _render_local_office_html(root, "/mnt/user-data/workspace/report.docx", 100)


def test_render_local_office_html_rejects_bad_input(tmp_path, monkeypatch):
    root = str(_runtime_root(tmp_path))
    _patch_officecli(monkeypatch, which_result="/usr/bin/officecli", returncode=0, write_preview=False)
    with pytest.raises(ValueError, match="仅支持 .docx"):
        _render_local_office_html(root, "/mnt/user-data/workspace/notes.txt", 100)
    with pytest.raises(FileNotFoundError, match="文件不存在"):
        _render_local_office_html(root, "/mnt/user-data/workspace/missing.docx", 100)


def test_render_local_office_html_failure_paths(tmp_path, monkeypatch):
    root = str(_runtime_root(tmp_path))
    (Path(root) / "workspace" / "report.docx").write_bytes(b"doc")

    _patch_officecli(
        monkeypatch, which_result="/usr/bin/officecli", returncode=1,
        write_preview=False, stderr="boom",
    )
    with pytest.raises(RuntimeError, match="boom"):
        _render_local_office_html(root, "/mnt/user-data/workspace/report.docx", 100)

    _patch_officecli(
        monkeypatch, which_result="/usr/bin/officecli", returncode=1,
        write_preview=False, stderr="",
    )
    with pytest.raises(RuntimeError, match="OfficeCLI HTML 预览失败"):
        _render_local_office_html(root, "/mnt/user-data/workspace/report.docx", 100)

    _patch_officecli(
        monkeypatch, which_result="/usr/bin/officecli", returncode=0, write_preview=False,
    )
    with pytest.raises(RuntimeError, match="未生成预览文件"):
        _render_local_office_html(root, "/mnt/user-data/workspace/report.docx", 100)


def test_render_local_office_html_success_and_truncation(tmp_path, monkeypatch):
    root = str(_runtime_root(tmp_path))
    (Path(root) / "workspace" / "report.docx").write_bytes(b"doc")
    _patch_officecli(
        monkeypatch, which_result="/usr/bin/officecli", returncode=0, write_preview=True,
    )

    result = _render_local_office_html(root, "/mnt/user-data/workspace/report.docx", 1000)
    assert result["html"] == "<html>preview</html>"
    assert result["truncated"] is False

    truncated = _render_local_office_html(root, "/mnt/user-data/workspace/report.docx", 5)
    assert truncated["truncated"] is True and truncated["html"] == ""


@pytest.mark.asyncio
async def test_render_conversation_workspace_office_preview_branches(tmp_path, monkeypatch):
    ensure_conversation_workspace("conv_preview", "user_a")
    monkeypatch.setattr(cw, "get_conversation_created_date", _stub_created_date_none)
    monkeypatch.setattr(cw, "_validate_office_preview_source", lambda *_args, **_kwargs: None)

    async def fake_render(_runtime, **_kwargs):
        return fake_render.payload

    monkeypatch.setattr(cw_runtime_module.workspace_office_preview_service, "render_html", fake_render)

    fake_render.payload = {"html": "", "truncated": True}
    assert await render_conversation_workspace_office_preview(
        "conv_preview", user_id="user_a", path="/mnt/user-data/workspace/report.docx",
    ) == {"available": False, "reason": "preview_too_large"}

    fake_render.payload = {"html": "", "truncated": False}
    with pytest.raises(RuntimeError, match="未返回 HTML 预览内容"):
        await render_conversation_workspace_office_preview(
            "conv_preview", user_id="user_a", path="/mnt/user-data/workspace/report.docx",
        )

    fake_render.payload = {"html": "<html/>", "truncated": False}
    result = await render_conversation_workspace_office_preview(
        "conv_preview", user_id="user_a", path="/mnt/user-data/workspace/report.docx",
    )
    assert result["available"] is True and result["html"] == "<html/>"


# ---------------------------------------------------------------------------
# 查询结果卸载与访问校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_conversation_query_result_delegates(monkeypatch):
    captured = {}
    monkeypatch.setattr(cw, "get_conversation_created_date", _stub_created_date_none)

    class StubStore:
        def load_query_result_for_conversation(self, **kwargs):
            captured.update(kwargs)
            return {"result_id": kwargs["result_id"]}

    monkeypatch.setattr(cw, "ToolResultStore", StubStore)
    payload = await read_conversation_query_result(
        "conv_q", user_id="user_a", result_id="qr_1",
    )
    assert payload == {"result_id": "qr_1"}
    assert captured == {
        "result_id": "qr_1",
        "conversation_id": "conv_q",
        "user_id": "user_a",
        "created_date": None,
    }


@pytest.mark.asyncio
async def test_can_access_conversation_workspace_branches(monkeypatch):
    assert await can_access_conversation_workspace("", "user_a") is False
    assert await can_access_conversation_workspace("conv_1", "  ") is False

    async def fake_exists(**kwargs):
        return kwargs["conversation_id"] == "conv_ok"

    monkeypatch.setattr(cw.conversation_dao, "exists_user_agent_thread", fake_exists)
    assert await can_access_conversation_workspace("conv_ok", "user_a") is True
    assert await can_access_conversation_workspace("conv_no", "user_a") is False


# ---------------------------------------------------------------------------
# 命令路径 token 校验
# ---------------------------------------------------------------------------


def test_absolute_command_path_tokens_branches():
    assert _absolute_command_path_tokens("cat /mnt/user-data/workspace/a.txt") == [
        "/mnt/user-data/workspace/a.txt"
    ]
    with pytest.raises(ValueError, match="command 解析失败"):
        _absolute_command_path_tokens("cat 'unclosed")
    with pytest.raises(ValueError, match="不允许路径越界"):
        _absolute_command_path_tokens("cat /etc/../passwd")
    # <> 重定向占位与路径段一起被剥离后再判断
    assert _absolute_command_path_tokens("cat </dev/null") == ["/dev/null"]


def test_officecli_selector_token_indices_variants():
    assert _officecli_selector_token_indices(["python", "script.py"]) == set()
    assert _officecli_selector_token_indices(["officecli", "view", "a.docx"]) == set()
    # get/query/... 的第三个 token 是 DOM 选择器
    assert _officecli_selector_token_indices(
        ["officecli", "get", "/tmp/a.docx", "//w:p[1]"]
    ) == {3}
    # move：目标选择器 + --to/--after/--before 的后一个 token
    assert _officecli_selector_token_indices(
        ["officecli", "slide", "move", "/tmp/a.pptx", "//p:slide[2]", "--to", "//p:slide[9]"]
    ) == {4, 6}
    # swap：紧随其后的两个选择器
    assert _officecli_selector_token_indices(
        ["officecli", "slide", "swap", "/tmp/a.pptx", "//p:slide[1]", "//p:slide[2]"]
    ) == {4, 5}
    # 只有 officecli 没有子命令 → 不产生选择器索引
    assert _officecli_selector_token_indices(["officecli", "run", "/tmp/a.docx"]) == set()


def test_absolute_command_path_tokens_skips_officecli_selectors():
    paths = _absolute_command_path_tokens(
        "officecli get /tmp/a.docx /mnt/user-data/workspace/selector"
    )
    # 选择器位置不参与路径校验，因此不出现在结果里
    assert "/tmp/a.docx" in paths
    assert "/mnt/user-data/workspace/selector" not in paths


# ---------------------------------------------------------------------------
# 向后兼容 re-export
# ---------------------------------------------------------------------------


def test_module_getattr_re_export_and_unknown():
    assert cw.LocalWorkspaceBackend is not None
    assert cw.SandboxWorkspaceBackend is not None
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = cw.NoSuchBackendSymbol
