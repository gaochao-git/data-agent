import ast
import asyncio
import importlib
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy.exc import DBAPIError, OperationalError

import auth
from apps.chat_data_app.agent.skills import loader
from apps.chat_data_app.agent.tools import calculator
from apps.chat_data_app.routers import conversation_workspace, dependencies
from apps.chat_data_app.services.agent_run import conversation_workspace as conversation_workspace_service
from apps.chat_data_app.services.semantic.core.common import build_table_columns_map
from apps.chat_data_app.services.agent_run.conversation_workspace import ConversationWorkspaceRuntime
from apps.chat_data_app.schema import DatasourceSourceItem, SkillImportFromWorkspaceRequest
from middleware import api_logging as api_logging_module
from utils.error_codes import ErrorCode
from utils import alarm as alarm_module
from utils import response as response_module
from utils import logger as logger_module
from utils import checkpoint, common_util, database, llm
from utils.db_failover import DatabaseEndpointSelector, is_mysql_connection_error, parse_database_endpoints


def _payload(response):
    import json

    return json.loads(response.body.decode("utf-8"))


def test_production_business_response_codes_are_centralized():
    server_root = Path(__file__).resolve().parents[4]
    scan_roots = [
        server_root / "apps" / "chat_data_app",
        server_root / "utils",
    ]
    violations = []
    for root in scan_roots:
        for path in root.rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text()
            compact = text.replace(" ", "")
            if "status_code*100" in compact or "*100+1" in compact:
                violations.append(f"{path}: dynamic error code formula")
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if name != "business_response":
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "code" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, int):
                        violations.append(f"{path}:{node.lineno}: business_response code must use ErrorCode")
    assert violations == []


def test_error_code_values_are_unique():
    seen = {}
    duplicates = []
    for name, item in ErrorCode.__members__.items():
        value = int(item.value)
        previous = seen.get(value)
        if previous:
            duplicates.append(f"{value}: {previous}, {name}")
        else:
            seen[value] = name
    assert duplicates == []


def test_alarm_helper_formats_and_marks_same_llm_connection_exception_once(monkeypatch):
    records = []
    monkeypatch.setattr(alarm_module, "get_alarm_logger", lambda: SimpleNamespace(error=lambda *args, **kwargs: records.append((args, kwargs))))

    alarm_module.emit_alarm(
        "boom" * 200,
        alarm_id="TEST_ALARM",
        request_type="TEST",
        ext1={"sql": "select 1", "nested": {"source": "unit"}},
        ext2="x" * 1200,
    )

    assert records[0][0][0].endswith("(truncated,total=800)")
    assert records[0][1]["extra"]["alarm_id"] == "TEST_ALARM"
    assert records[0][1]["extra"]["request_type"] == "TEST"
    assert records[0][1]["extra"]["ext1"] == "sql=select 1 nested.source=unit"
    assert "(truncated,total=1200)" in records[0][1]["extra"]["ext2"]
    alarm_module.emit_alarm("boom", alarm_id="TEST_ALARM", request_type="TEST")
    alarm_module.emit_alarm("boom", alarm_id="TEST_ALARM", request_type="TEST")
    assert len(records) == 3

    llm_error = RuntimeError("API connection error")
    assert alarm_module.is_llm_connection_exception(llm_error) is True
    assert alarm_module.emit_llm_connection_alarm_if_needed(llm_error, ext1={"purpose": "test"}) is True
    assert alarm_module.emit_llm_connection_alarm_if_needed(llm_error, ext1={"purpose": "test"}) is False

    # 大模型异常大类：连接失败与首 token 超时共用 LLM_EXCEPTION，细分落在 error_code。
    llm_records = records[3:]
    assert len(llm_records) == 1
    assert llm_records[0][1]["extra"]["alarm_id"] == "LLM_EXCEPTION"
    assert llm_records[0][1]["extra"]["error_code"] == alarm_module.LLM_FAIL_CONNECTION

    alarm_module.emit_llm_first_token_timeout_alarm(elapsed_ms=61000, threshold_seconds=60)
    timeout_record = records[-1][1]["extra"]
    assert timeout_record["alarm_id"] == "LLM_EXCEPTION"
    assert timeout_record["error_code"] == alarm_module.LLM_FAIL_FIRST_TOKEN_TIMEOUT


def test_api_alarm_alarms_split_external_and_internal(monkeypatch):
    """外部依赖与平台自身接口异常共用 API_CALL_EXCEPTION，细分落在 error_code。"""
    records = []
    monkeypatch.setattr(alarm_module, "get_alarm_logger", lambda: SimpleNamespace(error=lambda *args, **kwargs: records.append((args, kwargs))))

    alarm_module.emit_external_api_alarm("hive-gateway", TimeoutError("timed out"), method="POST", url="http://x/y")
    alarm_module.emit_api_call_exception_path = None
    alarm_module.emit_api_call_alarm("/chat/stream", ValueError("boom"), method="GET")

    external_extra = records[0][1]["extra"]
    assert external_extra["alarm_id"] == "API_CALL_EXCEPTION"
    assert external_extra["error_code"] == alarm_module.API_FAIL_EXTERNAL
    internal_extra = records[1][1]["extra"]
    assert internal_extra["alarm_id"] == "API_CALL_EXCEPTION"
    assert internal_extra["error_code"] == alarm_module.API_FAIL_INTERNAL


