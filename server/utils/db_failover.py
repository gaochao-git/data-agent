"""MySQL 节点解析和应用层 failover 工具。

后端当前使用的高可用策略：

- 配置格式是逗号分隔的 ``host:port`` 列表。每个节点都必须显式写端口；
  不再支持通过独立 ``*_PORT`` 变量补齐端口。
- 启动时按顺序探测节点。如果是连接类错误，例如无法连接、server gone
  away、连接丢失，则继续尝试下一个节点。非连接类错误，例如密码错误、
  access denied、数据库不存在、SQL/权限错误，都属于配置或权限问题，必须
  原样抛出，不能伪装成节点不可用。
- 运行中使用 fail-fast 策略。遇到断连的当前请求允许失败；选择器推进到下
  一个节点，后续请求会用新的数据库上下文连接新节点。
- 成功选中的节点会持续使用，直到该节点出现连接类失败才会切换到下一个
  节点。这里实现的是 failover，不做轮询分流或负载均衡。
- 节点状态是进程内状态。Uvicorn/Gunicorn 多 worker 之间不共享当前节点，
  每个 worker 在自己观测到连接错误后独立收敛。
- 没有后台健康检查，也不会自动切回恢复节点。恢复后的节点只有在后续轮转
  failover 再次到达时才会被使用。
- 如果启动阶段所有节点都不可用，应用启动失败；后续节点恢复也不会自动
  拉起应用，需要重新启动服务。如果运行中所有节点都不可用，请求会逐次
  失败并顺序推进节点；之后某个节点恢复时，后续请求轮转到该节点即可
  自然恢复成功。

本模块只放公共的解析、错误分类和节点选择逻辑。实际数据库探测、engine
释放、checkpointer 上下文包装等边界动作保留在各自模块中。
"""

from __future__ import annotations

from dataclasses import dataclass
import threading

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

MYSQL_CONNECTION_ERROR_CODES = {2003, 2006, 2013}


@dataclass(frozen=True)
class DatabaseEndpoint:
    host: str
    port: int

    def label(self) -> str:
        return f"{self.host}:{self.port}"


def parse_database_endpoints(raw_hosts: str) -> list[DatabaseEndpoint]:
    """解析逗号分隔的 host:port 节点，并去重。"""
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


def _extract_mysql_error_code(err: Exception) -> int | None:
    original = getattr(err, "orig", None) or err
    args = getattr(original, "args", None)
    if not args:
        return None
    try:
        return int(args[0])
    except (TypeError, ValueError):
        return None


def is_mysql_connection_error(err: Exception) -> bool:
    """仅在错误表示 MySQL 连接已断开或不可达时返回 True。

    这里不能把所有 OperationalError/InterfaceError 都当成 failover 信号。
    MySQL 也会用这类异常表示 access denied、unknown database 等配置或权限
    问题；如果对这些错误做 failover，会掩盖真正的问题。
    """
    if isinstance(err, DBAPIError) and getattr(err, "connection_invalidated", False):
        return True
    if not isinstance(err, (OperationalError, InterfaceError)):
        return _extract_mysql_error_code(err) in MYSQL_CONNECTION_ERROR_CODES
    return _extract_mysql_error_code(err) in MYSQL_CONNECTION_ERROR_CODES


class DatabaseEndpointSelector:
    """线程安全的进程内顺序 failover 节点选择器。"""

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
        """仅当 endpoint 仍是当前活动节点时才推进。

        多个并发请求可能同时失败在同一个坏节点上。第一个失败请求推进节点
        即可；后续来自旧 session 的失败应感知当前节点已经变化，避免继续推
        进而跳过额外节点。
        """
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
