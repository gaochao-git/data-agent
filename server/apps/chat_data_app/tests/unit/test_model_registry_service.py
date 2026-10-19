"""模型注册表管理服务单测：参数校验、保存往返、脱敏与 DB 加载（sqlite 内存库）。

覆盖：
- params 白名单与逐参数校验（temperature/context_window/max_output_tokens/thinking）；
- 保存 -> 读回的 params_json 往返一致（含 thinking 各形态，回读能再过 fail-fast 校验）；
- 管理端脱敏视图（不回传密文、params 解包为对象）；
- llm_config 注册表加载：行 -> 消费配置转换、非法 params_json 拦截、空表自动导入。
"""

import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.daos import model_registry_dao  # noqa: E402
from apps.chat_data_app.services import llm_config, model_registry_service  # noqa: E402
from apps.chat_data_app.services.model_registry_service import (  # noqa: E402
    ModelRegistryError,
    ModelRegistryNotFoundError,
    _generate_model_key,
    _normalize_incoming_model_key,
    _slugify_model_id,
    _validate_params,
    _validate_payload_fields,
    create_model,
    delete_model,
    list_models,
    set_model_default,
    set_model_enabled,
    update_model,
)
from utils.models import Base, ChatDataModelRegistry  # noqa: E402


@pytest.fixture()
def registry_env(monkeypatch):
    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.delenv("CHAT_DATA_MODELS_FILE", raising=False)
    llm_config.reset_registry_cache()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[ChatDataModelRegistry.__table__])
            )

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(model_registry_dao, "default_get_session", fake_get_session)
    yield
    llm_config.reset_registry_cache()
    asyncio.run(engine.dispose())


def _payload(**overrides):
    base = {
        "model_id": "qwen-test",
        "display_name": "测试模型",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key": "dGVzdC1pdi12YWx1ZQ==:dGVzdC1jaXBoZXJ0ZXN0",  # SM4 密文形态
        "params": {"context_window": 32000, "temperature": 0.3},
        "enabled": True,
        "remark": "备注",
    }
    base.update(overrides)
    return base


# ----------- 参数校验 -----------


def test_validate_params_defaults_and_normalization():
    params = _validate_params({}, model_id="m1")
    assert params["temperature"] == 0.2
    assert params["context_window"] == 48000
    assert params["max_output_tokens"] == 12000  # 必填缺省 = 上下文窗口的 1/4
    assert params["vlm"] is True
    assert params["thinking"] == {"mode": "none"}


def test_validate_params_max_output_defaults_to_quarter_of_context_window():
    params = _validate_params({"context_window": 32000}, model_id="m1")
    assert params["max_output_tokens"] == 8000
    # 显式指定仍然尊重
    explicit = _validate_params({"context_window": 32000, "max_output_tokens": 4096}, model_id="m1")
    assert explicit["max_output_tokens"] == 4096


def test_validate_params_rejects_unknown_key():
    with pytest.raises(ModelRegistryError, match="未知参数键"):
        _validate_params({"bogus": 1}, model_id="m1")


@pytest.mark.parametrize(
    "params,match",
    [
        ({"temperature": 3}, "temperature"),
        ({"temperature": "0.5"}, "temperature"),
        ({"context_window": 0}, "context_window"),
        ({"context_window": "48000"}, "context_window"),
        ({"max_output_tokens": 0}, "max_output_tokens"),
        ({"max_output_tokens": 48000, "context_window": 48000}, "必须小于"),
        ({"thinking": {"mode": "nope"}}, "mode"),
    ],
)
def test_validate_params_rejects_invalid_values(params, match):
    with pytest.raises(ModelRegistryError, match=match):
        _validate_params(params, model_id="m1")


def test_validate_payload_fields_packs_params_json_and_masks(registry_env):
    fields = _validate_payload_fields(_payload())
    assert fields["display_name"] == "测试模型"
    assert fields["base_url"].startswith("http://")
    assert fields["api_key_cipher"] == "dGVzdC1pdi12YWx1ZQ==:dGVzdC1jaXBoZXJ0ZXN0"  # 密文原样落库
    assert fields["enabled_flag"] == 1
    assert fields["remark_text"] == "备注"
    packed = json.loads(fields["params_json"])
    assert packed["context_window"] == 32000
    assert packed["thinking"] == {"mode": "none"}

    # enabled 不传（None）：保持原值语义（DAO 跳过 setattr，新建走列默认 1）
    fields_keep = _validate_payload_fields({**_payload(), "enabled": None})
    assert fields_keep["enabled_flag"] is None
    assert _validate_payload_fields({**_payload(), "enabled": False})["enabled_flag"] == 0

    # api_key 必填：空值拒绝
    with pytest.raises(ModelRegistryError, match="必填"):
        _validate_payload_fields(_payload(api_key="", model_id="m2"))

    # 明文拒绝：api_key 只接受密文形态
    with pytest.raises(ModelRegistryError, match="密文"):
        _validate_payload_fields(_payload(api_key="plain-key", model_id="m3"))


def test_validate_payload_fields_rejects_bad_identity_and_base_url():
    with pytest.raises(ModelRegistryError, match="model_id"):
        _validate_payload_fields(_payload(model_id="  "))
    with pytest.raises(ModelRegistryError, match="空白"):
        _validate_payload_fields(_payload(model_id="a b"))
    with pytest.raises(ModelRegistryError, match="base_url"):
        _validate_payload_fields(_payload(base_url="ftp://x"))


