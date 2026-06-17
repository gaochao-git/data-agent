"""Database endpoint parsing and failover state."""

from __future__ import annotations

from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class DatabaseEndpoint:
    host: str
    port: int

    def label(self) -> str:
        return f"{self.host}:{self.port}"


def parse_database_endpoints(raw_hosts: str, default_port: int) -> list[DatabaseEndpoint]:
    """Parse comma-separated host:port entries."""
    endpoints: list[DatabaseEndpoint] = []
    for item in [raw_item.strip() for raw_item in str(raw_hosts or "").split(",") if raw_item.strip()]:
        if ":" not in item:
            raise ValueError(f"数据库节点必须使用 host:port 格式: {item}")
        host, port_text = item.rsplit(":", 1)
        host = host.strip()
        port_text = port_text.strip()
        if not port_text:
            raise ValueError(f"数据库节点端口不能为空: {item}")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"数据库节点端口无效: {item}") from exc
        if not host:
            raise ValueError(f"数据库节点主机不能为空: {item}")
        if port < 1 or port > 65535:
            raise ValueError(f"数据库节点端口超出范围: {item}")
        endpoint = DatabaseEndpoint(host=host, port=port)
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    if not endpoints:
        raise ValueError("数据库节点配置不能为空")
    return endpoints


class DatabaseEndpointSelector:
    """Thread-safe round-robin failover selector."""

    def __init__(self, endpoints: list[DatabaseEndpoint]):
        if not endpoints:
            raise ValueError("数据库节点配置不能为空")
        self._endpoints = list(endpoints)
        self._index = 0
        self._lock = threading.Lock()

    @property
    def endpoints(self) -> list[DatabaseEndpoint]:
        return list(self._endpoints)

    def current(self) -> DatabaseEndpoint:
        with self._lock:
            return self._endpoints[self._index]

    def advance(self) -> tuple[DatabaseEndpoint, DatabaseEndpoint]:
        with self._lock:
            previous = self._endpoints[self._index]
            self._index = (self._index + 1) % len(self._endpoints)
            return previous, self._endpoints[self._index]

    def advance_if_current(self, endpoint: DatabaseEndpoint) -> tuple[DatabaseEndpoint, DatabaseEndpoint, bool]:
        with self._lock:
            previous = self._endpoints[self._index]
            if previous != endpoint:
                return previous, previous, False
            self._index = (self._index + 1) % len(self._endpoints)
            return previous, self._endpoints[self._index], True

    def set_current(self, endpoint: DatabaseEndpoint) -> None:
        with self._lock:
            self._index = self._endpoints.index(endpoint)

    def labels(self) -> list[str]:
        return [endpoint.label() for endpoint in self._endpoints]
