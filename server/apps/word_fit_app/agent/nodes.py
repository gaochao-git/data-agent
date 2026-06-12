"""
Word Fit App Agent Nodes
"""
import asyncio
import os
import json
import math
import shutil
import time
import uuid
from typing import Any, Awaitable, Callable
from contextlib import suppress
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig

from utils.logger import get_logger
from utils.lang_util.llm import get_llm
from utils.llm_prompt_metrics import record_llm_output_metrics, record_llm_prompt_metrics
from utils.file_service.service import get_file_path_by_id
from apps.word_fit_app.tools.image_text_extractor_llm import WORDFIT_OCR_MAX_WAIT_SECONDS, WORDFIT_OCR_PDF_MAX_WORKERS, recognize_image, recognize_pdf_files_concurrently
from apps.complaint_app.tools.doc_markdown import convert_doc_to_markdown, replace_images_with_ocr
from apps.complaint_app.services.analysis_agent.match_strategy import MATCH_STRATEGY_LAW_SKILL, normalize_match_strategy
from apps.complaint_app.services.complaint_api_service import build_base_id, build_base_id_for_file_ids, build_issue_analysis, ensure_user_id, extract_text_and_save, extract_texts_and_save, generate_summary_and_save, resolve_pdf_file, save_reused_text_content
from utils.storage_paths import resolve_complaint_scope
from .state import WordFitState

import httpx
import aiofiles
import fitz

logger = get_logger(__name__)