def test_dangerous_sql_alarm_splits_reason_code_from_alarm_id(monkeypatch):
    """alarm_id 固定为 OMS 契约值，细分原因落在 error_code。"""
    records = []
    warnings = []
    monkeypatch.setattr(alarm_module, "get_alarm_logger", lambda: SimpleNamespace(error=lambda *args, **kwargs: records.append((args, kwargs))))
    monkeypatch.setattr(alarm_module.logger, "warning", lambda *args, **kwargs: warnings.append(args))

    cases = [
        ("一次只能执行一条 SQL，请拆分后分别执行", None, alarm_module.SQL_BLOCK_MULTI_STATEMENT),
        ("只允许执行以下类型的 SQL: SELECT, SHOW", None, alarm_module.SQL_BLOCK_TYPE_NOT_ALLOWED),
        # 显式传入的 reason_code 优先于推断。
        ("任意文案", "CUSTOM_CODE", "CUSTOM_CODE"),
    ]
    for reason, reason_code, expected in cases:
        records.clear()
        alarm_module.emit_dangerous_sql_alarm("select 1", reason, reason_code=reason_code)
        extra = records[0][1]["extra"]
        assert extra["alarm_id"] == "DANGEROUS_SQL_BLOCKED"
        assert extra["error_code"] == expected

    # 解析失败等普通校验失败不算高危：不发 DANGEROUS_SQL_BLOCKED，仅记 warning。
    for reason in ("SQL 解析失败", "SQL 为空", 'Expecting "(" ...'):
        records.clear()
        warnings.clear()
        alarm_module.emit_dangerous_sql_alarm("select 1", reason)
        assert records == []
        assert len(warnings) == 1


def test_service_token_and_forwarded_user_auth_branches(monkeypatch):
    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "")
    with pytest.raises(auth.AuthenticationError, match="service token"):
        auth.validate_service_token(None)

    monkeypatch.setattr(auth, "SERVICE_AUTH_TOKEN", "secret")
    with pytest.raises(auth.AuthenticationError, match="service token"):
        auth.validate_service_token("bad")

    with pytest.raises(auth.AuthenticationError, match="forwarded user"):
        auth.get_forwarded_user(SimpleNamespace(headers={}))

    headers = {
        auth.SERVICE_AUTH_HEADER: "secret",
        auth.SERVICE_USER_ID_HEADER: "u1",
        auth.SERVICE_USERNAME_HEADER: "å¼ ä¸\u0089",
    }
    auth.validate_service_token(headers[auth.SERVICE_AUTH_HEADER])
    user = auth.get_forwarded_user(SimpleNamespace(headers=headers))
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


def test_llm_get_llm_omits_optional_kwargs_and_patch_import_failure(monkeypatch):
    captured = {}

    def fake_init_chat_model(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model=kwargs["model"])

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "langchain_openai.chat_models.base":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    llm._REASONING_PATCHED = False
    monkeypatch.setattr("builtins.__import__", fake_import)
    llm._patch_langchain_openai_reasoning_fields()
    assert llm._REASONING_PATCHED is True

    llm._REASONING_PATCHED = True
    monkeypatch.setattr(llm, "init_chat_model", fake_init_chat_model)
    monkeypatch.setenv("OPENAI_ANALYSIS_MODEL", "env-model")
    monkeypatch.setenv("LLM_PROVIDER", "fake-provider")
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://env")

    model = llm.get_llm(streaming=False, max_tokens=0, timeout=0, stream_usage=None, extra_body={})

    assert model.model == "env-model"
    assert captured == {
        "model": "env-model",
        "model_provider": "fake-provider",
        "temperature": 0.7,
        "api_key": "env-key",
        "base_url": "http://env",
        "streaming": False,
    }


def test_llm_patch_preserves_stream_reasoning_content():
    import langchain_openai.chat_models.base as openai_chat_base

    llm._REASONING_PATCHED = False
    llm._patch_langchain_openai_reasoning_fields()

    chunk = openai_chat_base._convert_delta_to_message_chunk(
        {"role": "assistant", "content": "", "reasoning_content": "思考内容"},
        openai_chat_base.AIMessageChunk,
    )

    assert chunk.additional_kwargs["reasoning_content"] == "思考内容"


def test_llm_patch_ignores_non_reasoning_or_non_ai_chunks():
    import langchain_openai.chat_models.base as openai_chat_base

    llm._REASONING_PATCHED = False
    llm._patch_langchain_openai_reasoning_fields()
    llm._patch_langchain_openai_reasoning_fields()

    human_chunk = openai_chat_base._convert_delta_to_message_chunk(
        {"role": "user", "content": "hello", "reasoning_content": "ignored"},
        openai_chat_base.HumanMessageChunk,
    )
    empty_reasoning = openai_chat_base._convert_delta_to_message_chunk(
        {"role": "assistant", "content": "hello", "reasoning_content": ""},
        openai_chat_base.AIMessageChunk,
    )

    assert "reasoning_content" not in human_chunk.additional_kwargs
    assert "reasoning_content" not in empty_reasoning.additional_kwargs


def test_checkpoint_config_branches(monkeypatch, tmp_path):
    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "sqlite")
    monkeypatch.setattr(checkpoint, "DB_PATH", str(tmp_path / "checkpoints.db"))
    sqlite_context = checkpoint.get_checkpointer()
    assert isinstance(sqlite_context, checkpoint._SqliteCheckpointerContext)
    assert checkpoint.get_checkpoint_info()["type"] == "sqlite"

    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "mysql")
    monkeypatch.setattr(checkpoint, "MYSQL_USER", "user")
    monkeypatch.setattr(checkpoint, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(checkpoint, "_endpoint_selector", DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306")))
    monkeypatch.setattr(checkpoint, "MYSQL_DB", "db")
    monkeypatch.setattr(checkpoint, "_get_mysql_password", lambda: "pwd")
    assert checkpoint.get_checkpoint_info()["type"] == "mysql"

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, conn_string):
            return {"conn_string": conn_string}

    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.mysql.aio", SimpleNamespace(AIOMySQLSaver=FakeSaver))
    get_context = checkpoint.get_checkpointer()
    assert get_context.context_factory(checkpoint._endpoint_selector.current())["conn_string"] == "mysql+aiomysql://user:pwd@127.0.0.1:3306/db"
    mysql_context = checkpoint._get_mysql_checkpointer()
    assert mysql_context.context_factory(checkpoint._endpoint_selector.current())["conn_string"] == "mysql+aiomysql://user:pwd@127.0.0.1:3306/db"

    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "unknown")
    with pytest.raises(ValueError, match="不支持"):
        checkpoint.get_checkpointer()
    assert checkpoint.get_checkpoint_info() == {"type": "unknown"}