# ----------- 保存/读取/操作往返（sqlite） -----------


def test_save_and_list_roundtrip(registry_env):
    async def run():
        saved = await create_model(_payload(), user_id="u1", username="张三")
        assert saved["model_id"] == "qwen-test"
        assert saved["has_api_key"] is True
        assert saved["api_key_cipher"] == "dGVzdC1pdi12YWx1ZQ==:dGVzdC1jaXBoZXJ0ZXN0"  # 密文回填
        assert saved["params"]["temperature"] == 0.3
        assert saved["params"]["thinking"]["mode"] == "none"
        assert "api_key_cipher" not in str(saved["params"])

        models = await list_models()
        assert len(models) == 1
        assert models[0]["params"]["context_window"] == 32000

        # 编辑（按注册键更新）回填密文后再保存：密文保持一致
        model_key = models[0]["model_key"]
        await update_model(model_key, _payload(display_name="改名"), user_id="u1", username="张三")
        models = await list_models()
        assert models[0]["display_name"] == "改名"
        assert models[0]["api_key_cipher"] == "dGVzdC1pdi12YWx1ZQ==:dGVzdC1jaXBoZXJ0ZXN0"

    asyncio.run(run())


def test_save_rejects_invalid_then_no_row_written(registry_env):
    async def run():
        with pytest.raises(ModelRegistryError, match="必须小于"):
            await create_model(
                _payload(params={"context_window": 1000, "max_output_tokens": 2000}),
                user_id="u1",
                username="张三",
            )
        assert await list_models() == []

    asyncio.run(run())


def test_enable_default_delete_lifecycle(registry_env):
    async def run():
        key_a = (await create_model(_payload(model_id="a"), user_id="u1", username="张三"))["model_key"]
        key_b = (await create_model(_payload(model_id="b"), user_id="u1", username="张三"))["model_key"]

        await set_model_default(key_b, user_id="u1", username="张三")
        models = {m["model_key"]: m for m in await list_models()}
        assert models[key_b]["is_default"] is True and models[key_a]["is_default"] is False

        await set_model_enabled(key_a, enabled=False, user_id="u1", username="张三")
        models = {m["model_key"]: m for m in await list_models()}
        assert models[key_a]["enabled_flag"] == 0 and models[key_a]["enabled"] is False

        await delete_model(key_a, user_id="u1", username="张三")
        assert [m["model_key"] for m in await list_models()] == [key_b]

        for operation in (
            lambda: set_model_enabled("ghost", enabled=True, user_id="u1", username="张三"),
            lambda: set_model_default("ghost", user_id="u1", username="张三"),
        ):
            with pytest.raises(ModelRegistryNotFoundError):
                await operation()

    asyncio.run(run())


def test_save_persists_thinking_all_shapes(registry_env):
    async def run():
        shapes = [
            {},
            {"mode": "forced", "dialect": "chat_template"},
            {
                "mode": "optional",
                "dialect": "reasoning_effort",
                "efforts": ["low", "high"],
                "default_on": True,
                "default_effort": "low",
            },
        ]
        for index, thinking in enumerate(shapes):
            model_id = f"m{index}"
            await create_model(
                _payload(model_id=model_id, params={"thinking": thinking}), user_id="u1", username="张三"
            )
        for model in await list_models():
            # 回读的 params 必须仍能通过保存校验（可回写不死锁）
            revalidated = _validate_params(model["params"], model_id=model["model_id"])
            assert revalidated["thinking"]["mode"] == model["params"]["thinking"]["mode"]

    asyncio.run(run())


# ----------- 连接测试 -----------


class _FakeLLM:
    def __init__(self, content: str = "ok"):
        self._content = content

    async def ainvoke(self, prompt: str):
        return type("Resp", (), {"content": self._content})()


def test_test_model_connection_success(registry_env, monkeypatch):
    captured: dict = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return _FakeLLM("ok")

    monkeypatch.setattr(model_registry_service, "get_llm", fake_get_llm)
    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")
    result = asyncio.run(model_registry_service.test_model_connection(_payload()))
    assert result["success"] is True
    assert result["sample"] == "ok"
    assert captured["model"] == "qwen-test"
    assert captured["base_url"] == "http://127.0.0.1:8000/v1"
    assert captured["api_key"]  # 已解密的明文


def test_test_model_connection_failure_becomes_structured_result(registry_env, monkeypatch):
    class _BrokenLLM:
        async def ainvoke(self, prompt: str):
            raise ConnectionError("connect refused")

    monkeypatch.setattr(model_registry_service, "get_llm", lambda **kwargs: _BrokenLLM())
    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")
    result = asyncio.run(model_registry_service.test_model_connection(_payload()))
    assert result["success"] is False
    assert "connect refused" in result["error"]


def test_test_model_connection_rejects_invalid_params(registry_env):
    with pytest.raises(ModelRegistryError, match="未知参数键"):
        asyncio.run(model_registry_service.test_model_connection(_payload(params={"bogus": 1})))


