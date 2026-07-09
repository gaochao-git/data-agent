"""
日志管理模块，提供统一的日志配置和管理功能，支持控制台输出和文件记录，自动日志轮转
日志规范：
  请求日志文件名：{app_name}_acc.log
  <log_time>|<trace_id>|[parentId]|<request_type>|<app_name>|[idc]|<ip>|<start_time>|<cost_time>|<error_code>|[ext1]|[ext2]
  报警日志文件名：{app_name}_alam.log
  <log_time>|<trace_id>|<alarm_id>|<request_type>|<app_name>|idc|<ip>|<error_code>|<error_msg>|[ext1]|[ext2]
  应用日志文件名：{app_name}_app.log
  <log_time>|<log_level>|<thread_id>|<trace_id>|[idc]|<ip>|<filename>|<module>|<lineno>|<msg，能够打印错误堆栈>|[ext1]|[ext2]
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from contextvars import ContextVar
from datetime import datetime
from typing import Optional

from utils.common_util import get_active_idc, get_local_ip


DEFAULT_BACKUP_COUNT = 30  # 保留30天
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)
user_ctx: ContextVar[Optional[str]] = ContextVar("user", default=None)
_STANDARD_LOG_RECORD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "taskName",
    "processName",
    "process",
    "message",
    "getMessage",
    "exc_info",
    "exc_text",
    "stack_info",
    "request_id",
    "user_id",
    "username",
    "user",
    "idc",
    "ip",
    "extra_fields",
}


LOCAL_IP = get_local_ip()


def _env_text(key: str, default: str) -> str:
    value = os.getenv(key)
    if value is None:
        return default
    text = value.strip()
    return text or default


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    text = value.strip().lower()
    if not text:
        return default
    return text in ("true", "1", "yes", "y", "on")


def get_current_log_level() -> str:
    """读取当前日志级别。"""
    return _env_text("LOG_LEVEL", "INFO")


def get_current_log_dir() -> str:
    """读取当前日志目录。"""
    return _env_text("LOG_DIR", "logs")


def get_current_log_file() -> str:
    """读取当前日志文件名。"""
    return _build_log_file_name("app")


def _get_log_file_prefix() -> str:
    return get_current_log_app_name().strip() or "data-agent"


def _build_log_file_name(suffix: str) -> str:
    return f"{_get_log_file_prefix()}_{suffix}.log"


def get_current_access_log_file() -> str:
    """读取访问日志文件名。"""
    return _build_log_file_name("acc")


def get_current_alarm_log_file() -> str:
    """读取告警日志文件名。"""
    return _build_log_file_name("alarm")


def get_current_console_output() -> bool:
    """读取是否输出控制台日志。"""
    return _env_bool("LOG_CONSOLE", True)


def get_current_log_app_name() -> str:
    """读取日志中的应用名。"""
    return _env_text("LOG_APP_NAME", "data-agent")


def get_current_log_idc() -> str:
    """读取日志 IDC 标识。"""
    return get_active_idc()


def resolve_log_path(path: str) -> str:
    """将日志路径规范为绝对路径。"""
    raw = str(path or "").strip()
    if not raw:
        return os.path.abspath("logs")
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(raw)


def build_log_user(user_id: str | None = None, username: str | None = None) -> str:
    """构造日志中的统一用户标识。"""
    normalized_user_id = str(user_id or "").strip()
    normalized_username = str(username or "").strip()
    if normalized_user_id and normalized_username and normalized_user_id != normalized_username:
        return f"{normalized_user_id}-{normalized_username}"
    return normalized_user_id or normalized_username


def set_log_context(request_id: str | None = None, user_id: str | None = None, username: str | None = None, user: str | None = None) -> None:
    """设置当前请求日志上下文。"""
    if request_id:
        request_id_ctx.set(str(request_id))
    log_user = str(user or "").strip() or build_log_user(user_id, username)
    if log_user:
        user_ctx.set(log_user)


def clear_log_context() -> None:
    """清理当前请求日志上下文。"""
    request_id_ctx.set(None)
    user_ctx.set(None)


def _format_log_time(created: float) -> str:
    return datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _clean_log_part(value: object, default: str = "-") -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return default
    return text.replace("|", "/").replace("\r", "\\r").replace("\n", "\\n")


def _collect_record_extra_fields(record: logging.LogRecord) -> dict[str, object]:
    extra_fields = {
        str(key): value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_RECORD_FIELDS and not str(key).startswith("_")
    }
    configured_extra = getattr(record, "extra_fields", None)
    if isinstance(configured_extra, dict):
        extra_fields.update({str(key): value for key, value in configured_extra.items() if str(key).strip()})
    return extra_fields


def _append_message_parts(message: str, user: object, extra_fields: dict[str, object]) -> str:
    parts = [message]
    if user:
        parts.append(f"user={user}")
    if extra_fields:
        parts.append(", ".join(f"{key}={value}" for key, value in extra_fields.items()))
    return " | ".join(parts)


class PipeLogFormatter(logging.Formatter):
    """应用日志管道分隔格式。"""

    def format(self, record: logging.LogRecord) -> str:
        log_time = _format_log_time(record.created)
        trace_id = getattr(record, "request_id", None) or request_id_ctx.get() or "-"
        user = (
            getattr(record, "user", None)
            or build_log_user(getattr(record, "user_id", None), getattr(record, "username", None))
            or user_ctx.get()
            or ""
        )
        idc = getattr(record, "idc", None) or get_current_log_idc()
        ip = getattr(record, "ip", None) or LOCAL_IP
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        message = _append_message_parts(message, user, _collect_record_extra_fields(record))

        return "|".join([
            log_time,
            record.levelname,
            str(record.thread),
            str(trace_id),
            str(idc),
            str(ip),
            record.filename,
            record.module,
            str(record.lineno),
            message,
        ])


class AccessLogFormatter(logging.Formatter):
    """访问日志管道分隔格式。"""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "request_id", None) or request_id_ctx.get() or "-"
        fields = [
            _format_log_time(record.created),
            trace_id,
            getattr(record, "parent_id", "-"),
            getattr(record, "request_type", "HTTP"),
            getattr(record, "app_name", get_current_log_app_name()),
            getattr(record, "idc", get_current_log_idc()),
            getattr(record, "ip", LOCAL_IP),
            getattr(record, "start_time", "-"),
            getattr(record, "cost_time", "-"),
            getattr(record, "error_code", "0"),
            getattr(record, "ext1", "-"),
            getattr(record, "ext2", record.getMessage()),
        ]
        return "|".join(_clean_log_part(field) for field in fields)


class AlarmLogFormatter(logging.Formatter):
    """告警日志管道分隔格式。"""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "request_id", None) or request_id_ctx.get() or "-"
        fields = [
            _format_log_time(record.created),
            trace_id,
            getattr(record, "alarm_id", "-"),
            getattr(record, "request_type", "ALARM"),
            getattr(record, "app_name", get_current_log_app_name()),
            getattr(record, "idc", get_current_log_idc()),
            getattr(record, "ip", LOCAL_IP),
            getattr(record, "error_code", "500"),
            record.getMessage(),
            getattr(record, "ext1", "-"),
            getattr(record, "ext2", "-"),
        ]
        return "|".join(_clean_log_part(field) for field in fields)


class LoggerManager:
    """日志管理器"""

    _loggers = {}  # 缓存已创建的 logger
    _file_handler = None  # 全局唯一的文件 handler
    _file_handler_key = None  # 当前文件 handler 对应的配置
    _access_logger = None  # 访问日志 logger
    _access_file_handler = None  # 全局唯一的访问日志文件 handler
    _access_file_handler_key = None  # 当前访问日志文件 handler 对应的配置
    _alarm_logger = None  # 告警日志 logger
    _alarm_file_handler = None  # 全局唯一的告警日志文件 handler
    _alarm_file_handler_key = None  # 当前告警日志文件 handler 对应的配置
    _console_handler = None  # 全局唯一的控制台 handler
    _formatter = None  # 全局唯一的格式化器
    _access_formatter = None  # 全局唯一的访问日志格式化器
    _alarm_formatter = None  # 全局唯一的告警日志格式化器

    @classmethod
    def _get_formatter(cls) -> logging.Formatter:
        """获取全局唯一的格式化器"""
        if cls._formatter is None:
            cls._formatter = PipeLogFormatter()
        return cls._formatter

    @classmethod
    def _get_access_formatter(cls) -> logging.Formatter:
        """获取全局唯一的访问日志格式化器。"""
        if cls._access_formatter is None:
            cls._access_formatter = AccessLogFormatter()
        return cls._access_formatter

    @classmethod
    def _get_alarm_formatter(cls) -> logging.Formatter:
        """获取全局唯一的告警日志格式化器。"""
        if cls._alarm_formatter is None:
            cls._alarm_formatter = AlarmLogFormatter()
        return cls._alarm_formatter

    @classmethod
    def _build_file_handler(cls, log_dir: str, log_file_name: str, backup_count: int) -> TimedRotatingFileHandler:
        """按当前配置创建文件 handler。"""
        resolved_dir = resolve_log_path(log_dir)
        os.makedirs(resolved_dir, exist_ok=True)
        log_file = os.path.join(resolved_dir, log_file_name)
        file_handler = TimedRotatingFileHandler(
            log_file,
            when='midnight',
            interval=1,
            backupCount=backup_count,
            encoding='utf-8',
            utc=False,
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(cls._get_formatter())
        return file_handler

    @classmethod
    def _get_file_handler(cls, log_dir: str, log_file_name: str, backup_count: int) -> TimedRotatingFileHandler:
        """
        获取全局唯一的文件 handler

        所有 logger 共享同一个 FileHandler，确保日志切割正常工作。
        若环境变量中的日志路径配置发生变化，则重新创建 handler。
        """
        resolved_dir = resolve_log_path(log_dir)
        handler_key = (resolved_dir, str(log_file_name or '').strip() or get_current_log_file(), int(backup_count))
        if cls._file_handler is not None and cls._file_handler_key == handler_key:
            return cls._file_handler
        if cls._file_handler is not None:
            try:
                cls._file_handler.close()
            except Exception:
                pass
        cls._file_handler = cls._build_file_handler(handler_key[0], handler_key[1], handler_key[2])
        cls._file_handler_key = handler_key
        return cls._file_handler

    @classmethod
    def _build_access_file_handler(cls, log_dir: str, log_file_name: str, backup_count: int) -> TimedRotatingFileHandler:
        """按当前配置创建访问日志文件 handler。"""
        handler = cls._build_file_handler(log_dir, log_file_name, backup_count)
        handler.setFormatter(cls._get_access_formatter())
        return handler

    @classmethod
    def _get_access_file_handler(cls, log_dir: str, log_file_name: str, backup_count: int) -> TimedRotatingFileHandler:
        """获取全局唯一的访问日志文件 handler。"""
        resolved_dir = resolve_log_path(log_dir)
        handler_key = (resolved_dir, str(log_file_name or '').strip() or get_current_access_log_file(), int(backup_count))
        if cls._access_file_handler is not None and cls._access_file_handler_key == handler_key:
            return cls._access_file_handler
        if cls._access_file_handler is not None:
            try:
                cls._access_file_handler.close()
            except Exception:
                pass
        cls._access_file_handler = cls._build_access_file_handler(handler_key[0], handler_key[1], handler_key[2])
        cls._access_file_handler_key = handler_key
        return cls._access_file_handler

    @classmethod
    def _build_alarm_file_handler(cls, log_dir: str, log_file_name: str, backup_count: int) -> TimedRotatingFileHandler:
        """按当前配置创建告警日志文件 handler。"""
        handler = cls._build_file_handler(log_dir, log_file_name, backup_count)
        handler.setFormatter(cls._get_alarm_formatter())
        return handler

    @classmethod
    def _get_alarm_file_handler(cls, log_dir: str, log_file_name: str, backup_count: int) -> TimedRotatingFileHandler:
        """获取全局唯一的告警日志文件 handler。"""
        resolved_dir = resolve_log_path(log_dir)
        handler_key = (resolved_dir, str(log_file_name or '').strip() or get_current_alarm_log_file(), int(backup_count))
        if cls._alarm_file_handler is not None and cls._alarm_file_handler_key == handler_key:
            return cls._alarm_file_handler
        if cls._alarm_file_handler is not None:
            try:
                cls._alarm_file_handler.close()
            except Exception:
                pass
        cls._alarm_file_handler = cls._build_alarm_file_handler(handler_key[0], handler_key[1], handler_key[2])
        cls._alarm_file_handler_key = handler_key
        return cls._alarm_file_handler

    @classmethod
    def _get_console_handler(cls) -> logging.StreamHandler:
        """
        获取全局唯一的控制台 handler

        所有 logger 共享同一个 ConsoleHandler
        """
        if cls._console_handler is None:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.DEBUG)
            console_handler.setFormatter(cls._get_formatter())
            cls._console_handler = console_handler

        return cls._console_handler

    @classmethod
    def get_logger(
        cls,
        name: str,
        log_level: str = "INFO",
        log_dir: str = "logs",
        log_file_name: str = "",
        backup_count: int = 30,
        console_output: bool = True,
        file_output: bool = True
    ) -> logging.Logger:
        """
        获取或创建 logger

        Args:
            name: logger 名称（通常使用 __name__）
            log_level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
            log_dir: 日志文件目录
            log_file_name: 日志文件名
            backup_count: 保留的日志文件数量（天数）
            console_output: 是否输出到控制台
            file_output: 是否输出到文件

        Returns:
            logging.Logger: 配置好的 logger 实例
        """
        if name in cls._loggers:
            return cls._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        logger.propagate = False

        if logger.handlers:
            cls._loggers[name] = logger
            return logger

        if console_output:
            logger.addHandler(cls._get_console_handler())

        if file_output:
            logger.addHandler(cls._get_file_handler(log_dir, log_file_name, backup_count))

        cls._loggers[name] = logger
        return logger

    @classmethod
    def get_access_logger(
        cls,
        name: str = "access",
        log_level: str = "INFO",
        log_dir: str = "logs",
        log_file_name: str = "",
        backup_count: int = 30,
    ) -> logging.Logger:
        """获取访问日志 logger。"""
        if cls._access_logger is not None:
            return cls._access_logger

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        logger.propagate = False
        logger.handlers.clear()
        logger.addHandler(cls._get_access_file_handler(log_dir, log_file_name, backup_count))
        cls._access_logger = logger
        return logger

    @classmethod
    def get_alarm_logger(
        cls,
        name: str = "alarm",
        log_level: str = "WARNING",
        log_dir: str = "logs",
        log_file_name: str = "",
        backup_count: int = 30,
    ) -> logging.Logger:
        """获取告警日志 logger。"""
        if cls._alarm_logger is not None:
            return cls._alarm_logger

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, log_level.upper(), logging.WARNING))
        logger.propagate = False
        logger.handlers.clear()
        logger.addHandler(cls._get_alarm_file_handler(log_dir, log_file_name, backup_count))
        cls._alarm_logger = logger
        return logger

    @classmethod
    def set_level(cls, name: str, level: str):
        """
        设置指定 logger 的日志级别

        Args:
            name: logger 名称
            level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        """
        if name in cls._loggers:
            logger = cls._loggers[name]
            logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    @classmethod
    def set_global_level(cls, level: str):
        """
        设置所有 logger 的日志级别

        Args:
            level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
        """
        log_level = getattr(logging, level.upper(), logging.INFO)
        for logger in cls._loggers.values():
            logger.setLevel(log_level)


def get_logger(name: str, log_level: Optional[str] = None, console_output: Optional[bool] = None) -> logging.Logger:
    """
    获取 logger 的便捷函数

    Args:
        name: logger 名称（建议使用 __name__）
        log_level: 日志级别（可选，默认从环境变量 LOG_LEVEL 读取）
        console_output: 是否输出到控制台（可选，默认从环境变量 LOG_CONSOLE 读取）

    Returns:
        logging.Logger: 配置好的 logger 实例
    """
    level = log_level or get_current_log_level()
    console = console_output if console_output is not None else get_current_console_output()

    if level.upper() == "DEBUG":
        console = True

    return LoggerManager.get_logger(
        name=name,
        log_level=level,
        log_dir=get_current_log_dir(),
        log_file_name=get_current_log_file(),
        backup_count=DEFAULT_BACKUP_COUNT,
        console_output=console,
    )


def get_access_logger(name: str = "access", log_level: Optional[str] = None) -> logging.Logger:
    """获取访问日志 logger。"""
    return LoggerManager.get_access_logger(
        name=name,
        log_level=log_level or "INFO",
        log_dir=get_current_log_dir(),
        log_file_name=get_current_access_log_file(),
        backup_count=DEFAULT_BACKUP_COUNT,
    )


def get_alarm_logger(name: str = "alarm", log_level: Optional[str] = None) -> logging.Logger:
    """获取告警日志 logger。"""
    return LoggerManager.get_alarm_logger(
        name=name,
        log_level=log_level or "WARNING",
        log_dir=get_current_log_dir(),
        log_file_name=get_current_alarm_log_file(),
        backup_count=DEFAULT_BACKUP_COUNT,
    )


__all__ = [
    'get_logger',
    'get_access_logger',
    'get_alarm_logger',
    'get_current_log_dir',
    'get_current_log_file',
    'get_current_access_log_file',
    'get_current_alarm_log_file',
    'get_current_log_app_name',
    'get_current_log_idc',
    'resolve_log_path',
    'set_log_context',
    'clear_log_context',
]
