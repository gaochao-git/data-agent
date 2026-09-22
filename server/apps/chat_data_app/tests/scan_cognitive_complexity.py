"""按 Sonar S3776（认知复杂度）口径手动扫描函数复杂度。

用法（server/ 目录下）::

    python apps/chat_data_app/tests/scan_cognitive_complexity.py
    python apps/chat_data_app/tests/scan_cognitive_complexity.py --threshold 15 --top 40
    python apps/chat_data_app/tests/scan_cognitive_complexity.py --target apps/chat_data_app/services/sql_run

背景：AGENTS.md §3 的复杂度红线「不能大于 15」权威口径是 SonarQube 的
Cognitive Complexity（规则 python:S3776），它与 radon 圈复杂度是两种度量：
radon 数决策点（if/for/每个 and/or 各 +1），Sonar 按嵌套深度加罚、扁平
elif 链几乎不罚。radon 全绿不代表 Sonar 达标，本脚本用于本地复现 Sonar 口径。

算法内联自开源实现 cognitive-complexity 1.3.0（MIT License，© Melevir），
与 SonarQube 实际值可能有 ±1~2 的差异，用于定位超标函数；最终以 Sonar 为准。
"""
import argparse
import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# 认知复杂度算法（vendored from cognitive-complexity 1.3.0, MIT）
# ---------------------------------------------------------------------------

AnyFuncdef = "ast.FunctionDef | ast.AsyncFunctionDef"


def _has_recursive_calls(funcdef) -> bool:
    return bool([
        n for n in ast.walk(funcdef)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == funcdef.name
    ])


def _is_decorator(funcdef) -> bool:
    return (
        isinstance(funcdef, ast.FunctionDef)
        and len(funcdef.body) == 2
        and isinstance(funcdef.body[0], ast.FunctionDef)
        and isinstance(funcdef.body[1], ast.Return)
    )


def _process_control_flow_breaker(node, increment_by: int):
    """If/For/While/IfExp/ExceptHandler：按 Sonar 规则计分并加深嵌套层。"""
    if isinstance(node, ast.IfExp):
        # 三元表达式：不加分，但内部嵌套层 +1
        increment = 0
        increment_by += 1
    elif isinstance(node, ast.If) and len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        # elif 本身不重复计分（分数记在 If 上）
        increment = 0
    elif isinstance(node, ast.ExceptHandler):
        increment = 0
        increment_by += 1
    elif node.orelse:
        # 有 else：+1 且嵌套层 +1
        increment = 1
        increment_by += 1
    else:
        increment = 0
        increment_by += 1
    return increment_by, max(1, increment_by) + increment, True


def _process_node_itself(node, increment_by: int):
    control_flow_breakers = (ast.If, ast.For, ast.While, ast.IfExp, ast.ExceptHandler)
    incrementers_nodes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

    if isinstance(node, control_flow_breakers):
        return _process_control_flow_breaker(node, increment_by)
    if isinstance(node, incrementers_nodes):
        return increment_by + 1, 0, True
    if isinstance(node, ast.BoolOp):
        # BoolOp 序列：整个链 +N（N = 链内 and/or 总数）
        inner = len([n for n in ast.walk(node) if isinstance(n, ast.BoolOp)])
        return increment_by, inner, False
    return increment_by, 0, True


def _complexity_for_node(node: ast.AST, increment_by: int = 0) -> int:
    increment_by, base_complexity, should_iter_children = _process_node_itself(node, increment_by)
    child_complexity = 0
    if should_iter_children:
        for child in ast.iter_child_nodes(node):
            child_complexity += _complexity_for_node(child, increment_by)
    return base_complexity + child_complexity


def cognitive_complexity(funcdef) -> int:
    """计算单个函数的认知复杂度（含其内部嵌套函数/lambda）。"""
    if _is_decorator(funcdef):
        return cognitive_complexity(funcdef.body[0])
    score = sum(_complexity_for_node(node) for node in funcdef.body)
    if _has_recursive_calls(funcdef):
        score += 1
    return score


# ---------------------------------------------------------------------------
# 扫描入口
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDE_DIRS = {"tests", "__pycache__", "code_no_use_bak"}


def _collect_functions(root: Path, exclude_dirs):
    """产出 (score, file, lineno, name)；只统计最外层函数（Sonar 报告粒度）。"""
    results = []
    for py in sorted(root.rglob("*.py")):
        if any(part in exclude_dirs for part in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            print(f"[跳过] 语法解析失败: {py}")
            continue

        def walk(node, in_function):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not in_function:
                        results.append((
                            cognitive_complexity(child),
                            str(py.relative_to(root)),
                            child.lineno,
                            child.name,
                        ))
                    walk(child, True)
                else:
                    walk(child, in_function)

        walk(tree, False)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--target",
        type=Path,
        default=script_dir.parent,
        help="扫描目标目录（默认 apps/chat_data_app，排除 tests 等）",
    )
    parser.add_argument("--threshold", type=int, default=15, help="AGENTS.md 红线，默认 15")
    parser.add_argument("--top", type=int, default=0, help="只显示前 N 个（0=全部）")
    args = parser.parse_args()

    target = args.target.resolve()
    if not target.exists():
        raise SystemExit(f"目标目录不存在: {target}")

    results = _collect_functions(target, DEFAULT_EXCLUDE_DIRS)
    results.sort(key=lambda r: (-r[0], r[1], r[2]))
    over = [r for r in results if r[0] > args.threshold]
    shown = over if not args.top else over[: args.top]

    print(f"目标: {target}")
    print(f"函数总数: {len(results)}，超阈值(>{args.threshold})的: {len(over)} 个\n")
    if shown:
        print(f"{'分数':>4}  位置")
        for score, rel, lineno, name in shown:
            print(f"{score:>4}  {rel}:{lineno} {name}")
    else:
        print(f"全部函数 ≤ {args.threshold}，达标。")


if __name__ == "__main__":
    main()
