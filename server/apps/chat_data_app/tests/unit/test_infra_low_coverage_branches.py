import asyncio
import importlib
import logging
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

import auth
from apps.chat_data_app.agent.skills import loader
from apps.chat_data_app.agent.tools import calculator
from apps.chat_data_app.agent.tools import skill_load
from apps.chat_data_app.route_modules import data_analyze_workspace
from apps.chat_data_app.schema import SkillImportFromWorkspaceRequest
from utils import logger as logger_module
from utils.lang_util import checkpoint, database, llm


def _payload(response):
    import json

    return json.loads(response.body.decode("utf-8"))


def test_authenticate_request_success_and_error_branches(monkeypatch):
    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "")
    with pytest.raises(auth.AuthenticationError, match="service token"):
        auth.authenticate_request(SimpleNamespace(headers={}))

    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "secret")
    with pytest.raises(auth.AuthenticationError, match="service token"):
        auth.authenticate_request(SimpleNamespace(headers={auth.SERVICE_AUTH_HEADER: "bad"}))

    with pytest.raises(auth.AuthenticationError, match="forwarded user"):
        auth.authenticate_request(SimpleNamespace(headers={auth.SERVICE_AUTH_HEADER: "secret"}))

    headers = {
        auth.SERVICE_AUTH_HEADER: "secret",
        auth.SERVICE_USER_ID_HEADER: "u1",
        auth.SERVICE_USERNAME_HEADER: "å¼ ä¸\u0089",
    }
    user = auth.authenticate_request(SimpleNamespace(headers=headers))
    assert user.user_id == "u1"
    assert user.username == "张三"
    assert auth._normalize_header_text(" plain ") == "plain"


def test_llm_count_tokens_and_get_llm_kwargs(monkeypatch):
    captured = {}

    def fake_init_chat_model(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model=kwargs["model"])

    monkeypatch.setattr(llm, "init_chat_model", fake_init_chat_model)
    token_count = llm.count_tokens(None, [HumanMessage(content="12345678"), HumanMessage(content="")], tools=[{"name": "t"}])
    assert token_count == 60

    model = llm.get_llm(
        model="model-a",
        streaming=True,
        stream_usage=True,
        temperature=0.1,
        max_tokens=100,
        timeout=30,
        api_key="key",
        base_url="http://llm",
        extra_body={"enable": True},
    )
    assert model.model == "model-a"
    assert captured == {
        "model": "model-a",
        "model_provider": "openai",
        "temperature": 0.1,
        "api_key": "key",
        "base_url": "http://llm",
        "streaming": True,
        "max_tokens": 100,
        "timeout": 30,
        "stream_usage": True,
        "extra_body": {"enable": True},
    }


def test_checkpoint_config_branches(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "sqlite")
    monkeypatch.setattr(checkpoint, "DB_PATH", str(tmp_path / "checkpoints.db"))
    sqlite_context = checkpoint.get_checkpointer()
    assert isinstance(sqlite_context, checkpoint._SqliteCheckpointerContext)
    assert checkpoint.get_checkpoint_info()["type"] == "sqlite"

    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "mysql")
    monkeypatch.setattr(checkpoint, "MYSQL_USER", "user")
    monkeypatch.setattr(checkpoint, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(checkpoint, "MYSQL_PORT", 3306)
    monkeypatch.setattr(checkpoint, "MYSQL_DB", "db")
    monkeypatch.setattr(checkpoint, "_get_mysql_password", lambda: "pwd")
    assert checkpoint.get_checkpoint_info()["type"] == "mysql"

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, conn_string):
            return {"conn_string": conn_string}

    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.mysql.aio", SimpleNamespace(AIOMySQLSaver=FakeSaver))
    mysql_context = checkpoint._get_mysql_checkpointer()
    assert mysql_context["conn_string"] == "mysql+aiomysql://user:pwd@127.0.0.1:3306/db"

    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "unknown")
    with pytest.raises(ValueError, match="不支持"):
        checkpoint.get_checkpointer()
    assert checkpoint.get_checkpoint_info() == {"type": "unknown"}


def test_setup_checkpointer_sqlite_and_mysql(monkeypatch):
    events = []

    class FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, sql):
            events.append(("execute", sql))

        async def commit(self):
            events.append(("commit", None))

    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "sqlite")
    monkeypatch.setitem(sys.modules, "aiosqlite", SimpleNamespace(connect=lambda _path: FakeConn()))
    monkeypatch.setattr("utils.lang_util.database.init_database", lambda: asyncio.sleep(0, result=events.append(("init", None))))
    asyncio.run(checkpoint.setup_checkpointer())
    assert events == [("execute", "PRAGMA journal_mode=WAL"), ("commit", None), ("init", None)]

    events.clear()
    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "mysql")
    asyncio.run(checkpoint.setup_checkpointer())
    assert events == [("init", None)]


