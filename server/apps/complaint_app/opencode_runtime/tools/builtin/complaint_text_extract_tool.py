from __future__ import annotations

import os
import uuid
import json
from typing import Any, Dict

from apps.complaint_app.core.versioning import load_manifest, stage_dir
from apps.complaint_app.services.text.text_pipeline import TextPipeline
from utils.storage_paths import get_complaints_real_path
from utils.logger import get_logger

from ..base_tool import Tool, ToolContext

logger = get_logger(__name__)


def registry() -> Tool:
    """返回内置举报件附件文本提取工具实例。"""

    def _load_latest_text(user_id: str, base_id: str) -> str:
        """读取最新的原文文本。"""
        try:
            manifest = load_manifest(user_id, base_id)
            text_version = int(manifest.get("text", {}).get("current") or 0) if isinstance(manifest.get("text"), dict) else 0
        except Exception as exc:
            logger.warning("[complaint_text_extract_tool] load manifest failed user_id=%s base_id=%s err=%s", user_id, base_id, exc)
            text_version = 0
        if text_version > 0:
            version_dir = stage_dir(user_id, base_id, "text", text_version)
            for candidate in ("final.txt", "final.md"):
                candidate_path = os.path.join(version_dir, candidate)
                if os.path.isfile(candidate_path):
                    try:
                        with open(candidate_path, "r", encoding="utf-8") as handle:
                            return handle.read()
                    except Exception as exc:
                        logger.warning("[complaint_text_extract_tool] read latest text failed path=%s err=%s", candidate_path, exc)
                        pass
        return ""

    async def _handler(payload: Dict[str, Any], context: ToolContext):
        """执行文本提取，返回抽取后的文本。"""
        data = payload or {}
        user_id = str(data.get("user_id") or "cli").strip()
        input_file_path = str(data.get("input_file_path") or data.get("file_path") or "").strip()
        base_id = str(data.get("base_id") or "").strip() or uuid.uuid4().hex
        upload_display_name = str(data.get("upload_display_name") or "").strip()
        reuse_existing = bool(data.get("reuse_existing_original") or False)
        error_message = ""
        original_input_path = input_file_path

        if input_file_path and base_id:
            try:
                original_root = os.path.abspath(get_complaints_real_path(user_id, base_id, "original"))
                input_abs = os.path.abspath(input_file_path)
                if os.path.commonpath([original_root, input_abs]) == original_root:
                    reuse_existing = True
                    input_file_path = ""
            except Exception as exc:
                logger.warning("[complaint_text_extract_tool] detect existing original failed user_id=%s base_id=%s input_file_path=%s err=%s", user_id, base_id, input_file_path, exc)
                pass

        if input_file_path:
            if not os.path.isfile(input_file_path):
                raise FileNotFoundError(f"input_file_path not found: {input_file_path}")
            if not upload_display_name:
                upload_display_name = os.path.basename(input_file_path)
            pipeline = TextPipeline(
                user_id,
                input_file_path=input_file_path,
                upload_display_name=upload_display_name,
                base_id_override=base_id,
                enable_summary=False,
            )
        else:
            if not base_id:
                raise ValueError("base_id is required when input_file_path is empty")
            pipeline = TextPipeline(
                user_id,
                resume_base_id=base_id,
                reuse_existing_original=reuse_existing,
                enable_summary=False,
            )

        async for chunk in pipeline.generate_from_upload():
            try:
                text = str(chunk or "")
            except Exception as exc:
                logger.warning("[complaint_text_extract_tool] normalize chunk failed user_id=%s base_id=%s err=%s", user_id, base_id, exc)
                continue
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                payload_raw = line.split("data:", 1)[1]
                if payload_raw.startswith(" "):
                    payload_raw = payload_raw[1:]
                if not payload_raw:
                    continue
                try:
                    payload_obj = json.loads(payload_raw)
                except Exception as exc:
                    logger.warning("[complaint_text_extract_tool] parse sse payload failed user_id=%s base_id=%s err=%s payload=%s", user_id, base_id, exc, payload_raw[:300])
                    continue
                if isinstance(payload_obj, dict):
                    if payload_obj.get("error"):
                        error_message = str(payload_obj.get("error") or "")
                    if str(payload_obj.get("status") or "").lower() == "error":
                        error_message = str(payload_obj.get("message") or error_message)

        text = _load_latest_text(user_id, base_id)
        status = "ok" if text else "empty"
        if error_message:
            status = "error"
        return {
            "title": "complaint_text_extract",
            "output": text or "",
            "metadata": {
                "user_id": user_id,
                "base_id": base_id,
                "status": status,
                "error": error_message,
            },
        }

    return Tool(
        name="complaint_text_extract",
        description="举报件附件文本提取工具：从上传文件或已有会话中提取原文文本。",
        _executor=_handler,
        metadata={
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "用户ID（用于文件存储路径）",
                    },
                    "input_file_path": {
                        "type": "string",
                        "description": "本地文件路径（pdf/png/jpg）",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "本地文件路径（input_file_path 的别名）",
                    },
                    "upload_display_name": {
                        "type": "string",
                        "description": "上传文件显示名（可选）",
                    },
                    "base_id": {
                        "type": "string",
                        "description": "会话ID（复用已有原件时使用）",
                    },
                    "reuse_existing_original": {
                        "type": "boolean",
                        "description": "是否复用已存在的原件（需配合 base_id）",
                    },
                },
                "required": [],
            }
        },
    )
