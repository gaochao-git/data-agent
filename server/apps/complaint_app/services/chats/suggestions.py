DEFAULT_COMPLAINT_SUGGESTIONS = [
    {"tool_id": "complaint_analysis", "name": "基于举报件中的举报事项，给出相关分析，研判举报事项归属、关联法律法规。"},
    {"tool_id": "export_flow", "name": "导出交易流水表格为csv文件"},
]


def get_default_complaint_suggestions() -> list[dict[str, str]]:
    return [dict(item) for item in DEFAULT_COMPLAINT_SUGGESTIONS]
