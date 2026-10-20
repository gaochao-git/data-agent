"""SSE 帧名门禁：校验后端只产出协议层登记的帧名，并与前端消费清单双向比对。

口径（三条，前两条硬失败，第三条只告警）：

1. 后端所有帧名实参必须写成 ``sse_protocol.SSE_EVENT_*`` 常量引用，禁止裸字面量；
   唯一允许的动态实参是子智能体 custom 流的 ``event_type``（该处有不信任输入白名单）。
2. ``f"event: ..."`` 手写整帧只允许出现在 ``sse_protocol.format_sse`` 内部——序列化器
   必须只有一个，否则转义/编码细节会各自漂移。
3. 前端 ``SSEEventType`` 允许声明后端尚未发出的名字（只是死分支，告警即可）；反向
   ——后端发出而前端不认识——会静默丢事件，必须失败。

扫描根是 ``server/``（不是单个 app）：帧生产者将来出现在 utils / 新 app 里同样受管。
**已知的范围外管线**：准确率评测流（``routers/evaluation.py`` + ``evaluate/accuracy/``）
不用 ``event:`` 行，而是把事件名塞进 JSON（``f"data: {json.dumps({'event': ...})}\\n\\n"``），
前端从 ``payload.event`` 读。它是独立词汇表（``start``/``case_start``/``case_result``/
``progress``），本门禁**看不见也不管**；要收口需另立一张表，不要在 sse_protocol 里混。

纯静态解析，不 import 应用模块（避免拉进 env/logger 副作用）。
用法: python scripts/checks/sse_vocabulary.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "server"
FRONTEND_TYPES = REPO_ROOT / "web-vue" / "src" / "views" / "chat-data" / "types" / "index.ts"
PROTOCOL_MODULE = BACKEND_ROOT / "apps" / "chat_data_app" / "sse_protocol.py"

# 允许的动态帧名实参：子智能体 custom 流传入的 event_type
DYNAMIC_ARGS = {"event_type"}
EMIT_ARG_RE = re.compile(r"(?:format_sse|_format_tool_lifecycle_event)\(\s*([A-Za-z_\"']+)")
RAW_FRAME_RE = re.compile(r'f"event:\s*([A-Za-z_]+)')
CONSTANT_DEF_RE = re.compile(r'^(SSE_EVENT_[A-Z_]+) = "([^"]+)"$', re.M)
FRONTEND_UNION_RE = re.compile(r"export type SSEEventType = ([^\n]+)")
UNION_MEMBER_RE = re.compile(r"'([^']+)'")


def _iter_backend_sources() -> list[Path]:
    return sorted(path for path in BACKEND_ROOT.rglob("*.py") if "tests" not in path.parts)


def _load_catalog() -> dict[str, str]:
    """常量名 -> 帧名。协议模块自身是唯一登记处，缺失即视为门禁失效。"""
    return dict(CONSTANT_DEF_RE.findall(PROTOCOL_MODULE.read_text(encoding="utf-8")))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_comment_line(text: str, offset: int) -> bool:
    line_start = text.rfind("\n", 0, offset) + 1
    return text[line_start:].lstrip().startswith("#")


def _violations_at(path: Path, text: str, arg: str, offset: int, catalog: dict[str, str]) -> list[str]:
    location = f"{_rel(path)}:{_line_of(text, offset)}"
    if arg.startswith(('"', "'")):
        return [f"{location} 帧名用了裸字面量 {arg}，必须引用 sse_protocol.SSE_EVENT_*"]
    if arg in DYNAMIC_ARGS:
        return []
    if not arg.startswith("SSE_EVENT_"):
        return [f"{location} 帧名实参 {arg} 既不是 SSE_EVENT_* 常量也不是允许的动态名 {sorted(DYNAMIC_ARGS)}"]
    if arg not in catalog:
        return [f"{location} 引用了未登记的常量 {arg}"]
    return []


def _check_emits(sources: list[Path], catalog: dict[str, str]) -> list[str]:
    """逐文件扫帧名实参；用全文匹配以覆盖换行书写的多行调用。"""
    problems: list[str] = []
    for path in sources:
        if path == PROTOCOL_MODULE:
            continue
        text = path.read_text(encoding="utf-8")
        for match in RAW_FRAME_RE.finditer(text):
            if _is_comment_line(text, match.start()):
                continue
            problems.append(f"{_rel(path)}:{_line_of(text, match.start())} 手写整帧，必须改用 format_sse")
        for match in EMIT_ARG_RE.finditer(text):
            if _is_comment_line(text, match.start()):
                continue
            problems.extend(_violations_at(path, text, match.group(1), match.start(1), catalog))
    return problems


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _frontend_event_names() -> set[str]:
    match = FRONTEND_UNION_RE.search(FRONTEND_TYPES.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"无法在 {_rel(FRONTEND_TYPES)} 解析 SSEEventType，门禁失效")
    return set(UNION_MEMBER_RE.findall(match.group(1)))


def _check_parity(catalog: dict[str, str], frontend: set[str]) -> tuple[list[str], list[str]]:
    emitted = set(catalog.values())
    unhandled = sorted(emitted - frontend)
    unused = sorted(frontend - emitted)
    failures = [f"后端可发出的帧名 {name} 未出现在前端 SSEEventType 中，会被静默丢弃" for name in unhandled]
    return failures, unused


def main() -> int:
    catalog = _load_catalog()
    if not catalog:
        print(f"{_rel(PROTOCOL_MODULE)} 未解析到任何 SSE_EVENT_* 常量，门禁失效", file=sys.stderr)
        return 1
    problems = _check_emits(_iter_backend_sources(), catalog)
    frontend = _frontend_event_names()
    parity_failures, unused = _check_parity(catalog, frontend)
    for line in [*problems, *parity_failures]:
        print(f"SSE 帧名违规: {line}", file=sys.stderr)
    for name in unused:
        print(f"SSE 帧名提示: 前端 SSEEventType 声明了后端未发出的 {name}（死分支，确认后请删除）")
    if problems or parity_failures:
        return 1
    print(f"SSE 帧名门禁通过：{len(catalog)} 个帧名，server/ 下 {len(_iter_backend_sources())} 个源文件无裸字面量")
    return 0


if __name__ == "__main__":
    sys.exit(main())