_ARCHIVE_EXTENSIONS = {'.zip', '.gz', '.tgz', '.tar', '.bz2', '.xz', '.7z', '.rar'}
_OCR_MEDIA_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp', '.pdf'}
OcrEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def is_wordfit_llm_request_log_enabled() -> bool:
    """是否打印字段提取大模型完整请求消息。"""
    return str(os.getenv("WORDFIT_LLM_REQUEST_LOG_DEBUG") or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def build_ocr_countdown(file_path: str) -> tuple[int, str]:
    """计算 OCR 倒计时总秒数，并返回前端展示说明。"""
    single_wait_seconds = max(0, math.ceil(WORDFIT_OCR_MAX_WAIT_SECONDS))
    if single_wait_seconds <= 0:
        return 0, ""

    if not str(file_path or "").lower().endswith(".pdf"):
        return single_wait_seconds, f"共1页，预计完成时间：{single_wait_seconds}秒"

    doc = fitz.open(file_path)
    try:
        page_count = len(doc)
    finally:
        doc.close()

    max_workers = min(page_count, WORDFIT_OCR_PDF_MAX_WORKERS) if page_count > 0 else 1
    rounds = math.ceil(page_count / max_workers) if page_count > 0 else 1
    countdown_total = rounds * single_wait_seconds
    message = f"共{page_count}页，预计完成时间：{countdown_total}秒"
    return countdown_total, message


def build_pdf_paths_ocr_countdown(file_paths: list[str]) -> tuple[int, str]:
    """计算多个 PDF 共用并发池时的 OCR 倒计时说明。"""
    single_wait_seconds = max(0, math.ceil(WORDFIT_OCR_MAX_WAIT_SECONDS))
    if single_wait_seconds <= 0:
        return 0, ""

    page_count = 0
    for file_path in file_paths:
        doc = fitz.open(file_path)
        try:
            page_count += len(doc)
        finally:
            doc.close()

    max_workers = min(page_count, WORDFIT_OCR_PDF_MAX_WORKERS) if page_count > 0 else 1
    rounds = math.ceil(page_count / max_workers) if page_count > 0 else 1
    countdown_total = rounds * single_wait_seconds
    return countdown_total, f"共{page_count}页，预计完成时间：{countdown_total}秒"


def format_ocr_countdown_message(message: str, remaining_seconds: int) -> str:
    """按当前剩余秒数刷新 OCR 倒计时文案。"""
    marker = "预计完成时间："
    if marker not in message:
        return message
    prefix = message.split(marker, 1)[0]
    return f"{prefix}{marker}{remaining_seconds}秒"


def resolve_wordfit_extract_model(request_model: str | None) -> str | None:
    """解析 WordFit 字段提取模型，优先使用请求级覆盖。"""
    model = str(request_model or "").strip()
    if model:
        return model
    return str(os.getenv("WORDFIT_EXTRACT_MODEL") or "").strip() or None


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


def build_wordfit_download_path(file_id: str, conversation_id: str | None = None) -> str:
    """按会话隔离下载文件路径，避免同文件并发写入冲突。"""
    save_dir = get_wordfit_download_dir()
    safe_file_id = str(file_id or "").lstrip("/")
    safe_conversation_id = str(conversation_id or uuid.uuid4()).strip() or str(uuid.uuid4())
    return os.path.join(save_dir, "runs", safe_conversation_id, safe_file_id)


def should_trigger_complaint_analysis(state: WordFitState) -> bool:
    """判断是否需要触发举报事项分析预热。"""
    return False


async def preheat_complaint_analysis(file_id: str, analysis_user_id: str, analysis_match_strategy: str | None = None, ocr_model: str | None = None, preheat_id: str = "", source_request_id: str = "") -> None:
    """后台触发举报事项分析主链路。"""
    preheat_id = preheat_id or uuid.uuid4().hex[:8]
    try:
        user_id = ensure_user_id(analysis_user_id)
        base_id = build_base_id(str(file_id or ""))
        match_strategy = normalize_match_strategy(analysis_match_strategy, default_value=MATCH_STRATEGY_LAW_SKILL)
        logger.info("[WordFitPreheat] start preheat_id=%s source_request_id=%s mode=single user_id=%s base_id=%s file_id=%s match_strategy=%s ocr_model=%s", preheat_id, source_request_id, user_id, base_id, file_id, match_strategy, ocr_model or "")
        original_path = await resolve_pdf_file(str(file_id or ""), user_id, base_id, force=False)
        logger.info("[WordFitPreheat] file_done preheat_id=%s base_id=%s file_id=%s path=%s", preheat_id, base_id, file_id, original_path)
        text_content, text_version = await extract_text_and_save(user_id, base_id, original_path, force=False, ocr_model=ocr_model)
        logger.info("[WordFitPreheat] text_done preheat_id=%s base_id=%s text_version=%s text_chars=%s", preheat_id, base_id, text_version, len(text_content or ""))
        summary_text, summary_version = await generate_summary_and_save(user_id, base_id, text_content, text_version, force=False)
        logger.info("[WordFitPreheat] summary_done preheat_id=%s base_id=%s summary_version=%s summary_chars=%s", preheat_id, base_id, summary_version, len(summary_text or ""))
        _issue_result, issue_version = await build_issue_analysis(user_id, base_id, summary_text, text_version, summary_version, force=False, match_strategy=match_strategy, file_id=str(file_id or ""))
        logger.info("[WordFitPreheat] completed preheat_id=%s source_request_id=%s mode=single user_id=%s base_id=%s file_id=%s issue_version=%s", preheat_id, source_request_id, user_id, base_id, file_id, issue_version)
    except Exception as exc:
        logger.error("[WordFitPreheat] failed preheat_id=%s source_request_id=%s mode=single file_id=%s user_id=%s err=%s", preheat_id, source_request_id, file_id, analysis_user_id, exc)


async def preheat_complaint_analysis_for_file_ids(file_ids: list[str], analysis_user_id: str, analysis_match_strategy: str | None = None, ocr_model: str | None = None, preheat_id: str = "", source_request_id: str = "") -> None:
    """后台触发多文件合案举报事项分析预热。"""
    preheat_id = preheat_id or uuid.uuid4().hex[:8]
    try:
        user_id = ensure_user_id(analysis_user_id)
        base_id = build_base_id_for_file_ids(file_ids)
        match_strategy = normalize_match_strategy(analysis_match_strategy)
        logger.info("[WordFitPreheat] start preheat_id=%s source_request_id=%s mode=multi user_id=%s base_id=%s file_count=%s file_ids=%s match_strategy=%s ocr_model=%s", preheat_id, source_request_id, user_id, base_id, len(file_ids), file_ids, match_strategy, ocr_model or "")
        original_files = []
        for index, file_id in enumerate(file_ids, start=1):
            original_path = await resolve_pdf_file(str(file_id or ""), user_id, base_id, force=False)
            original_files.append((str(file_id or ""), original_path))
            logger.info("[WordFitPreheat] file_done preheat_id=%s base_id=%s file_index=%s file_id=%s path=%s", preheat_id, base_id, index, file_id, original_path)
        text_content, text_version = await extract_texts_and_save(user_id, base_id, original_files, force=False, ocr_model=ocr_model)
        logger.info("[WordFitPreheat] text_done preheat_id=%s base_id=%s text_version=%s text_chars=%s", preheat_id, base_id, text_version, len(text_content or ""))
        summary_text, summary_version = await generate_summary_and_save(user_id, base_id, text_content, text_version, force=False)
        logger.info("[WordFitPreheat] summary_done preheat_id=%s base_id=%s summary_version=%s summary_chars=%s", preheat_id, base_id, summary_version, len(summary_text or ""))
        _issue_result, issue_version = await build_issue_analysis(user_id, base_id, summary_text, text_version, summary_version, force=False, match_strategy=match_strategy, file_id=",".join(file_ids))
        logger.info("[WordFitPreheat] completed preheat_id=%s source_request_id=%s mode=multi user_id=%s base_id=%s file_count=%s file_ids=%s issue_version=%s", preheat_id, source_request_id, user_id, base_id, len(file_ids), file_ids, issue_version)
    except Exception as exc:
        logger.error("[WordFitPreheat] failed preheat_id=%s source_request_id=%s mode=multi file_ids=%s user_id=%s err=%s", preheat_id, source_request_id, file_ids, analysis_user_id, exc)


async def save_wordfit_text_for_complaint_preheat(user_id: str, base_id: str, state: WordFitState, file_ids: list[str]) -> None:
    """保存字段提取 OCR 文本，供举报分析预热复用。"""
    text_content = str(state.get("file_content") or "").strip()
    if not text_content:
        return
    source_request_id = str(state.get("conversation_id") or "").strip()
    scope_context = resolve_complaint_scope("default")
    try:
        _text, text_version = await save_reused_text_content(user_id, base_id, text_content, file_ids=file_ids, source_request_id=source_request_id, scope_context=scope_context)
        logger.info("[WordFitPreheat] reused_text_saved user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s text_version=%s text_chars=%s source_request_id=%s", user_id, base_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, text_version, len(text_content), source_request_id)
    except Exception as exc:
        logger.error("[WordFitPreheat] reused_text_save_failed user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file_ids=%s err=%s", user_id, base_id, scope_context.scope, scope_context.org_id, scope_context.entity_id, file_ids, exc)


async def persist_complaint_text_cache_from_wordfit(state: WordFitState) -> None:
    """保存字段提取文本缓存，不触发举报事项分析。"""
    file_ids = [str(value or "").strip() for value in state.get("file_ids") or [] if str(value or "").strip()]
    file_id = str(state.get("file_id") or "").strip()
    analysis_user_id = str(state.get("analysis_user_id") or "").strip()
    state_base_id = str(state.get("base_id") or "").strip()
    if not analysis_user_id or not str(state.get("file_content") or "").strip():
        return
    try:
        user_id = ensure_user_id(analysis_user_id)
        if len(file_ids) > 1:
            base_id = build_base_id_for_file_ids(file_ids)
            await save_wordfit_text_for_complaint_preheat(user_id, base_id, state, file_ids)
            return
        if not file_id:
            logger.warning("[WordFitPreheat] skip reused_text_save because file_id is empty user_id=%s base_id=%s", analysis_user_id, state_base_id)
            return
        base_id = build_base_id(file_id)
        await save_wordfit_text_for_complaint_preheat(user_id, base_id, state, [file_id])
    except Exception as exc:
        logger.error("[WordFitPreheat] reused_text_prepare_failed user_id=%s base_id=%s file_id=%s file_ids=%s err=%s", analysis_user_id, state_base_id, file_id, file_ids, exc)


async def trigger_complaint_analysis_preheat(state: WordFitState) -> None:
    """异步启动举报事项分析预热。"""
    if not should_trigger_complaint_analysis(state):
        return

    file_ids = [str(value or "").strip() for value in state.get("file_ids") or [] if str(value or "").strip()]
    file_id = str(state.get("file_id") or "").strip()
    analysis_user_id = str(state.get("analysis_user_id") or "").strip()
    analysis_match_strategy = str(state.get("analysis_match_strategy") or "").strip() or None
    ocr_model = str(state.get("ocr_model") or "").strip() or None
    source_request_id = str(state.get("conversation_id") or "").strip()
    state_base_id = str(state.get("base_id") or "").strip()
    preheat_id = uuid.uuid4().hex[:8]
    if len(file_ids) > 1:
        if not analysis_user_id:
            logger.warning("[WordFit] skip complaint-analysis preheat because analysis_user_id is empty: user_id=%s base_id=%s file_ids=%s", analysis_user_id, state_base_id, file_ids)
            return
        try:
            user_id = ensure_user_id(analysis_user_id)
            base_id = build_base_id_for_file_ids(file_ids)
        except Exception as exc:
            logger.error("[WordFitPreheat] skip complaint-analysis preheat because context invalid: file_ids=%s user_id=%s base_id=%s err=%s", file_ids, analysis_user_id, state_base_id, exc)
            return
        await save_wordfit_text_for_complaint_preheat(user_id, base_id, state, file_ids)
        logger.info("[WordFitPreheat] scheduled preheat_id=%s source_request_id=%s mode=multi user_id=%s base_id=%s file_count=%s file_ids=%s match_strategy=%s ocr_model=%s", preheat_id, source_request_id, analysis_user_id, base_id, len(file_ids), file_ids, analysis_match_strategy or "", ocr_model or "")
        asyncio.create_task(preheat_complaint_analysis_for_file_ids(file_ids, analysis_user_id, analysis_match_strategy, ocr_model, preheat_id, source_request_id))
        return
    if not file_id or not analysis_user_id:
        logger.warning("[WordFit] skip complaint-analysis preheat because file_id or analysis_user_id is empty: user_id=%s base_id=%s file_id=%s", analysis_user_id, state_base_id, file_id)
        return
    try:
        user_id = ensure_user_id(analysis_user_id)
        base_id = build_base_id(str(file_id or ""))
    except Exception as exc:
        logger.error("[WordFitPreheat] skip complaint-analysis preheat because context invalid: file_id=%s user_id=%s base_id=%s err=%s", file_id, analysis_user_id, state_base_id, exc)
        return
    await save_wordfit_text_for_complaint_preheat(user_id, base_id, state, [file_id])
    logger.info("[WordFitPreheat] scheduled preheat_id=%s source_request_id=%s mode=single user_id=%s base_id=%s file_id=%s match_strategy=%s ocr_model=%s", preheat_id, source_request_id, analysis_user_id, base_id, file_id, analysis_match_strategy or "", ocr_model or "")
    asyncio.create_task(preheat_complaint_analysis(file_id, analysis_user_id, analysis_match_strategy, ocr_model, preheat_id, source_request_id))

async def resolve_file_node(state: WordFitState) -> dict:
    """
    文件路径校验与解析节点
    
    1. 接收上游 fetch_file_node 传递的 file_path (暂存路径)
    2. 如果该路径无效（如 mock 下载失败），尝试使用 DB 或其他方式查找
    3. 确认最终的 file_path
    """
    file_id = state.get("file_id")
    file_path = state.get("file_path")
    base_id = str(state.get("base_id") or "").strip()
    user_id = str(state.get("analysis_user_id") or "").strip()
    
    logger.info("[WordFit] resolve_file_node input user_id=%s base_id=%s file_id=%s file_path=%s", user_id, base_id, file_id, file_path)
    
    # 校验上游传入的路径是否存在
    if file_path and os.path.exists(file_path):
        return {"file_path": file_path}
    
    # 如果不存在，尝试使用原有的 DB/Mock 查找逻辑作为兜底
    
    # 1. 尝试 DB 查找
    try:
        db_path = await get_file_path_by_id(file_id)
        if db_path and os.path.exists(db_path):
            logger.info("[WordFit] Resolved from DB user_id=%s base_id=%s file_id=%s file_path=%s", user_id, base_id, file_id, db_path)
            return {"file_path": db_path}
    except Exception as e:
        logger.warning("[WordFit] DB lookup failed user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, file_id, e)

    # 2. 尝试文件系统查找 (Mock)
    try:
        # 适配 CWD 不同情况 (root vs server)
        upload_dir = "server/uploads" if os.path.exists("server/uploads") else "uploads"
        mock_path = os.path.join(upload_dir, file_id)
        
        if os.path.exists(mock_path):
            logger.info("[WordFit] Resolved from uploads/file_id user_id=%s base_id=%s file_id=%s file_path=%s", user_id, base_id, file_id, mock_path)
            return {"file_path": mock_path}
            
        if os.path.exists(file_id):
            logger.info("[WordFit] Resolved from CWD/file_id user_id=%s base_id=%s file_id=%s", user_id, base_id, file_id)
            return {"file_path": file_id}
            
    except Exception as e:
        logger.error("[WordFit] Error resolving file from FS user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, file_id, e)
    
    # 如果都找不到，返回 None，后续节点会处理
    logger.warning("[WordFit] Failed to resolve file user_id=%s base_id=%s file_id=%s cwd=%s", user_id, base_id, file_id, os.getcwd())
    return {"file_path": None}


async def fetch_file_node(state: WordFitState) -> dict:
    """
    获取文件节点 (Download from External API)
    
    调用外部 HTTP 接口下载文件并暂存到本地
    """
    file_id = state.get("file_id")
    base_id = str(state.get("base_id") or "").strip()
    user_id = str(state.get("analysis_user_id") or "").strip()
    if not file_id:
        logger.error("[WordFit] No file_id provided for fetch_file_node user_id=%s base_id=%s", user_id, base_id)
        return {"file_path": None}

    # 读取配置，默认为 http://localhost:8000/download
    # 建议在 .env 中配置 FILE_DOWNLOAD_API_URL
    api_url = os.getenv("FILE_DOWNLOAD_API_URL", "http://localhost:8000/download")
    api_user = os.getenv("FILE_DOWNLOAD_USER", "user")
    api_token = os.getenv("FILE_DOWNLOAD_TOKEN", "123456")
    api_cluserList = os.getenv("FILE_DOWNLOAD_CLUSTER", "12")

    file_path = build_wordfit_download_path(str(file_id), str(state.get("conversation_id") or "").strip() or None)
    
    # 确保父目录存在，防止 FileNotFoundError
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    logger.info("[WordFit] Fetching file user_id=%s base_id=%s file_id=%s api_url=%s save_path=%s", user_id, base_id, file_id, api_url, file_path)
    try:
        async with httpx.AsyncClient(verify=False) as client:
            # 传入参数为 fileName
            params_fd={"filePath": file_id, "user": api_user,"clusterList": api_cluserList,"token": api_token}
            response = await client.get(api_url, params=params_fd, timeout=30.0)
            response.raise_for_status()
            
            # 写入文件
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(response.content)
                
        logger.info("[WordFit] File saved user_id=%s base_id=%s file_id=%s file_path=%s", user_id, base_id, file_id, file_path)
        return {"file_path": file_path}
    except Exception as e:
        logger.error("[WordFit] Error fetching file user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, file_id, e)
        # 如果下载失败，返回 None，后续 resolve_file_node 可能会处理兜底逻辑
        return {"file_path": None}


async def build_file_content_for_extract(file_id: str, base_state: dict[str, Any], ocr_event_callback: OcrEventCallback | None = None) -> str:
    """复用现有节点读取单个文件内容，供多文件合并提取使用。"""
    state = dict(base_state)
    state["file_id"] = file_id
    state["file_path"] = None
    state["file_content"] = ""
    fetched = await fetch_file_node(state)
    state.update(fetched)
    resolved = await resolve_file_node(state)
    state.update(resolved)
    file_path = state.get("file_path")
    ext = os.path.splitext(str(file_path or ""))[1].lower()
    if ext in _OCR_MEDIA_EXTENSIONS:
        content_update = await ocr_node(state, event_callback=ocr_event_callback)
    elif ext in ['.doc', '.docx', '.wps', '.xls', '.xlsx']:
        content_update = await doc_node(state)
    else:
        content_update = await read_text_node(state)
    return str(content_update.get("file_content") or "").strip()


async def build_batch_file_contents_for_extract(file_ids: list[str], base_state: dict[str, Any], ocr_event_callback: OcrEventCallback | None = None) -> list[str]:
    """统一处理多文件内容读取，并按批次输出 OCR 进度。"""
    states: list[dict[str, Any]] = []
    ocr_media_states: list[dict[str, Any]] = []
    ocr_media_paths: list[str] = []
    contents_by_file_id: dict[str, str] = {}

    for file_id in file_ids:
        state = dict(base_state)
        state["file_id"] = file_id
        state["file_path"] = None
        state["file_content"] = ""
        fetched = await fetch_file_node(state)
        state.update(fetched)
        resolved = await resolve_file_node(state)
        state.update(resolved)
        file_path = str(state.get("file_path") or "")
        ext = os.path.splitext(file_path)[1].lower()
        states.append(state)
        if file_path and ext in _OCR_MEDIA_EXTENSIONS:
            ocr_media_states.append(state)
            ocr_media_paths.append(file_path)
            continue
        if ext in ['.doc', '.docx', '.wps', '.xls', '.xlsx']:
            content_update = await doc_node(state)
        else:
            content_update = await read_text_node(state)
        contents_by_file_id[str(file_id)] = str(content_update.get("file_content") or "").strip()

    if ocr_media_paths:
        extract_fields = base_state.get("extract_fields", [])
        force = bool(base_state.get("force"))
        ocr_model = str(base_state.get("ocr_model") or "").strip() or None
        ocr_metrics_batch_id = str(base_state.get("ocr_metrics_batch_id") or "").strip() or None
        request_id = str(base_state.get("conversation_id") or "").strip() or None
        base_id = str(base_state.get("base_id") or "").strip()
        user_id = str(base_state.get("analysis_user_id") or "").strip()
        ocr_hint = build_wordfit_ocr_hint(extract_fields)
        progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress_callback(current: int, total: int) -> None:
            loop.call_soon_threadsafe(progress_queue.put_nowait, {"current": current, "total": total})

        countdown_total, countdown_message = build_pdf_paths_ocr_countdown(ocr_media_paths)
        recognize_task = asyncio.create_task(
            asyncio.to_thread(
                recognize_pdf_files_concurrently,
                ocr_media_paths,
                force,
                ocr_hint,
                progress_callback,
                ocr_model,
                ocr_metrics_batch_id,
                request_id,
                base_id,
                user_id,
                True,
            )
        )
        countdown_started_at = time.monotonic()
        last_countdown: int | None = None

        while True:
            if recognize_task.done() and progress_queue.empty():
                break
            try:
                progress = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                progress = None

            if progress is not None:
                await emit_ocr_stream_event(
                    {
                        "type": "node",
                        "status": "progress",
                        "node": "ocr_node",
                        "node_name": "OCR识别",
                        "content": f"正在识别文件内容，进度：{progress['current']}/{progress['total']}",
                        "current": progress["current"],
                        "total": progress["total"],
                    },
                    event_callback=ocr_event_callback,
                )
                continue

            if countdown_total > 0 and not recognize_task.done():
                elapsed_seconds = int(time.monotonic() - countdown_started_at)
                remaining_seconds = max(countdown_total - elapsed_seconds, 0)
                if remaining_seconds != last_countdown:
                    last_countdown = remaining_seconds
                    await emit_ocr_stream_event(
                        {
                            "type": "ocr_countdown",
                            "content": remaining_seconds,
                            "total": countdown_total,
                            "message": format_ocr_countdown_message(countdown_message, remaining_seconds),
                            "node": "ocr_node",
                            "node_name": "OCR识别",
                        },
                        event_callback=ocr_event_callback,
                    )

        results = await recognize_task
        for state in ocr_media_states:
            file_path = str(state.get("file_path") or "")
            file_id = str(state.get("file_id") or "")
            contents_by_file_id[file_id] = str(results.get(file_path, {}).get("text_content") or "").strip()

    return [contents_by_file_id.get(str(file_id), "") for file_id in file_ids]


async def build_pdf_file_contents_for_extract(file_ids: list[str], base_state: dict[str, Any], ocr_event_callback: OcrEventCallback | None = None) -> list[str]:
    """下载并用共享页级并发池读取多个 PDF 的 OCR 文本。"""
    return await build_batch_file_contents_for_extract(file_ids, base_state, ocr_event_callback)


async def build_file_content_from_path_for_extract(file_id: str, file_path: str, base_state: dict[str, Any], ocr_event_callback: OcrEventCallback | None = None) -> str:
    """复用字段提取文件读取节点处理本地文件路径。"""
    state = dict(base_state)
    state["file_id"] = file_id
    state["file_path"] = file_path
    state["file_content"] = ""
    resolved = await resolve_file_node(state)
    state.update(resolved)
    resolved_path = state.get("file_path")
    ext = os.path.splitext(str(resolved_path or ""))[1].lower()
    if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp', '.pdf']:
        content_update = await ocr_node(state, event_callback=ocr_event_callback)
    elif ext in ['.doc', '.docx', '.wps', '.xls', '.xlsx']:
        content_update = await doc_node(state)
    else:
        content_update = await read_text_node(state)
    return str(content_update.get("file_content") or "").strip()


async def read_text_node(state: WordFitState) -> dict:
    """
    文本读取节点 (原 fetch_file_node 的读取逻辑)
    """
    file_path = state.get("file_path")
    base_id = str(state.get("base_id") or "").strip()
    user_id = str(state.get("analysis_user_id") or "").strip()
    file_id = str(state.get("file_id") or "").strip()
    if not file_path or not os.path.exists(file_path):
         return {"file_content": f"File not found: {file_path}"}

    ext = os.path.splitext(file_path)[1].lower()
    if ext in _ARCHIVE_EXTENSIONS:
        logger.info("[WordFit] Skip extract for archive file user_id=%s base_id=%s file_id=%s file=%s", user_id, base_id, file_id, file_path)
        return {"file_content": "", "skip_extract": True, "skip_extract_reason": f"archive:{ext}"}

    content = ""
    try:
        with open(file_path, 'rb') as f:
            sample = f.read(4096)
        if b'\x00' in sample:
            logger.info("[WordFit] Skip extract for binary file user_id=%s base_id=%s file_id=%s file=%s", user_id, base_id, file_id, file_path)
            return {"file_content": "", "skip_extract": True, "skip_extract_reason": "binary:null-byte"}
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
         logger.info("[WordFit] Skip extract for non-text file user_id=%s base_id=%s file_id=%s file=%s", user_id, base_id, file_id, file_path)
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


def serialize_llm_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """序列化实际发送给大模型的消息。"""
    serialized: list[dict[str, Any]] = []
    for message in messages:
        role = getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__
        content = getattr(message, "content", "")
        serialized.append({"role": str(role), "content": content})
    return serialized


async def emit_ocr_stream_event(event_data: dict[str, Any], config: RunnableConfig | None = None, event_callback: OcrEventCallback | None = None) -> None:
    """发送 OCR 流式事件，兼容图节点事件与直接调用路径。"""
    if event_callback is not None:
        await event_callback(event_data)
    if event_callback is None or config is not None:
        await adispatch_custom_event("word_fit_ocr_progress", event_data, config=config)


async def ocr_node(state: WordFitState, config: RunnableConfig | None = None, event_callback: OcrEventCallback | None = None) -> dict:
    """
    OCR 节点：读取图片类型文件中的文本内容
    支持 PDF (直接调用 OCR)
    """
    file_path = state.get("file_path")
    extract_fields = state.get("extract_fields", [])
    force = bool(state.get("force"))
    ocr_model = str(state.get("ocr_model") or "").strip() or None
    ocr_metrics_batch_id = str(state.get("ocr_metrics_batch_id") or "").strip() or None
    request_id = str(state.get("conversation_id") or "").strip() or None
    base_id = str(state.get("base_id") or "").strip()
    user_id = str(state.get("analysis_user_id") or "").strip()
    scope = str(state.get("scope") or "").strip()
    org_id = str(state.get("org_id") or "").strip()
    entity_id = str(state.get("entity_id") or "").strip()
    if not file_path or not os.path.exists(file_path):
         return {"file_content": "File path not resolved or file not found."}

    try:
        # 直接调用 OCR 处理
        logger.info("[WordFit] OCR processing user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file_id=%s file=%s force=%s ocr_model=%s", user_id, base_id, scope, org_id, entity_id, state.get("file_id") or "", file_path, force, ocr_model or "")
        ocr_hint = build_wordfit_ocr_hint(extract_fields)
        progress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress_callback(current: int, total: int) -> None:
            loop.call_soon_threadsafe(progress_queue.put_nowait, {"current": current, "total": total})

        countdown_total, countdown_message = build_ocr_countdown(file_path)
        recognize_task = asyncio.create_task(asyncio.to_thread(recognize_image, file_path, force, ocr_hint, progress_callback, ocr_model, ocr_metrics_batch_id, request_id, base_id, user_id))
        countdown_started_at = time.monotonic()
        last_countdown: int | None = None

        while True:
            if recognize_task.done() and progress_queue.empty():
                break
            try:
                progress = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                progress = None

            if progress is not None:
                await emit_ocr_stream_event(
                    {
                        "type": "node",
                        "status": "progress",
                        "node": "ocr_node",
                        "node_name": "OCR识别",
                        "content": f"正在识别文件内容，进度：{progress['current']}/{progress['total']}",
                        "current": progress["current"],
                        "total": progress["total"],
                    },
                    config,
                    event_callback,
                )
                continue

            if countdown_total > 0 and not recognize_task.done():
                elapsed_seconds = int(time.monotonic() - countdown_started_at)
                remaining_seconds = max(countdown_total - elapsed_seconds, 0)
                if remaining_seconds != last_countdown:
                    last_countdown = remaining_seconds
                    await emit_ocr_stream_event(
                        {
                            "type": "ocr_countdown",
                            "content": remaining_seconds,
                            "total": countdown_total,
                            "message": format_ocr_countdown_message(countdown_message, remaining_seconds),
                            "node": "ocr_node",
                            "node_name": "OCR识别",
                        },
                        config,
                        event_callback,
                    )

        result = await recognize_task
        content = result.get("text_content", "")
        return {"file_content": content}
    except Exception as e:
        logger.error("[WordFit] OCR Error user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file_id=%s file=%s err=%s", user_id, base_id, scope, org_id, entity_id, state.get("file_id") or "", file_path, e)
        return {"file_content": f"OCR failed: {str(e)}"}


async def doc_node(state: WordFitState) -> dict:
    """
    文档节点：读取 doc, docx, wps, xls 等文档类型文件内容
    """
    file_path = state.get("file_path")
    base_id = str(state.get("base_id") or "").strip()
    user_id = str(state.get("analysis_user_id") or "").strip()
    file_id = str(state.get("file_id") or "").strip()
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
                logger.error("[WordFit] Excel read error user_id=%s base_id=%s file_id=%s file=%s err=%s", user_id, base_id, file_id, file_path, exc)
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
        logger.error("[WordFit] Doc read error user_id=%s base_id=%s file_id=%s file=%s err=%s", user_id, base_id, file_id, file_path, e)
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
    extract_model = resolve_wordfit_extract_model(state.get("extract_model"))
    base_id = str(state.get("base_id") or "").strip()
    user_id = str(state.get("analysis_user_id") or "").strip()
    file_id = str(state.get("file_id") or "").strip()
    
    if skip_extract:
        logger.info("[WordFit] Skip extract triggered user_id=%s base_id=%s file_id=%s file_path=%s reason=%s", user_id, base_id, file_id, file_path, skip_extract_reason or "unknown")
        empty_result = build_empty_extract_result(extract_fields)
        return {"messages": [AIMessage(content=json.dumps(empty_result, ensure_ascii=False))]}

    if not file_content:
        return {"messages": [AIMessage(content="无法读取文件内容，提取失败。")]}

    await persist_complaint_text_cache_from_wordfit(state)
    await trigger_complaint_analysis_preheat(state)

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
    truncated_file_content = file_content[:50000]
    messages = prompt.format_messages(
        fields_desc=fields_desc,
        file_content=truncated_file_content # 截断防止 token 溢出
    )
    
    # 调用 LLM
    logger.info("[WordFit] extract_info user_id=%s base_id=%s file_id=%s model=%s file_path=%s field_count=%s", user_id, base_id, file_id, extract_model or "", file_path, len(extract_fields))
    if is_wordfit_llm_request_log_enabled():
        logger.error("[WordFit] extract_info llm request messages user_id=%s base_id=%s file_id=%s messages=%s", user_id, base_id, file_id, json.dumps(serialize_llm_messages(messages), ensure_ascii=False, default=str))
    llm = get_llm(model=extract_model, streaming=True)
    call_id = record_llm_prompt_metrics(
        "wordfit_field_extract",
        model=extract_model or "",
        messages=messages,
        metadata={
            "request_id": str(state.get("conversation_id") or ""),
            "conversation_id": str(state.get("conversation_id") or ""),
            "file_id": file_id,
            "base_id": base_id,
            "user_id": user_id,
            "business_stage": "field_extract",
        },
    )
    started_at = time.monotonic()
    response = await llm.ainvoke(messages)
    record_llm_output_metrics(
        "wordfit_field_extract",
        model=extract_model or "",
        output_text=getattr(response, "content", ""),
        llm_duration_ms=int((time.monotonic() - started_at) * 1000),
        metadata={
            "request_id": str(state.get("conversation_id") or ""),
            "conversation_id": str(state.get("conversation_id") or ""),
            "file_id": file_id,
            "base_id": base_id,
            "user_id": user_id,
            "business_stage": "field_extract",
        },
        call_id=call_id,
    )
    
    # 尝试解析 JSON (可选，如果后续流程需要结构化数据)
    # 这里直接返回 LLM 的输出消息，因为它会流式输出给前端
    
    return {"messages": [response], "extract_prompt_chars": len(fields_desc) + len(truncated_file_content)}


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
    base_id = str(state.get("base_id") or "").strip()
    user_id = str(state.get("analysis_user_id") or "").strip()
    file_id = str(state.get("file_id") or "").strip()
    
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
                logger.error("[WordFit] 解析提取结果失败 user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, file_id, e)
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
        logger.error("[WordFit] 定位信息失败 user_id=%s base_id=%s file_id=%s err=%s", user_id, base_id, file_id, e)
    
    return {"info_locations": info_locations}
