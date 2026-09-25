"""workspace 沙箱模块（原 workspace_execution.py，已拆分）覆盖率补充测试。

目标：覆盖 sandbox 会话缓存、Files API 包装、应用侧持久化同步、
命令执行与文件工具入口的未覆盖分支。全部使用 FakeSandbox / monkeypatch，
不访问真实 OpenSandbox 服务。
"""

import asyncio
from datetime import datetime
import fnmatch
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.workspace.workspace_sandbox_api import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
)
from apps.chat_data_app.agent.workspace import (
    workspace_ops as we_ops,
    workspace_sandbox_api as we_api,
    workspace_sandbox_paths as we_paths,
    workspace_search as we_search,
    workspace_session as we_session,
    workspace_sync as we_sync,
)


@pytest.fixture(autouse=True)
def _reset_session_caches():
    we_session._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    we_session._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()
    yield
    we_session._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    we_session._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


@pytest.fixture(autouse=True)
def _chat_data_home(tmp_path, monkeypatch):
    """让所有用例的工作区根目录都落在当前用例的 tmp_path 下。"""
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))


def _workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "chat-data" / "threads" / "user_a" / "conv_a" / "user-data"
    for name in ("workspace", "outputs", "outputs/.tool-offload", "uploads"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _make_session(sandbox, workspace_root: Path, **kwargs) -> SandboxExecutionSession:
    return SandboxExecutionSession(
        sandbox=sandbox,
        sandbox_id="sbx_1",
        workspace_root=str(workspace_root),
        **kwargs,
    )


def _ref(session: SandboxExecutionSession) -> SandboxExecutionSessionRef:
    ref = SandboxExecutionSessionRef()
    ref.current = session
    return ref


def _cache_key(workspace_root: Path) -> str:
    """会话缓存真实 key（与 get/attach 内部算法保持一致）。"""
    return we_session._workspace_session_cache_key(str(workspace_root))


class FakeFiles:
    """内存版 OpenSandbox Files API。"""

    def __init__(self, sandbox):
        self._sandbox = sandbox
        self.write_calls = []
        self.replace_calls = []
        self.delete_calls = []
        self.move_calls = []
        self.create_dir_calls = []
        self.search_calls = []

    async def write_file(self, path, data, mode=None):
        self.write_calls.append({"path": path, "mode": mode})
        self._sandbox.remote_files[path] = bytes(data)
        self._sandbox.remote_kinds[path] = "file"

    async def write_files(self, entries):
        for entry in entries:
            self.write_calls.append({"path": entry.path, "mode": entry.mode})
            self._sandbox.remote_files[entry.path] = entry.data
            self._sandbox.remote_kinds[entry.path] = "file"

    async def read_bytes(self, path, **kwargs):
        if path not in self._sandbox.remote_files:
            raise FileNotFoundError(path)
        raw = self._sandbox.remote_files[path]
        range_header = kwargs.get("range_header")
        if range_header:
            # 形如 "bytes=0-4095" 的字节区间采样
            span = str(range_header).split("=", 1)[-1]
            start_text, _, end_text = span.partition("-")
            start = int(start_text or 0)
            end = int(end_text) if end_text else len(raw) - 1
            return raw[start:end + 1]
        if "offset" in kwargs or "limit" in kwargs:
            # Files API 的 offset/limit 是按行（1 基）分页
            text = raw.decode("utf-8", errors="replace")
            lines = text.split("\n")
            if text.endswith("\n"):
                lines.pop()
            offset = max(1, int(kwargs.get("offset") or 1))
            limit = max(1, int(kwargs.get("limit") or 1))
            page = lines[offset - 1:offset - 1 + limit]
            if not page:
                return b""
            return ("\n".join(page) + "\n").encode("utf-8")
        return raw

    async def get_file_info(self, paths):
        result = {}
        for path in paths:
            if path in self._sandbox.remote_kinds:
                result[path] = SimpleNamespace(
                    path=path,
                    entry_type=self._sandbox.remote_kinds[path],
                    size=len(self._sandbox.remote_files.get(path, b"")),
                    modified_at=self._sandbox.modified_at,
                )
        return result

    async def list_directory(self, entry):
        path = str(getattr(entry, "path", "")).rstrip("/")
        depth = int(getattr(entry, "depth", 1))
        if path not in self._sandbox.remote_kinds or self._sandbox.remote_kinds[path] != "directory":
            raise FileNotFoundError(path)
        if depth <= 0:
            return []
        children = []
        for candidate in sorted(self._sandbox.remote_kinds):
            if not candidate.startswith(f"{path}/"):
                continue
            remainder = candidate[len(path) + 1:]
            if "/" in remainder and depth < remainder.count("/") + 1:
                continue
            children.append(SimpleNamespace(
                path=candidate,
                entry_type=self._sandbox.remote_kinds[candidate],
                size=len(self._sandbox.remote_files.get(candidate, b"")),
                modified_at=self._sandbox.modified_at,
            ))
        return children

    async def create_directories(self, entries):
        for entry in entries:
            self.create_dir_calls.append({"path": entry.path, "mode": entry.mode})
            self._sandbox.remote_kinds[entry.path] = "directory"

    async def delete_files(self, paths):
        self.delete_calls.append(("file", list(paths)))
        for path in paths:
            self._sandbox.remote_files.pop(path, None)
            self._sandbox.remote_kinds.pop(path, None)

    async def delete_directories(self, paths):
        self.delete_calls.append(("dir", list(paths)))
        for path in paths:
            for candidate in list(self._sandbox.remote_kinds):
                if candidate == path or candidate.startswith(f"{path}/"):
                    self._sandbox.remote_files.pop(candidate, None)
                    self._sandbox.remote_kinds.pop(candidate, None)

    async def move_files(self, entries):
        for entry in entries:
            # 真实 SDK 的 MoveEntry 字段是 src/dest（构造别名 source/destination）
            source = str(getattr(entry, "src", "") or "")
            destination = str(getattr(entry, "dest", "") or "")
            self.move_calls.append((source, destination))
            self._sandbox.remote_files[destination] = self._sandbox.remote_files.pop(source, b"")
            self._sandbox.remote_kinds[destination] = self._sandbox.remote_kinds.pop(source, "file")

    async def replace_contents(self, entries):
        for entry in entries:
            self.replace_calls.append((entry.path, entry.old_content, entry.new_content))
            self._sandbox.remote_files[entry.path] = self._sandbox.remote_files[entry.path].replace(
                entry.old_content.encode("utf-8"), entry.new_content.encode("utf-8"),
            )

    async def search(self, entry):
        self.search_calls.append((entry.path, entry.pattern))
        return [
            SimpleNamespace(path=path, entry_type="file")
            for path in sorted(self._sandbox.remote_kinds)
            if path.startswith(f"{str(entry.path).rstrip('/')}/")
            and fnmatch.fnmatch(path.split("/")[-1], str(entry.pattern))
        ]


class FakeCommands:
    def __init__(self, sandbox):
        self._sandbox = sandbox
        self.run_calls = []

    async def run(self, command, opts=None):
        self.run_calls.append(command)
        exit_code, stdout, stderr = self._sandbox.command_result
        error = None
        if exit_code == -1:
            error = SimpleNamespace(name="RuntimeError", value=stderr)
            exit_code = 0
        return SimpleNamespace(
            id=self._sandbox.next_command_id(),
            logs=SimpleNamespace(
                stdout=[SimpleNamespace(text=stdout)] if stdout else [],
                stderr=[SimpleNamespace(text=stderr)] if stderr else [],
            ),
            error=error,
        )

    async def get_command_status(self, command_id):
        if self._sandbox.status_error is not None:
            raise self._sandbox.status_error
        return SimpleNamespace(exit_code=self._sandbox.command_result[0] if self._sandbox.command_result[0] >= 0 else 1)


class FakeSandbox:
    def __init__(self):
        self.id = "sbx_fake"
        self.remote_files = {}
        self.remote_kinds = {}
        self.modified_at = datetime(2026, 8, 10)
        self.command_result = (0, "", "")
        self.status_error = None
        self._command_seq = 0
        self.files = FakeFiles(self)
        self.commands = FakeCommands(self)
        self.killed = 0
        self.closed = 0

    def next_command_id(self):
        self._command_seq += 1
        return f"cmd_{self._command_seq}"

    async def kill(self):
        self.killed += 1
        if self.kill_error is not None:
            raise self.kill_error

    kill_error = None

    async def close(self):
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error

    close_error = None


# ---------------------------------------------------------------------------
# 环境变量与配置辅助
# ---------------------------------------------------------------------------


def test_env_int_invalid_and_empty(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "abc")
    assert we_session._env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600) == 600
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "")
    assert we_session._env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600) == 600
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "0")
    assert we_session._env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600) == 1


def test_str_to_bool(monkeypatch):
    assert we_session._str_to_bool(None) is False
    assert we_session._str_to_bool("") is False
    assert we_session._str_to_bool(None, default=True) is True
    assert we_session._str_to_bool("ON") is True
    assert we_session._str_to_bool(" yes ") is True
    assert we_session._str_to_bool("0") is False


def test_workspace_session_ttl_seconds(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "600")
    assert we_session._workspace_session_ttl_seconds() == 300
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "40")
    assert we_session._workspace_session_ttl_seconds() == 30
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "1000")
    assert we_session._workspace_session_ttl_seconds() == 300


def test_workspace_session_lock_reuses_instance():
    lock_a = we_session._workspace_session_lock("key_a")
    lock_b = we_session._workspace_session_lock("key_a")
    assert lock_a is lock_b


def test_network_default_action(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "ALLOW")
    assert we_session._network_default_action() == "allow"
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "deny")
    assert we_session._network_default_action() == "deny"
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "bad")
    assert we_session._network_default_action() == "deny"


