import fcntl
import os
import shutil
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


def _prepare_default_system_skills() -> None:
    """Keep the default unit-test data root aligned with deployed system skills."""
    default_home = _DEFAULT_CHAT_DATA_HOME.expanduser().resolve()
    if Path(os.environ["CHAT_DATA_HOME"]).expanduser().resolve() != default_home:
        return
    source_root = Path(__file__).resolve().parent / "apps/chat_data_app/agent/skills"
    target_root = default_home / "skills/system"
    target_root.parent.mkdir(parents=True, exist_ok=True)
    # pytest-xdist 各 worker 的 conftest 会同时执行；不加锁会在 rmtree/copytree 间互相踩踏。
    with (default_home / ".system-skills.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        shutil.rmtree(target_root, ignore_errors=True)
        shutil.copytree(source_root, target_root)


_prepare_default_system_skills()
