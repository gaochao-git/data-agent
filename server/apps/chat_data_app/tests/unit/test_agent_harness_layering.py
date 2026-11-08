"""Agent harness 子包依赖棘轮：只允许现有边，禁止新增反向依赖。

当前仍有历史包级环（mcp/plugins、workspace/skills 等），先以基线锁住；
``middleware -> run`` 已通过 InboxPort 断开，不允许回流。
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"

# 当前基线：允许存在的包级依赖边（只许减少，不许新增）。
KNOWN_EDGES = {
    ("(root)", "contracts"),
    ("(root)", "mcp"),
    ("(root)", "middleware"),
    ("(root)", "plugins"),
    ("(root)", "prompts"),
    ("(root)", "skills"),
    ("(root)", "tools"),
    ("(root)", "workspace"),
    ("mcp", "contracts"),
    ("middleware", "context"),
    ("middleware", "contracts"),
    ("middleware", "mcp"),
    ("middleware", "prompts"),
    ("middleware", "tools"),
    ("middleware", "workspace"),
    ("plugins", "contracts"),
    ("plugins", "mcp"),
    ("run", "contracts"),
    ("skills", "contracts"),
    # 运行技能源枚举包含插件提供的技能（skill_service._append_runtime_user_skill_source
    # 读取 plugins.loader 的启用清单）；方向 skills→plugins 为有意设计，反向禁止。
    ("skills", "plugins"),
    ("tools", "workspace"),
    ("workspace", "contracts"),
}


def _agent_packages() -> set[str]:
    return {path.name for path in AGENT_DIR.iterdir() if path.is_dir() and path.name != "__pycache__"}


def _imported_agent_packages(path: Path, package: str, packages: set[str]) -> set[str]:
    rel = path.relative_to(AGENT_DIR)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(rel.parts[:-1])
                up = node.level - 1
                if up:
                    base = base[:-up]
                if node.module:
                    base.extend(node.module.split("."))
                modules.append(".".join(base))
            elif node.module and node.module.startswith("apps.chat_data_app.agent"):
                modules.append(node.module.removeprefix("apps.chat_data_app.agent."))
        elif isinstance(node, ast.Import):
            modules.extend(
                alias.name.removeprefix("apps.chat_data_app.agent.")
                for alias in node.names
                if alias.name.startswith("apps.chat_data_app.agent.")
            )
        for module in modules:
            target = module.split(".")[0]
            if target in packages and target != package:
                imported.add(target)
    return imported


def _collect_edges() -> set[tuple[str, str]]:
    packages = _agent_packages()
    edges: set[tuple[str, str]] = set()
    for path in AGENT_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(AGENT_DIR)
        package = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        edges.update((package, target) for target in _imported_agent_packages(path, package, packages))
    return edges


def test_harness_package_edges_do_not_grow():
    edges = _collect_edges()
    unexpected = edges - KNOWN_EDGES
    assert not unexpected, f"新增 harness 包级依赖，需要先补 layering 设计: {sorted(unexpected)}"
    assert ("middleware", "run") not in edges, "middleware 不允许反向依赖 run；应通过 contracts/InboxPort"


def test_contracts_layer_has_no_harness_implementation_dependencies():
    contracts_dir = AGENT_DIR / "contracts"
    packages = _agent_packages() - {"contracts"}
    for path in contracts_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        assert not _imported_agent_packages(path, path.relative_to(AGENT_DIR).parts[0], packages), (
            f"contracts 只能放 Protocol/纯类型，不能依赖 harness 实现: {path}"
        )


def test_harness_package_graph_is_acyclic():
    graph: dict[str, set[str]] = {}
    for source, target in _collect_edges():
        graph.setdefault(source, set()).add(target)
        graph.setdefault(target, set())
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(node: str) -> None:
        if node in visited:
            return
        assert node not in visiting, f"harness 包级依赖成环: {node}"
        visiting.add(node)
        for target in graph.get(node, set()):
            _visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        _visit(node)


def test_explicit_port_configuration_is_idempotent():
    from apps.chat_data_app.agent.contracts.mcp_server_source import get_mcp_server_source
    from apps.chat_data_app.agent.contracts.skill_source import get_skill_snapshot_source, get_skill_source
    from apps.chat_data_app.agent.contracts.workspace_preview import get_workspace_preview_source
    from apps.chat_data_app.agent.port_config import configure_default_harness_ports

    configure_default_harness_ports()
    configure_default_harness_ports()
    assert get_mcp_server_source() is not None
    assert get_skill_source() is not None
    assert get_skill_snapshot_source() is not None
    assert get_workspace_preview_source() is not None
