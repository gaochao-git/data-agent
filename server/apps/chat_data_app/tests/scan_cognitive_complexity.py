"""按 Sonar S3776（认知复杂度）口径扫描函数复杂度——1:1 移植 sonar-python 官方实现。

用法（server/ 目录下）::

    python apps/chat_data_app/tests/scan_cognitive_complexity.py
    python apps/chat_data_app/tests/scan_cognitive_complexity.py --threshold 15 --top 40
    python apps/chat_data_app/tests/scan_cognitive_complexity.py --target apps/chat_data_app/services/sql_run
    python apps/chat_data_app/tests/scan_cognitive_complexity.py --explain _run_subagent_with_events
    python apps/chat_data_app/tests/scan_cognitive_complexity.py --baseline ../scripts/checks/baselines/cognitive_complexity.txt

口径来源：SonarSource/sonar-python 仓库（SSAL v1）
``python-frontend/src/main/java/org/sonar/python/metrics/CognitiveComplexityVisitor.java``，
逐条移植其计分规则。校准实证（10-08）：``get_executor``（executor.py，改动前版本）与
``_build_builder_filters_and_having``（semantic_ai_query_adapter.py，改动前版本）修复
前后均为 本地 15→16，与 SonarQube 面板实测 16 逐分一致；布尔链 flatten 语义与 Java
flattenOperators 严格对齐（只沿链展开，不进其他子表达式）。

计分规则（与 Java 实现一一对应）：
- if +1+嵌套；elif 恒 +1（不带嵌套罚）；else 恒 +1；
- for/while +1+嵌套；except +1+嵌套；三元 +1+嵌套（且其内部嵌套再 +1）；
- 布尔链按「运算符类型切换」计：``a and b and c``=+1，``a and b or c``=+2；
- 嵌套层按语句块的父节点类型：if/for/while/except/match-case/else 体内 +1，
  try/finally/def/class/with 体内不加；嵌套函数 level+1（wrapper 函数除外）、class 归零；
- 推导式的 for/if 子句不计分（官方 visitor 无此逻辑），但其中的表达式照常扫；
- match/lambda/assert/raise 本身不计分。
背景：AGENTS.md §3 复杂度红线「≤15」即 SonarQube python:S3776。历史教训
（09-22）：早期 vendored 第三方复刻算法系统性低估（漏计条件内 BoolOp、推导式、
elif 罚等），报 0 超标而 Sonar 实测超标——本地结论必须以与本实现/Sonar 对齐为准。
"""

import argparse
import ast
import sys
from pathlib import Path


