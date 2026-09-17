"""DataAnalyze semantic_yaml_check_tool：Ossie 语义模型 YAML 交付前自检。

与导入接口（ossie-import/preview）共用同一批服务层函数——schema 校验、结构映射、
物理字段校验、物理列清单、质量报告——保证「会话内自检口径 = 导入机器闸门口径」，工具只读不落库。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from apps.chat_data_app.services.datasource.manager import get_datasource_manager
from apps.chat_data_app.services.semantic.run.chat_data_ossie_importer import (
    OssieToChatDataImporter,
    build_model_checklist,
)
from apps.chat_data_app.services.semantic.run.semantic_config_service import (
    load_semantic_physical_columns,
    validate_semantic_config_for_save,
)
from apps.chat_data_app.services.semantic.run.semantic_quality_checker import get_semantic_quality_checker
from .common import tool_result_payload

_TOOL_NAME = "semantic_yaml_check_tool"


def create_semantic_yaml_check_tool(datasource_id: str | None = None):
    bound_datasource_id = str(datasource_id or "").strip()

    @tool(_TOOL_NAME, parse_docstring=True)
    async def semantic_yaml_check_tool(yaml_content: str, intent: str = "") -> str:
        """语义模型 YAML 自检工具：把你生成或修改的 Ossie 语义模型 YAML 全文传入，执行与导入接口完全相同的检查（OSSI schema 校验、结构映射、物理字段校验、质量报告），只读不落库。

        在按 semantic-quality-check 技能生成或优化语义模型 YAML 后、交付给用户下载之前，必须调用本工具自检；
        根据返回的逐模型 errors 修正后重新调用，直到 all_importable 为 true 再把 YAML 写入 outputs 交付。
        禁止跳过自检直接交付 YAML 文件。

        Args:
            yaml_content: 完整的 Ossie 语义模型 YAML 文本（与导入接口接受的格式一致）。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        if not bound_datasource_id:
            raise ValueError("当前会话未绑定数据源")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(bound_datasource_id)
        if not datasource:
            raise ValueError(f"数据源不存在: {bound_datasource_id}")
        try:
            imports = OssieToChatDataImporter().import_yaml(str(yaml_content or ""))
        except ValueError as exc:
            return tool_result_payload(_TOOL_NAME, {
                "success": False,
                "stage": "parse",
                "datasource_id": bound_datasource_id,
                "error": str(exc),
                "models": [],
                "all_importable": False,
            })

        physical_columns = await load_semantic_physical_columns(ds_manager, bound_datasource_id)
        checker = get_semantic_quality_checker()
        models = []
        for item in imports:
            model_payload: dict[str, Any] = {
                "name": item.name,
                "importable": item.importable,
                "errors": list(item.errors),
                "warnings": list(item.warnings),
                "metric_count": len(item.metrics),
                "dimension_count": len(item.dimensions),
                "relationship_count": len(item.relationships),
            }
            findings = None
            if item.importable:
                item.bump("physical_checked")
                validation_error = await validate_semantic_config_for_save(
                    ds_manager,
                    bound_datasource_id,
                    datasource,
                    item.metrics,
                    item.dimensions,
                    item.relationships,
                )
                if validation_error:
                    item.validation_error = validation_error
                    item.errors.append(validation_error)
                    model_payload["importable"] = False
                    model_payload["errors"] = list(item.errors)
                else:
                    report = checker.check_config(
                        {
                            **item.to_config_payload(),
                            "enabled": False,
                            "scope_type": "public",
                        },
                        physical_columns=physical_columns,
                    )
                    # 只透出触发的 findings：评分无法指导动作，自检链路不输出 score/grade
                    findings = report.to_payload()["findings"]
                    model_payload["quality"] = {"findings": findings}
            model_payload["checklist"] = build_model_checklist(
                item,
                validation_error=item.validation_error,
                quality_findings=findings,
                inventory_available=physical_columns is not None,
            )
            models.append(model_payload)

        return tool_result_payload(_TOOL_NAME, {
            "success": True,
            "stage": "checked",
            "datasource_id": bound_datasource_id,
            "all_importable": bool(models) and all(model["importable"] for model in models),
            "models": models,
        })

    return semantic_yaml_check_tool
