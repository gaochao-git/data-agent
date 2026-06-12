"""
Word Fit App Agent Nodes
"""
import asyncio
import os
import json
import shutil
import uuid
from typing import Any
from contextlib import suppress
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig

from utils.logger import get_logger
from utils.lang_util.llm import get_llm
from utils.file_service.service import get_file_path_by_id
from apps.word_fit_app.tools.image_text_extractor_llm import recognize_image
from apps.complaint_app.tools.doc_markdown import convert_doc_to_markdown, replace_images_with_ocr
from apps.complaint_app.services.analysis_agent.match_strategy import normalize_match_strategy
from apps.complaint_app.services.complaint_api_service import build_base_id, build_issue_analysis, ensure_user_id, extract_text_and_save, generate_summary_and_save, resolve_pdf_file
from .state import WordFitState

import httpx
import aiofiles

logger = get_logger(__name__)

_ARCHIVE_EXTENSIONS = {'.zip', '.gz', '.tgz', '.tar', '.bz2', '.xz', '.7z', '.rar'}


def build_wordfit_ocr_hint(extract_fields: list[dict[str, Any]]) -> str:
    """根据字段定义构建 OCR 辅助提示，不改变原文提取目标。"""
    hints: list[str] = []
    for field in extract_fields:
        if not isinstance(field, dict):
            continue
        msgkey = str(field.get("msgkey") or "").strip()
        description = str(field.get("description") or "").strip()
        if msgkey or description:
            hints.append(f"- {msgkey}: {description}")
        items = field.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_key = str(item.get("msgkey") or "").strip()
                item_desc = str(item.get("description") or "").strip()
                if item_key or item_desc:
                    hints.append(f"- {item_key}: {item_desc}")

    if not hints:
        return ""

    hint_text = "\n".join(hints[:60])
    return (
        "核心任务是提取图片中的原文内容，保持原有段落、顺序和换行。\n"
        "不要根据下列字段列表直接输出字段 key-value 结果，不要组织成 JSON、冒号键值对或表单填空结果。\n"
        "字段列表仅作为辅助参考：当遇到明显无关的超长流水表、交易明细、账单对账单时，可适度压缩或忽略无关部分，但不能因此遗漏正文主体内容。\n"
        "只有在整张图片几乎全部是与正文无关的流水/明细表时，才可输出空字符串。\n"
        "辅助字段列表：\n"
        f"{hint_text}"
    )


def get_wordfit_download_dir() -> str:
    """读取 WordFit 下载文件保存目录。"""
    configured = str(os.getenv("WORDFIT_DOWNLOAD_DIR") or "").strip()
    if configured:
        path = os.path.expanduser(configured)
        if not os.path.isabs(path):
            path = os.path.join(os.getcwd(), path)
        return path
    return os.path.join(os.getcwd(), "server", "uploads") if os.path.exists("server") else os.path.join(os.getcwd(), "uploads")


def should_trigger_complaint_analysis(state: WordFitState) -> bool:
    """判断是否需要触发举报事项分析预热。"""
    return bool(str(state.get("analysis_scene") or "").strip())


async def preheat_complaint_analysis(file_id: str, analysis_user_id: str, analysis_match_strategy: str | None = None) -> None:
    """后台触发举报事项分析主链路。"""
    try:
        user_id = ensure_user_id(analysis_user_id)
        base_id = build_base_id(str(file_id or ""))
        match_strategy = normalize_match_strategy(analysis_match_strategy)
        original_path = await resolve_pdf_file(str(file_id or ""), user_id, base_id, force=False)
        text_content, text_version = await extract_text_and_save(user_id, base_id, original_path, force=False)
        summary_text, summary_version = await generate_summary_and_save(user_id, base_id, text_content, text_version, force=False)
        await build_issue_analysis(user_id, base_id, summary_text, text_version, summary_version, force=False, match_strategy=match_strategy, file_id=str(file_id or ""))
        logger.info("[WordFit] complaint-analysis preheat completed: file_id=%s user_id=%s base_id=%s", file_id, user_id, base_id)
    except Exception as exc:
        logger.error("[WordFit] complaint-analysis preheat failed: file_id=%s user_id=%s err=%s", file_id, analysis_user_id, exc)


