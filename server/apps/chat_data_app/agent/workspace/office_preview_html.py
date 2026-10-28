"""OfficeCLI HTML 预览的展示修正。

OfficeCLI 导出的默认样式把 ``p`` 设成 ``text-align: center``，但 docx 中
未显式对齐的段落实际是左对齐；生成器只给显式 center/justify 段落写内联
样式，导致预览里普通正文/标题被误居中。这里在 head 末尾追加一个同优先级
的默认左对齐规则，显式内联对齐仍然优先。
"""

from __future__ import annotations

_ALIGN_OVERRIDE = "<style>p { text-align: left; }</style>"


def normalize_office_preview_html(html: str) -> str:
    """追加默认左对齐 CSS，修正 OfficeCLI 预览与 Word 的段落对齐差异。"""
    text = str(html or "")
    if not text or _ALIGN_OVERRIDE in text:
        return text
    if "</head>" not in text:
        # 测试/调用方传入的是 HTML 片段，没有 OfficeCLI 默认样式可覆盖。
        return text
    return text.replace("</head>", f"{_ALIGN_OVERRIDE}</head>", 1)


__all__ = ["normalize_office_preview_html"]
