"""
Word Fit App Agent States
"""
from typing import Annotated, TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class WordFitState(TypedDict):
    """Word Fit App State"""
    # 消息历史（用于 LangChain 兼容性，虽然本应用主要是单次处理）
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 核心字段
    file_id: str
    file_ids: Optional[List[str]]
    extract_fields: List[Dict[str, Any]]
    locate_enabled: bool  # 是否启用信息定位
    analysis_scene: Optional[str]
    analysis_user_id: Optional[str]
    analysis_match_strategy: Optional[str]
    force: Optional[bool]
    extract_model: Optional[str]
    ocr_model: Optional[str]
    ocr_metrics_batch_id: Optional[str]
    conversation_id: Optional[str]
    base_id: Optional[str]
    
    # 中间状态
    file_path: Optional[str]  # Resolved file path
    file_content: Optional[str]
    extract_prompt_chars: Optional[int]
    skip_extract: Optional[bool]
    skip_extract_reason: Optional[str]
    extraction_result: Optional[Dict[str, Any]]
    info_locations: Optional[Dict[str, List[Dict]]]  # 信息位置
