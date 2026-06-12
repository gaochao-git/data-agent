from pydantic import BaseModel
from typing import Optional, Dict, Any


class AskRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    inputs: Optional[Dict[str, Any]] = None
    user: Optional[str] = None