def test_database_session_commit_rollback_and_info(monkeypatch):
    database._engine = None
    database._session_factory = None
    monkeypatch.setattr(database, "_get_mysql_password", lambda: "pwd")
    url = database.get_database_url()
    assert url.drivername == "mysql+aiomysql"
    assert database.get_database_info()["password"] == "(已设置)"

    class FakeEngine:
        def __init__(self):
            self.disposed = False

        async def dispose(self):
            self.disposed = True

    fake_engine = FakeEngine()
    monkeypatch.setattr(database, "create_engine", lambda: fake_engine)
    assert database.get_engine() is fake_engine
    assert database.get_engine() is fake_engine

    class FakeSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    sessions = []

    class FakeFactory:
        def __call__(self):
            session = FakeSession()
            sessions.append(session)
            return session

    monkeypatch.setattr(database, "get_session_factory", lambda: FakeFactory())

    async def use_success():
        async with database.get_session() as session:
            assert session is sessions[-1]

    asyncio.run(use_success())
    assert sessions[-1].committed is True

    async def use_failure():
        async with database.get_session():
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(use_failure())
    assert sessions[-1].rolled_back is True

    asyncio.run(database.close_database())
    assert fake_engine.disposed is True


def test_logger_env_and_manager_branches(monkeypatch, tmp_path):
    assert logger_module._env_text("MISSING_LOG_ENV", "fallback") == "fallback"
    monkeypatch.setenv("LOG_LEVEL", "  ")
    assert logger_module.get_current_log_level() == "INFO"
    monkeypatch.setenv("LOG_CONSOLE", "off")
    assert logger_module.get_current_console_output() is False
    monkeypatch.setenv("LOG_CONSOLE", "yes")
    assert logger_module.get_current_console_output() is True
    assert logger_module.resolve_log_path("").endswith("logs")
    assert logger_module.resolve_log_path(str(tmp_path)) == str(tmp_path)

    logger_module.LoggerManager._loggers.clear()
    logger_module.LoggerManager._file_handler = None
    logger_module.LoggerManager._file_handler_key = None
    logger_module.LoggerManager._console_handler = None
    logger_module.LoggerManager._formatter = None

    log = logger_module.LoggerManager.get_logger(
        "unit.logger",
        log_level="BAD",
        log_dir=str(tmp_path),
        log_file_name="unit.log",
        console_output=False,
        file_output=True,
    )
    assert log.level == logging.INFO
    assert logger_module.LoggerManager.get_logger("unit.logger") is log
    handler = logger_module.LoggerManager._get_file_handler(str(tmp_path), "unit.log", 30)
    assert logger_module.LoggerManager._get_file_handler(str(tmp_path), "unit.log", 30) is handler
    handler2 = logger_module.LoggerManager._get_file_handler(str(tmp_path), "other.log", 30)
    assert handler2 is not handler

    logger_module.LoggerManager.set_level("unit.logger", "DEBUG")
    assert log.level == logging.DEBUG
    logger_module.LoggerManager.set_global_level("ERROR")
    assert log.level == logging.ERROR

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    public_log = logger_module.get_logger("unit.public")
    assert public_log.level == logging.DEBUG


def test_skills_loader_frontmatter_metadata_and_activation(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skill-a"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: skill-a\n"
        "metadata: not-a-dict\n"
        "input_schema: bad\n"
        "---\n"
        "正文",
        encoding="utf-8",
    )
    meta = loader.parse_metadata(skill_file)
    assert meta["name"] == "skill-a"
    assert meta["title"] == "skill-a"
    assert meta["input_schema"] == {}
    assert loader.discover_skills([skill_dir, tmp_path / "missing"]) == [skill_file]

    no_name = tmp_path / "no-name.md"
    no_name.write_text("---\ntitle: NoName\n---\nbody", encoding="utf-8")
    assert loader.parse_metadata(no_name) is None
    assert loader.parse_metadata(tmp_path / "missing.md") is None

    monkeypatch.setattr(loader, "_metadata_cache", {"skill-a": meta})
    activated = loader.activate_skill("skill-a")
    assert activated["content"] == "正文"
    assert activated["output_type"] == "text"
    assert loader.activate_skill("missing") is None

    bad_meta = dict(meta)
    bad_meta["path"] = str(tmp_path / "missing-skill.md")
    monkeypatch.setattr(loader, "_metadata_cache", {"bad": bad_meta})
    assert loader.activate_skill("bad") is None


