"""``read_image`` 工具结果的视觉注入边界。

工具结果只携带文本回执（协议安全、checkpoint 轻量）；本中间件在下一次
模型调用前，把待读取的图片以 ``image_url`` content block 追加为一条隐藏
HumanMessage。注入只发生在模型请求层，不写入 state，前端与 checkpoint
均不可见。

- vlm-on：按 ``tool_call_id`` 反查 ``file_path`` 参数，读取文件并注入
  （最多最近 ``MAX_INJECTED_IMAGES`` 张，按路径去重）。
- vlm-off：不注入图片，改写工具结果为"不支持视觉"提示，模型可如实转告用户。
- 注入发生在 ``context_compression`` / ``context_usage`` 外层，
  图片按 ``IMAGE_TOKEN_ESTIMATE`` 正确计入上下文预算。
"""

from __future__ import annotations

import base64
from typing import Any

from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from utils.logger import get_logger
from ...services.agent_run.conversation_workspace import (
    resolve_workspace_path,
    workspace_runtime_from_state,
)
from ...services.agent_run.uploaded_file_service import IMAGE_EXTENSIONS, MAX_IMAGE_SIZE
from ...services.llm_config import get_chat_data_model_config

logger = get_logger(__name__)

READ_IMAGE_TOOL_NAME = "read_image"
# 单次注入的图片上限：与用户消息附件准入保持同一量级，防止上下文被图片占满
MAX_INJECTED_IMAGES = 4

_VLM_DISABLED_NOTE = (
    "\n当前模型未开启视觉能力（vlm-off），无法读取图片内容；"
    "请如实告知用户当前模型不支持直接查看图片。"
)


def create_image_injection_middleware(
    model_id: str | None = None,
    *,
    state_schema: Any | None = None,
):
    """创建 read_image 结果的图片注入 middleware。"""

    @wrap_model_call(name="ImageInjectionMiddleware", state_schema=state_schema)
    async def image_injection_middleware(request, handler):
        messages = list(getattr(request, "messages", None) or [])
        pending = _pending_read_image_messages(messages)
        if not pending:
            return await handler(request)

        if not bool(get_chat_data_model_config(model_id).get("vlm")):
            return await handler(_override_request(
                request,
                messages=_append_vlm_disabled_notes(messages, pending),
            ))

        injection = _build_image_injection_message(request, messages, pending)
        if injection is None:
            return await handler(request)
        return await handler(_override_request(request, messages=[*messages, injection]))

    return image_injection_middleware


def _pending_read_image_messages(messages: list[Any]) -> list[ToolMessage]:
    """收集当前上下文中成功的 read_image 工具结果。"""
    return [
        message
        for message in messages
        if isinstance(message, ToolMessage)
        and str(getattr(message, "name", "") or "") == READ_IMAGE_TOOL_NAME
        and getattr(message, "status", None) != "error"
    ]


def _tool_file_paths_by_call_id(messages: list[Any]) -> dict[str, str]:
    """从配对的 AIMessage.tool_calls 反查 read_image 的 file_path 参数。"""
    args_by_call_id: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            if not isinstance(tool_call, dict) or not tool_call.get("id"):
                continue
            args = tool_call.get("args")
            file_path = str(args.get("file_path") or "").strip() if isinstance(args, dict) else ""
            if file_path:
                args_by_call_id[str(tool_call["id"])] = file_path
    return args_by_call_id


def _build_image_injection_message(
    request: Any,
    messages: list[Any],
    pending: list[ToolMessage],
) -> HumanMessage | None:
    """构造携带图片 block 的隐藏 HumanMessage；全部失败时返回仅含说明的文本。"""
    state = getattr(request, "state", None)
    runtime = workspace_runtime_from_state(state if isinstance(state, dict) else {})
    workspace_root = runtime.workspace_root
    paths_by_call_id = _tool_file_paths_by_call_id(messages)

    image_blocks: list[dict[str, Any]] = []
    loaded_paths: set[str] = set()
    failures: list[str] = []
    for tool_message in reversed(pending):
        if len(image_blocks) >= MAX_INJECTED_IMAGES:
            break
        file_path = paths_by_call_id.get(str(tool_message.tool_call_id or ""))
        if not file_path:
            failures.append(f"{READ_IMAGE_TOOL_NAME} 结果缺少可追溯的 file_path 参数")
            continue
        try:
            physical = resolve_workspace_path(workspace_root, file_path, writable=False)
        except ValueError as exc:
            failures.append(f"{file_path}: {exc}")
            continue
        physical_key = str(physical)
        if physical_key in loaded_paths:
            continue
        try:
            data = physical.read_bytes()
        except OSError as exc:
            logger.warning("read_image 注入读取失败: %s", exc)
            failures.append(f"{file_path}: 读取文件失败")
            continue
        if len(data) > MAX_IMAGE_SIZE:
            failures.append(f"{file_path}: 图片超过大小上限")
            continue
        suffix = physical.suffix.lower().lstrip(".")
        if suffix not in IMAGE_EXTENSIONS:
            failures.append(f"{file_path}: 不支持的图片格式")
            continue
        mime = _IMAGE_MIME_TYPES.get(suffix, "application/octet-stream")
        image_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"},
        })
        loaded_paths.add(physical_key)

    if not image_blocks and not failures:
        return None

    lines = [f"[{READ_IMAGE_TOOL_NAME}] 以下是通过 read_image 加载的图片内容："]
    if image_blocks:
        lines.append(f"共 {len(image_blocks)} 张，请基于图片内容继续分析。")
    for failure in failures:
        lines.append(f"- 读取失败: {failure}")
    return HumanMessage(
        content=[{"type": "text", "text": "\n".join(lines)}, *image_blocks],
        additional_kwargs={"hide_from_ui": True, "image_injection": True},
    )


def _append_vlm_disabled_notes(
    messages: list[Any],
    pending: list[ToolMessage],
) -> list[Any]:
    """vlm-off 时在工具结果后追加不支持视觉的说明，不注入图片。"""
    pending_ids = {id(message) for message in pending}
    rewritten = []
    for message in messages:
        if id(message) in pending_ids:
            rewritten.append(message.model_copy(update={"content": str(message.content) + _VLM_DISABLED_NOTE}))
        else:
            rewritten.append(message)
    return rewritten


def _override_request(request: Any, **kwargs: Any):
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
