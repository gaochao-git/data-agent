"""扫描单文件行数——AGENTS.md 红线「后端单文件 ≤2000 行，前端单文件 ≤3000 行」。

用法（仓库任意目录）::

    python scripts/checks/file_lines.py                     # 全量扫描，baseline 外超标退出码 1
    python scripts/checks/file_lines.py --top 30
    python scripts/checks/file_lines.py --target web-vue/src --limit 3000

口径：按物理行数（含空行与注释，与 wc -l 一致）。后端扫 server/ 下 *.py，
前端扫 web-vue/src/ 下 *.vue/*.ts/*.tsx。改动量最大的先拆文件再拆函数——
文件超行是函数超行与职责混杂的先行指标。

baseline：``--baseline`` 指定豁免清单文件（缺省取同目录 baselines/file_lines.txt），
每行「相对仓库根的路径」，# 开头为注释；baseline 外超标退出码 1——存量债务
只能收敛不能增长：新文件不允许登记，拆掉一处就删一行。
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# (扫描根, 扩展名, 行数上限, 显示名)
DEFAULT_RULES = [
    (REPO_ROOT / "server", {".py"}, 2000, "后端"),
    (REPO_ROOT / "web-vue" / "src", {".vue", ".ts", ".tsx"}, 3000, "前端"),
]
DEFAULT_EXCLUDE_DIRS = {"__pycache__", "venv", ".venv", "node_modules", "dist", "coverage"}


def _scan(base: Path, exts: set[str], limit: int) -> list[tuple[int, Path]]:
    """返回 (行数, 相对仓库根路径)，仅行数 > limit 的文件。"""
    hits: list[tuple[int, Path]] = []
    if not base.is_dir():
        return hits
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in exts:
            continue
        if DEFAULT_EXCLUDE_DIRS & set(path.parts):
            continue
        try:
            lines = sum(1 for _ in open(path, encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if lines > limit:
            hits.append((lines, path.relative_to(REPO_ROOT)))
    return hits


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=20, help="显示前 N 个最大文件")
    parser.add_argument("--target", type=str, default=None, help="只扫该目录（相对仓库根），上限取 --limit")
    parser.add_argument("--limit", type=int, default=None, help="配合 --target 指定行数上限")
    parser.add_argument(
        "--baseline",
        type=str,
        default=str(script_dir / "baselines" / "file_lines.txt"),
        help="豁免清单文件，每行「相对仓库根路径」；传空字符串禁用",
    )
    args = parser.parse_args()

    if args.target:
        base = (REPO_ROOT / args.target).resolve()
        limit = args.limit or 2000
        rules = [(base, {".py", ".vue", ".ts", ".tsx"}, limit, "指定目标")]
    else:
        rules = DEFAULT_RULES

    over: list[tuple[int, Path, int, str]] = []  # (行数, 路径, 上限, 显示名)
    for base, exts, limit, label in rules:
        for lines, rel in _scan(base, exts, limit):
            over.append((lines, rel, limit, label))

    baseline_path = Path(args.baseline) if args.baseline else None
    baseline_keys: set[str] = set()
    if baseline_path and baseline_path.is_file():
        baseline_keys = {
            line.strip()
            for line in baseline_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    print(f"单文件超行: {len(over)} 个（baseline 豁免 {sum(1 for _, r, _, _ in over if str(r) in baseline_keys)} 个）")
    unresolved = 0
    if over:
        print("\n  行数  位置（全部超标项）")
        for lines, rel, limit, label in sorted(over, key=lambda x: -x[0]):
            exempt = str(rel) in baseline_keys
            if not exempt:
                unresolved += 1
            print(f"  {lines:5d}  {rel}（{label}上限 {limit}）" + ("  [baseline 豁免]" if exempt else ""))
    if unresolved:
        print(f"\nbaseline 外超标: {unresolved} 个，需拆分或登记 baseline 后再提交")
        sys.exit(1)
    print("\n-- Top %d --" % args.top)
    shown = 0
    for base, exts, limit, label in rules:
        ranked = sorted(_scan(base, exts, 0), key=lambda x: -x[0])  # limit=0 即全量排名
        for lines, rel in ranked[: args.top]:
            print(f"  {lines:5d}  {rel}")
            shown += 1
    if shown == 0:
        print("  （无文件）")


if __name__ == "__main__":
    main()
