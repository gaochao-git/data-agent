import asyncio
import base64
import importlib
import os
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import crypto, file_manager, storage_paths
from utils.lang_util import artifact_store


def test_storage_paths_scope_and_path_helpers(tmp_path):
    ctx_default = storage_paths.resolve_complaint_scope(reporter_name=" 张 三-fys-extra ")
    ctx_org = storage_paths.resolve_complaint_scope("org", org_id="org_a", reporter_name="李 四")
    ctx_entity = storage_paths.resolve_complaint_scope("auto", org_id="org_a", entity_id="entity_b")

    assert ctx_default.scope == "default"
    assert ctx_default.reporter_name == "张_三"
    assert ctx_org.path_parts == ("org_a", "李_四")
    assert ctx_entity.path_parts == ("org_a", "entity_b")
    assert ctx_entity.to_meta()["scope_path"] == "org_a/entity_b"

    chain = storage_paths.complaint_scope_read_chain(ctx_entity)
    assert [item.scope for item in chain] == ["entity", "org", "default"]
    assert storage_paths.normalize_complaint_scope_context(ctx_org) is ctx_org
    assert storage_paths.normalize_complaint_scope_context(ctx_entity.to_meta()).scope == "entity"

    assert storage_paths.normalize_user_id(" user/../a ") == "user_.._a"
    assert storage_paths.validate_base_id("base-1") == "base-1"
    assert storage_paths.get_user_dir("user/a").endswith("complaints/user_a")
    assert storage_paths.get_user_complaint_root("user/a", "base-1").endswith("complaints/user_a/base-1")
    assert storage_paths.get_scoped_complaint_root("base-1", ctx_org).endswith("complaints/base-1/org_a/李_四")
    assert storage_paths.get_complaints_real_path("u", "base-1", "a.txt", ctx_org).endswith("complaints/base-1/org_a/李_四/a.txt")

    with pytest.raises(ValueError):
        storage_paths.validate_base_id(".hidden")
    with pytest.raises(ValueError):
        storage_paths.validate_scope_segment("bad value", "org_id")
    with pytest.raises(ValueError):
        storage_paths.resolve_complaint_scope("org")
    with pytest.raises(ValueError):
        storage_paths.resolve_complaint_scope("entity", org_id="org")
    with pytest.raises(ValueError):
        storage_paths.resolve_complaint_scope("bad")
    with pytest.raises(ValueError):
        storage_paths.normalize_complaint_scope_context(object())
    with pytest.raises(ValueError):
        storage_paths.normalize_reporter_name("...")


def test_storage_paths_additional_error_and_dedupe_branches():
    with pytest.raises(ValueError, match="Invalid path segment type"):
        storage_paths.normalize_user_id(None)
    with pytest.raises(ValueError, match="must not be empty"):
        storage_paths.validate_base_id("  ")
    with pytest.raises(ValueError, match="unsupported"):
        storage_paths.validate_base_id("bad value")

    assert storage_paths.normalize_reporter_name(" 张/三\\四 ") == "张_三_四"
    assert storage_paths.normalize_reporter_name("张三-fys-case") == "张三"
    assert storage_paths.normalize_reporter_name("-fys-case") == ""

    default_ctx = storage_paths.resolve_complaint_scope("default", reporter_name="张三")
    assert [item.scope_path for item in storage_paths.complaint_scope_read_chain(default_ctx)] == ["default/张三"]

    org_ctx = storage_paths.resolve_complaint_scope("org", org_id="org_a")
    assert [item.scope for item in storage_paths.complaint_scope_read_chain(org_ctx)] == ["org", "default"]

    entity_ctx = storage_paths.resolve_complaint_scope("entity", org_id="default", entity_id="default")
    chain = storage_paths.complaint_scope_read_chain(entity_ctx)
    assert [item.scope_path for item in chain] == ["default/default", "default"]

    assert storage_paths.resolve_complaint_scope("auto", org_id="org_only").scope == "org"
    assert storage_paths.normalize_complaint_scope_context(None) is None
    with pytest.raises(ValueError, match="entity scope"):
        storage_paths.resolve_complaint_scope("entity", entity_id="entity_only")