class CognitiveComplexityVisitor:
    """移植自 sonar-python ``CognitiveComplexityVisitor``（Java），语义逐条对应。"""

    def __init__(self) -> None:
        self.complexity = 0
        self.breakdown: list[tuple[int, str]] = []
        # 对应 Java 的 nestingLevelStack：entry = {"kind", "level", "node"}
        self.stack: list[dict] = [{"kind": None, "level": 0, "node": None}]
        # 对应 Java 的 alreadyConsideredOperators：已计过运算符的 BoolOp 节点
        self._consumed_boolops: set[int] = set()

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------
    def _level(self) -> int:
        return self.stack[-1]["level"]

    def _add(self, value: int, reason: str) -> None:
        self.complexity += value
        self.breakdown.append((value, reason))

    def _push(self, kind: str, level: int, node) -> None:
        self.stack.append({"kind": kind, "level": level, "node": node})

    # ------------------------------------------------------------------
    # 语句（对应 visitStatementList 的「父节点类型决定是否加嵌套」）
    # ------------------------------------------------------------------
    def scan_stmts(self, stmts, increments_nesting: bool) -> None:
        if increments_nesting:
            self.stack[-1]["level"] += 1
        for stmt in stmts:
            self.scan_stmt(stmt)
        if increments_nesting:
            self.stack[-1]["level"] -= 1

    def scan_stmt(self, node) -> None:
        if isinstance(node, ast.If):
            self._scan_if(node)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._add(1 + self._level(), "for")
            self.scan_expr(node.iter)  # 迭代对象里的表达式照常计分
            self.scan_stmts(node.body, True)
            if node.orelse:
                self._add(1, "for-else")
                self.scan_stmts(node.orelse, True)
        elif isinstance(node, ast.While):
            self._add(1 + self._level(), "while")
            self.scan_expr(node.test)
            self.scan_stmts(node.body, True)
            if node.orelse:
                self._add(1, "while-else")
                self.scan_stmts(node.orelse, True)
        elif isinstance(node, ast.Try) or (hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)):
            self.scan_stmts(node.body, False)  # TRY_STMT 体内不加嵌套
            for handler in node.handlers:
                self._add(1 + self._level(), "except")
                self.scan_stmts(handler.body, True)
            if node.orelse:
                self._add(1, "try-else")
                self.scan_stmts(node.orelse, True)
            self.scan_stmts(node.finalbody, False)  # FINALLY_CLAUSE 不加嵌套
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            self.scan_stmts(node.body, False)  # WITH_STMT 不加嵌套
        elif isinstance(node, ast.Match):
            for case in node.cases:  # visitor 无 match 计分，仅 case 体内加嵌套
                self.scan_expr(case.guard)
                self.scan_stmts(case.body, True)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._scan_nested_function(node)
        elif isinstance(node, ast.ClassDef):
            self._push("class", 0, node)  # CLASSDEF 归零（visitClassDef）
            self.scan_stmts(node.body, False)
            self.stack.pop()
        else:
            self.scan_expr(node)

    def _scan_if(self, node: ast.If, is_elif: bool = False) -> None:
        # visitIfStatement：elif 恒 +1（incrementWithoutNesting）；if +1+嵌套
        if is_elif:
            self._add(1, "elif")
        else:
            self._add(1 + self._level(), "if")
        self.scan_expr(node.test)  # 条件里的 BoolOp/三元照常计分
        self.scan_stmts(node.body, True)
        orelse = node.orelse
        if not orelse:
            return
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            self._scan_if(orelse[0], is_elif=True)
        else:
            self._add(1, "else")  # visitElseClause：恒 +1
            self.scan_stmts(orelse, True)

    def _scan_nested_function(self, node) -> None:
        # visitFunctionDef：嵌套函数 level = 父 level+1；wrapper 函数继承；class/根下属归零
        parent = self.stack[-1]
        if parent["kind"] == "func":
            level = parent["level"] if _is_wrapper(parent["node"], node) else parent["level"] + 1
        else:
            level = 0
        self._push("func", level, node)
        for decorator in node.decorator_list:
            self.scan_expr(decorator)
        self._scan_signature(node)
        self.scan_stmts(node.body, False)  # FUNCDEF 体内不加嵌套
        self.stack.pop()

    def _scan_signature(self, node) -> None:
        args = node.args
        for expr in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
            self.scan_expr(expr)
        for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            self.scan_expr(arg.annotation)
        self.scan_expr(node.returns)

    # ------------------------------------------------------------------
    # 表达式（visitConditionalExpression / visitBinaryExpression）
    # ------------------------------------------------------------------
    def scan_expr(self, node) -> None:
        if node is None:
            return
        if isinstance(node, ast.BoolOp):
            # visitBinaryExpression：先按链计运算符，再 super 下钻操作数
            # （三元等结构照常计分；已消化的嵌套 BoolOp 靠 _consumed_boolops 防重）
            if id(node) not in self._consumed_boolops:
                self._scan_boolop(node)
            for value in node.values:
                self.scan_expr(value)
            return
        if isinstance(node, ast.IfExp):
            self._add(1 + self._level(), "ternary")
            self.stack[-1]["level"] += 1  # 三元内部嵌套再 +1
            self.scan_expr(node.test)
            self.scan_expr(node.body)
            self.scan_expr(node.orelse)
            self.stack[-1]["level"] -= 1
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self.scan_stmt(child)
            else:
                self.scan_expr(child)

    def _scan_boolop(self, node: ast.BoolOp) -> None:
        operators: list[str] = []
        self._flatten_ops(node, operators)
        previous = None
        for op in operators:
            if previous is None or op != previous:
                self._add(1, f"bool:{op}")
            previous = op

    def _flatten_ops(self, node, operators: list[str]) -> None:
        # 对应 Java flattenOperators：只沿本布尔链的 BoolOp 操作数展开（左/右操作数
        # 本身是 AND/OR 才递归，含括号嵌套的同链 bool），绝不下钻其他子表达式——
        # call 参数等内部嵌套的同类型链是独立序列，由后续 scan_expr 访问另行计分
        # （Java 用运算符 Token 集合防重，此处用 _consumed_boolops 等价防重）。
        # 10-08 教训：旧实现无差别 iter_child_nodes 下钻 + 后序追加，会把
        # ``str(x or "d").strip().lower() or "d"`` 的内外两条 or 链合并成一条，
        # 恰好低估 1 分（get_executor 15 vs Sonar 16 实证）。
        if isinstance(node, ast.BoolOp):
            self._consumed_boolops.add(id(node))
            for value in node.values:
                if isinstance(value, ast.BoolOp):
                    self._flatten_ops(value, operators)
            operators.append(type(node.op).__name__)


