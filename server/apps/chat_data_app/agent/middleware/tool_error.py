"""Tool error formatting middleware."""

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from utils.logger import get_logger

logger = get_logger(__name__)


def format_tool_error(exc: Exception) -> str:
    """格式化工具异常，避免前端出现空白错误信息。"""
    error_text = str(exc).strip() or repr(exc).strip()
    if not error_text:
        error_text = "工具执行失败，请检查参数后重试"
    logger.error("[tools] 工具执行异常: %s", error_text, exc_info=True)
    return error_text


def create_tool_error_middleware():
    """创建工具错误格式化 middleware。"""
    @wrap_tool_call(name="ToolErrorMiddleware")
    async def tool_error_middleware(request, handler):
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            tool_call = request.tool_call or {}
            return ToolMessage(
                content=format_tool_error(exc),
                name=str(tool_call.get("name") or ""),
                tool_call_id=str(tool_call.get("id") or ""),
                status="error",
            )

    return tool_error_middleware
