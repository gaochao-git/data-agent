"""通用工具函数。"""

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ENV_KEY = "CHAT_DATA_ENV"
ACTIVE_ENV_KEY = "CHAT_DATA_ACTIVE_ENV"
DEPLOY_ENV_HOSTS_FILE = "deploy_env_hosts.json"


def get_local_ip() -> str:
    """获取本机 IP，失败时回退本地环回地址。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0] or "127.0.0.1")
    except Exception:
        return "127.0.0.1"


def get_local_ips() -> tuple[str, ...]:
    """获取本机可能的 IP 列表。"""
    ips = [get_local_ip()]
    try:
        hostname = socket.gethostname()
        ips.extend(socket.gethostbyname_ex(hostname)[2])
    except Exception as exc:
        # 主机名解析失败不影响主流程,仅保留本地环回/首选 IP;此处函数内导入是因为
        # utils.logger 头部反向引用了本模块,文件头导入会形成循环依赖。
        from utils.logger import get_logger
        get_logger(__name__).warning("解析本机 hostname 对应 IP 列表失败,仅保留本地环回/首选 IP: %s", exc)
    return tuple(dict.fromkeys(ip for ip in ips if ip))


def _host_matches(pattern: str, ip: str) -> bool:
    host = str(pattern or "").strip()
    value = str(ip or "").strip()
    if not host or not value:
        return False
    if host.endswith("*"):
        return value.startswith(host[:-1])
    if host.endswith("."):
        return value.startswith(host)
    return value == host


def _normalize_hosts_config(raw_config: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw_config, dict):
        raise ValueError("deploy_env_hosts.json 必须是对象格式")
    result: dict[str, tuple[str, ...]] = {}
    for env_name, hosts in raw_config.items():
        if isinstance(hosts, str):
            host_values = (hosts,)
        elif isinstance(hosts, list):
            host_values = tuple(str(item).strip() for item in hosts)
        else:
            raise ValueError(f"环境 {env_name} 的 IP 配置必须是字符串或字符串数组")
        result[str(env_name).strip()] = tuple(host for host in host_values if host)
    return {key: value for key, value in result.items() if key and value}


def load_deploy_env_hosts(conf_dir: Path | str) -> dict[str, tuple[str, ...]]:
    """读取 IP 到环境的映射配置。"""
    config_path = Path(conf_dir) / DEPLOY_ENV_HOSTS_FILE
    if not config_path.exists():
        raise RuntimeError(f"未找到环境 IP 映射配置文件: {config_path}")
    try:
        raw_config = json.loads(config_path.read_text(encoding="utf-8"))
        return _normalize_hosts_config(raw_config)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"环境 IP 映射配置文件格式错误: {config_path}") from exc


def resolve_deploy_env(hosts_config: dict[str, tuple[str, ...]], local_ips: tuple[str, ...] | None = None) -> str:
    """根据本机 IP 解析部署环境。"""
    ips = local_ips or get_local_ips()
    matched_envs: list[str] = []
    for env_name, hosts in hosts_config.items():
        if any(_host_matches(host, ip) for host in hosts for ip in ips):
            matched_envs.append(env_name)
    if not matched_envs:
        raise RuntimeError(f"未识别当前机器所属环境，请在 {DEPLOY_ENV_HOSTS_FILE} 中配置本机 IP: {', '.join(ips) or '-'}")
    if len(matched_envs) > 1:
        raise RuntimeError(f"当前机器 IP 同时匹配多个环境: {', '.join(matched_envs)}")
    return matched_envs[0]


def get_active_deploy_env() -> str:
    """读取当前进程已识别的部署环境。"""
    return os.getenv(ACTIVE_ENV_KEY, "").strip() or "unknown"


def get_active_idc() -> str:
    """读取当前日志 IDC 标识。"""
    return get_active_deploy_env()


@dataclass(frozen=True)
class RuntimeEnvLoadResult:
    active_env: str
    loaded_files: tuple[str, ...]
    local_ips: tuple[str, ...]


def _apply_env_file(path: Path, protected_keys: set[str], override_loaded: bool) -> bool:
    if not path.exists():
        return False
    for key, value in dotenv_values(path).items():
        if not key or value is None or key in protected_keys:
            continue
        if override_loaded or key not in os.environ:
            os.environ[key] = value
    return True


def load_runtime_env(server_dir: Path | str) -> RuntimeEnvLoadResult:
    """
    加载运行环境配置。

    优先级：进程环境变量 > CHAT_DATA_ENV 显式指定 > 按 IP 识别的 conf/.env-{env}。
    """
    root = Path(server_dir)
    conf_dir = root / "conf"
    protected_keys = set(os.environ)
    loaded_files: list[str] = []

    local_ips = get_local_ips()
    active_env = os.getenv(ENV_KEY, "").strip()
    if not active_env:
        active_env = resolve_deploy_env(load_deploy_env_hosts(conf_dir), local_ips)

    env_file = conf_dir / f".env-{active_env}"
    if not _apply_env_file(env_file, protected_keys, override_loaded=True):
        raise RuntimeError(f"未找到环境配置文件: {env_file}")
    loaded_files.append(str(env_file))

    os.environ[ACTIVE_ENV_KEY] = active_env
    return RuntimeEnvLoadResult(
        active_env=active_env,
        loaded_files=tuple(loaded_files),
        local_ips=local_ips,
    )
