"""SQL completion 链路的运行参数解析（自 HTTP 入口层归位到 agent/run）。

供 sql_completion_handler 与 agent_schedule_runtime 派发链路共用，
保证两条入口的 options 字段解析口径一致；模块仅依赖标准库，
避免派发链路反向依赖入口层的重装配。
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class CompletionRunOptions:
    """sql_completion_handler 从 options 中解析出的本轮运行参数。"""

    datasource_id: str | None
    semantic_config_id: str | None
    model_id: Any
    enable_thinking: Any
    thinking_effort: Any
    file_selections: list[str]
    skill_names: list[str]
    quoted_snippets: list[dict[str, str]]
    # 文档选中内容（[{"file_path", "doc_path", "quote", "comment"}]）：
    # 前端在 Office 预览里划选后引用，问题/修改要求随消息透传到 Human 消息标记块。
    annotations: list[dict[str, Any]]
    # 无人值守运行（评测/批量等内部调用方经 options 显式置 True）：工具装配
    # 剔除 ask_user_question（用户不在场，提问只会悬挂在 waiting_user）。
    # chat 入口从 options 读取；定时入口在 build_run_context 里显式置 True，
    # 不依赖任务保存的 options，避免历史任务漏标。
    disable_user_interaction: bool


def normalize_enable_thinking(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def normalize_thinking_effort(value: Any) -> str | None:
    """归一化思考档位；非法值返回 None（由 resolve_thinking 回退 default_effort）。"""
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized or None
    return None


def parse_file_selections(value: Any) -> list[str]:
    """解析 @ 引用的工作区文件清单（相对路径列表），无效条目丢弃、按序去重。"""
    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    selections: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = str(item or "").strip().strip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        selections.append(normalized)
    return selections


def parse_quoted_snippets(value: Any) -> list[dict[str, str]]:
    """解析划选引用片段（[{"text": ...}]，兼容纯字符串条目）。

    strip 后去空、按序去重；单条与总条数设上限——引用为人工逐条添加，
    正常远低于上限，超出直接截断，防异常超大 payload 进 state/checkpoint。
    """
    if not isinstance(value, list):
        return []
    snippets: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        text = str(item.get("text") or "") if isinstance(item, dict) else str(item or "")
        text = text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        snippets.append({"text": text[:_QUOTED_SNIPPET_MAX_CHARS]})
        if len(snippets) >= _QUOTED_SNIPPET_MAX_ITEMS:
            break
    return snippets


_QUOTED_SNIPPET_MAX_CHARS = 2000
_QUOTED_SNIPPET_MAX_ITEMS = 5

_ANNOTATION_MAX_ITEMS = 5
_ANNOTATION_QUOTE_MAX_CHARS = 1000
_ANNOTATION_COMMENT_MAX_CHARS = 1000
_ANNOTATION_DOC_PATH_MAX_CHARS = 200
_ANNOTATION_FILE_ROOTS = ("uploads/", "workspace/", "outputs/")
_ANNOTATION_USER_DATA_PREFIX = "/mnt/user-data/"


def _normalize_annotation_file_path(value: Any) -> str:
    """标注文件路径归一到工作区相对路径（uploads/workspace/outputs 三根之一）。

    这是 HTTP 入参边界：客户端可以伪造任意路径，只接受会话工作区三根下的
    相对路径，其余丢弃；真正的写权限仍由工作区/OfficeCLI 层兜底。
    """
    path = str(value or "").strip()
    if path.startswith(_ANNOTATION_USER_DATA_PREFIX):
        path = path[len(_ANNOTATION_USER_DATA_PREFIX) :]
    path = path.lstrip("/")
    if not path or ".." in path.split("/"):
        return ""
    if not any(path.startswith(root) for root in _ANNOTATION_FILE_ROOTS):
        return ""
    return path


def _normalize_annotation_doc_path(value: Any) -> str:
    """OfficeCLI 文档内定位（/body/p[4]、/slide[1]、/Sheet1/A1）。"""
    doc_path = str(value or "").strip()
    if not doc_path.startswith("/") or len(doc_path) > _ANNOTATION_DOC_PATH_MAX_CHARS:
        return ""
    if any(char.isspace() for char in doc_path):
        return ""
    return doc_path


def _normalize_annotation_item(item: Any) -> dict[str, Any] | None:
    """单条标注归一：非法形状返回 None，由调用方丢弃。"""
    if not isinstance(item, dict):
        return None
    file_path = _normalize_annotation_file_path(item.get("file_path"))
    doc_path = _normalize_annotation_doc_path(item.get("doc_path"))
    quote = str(item.get("quote") or "").strip()[:_ANNOTATION_QUOTE_MAX_CHARS]
    comment = str(item.get("comment") or "").strip()[:_ANNOTATION_COMMENT_MAX_CHARS]
    if not file_path or not doc_path or not comment:
        return None
    return {
        "file_path": file_path,
        "doc_path": doc_path,
        "quote": quote,
        "comment": comment,
    }


def parse_document_annotations(value: Any) -> list[dict[str, Any]]:
    """解析文档选中内容；空条目与非法路径直接丢弃，按序去重。

    与 quoted_snippets 同一信任边界：HTTP options 由客户端提交，这里只做
    形状与长度收敛，不承担文件权限校验。
    """
    if not isinstance(value, list):
        return []
    annotations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in value:
        annotation = _normalize_annotation_item(item)
        if annotation is None:
            continue
        key = (
            annotation["file_path"],
            annotation["doc_path"],
            annotation["quote"],
            annotation["comment"],
        )
        if key in seen:
            continue
        seen.add(key)
        annotations.append(annotation)
        if len(annotations) >= _ANNOTATION_MAX_ITEMS:
            break
    return annotations


def parse_unattended_flag(value: Any) -> bool:
    """无人值守标记：服务端内部调用方（评测/批量）使用；chat 正常链路不传。

    注意：HTTP ``options`` 由客户端提交，本标记不构成安全边界——它只影响
    自身工具面（剔除 ask_user_question 与 ask 工具），不改变审批策略。
    """
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes")


def parse_completion_run_options(options: dict[str, Any] | None) -> CompletionRunOptions:
    options_data = options or {}
    raw_skill_names = options_data.get("skill_names") or []
    if isinstance(raw_skill_names, str):
        skill_names = [raw_skill_names]
    elif isinstance(raw_skill_names, list):
        skill_names = [str(item or "").strip() for item in raw_skill_names if str(item or "").strip()]
    else:
        skill_names = []
    return CompletionRunOptions(
        datasource_id=str(options_data.get("datasource_id") or "").strip() or None,
        semantic_config_id=str(options_data.get("semantic_config_id") or "").strip() or None,
        model_id=options_data.get("model_id"),
        enable_thinking=normalize_enable_thinking(options_data.get("enable_thinking")),
        thinking_effort=normalize_thinking_effort(options_data.get("thinking_effort")),
        file_selections=parse_file_selections(options_data.get("file_selections")),
        skill_names=skill_names,
        quoted_snippets=parse_quoted_snippets(options_data.get("quoted_snippets")),
        annotations=parse_document_annotations(options_data.get("annotations")),
        disable_user_interaction=parse_unattended_flag(options_data.get("disable_user_interaction")),
    )