def trigger_complaint_analysis_preheat(state: WordFitState) -> None:
    """异步启动举报事项分析预热。"""
    if not should_trigger_complaint_analysis(state):
        return

    file_id = str(state.get("file_id") or "").strip()
    analysis_user_id = str(state.get("analysis_user_id") or "").strip()
    analysis_match_strategy = str(state.get("analysis_match_strategy") or "").strip() or None
    if not file_id or not analysis_user_id:
        logger.warning("[WordFit] skip complaint-analysis preheat because file_id or analysis_user_id is empty: file_id=%s", file_id)
        return
    asyncio.create_task(preheat_complaint_analysis(file_id, analysis_user_id, analysis_match_strategy))

async def resolve_file_node(state: WordFitState) -> dict:
    """
    文件路径校验与解析节点
    
    1. 接收上游 fetch_file_node 传递的 file_path (暂存路径)
    2. 如果该路径无效（如 mock 下载失败），尝试使用 DB 或其他方式查找
    3. 确认最终的 file_path
    """
    file_id = state.get("file_id")
    file_path = state.get("file_path")
    
    logger.info(f"[WordFit] resolve_file_node input: file_id={file_id}, file_path={file_path}")
    
    # 校验上游传入的路径是否存在
    if file_path and os.path.exists(file_path):
        return {"file_path": file_path}
    
    # 如果不存在，尝试使用原有的 DB/Mock 查找逻辑作为兜底
    
    # 1. 尝试 DB 查找
    try:
        db_path = await get_file_path_by_id(file_id)
        if db_path and os.path.exists(db_path):
            logger.info(f"[WordFit] Resolved from DB: {db_path}")
            return {"file_path": db_path}
    except Exception as e:
        logger.warning(f"[WordFit] DB lookup failed: {e}")

    # 2. 尝试文件系统查找 (Mock)
    try:
        # 适配 CWD 不同情况 (root vs server)
        upload_dir = "server/uploads" if os.path.exists("server/uploads") else "uploads"
        mock_path = os.path.join(upload_dir, file_id)
        
        if os.path.exists(mock_path):
            logger.info(f"[WordFit] Resolved from uploads/file_id: {mock_path}")
            return {"file_path": mock_path}
            
        if os.path.exists(file_id):
            logger.info(f"[WordFit] Resolved from CWD/file_id: {file_id}")
            return {"file_path": file_id}
            
    except Exception as e:
        logger.error(f"[WordFit] Error resolving file from FS: {e}")
    
    # 如果都找不到，返回 None，后续节点会处理
    logger.warning(f"[WordFit] Failed to resolve file for id: {file_id} in {os.getcwd()}")
    return {"file_path": None}


async def fetch_file_node(state: WordFitState) -> dict:
    """
    获取文件节点 (Download from External API)
    
    调用外部 HTTP 接口下载文件并暂存到本地
    """
    file_id = state.get("file_id")
    if not file_id:
        logger.error("[WordFit] No file_id provided for fetch_file_node")
        return {"file_path": None}

    # 读取配置，默认为 http://localhost:8000/download
    # 建议在 .env 中配置 FILE_DOWNLOAD_API_URL
    api_url = os.getenv("FILE_DOWNLOAD_API_URL", "http://localhost:8000/download")
    api_user = os.getenv("FILE_DOWNLOAD_USER", "user")
    api_token = os.getenv("FILE_DOWNLOAD_TOKEN", "123456")
    api_cluserList = os.getenv("FILE_DOWNLOAD_CLUSTER", "12")

    # 构造保存路径
    save_dir = get_wordfit_download_dir()
    os.makedirs(save_dir, exist_ok=True)
    # 假设 file_id 即为文件名，但可能包含路径 (如 /data/upload/...)
    # 需要去除开头的 / 以避免 os.path.join 拼出绝对路径导致保存位置错误
    safe_file_id = file_id.lstrip("/")
    file_path = os.path.join(save_dir, safe_file_id)
    
    # 确保父目录存在，防止 FileNotFoundError
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    logger.info(f"[WordFit] Fetching file from {api_url} with fileName={file_id}, saving to {file_path}")
    try:
        async with httpx.AsyncClient(verify=False) as client:
            # 传入参数为 fileName
            params_fd={"filePath": file_id, "user": api_user,"clusterList": api_cluserList,"token": api_token}
            response = await client.get(api_url, params=params_fd, timeout=30.0)
            response.raise_for_status()
            
            # 写入文件
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(response.content)
                
        logger.info(f"[WordFit] File saved to: {file_path}")
        return {"file_path": file_path}
    except Exception as e:
        logger.error(f"[WordFit] Error fetching file: {e}")
        # 如果下载失败，返回 None，后续 resolve_file_node 可能会处理兜底逻辑
        return {"file_path": None}


