"""workspace_execution.py 覆盖率补充测试。

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

from apps.chat_data_app.services.agent_run import workspace_execution
from apps.chat_data_app.services.agent_run.workspace_execution import (
    SandboxExecutionSession,
    SandboxExecutionSessionRef,
)


@pytest.fixture(autouse=True)
def _reset_session_caches():
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()
    yield
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE.clear()
    workspace_execution._WORKSPACE_EXECUTION_SESSION_LOCKS.clear()


@pytest.fixture(autouse=True)
def _chat_data_home(tmp_path, monkeypatch):
    """让所有用例的工作区根目录都落在当前用例的 tmp_path 下。"""
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))


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
    return workspace_execution._workspace_session_cache_key(str(workspace_root))


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
    assert workspace_execution._env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600) == 600
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "")
    assert workspace_execution._env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600) == 600
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "0")
    assert workspace_execution._env_int("OPENSANDBOX_TIMEOUT_SECONDS", 600) == 1


def test_str_to_bool(monkeypatch):
    assert workspace_execution._str_to_bool(None) is False
    assert workspace_execution._str_to_bool("") is False
    assert workspace_execution._str_to_bool(None, default=True) is True
    assert workspace_execution._str_to_bool("ON") is True
    assert workspace_execution._str_to_bool(" yes ") is True
    assert workspace_execution._str_to_bool("0") is False


def test_workspace_session_ttl_seconds(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "600")
    assert workspace_execution._workspace_session_ttl_seconds() == 300
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "40")
    assert workspace_execution._workspace_session_ttl_seconds() == 30
    monkeypatch.setenv("OPENSANDBOX_TIMEOUT_SECONDS", "1000")
    assert workspace_execution._workspace_session_ttl_seconds() == 300


def test_workspace_session_lock_reuses_instance():
    lock_a = workspace_execution._workspace_session_lock("key_a")
    lock_b = workspace_execution._workspace_session_lock("key_a")
    assert lock_a is lock_b


def test_network_default_action(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "ALLOW")
    assert workspace_execution._network_default_action() == "allow"
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "deny")
    assert workspace_execution._network_default_action() == "deny"
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "bad")
    assert workspace_execution._network_default_action() == "deny"


def test_network_allow_targets_dedup(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS", "a.com, b.com\na.com,,c.com")
    assert workspace_execution._network_allow_targets() == ["a.com", "b.com", "c.com"]


def test_build_network_policy(monkeypatch):
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "allow")
    assert workspace_execution._build_network_policy() is None
    monkeypatch.setenv("OPENSANDBOX_NETWORK_DEFAULT_ACTION", "deny")
    monkeypatch.setenv("OPENSANDBOX_NETWORK_ALLOW_TARGETS", "a.com")
    policy = workspace_execution._build_network_policy()
    assert policy.default_action == "deny"
    assert [rule.target for rule in policy.egress] == ["a.com"]


def test_import_opensandbox_sdk_ok():
    Sandbox, ConnectionConfig, RunCommandOpts, models = workspace_execution._import_opensandbox_sdk()
    assert Sandbox is not None and ConnectionConfig is not None
    assert RunCommandOpts is not None and len(models) == 2


def test_import_opensandbox_sdk_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "opensandbox", None)
    with pytest.raises(ValueError, match="OpenSandbox Python SDK"):
        workspace_execution._import_opensandbox_sdk()


def test_import_filesystem_models_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "opensandbox.models.filesystem", None)
    with pytest.raises(ValueError, match="Files API models"):
        workspace_execution._import_opensandbox_filesystem_models()
    with pytest.raises(ValueError, match="files.replace_contents"):
        workspace_execution._import_opensandbox_content_replace_entry()
    with pytest.raises(ValueError, match="files.list_directory"):
        workspace_execution._import_opensandbox_directory_list_entry()


def test_connection_config(monkeypatch):
    monkeypatch.delenv("OPENSANDBOX_URL", raising=False)
    with pytest.raises(ValueError, match="OPENSANDBOX_URL"):
        workspace_execution._connection_config(10)
    monkeypatch.setenv("OPENSANDBOX_URL", "https://sandbox.example.com")
    monkeypatch.setenv("OPENSANDBOX_USE_SERVER_PROXY", "true")
    config = workspace_execution._connection_config(10)
    assert config.domain == "https://sandbox.example.com"


def test_raise_sandbox_create_error_mapping():
    connect_error = httpx.ConnectError("boom")
    with pytest.raises(ValueError, match="不可达"):
        workspace_execution._raise_sandbox_create_error(connect_error)
    timeout_error = httpx.ConnectTimeout("boom")
    with pytest.raises(ValueError, match="不可达"):
        workspace_execution._raise_sandbox_create_error(timeout_error)
    wrapped = RuntimeError("wrapped") 
    wrapped.__cause__ = httpx.ReadTimeout("slow")
    with pytest.raises(ValueError, match="超时"):
        workspace_execution._raise_sandbox_create_error(wrapped)
    with pytest.raises(KeyError):
        workspace_execution._raise_sandbox_create_error(KeyError("other"))


# ---------------------------------------------------------------------------
# 应用侧路径与 manifest
# ---------------------------------------------------------------------------


def test_application_workspace_root_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    outside = tmp_path.parent / "outside-chat-data-home" / "user-data"
    with pytest.raises(ValueError, match="不在 CHAT_DATA_HOME"):
        workspace_execution._application_workspace_root(str(outside))
    with pytest.raises(ValueError, match="user-data"):
        workspace_execution._application_workspace_root(str(tmp_path / "other"))


def test_application_manifests(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    (root / "workspace" / "a.txt").write_text("aaa")
    (root / "workspace" / "semantic" / "model.yaml").parent.mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "semantic" / "model.yaml").write_text("model")
    (root / "outputs" / ".tool-offload" / "r.json").write_text("{}")
    (root / "uploads" / "u.csv").write_text("csv")

    full = workspace_execution._application_manifest(root)
    assert set(full) == {
        "workspace/a.txt", "workspace/semantic/model.yaml",
        "outputs/.tool-offload/r.json", "uploads/u.csv",
    }
    mutable = workspace_execution._mutable_application_manifest(root)
    assert set(mutable) == {"workspace/a.txt"}
    metadata = workspace_execution._mutable_application_metadata_manifest(root)
    assert set(metadata) == {"workspace/a.txt"}
    # fingerprint 至少包含 size 与 sha256
    size, _mtime, digest = mutable["workspace/a.txt"]
    assert size == 3 and len(digest) == 64


def test_iter_application_files_ignores_missing_root(tmp_path):
    assert workspace_execution._iter_application_files(tmp_path / "nope") == []
    assert workspace_execution._iter_application_directories(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Files API 包装
# ---------------------------------------------------------------------------


def test_write_sandbox_file_requires_writer():
    with pytest.raises(ValueError, match="files.write_file"):
        asyncio.run(workspace_execution._write_sandbox_file(sandbox=SimpleNamespace(), path="/x", data=b"x"))


def test_write_and_read_sandbox_file():
    sandbox = FakeSandbox()
    asyncio.run(workspace_execution._write_sandbox_file(
        sandbox=sandbox, path="/mnt/user-data/workspace/a.txt", data=b"hello",
    ))
    assert sandbox.remote_files["/mnt/user-data/workspace/a.txt"] == b"hello"
    data = asyncio.run(workspace_execution._read_sandbox_file_bytes(
        sandbox=sandbox, path="/mnt/user-data/workspace/a.txt",
    ))
    assert data == b"hello"
    with pytest.raises(ValueError, match="files.read_bytes"):
        asyncio.run(workspace_execution._read_sandbox_file_bytes(sandbox=SimpleNamespace(), path="/x"))


def test_replace_sandbox_file_content():
    sandbox = FakeSandbox()
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"old-new"
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    asyncio.run(workspace_execution._replace_sandbox_file_content(
        sandbox=sandbox,
        path="/mnt/user-data/workspace/a.txt",
        old_content="old",
        new_content="brand",
    ))
    assert sandbox.remote_files["/mnt/user-data/workspace/a.txt"] == b"brand-new"
    with pytest.raises(ValueError, match="files.replace_contents"):
        asyncio.run(workspace_execution._replace_sandbox_file_content(
            sandbox=SimpleNamespace(), path="/x", old_content="a", new_content="b",
        ))


def test_create_sandbox_directories():
    sandbox = FakeSandbox()
    asyncio.run(workspace_execution._create_sandbox_directories(sandbox=sandbox, paths=[], mode=777))
    assert sandbox.files.create_dir_calls == []
    asyncio.run(workspace_execution._create_sandbox_directories(
        sandbox=sandbox, paths=["/mnt/user-data/workspace", ""], mode=777,
    ))
    assert sandbox.files.create_dir_calls == [{"path": "/mnt/user-data/workspace", "mode": 777}]
    with pytest.raises(ValueError, match="files.create_directories"):
        asyncio.run(workspace_execution._create_sandbox_directories(
            sandbox=SimpleNamespace(), paths=["/x"], mode=777,
        ))


def test_is_sandbox_descendant():
    assert workspace_execution._is_sandbox_descendant("/a/b", "/a") is True
    assert workspace_execution._is_sandbox_descendant("/a", "/a") is False
    assert workspace_execution._is_sandbox_descendant("/b/c", "/a") is False


def test_is_sandbox_file_not_found():
    assert workspace_execution._is_sandbox_file_not_found(FileNotFoundError()) is True
    assert workspace_execution._is_sandbox_file_not_found(SimpleNamespace(status_code=404)) is True
    assert workspace_execution._is_sandbox_file_not_found(RuntimeError("No such file or directory")) is True
    assert workspace_execution._is_sandbox_file_not_found(RuntimeError("permission denied")) is False


def test_is_sandbox_not_directory():
    assert workspace_execution._is_sandbox_not_directory(RuntimeError("Not a directory")) is True
    assert workspace_execution._is_sandbox_not_directory(RuntimeError("ENOTDIR")) is True
    assert workspace_execution._is_sandbox_not_directory(RuntimeError("other")) is False


def test_sandbox_visible_path_empty_returns_root():
    assert workspace_execution.sandbox_visible_path("") == "/mnt/user-data"
    assert workspace_execution.sandbox_visible_path(None) == "/mnt/user-data"


def test_sandbox_path_to_visible_mappings():
    assert workspace_execution.sandbox_path_to_visible("/mnt/user-data/workspace") == "/mnt/user-data/workspace"
    assert workspace_execution.sandbox_path_to_visible("/mnt/user-data/outputs/a.json") == "/mnt/user-data/outputs/a.json"
    assert workspace_execution.sandbox_path_to_visible("/mnt/skills/x/SKILL.md") == "/mnt/skills/x/SKILL.md"
    assert workspace_execution.sandbox_path_to_visible("/etc/passwd") == "/etc/passwd"


# ---------------------------------------------------------------------------
# _sandbox_path_info / _sandbox_path_kind
# ---------------------------------------------------------------------------


def test_sandbox_path_info_branches():
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"abc"
    entry = asyncio.run(workspace_execution._sandbox_path_info(
        session=_make_session(sandbox, Path("/host/user-data")), path="/mnt/user-data/workspace/a.txt",
    ))
    assert getattr(entry, "size") == 3
    missing = asyncio.run(workspace_execution._sandbox_path_info(
        session=_make_session(sandbox, Path("/host/user-data")), path="/mnt/user-data/workspace/missing",
    ))
    assert missing is None

    async def raise_not_found(_paths):
        raise FileNotFoundError("nope")

    weird = FakeSandbox()
    weird.files.get_file_info = raise_not_found
    assert asyncio.run(workspace_execution._sandbox_path_info(
        session=_make_session(weird, Path("/host/user-data")), path="/x",
    )) is None

    with pytest.raises(ValueError, match="files.get_file_info"):
        asyncio.run(workspace_execution._sandbox_path_info(
            session=_make_session(SimpleNamespace(), Path("/host/user-data")), path="/x",
        ))


def test_sandbox_path_kind_branches():
    session = _make_session(FakeSandbox(), Path("/host/user-data"))
    with pytest.raises(FileNotFoundError, match="路径不存在"):
        asyncio.run(workspace_execution._sandbox_path_kind(session=session, path="/none"))
    symlink_session = _make_session(FakeSandbox(), Path("/host/user-data"))
    with pytest.raises(ValueError, match="符号链接"):
        asyncio.run(workspace_execution._sandbox_path_kind(
            session=symlink_session, path="/x", entry=SimpleNamespace(entry_type="symlink"),
        ))
    assert asyncio.run(workspace_execution._sandbox_path_kind(
        session=session, path="/x", entry=SimpleNamespace(entry_type="directory"),
    )) == "dir"
    assert asyncio.run(workspace_execution._sandbox_path_kind(
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
    assert asyncio.run(workspace_execution._sandbox_path_kind(
        session=session, path="/mnt/user-data/workspace/notdir",
        entry=SimpleNamespace(entry_type=""),
    )) == "file"
    assert asyncio.run(workspace_execution._sandbox_path_kind(
        session=session, path="/mnt/user-data/workspace",
        entry=SimpleNamespace(entry_type=""),
    )) == "dir"

    no_lister = SimpleNamespace()
    with pytest.raises(ValueError, match="files.list_directory"):
        asyncio.run(workspace_execution._sandbox_path_kind(
            session=_make_session(no_lister, Path("/host/user-data")),
            path="/x", entry=SimpleNamespace(entry_type=""),
        ))


def test_list_sandbox_directory_entries(tmp_path):
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    session = _make_session(sandbox, _workspace_root(tmp_path))
    entries = asyncio.run(workspace_execution._list_sandbox_directory_entries(
        session=session, path="/mnt/user-data/workspace",
    ))
    assert sorted(getattr(entry, "path") for entry in entries) == [
        "/mnt/user-data/workspace/a.txt", "/mnt/user-data/workspace/sub",
    ]

    async def not_found(_entry):
        raise FileNotFoundError("missing")

    sandbox2 = FakeSandbox()
    sandbox2.files.list_directory = not_found
    assert asyncio.run(workspace_execution._list_sandbox_directory_entries(
        session=_make_session(sandbox2, Path("/host/user-data")), path="/x",
    )) == []
    with pytest.raises(ValueError, match="files.list_directory"):
        asyncio.run(workspace_execution._list_sandbox_directory_entries(
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
    files, directories = asyncio.run(workspace_execution._walk_sandbox_tree(
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
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    session = _make_session(FakeSandbox(), root)
    with pytest.raises(ValueError, match="不属于当前会话"):
        workspace_execution._application_path_for_sandbox_path(session, "/etc/passwd")
    with pytest.raises(ValueError, match="不属于允许同步目录"):
        workspace_execution._application_path_for_sandbox_path(session, "/mnt/user-data/uploads/a.txt")
    with pytest.raises(ValueError, match="越界"):
        workspace_execution._application_path_for_sandbox_path(
            session, "/mnt/user-data/workspace/../../etc/passwd",
        )
    resolved = workspace_execution._application_path_for_sandbox_path(
        session, "/mnt/user-data/workspace/a.txt",
    )
    assert resolved == root / "workspace" / "a.txt"


def test_write_application_bytes_atomically(tmp_path):
    target = tmp_path / "nested" / "a.txt"
    workspace_execution._write_application_bytes_atomically(target, b"data")
    assert target.read_bytes() == b"data"


def test_delete_application_file_branches(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x")
    workspace_execution._delete_application_file(target)
    assert not target.exists()
    # 不存在的文件直接忽略
    workspace_execution._delete_application_file(tmp_path / "nope")
    directory = tmp_path / "dir"
    directory.mkdir()
    with pytest.raises(ValueError, match="覆盖目录"):
        workspace_execution._delete_application_file(directory)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="符号链接"):
        workspace_execution._delete_application_file(link)
    link.unlink()


def test_ensure_application_paths_unchanged(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    local = root / "workspace" / "a.txt"
    local.write_text("v1")
    session = _make_session(FakeSandbox(), root)
    session.application_manifest = None
    workspace_execution._ensure_application_paths_unchanged(session=session, paths=[local])
    session.application_manifest = workspace_execution._mutable_application_manifest(root)
    workspace_execution._ensure_application_paths_unchanged(session=session, paths=[local])
    local.write_text("v2")
    with pytest.raises(ValueError, match="已变更，拒绝覆盖"):
        workspace_execution._ensure_application_paths_unchanged(session=session, paths=[local])


def test_persist_sandbox_paths_success_and_deletion(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace/changed.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/changed.txt"] = b"new"
    doomed = root / "workspace" / "doomed.txt"
    doomed.write_text("old")
    session = _make_session(sandbox, root)
    session.application_manifest = workspace_execution._mutable_application_manifest(root)

    asyncio.run(workspace_execution._persist_sandbox_paths(
        session=session,
        changed_paths=["/mnt/user-data/workspace/changed.txt"],
        deleted_paths=["/mnt/user-data/workspace/doomed.txt"],
    ))
    assert (root / "workspace" / "changed.txt").read_bytes() == b"new"
    assert not doomed.exists()
    assert session.pending_persistence is False


def test_persist_sandbox_paths_failure_marks_pending(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()

    async def boom(**_kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(workspace_execution, "_read_sandbox_file_bytes", boom)
    session = _make_session(sandbox, root)
    with pytest.raises(RuntimeError, match="download failed"):
        asyncio.run(workspace_execution._persist_sandbox_paths(
            session=session, changed_paths=["/mnt/user-data/workspace/x.txt"],
        ))
    assert session.pending_persistence is True


def test_recover_pending_persistence(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    session = _make_session(sandbox, root)
    called = []

    async def fake_sync(_session):
        called.append(_session)

    monkeypatch.setattr(workspace_execution, "_sync_sandbox_working_trees", fake_sync)
    asyncio.run(workspace_execution._recover_pending_persistence(session))
    assert called == []
    session.pending_persistence = True
    asyncio.run(workspace_execution._recover_pending_persistence(session))
    assert called == [session]


def test_sync_sandbox_working_trees_full_flow(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
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
    asyncio.run(workspace_execution.sync_sandbox_working_trees(session))
    assert (root / "workspace" / "a.txt").read_bytes() == b"remote-a"
    assert not (root / "workspace" / "b.txt").exists()
    assert (root / "workspace" / "sub").is_dir()
    assert not (root / "workspace" / "olddir").exists()
    # 应用侧工作区根目录不允许被目录同步覆盖
    with pytest.raises(ValueError, match="根目录"):
        workspace_execution._application_directory_for_sandbox_path(
            session, "/mnt/user-data/workspace",
        )


def test_sync_sandbox_uploads(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/uploads"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/uploads/stale.csv"] = "file"
    sandbox.remote_files["/mnt/user-data/uploads/stale.csv"] = b"old"
    session = _make_session(sandbox, root)
    session.uploads_manifest = {"uploads/stale.csv": (3, 1, "x")}
    # manifest 未变化时直接返回
    asyncio.run(workspace_execution._sync_sandbox_uploads(session))
    assert sandbox.files.write_calls == []

    (root / "uploads" / "new.csv").write_text("csv-data")
    asyncio.run(workspace_execution._sync_sandbox_uploads(session))
    written = {call["path"]: call["mode"] for call in sandbox.files.write_calls}
    assert written == {"/mnt/user-data/uploads/new.csv": 444}
    assert sandbox.files.delete_calls == [("file", ["/mnt/user-data/uploads/stale.csv"])]
    assert session.uploads_manifest["uploads/new.csv"][0] == 8


def test_sync_sandbox_uploads_requires_delete_files(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/uploads"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/uploads/stale.csv"] = "file"
    sandbox.files.delete_files = None
    session = _make_session(sandbox, root)
    with pytest.raises(ValueError, match="files.delete_files"):
        asyncio.run(workspace_execution._sync_sandbox_uploads(session))


def test_initialize_sandbox_filesystem(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    (root / "workspace" / "a.txt").write_text("data")
    (root / "uploads" / "u.csv").write_text("csv")
    skills = root.parent / "runtime-skills"
    (skills / "demo-skill").mkdir(parents=True, exist_ok=True)
    (skills / "demo-skill" / "SKILL.md").write_text("skill")
    sandbox = FakeSandbox()
    session = _make_session(sandbox, root, runtime_skills_dir=skills)
    asyncio.run(workspace_execution._initialize_sandbox_filesystem(session=session))
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
    exit_code, stdout, stderr = workspace_execution._extract_command_output(execution, None)
    assert (exit_code, stdout) == (1, "out")
    assert "RuntimeError: boom" in stderr and "err" in stderr

    execution.error = None
    exit_code, stdout, stderr = workspace_execution._extract_command_output(
        execution, None, status_lookup_error="lost",
    )
    assert exit_code == 1 and "未能确认" in stderr

    exit_code, _, _ = workspace_execution._extract_command_output(
        execution, SimpleNamespace(exit_code=7),
    )
    assert exit_code == 7


def test_run_command_in_sandbox(tmp_path):
    sandbox = FakeSandbox()
    sandbox.command_result = (0, "hello", "")
    session = _make_session(sandbox, _workspace_root(tmp_path))
    exit_code, stdout, stderr = asyncio.run(workspace_execution.run_command_in_sandbox(
        session=session, command="echo hello",
    ))
    assert (exit_code, stdout, stderr) == (0, "hello", "")
    assert sandbox.commands.run_calls == ["echo hello"]

    sandbox.status_error = RuntimeError("status lost")
    sandbox.command_result = (0, "x", "")
    exit_code, _, stderr = asyncio.run(workspace_execution.run_command_in_sandbox(
        session=session, command="echo x",
    ))
    assert exit_code == 1 and "status lost" in stderr

    sandbox.status_error = None
    sandbox.command_result = (-1, "", "exploded")
    exit_code, _, stderr = asyncio.run(workspace_execution.run_command_in_sandbox(
        session=session, command="bad",
    ))
    assert exit_code == 1 and "exploded" in stderr


# ---------------------------------------------------------------------------
# 会话缓存与生命周期
# ---------------------------------------------------------------------------


def test_require_sandbox_session():
    with pytest.raises(ValueError, match="未准备"):
        workspace_execution.require_sandbox_session(None)
    with pytest.raises(ValueError, match="未准备"):
        workspace_execution.require_sandbox_session(SandboxExecutionSessionRef())
    session = _make_session(FakeSandbox(), Path("/host/user-data"))
    assert workspace_execution.require_sandbox_session(_ref(session)) is session


def _patch_prepare(monkeypatch, session):
    async def fake_prepare(*_args, **_kwargs):
        return session

    monkeypatch.setattr(workspace_execution, "prepare_workspace_execution_session", fake_prepare)


def test_get_workspace_execution_session_cache_roundtrip(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    session = _make_session(FakeSandbox(), root)
    _patch_prepare(monkeypatch, session)
    synced = []

    async def matches(_session):
        return True

    async def fake_sync_uploads(_session):
        synced.append(1)

    monkeypatch.setattr(workspace_execution, "_cached_session_matches_application", matches)
    monkeypatch.setattr(workspace_execution, "_sync_sandbox_uploads", fake_sync_uploads)

    assert asyncio.run(workspace_execution.get_workspace_execution_session(str(root))) is session
    assert asyncio.run(workspace_execution.get_workspace_execution_session(str(root))) is session
    assert synced == [1]
    # 缓存过期后重新创建并释放旧会话
    cache_key = _cache_key(root)
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key].expires_at = 0.0
    assert asyncio.run(workspace_execution.get_workspace_execution_session(str(root))) is session
    assert session.sandbox.closed == 1


def test_get_workspace_execution_session_skips_mismatched_cache(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    stale = _make_session(FakeSandbox(), root)
    fresh = _make_session(FakeSandbox(), root)
    _patch_prepare(monkeypatch, fresh)

    async def always(_session):
        return False

    monkeypatch.setattr(workspace_execution, "_cached_session_matches_application", always)
    workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[_cache_key(root)] = (
        workspace_execution.CachedSandboxExecutionSession(session=stale, expires_at=1e12)
    )
    assert asyncio.run(workspace_execution.get_workspace_execution_session(str(root))) is fresh
    assert stale.sandbox.closed == 1


def test_attach_and_invalidate_session(tmp_path):
    root = _workspace_root(tmp_path)
    cache_key = _cache_key(root)
    first = _make_session(FakeSandbox(), root)
    second = _make_session(FakeSandbox(), root)
    asyncio.run(workspace_execution.attach_workspace_execution_session(None))
    asyncio.run(workspace_execution.attach_workspace_execution_session(first))
    assert workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key].session is first
    asyncio.run(workspace_execution.attach_workspace_execution_session(second))
    assert first.sandbox.closed == 1
    assert workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE[cache_key].session is second
    asyncio.run(workspace_execution.invalidate_workspace_execution_session(None))
    asyncio.run(workspace_execution.invalidate_workspace_execution_session(second))
    assert cache_key not in workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE
    assert second.sandbox.killed == 1 and second.sandbox.closed >= 1


def test_cleanup_and_release_session(tmp_path):
    root = _workspace_root(tmp_path)
    asyncio.run(workspace_execution.cleanup_workspace_execution_session(None))
    asyncio.run(workspace_execution.release_workspace_execution_session(None))
    session = _make_session(FakeSandbox(), root)
    asyncio.run(workspace_execution.attach_workspace_execution_session(session)
                )
    session.sandbox.kill_error = RuntimeError("kill fail")
    session.sandbox.close_error = RuntimeError("close fail")
    asyncio.run(workspace_execution.cleanup_workspace_execution_session(session))
    assert session.sandbox.killed == 1 and session.sandbox.closed == 1
    assert session.cache_key not in workspace_execution._WORKSPACE_EXECUTION_SESSION_CACHE
    other = _make_session(FakeSandbox(), root)
    other.sandbox = None
    asyncio.run(workspace_execution.cleanup_workspace_execution_session(other))
    asyncio.run(workspace_execution.release_workspace_execution_session(other))


def test_prepare_workspace_execution_session_success(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
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
    session = asyncio.run(workspace_execution.prepare_workspace_execution_session(str(root)))
    assert session.sandbox is fake_sandbox
    assert created["network_policy"].default_action == "deny"
    assert created["metadata"]["workspace_backend"] == "opensandbox"
    assert fake_sandbox.files.create_dir_calls  # 初始化建目录


def test_prepare_workspace_execution_session_failures(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENSANDBOX_URL", "https://sandbox.example.com")

    class TimeoutSdkSandbox:
        @staticmethod
        async def create(**_kwargs):
            await asyncio.sleep(60)

    real_asyncio = workspace_execution.asyncio

    async def fast_timeout(_awaitable, timeout=None):
        _awaitable.close()
        raise TimeoutError()

    # 超时分支只在 context 内生效，避免污染后面的连接失败分支
    with monkeypatch.context() as m:
        m.setattr(workspace_execution, "asyncio", SimpleNamespace(
            wait_for=fast_timeout, to_thread=real_asyncio.to_thread, Lock=real_asyncio.Lock,
        ))
        m.setattr("opensandbox.Sandbox", TimeoutSdkSandbox)
        with pytest.raises(ValueError, match="创建超时"):
            asyncio.run(workspace_execution.prepare_workspace_execution_session(str(root)))

    class ConnectErrorSdkSandbox:
        @staticmethod
        async def create(**_kwargs):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr("opensandbox.Sandbox", ConnectErrorSdkSandbox)
    with pytest.raises(ValueError, match="不可达"):
        asyncio.run(workspace_execution.prepare_workspace_execution_session(str(root)))


def test_prepare_workspace_execution_session_cleans_up_on_init_failure(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
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
        asyncio.run(workspace_execution.prepare_workspace_execution_session(str(root)))
    assert fake_sandbox.killed == 1 and fake_sandbox.closed == 1
    _ = bad_write_file  # 保持可读性
    _ = original_create


# ---------------------------------------------------------------------------
# 文件工具入口
# ---------------------------------------------------------------------------


def _session_with_root(tmp_path, monkeypatch):
    root = _workspace_root(tmp_path)
    monkeypatch.setattr(workspace_execution, "CHAT_DATA_HOME", str(tmp_path))
    sandbox = FakeSandbox()
    sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/outputs"] = "directory"
    session = _make_session(sandbox, root)
    return root, sandbox, session


def test_sandbox_artifact_payload_branches(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    ref = _ref(session)
    sandbox.command_result = (0, "1", "")
    assert asyncio.run(workspace_execution.sandbox_artifact_payload(
        session_ref=ref, file_path="/mnt/user-data/outputs/.tool-offload/x.json",
    )) == {}
    assert asyncio.run(workspace_execution.sandbox_artifact_payload(
        session_ref=ref, file_path="workspace/missing.txt",
    )) == {}
    sandbox.remote_kinds["/mnt/user-data/outputs/report.md"] = "file"
    sandbox.remote_files["/mnt/user-data/outputs/report.md"] = "# 结论".encode("utf-8")
    payload = asyncio.run(workspace_execution.sandbox_artifact_payload(
        session_ref=ref, file_path="outputs/report.md",
    ))
    assert payload["artifact_type"] == "markdown"
    assert payload["file_path"] == "/mnt/user-data/outputs/report.md"
    no_session = SandboxExecutionSessionRef()
    with pytest.raises(ValueError, match="未准备"):
        asyncio.run(workspace_execution.sandbox_artifact_payload(
            session_ref=no_session, file_path="outputs/chart.json",
        ))


def test_render_office_html_rejects_suffix(tmp_path, monkeypatch):
    _root, _sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=".docx、.xlsx、.pptx"):
        asyncio.run(workspace_execution.sandbox_render_office_html(
            session_ref=_ref(session), file_path="workspace/a.txt", max_chars=100,
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

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", fake_run)
    monkeypatch.setattr(workspace_execution, "_sandbox_path_info", fake_path_info)
    result = asyncio.run(workspace_execution.sandbox_render_office_html(
        session_ref=_ref(session), file_path="workspace/report.docx", max_chars=100,
    ))
    assert result["html"] == "<html>small</html>" and result["truncated"] is False

    async def failing_run(*, session, command, timeout_seconds=30):
        return 1, "", "officecli exploded"

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", failing_run)
    with pytest.raises(RuntimeError, match="officecli exploded"):
        asyncio.run(workspace_execution.sandbox_render_office_html(
            session_ref=_ref(session), file_path="workspace/report.docx", max_chars=100,
        ))


def test_sandbox_read_text_file_variants(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    ref = _ref(session)
    sandbox.command_result = (0, "2", "")

    missing = asyncio.run(workspace_execution.sandbox_read_text_file(
        session_ref=ref, file_path="workspace/missing.txt", offset=0, limit=10, max_chars=100,
    ))
    assert missing["exists"] is False

    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    is_dir = asyncio.run(workspace_execution.sandbox_read_text_file(
        session_ref=ref, file_path="workspace/sub", offset=0, limit=10, max_chars=100,
    ))
    assert is_dir["is_dir"] is True

    sandbox.remote_kinds["/mnt/user-data/workspace/bin.dat"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/bin.dat"] = b"a\x00b"
    binary = asyncio.run(workspace_execution.sandbox_read_text_file(
        session_ref=ref, file_path="workspace/bin.dat", offset=0, limit=10, max_chars=100,
    ))
    assert binary["is_binary"] is True

    sandbox.remote_kinds["/mnt/user-data/workspace/text.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/text.txt"] = b"one\ntwo\n"
    result = asyncio.run(workspace_execution.sandbox_read_text_file(
        session_ref=ref, file_path="workspace/text.txt", offset=0, limit=1, max_chars=10000,
    ))
    assert result["lines"] == ["one"]
    assert result["has_more"] is True
    assert result["total_lines"] == 2
    # 行数统计缓存命中
    assert session.text_line_counts["/mnt/user-data/workspace/text.txt"][2] == 2

    # 越界偏移返回空页（不带 lines 字段，用 offset_out_of_range 表达）
    empty = asyncio.run(workspace_execution.sandbox_read_text_file(
        session_ref=ref, file_path="workspace/text.txt", offset=99, limit=10, max_chars=100,
    ))
    assert empty.get("lines", []) == [] and empty["offset_out_of_range"] is True

    # reader 缺失
    readerless = FakeSandbox()
    readerless.remote_kinds["/mnt/user-data/workspace/text.txt"] = "file"
    readerless.remote_files["/mnt/user-data/workspace/text.txt"] = b"one\ntwo\n"
    readerless.files.read_bytes = None
    session2 = _make_session(readerless, root)
    with pytest.raises(ValueError, match="files.read_bytes"):
        asyncio.run(workspace_execution.sandbox_read_text_file(
            session_ref=_ref(session2), file_path="workspace/text.txt",
            offset=0, limit=1, max_chars=100,
        ))


def test_sandbox_text_line_count_errors(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    entry = SimpleNamespace(size=4, modified_at="m")
    sandbox.command_result = (1, "", "awk failed")
    with pytest.raises(RuntimeError, match="awk failed"):
        asyncio.run(workspace_execution._sandbox_text_line_count(
            session=session, path="/mnt/user-data/workspace/x.txt", entry=entry,
        ))
    sandbox.command_result = (0, "not-a-number", "")
    with pytest.raises(RuntimeError, match="无效"):
        asyncio.run(workspace_execution._sandbox_text_line_count(
            session=session, path="/mnt/user-data/workspace/x.txt", entry=entry,
        ))
    # 元数据未变时命中缓存
    session.text_line_counts["/mnt/user-data/workspace/x.txt"] = (4, "m", 7)
    sandbox.command_result = (1, "", "should not run")
    assert asyncio.run(workspace_execution._sandbox_text_line_count(
        session=session, path="/mnt/user-data/workspace/x.txt", entry=entry,
    )) == 7


def test_sandbox_list_dir(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"abc"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    entries = asyncio.run(workspace_execution.sandbox_list_dir(
        session_ref=_ref(session), path="workspace", max_depth=1,
    ))
    assert entries == [
        {"type": "dir", "path": "/mnt/user-data/workspace/sub/", "size": "-", "mtime": "2026-08-10 00:00"},
        {"type": "file", "path": "/mnt/user-data/workspace/a.txt", "size": 3, "mtime": "2026-08-10 00:00"},
    ]
    with pytest.raises(ValueError, match="不是目录"):
        asyncio.run(workspace_execution.sandbox_list_dir(
            session_ref=_ref(session), path="workspace/a.txt", max_depth=1,
        ))
    no_lister_sandbox = FakeSandbox()
    no_lister_sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    no_lister_sandbox.files.list_directory = None
    no_lister = _make_session(no_lister_sandbox, root)
    with pytest.raises(ValueError, match="files.list_directory"):
        asyncio.run(workspace_execution.sandbox_list_dir(
            session_ref=_ref(no_lister), path="workspace", max_depth=1,
        ))


def test_sandbox_create_dir(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    result = asyncio.run(workspace_execution.sandbox_create_dir(
        session_ref=_ref(session), path="workspace/newdir",
    ))
    assert result == {"path": "/mnt/user-data/workspace/newdir", "type": "dir"}
    assert "/mnt/user-data/workspace/newdir" in sandbox.remote_kinds
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(workspace_execution.sandbox_create_dir(
            session_ref=_ref(session), path="workspace/newdir",
        ))
    with pytest.raises(ValueError, match="根目录"):
        asyncio.run(workspace_execution.sandbox_create_dir(
            session_ref=_ref(session), path="workspace",
        ))


def test_sandbox_delete_path(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    sandbox.remote_kinds["/mnt/user-data/workspace/sub/inner.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/sub/inner.txt"] = b"inner"

    with pytest.raises(ValueError, match="不能作为dir删除"):
        asyncio.run(workspace_execution.sandbox_delete_path(
            session_ref=_ref(session), path="workspace/a.txt", recursive=False, expected_type="dir",
        ))
    with pytest.raises(ValueError, match="目录非空"):
        asyncio.run(workspace_execution.sandbox_delete_path(
            session_ref=_ref(session), path="workspace/sub", recursive=False, expected_type="dir",
        ))
    result = asyncio.run(workspace_execution.sandbox_delete_path(
        session_ref=_ref(session), path="workspace/a.txt", recursive=False, expected_type="file",
    ))
    assert result["type"] == "file"
    result = asyncio.run(workspace_execution.sandbox_delete_path(
        session_ref=_ref(session), path="workspace/sub", recursive=True, expected_type="dir",
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
        asyncio.run(workspace_execution.sandbox_delete_path(
            session_ref=_ref(no_deleter), path="workspace/b.txt", recursive=False, expected_type="file",
        ))


def test_sandbox_move_path(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"
    result = asyncio.run(workspace_execution.sandbox_move_path(
        session_ref=_ref(session),
        source_path="workspace/a.txt",
        destination_path="workspace/b.txt",
    ))
    assert result["type"] == "file"
    assert sandbox.files.move_calls == [(
        "/mnt/user-data/workspace/a.txt", "/mnt/user-data/workspace/b.txt",
    )]
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(workspace_execution.sandbox_move_path(
            session_ref=_ref(session),
            source_path="workspace/b.txt",
            destination_path="workspace/b.txt",
        ))
    sandbox.remote_kinds["/mnt/user-data/workspace/a.txt"] = "file"
    sandbox.remote_files["/mnt/user-data/workspace/a.txt"] = b"x"
    sandbox.remote_kinds["/mnt/user-data/workspace/c.txt"] = "file"
    with pytest.raises(ValueError, match="父路径不是目录"):
        asyncio.run(workspace_execution.sandbox_move_path(
            session_ref=_ref(session),
            source_path="workspace/c.txt",
            destination_path="workspace/a.txt/deep.txt",
        ))
    no_mover_sandbox = FakeSandbox()
    no_mover_sandbox.remote_kinds["/mnt/user-data/workspace"] = "directory"
    no_mover_sandbox.remote_kinds["/mnt/user-data/workspace/c.txt"] = "file"
    no_mover_sandbox.remote_files["/mnt/user-data/workspace/c.txt"] = b"x"
    no_mover_sandbox.files.move_files = None
    no_mover = _make_session(no_mover_sandbox, root)
    with pytest.raises(ValueError, match="files.move_files"):
        asyncio.run(workspace_execution.sandbox_move_path(
            session_ref=_ref(no_mover),
            source_path="workspace/c.txt",
            destination_path="workspace/d.txt",
        ))


def test_sandbox_copy_path(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/uploads/src.csv"] = "file"
    sandbox.remote_files["/mnt/user-data/uploads/src.csv"] = b"csv"
    with pytest.raises(ValueError, match="不能复制"):
        asyncio.run(workspace_execution.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="outputs/.tool-offload/r.json",
            destination_path="workspace/r.json",
        ))
    sandbox.remote_kinds["/mnt/user-data/workspace/sub"] = "directory"
    with pytest.raises(ValueError, match="只支持复制单个文件"):
        asyncio.run(workspace_execution.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="workspace/sub",
            destination_path="workspace/sub2",
        ))
    result = asyncio.run(workspace_execution.sandbox_copy_path(
        session_ref=_ref(session),
        source_path="uploads/src.csv",
        destination_path="workspace/src.csv",
    ))
    assert result["bytes"] == 3
    assert (root / "workspace" / "src.csv").read_bytes() == b"csv"
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(workspace_execution.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="uploads/src.csv",
            destination_path="workspace/src.csv",
        ))
    sandbox.remote_kinds["/mnt/user-data/uploads/other.csv"] = "file"
    with pytest.raises(ValueError, match="父路径不是目录"):
        asyncio.run(workspace_execution.sandbox_copy_path(
            session_ref=_ref(session),
            source_path="uploads/other.csv",
            destination_path="workspace/src.csv/deep.csv",
        ))


def test_sandbox_search_files(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    sandbox.remote_kinds["/mnt/user-data/workspace/a.csv"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/b.csv"] = "file"
    sandbox.remote_kinds["/mnt/user-data/workspace/c.txt"] = "file"
    results = asyncio.run(workspace_execution.sandbox_search_files(
        session_ref=_ref(session), path="workspace", pattern="*.csv", max_results=1,
    ))
    assert results == [{"path": "/mnt/user-data/workspace/a.csv", "type": "file"}]
    no_search = _make_session(SimpleNamespace(files=SimpleNamespace()), root)
    with pytest.raises(ValueError, match="files.search"):
        asyncio.run(workspace_execution.sandbox_search_files(
            session_ref=_ref(no_search), path="workspace", pattern="*", max_results=10,
        ))


def test_matches_sandbox_glob():
    assert workspace_execution._matches_sandbox_glob("a.txt", "**/*") is True
    assert workspace_execution._matches_sandbox_glob("a.txt", "*.txt") is True
    assert workspace_execution._matches_sandbox_glob("a.txt", "**/*.csv") is False


def test_build_rg_command_str():
    command = workspace_execution.build_rg_command_str(
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
    matches, has_more = workspace_execution.parse_rg_output(
        stdout, "/mnt/user-data/workspace", max_results=2, max_chars=10000,
    )
    assert [item["file"] for item in matches] == ["a.py", ""]
    assert has_more is True


def test_sandbox_grep_text(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)

    async def run_127(*, session, command, timeout_seconds=30):
        return 127, "", "rg: command not found"

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", run_127)
    with pytest.raises(RuntimeError, match="ripgrep"):
        asyncio.run(workspace_execution.sandbox_grep_text(
            session_ref=_ref(session), path="workspace", patterns=["x"],
            glob="**/*", max_results=10, max_chars=1000,
        ))

    async def run_fail(*, session, command, timeout_seconds=30):
        return 2, "", "boom"

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", run_fail)
    with pytest.raises(RuntimeError, match="rg 执行失败"):
        asyncio.run(workspace_execution.sandbox_grep_text(
            session_ref=_ref(session), path="workspace", patterns=["x"],
            glob="**/*", max_results=10, max_chars=1000,
        ))

    async def run_ok(*, session, command, timeout_seconds=30):
        line = '{"type": "match", "data": {"path": {"text": "/mnt/user-data/workspace/a.py"}, "line_number": 5, "lines": {"text": "hello"}}}'
        return 0, line, ""

    monkeypatch.setattr(workspace_execution, "run_command_in_sandbox", run_ok)
    payload = asyncio.run(workspace_execution.sandbox_grep_text(
        session_ref=_ref(session), path="workspace", patterns=["hello"],
        glob="**/*", max_results=10, max_chars=1000,
    ))
    assert payload["matches"] == [
        {"path": "/mnt/user-data/workspace/a.py", "line_number": 5, "line": "hello"},
    ]
    assert payload["has_more"] is False


def test_sandbox_write_text_file(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="只支持 overwrite"):
        asyncio.run(workspace_execution.sandbox_write_text_file(
            session_ref=_ref(session), file_path="workspace/a.txt", content="x", mode="append",
        ))
    result = asyncio.run(workspace_execution.sandbox_write_text_file(
        session_ref=_ref(session), file_path="workspace/a.txt", content="hello", mode="overwrite",
    ))
    assert result["bytes"] == 5
    assert (root / "workspace" / "a.txt").read_bytes() == b"hello"


def test_sandbox_write_internal_text_files(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="语义模型"):
        asyncio.run(workspace_execution.sandbox_write_internal_text_files(
            session_ref=_ref(session), entries=[("workspace/plain.txt", "x")],
        ))
    results = asyncio.run(workspace_execution.sandbox_write_internal_text_files(
        session_ref=_ref(session),
        entries=[("workspace/semantic/model.yaml", "a: 1")],
    ))
    assert results[0]["bytes"] == 4
    target = "/mnt/user-data/workspace/semantic/model.yaml"
    assert sandbox.remote_files[target] == b"a: 1"
    no_writer_sandbox = FakeSandbox()
    no_writer_sandbox.files.write_files = None
    no_writer = _make_session(no_writer_sandbox, root)
    with pytest.raises(ValueError, match="files.write_files"):
        asyncio.run(workspace_execution.sandbox_write_internal_text_files(
            session_ref=_ref(no_writer), entries=[("workspace/semantic/model.yaml", "a: 1")],
        ))


def test_sandbox_replace_text_file(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    target = "/mnt/user-data/workspace/a.txt"
    sandbox.remote_kinds[target] = "file"
    sandbox.remote_files[target] = b"\xff\xfe binary"
    with pytest.raises(ValueError, match="二进制"):
        asyncio.run(workspace_execution.sandbox_replace_text_file(
            session_ref=_ref(session), file_path="workspace/a.txt",
            old_content="a", new_content="b",
        ))
    sandbox.remote_files[target] = b"one two"
    with pytest.raises(ValueError, match="未在文件中找到"):
        asyncio.run(workspace_execution.sandbox_replace_text_file(
            session_ref=_ref(session), file_path="workspace/a.txt",
            old_content="zzz", new_content="b",
        ))
    with pytest.raises(ValueError, match="匹配多处"):
        asyncio.run(workspace_execution.sandbox_replace_text_file(
            session_ref=_ref(session), file_path="workspace/a.txt",
            old_content="o", new_content="0",
        ))
    result = asyncio.run(workspace_execution.sandbox_replace_text_file(
        session_ref=_ref(session), file_path="workspace/a.txt",
        old_content="two", new_content="THREE",
    ))
    assert result["replaced_count"] == 1
    assert sandbox.remote_files[target] == b"one THREE"


def test_sandbox_write_tool_result(tmp_path, monkeypatch):
    root, sandbox, session = _session_with_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="系统结果目录"):
        asyncio.run(workspace_execution.sandbox_write_tool_result(
            session_ref=_ref(session), file_path="workspace/a.txt", content="x",
        ))
    result = asyncio.run(workspace_execution.sandbox_write_tool_result(
        session_ref=_ref(session), file_path="outputs/.tool-offload/r_1.json", content="{}",
    ))
    assert result["bytes"] == 2
    with pytest.raises(ValueError, match="已存在"):
        asyncio.run(workspace_execution.sandbox_write_tool_result(
            session_ref=_ref(session), file_path="outputs/.tool-offload/r_1.json", content="{}",
        ))


def test_truncate_output():
    assert workspace_execution.truncate_output("short") == ("short", False)
    text = "x" * 20
    truncated, flag = workspace_execution.truncate_output(text, limit=5)
    assert flag is True and truncated.endswith("...[truncated]")
