from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from .schema import AskRequest
from .service import ChatAssistantService


router = APIRouter(prefix="/api/chat-assistant", tags=["chat-assistant"])
_service = ChatAssistantService()


@router.post("/ask")
def ask(request: AskRequest):
    if not _service.api_key or not _service.api_url:
        raise HTTPException(status_code=500, detail="未配置对话服务，请联系管理员")
    return StreamingResponse(
        _service.stream(
            question=request.question,
            inputs=request.inputs or {},
            conversation_id=request.conversation_id,
            user=request.user,
        ),
        media_type="text/event-stream",
    )
