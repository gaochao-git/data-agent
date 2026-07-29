import os
import shutil
from pathlib import Path


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
    shutil.rmtree(target_root, ignore_errors=True)
    shutil.copytree(source_root, target_root)


_prepare_default_system_skills()
