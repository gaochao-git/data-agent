"""
加密工具模块

提供对敏感数据（如密码）的加密和解密功能
使用 SM4 国密算法（CBC 模式，PKCS7 填充由 gmssl 自动处理）

命令行加解密入口见 scripts/crypto.py（自动加载 conf/.env-<env>，支持 key/decrypt-env）。
"""

import base64
import os

from gmssl.sm4 import SM4_DECRYPT, SM4_ENCRYPT, CryptSM4

from utils.logger import get_logger

logger = get_logger(__name__)


def _get_sm4_key() -> bytes | None:
    """
    获取 SM4 密钥（16字节）

    密钥格式：32字符的十六进制字符串，解码后为16字节
    """
    encrypt_key = os.getenv("ENCRYPT_KEY")
    if not encrypt_key:
        return None
    try:
        key = bytes.fromhex(encrypt_key)
        if len(key) != 16:
            logger.error(f"SM4 密钥长度错误：期望16字节，实际{len(key)}字节")
            return None
        return key
    except ValueError as e:
        logger.error(f"SM4 密钥格式错误（应为32字符十六进制）: {e}")
        return None


def encrypt_password(password: str) -> str:
    """
    使用 SM4-CBC 加密密码

    Args:
        password: 明文密码

    Returns:
        加密后的密码字符串（base64(iv):base64(ciphertext)），加密失败抛出异常
    """
    if not password:
        raise ValueError("密码不能为空")

    key = _get_sm4_key()
    if key is None:
        raise ValueError("ENCRYPT_KEY 未配置或格式错误")

    # 生成随机 IV
    iv = os.urandom(16)

    # 加密（gmssl 默认使用 PKCS7 填充）
    crypt_sm4 = CryptSM4()
    crypt_sm4.set_key(key, SM4_ENCRYPT)
    encrypted = crypt_sm4.crypt_cbc(iv, password.encode("utf-8"))

    # 格式：base64(iv):base64(ciphertext)
    iv_b64 = base64.b64encode(iv).decode("ascii")
    cipher_b64 = base64.b64encode(encrypted).decode("ascii")
    return f"{iv_b64}:{cipher_b64}"


def decrypt_password(encrypted_password: str) -> str:
    """
    解密 SM4 加密的密码

    Args:
        encrypted_password: 加密的密码字符串

    Returns:
        解密后的明文密码，解密失败抛出异常
    """
    if not encrypted_password:
        raise ValueError("密码未配置")

    key = _get_sm4_key()
    if key is None:
        raise ValueError("ENCRYPT_KEY 未配置或格式错误")

    # 解析格式：base64(iv):base64(ciphertext)
    parts = encrypted_password.split(":")
    if len(parts) != 2:
        raise ValueError(
            "密码格式错误，应为 base64(iv):base64(ciphertext)，请使用 python scripts/crypto.py encrypt 加密"
        )

    try:
        iv = base64.b64decode(parts[0], validate=True)
        ciphertext = base64.b64decode(parts[1], validate=True)
    except Exception:
        raise ValueError("密码 base64 解码失败，请重新加密") from None

    if len(iv) != 16:
        raise ValueError(f"密码 IV 长度错误：期望16字节，实际{len(iv)}字节，请重新加密")

    # 解密（gmssl 自动处理 PKCS7 去填充）
    try:
        crypt_sm4 = CryptSM4()
        crypt_sm4.set_key(key, SM4_DECRYPT)
        decrypted = crypt_sm4.crypt_cbc(iv, ciphertext)
        return decrypted.decode("utf-8")
    except Exception:
        raise ValueError("密码解密失败，密钥或密文不匹配，请重新加密") from None


def decrypt_env_value(name: str, *, required: bool = False) -> str:
    """读取加密环境变量；有值时必须是 SM4 密文。"""
    encrypted_value = str(os.getenv(name) or "").strip()
    if not encrypted_value:
        if required:
            raise ValueError(f"{name} 未配置")
        return ""
    try:
        return decrypt_password(encrypted_value)
    except ValueError as exc:
        raise ValueError(f"{name} 配置错误: {exc}") from exc
