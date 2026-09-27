"""后台配置变量服务单测（表覆盖 > env > 代码默认 + 写时校验 + 重置/恢复）。

所有 service 返回 dict 一律追加 json.dumps 断言：business_response 底层是裸
json.dumps（无 datetime 编码器），裸 datetime 进响应即线上 500，必须在单测层拦住
（update_time 在 service 内预格式化为字符串）。

白名单口径：upsert 只接受注册 key。harness 往 SETTING_REGISTRY 注入一个测试
spec（TEST_NOTICE_TEXT）作为第二个可覆盖 key；未注册 key 只允许直连 seed 行
做防御性读取测试（真实链路不会再产生这种行）。
"""

import asyncio
import json
import types
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.services import setting_service
from utils.models import Base, ChatDataSetting

REGISTERED_KEY = "CHAT_DATA_MAX_UPLOAD_SIZE_MB"
TEST_KEY = "TEST_NOTICE_TEXT"
UNREGISTERED_KEY = "UNREGISTERED_FLAG"

TEST_REGISTRY = {
    REGISTERED_KEY: setting_service.SETTING_REGISTRY[REGISTERED_KEY],
    TEST_KEY: setting_service.SettingSpec(
        key=TEST_KEY,
        value_type="string",
        default="",
        category="upload_preview",
        effect=setting_service.EFFECT_IMMEDIATE,
        description="测试用字符串变量",
    ),
}


@pytest.fixture()
def harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def test_transaction_scope():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def seed_setting(**overrides):
        fields = dict(
            setting_key=TEST_KEY,
            setting_value="on",
            delete_flag=0,
            create_by="creator-1",
            create_by_name="初始创建人",
            update_by="creator-1",
            update_by_name="初始创建人",
        )
        fields.update(overrides)
        async with test_transaction_scope() as session:
            session.add(ChatDataSetting(**fields))

    async def fetch_rows(setting_key):
        async with session_factory() as session:
            result = await session.execute(
                select(ChatDataSetting).where(ChatDataSetting.setting_key == setting_key)
            )
            return result.scalars().all()

    monkeypatch.setattr(setting_service, "transaction_scope", test_transaction_scope)
    monkeypatch.setattr(setting_service, "SETTING_REGISTRY", TEST_REGISTRY)
    setting_service.clear_setting_cache()
    yield types.SimpleNamespace(
        session_factory=session_factory,
        seed_setting=seed_setting,
        fetch_rows=fetch_rows,
        scope=test_transaction_scope,
    )
    setting_service.clear_setting_cache()
    asyncio.run(engine.dispose())


@pytest.mark.asyncio
async def test_table_override_wins_and_ttl_cache_serves_within_ttl(harness, monkeypatch):
    monkeypatch.delenv(REGISTERED_KEY, raising=False)
    await harness.seed_setting(setting_key=REGISTERED_KEY, setting_value="20")

    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 20

    # TTL 内第二次读走缓存（缓存的是表原始字符串），直接改库不影响本次 TTL 窗口
    async with harness.scope() as session:
        row = (await session.execute(
            select(ChatDataSetting).where(ChatDataSetting.setting_key == REGISTERED_KEY)
        )).scalar_one()
        row.setting_value = "30"
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 20

    # 写路径清缓存后立即读到新值
    setting_service.clear_setting_cache()
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 30


@pytest.mark.asyncio
async def test_env_fallback_then_default_and_invalid_env_falls_back(harness, monkeypatch):
    monkeypatch.delenv(REGISTERED_KEY, raising=False)
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 50

    monkeypatch.setenv(REGISTERED_KEY, "10")
    setting_service.clear_setting_cache()
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 10

    # env 值非法：告警回退代码默认，不抛异常（运行期不因脏配置 fail-fast）
    monkeypatch.setenv(REGISTERED_KEY, "abc")
    setting_service.clear_setting_cache()
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 50


@pytest.mark.asyncio
async def test_invalid_table_value_falls_back_to_env_then_default(harness, monkeypatch):
    await harness.seed_setting(setting_key=REGISTERED_KEY, setting_value="0")
    monkeypatch.setenv(REGISTERED_KEY, "8")
    setting_service.clear_setting_cache()
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 8

    monkeypatch.setenv(REGISTERED_KEY, "99999")  # 超过注册上限 2048
    setting_service.clear_setting_cache()
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 50


