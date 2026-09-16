"""
统一响应格式封装
提供标准的 API 响应格式，便于前后端统一处理
"""
from http import HTTPStatus

from fastapi import status as http_status
from fastapi.responses import JSONResponse
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from utils.alarm import emit_api_call_alarm
from utils.error_codes import ErrorCode, chatdata_http_exception_business_code, ensure_defined_error_code

SAFE_INTERNAL_ERROR_MESSAGE = "后端服务异常，请稍后重试"

_INTERNAL_ERROR_MARKERS = (
    "traceback",
    "pymysql.",
    "sqlalchemy.",
    "httpx.",
    "openai.",
    "sandbox",
    "can't connect",
    "connection refused",
    "connection timed out",
    "access denied for user",
    "background on this error",
)


def safe_user_error_message(msg) -> str:
    """隐藏内部异常细节，保留普通业务错误文案。"""
    text = str(msg or "")
    lowered = text.lower()
    if any(marker in lowered for marker in _INTERNAL_ERROR_MARKERS):
        return SAFE_INTERNAL_ERROR_MESSAGE
    return text


def business_response(
    data=None,
    msg: str = "操作成功",
    code: int = ErrorCode.SUCCESS,
    status: str = "success",
    http_status_code: int = http_status.HTTP_200_OK,
):
    """
    统一业务响应 (FastAPI 版本)

    Args:
        data: 返回的数据（可选）
        msg: 消息描述（默认 "操作成功"）
        code: 业务状态码（默认 2000）
        status: 状态（支持 "success" / "error"）
        http_status_code: HTTP 状态码（默认 200）

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
        >>> return business_response(msg="文件不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    """
    if status not in ("success", "error"):
        raise ValueError(f"status 仅支持 success/error，当前值: {status}")
    code = ensure_defined_error_code(code)
    if status == "error":
        msg = safe_user_error_message(msg)

    response = {
        "status": status,
        "msg": msg,
        "code": code
    }
    if data is not None:
        response["data"] = data

    return JSONResponse(response, status_code=http_status_code)


def is_chatdata_request(request: Request) -> bool:
    """判断是否是 ChatData 业务接口。"""
    return request.url.path.startswith("/api/chatdata")


async def chatdata_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """统一 ChatData HTTPException 响应格式，非 ChatData 路径走 FastAPI 默认处理。

    显式 raise 的 5xx 属于平台自身接口异常（未捕获异常由 APIAccessLogMiddleware
    兜底告警，这里是它的互补面），统一走 emit_api_call_alarm 告警。
    """
    if exc.status_code >= 500:
        # 5xx 不区分 chatdata 路径，全部告警；响应格式仍按上面的分流处理。
        emit_api_call_alarm(
            request.url.path,
            exc,
            method=request.method,
            ext1={
                "user_id": request.headers.get("X-User-Id") or "",
                "client": request.client.host if request.client else "",
            },
        )
    if not is_chatdata_request(request):
        return await http_exception_handler(request, exc)
    return business_response(
        msg=str(exc.detail or "请求失败"),
        code=chatdata_http_exception_business_code(exc.status_code),
        status="error",
        http_status_code=exc.status_code,
    )


async def chatdata_validation_exception_handler(request: Request, exc: RequestValidationError):
    """统一 ChatData 参数校验失败响应格式，非 ChatData 路径走 FastAPI 默认处理。"""
    if not is_chatdata_request(request):
        return await request_validation_exception_handler(request, exc)
    return business_response(
        msg="请求参数不合法",
        code=ErrorCode.CHATDATA_VALIDATION_FAILED,
        status="error",
        http_status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
    )