async def read_text_node(state: WordFitState) -> dict:
    """
    文本读取节点 (原 fetch_file_node 的读取逻辑)
    """
    file_path = state.get("file_path")
    if not file_path or not os.path.exists(file_path):
         return {"file_content": f"File not found: {file_path}"}

    ext = os.path.splitext(file_path)[1].lower()
    if ext in _ARCHIVE_EXTENSIONS:
        logger.info("[WordFit] Skip extract for archive file: %s", file_path)
        return {"file_content": "", "skip_extract": True, "skip_extract_reason": f"archive:{ext}"}

    content = ""
    try:
        with open(file_path, 'rb') as f:
            sample = f.read(4096)
        if b'\x00' in sample:
            logger.info("[WordFit] Skip extract for binary file: %s", file_path)
            return {"file_content": "", "skip_extract": True, "skip_extract_reason": "binary:null-byte"}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
         logger.info("[WordFit] Skip extract for non-text file: %s", file_path)
         return {"file_content": "", "skip_extract": True, "skip_extract_reason": "binary:utf8-decode-failed"}

    return {"file_content": content}


def build_empty_extract_result(extract_fields: list[dict[str, Any]]) -> dict[str, Any]:
    """按字段定义构造空结果。"""
    result: dict[str, Any] = {}
    for field in extract_fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("msgkey") or "").strip()
        if not key:
            continue
        if str(field.get("type") or "").strip() == "array":
            result[key] = []
        else:
            result[key] = ""
    return result


async def ocr_node(state: WordFitState, config: RunnableConfig | None = None) -> dict:
    """
    OCR 节点：读取图片类型文件中的文本内容
    支持 PDF (直接调用 OCR)
    """
    file_path = state.get("file_path")
    extract_fields = state.get("extract_fields", [])
    if not file_path or not os.path.exists(file_path):
         return {"file_content": "File path not resolved or file not found."}

    try:
        # 直接调用 OCR 处理
        logger.info(f"[WordFit] OCR processing file: {file_path}")
        ocr_hint = build_wordfit_ocr_hint(extract_fields)
        progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress_callback(current: int, total: int) -> None:
            loop.call_soon_threadsafe(progress_queue.put_nowait, {"current": current, "total": total})

        recognize_task = asyncio.create_task(asyncio.to_thread(recognize_image, file_path, False, ocr_hint, progress_callback))

        while True:
            if recognize_task.done() and progress_queue.empty():
                break
            try:
                progress = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            await adispatch_custom_event(
                "word_fit_ocr_progress",
                {
                    "type": "node",
                    "status": "progress",
                    "node": "ocr_node",
                    "node_name": "OCR识别",
                    "content": f"2/4 正在图像识别文件内容...（{progress['current']}/{progress['total']}）",
                    "current": progress["current"],
                    "total": progress["total"],
                },
                config=config,
            )

        result = await recognize_task
        content = result.get("text_content", "")
        return {"file_content": content}
    except Exception as e:
        logger.error(f"[WordFit] OCR Error: {e}")
        return {"file_content": f"OCR failed: {str(e)}"}