def _is_wrapper(parent_func, child_func) -> bool:
    """对应 NestingLevel.isWrapperFunction：父函数体内除子函数外全是简单 return <名>。"""
    return all(stmt is child_func or _is_simple_return(stmt) for stmt in parent_func.body)


def _is_simple_return(stmt) -> bool:
    return isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Name)


def cognitive_complexity(funcdef) -> int:
    visitor = CognitiveComplexityVisitor()
    visitor._push("func", 0, funcdef)
    for decorator in funcdef.decorator_list:
        visitor.scan_expr(decorator)
    visitor._scan_signature(funcdef)
    visitor.scan_stmts(funcdef.body, False)
    return visitor.complexity


def explain_function(funcdef) -> str:
    """按 Sonar 面板格式输出计分明细：+N 或 +N (incl M for nesting)。"""
    visitor = CognitiveComplexityVisitor()
    visitor._push("func", 0, funcdef)
    visitor.scan_stmts(funcdef.body, False)
    lines = []
    for value, reason in visitor.breakdown:
        text = f"+{value}" if value == 1 else f"+{value} (incl {value - 1} for nesting)"
        lines.append(f"  [{reason:<12}] {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 扫描入口
# ---------------------------------------------------------------------------

DEFAULT_EXCLUDE_DIRS = {"tests", "__pycache__", "code_no_use_bak"}


def _collect_functions(root: Path, exclude_dirs):
    """产出 (score, file, lineno, name, funcdef)；只统计最外层函数（嵌套并入外层，Sonar 同口径）。"""
    results = []
    for py in sorted(root.rglob("*.py")):
        if any(part in exclude_dirs for part in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            print(f"[跳过] 语法解析失败: {py}")
            continue

        def walk(node, in_function, py=py):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not in_function:
                        results.append(
                            (
                                cognitive_complexity(child),
                                str(py.relative_to(root)),
                                child.lineno,
                                child.name,
                                child,
                            )
                        )
                    walk(child, True)
                else:
                    walk(child, in_function)

        walk(tree, False)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    script_dir = Path(__file__).resolve().parent
    parser.add_argument("--threshold", type=int, default=15, help="超标阈值（默认 15，AGENTS.md 红线）")
    parser.add_argument("--top", type=int, default=20, help="显示前 N 个最高分")
    parser.add_argument("--target", type=str, default=str(script_dir.parent), help="扫描目标目录")
    parser.add_argument("--explain", type=str, default=None, help="输出指定函数的计分明细（对照 Sonar 面板）")
    parser.add_argument(
        "--baseline",
        type=str,
        default=None,
        help="豁免清单文件，每行「相对路径:函数名」；baseline 外超标时退出码 1",
    )
    args = parser.parse_args()

    root = Path(args.target).resolve()
    results = _collect_functions(root, DEFAULT_EXCLUDE_DIRS)

    if args.explain:
        hits = [(s, f, ln, n, fd) for s, f, ln, n, fd in results if n == args.explain]
        if not hits:
            print(f"未找到函数: {args.explain}")
            return
        for _, f, ln, n, fd in hits:
            print(f"{f}:{ln} {n}")
            print(explain_function(fd))
        return

    over = [r for r in results if r[0] > args.threshold]
    baseline_keys: set[str] = set()
    if args.baseline and Path(args.baseline).is_file():
        baseline_keys = {
            line.strip()
            for line in Path(args.baseline).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
    print(f"目标: {root}")
    print(f"函数总数: {len(results)}，超阈值(>{args.threshold})的: {len(over)} 个")
    unresolved = 0
    if over:
        print("\n  分数  位置（全部超标项）")
        for score, f, ln, name, _ in over:
            exempt = f"{f}:{name}" in baseline_keys
            if not exempt:
                unresolved += 1
            print(f"  {score:3d}  {f}:{ln} {name}" + ("  [baseline 豁免]" if exempt else ""))
    if unresolved:
        print(f"\nbaseline 外超标: {unresolved} 个，需修复或登记 baseline 后再提交")
        sys.exit(1)
    results.sort(key=lambda x: -x[0])
    print(f"\n-- Top {args.top} --")
    for score, f, ln, name, _ in results[: args.top]:
        print(f"  {score:3d}  {f}:{ln} {name}")


if __name__ == "__main__":
    main()
