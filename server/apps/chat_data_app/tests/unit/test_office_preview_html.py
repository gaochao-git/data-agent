"""OfficeCLI HTML 预览对齐修正。"""

from apps.chat_data_app.agent.workspace.office_preview_html import normalize_office_preview_html


def test_inserts_default_left_alignment_before_head_close():
    html = "<html><head><style>p { text-align: center; }</style></head><body><p>x</p></body></html>"
    normalized = normalize_office_preview_html(html)
    assert "<style>p { text-align: left; }</style></head>" in normalized
    assert normalized.index("text-align: left") > normalized.index("text-align: center")


def test_preserves_explicit_inline_alignment():
    html = '<html><head></head><body><p style="text-align:center">x</p></body></html>'
    normalized = normalize_office_preview_html(html)
    assert 'style="text-align:center"' in normalized


def test_is_idempotent_and_keeps_fragment_unchanged():
    assert normalize_office_preview_html("<p>x</p>") == "<p>x</p>"
    html = "<html><head></head><body><p>x</p></body></html>"
    once = normalize_office_preview_html(html)
    assert normalize_office_preview_html(once) == once