def test_skills_loader_yaml_fallback_and_load_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "yaml", SimpleNamespace(safe_load=lambda _raw: (_ for _ in ()).throw(RuntimeError("bad yaml"))))
    assert loader._parse_frontmatter("---\nname: fallback\nquoted: 'v'\n---\nbody") == {"name": "fallback", "quoted": "v"}
    assert loader._parse_frontmatter("no frontmatter") == {}

    root = tmp_path / "skills"
    good = root / "good"
    hidden = root / "_hidden"
    good.mkdir(parents=True)
    hidden.mkdir()
    (good / "SKILL.md").write_text("---\nname: good\ndescription: Good & safe\n---\nbody", encoding="utf-8")
    (hidden / "SKILL.md").write_text("---\nname: hidden\n---\nbody", encoding="utf-8")

    class FakePath:
        @staticmethod
        def parent():
            return root

    monkeypatch.setattr(loader, "_metadata_cache", None)
    monkeypatch.setattr(loader.Path, "__file__", str(root / "loader.py"), raising=False)
    monkeypatch.setattr(loader, "__file__", str(root / "loader.py"), raising=False)
    metadata = loader.load_metadata()
    assert "good" in metadata
    assert "hidden" not in metadata
    prompt = loader.format_skills_prompt([{"name": "good", "title": "G", "description": "A&B", "domain": "d", "category": "c"}])
    assert "&amp;" in prompt


@pytest.mark.asyncio
async def test_data_analyze_workspace_routes_branches(monkeypatch, tmp_path):
    user = SimpleNamespace(user_id="u1", username="用户1")
    monkeypatch.setattr(data_analyze_workspace, "_get_current_user", lambda _request: user)
    monkeypatch.setattr(data_analyze_workspace, "_can_access_data_analyze_conversation", lambda conversation_id, user_id: asyncio.sleep(0, result=conversation_id == "conv_ok"))
    monkeypatch.setattr(data_analyze_workspace, "ensure_data_analyze_workspace", lambda conversation_id: {"root": str(tmp_path / conversation_id)})
    monkeypatch.setattr(data_analyze_workspace, "list_workspace_files_from_sandbox", lambda **_kwargs: asyncio.sleep(0, result=[{"path": "a.txt"}]))
    monkeypatch.setattr(data_analyze_workspace, "read_workspace_file_from_sandbox", lambda **_kwargs: asyncio.sleep(0, result={"content": "hello"}))

    assert _payload(await data_analyze_workspace.list_data_analyze_workspace_files("denied", SimpleNamespace()))["code"] == 40404
    assert _payload(await data_analyze_workspace.list_data_analyze_workspace_files("conv_ok", SimpleNamespace()))["data"]["files"] == [{"path": "a.txt"}]
    assert _payload(await data_analyze_workspace.read_data_analyze_workspace_file("denied", "a.txt", SimpleNamespace()))["code"] == 40404
    assert _payload(await data_analyze_workspace.read_data_analyze_workspace_file("conv_ok", "a.txt", SimpleNamespace()))["data"]["content"] == "hello"

    skill_dir = tmp_path / "conv_ok" / "workspace" / "skill-a"
    monkeypatch.setattr(data_analyze_workspace, "resolve_workspace_path", lambda root, path: (tmp_path / "conv_ok" / path).resolve())
    monkeypatch.setattr(data_analyze_workspace.skill_service, "import_user_skill_from_directory_async", lambda path, user_id, operator_name="": asyncio.sleep(0, result={"path": str(path), "user_id": user_id, "operator_name": operator_name}))
    imported = _payload(await data_analyze_workspace.import_data_analyze_workspace_skill(
        "conv_ok",
        SkillImportFromWorkspaceRequest(skill_dir_path="workspace/skill-a"),
        SimpleNamespace(),
    ))
    assert imported["data"]["path"] == str(skill_dir.resolve())
    assert imported["data"]["operator_name"] == "用户1"

    outside = _payload(await data_analyze_workspace.import_data_analyze_workspace_skill(
        "conv_ok",
        SkillImportFromWorkspaceRequest(skill_dir_path="../outside"),
        SimpleNamespace(),
    ))
    assert outside["code"] == 40055


