"""兼容导出：脱敏实现已迁移到 utils/log_redaction，供 utils.logger 直接复用。"""

from utils.log_redaction import (  # noqa: F401  _mask_middle 等私有函数为单测兼容重导出
    BANK_CARD_PATTERN,
    ID_CARD_PATTERN,
    PHONE_PATTERN,
    REDACTED_HEADERS,
    _mask_bank_card_match,
    _mask_id_card_match,
    _mask_middle,
    _mask_phone_match,
    dump_masked_payload,
    mask_headers,
    mask_sensitive_payload,
    mask_sensitive_text,
)

__all__ = [
    "mask_sensitive_text",
    "mask_sensitive_payload",
    "mask_headers",
    "dump_masked_payload",
]
