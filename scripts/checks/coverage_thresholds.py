"""行覆盖率与分支覆盖率双门禁——AGENTS.md「行覆盖率、条件覆盖率均须大于95%」。

用法（在 server/ 目录下，紧跟 pytest --cov 之后执行）::

    python ../scripts/checks/coverage_thresholds.py
    python ../scripts/checks/coverage_thresholds.py --line 95 --branch 95

数据来源：当前目录的 coverage 数据文件（由 pytest --cov 或 coverage run 生成，
分支模式由 server/.coveragerc 的 branch=True 保证；受 COVERAGE_FILE 环境变量
影响——verify 的 coverage 步用独立数据文件 .coverage.gate 防止与历史运行叠加）。
脚本内部调用 ``coverage json`` 导出后解析 totals，分别校验：

    行覆盖   = covered_lines   / num_statements ≥ --line（默认 95）
    分支覆盖 = covered_branches / num_branches  ≥ --branch（默认 95）

任一不达标退出码 1。两阈值同时满足时合并覆盖率必然 ≥95，故可替代
``--cov-fail-under``（verify 的 coverage 步已据此去掉该参数）。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _export_json() -> dict:
    """从当前目录的 coverage 数据文件导出 JSON 并返回 totals；失败即终止。"""
    data_file = Path(os.environ.get("COVERAGE_FILE", ".coverage"))
    if not data_file.is_file():
        print(f"错误：当前目录没有 {data_file} 数据文件，请先跑 pytest --cov 或 coverage run", file=sys.stderr)
        sys.exit(1)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", tmp_path, "--quiet"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"错误：coverage json 导出失败：\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(Path(tmp_path).read_text(encoding="utf-8"))["totals"]
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--line", type=float, default=95.0, help="行覆盖率阈值（默认 95）")
    parser.add_argument("--branch", type=float, default=95.0, help="分支覆盖率阈值（默认 95）")
    args = parser.parse_args()

    totals = _export_json()
    num_statements = totals["num_statements"]
    covered_lines = totals["covered_lines"]
    num_branches = totals["num_branches"]
    covered_branches = totals["covered_branches"]

    line_pct = covered_lines / num_statements * 100 if num_statements else 0.0
    branch_pct = covered_branches / num_branches * 100 if num_branches else 0.0

    print(f"行覆盖:   {covered_lines}/{num_statements} = {line_pct:.2f}%（阈值 ≥{args.line:g}%）")
    print(f"分支覆盖: {covered_branches}/{num_branches} = {branch_pct:.2f}%（阈值 ≥{args.branch:g}%）")

    failed = []
    if line_pct < args.line:
        failed.append(f"行覆盖 {line_pct:.2f}% < {args.line:g}%")
    if branch_pct < args.branch:
        failed.append(f"分支覆盖 {branch_pct:.2f}% < {args.branch:g}%")
    if failed:
        print(f"\n覆盖率门禁未达标：{'；'.join(failed)}")
        sys.exit(1)
    print("\n覆盖率门禁通过（行与分支均达标）")


if __name__ == "__main__":
    main()