@pytest.mark.asyncio
async def test_unregistered_key_defensive_read_raw_env_and_default(harness, monkeypatch):
    """未注册 key 的防御性读取：真实链路不会再产生这种行（upsert 已拒绝），
    但 DBA 直插的行 / env 仍按「表 > env > 默认」兜底，来源徽标口径不破。"""
    monkeypatch.delenv(UNREGISTERED_KEY, raising=False)
    assert await setting_service.get_runtime_setting(UNREGISTERED_KEY, default="off") == "off"

    monkeypatch.setenv(UNREGISTERED_KEY, "env-on")
    setting_service.clear_setting_cache()
    assert await setting_service.get_runtime_setting(UNREGISTERED_KEY, default="off") == "env-on"

    await harness.seed_setting(setting_key=UNREGISTERED_KEY, setting_value="table-on")
    setting_service.clear_setting_cache()
    assert await setting_service.get_runtime_setting(UNREGISTERED_KEY, default="off") == "table-on"


@pytest.mark.asyncio
async def test_db_failure_falls_back_without_raising(harness, monkeypatch):
    @asynccontextmanager
    async def broken_scope():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(setting_service, "transaction_scope", broken_scope)
    monkeypatch.delenv(REGISTERED_KEY, raising=False)
    # DB 不可用不阻塞业务读取：告警并回退代码默认
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 50


def test_parse_registered_value_type_branches():
    bool_spec = setting_service.SettingSpec(key="X_FLAG", value_type="bool", default=False, category="query")
    float_spec = setting_service.SettingSpec(key="X_RATIO", value_type="float", default=0.5, category="query", minimum=0, maximum=1)
    string_spec = setting_service.SettingSpec(key="X_TEXT", value_type="string", default="", category="upload_preview")

    assert setting_service._parse_registered_value(bool_spec, "TRUE") is True
    assert setting_service._parse_registered_value(bool_spec, "false") is False
    with pytest.raises(ValueError, match="true/false"):
        setting_service._parse_registered_value(bool_spec, "1")
    assert setting_service._parse_registered_value(float_spec, "0.75") == 0.75
    with pytest.raises(ValueError, match="不能大于"):
        setting_service._parse_registered_value(float_spec, "1.5")
    with pytest.raises(ValueError):
        setting_service._parse_registered_value(float_spec, "nan-ish")
    assert setting_service._parse_registered_value(string_spec, " 保留原样 ") == "保留原样"


@pytest.mark.asyncio
async def test_upsert_create_update_and_restore_keeps_create_audit(harness):
    created = await setting_service.upsert_setting(
        TEST_KEY, "维护中", user_id="u1", username="管理员",
    )
    assert created == {"setting_key": TEST_KEY, "action": "created"}

    updated = await setting_service.upsert_setting(
        TEST_KEY, "维护完成", user_id="u2", username="运维二号",
    )
    assert updated["action"] == "updated"
    rows = await harness.fetch_rows(TEST_KEY)
    assert len(rows) == 1
    assert rows[0].setting_value == "维护完成"
    assert rows[0].create_by_name == "管理员"  # create 四件套为首次写入人
    assert rows[0].update_by_name == "运维二号"

    await setting_service.delete_setting(TEST_KEY, user_id="u2", username="运维二号")
    restored = await setting_service.upsert_setting(
        TEST_KEY, "重新上线", user_id="u3", username="运维三号",
    )
    assert restored["action"] == "restored"
    rows = await harness.fetch_rows(TEST_KEY)
    assert len(rows) == 1  # 复用原行，不 insert，规避唯一键冲突
    assert rows[0].delete_flag == 0
    assert rows[0].setting_value == "重新上线"
    assert rows[0].create_by_name == "管理员"  # create 四件套保留首次记录
    assert rows[0].update_by_name == "运维三号"


@pytest.mark.asyncio
async def test_upsert_rejects_unregistered_keys_and_bad_values(harness):
    # 白名单收口：未注册 key（含密钥类/小写/任意未知 key）一律拒绝
    for bad_key in ("APP_NOTICE_TEXT", "CHAT_DATA_API_KEY", "MYSQL_PASSWORD", "lower_case"):
        with pytest.raises(setting_service.SettingValidationError, match="未注册"):
            await setting_service.upsert_setting(bad_key, "v", "u1", "管理员")
    # 注册 key 的值校验仍在
    with pytest.raises(setting_service.SettingValidationError, match="不能为空"):
        await setting_service.upsert_setting(REGISTERED_KEY, "  ", "u1", "管理员")
    with pytest.raises(setting_service.SettingValidationError, match="长度不能超过"):
        await setting_service.upsert_setting(REGISTERED_KEY, "x" * 2049, "u1", "管理员")
    for bad in ("abc", "0", "-5", "99999"):
        with pytest.raises(setting_service.SettingValidationError, match="变量值非法"):
            await setting_service.upsert_setting(REGISTERED_KEY, bad, "u1", "管理员")


