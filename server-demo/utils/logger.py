"""
日志管理模块
提供统一的日志配置和管理功能
支持控制台输出和文件记录，自动日志轮转
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from typing import Optional


DEFAULT_BACKUP_COUNT = 30  # 保留30天


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
    return _env_text("LOG_FILE", "fxcjg_app.log")


def get_current_console_output() -> bool:
    """读取是否输出控制台日志。"""
    return _env_bool("LOG_CONSOLE", True)


def resolve_log_path(path: str) -> str:
    """将日志路径规范为绝对路径。"""
    raw = str(path or "").strip()
    if not raw:
        return os.path.abspath("logs")
    if os.path.isabs(raw):
        return raw
    return os.path.abspath(raw)


class LoggerManager:
    """日志管理器"""

    _loggers = {}  # 缓存已创建的 logger
    _file_handler = None  # 全局唯一的文件 handler
    _file_handler_key = None  # 当前文件 handler 对应的配置
    _console_handler = None  # 全局唯一的控制台 handler
    _formatter = None  # 全局唯一的格式化器

    @classmethod
    def _get_formatter(cls) -> logging.Formatter:
        """获取全局唯一的格式化器"""
        if cls._formatter is None:
            cls._formatter = logging.Formatter(
                '[%(asctime)s] [%(levelname)s] [%(name)s] [%(module)s.%(funcName)s:%(lineno)d] - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
        return cls._formatter

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
        handler_key = (resolved_dir, str(log_file_name or '').strip() or 'fxcjg_app.log', int(backup_count))
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
        log_file_name: str = "fxcjg_app.log",
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


__all__ = [
    'get_logger',
    'get_current_log_dir',
    'get_current_log_file',
    'resolve_log_path',
]
