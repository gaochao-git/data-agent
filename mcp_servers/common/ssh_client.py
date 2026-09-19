"""SSH 客户端（paramiko）：自 fullstack-langgraph 平移，默认连接参数改读 data-agent 环境变量。"""

import io
import os
from typing import Optional

import paramiko

from mcp_servers.common.config import MCP_DIAG_SSH_KEY_FILE, MCP_DIAG_SSH_PORT, MCP_DIAG_SSH_USERNAME
from mcp_servers.common.output import debug_log


class SSHClient:
    """SSH 客户端管理 - 从 config.yaml 读取默认配置"""

    def __init__(self, host: str, port: int = None, username: Optional[str] = None,
                 password: Optional[str] = None, key_file: Optional[str] = None):
        self.host = host
        self.port = port if port is not None else MCP_DIAG_SSH_PORT
        self.username = username if username is not None else MCP_DIAG_SSH_USERNAME
        self.password = password
        self.key_file = key_file if key_file is not None else MCP_DIAG_SSH_KEY_FILE
        self._client: Optional[paramiko.SSHClient] = None

    def _load_private_key(self, key_path: str) -> paramiko.PKey:
        """显式尝试多种私钥格式，避免仅依赖 Paramiko 自动识别。"""
        expanded_key_path = os.path.expanduser(key_path)
        key_loaders = (
            ("Ed25519", getattr(paramiko, "Ed25519Key", None)),
            ("RSA", getattr(paramiko, "RSAKey", None)),
            ("ECDSA", getattr(paramiko, "ECDSAKey", None)),
        )

        last_error: Optional[Exception] = None
        for key_type_name, key_cls in key_loaders:
            if key_cls is None:
                continue
            try:
                pkey = key_cls.from_private_key_file(expanded_key_path)
                debug_log(f"[SSH {self.host}] loaded private key type={key_type_name} path={expanded_key_path}")
                return pkey
            except paramiko.PasswordRequiredException as exc:
                raise ValueError(f"SSH 私钥需要口令，当前不支持带口令私钥: {expanded_key_path}") from exc
            except Exception as exc:
                last_error = exc

        # 兼容少量需要从文本再次解析的场景
        try:
            with open(expanded_key_path, "r", encoding="utf-8") as f:
                key_text = f.read()
            for key_type_name, key_cls in key_loaders:
                if key_cls is None:
                    continue
                try:
                    pkey = key_cls.from_private_key(io.StringIO(key_text))
                    debug_log(f"[SSH {self.host}] loaded private key(type=text) type={key_type_name} path={expanded_key_path}")
                    return pkey
                except paramiko.PasswordRequiredException as exc:
                    raise ValueError(f"SSH 私钥需要口令，当前不支持带口令私钥: {expanded_key_path}") from exc
                except Exception as exc:
                    last_error = exc
        except Exception as exc:
            last_error = exc

        raise ValueError(f"无法解析 SSH 私钥文件: {expanded_key_path}, err={last_error}")

    def connect(self) -> paramiko.SSHClient:
        """建立 SSH 连接"""
        if self._client is not None:
            return self._client

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": 5,
            "allow_agent": False,
            "look_for_keys": False,
        }

        if self.key_file and os.path.exists(os.path.expanduser(self.key_file)):
            expanded_key_path = os.path.expanduser(self.key_file)
            connect_kwargs["pkey"] = self._load_private_key(expanded_key_path)
            debug_log(
                f"[SSH {self.host}] connect with key username={self.username} "
                f"port={self.port} key_file={expanded_key_path}"
            )
        elif self.password:
            connect_kwargs["password"] = self.password
            debug_log(
                f"[SSH {self.host}] connect with password username={self.username} "
                f"port={self.port}"
            )
        else:
            debug_log(
                f"[SSH {self.host}] connect without password/key username={self.username} "
                f"port={self.port}"
            )

        self._client.connect(**connect_kwargs)
        return self._client

    def exec_command(self, command: str, timeout: int = 30) -> tuple:
        """执行命令"""
        debug_log(f"[SSH {self.host}] {command}")
        client = self.connect()
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        return stdout.read().decode(), stderr.read().decode()

    def get_sftp(self) -> paramiko.SFTPClient:
        """获取 SFTP 客户端"""
        client = self.connect()
        return client.open_sftp()

    def close(self):
        """关闭连接"""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_ssh_client(host: str) -> SSHClient:
    """获取 SSH 客户端"""
    return SSHClient(host)