def test_test_model_connection_requires_key_from_form_or_row(registry_env, monkeypatch):
    """连接信息只以表单为准：key/base_url 缺失直接报错（无环境变量、无已存行回退）。"""
    monkeypatch.delenv("CHAT_DATA_API_KEY", raising=False)
    monkeypatch.delenv("CHAT_DATA_BASE_URL", raising=False)
    with pytest.raises(ModelRegistryError, match="必填"):
        asyncio.run(model_registry_service.test_model_connection(_payload(api_key="")))
    with pytest.raises(ModelRegistryError, match="Base URL 未配置"):
        asyncio.run(model_registry_service.test_model_connection(_payload(base_url="")))


# ----------- 模型自动探测 -----------


class _FakeDiscoverResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeDiscoverClient:
    """httpx.AsyncClient 替身：记录请求参数，返回类属性预设的响应。"""

    captured: dict = {}
    response: _FakeDiscoverResponse = _FakeDiscoverResponse()

    def __init__(self, **kwargs) -> None:
        _ = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        captured = type(self).captured
        captured.clear()
        captured.update({"url": url, "headers": headers})
        return type(self).response


def _patch_discover(monkeypatch, response: _FakeDiscoverResponse) -> dict:
    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")
    monkeypatch.setattr("httpx.AsyncClient", _FakeDiscoverClient)
    _FakeDiscoverClient.response = response
    _FakeDiscoverClient.captured = {}
    return _FakeDiscoverClient.captured


