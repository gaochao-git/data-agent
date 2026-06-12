from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


ENV_LINE_RE = re.compile(r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*?)(?P<newline>\r?\n?)$")


@dataclass
class EnvLine:
    raw: str
    key: str = ""
    value: str = ""
    prefix: str = ""
    newline: str = ""


def parse_env(path: Path) -> tuple[list[EnvLine], dict[str, EnvLine]]:
    lines: list[EnvLine] = []
    values: dict[str, EnvLine] = {}
    text = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines(keepends=True):
        match = ENV_LINE_RE.match(raw_line)
        if not match:
            lines.append(EnvLine(raw=raw_line))
            continue
        item = EnvLine(
            raw=raw_line,
            key=match.group("key"),
            value=match.group("value"),
            prefix=match.group("prefix"),
            newline=match.group("newline") or "\n",
        )
        lines.append(item)
        values[item.key] = item
    return lines, values


def display_value(value: str, show_values: bool) -> str:
    if show_values:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"<hidden len={len(value)} sha256={digest}>"


def require_file(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")
    if not path.is_file():
        raise SystemExit(f"不是文件：{path}")


def print_key_list(title: str, keys: list[str]) -> None:
    print(f"{title}: {len(keys)}")
    for key in keys:
        print(f"  {key}")


def diff_env(args: argparse.Namespace) -> None:
    left = Path(args.left)
    right = Path(args.right)
    require_file(left)
    require_file(right)
    _, left_values = parse_env(left)
    _, right_values = parse_env(right)
    left_keys = set(left_values)
    right_keys = set(right_values)
    only_left = sorted(left_keys - right_keys)
    only_right = sorted(right_keys - left_keys)
    changed = sorted(key for key in left_keys & right_keys if left_values[key].value != right_values[key].value)

    print_key_list(f"仅 {left} 有的 key", only_left)
    print_key_list(f"仅 {right} 有的 key", only_right)
    print(f"两边都有但 value 不一致的 key: {len(changed)}")
    for key in changed:
        print(f"  {key}")
        print(f"    {left}: {display_value(left_values[key].value, args.show_values)}")
        print(f"    {right}: {display_value(right_values[key].value, args.show_values)}")


def get_env(args: argparse.Namespace) -> None:
    path = Path(args.file)
    require_file(path)
    _, values = parse_env(path)
    item = values.get(args.key)
    if not item:
        raise SystemExit(f"{path} 不存在 key：{args.key}")
    print(display_value(item.value, args.show_values))


def sync_env(args: argparse.Namespace) -> None:
    source = Path(args.source)
    target = Path(args.target)
    require_file(source)
    source_lines, source_values = parse_env(source)
    source_item = source_values.get(args.key)
    if not source_item:
        raise SystemExit(f"{source} 不存在 key：{args.key}")

    if target.exists():
        target_lines, target_values = parse_env(target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target_lines, target_values = [], {}

    target_item = target_values.get(args.key)
    if target_item:
        target_item.value = source_item.value
        target_item.raw = f"{target_item.prefix}{target_item.key}={source_item.value}{target_item.newline or chr(10)}"
        action = "updated"
    else:
        if target_lines and not target_lines[-1].raw.endswith(("\n", "\r\n")):
            target_lines[-1].raw += "\n"
        target_lines.append(EnvLine(raw=f"{source_item.prefix}{source_item.key}={source_item.value}\n"))
        action = "added"

    target.write_text("".join(line.raw for line in target_lines), encoding="utf-8")
    print(f"{action}: {args.key} from {source} -> {target}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare and sync .env files without printing secret values by default.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diff_parser = subparsers.add_parser("diff", help="Compare keys and values in two .env files.")
    diff_parser.add_argument("left")
    diff_parser.add_argument("right")
    diff_parser.add_argument("--show-values", action="store_true", help="Print raw values. Use carefully.")
    diff_parser.set_defaults(func=diff_env)

    get_parser = subparsers.add_parser("get", help="Read one key from an .env file.")
    get_parser.add_argument("file")
    get_parser.add_argument("key")
    get_parser.add_argument("--show-values", action="store_true", help="Print raw value. Use carefully.")
    get_parser.set_defaults(func=get_env)

    sync_parser = subparsers.add_parser("sync", help="Copy one key value from source file to target file.")
    sync_parser.add_argument("source")
    sync_parser.add_argument("target")
    sync_parser.add_argument("key")
    sync_parser.set_defaults(func=sync_env)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
