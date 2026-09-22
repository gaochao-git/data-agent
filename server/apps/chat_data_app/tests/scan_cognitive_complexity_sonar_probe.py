"""临时探针：对齐 Sonar S3776 口径的认知复杂度扫描（校准锚点后全仓扫描）。"""
import ast
from pathlib import Path


def score_body(stmts, inc):
    return sum(score_stmt(s, inc) for s in stmts)


def score_stmt(node, inc):
    if isinstance(node, ast.If):
        total = 1 + inc + score_expr(node.test, inc)
        body = score_body(node.body, inc + 1)
        orelse = node.orelse
        if not orelse:
            return total + body
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            # elif：自身分由内层 If 承担（1+inc），链不加嵌套层
            return total + body + score_stmt(orelse[0], inc)
        # else：+1 且带嵌套罚
        return total + body + (1 + inc) + score_body(orelse, inc + 1)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return 1 + inc + score_expr(node.iter, inc) + score_body(node.body, inc + 1) + score_body(node.orelse, inc + 1)
    if isinstance(node, ast.While):
        return 1 + inc + score_expr(node.test, inc) + score_body(node.body, inc + 1) + score_body(node.orelse, inc + 1)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return 1 + score_body(node.body, inc)
    if isinstance(node, ast.Try) or (hasattr(ast, "TryStar") and isinstance(node, ast.TryStar)):
        h = sum((1 + inc + score_expr(n.type, inc)) if n.type else (1 + inc) for n in node.handlers)
        return h + score_body(node.body, inc) + score_body(node.orelse, inc) + score_body(node.finalbody, inc)
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return score_body(node.body, inc)
    if isinstance(node, ast.Match):
        return sum(1 + inc + score_body(c.body, inc + 1) for c in node.cases)
    return score_expr(node, inc)


def score_expr(node, inc):
    if node is None:
        return 0
    total = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.IfExp):
            total += 1 + score_expr(child.test, inc) + score_expr(child.body, inc) + score_expr(child.orelse, inc)
        elif isinstance(child, ast.BoolOp):
            total += len(child.values) - 1 + sum(score_expr(v, inc) for v in child.values)
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            total += comp_score(child, inc)
        elif isinstance(child, ast.Lambda):
            total += 1 + score_expr(child.body, inc)
        elif isinstance(child, ast.stmt):
            total += score_stmt(child, inc)
        else:
            total += score_expr(child, inc)
    return total


def comp_score(node, inc):
    total = 0
    for gen in node.generators:
        total += 1 + inc + sum(1 + inc + score_expr(i, inc) for i in gen.ifs)
        total += score_expr(gen.iter, inc)
    if isinstance(node, ast.DictComp):
        total += score_expr(node.key, inc + 1) + score_expr(node.value, inc + 1)
    else:
        total += score_expr(node.elt, inc + 1)
    return total


def complexity(funcdef):
    s = score_body(funcdef.body, 0)
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == funcdef.name
           for n in ast.walk(funcdef)):
        s += 1
    return s


if __name__ == "__main__":
    BASE = Path(__file__).resolve().parent.parent
    EXCLUDE = {"tests", "__pycache__", "code_no_use_bak"}

    # 锚点校准（SonarQube 实测值）
    anchors = [
        ("agent/workspace/officecli_adapter.py", "_officecli_file_indices", 17),
        ("agent/middleware/subagents.py", "_run_subagent_with_events", 21),
    ]
    for f, name, expect in anchors:
        tree = ast.parse((BASE / f).read_text())
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                print(f"锚点 {name}: 算法={complexity(n)} Sonar={expect}")

    results = []
    for py in sorted(BASE.rglob("*.py")):
        if any(p in EXCLUDE for p in py.parts):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        def walk(node, in_func, py=py):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not in_func:
                        results.append((complexity(ch), str(py.relative_to(BASE)), ch.lineno, ch.name))
                    walk(ch, True)
                else:
                    walk(ch, in_func)

        walk(tree, False)

    results.sort(key=lambda x: -x[0])
    over = [r for r in results if r[0] > 15]
    print(f"\n函数总数: {len(results)}, Sonar 口径 >15: {len(over)}")
    print("\n-- Top 30 --")
    for s, f, ln, name in results[:30]:
        flag = "  <== 超标" if s > 15 else ""
        print(f"  {s:3d}  {f}:{ln} {name}{flag}")