def test_discover_models_success_dedup_sorted(registry_env, monkeypatch):
    captured = _patch_discover(
        monkeypatch,
        _FakeDiscoverResponse(payload={"data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "a-model"}]}),
    )
    models = asyncio.run(
        model_registry_service.discover_models({"base_url": "http://127.0.0.1:8000/v1", "api_key": "AAAAAAAA:BBBBBBBB"})
    )
    assert models == ["a-model", "b-model"]
    assert captured["url"] == "http://127.0.0.1:8000/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer plain-key"


def test_discover_models_upstream_error_becomes_chinese_message(registry_env, monkeypatch):
    _patch_discover(monkeypatch, _FakeDiscoverResponse(status_code=401, text='{"error":"invalid key"}'))
    with pytest.raises(ModelRegistryError, match="HTTP 401"):
        asyncio.run(
            model_registry_service.discover_models(
                {"base_url": "http://127.0.0.1:8000/v1", "api_key": "AAAAAAAA:BBBBBBBB"}
            )
        )


def test_discover_models_rejects_non_openai_payload(registry_env, monkeypatch):
    _patch_discover(monkeypatch, _FakeDiscoverResponse(payload={"models": ["x"]}))
    with pytest.raises(ModelRegistryError, match="data 模型列表"):
        asyncio.run(
            model_registry_service.discover_models(
                {"base_url": "http://127.0.0.1:8000/v1", "api_key": "AAAAAAAA:BBBBBBBB"}
            )
        )
    with pytest.raises(ModelRegistryError, match="http"):
        asyncio.run(
            model_registry_service.discover_models(
                {"base_url": "ftp://127.0.0.1:8000/v1", "api_key": "AAAAAAAA:BBBBBBBB"}
            )
        )


# ----------- llm_config 注册表加载与转换 -----------


def test_registry_row_to_config_roundtrip_and_bad_json(registry_env):
    fields = _validate_payload_fields(_payload())
    row = {
        "model_id": "qwen-test",
        "display_name": "测试模型",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_cipher": fields["api_key_cipher"],
        "params_json": fields["params_json"],
        "is_default": 1,
        "enabled_flag": 1,
        "remark_text": "",
    }
    config = llm_config._registry_row_to_config(row)
    assert config["id"] == "qwen-test"
    assert config["request_model"] == "qwen-test"  # 存量行无 model_key：注册键回落请求名
    assert config["is_default"] is True
    assert config["context_window"] == 32000
    assert config["thinking"].mode == "none"

    # 注册键与请求名解耦：id=注册键（引用标识），request_model=上游请求名
    keyed = llm_config._registry_row_to_config(dict(row, model_key="qwen-test-2"))
    assert keyed["id"] == "qwen-test-2"
    assert keyed["request_model"] == "qwen-test"

    with pytest.raises(ValueError, match="params_json"):
        llm_config._registry_row_to_config(dict(row, params_json="not-json"))
    with pytest.raises(ValueError, match="JSON 对象"):
        llm_config._registry_row_to_config(dict(row, params_json="[1]"))


def test_same_request_name_multiple_providers_coexist(registry_env):
    """同一请求名接多个供应商：提供商不同即可并存；引用按注册键、上游按请求名。"""

    async def run():
        saved1 = await create_model(
            _payload(model_id="deepseek-v3", provider="DeepSeek官方", base_url="https://api.deepseek.com"),
            user_id="u1",
            username="张三",
        )
        saved2 = await create_model(
            _payload(model_id="deepseek-v3", provider="本地vLLM", base_url="http://127.0.0.1:8000/v1"),
            user_id="u1",
            username="张三",
        )
        # 注册键自动生成：{请求名slug}-{8位随机hex}，不因撞车追加 #n
        assert re.fullmatch(r"deepseek-v3-[0-9a-f]{8}", saved1["model_key"])
        assert re.fullmatch(r"deepseek-v3-[0-9a-f]{8}", saved2["model_key"])
        assert saved1["model_key"] != saved2["model_key"]

        # 同供应商+同请求名重复新建：业务唯一键拦截
        with pytest.raises(ModelRegistryError, match="该供应商下已存在同名请求名"):
            await create_model(_payload(model_id="deepseek-v3", provider="DeepSeek官方"), user_id="u1", username="张三")

        # 引用按注册键精确命中
        config = llm_config.get_chat_data_model_config(saved2["model_key"])
        assert config["id"] == saved2["model_key"]
        assert config["request_model"] == "deepseek-v3"

        # 历史引用（存的是请求名）：回落按请求名匹配，默认行优先
        legacy = llm_config.get_chat_data_model_config("deepseek-v3")
        assert legacy["request_model"] == "deepseek-v3"

    asyncio.run(run())


def test_create_after_delete_generates_fresh_row(registry_env):
    """软删后重加同供应商同请求名：墓碑名不占组合，生成全新行（注册键递增）。"""

    async def run():
        first = await create_model(_payload(model_id="qwen", provider="阿里云"), user_id="u1", username="张三")
        assert re.fullmatch(r"qwen-[0-9a-f]{8}", first["model_key"])
        await delete_model(first["model_key"], user_id="u1", username="张三")

        second = await create_model(_payload(model_id="qwen", provider="阿里云"), user_id="u1", username="张三")
        assert re.fullmatch(r"qwen-[0-9a-f]{8}", second["model_key"])
        assert second["model_key"] != first["model_key"]  # 每次重建都是新注册键
        assert second["id"] != first["id"]
        models = await list_models()
        assert [m["model_key"] for m in models] == [second["model_key"]]

    asyncio.run(run())


def test_model_key_generation_is_url_safe_and_readable():
    """注册键 = {slug}-{8hex}：URL 安全、可读、不依赖请求名原字符。"""
    assert _slugify_model_id("stealth/ox-alpha#1") == "stealth-ox-alpha-1"
    assert _slugify_model_id("DeepSeek-V4 Flash") == "deepseek-v4-flash"
    assert _slugify_model_id("a//b") == "a-b"  # 连续非法字符折叠成单个 -
    assert len(_slugify_model_id("x" * 300)) == 100
    # 纯中文请求名：回落为 model-{sha256 前 8 位}，确定性且不同名不撞
    chinese = _slugify_model_id("订单模型")
    assert re.fullmatch(r"model-[0-9a-f]{8}", chinese)
    assert chinese == _slugify_model_id("订单模型")
    assert chinese != _slugify_model_id("用户模型")
    key = _generate_model_key("deepseek-v4-flash")
    assert re.fullmatch(r"deepseek-v4-flash-[0-9a-f]{8}", key)
    assert _generate_model_key("deepseek-v4-flash") != key  # 随机后缀保证唯一


def test_normalize_incoming_model_key_decodes_gateway_encoding():
    """网关重复编码的注册键：%23 -> #，%2523 -> #，普通键原样返回。"""
    assert _normalize_incoming_model_key("deepseek-v4-flash") == "deepseek-v4-flash"
    assert _normalize_incoming_model_key("deepseek-v4-flash%23232") == "deepseek-v4-flash#232"
    assert _normalize_incoming_model_key("deepseek-v4-flash%2523232") == "deepseek-v4-flash#232"
    assert _normalize_incoming_model_key("  plain  ") == "plain"


def test_legacy_encoded_key_resolves_on_mutations(registry_env):
    """老注册键（含 #）被网关编码后，默认/启停/更新/删除仍需命中。"""

    async def run():
        await create_model(_payload(model_id="flash", provider="p1"), user_id="u1", username="张三")
        dao = model_registry_dao.ModelRegistryDao()
        fields = _validate_payload_fields(_payload(model_id="legacy", provider="p2"))
        await dao.upsert(model_key="legacy#7", fields=fields, operator_id="u1", operator_name="张三")

        await set_model_default("legacy%237", user_id="u1", username="张三")
        row = next(m for m in await list_models() if m["model_key"] == "legacy#7")
        assert row["is_default"] is True

        await set_model_enabled("legacy%25237", enabled=False, user_id="u1", username="张三")
        row = next(m for m in await list_models() if m["model_key"] == "legacy#7")
        assert row["enabled"] is False

        await update_model("legacy%237", _payload(model_id="legacy", provider="p2"), user_id="u1", username="张三")
        await delete_model("legacy%25237", user_id="u1", username="张三")
        assert all(m["model_key"] != "legacy#7" for m in await list_models())

    asyncio.run(run())


def test_update_model_by_key_and_combo_conflict(registry_env):
    """按注册键更新；改供应商/请求名时校验 (provider, model_id) 不撞其它活跃行。"""

    async def run():
        key_m1 = (await create_model(_payload(model_id="m1", provider="p1"), user_id="u1", username="张三"))[
            "model_key"
        ]
        await create_model(_payload(model_id="m2", provider="p2"), user_id="u1", username="张三")

        saved = await update_model(
            key_m1,
            _payload(model_id="m1", provider="p1", display_name="改名"),
            user_id="u1",
            username="张三",
        )
        assert saved["display_name"] == "改名"
        assert saved["model_key"] == key_m1  # 注册键不随编辑变化

        # 改成与 m2 相同的 (provider, model_id) 组合：拦截
        with pytest.raises(ModelRegistryError, match="该供应商下已存在同名请求名"):
            await update_model(key_m1, _payload(model_id="m2", provider="p2"), user_id="u1", username="张三")

        # 不存在的注册键：404 语义
        with pytest.raises(ModelRegistryNotFoundError):
            await update_model("ghost", _payload(), user_id="u1", username="张三")

    asyncio.run(run())


def test_load_registry_from_db_reads_enabled_rows(registry_env):
    async def run():
        key_on = (await create_model(_payload(model_id="on"), user_id="u1", username="张三"))["model_key"]
        await create_model(_payload(model_id="off", enabled=False), user_id="u1", username="张三")
        configs = await llm_config.load_registry_from_db()
        assert [config["id"] for config in configs] == [key_on]
        assert configs[0]["temperature"] == 0.3

    asyncio.run(run())


# ----------- 参数/注册键校验补充分支 -----------


def test_validate_params_none_and_non_dict():
    """params 缺省视为空对象；非对象（数组/标量）拒绝。"""
    assert _validate_params(None, model_id="m1")["temperature"] == 0.2
    with pytest.raises(ModelRegistryError, match="JSON 对象"):
        _validate_params([1], model_id="m1")


def test_validate_model_key_rules():
    """注册键：非空、无空白；update_model 的路径参数直传，不经过表单清洗。"""
    from apps.chat_data_app.services.model_registry_service import _validate_model_key

    with pytest.raises(ModelRegistryError, match="不能为空"):
        _validate_model_key("")
    with pytest.raises(ModelRegistryError, match="空白"):
        _validate_model_key("a b")
    assert _validate_model_key("ok-key") == "ok-key"


# ----------- 写后缓存刷新失败与并发冲突 -----------


def test_refresh_cache_best_effort_resets_cache_on_failure(registry_env, monkeypatch):
    """写后刷新失败：清空缓存进入冷态（服务路径明确报错），不回滚已落库写操作。"""

    async def run():
        monkeypatch.setattr(llm_config, "_registry_cache", [{"id": "stale"}])
        monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)

        async def broken_refresh():
            raise RuntimeError("db down")

        monkeypatch.setattr(model_registry_service, "refresh_registry_cache", broken_refresh)
        await model_registry_service._refresh_cache_best_effort()
        assert llm_config._registry_cache is None

    asyncio.run(run())


def test_create_model_concurrent_unique_conflict(registry_env, monkeypatch):
    """并发窗口：查重后另一请求先落库，upsert 撞唯一键转结构化中文报错（非 500）。"""
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    async def run():
        dao = model_registry_dao.ModelRegistryDao()

        async def conflict_upsert(*args, **kwargs):
            raise SAIntegrityError("dup", None, Exception("dup key"))

        monkeypatch.setattr(dao, "upsert", conflict_upsert)
        monkeypatch.setattr(model_registry_service, "ModelRegistryDao", lambda: dao)
        with pytest.raises(ModelRegistryError, match="冲突"):
            await create_model(_payload(), user_id="u1", username="张三")

    asyncio.run(run())


# ----------- 缓存行为（stale-while-revalidate / 冷缓存拒绝回落） -----------


def test_refresh_registry_cache_failure_keeps_existing_cache(registry_env, monkeypatch):
    """刷新失败保留 last-known-good：DB 抖动不放大为服务不可用。"""

    async def run():
        monkeypatch.setattr(llm_config, "_registry_cache", [{"id": "cached"}])
        monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)

        async def broken_load():
            raise RuntimeError("db down")

        monkeypatch.setattr(llm_config, "load_registry_from_db", broken_load)
        await llm_config.refresh_registry_cache()
        assert llm_config._registry_cache == [{"id": "cached"}]

    asyncio.run(run())


