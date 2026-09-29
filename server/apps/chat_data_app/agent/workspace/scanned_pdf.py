"""扫描版 PDF（纯图像、无文本层）的检测、渲染与视觉转写。

PDF 解析主链路是 markitdown 的文本层提取（uploaded_file_parser）；扫描版没有文本层，
提取为空。本模块作为 PDF 专属兜底：用 PyMuPDF 把每页渲染为图片，交给已配置的视觉模型
分批转写为 Markdown 文本。转写发生在解析器内部、返回纯文本——parse_document 的
「文本进 → 文本出」契约保持不变，不向 workspace 写二进制，沙箱后端无需任何改动。
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from langchain_core.messages import HumanMessage

from utils.logger import get_logger

from apps.chat_data_app.agent.workspace.uploaded_file_parser import get_image_size_limit
from apps.chat_data_app.services.llm_config import get_chat_data_llm
from apps.chat_data_app.services.setting_service import get_runtime_setting

logger = get_logger(__name__)

# 页均可见字符低于该值判定为扫描版（对齐 MinerU 的页均口径，业内 OCRmyPDF/Marker 同为
# 页级或页均判断）：页码、水印等残留字符被页数均摊，不会像全文档总数口径那样在大页数
# 扫描件上（每页一个页码 → 总数被撑大）误判「有文本层」。
SCANNED_PDF_AVG_CHARS_PER_PAGE = 30
# 一次视觉请求携带的最大页数（控上下文体积）；总页数上限走运行时设置
# CHAT_DATA_MAX_SCAN_PDF_PAGES（表覆盖 > env > 默认）。
_TRANSCRIBE_BATCH_SIZE = 4
_RENDER_DPI = 150
# 超过视觉链路单张大小上限时的最低重渲染 DPI（A0 等超大画幅在 150 DPI 下会超限）。
_RENDER_MIN_DPI = 72


def load_pdf_page_texts(source_path: Path) -> list[str]:
    """提取每页文本层内容（PyMuPDF），用于扫描版判定。"""
    with fitz.open(str(source_path)) as document:
        return [page.get_text() for page in document]


def is_likely_scanned_pdf(page_texts: list[str]) -> bool:
    """页均可见字符 < 阈值即判定扫描版；空文档视为扫描版（由无页错误兜住）。"""
    if not page_texts:
        return True
    avg_chars = sum(len(text.strip()) for text in page_texts) / len(page_texts)
    return avg_chars < SCANNED_PDF_AVG_CHARS_PER_PAGE


def select_page_numbers(total_pages: int, max_pages: int) -> list[int]:
    """1 起始的页码列表，超出上限截断（只转写前 max_pages 页）。"""
    return list(range(1, min(total_pages, max(0, max_pages)) + 1))


def render_pdf_pages(
    source_path: Path,
    page_numbers: list[int],
    out_dir: Path,
    dpi: int = _RENDER_DPI,
    max_bytes: int | None = None,
) -> list[Path]:
    """把指定页渲染为 PNG（150 DPI 下 A4 约 0.1-0.5MB）。

    max_bytes 是视觉链路单张大小上限（CHAT_DATA_MAX_IMAGE_SIZE_MB 口径，唯一来源
    uploaded_file_parser.get_image_size_limit）：A0 工程图等超大画幅在 150 DPI 下可能
    超限，超限时逐次减半 DPI 重渲染，最低降到 _RENDER_MIN_DPI。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    with fitz.open(str(source_path)) as document:
        for number in page_numbers:
            page = document[number - 1]
            current_dpi = dpi
            target = out_dir / f"page-{number:03d}.png"
            page.get_pixmap(dpi=current_dpi).save(str(target))
            while (
                max_bytes is not None
                and target.stat().st_size > max_bytes
                and current_dpi > _RENDER_MIN_DPI
            ):
                current_dpi = max(_RENDER_MIN_DPI, current_dpi // 2)
                page.get_pixmap(dpi=current_dpi).save(str(target))
            rendered.append(target)
    return rendered


def _image_content_block(png_path: Path) -> dict:
    data = png_path.read_bytes()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"},
    }


def _page_range_header(start: int, end: int) -> str:
    return f"## 第 {start} 页" if start == end else f"## 第 {start}-{end} 页"


async def transcribe_scanned_pdf(source_path: Path, *, llm_factory=None) -> str:
    """渲染扫描页并调用视觉模型分批转写为 Markdown。

    llm_factory 仅供测试注入；生产走 get_chat_data_llm 新起一次独立模型调用——
    与 read_image 的链路不同（后者把图注入主对话、由主模型看图，自身不调 LLM）。
    单模型部署下两者等价；将来主模型/工具模型分开配置时需各自对齐视觉能力。
    模型不支持图片输入等失败统一包装为 ValueError，由 parse_document 返回给 agent。
    """
    max_pages = await get_runtime_setting("CHAT_DATA_MAX_SCAN_PDF_PAGES")
    _, max_bytes = await get_image_size_limit()
    loop = asyncio.get_running_loop()
    with tempfile.TemporaryDirectory() as tmp_dir:
        page_texts = await loop.run_in_executor(None, load_pdf_page_texts, source_path)
        if not is_likely_scanned_pdf(page_texts):
            raise ValueError("PDF 存在文本层，无法视觉转写")
        page_numbers = select_page_numbers(len(page_texts), max_pages)
        if not page_numbers:
            raise ValueError("PDF 无可渲染页面")
        out_dir = Path(tmp_dir) / "pages"
        rendered = await loop.run_in_executor(
            None,
            render_pdf_pages,
            source_path,
            page_numbers,
            out_dir,
            _RENDER_DPI,
            max_bytes,
        )

        factory = llm_factory or get_chat_data_llm
        sections: list[str] = []
        try:
            for start in range(0, len(rendered), _TRANSCRIBE_BATCH_SIZE):
                batch = rendered[start:start + _TRANSCRIBE_BATCH_SIZE]
                first_page = page_numbers[start]
                last_page = page_numbers[min(start + _TRANSCRIBE_BATCH_SIZE, len(page_numbers)) - 1]
                message = HumanMessage(content=[
                    {
                        "type": "text",
                        "text": (
                            "把这些 PDF 页面图片中的全部文字内容忠实转写为 Markdown。"
                            "保持原有标题层级、列表与表格结构，表格用 GFM 表格；"
                            "看不清的字用〔?〕占位；不要添加图片中不存在的内容。"
                        ),
                    },
                    *[_image_content_block(png) for png in batch],
                ])
                response = await factory().ainvoke([message])
                text = str(getattr(response, "content", "") or "").strip()
                if text:
                    sections.append(f"{_page_range_header(first_page, last_page)}\n\n{text}")
        except Exception as exc:
            logger.error("扫描版 PDF 视觉转写失败: path=%s error=%s", source_path.name, exc, exc_info=True)
            raise ValueError("扫描版 PDF 视觉转写失败:当前模型可能不支持图片输入") from exc

    if not sections:
        raise ValueError("扫描版 PDF 未转写出任何内容")
    if len(page_numbers) < len(page_texts):
        sections.append(f"> 注:原文件共 {len(page_texts)} 页,超出页数上限,仅转写前 {len(page_numbers)} 页。")
    return "\n\n".join(sections)
