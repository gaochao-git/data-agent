import os
import shutil
from PIL import Image
import io
from utils.logger import get_logger

# 单页图片大小上限（4 MiB）
PDF_PAGE_MAX_IMAGE_BYTES = 4 * 1024 * 1024
PDF_IMAGE_SCALE_STEP = 0.85
PDF_IMAGE_MAX_RESIZE_ATTEMPTS = 5

from utils.storage_paths import get_complaints_real_path

logger = get_logger(__name__)

def _estimate_base64_size(byte_length: int) -> int:
    """Return the byte length after base64 encoding (without data URI prefix)."""
    if byte_length <= 0:
        return 0
    return ((byte_length + 2) // 3) * 4

def _save_pdf_image_with_limit(image: Image.Image, target_path: str):
    """Save image as PNG ensuring its base64 size stays within PDF_PAGE_MAX_IMAGE_BYTES."""
    current_image = image
    scale = 1.0
    resize_attempts = 0
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    while True:
        buffer = io.BytesIO()
        current_image.save(buffer, format="PNG", optimize=True)
        raw_size = buffer.tell()
        encoded_size = _estimate_base64_size(raw_size)
        if encoded_size <= PDF_PAGE_MAX_IMAGE_BYTES:
            with open(target_path, "wb") as output:
                output.write(buffer.getvalue())
            return encoded_size, scale, resize_attempts
        if resize_attempts >= PDF_IMAGE_MAX_RESIZE_ATTEMPTS:
            with open(target_path, "wb") as output:
                output.write(buffer.getvalue())
            return encoded_size, scale, resize_attempts
        scale *= PDF_IMAGE_SCALE_STEP
        new_width = max(int(image.width * scale), 1)
        new_height = max(int(image.height * scale), 1)
        # 如果缩放后尺寸没有变化，则强制减少 1 个像素，避免死循环
        if new_width == current_image.width and new_height == current_image.height:
            if new_width > 1:
                new_width -= 1
            if new_height > 1:
                new_height -= 1
            if new_width == current_image.width and new_height == current_image.height:
                with open(target_path, "wb") as output:
                    output.write(buffer.getvalue())
                return encoded_size, scale, resize_attempts
        current_image = image.resize((new_width, new_height), Image.LANCZOS)
        resize_attempts += 1

def process_pdf(filepath: str, user_id: str, base_id: str, image_prefix: str | None = None, scope_context=None):
    from pdf2image import convert_from_path
    images = convert_from_path(filepath, dpi=150)
    image_paths = []
    image_foloder = get_complaints_real_path(user_id, base_id, "images", scope_context=scope_context)
    os.makedirs(image_foloder, exist_ok=True)
    for i, image in enumerate(images):
        prefix = image_prefix or base_id
        image_path = os.path.join(image_foloder, f"{prefix}_page_{i+1}.png")
        final_size, scale, attempts = _save_pdf_image_with_limit(image, image_path)
        if attempts > 0:
            logger.info(
                "PDF 页面 %d 经过 %d 次降采样，缩放比例 %.2f，Base64 字节数 %d",
                i + 1,
                attempts,
                scale,
                final_size,
            )
        if final_size > PDF_PAGE_MAX_IMAGE_BYTES:
            logger.warning(
                "PDF 页面 %d Base64 字节数超过限制（%d > %d）",
                i + 1,
                final_size,
                PDF_PAGE_MAX_IMAGE_BYTES,
            )
        image_paths.append(image_path)
    return image_paths


def process_image(filepath: str, user_id: str, base_id: str, ext: str, image_prefix: str | None = None, scope_context=None):
    image_foloder = get_complaints_real_path(user_id, base_id, "images", scope_context=scope_context)
    os.makedirs(image_foloder, exist_ok=True)
    prefix = image_prefix or base_id
    image_path = os.path.join(image_foloder, f"{prefix}.{ext}")
    shutil.copyfile(filepath, image_path)
    return [image_path]
