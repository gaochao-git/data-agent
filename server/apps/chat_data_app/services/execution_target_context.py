"""执行对象上下文格式化。"""

from typing import Any, Dict, List


def normalize_member_datasource_ids(member_datasource_ids: List[str] | None) -> List[str]:
    """规整执行对象数据源 ID。"""
    return [
        str(item).strip()
        for item in member_datasource_ids or []
        if str(item).strip()
    ]


def _execution_target_member_id(target: Dict[str, Any]) -> str:
    return str(target.get("member_datasource_id") or target.get("id") or target.get("org_code") or "").strip()


def _execution_target_name(target: Dict[str, Any], index: int) -> str:
    return str(target.get("member_name") or target.get("name") or _execution_target_member_id(target) or f"执行对象{index}").strip()


def _execution_target_org_code(target: Dict[str, Any]) -> str:
    source_config = target.get("source_config") if isinstance(target.get("source_config"), dict) else {}
    return str(target.get("org_code") or source_config.get("org_code") or _execution_target_member_id(target) or "").strip()


def append_selected_execution_targets_to_context(
    query_context: str,
    execution_targets: List[Dict[str, Any]],
    member_datasource_ids: List[str] | None,
) -> str:
    """追加用户已选择的执行对象说明。"""
    selected_member_ids = normalize_member_datasource_ids(member_datasource_ids)
    if not selected_member_ids:
        return str(query_context or "").strip()

    selected_id_set = set(selected_member_ids)
    selected_targets = [
        (index, target)
        for index, target in enumerate(execution_targets, start=1)
        if _execution_target_member_id(target) in selected_id_set
    ]
    if not selected_targets:
        return str(query_context or "").strip()

    lines: List[str] = []
    base_context = str(query_context or "").strip()
    if base_context:
        lines.extend([base_context, ""])
    lines.append("## 当前用户已选择的执行对象数据源")
    lines.append("本次查询必须只使用以下 member_datasource_ids：")
    for index, target in selected_targets:
        member_name = _execution_target_name(target, index)
        member_id = _execution_target_member_id(target)
        org_code = _execution_target_org_code(target)
        line = f"- {member_name}"
        if member_id:
            line += f"（member_datasource_id: {member_id}）"
        if org_code and org_code != member_name:
            line += f"；编码：{org_code}"
        lines.append(line)
    return "\n".join(lines)
