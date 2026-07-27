import asyncio
import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import crypto, file_manager, storage_paths
from utils import query_result_store


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


@pytest.mark.asyncio
async def test_file_manager_save_read_delete_and_list(tmp_path):
    upload = SimpleNamespace(
        filename="报告.txt",
        read=lambda: asyncio.sleep(0, result=b"hello"),
    )

    saved = await file_manager.save_file(str(tmp_path), "conv_a", upload, allowed_extensions=[".txt"])
    assert saved["file_name"] == "报告.txt"
    assert saved["file_size"] == 5
    assert saved["mime_type"] == "text/plain"
    assert Path(saved["file_path"]).exists()

    listed = file_manager.list_files(str(tmp_path), "conv_a")
    assert listed[0]["file_size"] == 5

    inline = await file_manager.read_file(str(tmp_path), "conv_a", saved["file_id"], preview=True)
    assert inline.body == b"hello"
    assert inline.headers["content-disposition"].startswith("inline")

    attachment = await file_manager.read_file(str(tmp_path), "conv_a", saved["file_id"], preview=False)
    assert attachment.headers["content-disposition"].startswith("attachment")

    assert await file_manager.delete_file(str(tmp_path), "conv_a", saved["file_id"]) is True
    assert await file_manager.delete_file(str(tmp_path), "conv_a", saved["file_id"]) is False


@pytest.mark.asyncio
async def test_file_manager_validation_branches(tmp_path):
    assert file_manager.get_upload_dir(str(tmp_path), "conv").endswith("conv/uploads")
    assert file_manager.get_file_path(str(tmp_path), "conv", "a.bin").endswith("conv/uploads/a.bin")
    assert file_manager.validate_path("safe_id") is True
    assert file_manager.validate_path("../bad") is False
    assert file_manager.get_mime_type("unknown.bin") == "application/octet-stream"
    assert file_manager.list_files(str(tmp_path), "../bad") == []
    assert file_manager.list_files(str(tmp_path), "missing") == []

    no_name = SimpleNamespace(filename="", read=lambda: asyncio.sleep(0, result=b""))
    with pytest.raises(ValueError, match="文件名为空"):
        await file_manager.save_file(str(tmp_path), "conv", no_name)

    bad_conv = SimpleNamespace(filename="a.txt", read=lambda: asyncio.sleep(0, result=b""))
    with pytest.raises(ValueError, match="无效的会话ID"):
        await file_manager.save_file(str(tmp_path), "../bad", bad_conv)

    bad_ext = SimpleNamespace(filename="a.exe", read=lambda: asyncio.sleep(0, result=b""))
    with pytest.raises(ValueError, match="不支持的文件格式"):
        await file_manager.save_file(str(tmp_path), "conv", bad_ext, allowed_extensions=[".txt"])

    with pytest.raises(ValueError, match="无效的会话ID"):
        await file_manager.read_file(str(tmp_path), "../bad", "a.txt")
    with pytest.raises(ValueError, match="无效的文件ID"):
        await file_manager.read_file(str(tmp_path), "conv", "../bad")
    with pytest.raises(FileNotFoundError):
        await file_manager.read_file(str(tmp_path), "conv", "missing.txt")
    assert await file_manager.delete_file(str(tmp_path), "../bad", "a.txt") is False


def test_query_result_store_writes_and_loads_current_conversation_only(tmp_path):
    store = query_result_store.QueryResultStore(tmp_path)
    record = store.save({"rows": [1]}, conversation_id="conv_a", user_id="u1")

    assert store.storage_path_for(
        result_id=record.result_id,
        conversation_id="conv_a",
        user_id="u1",
    ) == "users/u1/threads/conv_a/user-data/outputs/.tool-results/query_result/" + record.result_id + ".json"
    assert store.load_for_conversation(
        result_id=record.result_id,
        conversation_id="conv_a",
        user_id="u1",
    ) == {"rows": [1]}
    assert store.load_for_conversation(
        result_id=record.result_id,
        conversation_id="conv_a",
        user_id="u2",
    ) is None
    with pytest.raises(ValueError, match="result_id"):
        store.load_for_conversation(
            result_id="../outside",
            conversation_id="conv_a",
            user_id="u1",
        )


def test_query_result_store_rejects_tampered_metadata(tmp_path):
    store = query_result_store.QueryResultStore(tmp_path)
    record = store.save({"rows": [1]}, conversation_id="conv_a", user_id="u1")
    file_path = tmp_path / store.storage_path_for(
        result_id=record.result_id,
        conversation_id="conv_a",
        user_id="u1",
    )
    file_path.write_text('{"result_id":"bad","conversation_id":"conv_a","user_id":"u1","data":{}}', encoding="utf-8")
    assert store.load_for_conversation(
        result_id=record.result_id,
        conversation_id="conv_a",
        user_id="u1",
    ) is None


def test_query_result_store_handles_corrupt_json(tmp_path):
    store = query_result_store.QueryResultStore(tmp_path)
    bad_path = tmp_path / "users" / "u1" / "threads" / "conv" / "user-data" / "outputs" / ".tool-results" / "query_result" / "bad.json"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("{bad", encoding="utf-8")
    assert store.load_for_conversation(
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


def test_crypto_command_line_usage(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["crypto.py"])
    with pytest.raises(SystemExit) as exc:
        crypto.main()
    assert exc.value.code == 1
    assert "用法:" in capsys.readouterr().out


def test_crypto_command_line_encrypt_decrypt_and_error(monkeypatch, capsys):
    monkeypatch.setenv("ENCRYPT_KEY", "00112233445566778899aabbccddeeff")

    monkeypatch.setattr("sys.argv", ["crypto.py", "encrypt", "secret"])
    crypto.main()
    encrypted = capsys.readouterr().out.strip()
    assert ":" in encrypted

    monkeypatch.setattr("sys.argv", ["crypto.py", "decrypt", encrypted])
    crypto.main()
    assert capsys.readouterr().out.strip() == "secret"

    monkeypatch.setattr("sys.argv", ["crypto.py", "decrypt", "bad-format"])
    with pytest.raises(SystemExit) as exc:
        crypto.main()
    assert exc.value.code == 1
    assert "错误:" in capsys.readouterr().out


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