def test_database_endpoint_parsing_and_selector():
    endpoints = parse_database_endpoints("62.1.1.1:3306, 62.1.1.2:3307,62.1.1.3:3308")
    assert [endpoint.label() for endpoint in endpoints] == ["62.1.1.1:3306", "62.1.1.2:3307", "62.1.1.3:3308"]

    single_endpoint = parse_database_endpoints("62.1.1.4:3306")
    assert [endpoint.label() for endpoint in single_endpoint] == ["62.1.1.4:3306"]

    selector = DatabaseEndpointSelector(endpoints)
    assert selector.current().label() == "62.1.1.1:3306"
    previous, current = selector.advance()
    assert previous.label() == "62.1.1.1:3306"
    assert current.label() == "62.1.1.2:3307"
    previous, current, advanced = selector.advance_if_current(endpoints[0])
    assert advanced is False
    assert previous.label() == "62.1.1.2:3307"
    assert current.label() == "62.1.1.2:3307"
    previous, current, advanced = selector.advance_if_current(endpoints[1])
    assert advanced is True
    assert previous.label() == "62.1.1.2:3307"
    assert current.label() == "62.1.1.3:3308"
    selector.set_current(endpoints[2])
    assert selector.current().label() == "62.1.1.3:3308"

    with pytest.raises(ValueError, match="端口无效"):
        parse_database_endpoints("127.0.0.1:bad")

    with pytest.raises(ValueError, match="必须使用 host:port"):
        parse_database_endpoints("127.0.0.1")

    with pytest.raises(ValueError, match="必须使用 host:port"):
        parse_database_endpoints("127.0.0.1:3306,127.0.0.2")

    with pytest.raises(ValueError, match="不能为空"):
        parse_database_endpoints(" , ")


def test_database_endpoint_parser_remaining_error_edges():
    endpoints = parse_database_endpoints("127.0.0.1:3306,127.0.0.1:3306")
    assert [endpoint.label() for endpoint in endpoints] == ["127.0.0.1:3306"]

    with pytest.raises(ValueError, match="端口不能为空"):
        parse_database_endpoints("127.0.0.1:")
    with pytest.raises(ValueError, match="主机不能为空"):
        parse_database_endpoints(":3306")
    with pytest.raises(ValueError, match="端口超出范围"):
        parse_database_endpoints("127.0.0.1:0")
    with pytest.raises(ValueError, match="不能为空"):
        DatabaseEndpointSelector([])

    invalidated = DBAPIError("select 1", None, Exception("lost"), connection_invalidated=True)
    assert is_mysql_connection_error(invalidated) is True
    assert is_mysql_connection_error(SimpleNamespace(args=())) is False
    assert is_mysql_connection_error(SimpleNamespace(args=("bad",))) is False


def test_checkpoint_mysql_failover_retries_after_enter_error(monkeypatch):
    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "mysql")
    monkeypatch.setattr(checkpoint, "MYSQL_USER", "user")
    monkeypatch.setattr(checkpoint, "MYSQL_DB", "db")
    monkeypatch.setattr(checkpoint, "_get_mysql_password", lambda: "pwd")
    monkeypatch.setattr(
        checkpoint,
        "_endpoint_selector",
        DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307")),
    )
    alarm_records = []
    monkeypatch.setattr(checkpoint, "emit_database_connection_alarm", lambda *args, **kwargs: alarm_records.append((args, kwargs)))

    class FakeContext:
        def __init__(self, conn_string):
            self.conn_string = conn_string

        async def __aenter__(self):
            FakeSaver.enter_calls.append(self.conn_string)
            if len(FakeSaver.enter_calls) == 1:
                raise OperationalError(statement=None, params=None, orig=SimpleNamespace(args=(2003, "down")))
            return {"conn_string": self.conn_string}

        async def __aexit__(self, *_args):
            return False

    class FakeSaver:
        create_calls = []
        enter_calls = []

        @classmethod
        def from_conn_string(cls, conn_string):
            cls.create_calls.append(conn_string)
            return FakeContext(conn_string)

    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.mysql.aio", SimpleNamespace(AIOMySQLSaver=FakeSaver))
    context = checkpoint._get_mysql_checkpointer()

    async def enter_context():
        async with context as checkpointer_obj:
            return checkpointer_obj

    checkpointer_obj = asyncio.run(enter_context())
    assert FakeSaver.create_calls == [
        "mysql+aiomysql://user:pwd@127.0.0.1:3306/db",
        "mysql+aiomysql://user:pwd@127.0.0.2:3307/db",
    ]
    assert FakeSaver.enter_calls == FakeSaver.create_calls
    assert checkpointer_obj["conn_string"] == "mysql+aiomysql://user:pwd@127.0.0.2:3307/db"
    assert alarm_records == []


def test_checkpoint_mysql_failover_raises_non_connection_enter_error(monkeypatch):
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    monkeypatch.setattr(checkpoint, "_endpoint_selector", selector)

    class BadContext:
        async def __aenter__(self):
            raise RuntimeError("enter down")

        async def __aexit__(self, *_args):
            return False

    context = checkpoint._MySQLCheckpointerFailoverContext(lambda _endpoint: BadContext())

    async def enter_context():
        async with context:
            pass

    with pytest.raises(RuntimeError, match="enter down"):
        asyncio.run(enter_context())

    assert selector.current().label() == "127.0.0.1:3306"