def test_get_configs_stale_cache_returns_old_value_and_schedules_refresh(registry_env, monkeypatch):
    """TTL 过期：先返回旧值不阻塞，事件循环内调度一次后台刷新（去重不叠加）。"""

    async def run():
        monkeypatch.setattr(llm_config, "_registry_cache", [{"id": "stale"}])
        monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)
        monkeypatch.setattr(
            llm_config,
            "_registry_cache_loaded_at",
            time.monotonic() - llm_config._registry_cache_ttl() - 1,
        )
        monkeypatch.setattr(llm_config, "_registry_refresh_task", None)

        configs = llm_config.get_chat_data_model_configs()
        assert configs == [{"id": "stale"}]
        task = llm_config._registry_refresh_task
        assert task is not None and not task.done()
        # 已有在途任务时不再重复调度
        llm_config.get_chat_data_model_configs()
        assert llm_config._registry_refresh_task is task
        task.cancel()

    asyncio.run(run())


def test_get_configs_cold_cache_in_event_loop_rejects_file_fallback(registry_env):
    """冷缓存 + 服务路径：拒绝回落文件并明确报错，同时调度一次重试刷新。"""

    async def run():
        with pytest.raises(RuntimeError, match="拒绝回落"):
            llm_config.get_chat_data_model_configs()
        task = llm_config._registry_refresh_task
        assert task is not None
        task.cancel()

    asyncio.run(run())


