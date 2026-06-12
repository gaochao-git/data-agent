"""
Word Fit App Agent Graph
"""
import os
from langgraph.graph import StateGraph, START, END

from .state import WordFitState
from .nodes import fetch_file_node, resolve_file_node, ocr_node, doc_node, read_text_node, extract_info_node, locate_info_node
from utils.lang_util.conversation import get_checkpointer

def route_file(state: WordFitState):
    """
    根据已解析的文件路径决定处理节点
    """
    file_path = state.get("file_path")
    
    if file_path:
        ext = os.path.splitext(file_path)[1].lower()
        print(f"[DEBUG] route_file: file_path={file_path}, ext={ext}")
        if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp', '.pdf']:
            return "ocr_node"
        if ext in ['.doc', '.docx', '.wps', '.xls', '.xlsx']:
            return "doc_node"
            
    print(f"[DEBUG] route_file: Fallback to read_text_node. file_path={file_path}")
    return "read_text_node"

def route_locate(state: WordFitState):
    """
    根据locate_enabled标志决定是否执行定位节点
    """
    # 确保locate_enabled字段存在，默认为False
    locate_enabled = state.get("locate_enabled", False)
    print(f"[DEBUG] route_locate: locate_enabled={locate_enabled}")
    return "locate_info" if locate_enabled else END

def get_graph(checkpointer=None):
    """构建 Word Fit 工作流图"""
    builder = StateGraph(WordFitState)
    
    # 添加节点
    builder.add_node("fetch_file", fetch_file_node)
    builder.add_node("resolve_file", resolve_file_node)
    builder.add_node("read_text_node", read_text_node)
    builder.add_node("ocr_node", ocr_node)
    builder.add_node("doc_node", doc_node)
    builder.add_node("extract_info", extract_info_node)
    builder.add_node("locate_info", locate_info_node)
    
    # 添加边
    # 1. 获取文件 (Mock 下载)
    builder.add_edge(START, "fetch_file")
    
    # 2. 解析/校验文件路径
    builder.add_edge("fetch_file", "resolve_file")
    
    # 3. 根据文件类型路由到不同的读取节点
    builder.add_conditional_edges(
        "resolve_file",
        route_file,
        {
            "ocr_node": "ocr_node",
            "doc_node": "doc_node",
            "read_text_node": "read_text_node"
        }
    )
    
    # 4. 所有读取节点都连接到提取节点
    builder.add_edge("read_text_node", "extract_info")
    builder.add_edge("ocr_node", "extract_info")
    builder.add_edge("doc_node", "extract_info")
    
    # 5. 根据locate_enabled标志决定是否执行定位节点
    builder.add_conditional_edges(
        "extract_info",
        route_locate,
        {
            "locate_info": "locate_info",
            END: END
        }
    )
    
    # 6. 定位节点连接到结束
    builder.add_edge("locate_info", END)
    
    # 编译图
    return builder.compile(checkpointer=checkpointer)

__all__ = ['get_graph']
