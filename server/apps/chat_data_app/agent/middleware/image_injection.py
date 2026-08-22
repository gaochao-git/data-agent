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
USER_UPLOAD_SOURCE = "user_upload"
# 单次注入的图片上限：与用户消息附件准入保持同一量级，防止上下文被图片占满
MAX_INJECTED_IMAGES = 4
VISION_ATTACHMENTS_KEY = "vision_attachments"

_VLM_DISABLED_NOTE = (
    "\n当前模型未开启视觉能力（vlm-off），无法读取图片内容；"
    "请如实告知用户当前模型不支持直接查看图片。"
)


def create_image_injection_middleware(
    model_id: str | None = None,
    *,
    state_schema: Any | None = None,
):
    """创建视觉注入 middleware（read_image 结果 + 用户上传图片附件）。"""

    @wrap_model_call(name="ImageInjectionMiddleware", state_schema=state_schema)
    async def image_injection_middleware(request, handler):
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
            return await handler(_override_request(
                request,
                messages=_append_vlm_disabled_notes(messages, read_image_pendings),
            ))

        injection = _build_image_injection_message(request, pending_requests)
        if injection is None:
            return await handler(request)
        return await handler(_override_request(request, messages=[*messages, injection]))

    return image_injection_middleware


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
        if isinstance(message, HumanMessage):
            marked = (getattr(message, "additional_kwargs", None) or {}).get(VISION_ATTACHMENTS_KEY)
            if isinstance(marked, list):
                for path in marked:
                    if isinstance(path, str) and path.strip():
                        requests.append((USER_UPLOAD_SOURCE, path.strip()))
            continue
        if (
            isinstance(message, ToolMessage)
            and str(getattr(message, "name", "") or "") == READ_IMAGE_TOOL_NAME
            and getattr(message, "status", None) != "error"
        ):
            path = paths_by_call_id.get(str(message.tool_call_id or "")) or ""
            requests.append((READ_IMAGE_TOOL_NAME, path))
    return requests


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
    pending_requests: list[tuple[str, str]],
) -> HumanMessage | None:
    """构造携带图片 block 的隐藏 HumanMessage；全部失败时返回仅含说明的文本。"""
    if not pending_requests:
        return None
    state = getattr(request, "state", None)
    runtime = workspace_runtime_from_state(state if isinstance(state, dict) else {})
    workspace_root = runtime.workspace_root

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
            logger.warning("图片注入读取失败: %s", exc)
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
        loaded_sources[source].append(file_path)

    total = len(image_blocks)
    if total == 0 and not failures:
        return None

    lines = ["[视觉输入] 以下是本轮可用的图片内容："]
    if total:
        lines.append(f"共 {total} 张，请基于图片内容继续分析。")
    if loaded_sources[USER_UPLOAD_SOURCE]:
        lines.append(f"- 用户上传附件 {len(loaded_sources[USER_UPLOAD_SOURCE])} 张：{'、'.join(loaded_sources[USER_UPLOAD_SOURCE])}")
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
