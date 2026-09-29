"""SQLite 兼容 shim：用 pysqlite3 替换标准库 sqlite3。

解决旧编译 Python（编译期 sqlite3.h < 3.8.3）上
``create_function(deterministic=True)`` 报 NotSupportedError 的问题。
pysqlite3-binary 自带新版 SQLite C 库，替换后 aiosqlite / sqlalchemy
底层均使用新版实现。

必须在任何 aiosqlite / sqlalchemy / sqlite3 导入之前调用
:func:`apply_sqlite_compat`（main.py 与 conftest.py 顶部各接一次）。
"""

import logging
import sys

logger = logging.getLogger(__name__)

_applied = False
_attempted = False


def apply_sqlite_compat() -> bool:
    """用 pysqlite3 替换 sys.modules 中的 sqlite3。

    返回是否实际发生了替换。macOS 等无法安装 pysqlite3-binary 的平台
    会静默保留系统自带 sqlite3；其他平台缺失时记 warning，便于在
    日志中发现内网环境漏装，而不是等测试报错再排查。
    """
    global _applied, _attempted
    if _applied:
        return True
    if _attempted:
        return False
    _attempted = True
    try:
        import pysqlite3

        sys.modules["sqlite3"] = pysqlite3
        _applied = True
        return True
    except ImportError:
        logger.warning(
            "pysqlite3 未安装，继续使用系统自带 sqlite3；"
            "若部署环境为旧编译 Python，运行时可能触发 "
            "deterministic=True requires SQLite 3.8.3 or higher"
        )
        return False
