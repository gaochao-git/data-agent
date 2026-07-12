#!/usr/bin/env python3
"""ChatData 配置加解密命令行工具。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from utils.common_util import ACTIVE_ENV_KEY, ENV_KEY, get_local_ip, load_deploy_env_hosts, resolve_deploy_env  # noqa: E402
from utils.crypto import decrypt_password, encrypt_password  # noqa: E402


def _print_usage() -> None:
    print("用法:")
    print("  生成密钥: python scripts/crypto.py key")
    print("  加密明文: python scripts/crypto.py encrypt <明文>")
    print("  解密密文: python scripts/crypto.py decrypt <密文>")
    print("  解密环境变量: python scripts/crypto.py decrypt-env <变量名>")
    print("可通过 CHAT_DATA_ENV=dev 指定加载 conf/.env-dev。")


def _load_encrypt_key() -> None:
    protected_keys = set(os.environ)
    active_env = os.getenv(ENV_KEY, "").strip()
    if not active_env:
        hosts = load_deploy_env_hosts(SERVER_DIR / "conf")
        active_env = resolve_deploy_env(hosts, local_ips=(get_local_ip(),))
    env_file = SERVER_DIR / "conf" / f".env-{active_env}"
    if not env_file.exists():
        raise ValueError(f"未找到环境配置文件: {env_file}")
    for key, value in dotenv_values(env_file).items():
        if key and value is not None and key not in protected_keys:
            os.environ[key] = value
    os.environ[ACTIVE_ENV_KEY] = active_env
    if not os.getenv("ENCRYPT_KEY"):
        raise ValueError(f"环境 {active_env} 未配置 ENCRYPT_KEY")

def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        _print_usage()
        return

    command = args[0].lower()
    if command == "key" and len(args) == 1:
        print(os.urandom(16).hex())
        return
    if command not in {"encrypt", "decrypt", "decrypt-env"} or len(args) != 2:
        _print_usage()
        raise SystemExit(1)

    try:
        _load_encrypt_key()
        if command == "encrypt":
            print(encrypt_password(args[1]))
        elif command == "decrypt":
            print(decrypt_password(args[1]))
        else:
            variable_name = args[1].strip()
            value = os.getenv(variable_name)
            if not value:
                raise ValueError(f"环境变量 {variable_name} 未配置")
            print(decrypt_password(value))
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
