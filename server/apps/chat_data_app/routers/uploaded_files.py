"""
文件上传API路由
"""

from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import Response

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..services.uploaded_file_service import (
    upload_file,
    get_file_info,
    delete_file,
    get_file_content,
    list_files_by_thread,
)
from .dependencies import _get_current_user

logger = get_logger(__name__)

router = APIRouter(prefix="/uploaded-files", tags=["uploaded-files"])


@router.post("/upload")
async def upload_file_api(
    file: UploadFile = File(...),
    thread_id: str = Form(...),
    req: Request = None
):
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
            code=ErrorCode.HTTP_BAD_REQUEST,
            status="error",
            http_status_code=ErrorCode.HTTP_BAD_REQUEST,
        )

    # 限制文件大小 (50MB)
    MAX_SIZE = 50 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_SIZE:
        return business_response(
            msg="文件大小不能超过50MB",
            code=ErrorCode.HTTP_BAD_REQUEST,
            status="error",
            http_status_code=ErrorCode.HTTP_BAD_REQUEST,
        )

    # 重置文件指针
    await file.seek(0)

    try:
        user = _get_current_user(req)
        user_id = str(user.user_id)
        result = await upload_file(file, thread_id, user_id=user_id)
        return business_response(data=result, msg="文件上传成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传失败: {e}", exc_info=True)
        return business_response(
            msg="文件上传失败，请稍后重试",
            code=ErrorCode.HTTP_INTERNAL_SERVER_ERROR,
            status="error",
            http_status_code=ErrorCode.HTTP_INTERNAL_SERVER_ERROR,
        )


@router.get("/thread/{thread_id}")
async def list_thread_files_api(thread_id: str):
    """
    获取会话下的所有文件
    """
    files = await list_files_by_thread(thread_id)
    return business_response(data={"files": files}, msg="获取成功")


@router.get("/{file_id}")
async def get_file_info_api(file_id: str):
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
    info = await get_file_info(file_id)
    if not info:
        return business_response(
            msg="文件不存在",
            code=ErrorCode.HTTP_NOT_FOUND,
            status="error",
            http_status_code=ErrorCode.HTTP_NOT_FOUND,
        )

    return business_response(data=info, msg="获取成功")


@router.delete("/{file_id}")
async def delete_file_api(file_id: str, req: Request = None):
    """
    删除文件
    """
    user = _get_current_user(req)
    user_id = str(user.user_id)
    success = await delete_file(file_id, user_id=user_id)
    if not success:
        return business_response(
            msg="文件不存在",
            code=ErrorCode.HTTP_NOT_FOUND,
            status="error",
            http_status_code=ErrorCode.HTTP_NOT_FOUND,
        )

    return business_response(data={"file_id": file_id}, msg="删除成功")


@router.get("/{file_id}/content")
async def get_file_content_api(file_id: str, req: Request = None):
    """
    获取原始文件内容（下载）
    """
    user = _get_current_user(req)
    user_id = str(user.user_id)
    info = await get_file_info(file_id)
    if not info:
        return business_response(
            msg="文件不存在",
            code=ErrorCode.HTTP_NOT_FOUND,
            status="error",
            http_status_code=ErrorCode.HTTP_NOT_FOUND,
        )

    content = await get_file_content(file_id, user_id=user_id)
    if content is None:
        return business_response(
            msg="文件内容不存在",
            code=ErrorCode.HTTP_NOT_FOUND,
            status="error",
            http_status_code=ErrorCode.HTTP_NOT_FOUND,
        )

    # 设置Content-Type
    file_type = info["file_type"]
    content_type = _get_content_type(file_type)

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{info["original_filename"]}"'
        }
    )


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
    }
    return type_map.get(file_type, "application/octet-stream")