def test_network_allow_targets_dedup(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS", "a.com, b.com\na.com,,c.com")
    assert we_session._network_allow_targets() == ["a.com", "b.com", "c.com"]


def test_build_network_policy(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")
    assert we_session._build_network_policy() is None
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "deny")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS", "a.com")
    policy = we_session._build_network_policy()
    assert policy.default_action == "deny"
    assert [rule.target for rule in policy.egress] == ["a.com"]


def test_import_opensandbox_sdk_ok():
    Sandbox, ConnectionConfig, RunCommandOpts, models = we_api._import_opensandbox_sdk()
    assert Sandbox is not None and ConnectionConfig is not None
    assert RunCommandOpts is not None and len(models) == 2


def test_import_opensandbox_sdk_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "opensandbox", None)
    with pytest.raises(ValueError, match="OpenSandbox Python SDK"):
        we_api._import_opensandbox_sdk()


def test_import_filesystem_models_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "opensandbox.models.filesystem", None)
    with pytest.raises(ValueError, match="Files API models"):
        we_api._import_opensandbox_filesystem_models()
    with pytest.raises(ValueError, match="files.replace_contents"):
        we_api._import_opensandbox_content_replace_entry()
    with pytest.raises(ValueError, match="files.list_directory"):
        we_api._import_opensandbox_directory_list_entry()


def test_connection_config(monkeypatch):
    monkeypatch.delenv("OPENSANDBOX_URL", raising=False)
    with pytest.raises(ValueError, match="OPENSANDBOX_URL"):
        we_session._connection_config(10)
    monkeypatch.setenv("OPENSANDBOX_URL", "https://sandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_USE_SERVER_PROXY", "true")
    config = we_session._connection_config(10)
    assert config.domain == "https://sandbox.example.com"


def test_raise_sandbox_create_error_mapping():
    connect_error = httpx.ConnectError("boom")
    with pytest.raises(ValueError, match="不可达"):
        we_session._raise_sandbox_create_error(connect_error)
    timeout_error = httpx.ConnectTimeout("boom")
    with pytest.raises(ValueError, match="不可达"):
        we_session._raise_sandbox_create_error(timeout_error)
    wrapped = RuntimeError("wrapped") 
    wrapped.__cause__ = httpx.ReadTimeout("slow")
    with pytest.raises(ValueError, match="超时"):
        we_session._raise_sandbox_create_error(wrapped)
    with pytest.raises(KeyError):
        we_session._raise_sandbox_create_error(KeyError("other"))


# ---------------------------------------------------------------------------
# 应用侧路径与 manifest
# ---------------------------------------------------------------------------


def test_application_workspace_root_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    outside = tmp_path.parent / "outside-chat-data-home" / "user-data"
    with pytest.raises(ValueError, match="不在 CHAT_DATA_HOME"):
        we_paths._application_workspace_root(str(outside))
    with pytest.raises(ValueError, match="user-data"):
        we_paths._application_workspace_root(str(tmp_path / "other"))


def test_application_manifests(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    (root / "workspace" / "a.txt").write_text("aaa")
    (root / "workspace" / "semantic" / "model.yaml").parent.mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "semantic" / "model.yaml").write_text("model")
    (root / "outputs" / ".tool-offload" / "r.json").write_text("{}")
    (root / "uploads" / "u.csv").write_text("csv")

    full = we_sync._application_manifest(root)
    assert set(full) == {
        "workspace/a.txt", "workspace/semantic/model.yaml",
        "outputs/.tool-offload/r.json", "uploads/u.csv",
    }
    mutable = we_sync._mutable_application_manifest(root)
    assert set(mutable) == {"workspace/a.txt"}
    metadata = we_sync._mutable_application_metadata_manifest(root)
    assert set(metadata) == {"workspace/a.txt"}
    # fingerprint 至少包含 size 与 sha256
    size, _mtime, digest = mutable["workspace/a.txt"]
    assert size == 3 and len(digest) == 64


def test_iter_application_files_ignores_missing_root(tmp_path):
    assert we_sync._iter_application_files(tmp_path / "nope") == []
    assert we_sync._iter_application_directories(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Files API 包装
# ---------------------------------------------------------------------------


def test_write_sandbox_file_requires_writer():
    with pytest.raises(ValueError, match="files.write_file"):
        asyncio.run(we_api._write_sandbox_file(sandbox=SimpleNamespace(), path="/x", data=b"x"))


def test_write_and_read_sandbox_file():
    sandbox = FakeSandbox()
    asyncio.run(we_api._write_sandbox_file(
        sandbox=sandbox, path="/mnt/user-data/workspace/a.txt", data=b"hello",
    ))
    assert sandbox.remote_files["/mnt/user-data/workspace/a.txt"] == b"hello"
    data = asyncio.run(we_api._read_sandbox_file_bytes(
        sandbox=sandbox, path="/mnt/user-data/workspace/a.txt",
    ))
    assert data == b"hello"
    with pytest.raises(ValueError, match="files.read_bytes"):
        asyncio.run(we_api._read_sandbox_file_bytes(sandbox=SimpleNamespace(), path="/x"))


def test_replace_sandbox_file_content():
    sandbox = FakeSandbox()
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"old-new"
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    asyncio.run(we_api._replace_sandbox_file_content(
        sandbox=sandbox,
        path="/mnt/user-data/workspace/a.txt",
        old_content="old",
        new_content="brand",
    ))
    assert sandbox.remote_files["/mnt/user-data/workspace/a.txt"] == b"brand-new"
    with pytest.raises(ValueError, match="files.replace_contents"):
        asyncio.run(we_api._replace_sandbox_file_content(
            sandbox=SimpleNamespace(), path="/x", old_content="a", new_content="b",
        ))


def test_create_sandbox_directories():
    sandbox = FakeSandbox()
    asyncio.run(we_api._create_sandbox_directories(sandbox=sandbox, paths=[], mode=777))
    assert sandbox.files.create_dir_calls == []
    asyncio.run(we_api._create_sandbox_directories(
        sandbox=sandbox, paths=["/mnt/user-data/workspace", ""], mode=777,
    ))
    assert sandbox.files.create_dir_calls == [{"path": "/mnt/user-data/workspace", "mode": 777}]
    with pytest.raises(ValueError, match="files.create_directories"):
        asyncio.run(we_api._create_sandbox_directories(
            sandbox=SimpleNamespace(), paths=["/x"], mode=777,
        ))


def test_is_sandbox_descendant():
    assert we_paths._is_sandbox_descendant("/a/b", "/a") is True
    assert we_paths._is_sandbox_descendant("/a", "/a") is False
    assert we_paths._is_sandbox_descendant("/b/c", "/a") is False


def test_is_sandbox_file_not_found():
    assert we_paths._is_sandbox_file_not_found(FileNotFoundError()) is True
    assert we_paths._is_sandbox_file_not_found(SimpleNamespace(status_code=404)) is True
    assert we_paths._is_sandbox_file_not_found(RuntimeError("No such file or directory")) is True
    assert we_paths._is_sandbox_file_not_found(RuntimeError("permission denied")) is False


def test_is_sandbox_not_directory():
    assert we_paths._is_sandbox_not_directory(RuntimeError("Not a directory")) is True
    assert we_paths._is_sandbox_not_directory(RuntimeError("ENOTDIR")) is True
    assert we_paths._is_sandbox_not_directory(RuntimeError("other")) is False


def test_sandbox_visible_path_empty_returns_root():
    assert we_paths.sandbox_visible_path("") == "/mnt/user-data"
    assert we_paths.sandbox_visible_path(None) == "/mnt/user-data"


def test_sandbox_path_to_visible_mappings():
    assert we_paths.sandbox_path_to_visible("/mnt/user-data/workspace") == "/mnt/user-data/workspace"
    assert we_paths.sandbox_path_to_visible("/mnt/user-data/outputs/a.json") == "/mnt/user-data/outputs/a.json"
    assert we_paths.sandbox_path_to_visible("/mnt/skills/x/SKILL.md") == "/mnt/skills/x/SKILL.md"
    assert we_paths.sandbox_path_to_visible("/etc/passwd") == "/etc/passwd"


# ---------------------------------------------------------------------------
# _sandbox_path_info / _sandbox_path_kind
# ---------------------------------------------------------------------------


def test_sandbox_path_info_branches():
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"abc"
    entry = asyncio.run(we_api._sandbox_path_info(
        session=_make_session(sandbox, Path("/host/user-data")), path="/mnt/user-data/workspace/a.txt",
    ))
    assert getattr(entry, "size") == 3
    missing = asyncio.run(we_api._sandbox_path_info(
        session=_make_session(sandbox, Path("/host/user-data")), path="/mnt/user-data/workspace/missing",
    ))
    assert missing is None

    async def raise_not_found(_paths):
        raise FileNotFoundError("nope")

    weird = FakeSandbox()
    weird.files.get_file_info = raise_not_found
    assert asyncio.run(we_api._sandbox_path_info(
        session=_make_session(weird, Path("/host/user-data")), path="/x",
    )) is None

    with pytest.raises(ValueError, match="files.get_file_info"):
        asyncio.run(we_api._sandbox_path_info(
            session=_make_session(SimpleNamespace(), Path("/host/user-data")), path="/x",
        ))


def test_sandbox_path_kind_branches():
    session = _make_session(FakeSandbox(), Path("/host/user-data"))
    with pytest.raises(FileNotFoundError, match="路径不存在"):
        asyncio.run(we_api._sandbox_path_kind(session=session, path="/none"))
    symlink_session = _make_session(FakeSandbox(), Path("/host/user-data"))
    with pytest.raises(ValueError, match="符号链接"):
        asyncio.run(we_api._sandbox_path_kind(
            session=symlink_session, path="/x", entry=SimpleNamespace(entry_type="symlink"),
        ))
    assert asyncio.run(we_api._sandbox_path_kind(
        session=session, path="/x", entry=SimpleNamespace(entry_type="directory"),
    )) == "dir"
    assert asyncio.run(we_api._sandbox_path_kind(
        session=session, path="/x", entry=SimpleNamespace(entry_type="file"),
    )) == "file"


def test_sandbox_path_kind_falls_back_to_depth0_listing(monkeypatch):
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"

    async def lister(entry):
        if "notdir" in str(getattr(entry, "path", "")):
            raise RuntimeError("not a directory")
        return []

    monkeypatch.setattr(sandbox.files, "list_directory", lister)
    session = _make_session(sandbox, Path("/host/user-data"))
    assert asyncio.run(we_api._sandbox_path_kind(
        session=session, path="/mnt/user-data/workspace/notdir",
        entry=SimpleNamespace(entry_type=""),
    )) == "file"
    assert asyncio.run(we_api._sandbox_path_kind(
        session=session, path="/mnt/user-data/workspace",
        entry=SimpleNamespace(entry_type=""),
    )) == "dir"

    no_lister = SimpleNamespace()
    with pytest.raises(ValueError, match="files.list_directory"):
        asyncio.run(we_api._sandbox_path_kind(
            session=_make_session(no_lister, Path("/host/user-data")),
            path="/x", entry=SimpleNamespace(entry_type=""),
        ))


def test_list_sandbox_directory_entries(tmp_path):
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    session = _make_session(sandbox, _workspace_root(tmp_path))
    entries = asyncio.run(we_api._list_sandbox_directory_entries(
        session=session, path="/mnt/user-data/workspace",
    ))
    assert sorted(getattr(entry, "path") for entry in entries) == [
        "/mnt/user-data/workspace/a.txt", "/mnt/user-data/workspace/sub",
    ]

    async def not_found(_entry):
        raise FileNotFoundError("missing")

    sandbox2 = FakeSandbox()
    sandbox2.files.list_directory = not_found
    assert asyncio.run(we_api._list_sandbox_directory_entries(
        session=_make_session(sandbox2, Path("/host/user-data")), path="/x",
    )) == []
    with pytest.raises(ValueError, match="files.list_directory"):
        asyncio.run(we_api._list_sandbox_directory_entries(
            session=_make_session(SimpleNamespace(), Path("/host/user-data")), path="/x",
        ))


def test_walk_sandbox_tree(tmp_path):
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub/b.txt"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/link"] = "symlink"
    sandbox.remote_kinds[""] = "directory"  # 不会被遍历到
    files, directories = asyncio.run(we_api._walk_sandbox_tree(
        session=_make_session(sandbox, _workspace_root(tmp_path)),
        root="/mnt/user-data/workspace",
    ))
    assert [getattr(item, "path") for item in files] == [
        "/mnt/user-data/workspace/a.txt", "/mnt/user-data/workspace/sub/b.txt",
    ]
    assert [getattr(item, "path") for item in directories] == ["/mnt/user-data/workspace/sub"]