async def doc_node(state: WordFitState) -> dict:
    """
    文档节点：读取 doc, docx, wps, xls 等文档类型文件内容
    """
    file_path = state.get("file_path")
    if not file_path or not os.path.exists(file_path):
         return {"file_content": "File path not resolved or file not found."}

    try:
        ext = os.path.splitext(file_path)[1].lower()
        content = ""

        if ext in ['.doc', '.docx', '.wps']:
            # 使用 doc_markdown 工具
            temp_dir = os.path.join("temp", f"doc_convert_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
            try:
                markdown_path, media_dir = convert_doc_to_markdown(file_path, temp_dir)
                with open(markdown_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            finally:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

        elif ext in ['.xls', '.xlsx']:
            # 使用 openpyxl 读取 Excel
            from openpyxl import load_workbook
            try:
                wb = load_workbook(file_path, data_only=True)
                lines = []
                for sheet in wb.worksheets:
                    lines.append(f"Sheet: {sheet.title}")
                    for row in sheet.iter_rows(values_only=True):
                        row_text = " | ".join([str(cell) for cell in row if cell is not None])
                        if row_text.strip():
                            lines.append(row_text)
                    lines.append("\n")
                content = "\n".join(lines)
            except Exception as exc:
                logger.error(f"[WordFit] Excel read error: {exc}")
                content = f"Error reading Excel file: {exc}"
        else:
             # 其他类型尝试直接读取
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                content = f"Unsupported file type for doc_node: {ext}"

        return {"file_content": content}

    except Exception as e:
        logger.error(f"[WordFit] Doc read error: {e}")
        return {"file_content": f"Doc read failed: {str(e)}"}


async def extract_info_node(state: WordFitState) -> dict:
    """
    信息提取节点
    
    调用 LLM 根据 extract_fields 提取信息
    """
    file_content = state.get("file_content", "")
    extract_fields = state.get("extract_fields", [])
    skip_extract = bool(state.get("skip_extract"))
    skip_extract_reason = str(state.get("skip_extract_reason") or "").strip()
    file_path = str(state.get("file_path") or "").strip()
    
    if skip_extract:
        logger.info("[WordFit] Skip extract triggered: file_path=%s reason=%s", file_path, skip_extract_reason or "unknown")
        empty_result = build_empty_extract_result(extract_fields)
        return {"messages": [AIMessage(content=json.dumps(empty_result, ensure_ascii=False))]}

    if not file_content:
        return {"messages": [AIMessage(content="无法读取文件内容，提取失败。")]}

    trigger_complaint_analysis_preheat(state)

    # 构建 Prompt
    fields_desc = json.dumps(extract_fields, ensure_ascii=False, indent=2)
    
    system_prompt = '''你是一个智能信息提取助手。你的任务是从给定的文本内容中，提取指定的信息字段。

请严格按照要求的字段列表进行提取，并以 JSON 格式输出。
字段列表格式如下：
[
    {{"msgkey": "name", "description": "举报人姓名"}},
    {{"msgkey": "amount", "description": "索赔金额"}},
    {{"msgkey":"agentList", "description": "被诉人列表", "type": "array", 
    "items": [{{"msgkey": "agent_name", "description": "被举报人姓名"}},
    {{"msgkey": "agent_address", "description": "被举报人地址"}}]}}
    ...
]
提取要求：
1.根据description含义在文本中进行提取相应内容，每个字段只提取一次。
2.提取内容需要准确匹配description含义，不能有任何偏差，不要联想或编造信息。
3.如果字段是列表格式，代表文本中可能有多个实例需要提取。
4.非列表格式的字段，文本内容包含多个实例的，仅提取第一个实例。
4.如果某个字段在文中未找到，返回空字符串。
5.输出格式为 JSON 字符串，键为 msgkey，值为提取到的内容。
6.如果字段表示“收件日期”，优先提取收件章、收文章、登记章、签收章等盖章日期，不要提取举报件、申请书、说明材料等正文末尾的落款日期。
输出示例：
{{
    "name": "张三", 
    "amount": "", 
    "agentList": [
        {{ "agent_name": "李四", "agent_address": "地址A" }},
        {{ "agent_name": "王五", "agent_address": "地址B" }}
    ]
}}


需要提取的字段定义如下：
{fields_desc}
'''

    human_prompt = '''文件内容如下：
{file_content}

请提取上述字段，直接返回 JSON 数据。
'''

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", human_prompt)
    ])
    
    # 格式化 Prompt
    messages = prompt.format_messages(
        fields_desc=fields_desc,
        file_content=file_content[:50000] # 截断防止 token 溢出
    )
    
    # 调用 LLM
    llm = get_llm(streaming=True)
    response = await llm.ainvoke(messages)
    
    # 尝试解析 JSON (可选，如果后续流程需要结构化数据)
    # 这里直接返回 LLM 的输出消息，因为它会流式输出给前端
    
    return {"messages": [response]}


