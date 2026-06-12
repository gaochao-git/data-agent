"""
统一响应格式封装
提供标准的 API 响应格式，便于前后端统一处理
"""
from fastapi.responses import JSONResponse


def business_response(data=None, msg: str = "操作成功", code: int = 2000, status: str = "success"):
    """
    统一业务响应 (FastAPI 版本)

    Args:
        data: 返回的数据（可选）
        msg: 消息描述（默认 "操作成功"）
        code: 业务状态码（默认 2000）
        status: 状态（支持 "success" / "error"）

    响应格式：
        {
            "status": "success" | "error",
            "msg": "描述信息",
            "code": 状态码,
            "data": 数据（可选）
        }

    Example:
        # 成功响应
        >>> return business_response(data={"user_id": 123})

        # 错误响应
        >>> return business_response(msg="文件不存在", code=40001, status="error")
    """
    if status not in ("success", "error"):
        raise ValueError(f"status 仅支持 success/error，当前值: {status}")

    response = {
        "status": status,
        "msg": msg,
        "code": code
    }
    if data is not None:
        response["data"] = data

    return JSONResponse(response)