# ---------------------------------------------------------------------------
# 应用侧持久化
# ---------------------------------------------------------------------------


def test_application_path_for_sandbox_path_errors(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    session = _make_session(FakeSandbox(), root)
    with pytest.raises(ValueError, match="不属于当前会话"):
        we_sync._application_path_for_sandbox_path(session, "/etc/passwd")
    with pytest.raises(ValueError, match="不属于允许同步目录"):
        we_sync._application_path_for_sandbox_path(session, "/mnt/user-data/uploads/a.txt")
    with pytest.raises(ValueError, match="越界"):
        we_sync._application_path_for_sandbox_path(
            session, "/mnt/user-data/workspace/../../etc/passwd",
        )
    resolved = we_sync._application_path_for_sandbox_path(
        session, "/mnt/user-data/workspace/a.txt",
    )
    assert resolved == root / "workspace" / "a.txt"


def test_write_application_bytes_atomically(tmp_path):
    target = tmp_path / "nested" / "a.txt"
    we_sync._write_application_bytes_atomically(target, b"data")
    assert target.read_bytes() == b"data"


def test_delete_application_file_branches(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x")
    we_sync._delete_application_file(target)
    assert not target.exists()
    # 不存在的文件直接忽略
    we_sync._delete_application_file(tmp_path / "nope")
    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(ValueError, match="覆盖目录"):
        we_sync._delete_application_file(directory)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="符号链接"):
        we_sync._delete_application_file(link)
    link.unlink()


def test_ensure_application_paths_unchanged(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    local = root / "workspace" / "a.txt"
    local.write_text("v1")
    session = _make_session(FakeSandbox(), root)
    session.application_manifest = None
    we_sync._ensure_application_paths_unchanged(session=session, paths=[local])
    session.application_manifest = we_sync._mutable_application_manifest(root)
    we_sync._ensure_application_paths_unchanged(session=session, paths=[local])
    local.write_text("v2")
    with pytest.raises(ValueError, match="已变更，拒绝覆盖"):
        we_sync._ensure_application_paths_unchanged(session=session, paths=[local])


def test_persist_sandbox_paths_success_and_deletion(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace/changed.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/changed.txt"] = b"new"
    doomed = root / "workspace" / "doomed.txt"
    doomed.write_text("old")
    session = _make_session(sandbox, root)
    session.application_manifest = we_sync._mutable_application_manifest(root)

    asyncio.run(we_sync._persist_sandbox_paths(
        session=session,
        changed_paths=["/mnt/user-data/workspace/changed.txt"],
        deleted_paths=["/mnt/user-data/workspace/doomed.txt"],
    ))
    assert (root / "workspace" / "changed.txt").read_bytes() == b"new"
    assert not doomed.exists()
    assert session.pending_persistence is False


def test_persist_sandbox_paths_failure_marks_pending(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()

    async def boom(**_kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(we_sync, "_read_sandbox_file_bytes", boom)
    session = _make_session(sandbox, root)
    with pytest.raises(RuntimeError, match="download failed"):
        asyncio.run(we_sync._persist_sandbox_paths(
            session=session, changed_paths=["/mnt/user-data/workspace/x.txt"],
        ))
    assert session.pending_persistence is True


def test_recover_pending_persistence(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    session = _make_session(sandbox, root)
    called = []

    async def fake_sync(_session):
        called.append(_session)

    monkeypatch.setattr(we_sync, "_sync_sandbox_working_trees", fake_sync)
    asyncio.run(we_sync._recover_pending_persistence(session))
    assert called == []
    session.pending_persistence = True
    asyncio.run(we_sync._recover_pending_persistence(session))
    assert called == [session]


def test_sync_sandbox_working_trees_full_flow(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/outputs"] = "directory"
    # 远端已有 a.txt 与新目录 sub/
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"remote-a"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    # 本地：a.txt 已在远端；b.txt 远端没有（会被判删除）；olddir 本地目录远端没有
    (root / "workspace" / "a.txt").write_text("local-a")
    (root / "workspace" / "b.txt").write_text("local-b")
    (root / "workspace" / "olddir").mkdir()
    session = _make_session(sandbox, root)
    asyncio.run(we_sync.sync_sandbox_working_trees(session))
    assert (root / "workspace" / "a.txt").read_bytes() == b"remote-a"
    assert not (root / "workspace" / "b.txt").exists()
    assert (root / "workspace" / "sub").is_dir()
    assert not (root / "workspace" / "olddir").exists()
    # 应用侧工作区根目录不允许被目录同步覆盖
    with pytest.raises(ValueError, match="根目录"):
        we_sync._application_directory_for_sandbox_path(
            session, "/mnt/user-data/workspace",
        )


def test_sync_sandbox_uploads(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/uploads"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/uploads/stale.csv"] = "file"
    sandbox.remote_files["/mnt/user-data/uploads/stale.csv"] = b"old"
    session = _make_session(sandbox, root)
    session.uploads_manifest = {"uploads/stale.csv": (3, 1, "x")}
    # manifest 未变化时直接返回
    asyncio.run(we_sync._sync_sandbox_uploads(session))
    assert sandbox.files.write_calls == []

    (root / "uploads" / "new.csv").write_text("csv-data")
    asyncio.run(we_sync._sync_sandbox_uploads(session))
    written = {call["path"]: call["mode"] for call in sandbox.files.write_calls}
    assert written == {"/mnt/user-data/uploads/new.csv": 444}
    assert sandbox.files.delete_calls == [("file", ["/mnt/user-data/uploads/stale.csv"])]
    assert session.uploads_manifest["uploads/new.csv"][0] == 8


def test_sync_sandbox_uploads_requires_delete_files(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/uploads"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/uploads/stale.csv"] = "file"
    sandbox.files.delete_files = None
    session = _make_session(sandbox, root)
    with pytest.raises(ValueError, match="files.delete_files"):
        asyncio.run(we_sync._sync_sandbox_uploads(session))


def test_initialize_sandbox_filesystem(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    (root / "workspace" / "a.txt").write_text("data")
    (root / "uploads" / "u.csv").write_text("csv")
    skills = root.parent / "runtime-skills"
    (skills / "demo-skill").mkdir(parents=True, exist_ok=True)
    (skills / "demo-skill" / "SKILL.md").write_text("skill")
    sandbox = FakeSandbox()
    session = _make_session(sandbox, root, runtime_skills_dir=skills)
    asyncio.run(we_sync._initialize_sandbox_filesystem(session=session))
    written = {call["path"] for call in sandbox.files.write_calls}
    assert "/mnt/user-data/workspace/a.txt" in written
    assert "/mnt/user-data/uploads/u.csv" in written
    assert "/mnt/skills/demo-skill/SKILL.md" in written
    assert set(session.application_manifest) == {"workspace/a.txt"}
    assert set(session.uploads_manifest) == {"uploads/u.csv"}


# ---------------------------------------------------------------------------
# 命令执行
# ---------------------------------------------------------------------------


def test_extract_command_output_branches():
    execution = SimpleNamespace(
        logs=SimpleNamespace(stdout=[SimpleNamespace(text="out")], stderr=[SimpleNamespace(text="err")]),
        error=SimpleNamespace(name="RuntimeError", value="boom"),
    )
    exit_code, stdout, stderr = we_api._extract_command_output(execution, None)
    assert (exit_code, stdout) == (1, "out")
    assert "RuntimeError: boom" in stderr and "err" in stderr

    execution.error = None
    exit_code, stdout, stderr = we_api._extract_command_output(
        execution, None, status_lookup_error="lost",
    )
    assert exit_code == 1 and "未能确认" in stderr

    exit_code, _, _ = we_api._extract_command_output(
        execution, SimpleNamespace(exit_code=7),
    )
    assert exit_code == 7


def test_run_command_in_sandbox(tmp_path):
    sandbox = FakeSandbox()
    sandbox.command_result = (0, "hello", "")
    session = _make_session(sandbox, _workspace_root(tmp_path))
    exit_code, stdout, stderr = asyncio.run(we_api.run_command_in_sandbox(
        session=session, command="echo hello",
    ))
    assert (exit_code, stdout, stderr) == (0, "hello", "")
    assert sandbox.commands.run_calls == ["echo hello"]

    sandbox.status_error = RuntimeError("status lost")
    sandbox.command_result = (0, "x", "")
    exit_code, _, stderr = asyncio.run(we_api.run_command_in_sandbox(
        session=session, command="echo x",
    ))
    assert exit_code == 1 and "status lost" in stderr

    sandbox.status_error = None
    sandbox.command_result = (-1, "", "exploded")
    exit_code, _, stderr = asyncio.run(we_api.run_command_in_sandbox(
        session=session, command="bad",
    ))
    assert exit_code == 1 and "exploded" in stderr


# ---------------------------------------------------------------------------
# 会话缓存与生命周期
# ---------------------------------------------------------------------------


def test_require_sandbox_session():
    with pytest.raises(ValueError, match="未准备"):
        we_api.require_sandbox_session(None)
    with pytest.raises(ValueError, match="未准备"):
        we_api.require_sandbox_session(SandboxExecutionSessionRef())
    session = _make_session(FakeSandbox(), Path("/host/user-data"))
    assert we_api.require_sandbox_session(_ref(session)) is session


def _patch_prepare(monkeypatch, session):
    async def fake_prepare(*_args, **_kwargs):
        return session

    monkeypatch.setattr(we_session, "prepare_workspace_execution_session", fake_prepare)


def test_get_workspace_execution_session_cache_roundtrip(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    session = _make_session(FakeSandbox(), root)
    _patch_prepare(monkeypatch, session)
    synced = []

    async def matches(_session):
        return True

    async def fake_sync_uploads(_session):
        synced.append(1)

    monkeypatch.setattr(we_session, "_cached_session_matches_application", matches)
    monkeypatch.setattr(we_session, "_sync_sandbox_uploads", fake_sync_uploads)

    assert asyncio.run(we_session.get_workspace_execution_session(str(root))) is session
    assert asyncio.run(we_session.get_workspace_execution_session(str(root))) is session
    assert synced == [1]
    # 缓存过期后重新创建并释放旧会话
    cache_key = _cache_key(root)
    we_session._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key].expires_at = 0.0
    assert asyncio.run(we_session.get_workspace_execution_session(str(root))) is session
    assert session.sandbox.closed == 1


def test_get_workspace_execution_session_skips_mismatched_cache(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    stale = _make_session(FakeSandbox(), root)
    fresh = _make_session(FakeSandbox(), root)
    _patch_prepare(monkeypatch, fresh)

    async def always(_session):
        return False

    monkeypatch.setattr(we_session, "_cached_session_matches_application", always)
    we_session._WORKSPACE_EXECUTION_SESSION_CACHE[_cache_key(root)] = (
        we_session.CachedSandboxExecutionSession(session=stale, expires_at=1e12)
    )
    assert asyncio.run(we_session.get_workspace_execution_session(str(root))) is fresh
    assert stale.sandbox.closed == 1


def test_attach_and_invalidate_session(tmp_path):
    root = _workspace_root(tmp_path)
    cache_key = _cache_key(root)
    first = _make_session(FakeSandbox(), root)
    second = _make_session(FakeSandbox(), root)
    asyncio.run(we_session.attach_workspace_execution_session(None))
    asyncio.run(we_session.attach_workspace_execution_session(first))
    assert we_session._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key].session is first
    asyncio.run(we_session.attach_workspace_execution_session(second))
    assert first.sandbox.closed == 1
    assert we_session._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key].session is second
    asyncio.run(we_session.invalidate_workspace_execution_session(None))
    asyncio.run(we_session.invalidate_workspace_execution_session(second))
    assert cache_key not in we_session._WORKSPACE_EXECUTION_SESSION_CACHE
    assert second.sandbox.killed == 1 and second.sandbox.closed >= 1


def test_cleanup_and_release_session(tmp_path):
    root = _workspace_root(tmp_path)
    asyncio.run(we_session.cleanup_workspace_execution_session(None))
    asyncio.run(we_session.release_workspace_execution_session(None))
    session = _make_session(FakeSandbox(), root)
    asyncio.run(we_session.attach_workspace_execution_session(session)
                )
    session.sandbox.kill_error = RuntimeError("kill fail")
    session.sandbox.close_error = RuntimeError("close fail")
    asyncio.run(we_session.cleanup_workspace_execution_session(session))
    assert session.sandbox.killed == 1 and session.sandbox.closed == 1
    assert session.cache_key not in we_session._WORKSPACE_EXECUTION_SESSION_CACHE
    other = _make_session(FakeSandbox(), root)
    other.sandbox = None
    asyncio.run(we_session.cleanup_workspace_execution_session(other))
    asyncio.run(we_session.release_workspace_execution_session(other))


def test_prepare_workspace_execution_session_success(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENSANDBOX_URL", "https://sandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "deny")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS", "a.com")
    fake_sandbox = FakeSandbox()
    created = {}

    class FakeSdkSandbox:
        @staticmethod
        async def create(**kwargs):
            created.update(kwargs)
            return fake_sandbox

    monkeypatch.setattr("opensandbox.Sandbox", FakeSdkSandbox)
    session = asyncio.run(we_session.prepare_workspace_execution_session(str(root)))
    assert session.sandbox is fake_sandbox
    assert created["network_policy"].default_action == "deny"
    assert created["metadata"]["workspace_backend"] == "opensandbox"
    assert fake_sandbox.files.create_dir_calls  # 初始化建目录


def test_prepare_workspace_execution_session_failures(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENSANDBOX_URL", "https://sandbox.example.com")

    class TimeoutSdkSandbox:
        @staticmethod
        async def create(**_kwargs):
            await asyncio.sleep(60)

    real_asyncio = we_session.asyncio

    async def fast_timeout(_awaitable, timeout=None):
        _awaitable.close()
        raise TimeoutError()

    # 超时分支只在 context 内生效，避免污染后面的连接失败分支
    with monkeypatch.context() as m:
        m.setattr(we_session, "asyncio", SimpleNamespace(
            wait_for=fast_timeout, to_thread=real_asyncio.to_thread, Lock=real_asyncio.Lock,
        ))
        m.setattr("opensandbox.Sandbox", TimeoutSdkSandbox)
        with pytest.raises(ValueError, match="创建超时"):
            asyncio.run(we_session.prepare_workspace_execution_session(str(root)))

    class ConnectErrorSdkSandbox:
        @staticmethod
        async def create(**_kwargs):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr("opensandbox.Sandbox", ConnectErrorSdkSandbox)
    with pytest.raises(ValueError, match="不可达"):
        asyncio.run(we_session.prepare_workspace_execution_session(str(root)))


def test_prepare_workspace_execution_session_cleans_up_on_init_failure(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENSANDBOX_URL", "https://sandbox.example.com")
    fake_sandbox = FakeSandbox()

    async def bad_write_file(*_args, **_kwargs):
        raise RuntimeError("write failed")

    original_create = fake_sandbox.files.create_directories

    async def failing_create(*_args, **_kwargs):
        await original_create(_args[0]) if False else None
        raise RuntimeError("init failed")

    fake_sandbox.files.create_directories = failing_create

    class FakeSdkSandbox:
        @staticmethod
        async def create(**_kwargs):
            return fake_sandbox

    monkeypatch.setattr("opensandbox.Sandbox", FakeSdkSandbox)
    with pytest.raises(RuntimeError, match="init failed"):
        asyncio.run(we_session.prepare_workspace_execution_session(str(root)))
    assert fake_sandbox.killed == 1 and fake_sandbox.closed == 1
    _ = bad_write_file  # 保持可读性
    _ = original_create


# ---------------------------------------------------------------------------
# 文件工具入口
# ---------------------------------------------------------------------------


def _session_with_root(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(we_paths, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/outputs"] = "directory"
    session = _make_session(sandbox, root)
    return root, sandbox, session


def test_sandbox_artifact_payload_branches(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    ref = _ref(session)
    sandbox.command_result = (0, "1", "")
    assert asyncio.run(we_ops.sandbox_artifact_payload(
        session_ref=ref, file_path="/mnt/user-data/outputs/.tool-offload/x.json",
    )) == {}
    assert asyncio.run(we_ops.sandbox_artifact_payload(
        session_ref=ref, file_path="/mnt/user-data/workspace/missing.txt",
    )) == {}
    sandbox.remote_kinds["/mnt/user-data/outputs/report.md"] = "file"
    sandbox.remote_files["/mnt/user-data/outputs/report.md"] = "# 结论".encode("utf-8")
    payload = asyncio.run(we_ops.sandbox_artifact_payload(
        session_ref=ref, file_path="/mnt/user-data/outputs/report.md",
    ))
    assert payload["artifact_type"] == "markdown"
    assert payload["file_path"] == "/mnt/user-data/outputs/report.md"
    no_session = SandboxExecutionSessionRef()
    with pytest.raises(ValueError, match="未准备"):
        asyncio.run(we_ops.sandbox_artifact_payload(
            session_ref=no_session, file_path="/mnt/user-data/outputs/chart.json",
        ))


def test_render_office_html_rejects_suffix(tmp_path, monkeypatch):
    _root, _sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=".docx、.xlsx、.pptx"):
        asyncio.run(we_ops.sandbox_render_office_html(
            session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt", max_chars=100,
        ))


def test_render_office_html_success_and_truncation(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/report.docx"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/report.docx"] = b"doc"

    def _preview_path_from(command: str) -> str:
        return command.split("--out ", 1)[1].strip().strip("'")

    async def fake_run(*, session, command, timeout_seconds=30):
        assert "officecli" in command
        preview_path = _preview_path_from(command)
        html = "<html>small</html>" if "report" in command else "x"
        sandbox.remote_kinds[preview_path] = "file"
        sandbox.remote_files[preview_path] = html.encode()
        return 0, "", ""

    async def fake_path_info(*, session, path):
        return SimpleNamespace(size=len(sandbox.remote_files.get(path, b"")))

    monkeypatch.setattr(we_ops, "run_command_in_sandbox", fake_run)
    monkeypatch.setattr(we_ops, "_sandbox_path_info", fake_path_info)
    result = asyncio.run(we_ops.sandbox_render_office_html(
        session_ref=_ref(session), file_path="/mnt/user-data/workspace/report.docx", max_chars=100,
    ))
    assert result["html"] == "<html>small</html>" and result["truncated"] is False

    async def failing_run(*, session, command, timeout_seconds=30):
        return 1, "", "officecli exploded"

    monkeypatch.setattr(we_ops, "run_command_in_sandbox", failing_run)
    with pytest.raises(RuntimeError, match="officecli exploded"):
        asyncio.run(we_ops.sandbox_render_office_html(
            session_ref=_ref(session), file_path="/mnt/user-data/workspace/report.docx", max_chars=100,
        ))


def test_sandbox_read_text_file_variants(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    ref = _ref(session)
    sandbox.command_result = (0, "2", "")

    missing = asyncio.run(we_ops.sandbox_read_text_file(
        session_ref=ref, file_path="/mnt/user-data/workspace/missing.txt", offset=0, limit=10, max_chars=100,
    ))
    assert missing["exists"] is False

    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    is_dir = asyncio.run(we_ops.sandbox_read_text_file(
        session_ref=ref, file_path="/mnt/user-data/workspace/sub", offset=0, limit=10, max_chars=100,
    ))
    assert is_dir["is_dir"] is True

    sandbox.remote_kinds["/mnt/user-data/workspace/bin.dat"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/bin.dat"] = b"a\x00b"
    binary = asyncio.run(we_ops.sandbox_read_text_file(
        session_ref=ref, file_path="/mnt/user-data/workspace/bin.dat", offset=0, limit=10, max_chars=100,
    ))
    assert binary["is_binary"] is True

    sandbox.remote_kinds["/mnt/user-data/workspace/text.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/text.txt"] = b"one\ntwo\n"
    result = asyncio.run(we_ops.sandbox_read_text_file(
        session_ref=ref, file_path="/mnt/user-data/workspace/text.txt", offset=0, limit=1, max_chars=10000,
    ))
    assert result["lines"] == ["one"]
    assert result["has_more"] is True
    assert result["total_lines"] == 2
    # 行数统计缓存命中
    assert session.text_line_counts["/mnt/user-data/workspace/text.txt"][2] == 2

    # 越界偏移返回空页（不带 lines 字段，用 offset_out_of_range 表达）
    empty = asyncio.run(we_ops.sandbox_read_text_file(
        session_ref=ref, file_path="/mnt/user-data/workspace/text.txt", offset=99, limit=10, max_chars=100,
    ))
    assert empty.get("lines", []) == [] and empty["offset_out_of_range"] is True

    # reader 缺失
    readerless = FakeSandbox()
    readerless.remote_kinds["/mnt/user-data/workspace/text.txt"] = "file"
    readerless.remote_files["/mnt/user-data/workspace/text.txt"] = b"one\ntwo\n"
    readerless.files.read_bytes = None
    session2 = _make_session(readerless, root)
    with pytest.raises(ValueError, match="files.read_bytes"):
        asyncio.run(we_ops.sandbox_read_text_file(
            session_ref=_ref(session2), file_path="/mnt/user-data/workspace/text.txt",
            offset=0, limit=1, max_chars=100,
        ))


def test_sandbox_text_line_count_errors(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    entry = SimpleNamespace(size=4, modified_at="m")
    sandbox.command_result = (1, "", "awk failed")
    with pytest.raises(RuntimeError, match="awk failed"):
        asyncio.run(we_ops._sandbox_text_line_count(
            session=session, path="/mnt/user-data/workspace/x.txt", entry=entry,
        ))
    sandbox.command_result = (0, "not-a-number", "")
    with pytest.raises(RuntimeError, match="无效"):
        asyncio.run(we_ops._sandbox_text_line_count(
            session=session, path="/mnt/user-data/workspace/x.txt", entry=entry,
        ))
    # 元数据未变时命中缓存
    session.text_line_counts["/mnt/user-data/workspace/x.txt"] = (4, "m", 7)
    sandbox.command_result = (1, "", "should not run")
    assert asyncio.run(we_ops._sandbox_text_line_count(
        session=session, path="/mnt/user-data/workspace/x.txt", entry=entry,
    )) == 7


def test_sandbox_list_dir(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"abc"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    entries = asyncio.run(we_ops.sandbox_list_dir(
        session_ref=_ref(session), path="/mnt/user-data/workspace", max_depth=1,
    ))
    assert entries == [
        {"type": "dir", "path": "/mnt/user-data/workspace/sub/", "size": "-", "mtime": "2026-08-10 00:00"},
        {"type": "file", "path": "/mnt/user-data/workspace/a.txt", "size": 3, "mtime": "2026-08-10 00:00"},
    ]
    with pytest.raises(ValueError, match="不是目录"):
        asyncio.run(we_ops.sandbox_list_dir(
            session_ref=_ref(session), path="/mnt/user-data/workspace/a.txt", max_depth=1,
        ))
    no_lister_sandbox = FakeSandbox()
    no_lister_sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    no_lister_sandbox.files.list_directory = None
    no_lister = _make_session(no_lister_sandbox, root)
    with pytest.raises(ValueError, match="files.list_directory"):
        asyncio.run(we_ops.sandbox_list_dir(
            session_ref=_ref(no_lister), path="/mnt/user-data/workspace", max_depth=1,
        ))


def test_sandbox_create_dir(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    result = asyncio.run(we_ops.sandbox_create_dir(
        session_ref=_ref(session), path="/mnt/user-data/workspace/newdir",
    ))
    assert result == {"path": "/mnt/user-data/workspace/newdir", "type": "dir"}
    assert "/mnt/user-data/workspace/newdir" in sandbox.remote_kinds
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(we_ops.sandbox_create_dir(
            session_ref=_ref(session), path="/mnt/user-data/workspace/newdir",
        ))
    with pytest.raises(ValueError, match="根目录"):
        asyncio.run(we_ops.sandbox_create_dir(
            session_ref=_ref(session), path="/mnt/user-data/workspace",
        ))


def test_sandbox_delete_path(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub/inner.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/sub/inner.txt"] = b"inner"

    with pytest.raises(ValueError, match="不能作为dir删除"):
        asyncio.run(we_ops.sandbox_delete_path(
            session_ref=_ref(session), path="/mnt/user-data/workspace/a.txt", recursive=False, expected_type="dir",
        ))
    with pytest.raises(ValueError, match="目录非空"):
        asyncio.run(we_ops.sandbox_delete_path(
            session_ref=_ref(session), path="/mnt/user-data/workspace/sub", recursive=False, expected_type="dir",
        ))
    result = asyncio.run(we_ops.sandbox_delete_path(
        session_ref=_ref(session), path="/mnt/user-data/workspace/a.txt", recursive=False, expected_type="file",
    ))
    assert result["type"] == "file"
    result = asyncio.run(we_ops.sandbox_delete_path(
        session_ref=_ref(session), path="/mnt/user-data/workspace/sub", recursive=True, expected_type="dir",
    ))
    assert result["type"] == "dir"
    assert "/mnt/user-data/workspace/sub" not in sandbox.remote_kinds

    no_deleter_sandbox = FakeSandbox()
    no_deleter_sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    no_deleter_sandbox.remote_kinds["/mnt/user-data/workspace/b.txt"] = "file"
    no_deleter_sandbox.remote_files["/mnt/user-data/workspace/b.txt"] = b"y"
    no_deleter_sandbox.files.delete_files = None
    no_deleter = _make_session(no_deleter_sandbox, root)
    with pytest.raises(ValueError, match="删除 Files API"):
        asyncio.run(we_ops.sandbox_delete_path(
            session_ref=_ref(no_deleter), path="/mnt/user-data/workspace/b.txt", recursive=False, expected_type="file",
        ))


def test_sandbox_move_path(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"
    result = asyncio.run(we_ops.sandbox_move_path(
        session_ref=_ref(session),
        source_path="/mnt/user-data/workspace/a.txt",
        destination_path="/mnt/user-data/workspace/b.txt",
    ))
    assert result["type"] == "file"
    assert sandbox.files.move_calls == [(
        "/mnt/user-data/workspace/a.txt", "/mnt/user-data/workspace/b.txt",
    )]
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(we_ops.sandbox_move_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/workspace/b.txt",
            destination_path="/mnt/user-data/workspace/b.txt",
        ))
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"
    sandbox.remote_kinds["/mnt/user-data/workspace/c.txt"] = "file"
    with pytest.raises(ValueError, match="父路径不是目录"):
        asyncio.run(we_ops.sandbox_move_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/workspace/c.txt",
            destination_path="/mnt/user-data/workspace/a.txt/deep.txt",
        ))
    no_mover_sandbox = FakeSandbox()
    no_mover_sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    no_mover_sandbox.remote_kinds["/mnt/user-data/workspace/c.txt"] = "file"
    no_mover_sandbox.remote_files["/mnt/user-data/workspace/c.txt"] = b"x"
    no_mover_sandbox.files.move_files = None
    no_mover = _make_session(no_mover_sandbox, root)
    with pytest.raises(ValueError, match="files.move_files"):
        asyncio.run(we_ops.sandbox_move_path(
            session_ref=_ref(no_mover),
            source_path="/mnt/user-data/workspace/c.txt",
            destination_path="/mnt/user-data/workspace/d.txt",
        ))


def test_sandbox_copy_path(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/uploads/src.csv"] = "file"
    sandbox.remote_files["/mnt/user-data/uploads/src.csv"] = b"csv"
    with pytest.raises(ValueError, match="不能复制"):
        asyncio.run(we_ops.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/outputs/.tool-offload/r.json",
            destination_path="/mnt/user-data/workspace/r.json",
        ))
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    with pytest.raises(ValueError, match="只支持复制单个文件"):
        asyncio.run(we_ops.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/workspace/sub",
            destination_path="/mnt/user-data/workspace/sub2",
        ))
    result = asyncio.run(we_ops.sandbox_copy_path(
        session_ref=_ref(session),
        source_path="/mnt/user-data/uploads/src.csv",
        destination_path="/mnt/user-data/workspace/src.csv",
    ))
    assert result["bytes"] == 3
    assert (root / "workspace" / "src.csv").read_bytes() == b"csv"
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(we_ops.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/uploads/src.csv",
            destination_path="/mnt/user-data/workspace/src.csv",
        ))
    sandbox.remote_kinds["/mnt/user-data/uploads/other.csv"] = "file"
    with pytest.raises(ValueError, match="父路径不是目录"):
        asyncio.run(we_ops.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/uploads/other.csv",
            destination_path="/mnt/user-data/workspace/src.csv/deep.csv",
        ))


def test_sandbox_search_files(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.csv"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/b.csv"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/c.txt"] = "file"
    results = asyncio.run(we_ops.sandbox_search_files(
        session_ref=_ref(session), path="/mnt/user-data/workspace", pattern="*.csv", max_results=1,
    ))
    assert results == [{"path": "/mnt/user-data/workspace/a.csv", "type": "file"}]
    no_search = _make_session(SimpleNamespace(files=SimpleNamespace()), root)
    with pytest.raises(ValueError, match="files.search"):
        asyncio.run(we_ops.sandbox_search_files(
            session_ref=_ref(no_search), path="/mnt/user-data/workspace", pattern="*", max_results=10,
        ))


def test_matches_sandbox_glob():
    assert we_search._matches_sandbox_glob("a.txt", "**/*") is True
    assert we_search._matches_sandbox_glob("a.txt", "*.txt") is True
    assert we_search._matches_sandbox_glob("a.txt", "**/*.csv") is False


def test_build_rg_command_str():
    command = we_search.build_rg_command_str(
        "/mnt/user-data/workspace", ["hello world", "foo"], glob="**/*.py",
    )
    assert command.startswith("rg -F --json --hidden")
    assert "--glob '**/*.py'" in command
    assert command.endswith("-e 'hello world' -e foo")


def test_parse_rg_output():
    stdout = "\n".join([
        '{"type": "summary"}',
        'not json',
        '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/a.py"}, "line_number": 3, "lines": {"text": "hello\\n"}}}',
        '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace"}, "line_number": 1, "lines": {"text": "root"}}}',
        '{"type": "match", "data": {"path": {"text": "/other/x.py"}, "line_number": 1, "lines": {"text": "skip"}}}',
        '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/b.py"}}}',
        '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/c.py"}, "line_number": 2, "lines": {"text": "tail"}}}',
    ])
    matches, has_more = we_search.parse_rg_output(
        stdout, "/mnt/user-data/workspace", max_results=2, max_chars=10000,
    )
    assert [item["file"] for item in matches] == ["a.py", ""]
    assert has_more is True


def test_sandbox_grep_text(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def run_127(*, session, command, timeout_seconds=30):
        return 127, "", "rg: command not found"

    monkeypatch.setattr(we_search, "run_command_in_sandbox", run_127)
    with pytest.raises(RuntimeError, match="ripgrep"):
        asyncio.run(we_search.sandbox_grep_text(
            session_ref=_ref(session), path="/mnt/user-data/workspace", patterns=["x"],
            glob="**/*", max_results=10, max_chars=1000,
        ))

    async def run_fail(*, session, command, timeout_seconds=30):
        return 2, "", "boom"

    monkeypatch.setattr(we_search, "run_command_in_sandbox", run_fail)
    with pytest.raises(RuntimeError, match="rg 执行失败"):
        asyncio.run(we_search.sandbox_grep_text(
            session_ref=_ref(session), path="/mnt/user-data/workspace", patterns=["x"],
            glob="**/*", max_results=10, max_chars=1000,
        ))

    async def run_ok(*, session, command, timeout_seconds=30):
        line = '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/a.py"}, "line_number": 5, "lines": {"text": "hello"}}}'
        return 0, line, ""

    monkeypatch.setattr(we_search, "run_command_in_sandbox", run_ok)
    payload = asyncio.run(we_search.sandbox_grep_text(
        session_ref=_ref(session), path="/mnt/user-data/workspace", patterns=["hello"],
        glob="**/*", max_results=10, max_chars=1000,
    ))
    assert payload["matches"] == [
        {"path": "/mnt/user-data/workspace/a.py", "line_number": 5, "line": "hello"},
    ]
    assert payload["has_more"] is False


def test_sandbox_write_text_file(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="只支持 overwrite"):
        asyncio.run(we_ops.sandbox_write_text_file(
            session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt", content="x", mode="append",
        ))
    result = asyncio.run(we_ops.sandbox_write_text_file(
        session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt", content="hello", mode="overwrite",
    ))
    assert result["bytes"] == 5
    assert (root / "workspace" / "a.txt").read_bytes() == b"hello"


def test_sandbox_write_internal_text_files(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="语义模型"):
        asyncio.run(we_ops.sandbox_write_internal_text_files(
            session_ref=_ref(session), entries=[("/mnt/user-data/workspace/plain.txt", "x")],
        ))
    results = asyncio.run(we_ops.sandbox_write_internal_text_files(
        session_ref=_ref(session),
        entries=[("/mnt/user-data/workspace/semantic/model.yaml", "a: 1")],
    ))
    assert results[0]["bytes"] == 4
    target = "/mnt/user-data/workspace/semantic/model.yaml"
    assert sandbox.remote_files[target] == b"a: 1"
    no_writer_sandbox = FakeSandbox()
    no_writer_sandbox.files.write_files = None
    no_writer = _make_session(no_writer_sandbox, root)
    with pytest.raises(ValueError, match="files.write_files"):
        asyncio.run(we_ops.sandbox_write_internal_text_files(
            session_ref=_ref(no_writer), entries=[("/mnt/user-data/workspace/semantic/model.yaml", "a: 1")],
        ))


def test_sandbox_delete_internal_text_file(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    stale = "/mnt/user-data/workspace/semantic/已停用模型__deadbeef.yaml"
    sandbox.remote_kinds[stale] = "file"
    sandbox.remote_files[stale] = b"stale"

    # 策略层先拒：内部通道只允许 semantic 下的 YAML
    with pytest.raises(ValueError, match="内部语义模型写入"):
        asyncio.run(we_ops.sandbox_delete_internal_text_file(
            session_ref=_ref(session), path="/mnt/user-data/workspace/plain.yaml",
        ))
    result = asyncio.run(we_ops.sandbox_delete_internal_text_file(
        session_ref=_ref(session), path=stale,
    ))
    assert result == {"path": stale, "type": "file"}
    assert stale not in sandbox.remote_kinds
    assert stale not in sandbox.remote_files

    no_deleter_sandbox = FakeSandbox()
    no_deleter_sandbox.remote_kinds[stale] = "file"
    no_deleter_sandbox.files.delete_files = None
    no_deleter = _make_session(no_deleter_sandbox, root)
    with pytest.raises(ValueError, match="files.delete_files"):
        asyncio.run(we_ops.sandbox_delete_internal_text_file(
            session_ref=_ref(no_deleter), path=stale,
        ))


def test_sandbox_replace_text_file(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    target = "/mnt/user-data/workspace/a.txt"
    sandbox.remote_kinds[target] = "file"
    sandbox.remote_files[target] = b"\xff\xfe binary"
    with pytest.raises(ValueError, match="二进制"):
        asyncio.run(we_ops.sandbox_replace_text_file(
            session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt",
            old_content="a", new_content="b",
        ))
    sandbox.remote_files[target] = b"one two"
    with pytest.raises(ValueError, match="未在文件中找到"):
        asyncio.run(we_ops.sandbox_replace_text_file(
            session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt",
            old_content="zzz", new_content="b",
        ))
    with pytest.raises(ValueError, match="匹配多处"):
        asyncio.run(we_ops.sandbox_replace_text_file(
            session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt",
            old_content="o", new_content="0",
        ))
    result = asyncio.run(we_ops.sandbox_replace_text_file(
        session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt",
        old_content="two", new_content="THREE",
    ))
    assert result["replaced_count"] == 1
    assert sandbox.remote_files[target] == b"one THREE"


def test_sandbox_write_tool_result(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="系统结果目录"):
        asyncio.run(we_ops.sandbox_write_tool_result(
            session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.txt", content="x",
        ))
    result = asyncio.run(we_ops.sandbox_write_tool_result(
        session_ref=_ref(session), file_path="/mnt/user-data/outputs/.tool-offload/r_1.json", content="{}",
    ))
    assert result["bytes"] == 2
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(we_ops.sandbox_write_tool_result(
            session_ref=_ref(session), file_path="/mnt/user-data/outputs/.tool-offload/r_1.json", content="{}",
        ))


def test_truncate_output():
    assert we_api.truncate_output("short") == ("short", False)
    text = "x" * 20
    truncated, flag = we_api.truncate_output(text, limit=5)
    assert flag is True and truncated.endswith("...[truncated]")


# ---------------------------------------------------------------------------
# 分支缺口补测（第二批）
#
# 下方用例针对沙箱模块（原 workspace_execution.py，已拆分）中仍未触达的分支弧，按原缺失行号标注，
# 便于后续对照 gaps.py 的输出复核。
# ---------------------------------------------------------------------------


def test_parse_rg_output_skips_paths_outside_target():
    """搜索结果路径不在 target 前缀下时跳过（原缺失分支 1761->1764）。"""
    stdout = "\n".join([
        '{"type": "match", "data": {"path": {"text": "/other/outside.py"}, "line_number": 1, "lines": {"text": "x"}}}',
        '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/in.py"}, "line_number": 2, "lines": {"text": "y"}}}',
    ])
    matches, has_more = we_search.parse_rg_output(
        stdout, "/mnt/user-data/workspace", max_results=10, max_chars=10000,
    )
    assert [item["file"] for item in matches] == ["in.py"]
    assert has_more is False


def test_parse_rg_output_skips_match_without_line_number():
    """缺少 line_number 的匹配被跳过（原缺失分支 1766->1767）。"""
    stdout = '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/b.py"}}}'
    matches, has_more = we_search.parse_rg_output(
        stdout, "/mnt/user-data/workspace", max_results=10, max_chars=10000,
    )
    assert matches == []
    assert has_more is False


def test_parse_rg_output_stops_when_body_chars_exhausted():
    """正文字符预算耗尽时置 has_more 并 break（原缺失分支 1770->1771）。

    第一条 line_cost = 4（内容长度）+ 0（结果列表为空不加分隔符）；
    第二条 = 4 + 1（结果列表非空需补分隔符）= 5，累计 9 > max_chars=6 触发截断。
    """
    stdout = "\n".join([
        '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/a.py"}, "line_number": 1, "lines": {"text": "aaaa"}}}',
        '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/b.py"}, "line_number": 2, "lines": {"text": "bbbb"}}}',
    ])
    matches, has_more = we_search.parse_rg_output(
        stdout, "/mnt/user-data/workspace", max_results=10, max_chars=6,
    )
    assert [item["file"] for item in matches] == ["a.py"]
    assert has_more is True


def test_extract_command_output_skips_blank_error_text():
    """error 对象存在但 name/value 均为空时，不产生多余错误行（原缺失分支 884->888）。

    注意 886 的 ``elif`` 与 882 的 ``if execution_error is not None`` 配对，而非与
    884 的 ``if error_text`` 配对 —— 因此 execution_error 存在时不会落到
    status_lookup_error 分支，stderr 应为空而不是补一行提示。
    """
    execution = SimpleNamespace(
        logs=SimpleNamespace(stdout=[], stderr=[]),
        error=SimpleNamespace(name="", value=""),
    )
    exit_code, _stdout, stderr = we_api._extract_command_output(execution, None)
    assert exit_code == 1
    assert stderr == ""


def test_resolve_sandbox_target_for_batch_is_guarded_upstream():
    """非语义模型路径在 sandbox_visible_path 阶段即被拒绝（1927 是不可达的二次校验）。

    ``sandbox_visible_path(..., allow_managed_semantic_model_write=True)`` 已保证只有
    ``workspace/semantic/*.yaml`` 能通过，故 1926 的
    ``_is_managed_semantic_model_sandbox_path`` 判定恒为 True，1927 的 raise 无法从
    公开入口触发。此处锁定上游的真实报错，防止该防御被误删后行为漂移。
    """
    with pytest.raises(ValueError, match="内部语义模型写入只能覆盖"):
        we_ops._resolve_sandbox_target_for_batch(
            None, "/mnt/user-data/workspace/notes.txt",
        )


def test_write_application_bytes_atomically_cleans_temp_file(tmp_path, monkeypatch):
    """写入中途失败时，finally 必须清理遗留的临时文件（原缺失分支 633->634）。"""
    path = tmp_path / "out.bin"

    def _boom(_fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(we_sync.os, "fsync", _boom)
    with pytest.raises(OSError):
        we_sync._write_application_bytes_atomically(path, b"data")

    # 目标文件不应存在，临时文件也不应残留
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# 分支缺口补测（第三批）：沙箱异常传播与会话缓存
#
# 设计原则：只伪造「异常类型」，其余走真实代码路径，确保断言的是
# 「哪一类错误该吞、哪一类必须向上抛」这一真实契约。
# ---------------------------------------------------------------------------

_UNEXPECTED = PermissionError("permission denied")


def _cached(session, ttl: float = 60.0):
    """构造一个尚未过期的缓存条目。"""
    return we_session.CachedSandboxExecutionSession(
        session=session,
        expires_at=we_session.time.monotonic() + ttl,
    )


def test_sync_sandbox_uploads_returns_when_manifest_unchanged(tmp_path, monkeypatch):
    """uploads 清单未变化时直接返回，不触发后续同步（原缺失行 556）。"""
    _root, _sandbox, session = _session_with_root(tmp_path, monkeypatch)
    manifest = {"uploads/a.csv": (1, 2, "fingerprint")}
    session.uploads_manifest = manifest
    monkeypatch.setattr(we_sync, "_tree_manifest", lambda *args, **kwargs: manifest)

    def _boom(*args, **kwargs):
        raise AssertionError("清单未变化时应提前返回，不得继续遍历上传目录")

    monkeypatch.setattr(we_sync, "_iter_application_files", _boom)
    asyncio.run(we_sync._sync_sandbox_uploads(session))


def test_list_sandbox_directory_entries_reraises_unexpected_error(tmp_path, monkeypatch):
    """非「文件不存在」的异常必须向上传播，不能被吞成空列表（原缺失行 733）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def _boom(entry):
        raise _UNEXPECTED

    monkeypatch.setattr(sandbox.files, "list_directory", _boom)
    with pytest.raises(PermissionError):
        asyncio.run(we_api._list_sandbox_directory_entries(
            session=session, path="/mnt/user-data/workspace",
        ))


def test_walk_sandbox_tree_skips_already_visited_directory(tmp_path, monkeypatch):
    """重复入队的目录被跳过，避免自引用导致死循环（原缺失分支 753->754）。"""
    _root, _sandbox, session = _session_with_root(tmp_path, monkeypatch)
    listed = []

    async def _fake_list(*, session, path):
        listed.append(path)
        # 无论查询哪个目录都返回同一子目录，使其被反复入队
        return [SimpleNamespace(path="/mnt/user-data/workspace/sub", entry_type="directory")]

    async def _fake_kind(*, session, path, entry=None):
        return "dir"

    monkeypatch.setattr(we_api, "_list_sandbox_directory_entries", _fake_list)
    monkeypatch.setattr(we_api, "_sandbox_path_kind", _fake_kind)

    files, directories = asyncio.run(we_api._walk_sandbox_tree(
        session=session, root="/mnt/user-data/workspace",
    ))
    assert files == []
    assert [str(getattr(item, "path", "")) for item in directories] == ["/mnt/user-data/workspace/sub"]
    # 子目录被列出一次、入队两次；第二次出队时命中 visited 直接 continue，
    # 因此列表里只有一次 sub，且不会无限循环。
    assert listed == ["/mnt/user-data/workspace", "/mnt/user-data/workspace/sub"]


def test_walk_sandbox_tree_ignores_unknown_entry_types(tmp_path, monkeypatch):
    """既非 dir 也非 file 的条目（如 socket）被忽略（原缺失分支 769->756）。"""
    _root, _sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def _fake_list(*, session, path):
        return [SimpleNamespace(path="/mnt/user-data/workspace/weird", entry_type="other")]

    async def _fake_kind(*, session, path, entry=None):
        return "socket"

    monkeypatch.setattr(we_api, "_list_sandbox_directory_entries", _fake_list)
    monkeypatch.setattr(we_api, "_sandbox_path_kind", _fake_kind)

    files, directories = asyncio.run(we_api._walk_sandbox_tree(
        session=session, root="/mnt/user-data/workspace",
    ))
    assert files == [] and directories == []


def test_get_session_reuses_cached_session_and_syncs_uploads(tmp_path, monkeypatch):
    """缓存命中且应用侧未变化时复用会话并增量同步 uploads（原缺失行 1005-1006）。"""
    root, _sandbox, session = _session_with_root(tmp_path, monkeypatch)
    we_session._WORKSPACE_EXECUTION_SESSION_CACHE[_cache_key(root)] = _cached(session)

    synced = []

    async def _fake_matches(candidate):
        return True

    async def _fake_sync(candidate):
        synced.append(candidate)

    monkeypatch.setattr(we_session, "_cached_session_matches_application", _fake_matches)
    monkeypatch.setattr(we_session, "_sync_sandbox_uploads", _fake_sync)

    result = asyncio.run(we_session.get_workspace_execution_session(str(root), "user_a"))
    assert result is session
    assert synced == [session]


def test_invalidate_session_pops_matching_cache_entry(tmp_path, monkeypatch):
    """失效会话时先摘掉缓存条目，再销毁沙箱（原缺失分支 1049->1051）。"""
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    key = _cache_key(root)
    we_session._WORKSPACE_EXECUTION_SESSION_CACHE[key] = _cached(session)

    asyncio.run(we_session.invalidate_workspace_execution_session(session, "user_a"))

    assert key not in we_session._WORKSPACE_EXECUTION_SESSION_CACHE
    assert sandbox.killed == 1 and sandbox.closed == 1


def test_cleanup_session_evicts_matching_cache_entry(tmp_path, monkeypatch):
    """清理会话时遍历缓存摘除自身条目（原缺失分支 1058->1057）。"""
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    key = _cache_key(root)
    we_session._WORKSPACE_EXECUTION_SESSION_CACHE[key] = _cached(session)

    asyncio.run(we_session.cleanup_workspace_execution_session(session))

    assert key not in we_session._WORKSPACE_EXECUTION_SESSION_CACHE
    assert sandbox.killed == 1 and sandbox.closed == 1


def test_release_session_swallows_close_error(tmp_path, monkeypatch):
    """释放会话时 close 失败只告警，不向上传播（原缺失行 1075-1076）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.close_error = RuntimeError("close failed")

    asyncio.run(we_session.release_workspace_execution_session(session))

    assert sandbox.closed == 1


def test_sandbox_path_info_reraises_unexpected_error(tmp_path, monkeypatch):
    """get_file_info 抛出非「文件不存在」异常时向上传播（原缺失行 1183）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def _boom(paths):
        raise _UNEXPECTED

    monkeypatch.setattr(sandbox.files, "get_file_info", _boom)
    with pytest.raises(PermissionError):
        asyncio.run(we_api._sandbox_path_info(
            session=session, path="/mnt/user-data/workspace/a.txt",
        ))


def test_sandbox_path_info_returns_none_for_non_dict_payload(tmp_path, monkeypatch):
    """SDK 返回的不是 dict（协议不匹配）时按「查不到」处理（原缺失行 1185）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def _unexpected(paths):
        return ["not", "a", "dict"]

    monkeypatch.setattr(sandbox.files, "get_file_info", _unexpected)
    assert asyncio.run(we_api._sandbox_path_info(
        session=session, path="/mnt/user-data/workspace/a.txt",
    )) is None


def test_sandbox_path_kind_reraises_unexpected_error(tmp_path, monkeypatch):
    """探测路径类型时，非「不是目录」的异常必须向上传播（原缺失行 1225）。

    必须显式传入不带 type 元信息的 entry，否则会先走 `_sandbox_path_info` 查询，
    文件不存在时直接抛 FileNotFoundError，到不了 list_directory 探测这一步。
    """
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def _boom(entry):
        raise _UNEXPECTED

    monkeypatch.setattr(sandbox.files, "list_directory", _boom)
    with pytest.raises(PermissionError):
        asyncio.run(we_api._sandbox_path_kind(
            session=session,
            path="/mnt/user-data/workspace/a.txt",
            entry=SimpleNamespace(path="/mnt/user-data/workspace/a.txt"),
        ))


def test_sandbox_artifact_payload_returns_empty_on_read_error(tmp_path, monkeypatch):
    """文件元信息存在但读取时传输失败，按空载荷处理（原缺失行 1244-1245）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/outputs/report.json"] = "file"
    sandbox.remote_files["/mnt/user-data/outputs/report.json"] = b"{}"

    async def _boom(path, **kwargs):
        raise RuntimeError("transport broken")

    monkeypatch.setattr(sandbox.files, "read_bytes", _boom)
    assert asyncio.run(we_ops.sandbox_artifact_payload(
        session_ref=_ref(session), file_path="/mnt/user-data/outputs/report.json",
    )) == {}


def _office_preview_setup(sandbox):
    """准备一个可被 OfficeCLI 预览的 docx。"""
    sandbox.remote_kinds["/mnt/user-data/workspace/a.docx"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.docx"] = b"PK"


def test_sandbox_render_office_html_truncates_oversized_preview(tmp_path, monkeypatch):
    """预览体积超过字符上限时返回 truncated 与空 HTML（原缺失行 1280）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    _office_preview_setup(sandbox)

    async def _fake_info(*, session, path):
        return SimpleNamespace(path=path, size=10_000_000)

    monkeypatch.setattr(we_ops, "_sandbox_path_info", _fake_info)
    result = asyncio.run(we_ops.sandbox_render_office_html(
        session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.docx", max_chars=100,
    ))
    assert result["truncated"] is True
    assert result["html"] == ""


def test_sandbox_render_office_html_swallows_cleanup_error(tmp_path, monkeypatch):
    """清理临时预览文件失败时只告警，不影响返回结果（原缺失行 1295-1296）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    _office_preview_setup(sandbox)

    async def _fake_info(*, session, path):
        return SimpleNamespace(path=path, size=16)

    async def _fake_read(*, sandbox, path):
        return b"<p>preview</p>"

    async def _boom(paths):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(we_ops, "_sandbox_path_info", _fake_info)
    monkeypatch.setattr(we_ops, "_read_sandbox_file_bytes", _fake_read)
    monkeypatch.setattr(sandbox.files, "delete_files", _boom)

    result = asyncio.run(we_ops.sandbox_render_office_html(
        session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.docx", max_chars=1000,
    ))
    assert result["html"] == "<p>preview</p>"
    assert result["truncated"] is False


def test_sandbox_render_office_html_skips_cleanup_without_deleter(tmp_path, monkeypatch):
    """SDK 不提供 delete_files 时跳过清理，仍正常返回（原缺失分支 1292->1302）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    _office_preview_setup(sandbox)

    async def _fake_info(*, session, path):
        return SimpleNamespace(path=path, size=16)

    async def _fake_read(*, sandbox, path):
        return b"<p>preview</p>"

    monkeypatch.setattr(we_ops, "_sandbox_path_info", _fake_info)
    monkeypatch.setattr(we_ops, "_read_sandbox_file_bytes", _fake_read)
    monkeypatch.setattr(sandbox.files, "delete_files", None)

    result = asyncio.run(we_ops.sandbox_render_office_html(
        session_ref=_ref(session), file_path="/mnt/user-data/workspace/a.docx", max_chars=1000,
    ))
    assert result["html"] == "<p>preview</p>"


def test_sandbox_create_dir_reraises_unexpected_probe_error(tmp_path, monkeypatch):
    """建目录前探测目标时，非「不存在」的异常必须向上传播（原缺失行 1522）。"""
    _root, _sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def _boom(*, session, path, entry=None):
        raise _UNEXPECTED

    monkeypatch.setattr(we_api, "_sandbox_path_kind", _boom)
    with pytest.raises(PermissionError):
        asyncio.run(we_ops.sandbox_create_dir(
            session_ref=_ref(session), path="/mnt/user-data/workspace/newdir",
        ))


def test_sandbox_delete_path_allows_empty_directory(tmp_path, monkeypatch):
    """非 recursive 删除空目录时放行，不报「目录非空」（原缺失分支 1552->1554）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/empty"] = "directory"

    result = asyncio.run(we_ops.sandbox_delete_path(
        session_ref=_ref(session), path="/mnt/user-data/workspace/empty",
        recursive=False, expected_type="dir",
    ))
    assert result["type"] == "dir"
    assert ("dir", ["/mnt/user-data/workspace/empty"]) in sandbox.files.delete_calls


def test_sandbox_move_path_reraises_unexpected_probe_error(tmp_path, monkeypatch):
    """移动前探测目标路径时，非「不存在」的异常必须向上传播（原缺失行 1581）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"

    async def _fake_kind(*, session, path, entry=None):
        if path.endswith("/b.txt"):
            raise _UNEXPECTED
        return "file"

    monkeypatch.setattr(we_api, "_sandbox_path_kind", _fake_kind)
    with pytest.raises(PermissionError):
        asyncio.run(we_ops.sandbox_move_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/workspace/a.txt",
            destination_path="/mnt/user-data/workspace/b.txt",
        ))


def test_sandbox_copy_path_reraises_unexpected_probe_error(tmp_path, monkeypatch):
    """复制前探测目标路径时，非「不存在」的异常必须向上传播（原缺失行 1632）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"

    async def _fake_kind(*, session, path, entry=None):
        if path.endswith("/b.txt"):
            raise _UNEXPECTED
        return "file"

    monkeypatch.setattr(we_ops, "_sandbox_path_kind", _fake_kind)
    with pytest.raises(PermissionError):
        asyncio.run(we_ops.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="/mnt/user-data/workspace/a.txt",
            destination_path="/mnt/user-data/workspace/b.txt",
        ))


def test_sandbox_write_tool_result_reraises_unexpected_read_error(tmp_path, monkeypatch):
    """写入工具结果前探测已存在文件时，非「不存在」的异常必须向上传播（原缺失行 1992）。"""
    _root, sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def _boom(path, **kwargs):
        raise _UNEXPECTED

    monkeypatch.setattr(sandbox.files, "read_bytes", _boom)
    with pytest.raises(PermissionError):
        asyncio.run(we_ops.sandbox_write_tool_result(
            session_ref=_ref(session),
            file_path="/mnt/user-data/outputs/.tool-offload/r_9.json",
            content="{}",
        ))
