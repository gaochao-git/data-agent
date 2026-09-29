"""agent harness 边界约束（棘轮：只许收紧，不许放松）。

- agent/context 子系统不得依赖业务面（semantic/sql_run/dashboard/datasource/daos）。
- agent/ 对 services.agent_run 的依赖边数只许下降；每搬走一块就同步下调上限。
"""

import re
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
CONTEXT_DIR = AGENT_DIR / "context"

# agent/context 禁止出现的业务面标记
CONTEXT_FORBIDDEN_MARKERS = (
    "services.semantic",
    "services.sql_run",
    "services.dashboard",
    "services.datasource",
    "daos.",
)

# agent/ 对 services.agent_run 依赖边的当前上限
# （2026-08：context_budget 迁出后 16；run 协调组迁出后 15；workspace 机制层迁出后 12）
AGENT_RUN_EDGE_CEILING = 12

# services/ 反向依赖 agent/ 的边数上限（含懒加载）。
# 2026-08：skill_service 迁出后 6；conversation_event_service 迁出后 5；
# workspace 机制层迁入 agent/workspace 后，留守门面（conversation_workspace/双 backend）
# 反向引用机制属"业务消费 harness"的合法方向，第三刀门面迁入后大幅塌缩。
SERVICES_TO_AGENT_EDGE_CEILING = 12

_IMPORT_LINE_RE = re.compile(r"^\s*(from|import)\s", re.MULTILINE)


def _python_sources(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, path.read_text(encoding="utf-8")


def test_context_subsystem_has_no_business_plane_deps():
    violations = []
    for path, text in _python_sources(CONTEXT_DIR):
        for marker in CONTEXT_FORBIDDEN_MARKERS:
            if marker in text:
                violations.append(f"{path.relative_to(AGENT_DIR)}: {marker}")
    assert violations == [], f"context 子系统出现业务面依赖: {violations}"


def test_agent_run_edge_count_only_shrinks():
    count = 0
    for path, text in _python_sources(AGENT_DIR):
        if CONTEXT_DIR in path.parents:
            continue
        for line in text.splitlines():
            if _IMPORT_LINE_RE.match(line) and "services.agent_run" in line:
                count += 1
    assert count <= AGENT_RUN_EDGE_CEILING, (
        f"agent/ 对 services.agent_run 的依赖边数 {count} 超过上限 "
        f"{AGENT_RUN_EDGE_CEILING}；只许通过搬迁下调上限，不许新增依赖"
    )


def test_services_reverse_deps_on_agent_only_shrink():
    services_dir = AGENT_DIR.parent / "services"
    agent_pkg_markers = ("agent.skills", "agent.prompts", "agent.run", "agent.context", "agent.workspace")
    count = 0
    for _path, text in _python_sources(services_dir):
        for line in text.splitlines():
            if not _IMPORT_LINE_RE.match(line):
                continue
            targets_agent = any(marker in line for marker in agent_pkg_markers) or bool(
                re.search(r"from\s+\.+agent\.", line)
            )
            if targets_agent:
                count += 1
    assert count <= SERVICES_TO_AGENT_EDGE_CEILING, (
        f"services/ 反向依赖 agent/ 的边数 {count} 超过上限 "
        f"{SERVICES_TO_AGENT_EDGE_CEILING}；只许通过搬迁下调上限，不许新增依赖"
    )
