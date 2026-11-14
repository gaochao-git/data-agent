"""用户消息的模型可见后缀：@ 引用文件、划选引用、文档选中内容。

这些短句/标记块由服务端在 Human 消息正文末尾统一追加，前端只透传结构化
字段；历史回放时前端按同一标记剥离，正文不重复平铺。
"""

from __future__ import annotations

from typing import Any

# 与 workspace/workspace_path_policy.USER_DATA_VIRTUAL_ROOT 同一口径；run 层不反向
# 依赖 workspace（harness 分层棘轮），这里保留镜像常量，改值需两处同步。
_USER_DATA_VIRTUAL_ROOT = "/mnt/user-data"


def file_selection_mention_suffix(file_selections: list[str]) -> str:
    """用户在输入框 @ 引用工作区文件时，给 Human 消息追加模型可见的引用短句。"""
    if not file_selections:
        return ""
    references = "、".join(f"{_USER_DATA_VIRTUAL_ROOT}/{path}" for path in file_selections)
    return f"\n\n[引用文件：{references}]"


def quote_snippets_suffix(quoted_snippets: list[dict[str, str]] | None) -> str:
    """划选引用的模型可见后缀：片段原文按标记块逐一包住，追加在正文之后。"""
    blocks = [str(item.get("text") or "").strip() for item in (quoted_snippets or [])]
    blocks = [block for block in blocks if block]
    if not blocks:
        return ""
    lines = "\n\n".join(f"[引用助手回复片段开始]\n{block}\n[引用助手回复片段结束]" for block in blocks)
    return f"\n\n{lines}"


def _document_annotation_block(item: dict[str, Any], user_content: str) -> str:
    """单个标注 → 模型可见标记块；缺少文件、定位或问题时返回空串。"""
    file_path = str(item.get("file_path") or "").strip()
    doc_path = str(item.get("doc_path") or "").strip()
    comment = str(item.get("comment") or "").strip()
    if not file_path or not doc_path or not comment:
        return ""
    lines = [
        "[文档选中内容开始]",
        f"文件：/mnt/user-data/{file_path}",
        f"定位：{doc_path}",
    ]
    # 标记块按行解析；原文含换行会破坏「选中原文」之后的字段边界，
    # 在模型可见文本里压平成单行，前端旧消息兜底解析也能稳定取全。
    quote = " ".join(part.strip() for part in str(item.get("quote") or "").strip().splitlines() if part.strip())
    if quote:
        lines.append(f"选中原文：{quote}")
    if comment != user_content.strip():
        lines.append(f"问题或要求：{comment}")
    lines.extend(
        [
            # 处理建议放在标记块内部：前端按整块剥离，不会残留在用户气泡里；
            # 默认回答，只有明确要求修改时才动文件，避免把划选误解为改稿。
            "处理建议：先根据用户的问题或要求判断意图；只需解释、核对或回答时，不要改动文件；只有明确要求修改时，才使用 officecli 按「定位」编辑并重新展示。",
            "[文档选中内容结束]",
        ]
    )
    return "\n".join(lines)


def document_annotations_suffix(
    annotations: list[dict[str, Any]] | None,
    *,
    user_content: str = "",
) -> str:
    """文档选中内容的模型可见后缀。

    前端在 Office 预览里划选后点「引用选中内容」，带文件、OfficeCLI 文档内定位
    （``/body/p[4]``）、原文与用户问题随消息透传。页码不参与定位，也不传给模型。
    划选只是提供定位上下文，
    可能用于提问、核对来源或要求修改；这里保持中性，由模型结合用户问题判断意图。
    ``comment`` 与消息正文相同时不重复写入标记块——问题已经在 Human 正文里。
    """
    blocks = [block for item in annotations or [] if (block := _document_annotation_block(item, user_content))]
    if not blocks:
        return ""
    return "\n\n" + "\n\n".join(blocks)