@pytest.mark.asyncio
async def test_data_analyze_workspace_exception_branches(monkeypatch):
    original_can_access = data_analyze_workspace._can_access_data_analyze_conversation
    monkeypatch.setattr(data_analyze_workspace, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="用户1"))
    monkeypatch.setattr(data_analyze_workspace, "_can_access_data_analyze_conversation", lambda *_args: asyncio.sleep(0, result=True))
    monkeypatch.setattr(data_analyze_workspace, "ensure_data_analyze_workspace", lambda _conversation_id: (_ for _ in ()).throw(ValueError("bad workspace")))
    assert _payload(await data_analyze_workspace.list_data_analyze_workspace_files("conv", SimpleNamespace()))["code"] == 40001
    assert _payload(await data_analyze_workspace.read_data_analyze_workspace_file("conv", "a.txt", SimpleNamespace()))["code"] == 40001

    monkeypatch.setattr(data_analyze_workspace, "ensure_data_analyze_workspace", lambda _conversation_id: (_ for _ in ()).throw(RuntimeError("disk down")))
    assert _payload(await data_analyze_workspace.list_data_analyze_workspace_files("conv", SimpleNamespace()))["code"] == 50053
    assert _payload(await data_analyze_workspace.read_data_analyze_workspace_file("conv", "a.txt", SimpleNamespace()))["code"] == 50054
    assert _payload(await data_analyze_workspace.import_data_analyze_workspace_skill("conv", SkillImportFromWorkspaceRequest(skill_dir_path="workspace/a"), SimpleNamespace()))["code"] == 50055

    @asynccontextmanager
    async def fake_session():
        class Result:
            def scalar_one_or_none(self):
                return 1

        class Session:
            async def execute(self, _stmt):
                return Result()

        yield Session()

    monkeypatch.setattr(data_analyze_workspace, "get_session", fake_session)
    monkeypatch.setattr(data_analyze_workspace, "_can_access_data_analyze_conversation", original_can_access)
    assert await data_analyze_workspace._can_access_data_analyze_conversation("conv", "u1") is True
    assert await data_analyze_workspace._can_access_data_analyze_conversation("", "u1") is False


def test_calculator_and_skill_load_extra_branches(monkeypatch, tmp_path):
    assert calculator._format_decimal(calculator.Decimal("1.2300")) == "1.23"
    assert calculator._calculate_item({"expression": ""})["error"] == "expression 不能为空"
    assert calculator._calculate_item({"expression": "1/0"})["error"] == "除数不能为 0"
    assert calculator._calculate_item({"expression": "(1+2)", "decimal_places": 0})["result"] == "3"

    payload = {
        "name": "skill-a",
        "title": "技能A",
        "visible_path": "/runtime/skill-a",
        "references": ["guide"],
        "scripts": ["run.py"],
        "assets": ["a.png"],
        "skill_md": "# Skill",
    }
    assert "runtime_path=/runtime/skill-a" in skill_load.format_skill_load_result(payload)

    skill_dir = tmp_path / "skill-a"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill A", encoding="utf-8")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    (skill_dir / "assets").mkdir()
    (skill_dir / "assets" / "a.png").write_bytes(b"png")

    monkeypatch.setattr(skill_load, "resolve_skill_name", lambda name: name.strip())
    monkeypatch.setattr(skill_load.skill_service, "resolve_skill_path_async", lambda _name, user_id="": asyncio.sleep(0, result={"path": str(skill_dir / "SKILL.md"), "title": "技能A", "description": "描述"}))
    built, source_dir = asyncio.run(skill_load._build_skill_payload_async(" skill-a ", user_id="u1"))
    assert source_dir == skill_dir
    assert built["references"] == ["guide"]
    assert built["scripts"] == ["run.py"]
    assert built["assets"] == ["a.png"]

    monkeypatch.setattr(skill_load.skill_service, "resolve_skill_path_async", lambda _name, user_id="": asyncio.sleep(0, result=None))
    with pytest.raises(ValueError, match="技能不存在"):
        asyncio.run(skill_load._build_skill_payload_async("missing"))

    tool = skill_load.create_skill_load_tool(str(tmp_path), user_id="u1")
    monkeypatch.setattr(skill_load, "_build_skill_payload_async", lambda *_args, **_kwargs: asyncio.sleep(0, result=(dict(built), skill_dir)))
    monkeypatch.setattr(skill_load, "upload_skill_directory_to_sandbox", lambda **_kwargs: asyncio.sleep(0, result="/sandbox/skill-a"))
    result = asyncio.run(tool.ainvoke({"skill_name": "skill-a"}))
    assert "runtime_path=/sandbox/skill-a" in result

    async def raise_payload(*_args, **_kwargs):
        raise RuntimeError("load failed")

    monkeypatch.setattr(skill_load, "_build_skill_payload_async", raise_payload)
    assert "skill_load 执行失败: load failed" in asyncio.run(tool.ainvoke({"skill_name": "skill-a"}))
