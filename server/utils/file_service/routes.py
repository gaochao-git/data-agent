"""
文件上传API路由
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, Response

from utils.logger import get_logger
from .service import (
    upload_file,
    get_file_info,
    delete_file,
    get_file_content,
    list_files_by_thread,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/chatdata/uploaded-files", tags=["uploaded-files"])


@router.post("/upload")
async def upload_file_api(
    file: UploadFile = File(...),
    thread_id: str = Form(...)
):
    """
    上传文件

    - file: 文件
    - thread_id: 会话ID

    Returns:
        {
            "file_id": "xxx",
            "status": "parsing",
            "original_filename": "xxx.pdf",
            "file_type": "pdf",
            "file_size": 12345
        }
    """
    # 验证文件
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    # 限制文件大小 (50MB)
    MAX_SIZE = 50 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="文件大小不能超过50MB")

    # 重置文件指针
    await file.seek(0)

    try:
        result = await upload_file(file, thread_id)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"文件上传失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{file_id}")
async def get_file_info_api(file_id: str):
    """
    获取文件信息（用于轮询状态）

    Returns:
        {
            "file_id": "xxx",
            "status": "completed",
            "original_filename": "xxx.pdf",
            "file_type": "pdf",
            "file_size": 12345,
            ...
        }
    """
    info = await get_file_info(file_id)
    if not info:
        raise HTTPException(status_code=404, detail="文件不存在")

    return JSONResponse(info)


@router.delete("/{file_id}")
async def delete_file_api(file_id: str):
    """
    删除文件
    """
    success = await delete_file(file_id)
    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    return JSONResponse({"message": "删除成功"})


@router.get("/{file_id}/content")
async def get_file_content_api(file_id: str):
    """
    获取原始文件内容（下载）
    """
    info = await get_file_info(file_id)
    if not info:
        raise HTTPException(status_code=404, detail="文件不存在")

    content = await get_file_content(file_id)
    if content is None:
        raise HTTPException(status_code=404, detail="文件内容不存在")

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


@router.get("/thread/{thread_id}")
async def list_thread_files_api(thread_id: str):
    """
    获取会话下的所有文件
    """
    files = await list_files_by_thread(thread_id)
    return JSONResponse({"files": files})


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
