import base64
import os
from pathlib import Path

import pytest

from utils import crypto, storage_paths, tool_result_store


def test_normalize_user_id(monkeypatch):
    # 仅保留在生产代码中被 conversation_workspace 使用的用户 id 落盘安全化函数
    assert storage_paths.normalize_user_id(" user/../a ") == "user%2F..%2Fa"
    assert storage_paths.normalize_user_id("user_a") != storage_paths.normalize_user_id("user/a")
    assert storage_paths.normalize_conversation_id("conv/a") == "conv%2Fa"

    with pytest.raises(ValueError, match="Invalid path segment type"):
        storage_paths.normalize_user_id(None)

    with pytest.raises(ValueError, match="must not be empty"):
        storage_paths.normalize_user_id("  ")


def test_storage_paths_chat_data_home_required_and_resolves_absolute(monkeypatch, tmp_path):
    # CHAT_DATA_HOME 必须显式指定，禁止回退到其它变量推导
    monkeypatch.delenv("CHAT_DATA_HOME", raising=False)
    with pytest.raises(RuntimeError, match="CHAT_DATA_HOME is required"):
        storage_paths._resolve_chat_data_home()

    monkeypatch.setenv("CHAT_DATA_HOME", str(tmp_path / "custom-chat-data"))
    resolved = storage_paths._resolve_chat_data_home()
    assert resolved.is_absolute()
    assert resolved.name == "custom-chat-data"

    monkeypatch.setenv("CHAT_DATA_HOME", "relative-chat-data")
    with pytest.raises(RuntimeError, match="绝对路径"):
        storage_paths._resolve_chat_data_home()


def test_tool_result_store_writes_and_loads_current_conversation_only(tmp_path):
    store = tool_result_store.ToolResultStore(tmp_path)
    record = store.save_query_result({"rows": [1]}, conversation_id="conv_a", user_id="u1")

    assert record.virtual_path == f"/mnt/user-data/outputs/query_result/{record.result_id}.json"
    assert (
        tmp_path / "threads" / "u1" / "conv_a" / "user-data" / "outputs" / "query_result" / f"{record.result_id}.json"
    ).is_file()
    assert store.load_query_result_for_conversation(
        result_id=record.result_id,
        conversation_id="conv_a",
        user_id="u1",
    ) == {"rows": [1]}
    assert store.load_query_result_for_conversation(
        result_id=record.result_id,
        conversation_id="conv_a",
        user_id="u2",
    ) is None
    with pytest.raises(ValueError, match="result_id"):
        store.load_query_result_for_conversation(
            result_id="../outside",
            conversation_id="conv_a",
            user_id="u1",
        )


def test_tool_result_store_handles_corrupt_json(tmp_path):
    store = tool_result_store.ToolResultStore(tmp_path)
    bad_path = tmp_path / "threads" / "u1" / "conv" / "user-data" / "outputs" / "query_result" / "bad.json"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("{bad", encoding="utf-8")
    assert store.load_query_result_for_conversation(
        result_id="bad",
        conversation_id="conv",
        user_id="u1",
    ) is None


def test_crypto_key_validation_encrypt_decrypt_and_errors(monkeypatch):
    monkeypatch.delenv("ENCRYPT_KEY", raising=False)
    assert crypto._get_sm4_key() is None
    with pytest.raises(ValueError, match="密码不能为空"):
        crypto.encrypt_password("")
    with pytest.raises(ValueError, match="ENCRYPT_KEY"):
        crypto.encrypt_password("secret")

    monkeypatch.setenv("ENCRYPT_KEY", "bad")
    assert crypto._get_sm4_key() is None
    monkeypatch.setenv("ENCRYPT_KEY", "00")
    assert crypto._get_sm4_key() is None

    monkeypatch.setenv("ENCRYPT_KEY", "00112233445566778899aabbccddeeff")
    encrypted = crypto.encrypt_password("secret")
    assert crypto.decrypt_password(encrypted) == "secret"

    with pytest.raises(ValueError, match="密码未配置"):
        crypto.decrypt_password("")
    with pytest.raises(ValueError, match="密码格式错误"):
        crypto.decrypt_password("bad-format")
    with pytest.raises(ValueError, match="base64 解码失败"):
        crypto.decrypt_password("bad:base64")
    short_iv = f"{base64.b64encode(b'short').decode()}:abcd"
    with pytest.raises(ValueError, match="IV 长度错误"):
        crypto.decrypt_password(short_iv)
    with pytest.raises(ValueError, match="密码解密失败"):
        crypto.decrypt_password(f"{base64.b64encode(b'1'*16).decode()}:{base64.b64encode(b'bad').decode()}")


def test_decrypt_env_value_requires_ciphertext_when_configured(monkeypatch):
    monkeypatch.setenv("ENCRYPT_KEY", "00112233445566778899aabbccddeeff")
    monkeypatch.delenv("OPTIONAL_SECRET", raising=False)
    assert crypto.decrypt_env_value("OPTIONAL_SECRET") == ""

    encrypted = crypto.encrypt_password("secret")
    monkeypatch.setenv("OPTIONAL_SECRET", encrypted)
    assert crypto.decrypt_env_value("OPTIONAL_SECRET") == "secret"

    monkeypatch.setenv("OPTIONAL_SECRET", "plain-secret")
    with pytest.raises(ValueError, match="OPTIONAL_SECRET 配置错误"):
        crypto.decrypt_env_value("OPTIONAL_SECRET")
