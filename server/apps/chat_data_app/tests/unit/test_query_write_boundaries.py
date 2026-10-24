"""架构边界：查询层只走 QueryOrchestrator，写路径只走 WriteAdapter。"""

from __future__ import annotations

from pathlib import Path

SERVICES = Path(__file__).resolve().parents[4] / "apps/chat_data_app/services"
QUERY_RUNTIME_IMPORT = "sql_run.runtime"
FORBIDDEN_DB_IMPORTS = ("import pyhive", "from pyhive", "import pymysql", "from pymysql", "create_engine")


def _service_files(*parts: str) -> list[Path]:
    return [path for path in (SERVICES.joinpath(*parts)).rglob("*.py") if "__pycache__" not in path.parts]


def test_business_services_do_not_import_query_runtime_directly():
    offenders = []
    for directory in ("ontology", "dataset", "semantic"):
        for path in _service_files(directory):
            text = path.read_text(encoding="utf-8")
            if QUERY_RUNTIME_IMPORT in text and "orchestrator" not in str(path):
                offenders.append(str(path))
    assert not offenders, offenders


def test_only_write_adapter_imports_database_drivers():
    offenders = []
    for directory in ("ontology", "dataset", "query", "semantic"):
        for path in _service_files(directory):
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_DB_IMPORTS:
                if token in text:
                    offenders.append((str(path), token))
    # pymysql.converters.escape_string 是纯转义工具，不建立连接。
    offenders = [
        item
        for item in offenders
        if not (item[0].endswith("ontology/object_projection.py") and item[1] == "from pymysql")
    ]
    assert not offenders, offenders