def test_get_configs_origin_mismatch_treated_as_cold(registry_env, monkeypatch):
    """来源锚定不匹配（env 指向其它文件）视同冷缓存，拒绝在服务路径使用。"""

    async def run():
        monkeypatch.setattr(llm_config, "_registry_cache", [{"id": "file-cached"}])
        monkeypatch.setattr(llm_config, "_registry_cache_origin", "/some/other/path.json")
        with pytest.raises(RuntimeError, match="拒绝回落"):
            llm_config.get_chat_data_model_configs()

    asyncio.run(run())


# ----------- 默认模型与精确匹配 fail-fast -----------


def test_default_model_id_rules():
    """默认模型：空表报错 > 显式 is_default > 首行兜底。"""
    with pytest.raises(ValueError, match="注册表为空"):
        llm_config._default_model_id([])
    configs = [{"id": "a", "is_default": False}, {"id": "b", "is_default": True}]
    assert llm_config._default_model_id(configs) == "b"
    assert llm_config._default_model_id([{"id": "x"}, {"id": "y"}]) == "x"


def test_get_chat_data_model_config_unknown_model_raises(registry_env, monkeypatch):
    """注册键与请求名都未命中：明确报错（不静默回退默认模型）。"""

    async def run():
        await create_model(_payload(model_id="m1"), user_id="u1", username="张三")
        configs = await llm_config.load_registry_from_db()
        monkeypatch.setattr(llm_config, "_registry_cache", configs)
        monkeypatch.setattr(llm_config, "_registry_cache_loaded_at", time.monotonic())
        monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)
        with pytest.raises(ValueError, match="不在模型注册表中"):
            llm_config.get_chat_data_model_config("ghost")

    asyncio.run(run())


def test_get_chat_data_llm_missing_connection_info_fail_fast(registry_env, monkeypatch):
    """行内缺 base_url/api_key：中文可行动报错，不透传 OpenAI 的 Missing credentials。"""
    from apps.chat_data_app.services.llm_config import ThinkingCapability

    config = {
        "id": "m1",
        "request_model": "m1",
        "temperature": 0.2,
        "context_window": 128000,
        "max_output_tokens": 8000,
        "thinking": ThinkingCapability(mode="none"),
        "base_url": "",
        "api_key_cipher": "",
    }
    monkeypatch.setattr(llm_config, "_registry_cache", [config])
    monkeypatch.setattr(llm_config, "_registry_cache_loaded_at", time.monotonic())
    monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)
    with pytest.raises(ValueError, match="未配置"):
        llm_config.get_chat_data_llm(max_tokens=100)

    # 缺 key 单独报错
    config_with_url = dict(config, base_url="http://127.0.0.1:9/v1")
    monkeypatch.setattr(llm_config, "_registry_cache", [config_with_url])
    with pytest.raises(ValueError, match="未配置"):
        llm_config.get_chat_data_llm(max_tokens=100)


# ----------- 注册表首导分支（种子文件不可用 / 并发撞键） -----------


