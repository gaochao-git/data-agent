import os
from pathlib import Path

# 必须先于 aiosqlite / sqlalchemy 导入执行：用 pysqlite3 替换 sqlite3，
# 覆盖旧编译 Python 上 deterministic=True 报错的场景（与 main.py 同一入口）。
from utils.sqlite_compat import apply_sqlite_compat

apply_sqlite_compat()

_DEFAULT_CHAT_DATA_HOME = Path("/tmp/fxcjg_test_data/chat_data")
os.environ.setdefault("CHAT_DATA_HOME", str(_DEFAULT_CHAT_DATA_HOME))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "test")
