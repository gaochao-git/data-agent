from __future__ import annotations

from textwrap import dedent

from apps.complaint_app.core.model_config import (
    get_tool_planner_model,
    get_tool_planner_temperature,
    get_tool_summary_model,
    get_tool_summary_temperature,
)

# 模型与温度可复用到其他工具前置/后置对话
TOOL_PLANNER_MODEL = get_tool_planner_model()
TOOL_PLANNER_TEMPERATURE = get_tool_planner_temperature()
TOOL_SUMMARY_MODEL = get_tool_summary_model()
TOOL_SUMMARY_TEMPERATURE = get_tool_summary_temperature()


def build_text_tool_planner_messages(upload_label: str, file_size_mb: float | None = None) -> list[dict[str, str]]:
    size_hint = f"（约 {file_size_mb:.2f} MB）" if file_size_mb else ""
    user_brief = f"帮我提取举报件原文，附件名：{upload_label}{size_hint}"
    system_prompt = dedent(
        """
        你是举报件处理助理，目标是：简洁清晰地判断并引导调用原文提取工具，再生成摘要。
        - 用简洁、逻辑通顺的中文说明判断。
        - 判断素材类型/质量（PDF/图片/扫描/表格等），确认需要 OCR/版式转文本。
        - 明确写出将调用原文提取工具获取全文的决定。
        - 提一句下一步会基于提取结果生成摘要的计划。
        - 只写思考和决策，不要假设已经拿到原文或摘要内容。
        """
    ).strip()
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_brief},
    ]


def build_text_tool_summary_messages(upload_label: str, extracted_text: str) -> list[dict[str, str]]:
    snippet = (extracted_text or "").strip()
    if len(snippet) > 2400:
        snippet = snippet[:2400]
    system_prompt = dedent(
        """
        你是举报件处理助理，刚刚完成原文提取并自动生成摘要。
        - 用一句话汇报两步工具的整体结果（无需展开内部细节）。
        - 评价提取文本质量（完整/缺页/模糊/表格等），并说明摘要生成是否正常。
        - 给出后续动作建议（人工校对原文和摘要的准确性、查看摘要结果、继续分析举报事项）。
        - 语气简洁、口语化，中文输出。
        """
    ).strip()
    user_prompt = f"附件：{upload_label}\n\n提取到的文字片段示例：\n{snippet}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


__all__ = [
    "build_text_tool_planner_messages",
    "build_text_tool_summary_messages",
    "TOOL_PLANNER_MODEL",
    "TOOL_PLANNER_TEMPERATURE",
    "TOOL_SUMMARY_MODEL",
    "TOOL_SUMMARY_TEMPERATURE",
]