def test_load_registry_empty_table_returns_empty_without_file_fallback(registry_env, monkeypatch, tmp_path):
    """空表即空清单：不再回源 CHAT_DATA_MODELS_FILE 自动导入（DB 唯一真值）。"""
    models_file = tmp_path / "models.json"
    models_file.write_text(
        json.dumps(
            [
                {
                    "id": "should-not-import",
                    "temperature": 0.1,
                    "context_window": 64000,
                    "vlm": False,
                    "thinking": {"mode": "none"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHAT_DATA_MODELS_FILE", str(models_file))

    async def run():
        assert await llm_config.load_registry_from_db() == []
        # 文件里 despite 存在条目，DB 也不落任何行
        assert await model_registry_dao.ModelRegistryDao().list_rows() == []

    asyncio.run(run())


# ----------- 探测异常分支补充 -----------


def test_discover_models_network_error_becomes_chinese_message(registry_env, monkeypatch):
    class _BrokenClient:
        def __init__(self, **kwargs):
            _ = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            raise ConnectionError("connection refused")

    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")
    monkeypatch.setattr("httpx.AsyncClient", _BrokenClient)
    with pytest.raises(ModelRegistryError, match="探测请求失败"):
        asyncio.run(
            model_registry_service.discover_models(
                {"base_url": "http://127.0.0.1:8000/v1", "api_key": "AAAAAAAA:BBBBBBBB"}
            )
        )


def test_discover_models_rejects_non_json_body(registry_env, monkeypatch):
    _patch_discover(monkeypatch, _FakeDiscoverResponse(payload=None, text="<html>gateway</html>"))
    with pytest.raises(ModelRegistryError, match="合法 JSON"):
        asyncio.run(
            model_registry_service.discover_models(
                {"base_url": "http://127.0.0.1:8000/v1", "api_key": "AAAAAAAA:BBBBBBBB"}
            )
        )


# ----------- 残余分支收口（覆盖率门禁） -----------


def test_read_positive_int_env_falls_back_on_non_int(monkeypatch):
    """数值环境变量非数字时回退默认值。"""
    monkeypatch.setenv("CHAT_DATA_OUTPUT_MAX_TOKENS", "abc")
    assert llm_config.get_chat_data_output_max_tokens() == llm_config.DEFAULT_CHAT_DATA_OUTPUT_MAX_TOKENS


def test_thinking_cap_to_json_efforts_without_default_effort():
    """有档位无默认档：只序列化 efforts（default_effort 缺省不落键；default_on 由读侧推导）。"""
    cap = llm_config.ThinkingCapability(
        mode="optional", dialect="openrouter", efforts=(llm_config.EffortLevel(level="low"),)
    )
    assert llm_config._thinking_cap_to_json(cap) == {
        "mode": "optional",
        "dialect": "openrouter",
        "efforts": [{"level": "low"}],
    }


def test_get_configs_stale_cache_without_event_loop_returns_old_value(registry_env, monkeypatch):
    """TTL 过期且无事件循环（脚本场景）：直接返回旧值，不调度刷新。"""
    monkeypatch.setattr(llm_config, "_registry_cache", [{"id": "stale-script"}])
    monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)
    monkeypatch.setattr(
        llm_config,
        "_registry_cache_loaded_at",
        time.monotonic() - llm_config._registry_cache_ttl() - 1,
    )
    monkeypatch.setattr(llm_config, "_registry_refresh_task", None)
    assert llm_config.get_chat_data_model_configs() == [{"id": "stale-script"}]
    assert llm_config._registry_refresh_task is None


def test_get_model_options_empty_registry_returns_empty_list(registry_env, monkeypatch):
    """空注册表（新装未配模型）：/config 返回空列表由前端引导，不抛错 500。"""
    monkeypatch.setattr(llm_config, "_registry_cache", [])
    monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)
    monkeypatch.setattr(llm_config, "_registry_cache_loaded_at", time.monotonic())
    options = llm_config.get_chat_data_model_options()
    assert options == {"models": [], "default_model": ""}


def test_get_chat_data_model_config_matches_request_model_after_rename(registry_env, monkeypatch):
    """请求名回落匹配：注册键仍在但请求名已改，历史引用按旧请求名命中改后的行。"""

    async def run():
        key_req = (await create_model(_payload(model_id="req-x", provider="p"), user_id="u1", username="张三"))[
            "model_key"
        ]
        # 请求名改成 req-y（注册键不变）
        await update_model(key_req, _payload(model_id="req-y", provider="p"), user_id="u1", username="张三")
        configs = await llm_config.load_registry_from_db()
        monkeypatch.setattr(llm_config, "_registry_cache", configs)
        monkeypatch.setattr(llm_config, "_registry_cache_loaded_at", time.monotonic())
        monkeypatch.setattr(llm_config, "_registry_cache_origin", llm_config._REGISTRY_ORIGIN_DB)
        # 历史会话存的旧请求名 req-y 命中
        config = llm_config.get_chat_data_model_config("req-y")
        assert config["id"] == key_req
        assert config["request_model"] == "req-y"

    asyncio.run(run())


def test_mask_row_bad_params_json_degrades_to_empty(registry_env):
    """params_json 落库内容损坏：管理视图降级为空对象不抛错。"""
    masked = model_registry_service._mask_row(
        {"params_json": "not-json", "enabled_flag": 1, "is_default": 0, "api_key_cipher": "x:y"}
    )
    assert masked["params"] == {}
    assert masked["enabled"] is True
    assert masked["is_default"] is False


def test_validate_params_rejects_bool_as_number():
    """布尔是 int 子类：显式排除，temperature/context_window/max_output 均不接受。"""
    with pytest.raises(ModelRegistryError, match="max_output_tokens"):
        _validate_params({"max_output_tokens": True, "context_window": 32000}, model_id="m1")
    with pytest.raises(ModelRegistryError, match="context_window"):
        _validate_params({"context_window": True}, model_id="m1")


def test_validate_params_empty_thinking_dict_means_none():
    """thinking 空对象等同缺省：归一化为 mode=none。"""
    params = _validate_params({"thinking": {}}, model_id="m1")
    assert params["thinking"] == {"mode": "none"}


def test_discover_models_rejects_non_dict_json_body(registry_env, monkeypatch):
    """上游返回 JSON 数组/标量（非对象）：按非 OpenAI 兼容接口拒绝。"""

    class _ListResponse(_FakeDiscoverResponse):
        def json(self):
            return ["m1", "m2"]

    _patch_discover(monkeypatch, _ListResponse(payload=[]))
    with pytest.raises(ModelRegistryError, match="data 模型列表"):
        asyncio.run(
            model_registry_service.discover_models(
                {"base_url": "http://127.0.0.1:8000/v1", "api_key": "AAAAAAAA:BBBBBBBB"}
            )
        )


# ----------- 思考档位可配置化：save 落库/回读一致 -----------


def test_save_model_persists_configurable_efforts_and_roundtrips(registry_env):
    """新形状档位（含 value）保存后落库与回读一致；旧 string[] 也可保存；废弃 off_value 静默丢弃。"""

    async def run():
        await create_model(
            _payload(
                model_id="deepseek-v3",
                params={
                    "temperature": 0.2,
                    "context_window": 64000,
                    "max_output_tokens": 8000,
                    "thinking": {
                        "mode": "optional",
                        "dialect": "reasoning_effort",
                        "default_on": False,
                        "efforts": [{"level": "low", "value": "minimal"}, {"level": "high"}],
                        "default_effort": "low",
                    },
                },
            ),
            user_id="u1",
            username="张三",
        )
        configs = await llm_config.load_registry_from_db()
        cap = configs[0]["thinking"]
        assert cap.efforts == (
            llm_config.EffortLevel(level="low", value="minimal"),
            llm_config.EffortLevel(level="high"),
        )

        # 旧前端发 string[] 也能保存（落为新形状）
        saved_key = (await list_models())[0]["model_key"]
        await update_model(
            saved_key,
            _payload(
                model_id="deepseek-v3",
                params={
                    "temperature": 0.2,
                    "context_window": 64000,
                    "max_output_tokens": 8000,
                    "thinking": {
                        "mode": "optional",
                        "dialect": "reasoning_effort",
                        "default_on": False,
                        "efforts": ["low", "high"],
                        "default_effort": "low",
                        "off_value": "low",  # 废弃字段：容忍但不再落库
                    },
                },
            ),
            user_id="u1",
            username="张三",
        )
        rows = await model_registry_dao.ModelRegistryDao().list_rows()
        packed = json.loads(rows[0]["params_json"])
        assert packed["thinking"]["efforts"] == [{"level": "low"}, {"level": "high"}]
        assert "off_value" not in packed["thinking"]

    asyncio.run(run())


def test_save_model_rejects_illegal_effort_payload(registry_env):
    """保存期校验：非法档位名/非法 value 直接拒绝，错误消息带 model_id。"""

    async def run():
        bad_name = _payload(
            model_id="m-bad",
            params={
                "context_window": 64000,
                "thinking": {
                    "mode": "optional",
                    "dialect": "reasoning_effort",
                    "default_on": False,
                    "efforts": [{"level": "bad name!"}],
                    "default_effort": "bad name!",
                },
            },
        )
        with pytest.raises(ModelRegistryError, match="m-bad"):
            await create_model(bad_name, user_id="u1", username="张三")

        bad_value = _payload(
            model_id="m-bad2",
            params={
                "context_window": 64000,
                "thinking": {
                    "mode": "optional",
                    "dialect": "reasoning_effort",
                    "default_on": False,
                    "efforts": [{"level": "low", "value": [1]}],
                    "default_effort": "low",
                },
            },
        )
        with pytest.raises(ModelRegistryError, match="参数值"):
            await create_model(bad_value, user_id="u1", username="张三")

    asyncio.run(run())


def test_test_model_connection_probes_highest_effort(registry_env, monkeypatch):
    """连接测试：声明档位时用最高档（末位）发 on 形态探测 wire 值；无档位保持 off 形态。"""
    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            captured["prompt"] = prompt
            return type("R", (), {"content": "ok"})()

    def fake_get_llm(**kwargs):
        captured["extra_body"] = kwargs["extra_body"]
        return _FakeLLM()

    monkeypatch.setattr(model_registry_service, "get_llm", fake_get_llm)
    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")

    with_efforts = _payload(
        model_id="m-probe",
        params={
            "context_window": 64000,
            "thinking": {
                "mode": "optional",
                "dialect": "reasoning_effort",
                "default_on": False,
                "efforts": [{"level": "low", "value": "minimal"}, {"level": "high"}],
                "default_effort": "low",
            },
        },
    )
    result = asyncio.run(model_registry_service.test_model_connection(with_efforts))
    assert result["success"] is True
    assert captured["extra_body"] == {"reasoning_effort": "high"}  # 末位档=high，发档位名

    # optional+方言但未声明档位：off 形态按方言兜底
    plain = _payload(
        model_id="m-plain",
        params={
            "context_window": 64000,
            "thinking": {"mode": "optional", "dialect": "reasoning_effort", "default_on": False},
        },
    )
    asyncio.run(model_registry_service.test_model_connection(plain))
    assert captured["extra_body"] == {"reasoning_effort": "none"}


def test_test_model_connection_skips_off_sentinel_efforts(registry_env, monkeypatch):
    """连接测试：关哨兵档位（off/none）不参与 on 形态探测；全是哨兵档时退回 off 形态。"""
    captured = {}

    class _FakeLLM:
        async def ainvoke(self, prompt):
            return type("R", (), {"content": "ok"})()

    def fake_get_llm(**kwargs):
        captured["extra_body"] = kwargs["extra_body"]
        return _FakeLLM()

    monkeypatch.setattr(model_registry_service, "get_llm", fake_get_llm)
    monkeypatch.setattr(model_registry_service, "decrypt_password", lambda value: "plain-key")

    def _thinking_payload(model_id: str, efforts: list[dict]):
        return _payload(
            model_id=model_id,
            params={
                "context_window": 64000,
                "thinking": {
                    "mode": "optional",
                    "dialect": "reasoning_effort",
                    "default_on": False,
                    "efforts": efforts,
                    "default_effort": efforts[0]["level"],
                },
            },
        )

    # 末位档是哨兵档：探测跳到非哨兵的最高档 low，而不是把 off 当 on wire 值发
    trailing_sentinel = _thinking_payload("m-sentinel-tail", [{"level": "low"}, {"level": "off"}])
    result = asyncio.run(model_registry_service.test_model_connection(trailing_sentinel))
    assert result["success"] is True
    assert captured["extra_body"] == {"reasoning_effort": "low"}

    # 全是哨兵档：无 on 档可探测，退回 off 形态（只验证连通与鉴权）
    all_sentinel = _thinking_payload("m-sentinel-only", [{"level": "off"}, {"level": "none"}])
    asyncio.run(model_registry_service.test_model_connection(all_sentinel))
    assert captured["extra_body"] == {"reasoning_effort": "none"}