async def locate_info_node(state: WordFitState) -> dict:
    """
    信息位置定位节点
    
    仅对 PDF 文件执行位置定位，非 PDF 文件直接返回空结果
    找到每个信息在 PDF 文件中的具体位置，返回页码与坐标
    """
    file_path = state.get("file_path")
    file_content = state.get("file_content", "")
    messages = state.get("messages", [])
    extract_fields = state.get("extract_fields", [])
    
    # 判断文件类型
    if not file_path or not file_path.lower().endswith('.pdf'):
        return {"info_locations": {}}
    
    # 解析提取结果
    extracted_info = {}
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            try:
                # 尝试解析 AIMessage 的内容为 JSON
                content = msg.content
                # 去除可能的前后引号
                if content.startswith('```json') and content.endswith('```'):
                    content = content[7:-3].strip()
                elif content.startswith('```') and content.endswith('```'):
                    content = content[3:-3].strip()
                extraction_result = json.loads(content)
                
                # 处理提取结果，构建待定位信息
                def process_extracted_data(data, prefix=""):
                    if isinstance(data, dict):
                        for key, value in data.items():
                            if isinstance(value, (dict, list)):
                                process_extracted_data(value, key)
                            elif isinstance(value, str) and value:
                                extracted_info[key] = value
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                for key, value in item.items():
                                    if isinstance(value, str) and value:
                                        extracted_info[value] = value
                
                process_extracted_data(extraction_result)
                break
            except Exception as e:
                logger.error(f"[WordFit] 解析提取结果失败: {e}")
                continue
    
    if not extracted_info:
        return {"info_locations": {}}
    
    # 构建 Prompt - 使用原始 PDF 文件进行定位
    system_prompt = '''你是一个精准的 PDF 文本定位助手。你的任务是在给定的 PDF 文件中，找到指定信息的具体位置。

定位要求：
1. 精确匹配：找到与给定值完全匹配的文本
2. 上下文理解：考虑上下文含义，避免误匹配
3. 详细位置：提供页码和坐标信息
4. 置信度评估：评估位置匹配的置信度（0-1）
5. 多实例处理：如果同一信息出现多次，返回所有位置
6. 未找到处理：如果某个信息未找到，不要在输出中包含该信息

输入：
- PDF 文件路径：{file_path}
- 待定位信息：{{
  "字段名": "提取值",
  ...
}}

输出格式（JSON）：
{{
  "提取值1": [
    {{
      "value": "提取值1",
      "page": 页码,
      "x": x坐标,
      "y": y坐标,
      "width": 宽度,
      "height": 高度,
      "confidence": 置信度
    }},
    ...
  ],
  "提取值2": [
    {{
      "value": "提取值2",
      "page": 页码,
      "x": x坐标,
      "y": y坐标,
      "width": 宽度,
      "height": 高度,
      "confidence": 置信度
    }},
    ...
  ],
  ...
}}
'''

    human_prompt = '''请分析 PDF 文件并定位以下信息：
{extracted_info}

PDF 文件路径：{file_path}

请返回每个信息在 PDF 中的具体位置，直接返回 JSON 数据。
'''

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", human_prompt)
    ])
    
    # 格式化 Prompt
    messages_prompt = prompt.format_messages(
        file_path=file_path,
        extracted_info=json.dumps(extracted_info, ensure_ascii=False, indent=2)
    )
    
    # 调用 LLM - 假设 get_llm() 返回的模型支持处理 PDF 文件（如 Qwen2.5-VL）
    info_locations = {}
    try:
        llm = get_llm(streaming=True)
        response = await llm.ainvoke(messages_prompt)
        
        # 解析 LLM 返回的位置信息
        content = response.content
        if content.startswith('```json') and content.endswith('```'):
            content = content[7:-3].strip()
        elif content.startswith('```') and content.endswith('```'):
            content = content[3:-3].strip()
        
        info_locations = json.loads(content)
    except Exception as e:
        logger.error(f"[WordFit] 定位信息失败: {e}")
    
    return {"info_locations": info_locations}
