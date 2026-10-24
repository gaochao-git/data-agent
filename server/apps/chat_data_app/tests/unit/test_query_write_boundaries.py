"""架构边界：查询层只走 QueryOrchestrator，写路径只走 WriteAdapter。

用 AST 解析 import，避免相对导入、别名导入、文件改名绕过文本匹配。
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[4] / "apps/chat_data_app/services"
QUERY_ORCHESTRATOR = SERVICES / "query" / "orchestrator.py"
WRITE_ADAPTER_DIR = SERVICES / "write"


def _service_files(*parts: str) -> list[Path]:
    return [path for path in (SERVICES.joinpath(*parts)).rglob("*.py") if "__pycache__" not in path.parts]


def _resolve_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    base = path.parent
    for _ in range(node.level - 1):
        base = base.parent
    return ".".join([*base.parts, *(node.module or "").split(".")])


def _iter_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield _resolve_from(path, node), [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, [alias.name]


def test_business_services_do_not_import_query_runtime_directly():
    offenders = []
    for directory in ("dataset", "semantic"):
        for path in _service_files(directory):
            for module, names in _iter_imports(path):
                if module.startswith("sql_run.runtime") or any(name.startswith("sql_run.runtime") for name in names):
                    offenders.append(str(path))
    for path in _service_files("query"):
        if path == QUERY_ORCHESTRATOR:
            continue
        for module, names in _iter_imports(path):
            if module.startswith("sql_run.runtime") or any(name.startswith("sql_run.runtime") for name in names):
                offenders.append(str(path))
    assert not offenders, offenders


def test_only_write_adapter_imports_database_drivers():
    offenders = []
    for directory in ("dataset", "query", "semantic"):
        for path in _service_files(directory):
            for module, names in _iter_imports(path):
                if module.startswith(("pyhive", "pymysql")) or any(
                    name.startswith(("pyhive", "pymysql")) for name in names
                ):
                    offenders.append((str(path), module))
    for path in _service_files("write"):
        for module, _names in _iter_imports(path):
            if module.startswith(("pyhive", "pymysql")) and path != WRITE_ADAPTER_DIR / "hive.py":
                offenders.append((str(path), module))
    assert not offenders, offenders