def test_checkpoint_mysql_failover_advances_after_body_connection_error(monkeypatch):
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    monkeypatch.setattr(checkpoint, "_endpoint_selector", selector)
    alarm_records = []
    monkeypatch.setattr(checkpoint, "emit_database_connection_alarm", lambda *args, **kwargs: alarm_records.append((args, kwargs)))

    class Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    endpoint = selector.current()
    context = checkpoint._MySQLCheckpointerFailoverContext(lambda _endpoint: Context())
    context.endpoint = endpoint
    err = OperationalError(statement="select 1", params=None, orig=SimpleNamespace(args=(2006, "gone away")))

    async def use_context():
        async with context:
            raise err

    with pytest.raises(OperationalError):
        asyncio.run(use_context())

    assert selector.current().label() == "127.0.0.2:3307"
    assert alarm_records == []


def test_checkpoint_context_noop_exit_and_stale_endpoint(monkeypatch):
    sqlite_context = checkpoint._SqliteCheckpointerContext("/tmp/unused.db")

    async def exit_without_connection():
        assert await sqlite_context.__aexit__(None, None, None) is False

    asyncio.run(exit_without_connection())

    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    stale_endpoint = selector.current()
    selector.advance()
    monkeypatch.setattr(checkpoint, "_endpoint_selector", selector)
    checkpoint._advance_checkpointer_endpoint(stale_endpoint, RuntimeError("already advanced"))
    assert selector.current().label() == "127.0.0.2:3307"


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
    monkeypatch.setattr("utils.database.init_database", lambda: asyncio.sleep(0, result=events.append(("init", None))))
    asyncio.run(checkpoint.setup_checkpointer())
    assert events == [("execute", "PRAGMA journal_mode=WAL"), ("commit", None), ("init", None)]

    events.clear()
    monkeypatch.setattr(checkpoint, "CHECKPOINTER_TYPE", "mysql")
    monkeypatch.setattr(checkpoint, "select_available_checkpointer_endpoint", lambda: asyncio.sleep(0, result=events.append(("checkpoint", None))))
    asyncio.run(checkpoint.setup_checkpointer())
    assert events == [("checkpoint", None), ("init", None)]


def test_select_available_checkpointer_endpoint_selects_first_available(monkeypatch):
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    events = []

    class Context:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        async def __aenter__(self):
            events.append(("enter", self.endpoint.label()))
            return self

        async def __aexit__(self, *_args):
            events.append(("exit", self.endpoint.label()))
            return False

    monkeypatch.setattr(checkpoint, "_endpoint_selector", selector)
    monkeypatch.setattr(checkpoint, "_build_mysql_checkpointer_context_factory", lambda: Context)

    asyncio.run(checkpoint.select_available_checkpointer_endpoint())

    assert selector.current().label() == "127.0.0.1:3306"
    assert events == [("enter", "127.0.0.1:3306"), ("exit", "127.0.0.1:3306")]


def test_select_available_checkpointer_endpoint_skips_connection_errors(monkeypatch):
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    calls = []

    class Context:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        async def __aenter__(self):
            calls.append(self.endpoint.label())
            if self.endpoint.host == "127.0.0.1":
                raise OperationalError(statement=None, params=None, orig=SimpleNamespace(args=(2003, "down")))
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(checkpoint, "_endpoint_selector", selector)
    monkeypatch.setattr(checkpoint, "_build_mysql_checkpointer_context_factory", lambda: Context)

    asyncio.run(checkpoint.select_available_checkpointer_endpoint())

    assert calls == ["127.0.0.1:3306", "127.0.0.2:3307"]
    assert selector.current().label() == "127.0.0.2:3307"


def test_select_available_checkpointer_endpoint_raises_when_all_nodes_fail(monkeypatch):
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306"))

    class Context:
        def __init__(self, _endpoint):
            pass

        async def __aenter__(self):
            raise OperationalError(statement=None, params=None, orig=SimpleNamespace(args=(2003, "down")))

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(checkpoint, "_endpoint_selector", selector)
    monkeypatch.setattr(checkpoint, "_build_mysql_checkpointer_context_factory", lambda: Context)
    alarm_records = []
    monkeypatch.setattr(checkpoint, "emit_database_connection_alarm", lambda *args, **kwargs: alarm_records.append((args, kwargs)))

    with pytest.raises(ValueError, match="所有 checkpoint 库节点均不可用"):
        asyncio.run(checkpoint.select_available_checkpointer_endpoint())
    assert alarm_records[0][0][0] == "checkpoint库启动探测失败"
    assert alarm_records[0][1]["reason_code"] == alarm_module.DB_FAIL_CHECKPOINT


