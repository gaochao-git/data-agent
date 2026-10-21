"""上传文件应用层加密原型。"""

from __future__ import annotations

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from utils import upload_storage_crypto as crypto
from utils.crypto import encrypt_password


@pytest.fixture(autouse=True)
def _clean_crypto(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_DATA_DECRYPT_CACHE_DIR", str(tmp_path / "decrypted"))
    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.setenv(
        "CHAT_DATA_UPLOAD_ENCRYPTION_KEY",
        encrypt_password(base64.b64encode(b"k" * 32).decode()),
    )
    crypto.clear_materialized_cache()
    yield
    crypto.clear_materialized_cache()


def test_roundtrip_bytes_and_stream(tmp_path):
    data = "中文内容".encode() * 100
    encrypted = crypto.encrypt_bytes(data)
    assert encrypted.startswith(crypto.MAGIC)
    assert crypto.decrypt_bytes(encrypted) == data

    target = tmp_path / "report.docx"
    size = crypto.encrypt_stream([b"ab", b"cd"], target)
    assert size == 4
    assert crypto.read_plaintext_bytes(target) == b"abcd"


def test_legacy_plaintext_is_readable(tmp_path):
    target = tmp_path / "old.txt"
    target.write_bytes(b"plain")
    assert crypto.read_plaintext_bytes(target) == b"plain"


def test_wrong_key_or_tamper_fails(monkeypatch, tmp_path):
    encrypted = crypto.encrypt_bytes(b"secret")
    monkeypatch.setenv(
        "CHAT_DATA_UPLOAD_ENCRYPTION_KEY",
        encrypt_password(base64.b64encode(b"x" * 32).decode()),
    )
    crypto.clear_materialized_cache()
    with pytest.raises(crypto.UploadCryptoError):
        crypto.decrypt_bytes(encrypted)
    with pytest.raises(crypto.UploadCryptoError):
        crypto.decrypt_bytes(encrypted[:-1] + b"\x00")


def test_materialize_and_visible_path_mapping(tmp_path, monkeypatch):
    source = tmp_path / "uploads" / "report.docx"
    source.parent.mkdir()
    crypto.encrypt_stream([b"hello"], source)
    materialized = crypto.materialize_plaintext(source)
    assert materialized.read_bytes() == b"hello"
    assert crypto.original_upload_path(materialized) == source.resolve()
    assert crypto.materialize_plaintext(source) == materialized


def test_free_form_passphrase_is_derived(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_UPLOAD_ENCRYPTION_KEY", encrypt_password("short-passphrase"))
    crypto.clear_materialized_cache()
    payload = crypto.decrypt_bytes(crypto.encrypt_bytes(b"x"))
    assert payload == b"x"


def test_runtime_materializes_upload_for_local_tools(tmp_path):
    from apps.chat_data_app.agent.workspace.runtime import resolve_workspace_path, to_visible_path

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "report.txt"
    crypto.encrypt_stream([b"visible"], source)
    plain = resolve_workspace_path(str(tmp_path), "/mnt/user-data/uploads/report.txt")
    assert plain.read_bytes() == b"visible"
    assert to_visible_path(str(tmp_path), plain) == "/mnt/user-data/uploads/report.txt"


def test_resolver_can_skip_upload_decryption_for_listing(tmp_path):
    from apps.chat_data_app.agent.workspace.runtime import resolve_workspace_path

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "bad.pdf"
    source.write_bytes(crypto.MAGIC + b"broken")
    resolved = resolve_workspace_path(
        str(tmp_path),
        "/mnt/user-data/uploads/bad.pdf",
        materialize_uploads=False,
    )
    assert resolved == source


@pytest.mark.asyncio
async def test_parse_document_accepts_materialized_upload(tmp_path):
    from apps.chat_data_app.agent.tools.parse_document import create_parse_document_tool
    from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime

    (tmp_path / "workspace").mkdir()
    (tmp_path / "uploads").mkdir()
    source = tmp_path / "uploads" / "note.txt"
    crypto.encrypt_stream([b"hello encrypted"], source)
    runtime = ConversationWorkspaceRuntime(workspace_root=str(tmp_path), user_id="u1", backend="local")
    tool = create_parse_document_tool(runtime)[0]
    result = await tool.ainvoke(
        {
            "source_path": "/mnt/user-data/uploads/note.txt",
            "target_path": "/mnt/user-data/workspace/note.md",
        }
    )
    assert '"success": true' in result
    assert (tmp_path / "workspace" / "note.md").exists()


def test_writable_resolve_returns_encrypted_original(tmp_path):
    from apps.chat_data_app.agent.workspace.runtime import resolve_workspace_path

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "report.txt"
    crypto.encrypt_stream([b"x"], source)
    resolved = resolve_workspace_path(
        str(tmp_path),
        "/mnt/user-data/uploads/report.txt",
        writable=True,
        allow_uploads_write=True,
    )
    assert resolved == source
