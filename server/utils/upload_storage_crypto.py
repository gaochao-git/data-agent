"""上传文件的应用层加密原型（默认关闭）。

启用方式：设置 ``CHAT_DATA_UPLOAD_ENCRYPTION_KEY`` 为 SM4 密文（与数据库密码共用 ENCRYPT_KEY）。
文件格式：``CDE1`` + 12 字节 nonce + AES-256-GCM 密文 + 16 字节 tag。
未带 magic 的旧文件按明文读取，保证向后兼容。
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import os
import shutil
import tempfile
import time
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from utils.crypto import decrypt_password
from utils.logger import get_logger

logger = get_logger(__name__)

MAGIC = b"CDE1"
NONCE_BYTES = 12
TAG_BYTES = 16
_UPLOAD_KEY_ENV = "CHAT_DATA_UPLOAD_ENCRYPTION_KEY"


class UploadCryptoError(RuntimeError):
    """密钥无效、密文损坏或解密失败。"""


@lru_cache(maxsize=4)
def _decode_key(raw: str) -> bytes:
    """解析密钥：优先 32 字节 base64/hex，否则按口令 SHA-256 派生。

    原型阶段允许任意非空口令，避免手工配置长度不对直接打断上传；生产应使用
    32 字节随机密钥并通过 KMS 管理。
    """
    try:
        key = base64.b64decode(raw, validate=True)
        if len(key) == 32:
            return key
    except Exception:  # noqa: BLE001 - 非 base64 时走口令派生
        pass
    try:
        key = bytes.fromhex(raw)
        if len(key) == 32:
            return key
    except ValueError:
        pass
    logger.warning("CHAT_DATA_UPLOAD_ENCRYPTION_KEY 不是 32 字节 base64/hex，按口令 SHA-256 派生")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encryption_key() -> bytes:
    """读取 SM4 密文配置（与数据库密码同一 ENCRYPT_KEY），解密后再转 AES 密钥。"""
    raw = str(os.getenv(_UPLOAD_KEY_ENV) or "").strip()
    if not raw:
        raise UploadCryptoError("未配置 CHAT_DATA_UPLOAD_ENCRYPTION_KEY")
    try:
        plaintext = decrypt_password(raw)
    except ValueError as exc:
        raise UploadCryptoError(f"CHAT_DATA_UPLOAD_ENCRYPTION_KEY 解密失败: {exc}") from exc
    return _decode_key(plaintext)


def encryption_enabled() -> bool:
    return bool(str(os.getenv(_UPLOAD_KEY_ENV) or "").strip())


def is_encrypted_file(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def new_stream_encryptor():  # noqa: ANN201 - cryptography 的 encryptor 类型不在本模块暴露
    """返回 (nonce, encryptor)，供上传循环边读边写。"""
    key = encryption_key()
    nonce = os.urandom(NONCE_BYTES)
    return nonce, Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()


def encrypt_stream(chunks: Iterable[bytes], target: Path) -> int:
    """流式加密写目标文件，返回明文字节数。"""
    key = encryption_key()
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    total = 0
    with Path(target).open("wb") as handle:
        handle.write(MAGIC)
        handle.write(nonce)
        for chunk in chunks:
            if not chunk:
                continue
            total += len(chunk)
            handle.write(encryptor.update(chunk))
        handle.write(encryptor.finalize())
        handle.write(encryptor.tag)
    return total


def encrypt_bytes(data: bytes) -> bytes:
    key = encryption_key()
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    ciphertext = encryptor.update(data) + encryptor.finalize()
    return MAGIC + nonce + ciphertext + encryptor.tag


def decrypt_bytes(data: bytes) -> bytes:
    if not data.startswith(MAGIC):
        return data
    if len(data) < len(MAGIC) + NONCE_BYTES + TAG_BYTES:
        raise UploadCryptoError("密文文件不完整")
    nonce = data[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
    ciphertext = data[len(MAGIC) + NONCE_BYTES : -TAG_BYTES]
    tag = data[-TAG_BYTES:]
    try:
        decryptor = Cipher(algorithms.AES(encryption_key()), modes.GCM(nonce, tag)).decryptor()
        return decryptor.update(ciphertext) + decryptor.finalize()
    except Exception as exc:  # noqa: BLE001 - 密钥错误/内容被篡改都走同一错误
        raise UploadCryptoError("上传文件解密失败") from exc


def read_plaintext_bytes(path: Path) -> bytes:
    """读取上传文件；密文自动解密，旧明文原样返回。"""
    data = Path(path).read_bytes()
    return decrypt_bytes(data)


_MATERIALIZE_TTL_SECONDS = int(os.getenv("CHAT_DATA_DECRYPT_TTL_SECONDS", "3600"))
_MATERIALIZE_MAX_BYTES = int(os.getenv("CHAT_DATA_DECRYPT_MAX_BYTES", str(512 * 1024 * 1024)))
_MATERIALIZE_BASE_ENV = "CHAT_DATA_DECRYPT_CACHE_DIR"
_materialized_dir_ready = False


def _materialize_base() -> Path:
    override = str(os.getenv(_MATERIALIZE_BASE_ENV) or "").strip()
    if override:
        return Path(override)
    shared_memory = Path("/dev/shm")
    if shared_memory.is_dir():
        return shared_memory / "chatdata-decrypted"
    return Path(tempfile.gettempdir()) / "chatdata-decrypted"


def _prepare_materialize_dir() -> Path:
    """进程级目录，启动时清掉本进程旧目录与 24h 前的其他进程目录。"""
    global _materialized_dir_ready
    base = _materialize_base()
    process_dir = base / str(os.getpid())
    if not _materialized_dir_ready:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.rmtree(process_dir, ignore_errors=True)
        cutoff = time.time() - 24 * 3600
        for sibling in base.iterdir():
            if sibling == process_dir or not sibling.is_dir():
                continue
            try:
                if sibling.stat().st_mtime < cutoff:
                    shutil.rmtree(sibling, ignore_errors=True)
            except OSError:
                continue
        _materialized_dir_ready = True
    process_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return process_dir


def _prune_materialized(directory: Path, keep: Path) -> None:
    """按 TTL 和总容量清理明文影子，避免 /dev/shm 无限增长。"""
    now = time.time()
    entries: list[tuple[float, int, Path]] = []
    total = 0
    for item in directory.iterdir():
        if not item.is_file() or item == keep:
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        if stat.st_mtime < now - _MATERIALIZE_TTL_SECONDS:
            item.unlink(missing_ok=True)
            continue
        entries.append((stat.st_mtime, stat.st_size, item))
        total += stat.st_size
    total += keep.stat().st_size if keep.exists() else 0
    for _, size, item in sorted(entries):
        if total <= _MATERIALIZE_MAX_BYTES:
            break
        item.unlink(missing_ok=True)
        total -= size


def _cleanup_process_materialized() -> None:
    shutil.rmtree(_materialize_base() / str(os.getpid()), ignore_errors=True)


atexit.register(_cleanup_process_materialized)

_materialized: dict[Path, Path] = {}


def materialize_plaintext(path: Path) -> Path:
    """把加密上传文件解密到受控临时路径；非加密文件直接返回原路径。

    反查表是进程内的，只在同一进程/同一请求内保证 ``to_visible_path`` 可还原。
    """
    source = Path(path)
    if not is_encrypted_file(source):
        return source
    stat = source.stat()
    digest = hashlib.sha256(f"{source}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()
    directory = _prepare_materialize_dir()
    target = directory / f"{digest}{source.suffix}"
    if not target.exists():
        plaintext = read_plaintext_bytes(source)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_bytes(plaintext)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    _materialized[target.resolve()] = source.resolve()
    _prune_materialized(directory, target)
    return target


def original_upload_path(materialized: Path) -> Path | None:
    """解密临时路径对应的原始上传路径，供 to_visible_path 还原展示路径。"""
    return _materialized.get(Path(materialized).resolve())


def clear_materialized_cache() -> None:
    global _materialized_dir_ready
    _materialized.clear()
    _materialized_dir_ready = False
    _decode_key.cache_clear()