def test_database_session_commit_rollback_and_info(monkeypatch):
    database._engine = None
    database._session_factory = None
    monkeypatch.setattr(database, "_endpoint_selector", DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306")))
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


def test_create_engine_uses_bounded_recyclable_pool(monkeypatch):
    captured = {}

    class FakeEngine:
        pass

    def fake_create_async_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeEngine()

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(database, "get_database_url", lambda: "mysql://test")

    engine = database.create_engine()

    assert isinstance(engine, FakeEngine)
    assert captured["kwargs"] == {
        "pool_size": database.DB_POOL_SIZE,
        "max_overflow": database.DB_MAX_OVERFLOW,
        "pool_timeout": database.DB_POOL_TIMEOUT_SECONDS,
        "pool_recycle": database.DB_POOL_RECYCLE_SECONDS,
        "pool_pre_ping": True,
        "echo": False,
    }


def test_database_dispose_and_close_noop_branches(monkeypatch):
    database._engine = None
    database._session_factory = object()
    asyncio.run(database._dispose_engine())
    assert database._engine is None
    assert database._session_factory is None

    database._engine = None
    database._session_factory = object()
    asyncio.run(database.close_database())
    assert database._session_factory is not None
    database._session_factory = None


def test_database_session_connection_error_advances_endpoint(monkeypatch):
    database._engine = None
    database._session_factory = None
    alarm_records = []
    monkeypatch.setattr(database, "emit_database_connection_alarm", lambda *args, **kwargs: alarm_records.append((args, kwargs)))
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    monkeypatch.setattr(database, "_endpoint_selector", selector)

    class FakeEngine:
        def __init__(self):
            self.disposed = False

        async def dispose(self):
            self.disposed = True

    fake_engine = FakeEngine()
    database._engine = fake_engine

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(database, "get_session_factory", lambda: FakeFactory())
    monkeypatch.setattr(database, "_is_connection_error", lambda err: isinstance(err, RuntimeError))

    async def use_failure():
        async with database.get_session():
            raise RuntimeError("connection down")

    with pytest.raises(RuntimeError, match="connection down"):
        asyncio.run(use_failure())

    assert fake_engine.disposed is True
    assert database._engine is None
    assert database._session_factory is None
    assert selector.current().label() == "127.0.0.2:3307"
    assert alarm_records == []


def test_database_startup_probe_failure_writes_alarm(monkeypatch):
    alarm_records = []
    monkeypatch.setattr(database, "emit_database_connection_alarm", lambda *args, **kwargs: alarm_records.append((args, kwargs)))
    monkeypatch.setattr(database, "_endpoint_selector", DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307")))
    monkeypatch.setattr(database, "_probe_database_endpoint", lambda _endpoint: asyncio.sleep(0, result=False))

    with pytest.raises(ValueError, match="所有业务库节点均不可用"):
        asyncio.run(database.select_available_database_endpoint())

    assert alarm_records[0][0][0] == "业务库启动探测失败"
    assert alarm_records[0][1]["reason_code"] == alarm_module.DB_FAIL_CHATDATA
    assert alarm_records[0][1]["ext1"] == {"endpoints": ["127.0.0.1:3306", "127.0.0.2:3307"]}


def test_database_session_rollback_connection_error_advances_endpoint(monkeypatch):
    database._engine = None
    database._session_factory = None
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    monkeypatch.setattr(database, "_endpoint_selector", selector)

    class FakeEngine:
        async def dispose(self):
            pass

    database._engine = FakeEngine()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def commit(self):
            pass

        async def rollback(self):
            raise OperationalError(statement="rollback", params=None, orig=SimpleNamespace(args=(2006, "gone away")))

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(database, "get_session_factory", lambda: FakeFactory())

    async def use_failure():
        async with database.get_session():
            raise RuntimeError("business error")

    with pytest.raises(RuntimeError, match="business error"):
        asyncio.run(use_failure())

    assert selector.current().label() == "127.0.0.2:3307"


def test_database_failover_does_not_advance_stale_endpoint(monkeypatch):
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307"))
    monkeypatch.setattr(database, "_endpoint_selector", selector)
    stale_endpoint = selector.current()
    selector.advance()

    asyncio.run(database._failover_database_endpoint(stale_endpoint, RuntimeError("already switched")))
    assert selector.current().label() == "127.0.0.2:3307"


def test_database_connection_error_detection_is_selective():
    disconnected = OperationalError(
        statement="select 1",
        params=None,
        orig=SimpleNamespace(args=(2003, "connect failed")),
    )
    syntax_like = OperationalError(
        statement="select 1",
        params=None,
        orig=SimpleNamespace(args=(1064, "syntax")),
    )
    assert database._is_connection_error(disconnected) is True
    assert database._is_connection_error(syntax_like) is False
    assert database._is_connection_error(RuntimeError("boom")) is False
    assert is_mysql_connection_error(SimpleNamespace(args=(2013, "lost"))) is True


def test_database_probe_reraises_non_connection_error(monkeypatch):
    endpoint = parse_database_endpoints("127.0.0.1:3306")[0]
    monkeypatch.setattr(database, "_get_mysql_password", lambda: "pwd")

    class FakeConn:
        async def __aenter__(self):
            raise PermissionError("access denied")

        async def __aexit__(self, *_args):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConn()

        async def dispose(self):
            pass

    monkeypatch.setattr(database, "create_async_engine", lambda *args, **kwargs: FakeEngine())
    monkeypatch.setattr(database, "_is_connection_error", lambda err: False)

    with pytest.raises(PermissionError, match="access denied"):
        asyncio.run(database._probe_database_endpoint(endpoint))


def test_database_probe_connection_error_returns_false_and_success(monkeypatch):
    endpoint = parse_database_endpoints("127.0.0.1:3306")[0]
    monkeypatch.setattr(database, "_get_mysql_password", lambda: "pwd")

    class ErrorConn:
        async def __aenter__(self):
            raise RuntimeError("connect down")

        async def __aexit__(self, *_args):
            return False

    class SuccessConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, _sql):
            return None

    class FakeEngine:
        def __init__(self, conn):
            self.conn = conn
            self.disposed = False

        def connect(self):
            return self.conn

        async def dispose(self):
            self.disposed = True

    engines = [FakeEngine(ErrorConn()), FakeEngine(SuccessConn())]
    monkeypatch.setattr(database, "create_async_engine", lambda *args, **kwargs: engines.pop(0))
    monkeypatch.setattr(database, "_is_connection_error", lambda err: isinstance(err, RuntimeError))

    assert asyncio.run(database._probe_database_endpoint(endpoint)) is False
    assert asyncio.run(database._probe_database_endpoint(endpoint)) is True


def test_select_available_endpoint_reraises_non_connection_error(monkeypatch):
    selector = DatabaseEndpointSelector(parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3306"))
    monkeypatch.setattr(database, "_endpoint_selector", selector)

    async def fake_probe(_endpoint):
        raise PermissionError("access denied")

    monkeypatch.setattr(database, "_probe_database_endpoint", fake_probe)

    with pytest.raises(PermissionError, match="access denied"):
        asyncio.run(database.select_available_database_endpoint())


def test_select_available_endpoint_sets_first_success_and_all_failed(monkeypatch):
    endpoints = parse_database_endpoints("127.0.0.1:3306,127.0.0.2:3307")
    selector = DatabaseEndpointSelector(endpoints)
    monkeypatch.setattr(database, "_endpoint_selector", selector)

    async def second_endpoint_only(endpoint):
        return endpoint.host == "127.0.0.2"

    monkeypatch.setattr(database, "_probe_database_endpoint", second_endpoint_only)
    asyncio.run(database.select_available_database_endpoint())
    assert selector.current().label() == "127.0.0.2:3307"

    async def always_false(_endpoint):
        return False

    monkeypatch.setattr(database, "_probe_database_endpoint", always_false)
    with pytest.raises(ValueError, match="所有业务库节点均不可用"):
        asyncio.run(database.select_available_database_endpoint())


def test_init_database_selects_endpoint_and_creates_engine(monkeypatch):
    calls = []

    async def fake_select():
        calls.append("select")

    monkeypatch.setattr(database, "select_available_database_endpoint", fake_select)
    monkeypatch.setattr(database, "get_engine", lambda: calls.append("engine") or object())

    asyncio.run(database.init_database())

    assert calls == ["select", "engine"]


def test_logger_env_and_manager_branches(monkeypatch, tmp_path):
    assert logger_module._env_text("MISSING_LOG_ENV", "fallback") == "fallback"
    monkeypatch.setenv("UNIT_BOOL_ENV", "  ")
    assert logger_module._env_bool("UNIT_BOOL_ENV", default=True) is True
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
    logger_module.LoggerManager._access_logger = None
    logger_module.LoggerManager._access_file_handler = None
    logger_module.LoggerManager._access_file_handler_key = None
    logger_module.LoggerManager._alarm_logger = None
    logger_module.LoggerManager._alarm_file_handler = None
    logger_module.LoggerManager._alarm_file_handler_key = None
    logger_module.LoggerManager._console_handler = None
    logger_module.LoggerManager._formatter = None
    logger_module.LoggerManager._access_formatter = None
    logger_module.LoggerManager._alarm_formatter = None

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

    existing_logger = logging.getLogger("unit.existing")
    existing_logger.handlers[:] = [logging.NullHandler()]
    logger_module.LoggerManager._loggers.pop("unit.existing", None)
    assert logger_module.LoggerManager.get_logger("unit.existing") is existing_logger


def test_log_file_derivation_and_formatters(monkeypatch):
    monkeypatch.delenv("LOG_APP_NAME", raising=False)
    assert logger_module.get_current_log_file() == "data-agent_app.log"
    assert logger_module.get_current_access_log_file() == "data-agent_acc.log"
    assert logger_module.get_current_alarm_log_file() == "data-agent_alarm.log"

    monkeypatch.setenv("LOG_APP_NAME", "chat-data-prod")
    assert logger_module.get_current_log_file() == "chat-data-prod_app.log"
    assert logger_module.get_current_access_log_file() == "chat-data-prod_acc.log"
    assert logger_module.get_current_alarm_log_file() == "chat-data-prod_alarm.log"

    monkeypatch.setenv("LOG_APP_NAME", "  ")
    assert logger_module.get_current_log_file() == "data-agent_app.log"

    access_record = logging.LogRecord("access", logging.INFO, __file__, 1, "ignored", (), None)
    access_record.request_id = "trace-1"
    access_record.request_type = "GET"
    access_record.app_name = "chat-data"
    access_record.idc = "idc-a"
    access_record.ip = "10.0.0.2"
    access_record.start_time = "2026-06-25 12:00:00.000"
    access_record.cost_time = "12ms"
    access_record.error_code = "0"
    access_record.ext1 = "client_ip:127.0.0.1"
    access_record.ext2 = "path:/demo"
    assert "|trace-1|-|GET|chat-data|idc-a|10.0.0.2|2026-06-25 12:00:00.000|12ms|0|client_ip:127.0.0.1|path:/demo" in logger_module.AccessLogFormatter().format(access_record)

    alarm_record = logging.LogRecord("alarm", logging.ERROR, __file__, 1, "boom", (), None)
    alarm_record.request_id = "trace-2"
    alarm_record.request_type = "POST"
    alarm_record.app_name = "chat-data"
    alarm_record.idc = "idc-b"
    alarm_record.ip = "10.0.0.3"
    alarm_record.error_code = "500"
    alarm_record.ext1 = "client_ip:127.0.0.2"
    alarm_record.ext2 = "path:/fail"
    assert "|trace-2|-|POST|chat-data|idc-b|10.0.0.3|500|boom|client_ip:127.0.0.2|path:/fail" in logger_module.AlarmLogFormatter().format(alarm_record)


def test_runtime_env_loading_from_ip_mapping(monkeypatch, tmp_path):
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "deploy_env_hosts.json").write_text(json.dumps({
        "dev": ["172.20.10.2"],
        "bj12": ["10.96."],
    }), encoding="utf-8")
    (conf_dir / ".env-dev").write_text("UNIT_RUNTIME_ENV_VALUE=dev-value\n", encoding="utf-8")
    (conf_dir / ".env-bj12").write_text("UNIT_RUNTIME_ENV_VALUE=bj12-value\n", encoding="utf-8")

    for key in (
        common_util.ENV_KEY,
        common_util.ACTIVE_ENV_KEY,
        "UNIT_RUNTIME_ENV_VALUE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(common_util, "get_local_ips", lambda: ("172.20.10.2",))

    result = common_util.load_runtime_env(tmp_path)

    assert result.active_env == "dev"
    assert result.local_ips == ("172.20.10.2",)
    assert result.loaded_files == (str(conf_dir / ".env-dev"),)
    assert common_util.get_active_idc() == "dev"
    assert common_util.get_active_deploy_env() == "dev"
    assert common_util.load_deploy_env_hosts(conf_dir)["bj12"] == ("10.96.",)
    assert common_util.resolve_deploy_env({"bj12": ("10.96.",)}, ("10.96.1.2",)) == "bj12"


def test_runtime_env_loading_errors_and_explicit_env(monkeypatch, tmp_path):
    conf_dir = tmp_path / "conf"
    conf_dir.mkdir()
    (conf_dir / "deploy_env_hosts.json").write_text(json.dumps({
        "dev": ["172.20."],
        "uat": ["172.20.10."],
    }), encoding="utf-8")
    (conf_dir / ".env-dev").write_text("UNIT_RUNTIME_ENV_VALUE=dev-value\n", encoding="utf-8")

    monkeypatch.setattr(common_util, "get_local_ips", lambda: ("172.20.10.2",))
    for key in (common_util.ENV_KEY, common_util.ACTIVE_ENV_KEY):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="同时匹配多个环境"):
        common_util.load_runtime_env(tmp_path)

    monkeypatch.setenv(common_util.ENV_KEY, "dev")
    result = common_util.load_runtime_env(tmp_path)
    assert result.active_env == "dev"

    monkeypatch.setenv(common_util.ENV_KEY, "missing")
    with pytest.raises(RuntimeError, match="未找到环境配置文件"):
        common_util.load_runtime_env(tmp_path)

    (conf_dir / "deploy_env_hosts.json").write_text(json.dumps({"bj12": ["10.96."]}), encoding="utf-8")
    monkeypatch.delenv(common_util.ENV_KEY, raising=False)
    with pytest.raises(RuntimeError, match="未识别当前机器所属环境"):
        common_util.load_runtime_env(tmp_path)

    monkeypatch.setenv(common_util.ENV_KEY, "dev")
    (conf_dir / "deploy_env_hosts.json").unlink()
    assert common_util.load_runtime_env(tmp_path).active_env == "dev"


def test_pipe_log_formatter_and_context_helpers(monkeypatch):
    formatter = logger_module.PipeLogFormatter()
    monkeypatch.setattr(logger_module, "LOCAL_IP", "10.0.0.1")
    monkeypatch.setattr(logger_module, "get_current_log_idc", lambda: "idc-test")
    logger_module.set_log_context(request_id="req-123", user_id="user-456", username="测试用户")

    record = logging.LogRecord(
        name="unit.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=42,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-record"
    record.user = "user-record-记录用户"
    record.idc = "idc-record"
    record.ip = "127.0.0.9"
    record.extra_fields = {"tenant": "ops"}
    record.custom = "value"

    output = formatter.format(record)

    assert "|INFO|" in output
    assert "|req-record|idc-record|127.0.0.9|" in output
    assert "|hello | user=user-record-记录用户 | custom=value, tenant=ops" in output

    logger_module.clear_log_context()
    assert logger_module.request_id_ctx.get() is None
    assert logger_module.user_ctx.get() is None


@pytest.mark.asyncio
async def test_api_access_log_middleware_sets_and_clears_context(monkeypatch):
    records = {"set": [], "clear": 0, "access_info": [], "exception": []}

    monkeypatch.setattr(api_logging_module, "logger", SimpleNamespace(
        exception=lambda msg, *args, **kwargs: records["exception"].append((msg, args, kwargs)),
    ))
    monkeypatch.setattr(api_logging_module, "access_logger", SimpleNamespace(
        info=lambda msg, *args, **kwargs: records["access_info"].append((msg, args, kwargs)),
    ))
    monkeypatch.setattr(api_logging_module, "set_log_context", lambda **kwargs: records["set"].append(kwargs))
    monkeypatch.setattr(api_logging_module, "clear_log_context", lambda: records.__setitem__("clear", records["clear"] + 1))
    monkeypatch.setattr(api_logging_module, "dump_masked_payload", lambda payload: payload)
    monkeypatch.setattr(api_logging_module, "_build_request_id", lambda: "req-abc")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    middleware = api_logging_module.APIAccessLogMiddleware(app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/demo",
        "query_string": b"a=1",
        "headers": [(b"x-user-id", b"user-1"), (b"x-username", "测试用户".encode("utf-8")), (b"user-agent", b"pytest")],
        "client": ("127.0.0.1", 12345),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)

    assert records["set"] == [{"request_id": "req-abc", "user": "user-1-测试用户"}]
    assert records["clear"] == 1
    assert sent[0]["type"] == "http.response.start"
    assert (b"x-request-id", b"req-abc") in sent[0]["headers"]
    assert len(records["access_info"]) == 1
    assert records["access_info"][0][0] == "api_access"
    assert records["access_info"][0][2]["extra"]["request_id"] == "req-abc"
    assert records["access_info"][0][2]["extra"]["error_code"] == "0"
    assert records["exception"] == []


@pytest.mark.asyncio
async def test_api_access_log_middleware_logs_access_error_with_alarm(monkeypatch):
    records = {"set": [], "clear": 0, "access_info": [], "exception": [], "alarm": []}

    monkeypatch.setattr(api_logging_module, "logger", SimpleNamespace(
        exception=lambda msg, *args, **kwargs: records["exception"].append((msg, args, kwargs)),
    ))
    monkeypatch.setattr(api_logging_module, "access_logger", SimpleNamespace(
        info=lambda msg, *args, **kwargs: records["access_info"].append((msg, args, kwargs)),
    ))
    monkeypatch.setattr(api_logging_module, "emit_api_call_alarm", lambda *args, **kwargs: records["alarm"].append((args, kwargs)))
    monkeypatch.setattr(api_logging_module, "set_log_context", lambda **kwargs: records["set"].append(kwargs))
    monkeypatch.setattr(api_logging_module, "clear_log_context", lambda: records.__setitem__("clear", records["clear"] + 1))
    monkeypatch.setattr(api_logging_module, "dump_masked_payload", lambda payload: payload)
    monkeypatch.setattr(api_logging_module, "_build_request_id", lambda: "req-err")

    async def app(_scope, _receive, _send):
        raise RuntimeError("boom")

    middleware = api_logging_module.APIAccessLogMiddleware(app)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/fail",
        "query_string": b"",
        "headers": [(b"x-user-id", b"user-2")],
        "client": ("127.0.0.2", 12345),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    with pytest.raises(RuntimeError, match="boom"):
        await middleware(scope, receive, send)

    assert records["set"] == [{"request_id": "req-err", "user": "user-2"}]
    assert records["clear"] == 1
    assert len(records["access_info"]) == 1
    assert records["access_info"][0][0] == "api_access_error"
    assert records["alarm"][0][0][0] == "/fail"
    assert records["alarm"][0][1]["method"] == "POST"
    assert records["exception"][0][0] == "[api_access] unhandled_error request_id=%s"


def test_route_common_and_schema_edge_branches(monkeypatch):
    with pytest.raises(ValueError, match="success/error"):
        response_module.business_response(status="bad")
    with pytest.raises(ValueError, match="未定义的错误码"):
        response_module.business_response(msg="bad code", code=49999, status="error")

    internal_error = (
        "获取失败: (pymysql.err.OperationalError) "
        "(2003, \"Can't connect to MySQL server on '62.234.152.217'\") "
        "(Background on this error at: https://sqlalche.me/e/20/e3q8)"
    )
    sanitized = _payload(response_module.business_response(msg=internal_error, code=ErrorCode.CHATDATA_GENERIC_OPERATION_FAILED, status="error"))
    assert sanitized["msg"] == "后端服务异常，请稍后重试"
    assert "62.234.152.217" not in sanitized["msg"]
    assert "pymysql" not in sanitized["msg"].lower()
    business_error = _payload(response_module.business_response(msg="数据源不存在: ds_a", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"))
    assert business_error["msg"] == "数据源不存在: ds_a"
    success = _payload(response_module.business_response(msg=internal_error, status="success"))
    assert success["msg"] == internal_error

    monkeypatch.setattr(dependencies, "get_forwarded_user", lambda _request: SimpleNamespace(user_id="", username="用户"))
    with pytest.raises(dependencies.HTTPException, match="Missing user_id"):
        dependencies._get_current_user(SimpleNamespace(headers={}))

    monkeypatch.setattr(dependencies, "get_forwarded_user", lambda _request: SimpleNamespace(user_id="u1", username="用户"))
    assert dependencies._get_current_user(SimpleNamespace(headers={})).user_id == "u1"

    assert build_table_columns_map({"ds_tables": [{"table_name": ""}, {"table_name": "orders", "columns": [{"name": "id"}, {"name": ""}]}]}) == {"orders": {"id"}}

    for field, value, message in [
        ("org_name", " ", "org_name"),
        ("org_code", " ", "org_code"),
        ("host", " ", "host"),
        ("database", " ", "database"),
    ]:
        payload = {"org_name": "机构", "org_code": "ORG", "query_executor_type": "direct", "host": "127.0.0.1", "database": "demo"}
        payload[field] = value
        with pytest.raises(ValueError, match=message):
            DatasourceSourceItem(**payload)
    assert DatasourceSourceItem(
        org_name="机构",
        org_code="ORG",
        query_executor_type="http",
        database="demo",
    ).host == ""
    with pytest.raises(ValueError, match="HTTP 模式只填写数据库名"):
        DatasourceSourceItem(
            org_name="机构",
            org_code="ORG",
            query_executor_type="http",
            host="127.0.0.1",
            database="demo",
        )


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
    monkeypatch.setattr(loader, "yaml", None)
    assert loader._parse_frontmatter("---\nname: no-yaml\nignored-line\n---\nbody") == {"name": "no-yaml"}
    monkeypatch.setattr(loader, "yaml", SimpleNamespace(safe_load=lambda _raw: ["not", "dict"]))
    assert loader._parse_frontmatter("---\nname: list-yaml\n---\nbody") == {"name": "list-yaml"}

    root = tmp_path / "skills"
    good = root / "good"
    hidden = root / "_hidden"
    bad = root / "bad"
    good.mkdir(parents=True)
    hidden.mkdir()
    bad.mkdir()
    (good / "SKILL.md").write_text("---\nname: good\ndescription: Good & safe\n---\nbody", encoding="utf-8")
    (hidden / "SKILL.md").write_text("---\nname: hidden\n---\nbody", encoding="utf-8")
    (bad / "SKILL.md").write_text("---\ntitle: Missing name\n---\nbody", encoding="utf-8")
    schema_skill = root / "schema"
    schema_skill.mkdir()
    schema_skill_file = schema_skill / "SKILL.md"
    schema_skill_file.write_text("---\nname: schema\ninput_schema:\n  type: object\n---\nbody", encoding="utf-8")
    monkeypatch.setattr(loader, "yaml", __import__("yaml"))
    assert loader.parse_metadata(schema_skill_file)["input_schema"] == {"type": "object"}

    class FakePath:
        @staticmethod
        def parent():
            return root

    monkeypatch.setattr(loader, "_metadata_cache", None)
    monkeypatch.setattr(loader, "system_skills_root", lambda: root)
    metadata = loader.load_metadata()
    assert "good" in metadata
    assert "hidden" not in metadata
    assert "bad" not in metadata
    prompt = loader.format_skills_prompt([{"name": "good", "title": "G", "description": "A&B", "domain": "d", "category": "c"}])
    assert "&amp;" in prompt
    minimal_prompt = loader.format_skills_prompt([{"name": "plain", "title": "Plain", "description": "desc"}])
    assert "<domain>" not in minimal_prompt
    assert "<category>" not in minimal_prompt


def test_calculator_extra_branches():
    assert calculator._format_decimal(calculator.Decimal("1.2300")) == "1.23"
    assert calculator._calculate_item({"expression": ""})["error"] == "expression 不能为空"
    assert calculator._calculate_item({"expression": "1/0"})["error"] == "除数不能为 0"
    assert calculator._calculate_item({"expression": "(1+2)", "decimal_places": 0})["result"] == "3"