def test_storage_paths_requires_data_root(monkeypatch):
    monkeypatch.delenv("DATA_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="DATA_ROOT is required"):
        storage_paths._resolve_data_root()

    monkeypatch.setenv("DATA_ROOT", "relative-data")
    resolved = storage_paths._resolve_data_root()
    assert resolved.is_absolute()
    assert resolved.name == "relative-data"


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
    assert listed[0]["file_id"] == saved["file_id"]
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


def test_artifact_store_save_load_delete_and_singleton(tmp_path, monkeypatch):
    store = artifact_store.ArtifactStore(tmp_path)
    artifact_id = store.save("query_result", {"rows": [1]}, conversation_id="conv_a")

    assert store.exists("query_result", artifact_id) is True
    assert store.load("query_result", artifact_id) == {"rows": [1]}
    assert store.load_with_meta("query_result", artifact_id)["conversation_id"] == "conv_a"
    assert store.load_by_id(artifact_id) == {"rows": [1]}
    assert store.load_meta_by_id(artifact_id)["type"] == "query_result"
    assert store.list_ids("query_result") == [artifact_id]
    assert store.delete("query_result", artifact_id) is True
    assert store.delete("query_result", artifact_id) is False
    assert store.load("query_result", artifact_id) is None
    assert store.load_by_id(artifact_id) is None
    assert store.load_meta_by_id(artifact_id) is None
    assert store.load("query_result", "") is None
    assert store.load_with_meta("query_result", "") is None
    assert store.load_by_id("") is None
    assert store.load_meta_by_id("") is None
    assert store.load_by_id("missing") is None
    assert store.load("query_result", "missing") is None

    monkeypatch.setattr(artifact_store, "_default_store", None)
    monkeypatch.setattr(artifact_store, "_DEFAULT_ARTIFACT_DIR", tmp_path / "default")
    default_store = artifact_store.get_artifact_store()
    assert default_store is artifact_store.get_artifact_store()
    saved_id = artifact_store.save_artifact("chart_config", {"chart": "bar"}, artifact_id="chart_a")
    assert saved_id == "chart_a"
    assert artifact_store.load_artifact("chart_config", "chart_a") == {"chart": "bar"}
    assert artifact_store.load_artifact_by_id("chart_a") == {"chart": "bar"}
    assert artifact_store.load_artifact_meta_by_id("chart_a")["id"] == "chart_a"
    assert artifact_store.delete_artifact("chart_config", "chart_a") is True


def test_artifact_store_handles_corrupt_json(tmp_path):
    store = artifact_store.ArtifactStore(tmp_path)
    bad_path = tmp_path / "query_result" / "bad.json"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("{bad", encoding="utf-8")

    assert store.load("query_result", "bad") is None
    assert store.load_with_meta("query_result", "bad") is None
    assert store.load_by_id("bad") is None
    assert store.load_meta_by_id("bad") is None


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
    module = importlib.import_module("utils.crypto")
    monkeypatch.setattr("sys.argv", ["crypto.py"])
    with pytest.raises(SystemExit) as exc:
        exec(Path(module.__file__).read_text(encoding="utf-8"), {"__name__": "__main__", "__file__": module.__file__})
    assert exc.value.code == 1
    assert "用法:" in capsys.readouterr().out


def test_crypto_command_line_encrypt_decrypt_and_error(monkeypatch, capsys):
    monkeypatch.setenv("ENCRYPT_KEY", "00112233445566778899aabbccddeeff")

    monkeypatch.setattr("sys.argv", ["crypto.py", "encrypt", "secret"])
    runpy.run_module("utils.crypto", run_name="__main__")
    encrypted = capsys.readouterr().out.strip()
    assert ":" in encrypted

    monkeypatch.setattr("sys.argv", ["crypto.py", "decrypt", encrypted])
    runpy.run_module("utils.crypto", run_name="__main__")
    assert capsys.readouterr().out.strip() == "secret"

    monkeypatch.setattr("sys.argv", ["crypto.py", "decrypt", "bad-format"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("utils.crypto", run_name="__main__")
    assert exc.value.code == 1
    assert "错误:" in capsys.readouterr().out
