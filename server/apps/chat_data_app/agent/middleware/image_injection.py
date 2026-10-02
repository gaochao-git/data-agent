"""视觉注入边界：read_image 工具结果与用户上传图片附件。

两条来源统一在本中间件收口，base64 只存在于模型请求层，不写入
state、checkpoint 与事件表：

- read_image 工具结果只携带文本回执（协议安全、checkpoint 轻量），
  按 ``tool_call_id`` 反查 ``file_path`` 参数后装载。
- 用户上传图片附件在构造消息时仅打 ``vision_attachments`` 路径标记
  （additional_kwargs），本中间件按标记装载。

装载策略：两来源合并、最新优先、共享 ``MAX_INJECTED_IMAGES`` 上限、
按物理路径去重；注入为一条隐藏 HumanMessage（``image_url`` content
block），前端不可见。

- vlm-on：按当轮模型能力位装载并注入。会话中途切换模型时历史图片
  附件自动恢复/停止可见。
- vlm-off：不注入图片；read_image 工具结果改写为"不支持视觉"提示
  （模型主动动作需被告知失败原因），用户上传附件静默跳过（附件占位
  行已告知模型文件位置）。
- 注入发生在 ``context_compression`` / ``context_usage`` 外层，
  图片按 ``IMAGE_TOKEN_ESTIMATE`` 正确计入上下文预算。
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelCallResult, ModelRequest, wrap_model_call
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.chat_data_app.agent.workspace.runtime import (
    resolve_workspace_path,
    workspace_runtime_from_state,
)
from apps.chat_data_app.agent.workspace.uploaded_file_parser import IMAGE_EXTENSIONS, get_image_size_limit
from apps.chat_data_app.services.llm_config import get_chat_data_model_config
from utils.logger import get_logger

logger = get_logger(__name__)

READ_IMAGE_TOOL_NAME = "read_image"
USER_UPLOAD_SOURCE = "user_upload"
# 单次注入的图片上限：与用户消息附件准入保持同一量级，防止上下文被图片占满
MAX_INJECTED_IMAGES = 4
VISION_ATTACHMENTS_KEY = "vision_attachments"

_VLM_DISABLED_NOTE = "\n当前模型未开启视觉能力（vlm-off），无法读取图片内容；请如实告知用户当前模型不支持直接查看图片。"


def create_image_injection_middleware(
    model_id: str | None = None,
    *,
    state_schema: Any | None = None,
) -> AgentMiddleware:
    """创建视觉注入 middleware（read_image 结果 + 用户上传图片附件）。"""

    @wrap_model_call(name="ImageInjectionMiddleware", state_schema=state_schema)
    async def image_injection_middleware(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelCallResult]]
    ) -> ModelCallResult:
        messages = list(getattr(request, "messages", None) or [])
        pending_requests = _collect_pending_image_requests(messages)

        if not bool(get_chat_data_model_config(model_id).get("vlm")):
            read_image_pendings = [
                message
                for message in messages
                if isinstance(message, ToolMessage)
                and str(getattr(message, "name", "") or "") == READ_IMAGE_TOOL_NAME
                and getattr(message, "status", None) != "error"
            ]
            if not read_image_pendings:
                return await handler(request)
            return await handler(
                _override_request(
                    request,
                    messages=_append_vlm_disabled_notes(messages, read_image_pendings),
                )
            )

        _, max_image_size = await get_image_size_limit()
        injection = _build_image_injection_message(request, pending_requests, max_image_size=max_image_size)
        if injection is None:
            return await handler(request)
        return await handler(_override_request(request, messages=[*messages, injection]))

    return image_injection_middleware


def _user_upload_requests(message: Any) -> list[tuple[str, str]]:
    """从用户消息的 ``vision_attachments`` 标记收集上传图片请求。"""
    requests: list[tuple[str, str]] = []
    marked = (getattr(message, "additional_kwargs", None) or {}).get(VISION_ATTACHMENTS_KEY)
    if isinstance(marked, list):
        for path in marked:
            if isinstance(path, str) and path.strip():
                requests.append((USER_UPLOAD_SOURCE, path.strip()))
    return requests


def _collect_message_image_requests(
    message: Any,
    paths_by_call_id: dict[str, str],
) -> list[tuple[str, str]]:
    """收集单条消息的待注入图片请求 ``(source, path)``。"""
    if isinstance(message, HumanMessage):
        return _user_upload_requests(message)
    if (
        isinstance(message, ToolMessage)
        and str(getattr(message, "name", "") or "") == READ_IMAGE_TOOL_NAME
        and getattr(message, "status", None) != "error"
    ):
        path = paths_by_call_id.get(str(message.tool_call_id or "")) or ""
        return [(READ_IMAGE_TOOL_NAME, path)]
    return []


def _collect_pending_image_requests(messages: list[Any]) -> list[tuple[str, str]]:
    """按消息序（旧→新）收集待注入图片请求 ``(source, path)``。

    来源一：带 ``vision_attachments`` 标记的用户消息（路径直接来自标记）。
    来源二：成功的 read_image 工具结果，经配对 AIMessage.tool_calls 反查
    ``file_path`` 参数；反查不到的记录空路径，由装载阶段给出失败说明。
    消费方从最新往旧扫描，配合去重即得"最近优先"滚动窗口。
    """
    paths_by_call_id = _tool_file_paths_by_call_id(messages)
    requests: list[tuple[str, str]] = []
    for message in messages:
        requests.extend(_collect_message_image_requests(message, paths_by_call_id))
    return requests


def _tool_file_path_for_call(tool_call: Any) -> str:
    """反查单个 tool_call 的 read_image file_path 参数（解析失败返回空串）。"""
    if not isinstance(tool_call, dict) or not tool_call.get("id"):
        return ""
    args = tool_call.get("args")
    file_path = str(args.get("file_path") or "").strip() if isinstance(args, dict) else ""
    return file_path


def _tool_file_paths_by_call_id(messages: list[Any]) -> dict[str, str]:
    """从配对的 AIMessage.tool_calls 反查 read_image 的 file_path 参数。"""
    args_by_call_id: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            path = _tool_file_path_for_call(tool_call)
            if path:
                args_by_call_id[str(tool_call["id"])] = path
    return args_by_call_id


def _build_image_injection_message(
    request: Any,
    pending_requests: list[tuple[str, str]],
    *,
    max_image_size: int,
) -> HumanMessage | None:
    """构造携带图片 block 的隐藏 HumanMessage；全部失败时返回仅含说明的文本。"""
    if not pending_requests:
        return None
    state = getattr(request, "state", None)
    runtime = workspace_runtime_from_state(state if isinstance(state, dict) else {})
    workspace_root = runtime.workspace_root

    image_blocks, loaded_sources, failures = _load_injection_images(
        workspace_root, pending_requests, max_image_size=max_image_size
    )
    total = len(image_blocks)
    if total == 0 and not failures:
        return None

    lines = ["[视觉输入] 以下是本轮可用的图片内容："]
    if total:
        lines.append(f"共 {total} 张，请基于图片内容继续分析。")
    if loaded_sources[USER_UPLOAD_SOURCE]:
        lines.append(
            f"- 用户上传附件 {len(loaded_sources[USER_UPLOAD_SOURCE])} 张：{'、'.join(loaded_sources[USER_UPLOAD_SOURCE])}"
        )
    if loaded_sources[READ_IMAGE_TOOL_NAME]:
        lines.append(
            f"- 通过 {READ_IMAGE_TOOL_NAME} 加载 {len(loaded_sources[READ_IMAGE_TOOL_NAME])} 张："
            f"{'、'.join(loaded_sources[READ_IMAGE_TOOL_NAME])}"
        )
    for failure in failures:
        lines.append(f"- 读取失败: {failure}")
    return HumanMessage(
        content=[{"type": "text", "text": "\n".join(lines)}, *image_blocks],
        additional_kwargs={"hide_from_ui": True, "image_injection": True},
    )


def _load_injection_images(
    workspace_root: Any,
    pending_requests: list[tuple[str, str]],
    *,
    max_image_size: int,
) -> tuple[list[dict[str, Any]], dict[str, list[str]], list[str]]:
    """按逆序加载待注入图片（上限 MAX_INJECTED_IMAGES，按物理路径去重）。

    返回 (image_blocks, loaded_sources, failures)：失败的路径进入 failures，
    成功的按来源（用户上传 / read_image 工具）归入 loaded_sources。
    """
    image_blocks: list[dict[str, Any]] = []
    loaded_sources: dict[str, list[str]] = {USER_UPLOAD_SOURCE: [], READ_IMAGE_TOOL_NAME: []}
    loaded_paths: set[str] = set()
    failures: list[str] = []
    for source, file_path in reversed(pending_requests):
        if len(image_blocks) >= MAX_INJECTED_IMAGES:
            break
        if not file_path:
            failures.append(f"{READ_IMAGE_TOOL_NAME} 结果缺少可追溯的 file_path 参数")
            continue
        physical = _resolve_injection_image_path(workspace_root, file_path, failures)
        if physical is None:
            continue
        physical_key = str(physical)
        if physical_key in loaded_paths:
            continue
        outcome = _read_image_block(physical, file_path, max_image_size=max_image_size)
        if outcome[0] is None:  # 不变量：block 为 None 当且仅当失败
            failures.append(outcome[1])
            continue
        image_blocks.append(outcome[0])
        loaded_paths.add(physical_key)
        loaded_sources[source].append(file_path)
    return image_blocks, loaded_sources, failures


def _resolve_injection_image_path(workspace_root: Any, file_path: str, failures: list[str]) -> Path | None:
    """把注入目标解析为可读的物理 Path；解析失败记录原因并返回 None。"""
    try:
        return resolve_workspace_path(workspace_root, file_path, writable=False)
    except ValueError as exc:
        failures.append(f"{file_path}: {exc}")
        return None


def _read_image_block(
    physical: Any,
    file_path: str,
    *,
    max_image_size: int,
    # 返回形状绑定不变量：block 为 None 当且仅当 failure 非 None。
) -> tuple[dict[str, Any], None] | tuple[None, str]:
    """读取单张图片并编码为 base64 block；超限/格式不支持返回失败原因（文案用原始 file_path）。"""
    try:
        data = physical.read_bytes()
    except OSError as exc:
        logger.warning("图片注入读取失败: %s", exc)
        return None, f"{file_path}: 读取文件失败"
    if len(data) > max_image_size:
        return None, f"{file_path}: 图片超过大小上限"
    suffix = physical.suffix.lower().lstrip(".")
    if suffix not in IMAGE_EXTENSIONS:
        return None, f"{file_path}: 不支持的图片格式"
    mime = _IMAGE_MIME_TYPES.get(suffix, "application/octet-stream")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"},
    }, None


def _append_vlm_disabled_notes(
    messages: list[Any],
    pending: list[ToolMessage],
) -> list[Any]:
    """vlm-off 时在 read_image 工具结果后追加不支持视觉的说明，不注入图片。"""
    pending_ids = {id(message) for message in pending}
    rewritten = []
    for message in messages:
        if id(message) in pending_ids:
            rewritten.append(message.model_copy(update={"content": str(message.content) + _VLM_DISABLED_NOTE}))
        else:
            rewritten.append(message)
    return rewritten


def _override_request(request: Any, **kwargs: Any) -> ModelRequest:
    """兼容 LangChain ModelRequest 和单测中的轻量对象。"""
    if hasattr(request, "override"):
        return request.override(**kwargs)
    data = dict(getattr(request, "__dict__", {}) or {})
    data.update(kwargs)
    return type(request)(**data)


_IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}
