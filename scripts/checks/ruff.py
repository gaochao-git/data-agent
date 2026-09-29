"""扫描后端 lint——AGENTS.md 新增门禁「server/ 下 bug 级告警必须为零」。

用法（仓库任意目录）::

    python scripts/checks/ruff.py                    # 扫描 server/，有告警退出码 1
    python scripts/checks/ruff.py --target server/apps/chat_data_app
    python scripts/checks/ruff.py --statistics       # 按规则聚合，便于评估存量

口径：规则集与豁免见仓库根 ruff.toml（F/B/SIM bug 级三类 + I/W 导入排序与空白符，
2026-09 扩容；风格类 E501/UP/E402 仍不纳入——E501 存量 2200+ 且字符串/注释不可自动修，
E402 会误伤文档允许的循环依赖惰性导入）。ruff 用 ``python -m ruff``
调用——不依赖 PATH，与 verify 里其它步骤共用同一个 fxcjg 解释器，避免版本漂移。

subprocess 固定 cwd=REPO_ROOT：ruff.toml 的 src 按调用方 cwd 解析，不钉死则 I/W 的
首方包分类随执行目录漂移（手工对拍同理，务必在仓库根执行）。

存量为零即通过，因此**不设 baseline**：lint 告警与复杂度债务不同，前者几乎总能
就地改掉或就地论证为误报（加 noqa 并写明理由），留 baseline 等于放任积累。

注意：F401（未使用 import）**不要用 --fix 批量删**——本仓存在「为测试 monkeypatch
而有意重导出」的导入（源码里有 re-export 注释），ruff 认不出来，批量删会打断测试打桩。
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TARGET = REPO_ROOT / "server"
CONFIG = REPO_ROOT / "ruff.toml"


def _build_cmd(target: Path, statistics: bool) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--config",
        str(CONFIG),
        "--no-cache",
    ]
    if statistics:
        cmd.append("--statistics")
    cmd.append(str(target))
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=str, default=str(DEFAULT_TARGET), help="扫描目标（默认 server/）")
    parser.add_argument("--statistics", action="store_true", help="按规则聚合输出，不判定门禁")
    args = parser.parse_args()

    target = Path(args.target)
    if not target.is_absolute():
        target = (REPO_ROOT / target).resolve()

    cmd = _build_cmd(target, args.statistics)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)

    if proc.returncode == 2:
        # 区别于「有告警」的返回码 1：这里是 ruff 自身跑不起来（未安装/配置错误）。
        print("\nruff 未安装或执行失败；fxcjg 环境已自带（python -m ruff），缺失时执行：pip install ruff")
        sys.exit(1)
    if args.statistics:
        return
    if proc.returncode != 0:
        print("\nlint 告警不为零：请就地修复；确属误报则加 `# noqa: <规则>` 并在同行写明理由")
        sys.exit(1)
    print("\nlint 通过：0 条告警")


if __name__ == "__main__":
    main()
