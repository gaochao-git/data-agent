#!/usr/bin/env python
"""测试基线联动提醒——本轮改动触碰 browser_click_test_baseline.md 条目引用的文件时提醒同步维护。

只提醒不阻断（恒定退出码 0）：基线是否真的需要更新由维护者判断，机器只负责不让
「改了功能、忘了登记测试基线」静默发生。规则：
- 变更文件集合 = 工作区改动（含暂存）+ 未跟踪文件；两者皆空时回退最近一次 commit
  （覆盖「改完即提交」的 verify 时机）
- 基线引用集合 = 基线文档中出现的源码文件引用，按文件名匹配——文档里部分引用省略
  目录前缀，按全路径匹配会漏；同名文件可能误报，提醒语中已注明
- 交集非空 → 打印命中条目编号与涉及文件，提示检查 §4 用例与 §5 执行记录
"""

import argparse
import re
import subprocess
from pathlib import Path

DEFAULT_BASELINE = Path("server/apps/chat_data_app/tests/browser_click_test_baseline.md")

# 基线文档中的源码文件引用：反引号包裹或裸写，可带目录前缀；不含 .md（文档互引不算代码变更面）
_FILE_REF_RE = re.compile(r"[A-Za-z0-9_./\-]+\.(?:vue|tsx|ts|js|py|css|json)\b")
# 条目编号：CTX-07 / ST-04 / SCHED-01 等；AG-* 这类通配写法匹配不到，命中行无编号时降级提示
_ENTRY_ID_RE = re.compile(r"\b[A-Z]{2,10}-[0-9]{1,3}\b")


def _git_lines(root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def collect_changed_files(root: Path) -> list[str]:
    changed = _git_lines(root, "diff", "--name-only", "HEAD")
    changed += _git_lines(root, "ls-files", "--others", "--exclude-standard")
    if not changed:
        changed = _git_lines(root, "diff", "--name-only", "HEAD~1", "HEAD")
    return sorted(set(changed))


def parse_baseline_refs(baseline: Path) -> list[tuple[frozenset[str], frozenset[str]]]:
    """逐行抽取（条目编号集合, 文件引用集合），返回含文件引用的行。"""
    try:
        text = baseline.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[tuple[frozenset[str], frozenset[str]]] = []
    for line in text.splitlines():
        refs = set(_FILE_REF_RE.findall(line))
        if not refs:
            continue
        rows.append((frozenset(_ENTRY_ID_RE.findall(line)), frozenset(refs)))
    return rows


def match_hits(
    changed: list[str],
    rows: list[tuple[frozenset[str], frozenset[str]]],
) -> list[tuple[frozenset[str], str]]:
    """按涉及文件聚合命中行并合并条目编号——同一文件常被通配表格行和具体关联行
    同时引用，不合并会把带编号的关联行挤丢，提醒只剩「未标注编号」。"""
    by_name: dict[str, list[str]] = {}
    for path in changed:
        by_name.setdefault(Path(path).name, []).append(path)
    merged: dict[str, set[str]] = {}
    for ids, refs in rows:
        matched: list[str] = []
        for ref in refs:
            matched.extend(by_name.get(Path(ref).name, []))
        if matched:
            files = "、".join(sorted(set(matched)))
            merged.setdefault(files, set()).update(ids)
    return [(frozenset(ids), files) for files, ids in merged.items()]


def main() -> int:
    parser = argparse.ArgumentParser(description="测试基线联动提醒（只提醒不阻断）")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="基线文档路径")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="仓库根目录（默认按脚本位置推断）",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    baseline = args.baseline if args.baseline.is_absolute() else root / args.baseline
    if not baseline.is_file():
        print(f"    [test-baseline-watch] 基线文档不存在，跳过提醒: {baseline}")
        return 0

    changed = collect_changed_files(root)
    if not changed:
        return 0
    hits = match_hits(changed, parse_baseline_refs(baseline))
    if not hits:
        return 0

    print()
    print("    ⚠ 本轮改动涉及浏览器测试基线条目引用的文件，请检查测试基线是否需要同步更新：")
    print(f"      基线文档: {baseline}")
    for ids, files in sorted(hits, key=lambda h: h[1]):
        label = "/".join(sorted(ids)) if ids else "未标注编号的引用行"
        print(f"      - [{label}] 涉及: {files}")
    print("      功能有变化时请同步维护基线 §4 用例与 §5 执行记录（按文件名匹配，同名文件可能误报）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
