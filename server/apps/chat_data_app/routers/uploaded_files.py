"""
文件上传API路由
"""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..services.agent_run.uploaded_file_service import (
    ALLOWED_FILE_EXTENSIONS,
    delete_file,
    get_file_content,
    get_file_info,
    list_files_by_thread,
    upload_file,
)
from .dependencies import _get_current_user

logger = get_logger(__name__)

from . import BINARY_RESPONSES, JSON_API_RESPONSES

router = APIRouter(prefix="/uploaded-files", tags=["ChatData 上传文件"], responses=JSON_API_RESPONSES)

# 允许上传的文件类型白名单已下沉到 services/agent_run/uploaded_file_service.py（唯一来源），
# 此处 re-export 供既有引用与测试使用。


@router.post("/upload", operation_id="upload_chatdata_file")
async def upload_file_api(req: Request, file: UploadFile = File(...), thread_id: str = Form(...)) -> JSONResponse:
    """
    上传文件

    - file: 文件
    - thread_id: 会话ID

    Returns:
        {
            "status": "success",
            "msg": "文件上传成功",
            "code": ErrorCode.SUCCESS,
            "data": {
                "file_id": "xxx",
                "status": "parsing",
                "original_filename": "xxx.pdf",
                "file_type": "pdf",
                "file_size": 12345
            }
        }
    """
    # 验证文件
    if not file.filename:
        return business_response(
            msg="文件名不能为空",
            code=ErrorCode.CHATDATA_REQUIRED_FIELD,
            status="error",
        )

    # 限制文件类型（与前端白名单一致，防止绕过）
    ext = Path(file.filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_FILE_EXTENSIONS:
        return business_response(
            msg=f"不支持的文件类型: .{ext}，仅支持 {', '.join(sorted(ALLOWED_FILE_EXTENSIONS))}",
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            status="error",
        )

    try:
        user = _get_current_user(req)
        user_id = str(user.user_id)
        result = await upload_file(file, thread_id, user_id=user_id)
        return business_response(data=result, msg="文件上传成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            status="error",
        )
    except Exception as e:
        logger.error(f"文件上传失败: {e}", exc_info=True)
        return business_response(
            msg="文件上传失败，请稍后重试",
            code=ErrorCode.CHATDATA_UPLOAD_FAILED,
            status="error",
        )


@router.get("/thread/{thread_id}", operation_id="list_chatdata_thread_files")
async def list_thread_files_api(thread_id: str, req: Request) -> JSONResponse:
    """
    获取会话下的所有文件
    """
    user = _get_current_user(req)
    files = await list_files_by_thread(thread_id, user_id=str(user.user_id))
    return business_response(data={"files": files}, msg="获取成功")


@router.get("/{file_id}", operation_id="get_chatdata_file_info")
async def get_file_info_api(file_id: str, req: Request) -> JSONResponse:
    """
    获取文件信息（用于轮询状态）

    Returns:
        {
            "status": "success",
            "msg": "获取成功",
            "code": ErrorCode.SUCCESS,
            "data": {
                "file_id": "xxx",
                "status": "completed",
                "original_filename": "xxx.pdf",
                "file_type": "pdf",
                "file_size": 12345,
                ...
            }
        }
    """
    user = _get_current_user(req)
    info = await get_file_info(file_id, user_id=str(user.user_id))
    if not info:
        return business_response(
            msg="文件不存在",
            code=ErrorCode.CHATDATA_NOT_FOUND,
            status="error",
        )

    return business_response(data=info, msg="获取成功")


@router.delete("/{file_id}", operation_id="delete_chatdata_file")
async def delete_file_api(file_id: str, req: Request) -> JSONResponse:
    """
    删除文件
    """
    user = _get_current_user(req)
    user_id = str(user.user_id)
    success = await delete_file(file_id, user_id=user_id)
    if not success:
        return business_response(
            msg="文件不存在",
            code=ErrorCode.CHATDATA_NOT_FOUND,
            status="error",
        )

    return business_response(data={"file_id": file_id}, msg="删除成功")


@router.get("/{file_id}/content", operation_id="download_chatdata_file", responses=BINARY_RESPONSES)
async def get_file_content_api(file_id: str, req: Request) -> Response:
    """
    获取原始文件内容（下载）
    """
    user = _get_current_user(req)
    user_id = str(user.user_id)
    info = await get_file_info(file_id, user_id=user_id)
    if not info:
        return business_response(
            msg="文件不存在",
            code=ErrorCode.CHATDATA_NOT_FOUND,
            status="error",
        )

    content = await get_file_content(file_id, user_id=user_id)
    if content is None:
        return business_response(
            msg="文件内容不存在",
            code=ErrorCode.CHATDATA_NOT_FOUND,
            status="error",
        )

    # 设置Content-Type
    file_type = info["file_type"]
    content_type = _get_content_type(file_type)

    original_filename = info["original_filename"]
    ascii_fallback = original_filename.encode("ascii", "ignore").decode("ascii") or "file"
    disposition = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(original_filename)}"
    return Response(content=content, media_type=content_type, headers={"Content-Disposition": disposition})


def _get_content_type(file_type: str) -> str:
    """根据文件类型获取Content-Type"""
    type_map = {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "txt": "text/plain",
        "md": "text/markdown",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xls": "application/vnd.ms-excel",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return type_map.get(file_type, "application/octet-stream")