@pytest.mark.asyncio
async def test_delete_setting_not_found_cases(harness):
    with pytest.raises(setting_service.SettingNotFoundError):
        await setting_service.delete_setting(TEST_KEY, user_id="u1", username="管理员")
    await harness.seed_setting()
    await setting_service.delete_setting(TEST_KEY, user_id="u1", username="管理员")
    with pytest.raises(setting_service.SettingNotFoundError):
        await setting_service.delete_setting(TEST_KEY, user_id="u1", username="管理员")


@pytest.mark.asyncio
async def test_reset_all_soft_deletes_every_override_row(harness):
    await harness.seed_setting(setting_key=REGISTERED_KEY, setting_value="20")
    await harness.seed_setting(setting_value="公告")
    result = await setting_service.reset_all_settings(user_id="u1", username="管理员")
    assert result == {"reset_count": 2}

    for key in (REGISTERED_KEY, TEST_KEY):
        rows = await harness.fetch_rows(key)
        assert len(rows) == 1 and rows[0].delete_flag == 1
    assert await setting_service.get_runtime_setting(REGISTERED_KEY) == 50
    assert await setting_service.get_runtime_setting(TEST_KEY, default="x") == ""  # 注册 key 回退 spec.default


@pytest.mark.asyncio
async def test_list_settings_sources_invalid_flag_and_keyword_filter(harness, monkeypatch):
    monkeypatch.delenv(REGISTERED_KEY, raising=False)
    data = await setting_service.list_settings()
    by_key = {item["setting_key"]: item for item in data["items"]}
    assert data["total"] == 2  # 列表 = 注册表全量（白名单）
    assert set(by_key) == {REGISTERED_KEY, TEST_KEY}
    assert by_key[REGISTERED_KEY]["source"] == "default"
    assert by_key[REGISTERED_KEY]["effective_value"] == 50
    assert by_key[REGISTERED_KEY]["effect_label"] == "即时生效（≤30秒）"
    assert by_key[REGISTERED_KEY]["category"] == "upload_preview"
    assert by_key[REGISTERED_KEY]["category_label"] == "上传与预览"
    assert by_key[TEST_KEY]["setting_desc"] == "测试用字符串变量"  # 说明单一来源=注册表
    json.dumps(data)  # 无 datetime 裸对象

    # env 来源
    monkeypatch.setenv(REGISTERED_KEY, "8")
    setting_service.clear_setting_cache()
    data = await setting_service.list_settings()
    by_key = {item["setting_key"]: item for item in data["items"]}
    assert by_key[REGISTERED_KEY]["source"] == "env"
    assert by_key[REGISTERED_KEY]["effective_value"] == 8

    # 表覆盖来源
    await harness.seed_setting(setting_key=REGISTERED_KEY, setting_value="20")
    setting_service.clear_setting_cache()
    data = await setting_service.list_settings()
    by_key = {item["setting_key"]: item for item in data["items"]}
    assert by_key[REGISTERED_KEY]["source"] == "table"
    assert by_key[REGISTERED_KEY]["setting_value"] == "20"

    assert (await setting_service.list_settings(keyword="TEST"))["total"] == 1
    assert (await setting_service.list_settings(keyword="测试用"))["total"] == 1
    assert (await setting_service.list_settings(keyword="不存在的关键字"))["total"] == 0


@pytest.mark.asyncio
async def test_list_settings_marks_invalid_table_value(harness):
    """string 类型无法构造解析失败，用 int 注册 key 造非法表值验证 invalid 标记。"""
    await harness.seed_setting(setting_key=REGISTERED_KEY, setting_value="not-a-number")
    setting_service.clear_setting_cache()
    data = await setting_service.list_settings()
    by_key = {item["setting_key"]: item for item in data["items"]}
    assert by_key[REGISTERED_KEY]["source"] == "table"
    assert by_key[REGISTERED_KEY]["value_invalid"] is True
    assert by_key[REGISTERED_KEY]["effective_value"] == 50
