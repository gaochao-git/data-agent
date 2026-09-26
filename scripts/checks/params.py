"""扫描函数参数个数——AGENTS.md §3 红线「单个方法的参数不能大于 13」。

用法（仓库任意目录）::

    python scripts/checks/params.py                # 全量扫描，baseline 外超标退出码 1
    python scripts/checks/params.py --threshold 13 --top 30
    python scripts/checks/params.py --target server/apps/chat_data_app

口径：ast 解析，统计 FunctionDef/AsyncFunctionDef 的形参总数（含位置、仅位置、
仅关键字、*args/**kwargs），self/cls 不计。lambda 不计（无名字可登记，且长度
天然受限）。嵌套函数单独计分（各自是独立调用面）。

baseline：``--baseline`` 指定豁免清单文件（缺省取同目录 baselines/params.txt），
每行「相对路径:函数名」，# 开头为注释；baseline 外超标退出码 1——存量债务只能
收敛不能增长。
"""
import argparse
import ast
import sys
from pathlib import Path

THRESHOLD_DEFAULT = 13
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TARGETS = [REPO_ROOT / "server" / "apps" / "chat_data_app", REPO_ROOT / "server" / "utils"]
DEFAULT_EXCLUDE_DIRS = {"__pycache__", "venv", "node_modules", "tests"}


def _param_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    a = node.args
    names = [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return len(names)


def _collect(target: Path, exclude: set[str]) -> list[tuple[int, str, int, str]]:
    """返回 (参数数, 相对路径, 行号, 函数名)，路径相对扫描目标目录。"""
    hits: list[tuple[int, str, int, str]] = []
    if not target.is_dir():
        return hits
    for path in sorted(target.rglob("*.py")):
        if exclude & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = path.relative_to(target).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                count = _param_count(node)
                if count > 0:
                    hits.append((count, rel, node.lineno, node.name))
    return hits


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--threshold", type=int, default=THRESHOLD_DEFAULT, help="超标阈值（默认 13，AGENTS.md 红线）")
    parser.add_argument("--top", type=int, default=20, help="显示前 N 个最高参数数")
    parser.add_argument("--target", type=str, default=None, help="扫描目标目录（缺省 apps/chat_data_app 与 utils）")
    parser.add_argument(
        "--baseline",
        type=str,
        default=str(script_dir / "baselines" / "params.txt"),
        help="豁免清单文件，每行「相对路径:函数名」；传空字符串禁用",
    )
    args = parser.parse_args()

    targets = [Path(args.target).resolve()] if args.target else DEFAULT_TARGETS
    over: list[tuple[int, str, int, str]] = []
    total = 0
    for target in targets:
        hits = _collect(target, DEFAULT_EXCLUDE_DIRS)
        total += len(hits)
        over.extend((c, f"{target.name}/{rel}", ln, n) for c, rel, ln, n in hits if c > args.threshold)

    baseline_path = Path(args.baseline) if args.baseline else None
    baseline_keys: set[str] = set()
    if baseline_path and baseline_path.is_file():
        baseline_keys = {
            line.strip()
            for line in baseline_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }

    print(f"函数总数: {total}，参数超阈值(>{args.threshold})的: {len(over)} 个")
    unresolved = 0
    if over:
        print("\n  参数  位置（全部超标项）")
        for count, f, ln, name in sorted(over, key=lambda x: -x[0]):
            exempt = f"{f}:{name}" in baseline_keys
            if not exempt:
                unresolved += 1
            print(f"  {count:3d}  {f}:{ln} {name}" + ("  [baseline 豁免]" if exempt else ""))
        over.sort(key=lambda x: -x[0])
    if unresolved:
        print(f"\nbaseline 外超标: {unresolved} 个，需修复或登记 baseline 后再提交")
        sys.exit(1)
    print("\n-- Top %d --" % args.top)
    for count, f, ln, name in over[: args.top]:
        print(f"  {count:3d}  {f}:{ln} {name}")


if __name__ == "__main__":
    main()
