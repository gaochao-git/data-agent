"""Semantic config routes for ChatData."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from auth import AuthenticationError
from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..schema import (
    SemanticConfigBatchToggleRequest,
    SemanticConfigCreateRequest,
    SemanticConfigUpdateRequest,
    SemanticHistoryLearningRequest,
    SemanticOssieImportRequest,
)
from ..services.datasource.manager import get_datasource_manager
from ..services.llm_config import get_chat_data_llm
from ..services.semantic.run import binding_guard
from ..services.semantic.run.chat_data_ossie_importer import build_model_checklist, plan_column_dict_fills
from ..services.semantic.run.semantic_config_service import (
    OssieImportWriteError as _OssieImportWriteError,
)
from ..services.semantic.run.semantic_config_service import (
    can_mutate_semantic_config,
    dedupe_semantic_config_ids,
    get_semantic_config_service,
    is_private_semantic_config_owner,
    is_public_semantic_config,
    persist_ossie_imports_in_transaction,
    prepare_ossie_import_models,
    quality_findings_for_imports,
    quality_findings_for_save,
    validate_semantic_config_for_save,
)
from ..services.semantic.run.semantic_config_service import (
    load_semantic_physical_columns as _load_physical_columns,
)
from ..services.semantic.run.semantic_history_learning_service import (
    SemanticHistoryLearningService,
    _load_history_conversation_context,
    build_semantic_history_learning_service,
)
from ..services.semantic.run.semantic_history_learning_service import (
    _generate_history_learning_suggestions_by_llm as _service_generate_history_learning_suggestions_by_llm,
)
from ..services.semantic.run.semantic_history_learning_service import (
    _history_belongs_to_config as _service_history_belongs_to_config,
)
from ..services.semantic.run.semantic_quality_checker import (
    check_cross_model_metrics,
    get_semantic_quality_checker,
    grade_for_score,
)
from ..services.sql_run.parser import SqlParseResult, extract_physical_tables
from .dependencies import _get_current_user

logger = get_logger(__name__)
from . import JSON_API_RESPONSES

router = APIRouter(tags=["ChatData 语义模型"], responses=JSON_API_RESPONSES)

# 单次批量切换的指标集数量上限：限制 UPDATE ... IN 的规模与整批回滚成本。
SEMANTIC_BATCH_TOGGLE_MAX_IDS = 200


def _can_manage_semantic_config(config: dict[str, Any], user_id: str) -> bool:
    """普通用户只能管理自己创建的个人指标集。"""
    return is_private_semantic_config_owner(config, user_id)


def _with_semantic_config_permissions(
    config: dict[str, Any], user_id: str, username: str = "", admin_manage: bool = False
) -> dict[str, Any]:
    """补充指标集权限字段。"""
    item = dict(config or {})
    item["scope_type"] = item.get("scope_type") or "public"
    item["can_manage"] = can_mutate_semantic_config(item, user_id, admin_manage)
    return item


async def _get_semantic_configs_for_user(
    semantic_service: Any, datasource_id: str, user_id: str, username: str = ""
) -> list[dict[str, Any]]:
    """读取查询链路可见指标集：公共指标集 + 当前用户个人指标集。"""
    configs = await semantic_service.get_configs_async(datasource_id, user_id)
    return [_with_semantic_config_permissions(config, user_id, username) for config in (configs or [])]


async def _get_my_semantic_configs(
    semantic_service: Any, datasource_id: str, user_id: str, username: str = ""
) -> list[dict[str, Any]]:
    """读取普通维护页可维护的个人指标集。"""
    configs = await semantic_service.get_configs_async(datasource_id, user_id)
    return [
        _with_semantic_config_permissions(config, user_id, username)
        for config in (configs or [])
        if is_private_semantic_config_owner(config, user_id)
    ]


async def _get_admin_semantic_configs(
    semantic_service: Any, datasource_id: str, user_id: str, username: str = ""
) -> list[dict[str, Any]]:
    """读取后台维护页可维护的公共指标集。"""
    configs = await semantic_service.get_configs_async(datasource_id)
    return [
        _with_semantic_config_permissions(config, user_id, username, admin_manage=True)
        for config in (configs or [])
        if is_public_semantic_config(config)
    ]


def _history_table_parser(sql: str, source_type: str) -> SqlParseResult:
    return extract_physical_tables(sql, source_type=source_type)


def _history_belongs_to_config(
    query_type: str,
    task_payload: dict[str, Any],
    sql: str,
    config_id: str,
    config: dict[str, Any],
    source_type: str,
) -> tuple[bool, str, list[str], str]:
    """判断历史记录是否属于当前指标集。"""
    return _service_history_belongs_to_config(
        query_type,
        task_payload,
        sql,
        config_id,
        config,
        source_type,
        table_parser=_history_table_parser,
    )


async def _generate_history_learning_suggestions_by_llm(
    datasource: dict[str, Any],
    config: dict[str, Any],
    samples: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """调用大模型基于历史生成指标集维护建议。"""
    return await _service_generate_history_learning_suggestions_by_llm(
        datasource,
        config,
        samples,
        llm_factory=get_chat_data_llm,
    )


def _build_history_learning_service() -> SemanticHistoryLearningService:
    """构建历史学习服务。"""
    return build_semantic_history_learning_service(
        context_loader=_load_history_conversation_context,
        suggestion_loader=_generate_history_learning_suggestions_by_llm,
        history_matcher=_history_belongs_to_config,
    )


@router.get("/datasources/{datasource_id}/semantic-configs")
async def list_semantic_configs(datasource_id: str, request: Request) -> JSONResponse:
    """获取查询链路可见指标集：公共指标集 + 当前用户个人指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        if not await ds_manager.has_datasource_async(datasource_id):
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        configs = await _get_semantic_configs_for_user(get_semantic_config_service(), datasource_id, user_id, username)
        return business_response(data={"configs": configs, "total": len(configs)}, msg="获取成功")
    except Exception as e:
        logger.error(f"获取语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/datasources/{datasource_id}/my-semantic-configs")
async def list_my_semantic_configs(datasource_id: str, request: Request) -> JSONResponse:
    """获取普通维护页可维护的个人指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        if not await ds_manager.has_datasource_async(datasource_id):
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        configs = await _get_my_semantic_configs(get_semantic_config_service(), datasource_id, user_id, username)
        return business_response(data={"configs": configs, "total": len(configs)}, msg="获取成功")
    except Exception as e:
        logger.error(f"获取个人语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/admin/datasources/{datasource_id}/semantic-configs")
async def list_admin_semantic_configs(datasource_id: str, request: Request) -> JSONResponse:
    """后台获取可维护的公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        if not await ds_manager.has_datasource_async(datasource_id):
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        configs = await _get_admin_semantic_configs(get_semantic_config_service(), datasource_id, user_id, username)
        return business_response(data={"configs": configs, "total": len(configs)}, msg="获取成功")
    except Exception as e:
        logger.error(f"获取后台语义配置列表失败: {str(e)}", exc_info=True)
        return business_response(msg=f"获取失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


async def _create_semantic_config_with_scope(
    datasource_id: str,
    payload: SemanticConfigCreateRequest,
    request: Request,
    *,
    scope_type: str,
    admin_manage: bool = False,
) -> JSONResponse:
    """按指定范围创建指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        metrics = [m.model_dump() for m in payload.metrics]
        dimensions = [d.model_dump() for d in payload.dimensions]
        relationships = [r.model_dump() for r in payload.relationships]
        validation_error = await validate_semantic_config_for_save(
            ds_manager,
            datasource_id,
            datasource,
            metrics,
            dimensions,
            relationships,
        )
        if validation_error:
            return business_response(msg=validation_error, code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")

        config = await get_semantic_config_service().create_async(
            datasource_id=datasource_id,
            name=payload.name,
            description=payload.description,
            business_notes=payload.business_notes,
            enabled=payload.enabled,
            metrics=metrics,
            dimensions=dimensions,
            relationships=relationships,
            scope_type=scope_type,
            create_by=user_id,
            create_by_name=username,
        )
        if not config:
            return business_response(msg="创建失败", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")

        logger.info(f"创建语义配置: {config['id']} - {config['name']}")
        return business_response(
            data=_with_semantic_config_permissions(config, user_id, username, admin_manage=admin_manage), msg="创建成功"
        )
    except Exception as e:
        logger.error(f"创建语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"创建失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs")
async def create_semantic_config(
    datasource_id: str, payload: SemanticConfigCreateRequest, request: Request
) -> JSONResponse:
    """创建个人指标集。"""
    return await _create_semantic_config_with_scope(datasource_id, payload, request, scope_type="private")


@router.post("/admin/datasources/{datasource_id}/semantic-configs")
async def create_admin_semantic_config(
    datasource_id: str, payload: SemanticConfigCreateRequest, request: Request
) -> JSONResponse:
    """创建公共指标集。"""
    _get_current_user(request)
    return await _create_semantic_config_with_scope(
        datasource_id, payload, request, scope_type="public", admin_manage=True
    )


@router.post("/admin/datasources/{datasource_id}/semantic-configs/generate-basic")
async def generate_admin_basic_semantic_configs(datasource_id: str, request: Request) -> JSONResponse:
    """后台按物理表批量创建公共基础模型（默认启用）。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        result = await get_semantic_config_service().generate_basic_public_single_table_configs_async(
            datasource_id,
            create_by=user_id,
            create_by_name=username,
        )
        logger.info(
            "后台批量生成公共基础语义模型: datasource_id=%s created=%s skipped=%s failed=%s",
            datasource_id,
            result["created"],
            result["skipped"],
            result["failed"],
        )
        return business_response(data=result, msg="基础模型生成完成")
    except ValueError as e:
        datasource_missing = str(e).startswith("数据源不存在")
        code = ErrorCode.CHATDATA_NOT_FOUND if datasource_missing else ErrorCode.CHATDATA_VALIDATION_FAILED
        msg = "数据源不存在" if datasource_missing else "生成参数校验失败"
        logger.warning("批量生成基础语义模型参数校验失败: datasource_id=%s error=%s", datasource_id, e)
        return business_response(msg=msg, code=code, status="error")
    except Exception as e:
        logger.error("批量生成基础语义模型失败: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
        return business_response(msg="生成失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


async def _prepare_ossie_import(
    datasource_id: str,
    payload: SemanticOssieImportRequest,
    request: Request,
) -> tuple[list[Any], None] | tuple[None, JSONResponse]:
    """Ossie 导入公共前置：鉴权、数据源校验，校验核心委托 service 层共享实现。

    校验口径（解析 / 请求内重名 / 同名冲突标注 / 逐模型物理校验）统一在
    services.semantic_config_service.prepare_ossie_import_models，与 check_yaml
    工具共用同一套实现；本函数只做 HTTP 错误形态转换。

    返回 tagged tuple：imports 仅在 error 为 None 时非空，调用方判空 error 即可同步收窄 imports。
    """
    current_user = _get_current_user(request)
    user_id = str(current_user.user_id)
    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(datasource_id)
    if not datasource:
        return None, business_response(
            msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
        )
    try:
        imports, duplicate_error = await prepare_ossie_import_models(
            ds_manager,
            datasource,
            datasource_id,
            payload.yaml_content,
            semantic_service=get_semantic_config_service(),
            user_id=user_id,
        )
    except ValueError as e:
        logger.warning("Ossie 文档解析失败: datasource_id=%s error=%s", datasource_id, e)
        return None, business_response(msg=str(e), code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")
    if duplicate_error:
        return None, business_response(msg=duplicate_error, code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")
    return imports, None


@router.post("/admin/datasources/{datasource_id}/semantic-configs/ossie-import/preview", response_model=None)
async def preview_admin_ossie_semantic_import(
    datasource_id: str,
    payload: SemanticOssieImportRequest,
    request: Request,
) -> JSONResponse | list[dict[str, Any]]:
    """解析 Ossie 语义模型 YAML 并预览导入结果：逐模型返回映射草稿、警告与阻断问题，不落库。

    可导入的模型同时附带质量检查报告（quality 字段），导入前即可看到优化空间。
    """
    try:
        prepared = await _prepare_ossie_import(datasource_id, payload, request)
        if prepared[1] is not None:
            return prepared[1]
        imports = prepared[0]
        user_id = str(_get_current_user(request).user_id)
        physical_columns = await _load_physical_columns(get_datasource_manager(), datasource_id)
        # 仅填空字典闭环：对照现有字典逐模型计算填充计划，作为 preview 确认面
        existing_column_dicts = await get_semantic_config_service().get_column_dicts_async(datasource_id)
        # 质量检查（单模型 5 维 + 跨模型口径）统一编排入口，与 check_yaml 工具同源
        findings_by_model = await quality_findings_for_imports(
            imports, datasource_id, physical_columns=physical_columns, user_id=user_id
        )
        models = []
        for item in imports:
            model = item.to_preview_payload()
            dict_fill_plan = plan_column_dict_fills(item, existing_column_dicts)
            model["column_dict_fill_plan"] = dict_fill_plan
            findings = None
            if item.importable:
                # 只透出触发的 findings：评分无法指导动作，预览链路不输出 score/grade
                findings = findings_by_model.get(item.name, [])
                model["quality"] = {"findings": findings}
            model["checklist"] = build_model_checklist(
                item,
                validation_error=item.validation_error,
                quality_findings=findings,
                inventory_available=physical_columns is not None,
                dict_fill_plan=dict_fill_plan,
            )
            models.append(model)
        importable_count = len([item for item in imports if item.importable])
        return business_response(
            data={"models": models, "total": len(models), "importable": importable_count},
            msg="解析完成",
        )
    except Exception as e:
        logger.error(f"Ossie 导入预览失败: datasource_id={datasource_id} error={str(e)}", exc_info=True)
        return business_response(
            msg="Ossie 导入预览失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error"
        )


def _collect_metric_dimension_tables(config: dict[str, Any]) -> set:
    """收集单个配置中指标/维度引用的物理表名。"""
    tables: set = set()
    for item in [*(config.get("metrics") or []), *(config.get("dimensions") or [])]:
        if isinstance(item, dict) and item.get("table"):
            tables.add(str(item["table"]).strip())
    return tables


def _collect_relationship_tables(config: dict[str, Any]) -> set:
    """收集单个配置中关系两侧引用的物理表名。"""
    tables: set = set()
    for relationship in config.get("relationships") or []:
        if isinstance(relationship, dict):
            for key in ("left_table", "right_table"):
                value = str(relationship.get(key) or "").strip()
                if value:
                    tables.add(value)
    return tables


def _collect_modeled_tables(configs: list[dict[str, Any]]) -> set:
    """汇总所有配置引用过的物理表名：指标/维度的 table，以及关系两侧表名。"""
    modeled_tables: set = set()
    for config in configs:
        modeled_tables.update(_collect_metric_dimension_tables(config))
        modeled_tables.update(_collect_relationship_tables(config))
    return modeled_tables


@router.get("/admin/datasources/{datasource_id}/semantic-configs/quality-check")
async def check_admin_semantic_config_quality(
    datasource_id: str,
    request: Request,
) -> JSONResponse:
    """对数据源下公共语义配置执行质量检查：逐配置返回评分与问题清单，并给出数据源级汇总。

    与后台维护页口径一致，只检公共配置（is_public_semantic_config），
    不触碰、不透出任何用户私有配置的元信息。
    """
    try:
        _get_current_user(request)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        service = get_semantic_config_service()
        configs = [
            config
            for config in (await service.get_configs_async(datasource_id) or [])
            if is_public_semantic_config(config)
        ]
        physical_columns = await _load_physical_columns(ds_manager, datasource_id)
        checker = get_semantic_quality_checker()
        reports = [checker.check_config(config, physical_columns=physical_columns).to_payload() for config in configs]

        modeled_tables = _collect_modeled_tables(configs)
        inventory_tables = {table for table, _column in physical_columns} if physical_columns is not None else set()
        unmodeled_tables = sorted(inventory_tables - modeled_tables)
        # 跨模型口径检查（第二层）：组内公共配置之间的指标冲突，独立于单模型评分
        cross_model_findings = check_cross_model_metrics(configs)
        avg_score = round(sum(report["score"] for report in reports) / len(reports)) if reports else 100
        overall = {
            "config_count": len(reports),
            "avg_score": avg_score,
            "grade": grade_for_score(avg_score),
            "table_total": len(inventory_tables),
            "table_modeled": len(inventory_tables & modeled_tables),
            "unmodeled_tables": unmodeled_tables[:10],
            "unmodeled_table_count": len(unmodeled_tables),
            "inventory_available": physical_columns is not None,
            "cross_model_findings": cross_model_findings,
        }
        return business_response(data={"configs": reports, "overall": overall}, msg="质量检查完成")
    except Exception as e:
        logger.error(f"语义质量检查失败: datasource_id={datasource_id} error={str(e)}", exc_info=True)
        return business_response(
            msg="语义质量检查失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error"
        )


@router.post("/admin/datasources/{datasource_id}/semantic-configs/ossie-import")
async def import_admin_ossie_semantic_configs(
    datasource_id: str,
    payload: SemanticOssieImportRequest,
    request: Request,
) -> JSONResponse:
    """导入 Ossie 语义模型 YAML 为禁用状态的公共语义配置草稿；任一模型存在阻断问题时整体不创建。

    同名冲突由 conflict_actions 决定：replace=覆盖同名公共配置内容（保留 config_id，
    强制禁用）；skip=跳过该模型；缺省按 skip 处理。同名但不可操作的配置
    （如他人私有配置）一律强制跳过，不允许 replace。
    整批落库在同一事务内完成：任一模型写入失败即整体回滚，不留半批草稿。
    """
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        conflict_actions, action_error = _normalize_conflict_actions(payload)
        if action_error:
            return business_response(
                msg=action_error,
                code=ErrorCode.CHATDATA_VALIDATION_FAILED,
                status="error",
            )
        prepared = await _prepare_ossie_import(datasource_id, payload, request)
        if prepared[1] is not None:
            return prepared[1]
        imports = prepared[0]
        if any(not item.importable for item in imports):
            return business_response(
                msg="存在无法导入的模型，请根据各模型的 errors 清单修复后重试",
                code=ErrorCode.CHATDATA_VALIDATION_FAILED,
                data={"models": [item.to_preview_payload() for item in imports]},
                status="error",
            )
        semantic_service = get_semantic_config_service()
        ds_manager = get_datasource_manager()
        # 整批落库放进同一事务：任一模型写入失败即整体回滚，避免留下半批草稿
        # （接口对外语义是「全有或全无」）。字典仅填空写入也在同一事务内，
        # 模型与字典要么一起落库、要么一起回滚。
        try:
            created, replaced, skipped = await persist_ossie_imports_in_transaction(
                imports,
                conflict_actions,
                semantic_service,
                ds_manager,
                datasource_id,
                user_id,
                username,
            )
        except _OssieImportWriteError as e:
            logger.error("Ossie 导入写入失败，已回滚: datasource_id=%s error=%s", datasource_id, e, exc_info=True)
            return business_response(
                msg=f"导入失败，已回滚：{e}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error"
            )
        except ValueError as e:
            logger.warning("Ossie 导入内容校验失败，已回滚: datasource_id=%s error=%s", datasource_id, e)
            return business_response(
                msg=f"导入失败，已回滚：{e}", code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error"
            )
        logger.info(
            "Ossie 导入草稿: datasource_id=%s created=%s replaced=%s skipped=%s",
            datasource_id,
            [item["config_id"] for item in created],
            [item["config_id"] for item in replaced],
            [item["name"] for item in skipped],
        )
        return business_response(
            data={"created": created, "replaced": replaced, "skipped": skipped},
            msg="导入成功，配置为禁用状态，请人工确认后启用",
        )
    except Exception as e:
        logger.error(f"Ossie 导入失败: datasource_id={datasource_id} error={str(e)}", exc_info=True)
        return business_response(
            msg="Ossie 导入失败，请稍后重试", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error"
        )


def _normalize_conflict_actions(payload: SemanticOssieImportRequest) -> tuple[dict[str, str], str | None]:
    """归一化 conflict_actions 为「模型名 -> 小写动作」。

    存在 replace/skip 之外的动作时返回错误文案，调用方据此直接回错误响应。
    """
    actions = {
        str(name).strip(): str(action).strip().lower() for name, action in (payload.conflict_actions or {}).items()
    }
    invalid_actions = sorted({action for action in actions.values() if action not in {"replace", "skip"}})
    if invalid_actions:
        return actions, f"conflict_actions 只支持 replace/skip，收到: {'、'.join(invalid_actions)}"
    return actions, None


async def _learn_semantic_config_from_history_route(
    datasource_id: str,
    config_id: str,
    payload: SemanticHistoryLearningRequest,
    request: Request,
    *,
    admin_manage: bool = False,
) -> JSONResponse:
    """基于当前指标集的智能问数/可视化查询历史生成维护建议。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        config = await get_semantic_config_service().get_config_async(datasource_id, config_id)
        if not config:
            return business_response(
                msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        if not can_mutate_semantic_config(config, user_id, admin_manage):
            return business_response(
                msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error"
            )

        data = await _build_history_learning_service().learn(
            datasource_id=datasource_id,
            config_id=config_id,
            user_id=user_id,
            datasource=datasource,
            config=config,
            days=payload.days,
            limit=payload.limit,
            sources=payload.sources,
            include_failed=payload.include_failed,
        )
        return business_response(data=data, msg="学习完成")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")
    except Exception as e:
        logger.error("历史学习失败: datasource_id=%s config_id=%s error=%s", datasource_id, config_id, e, exc_info=True)
        return business_response(msg=f"历史学习失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/{config_id}/history-learning")
async def learn_semantic_config_from_history(
    datasource_id: str,
    config_id: str,
    payload: SemanticHistoryLearningRequest,
    request: Request,
) -> JSONResponse:
    """基于个人指标集的查询历史生成维护建议。"""
    return await _learn_semantic_config_from_history_route(
        datasource_id, config_id, payload, request, admin_manage=False
    )


@router.post("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/history-learning")
async def learn_admin_semantic_config_from_history(
    datasource_id: str,
    config_id: str,
    payload: SemanticHistoryLearningRequest,
    request: Request,
) -> JSONResponse:
    """后台基于公共指标集的查询历史生成维护建议。"""
    _get_current_user(request)
    return await _learn_semantic_config_from_history_route(
        datasource_id, config_id, payload, request, admin_manage=True
    )


_SEMANTIC_UPDATE_PLAIN_FIELDS = ("name", "description", "business_notes", "enabled")
_SEMANTIC_UPDATE_LIST_FIELDS = ("metrics", "dimensions", "relationships")


def _build_semantic_update_kwargs(payload: SemanticConfigUpdateRequest) -> dict[str, Any]:
    """将更新请求转换为语义配置更新参数；None 字段不参与更新。"""
    update_kwargs: dict[str, Any] = {}
    for field in _SEMANTIC_UPDATE_PLAIN_FIELDS:
        value = getattr(payload, field)
        if value is not None:
            update_kwargs[field] = value
    for field in _SEMANTIC_UPDATE_LIST_FIELDS:
        items = getattr(payload, field)
        if items is not None:
            update_kwargs[field] = [item.model_dump() for item in items]
    return update_kwargs


async def _update_semantic_config(
    datasource_id: str,
    config_id: str,
    payload: SemanticConfigUpdateRequest,
    request: Request,
    *,
    admin_manage: bool = False,
) -> JSONResponse:
    """更新指标集。普通入口仅允许维护本人个人指标集；后台入口允许维护公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        semantic_service = get_semantic_config_service()
        current_config = await semantic_service.get_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(
                msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        if not can_mutate_semantic_config(current_config, user_id, admin_manage):
            return business_response(
                msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error"
            )

        update_kwargs = _build_semantic_update_kwargs(payload)

        merged_metrics = update_kwargs.get("metrics", current_config.get("metrics", []))
        merged_dimensions = update_kwargs.get("dimensions", current_config.get("dimensions", []))
        merged_relationships = update_kwargs.get("relationships", current_config.get("relationships", []))
        validation_error = await validate_semantic_config_for_save(
            ds_manager,
            datasource_id,
            datasource,
            merged_metrics,
            merged_dimensions,
            merged_relationships,
        )
        if validation_error:
            return business_response(msg=validation_error, code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error")

        config = await semantic_service.update_async(
            datasource_id=datasource_id,
            config_id=config_id,
            update_by=user_id,
            update_by_name=username,
            **update_kwargs,
        )
        if not config:
            return business_response(
                msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        logger.info(f"更新语义配置: {config_id}")
        return business_response(
            data=_with_semantic_config_permissions(config, user_id, username, admin_manage=admin_manage), msg="更新成功"
        )
    except binding_guard.SemanticOntologyBoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SEMANTIC_ONTOLOGY_BOUND, status="error")
    except Exception as e:
        logger.error(f"更新语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"更新失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


async def _validate_semantic_config_with_scope(
    datasource_id: str,
    payload: SemanticConfigCreateRequest,
    request: Request,
    *,
    exclude_config_id: str = "",
) -> JSONResponse:
    """保存前校验（dry-run）：结构校验 + 质量/跨模型口径检查，不落库。

    校验方法多处统一的编辑器入口：与导入 preview / check_yaml / admin 质量报告
    共用同一套 RULES。structure_error 非空表示结构校验未过（保存会被拦截）；
    quality_findings 为质量提示（弹窗展示后由用户决定是否仍要保存）。
    """
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        ds_manager = get_datasource_manager()
        datasource = await ds_manager.get_datasource_async(datasource_id)
        if not datasource:
            return business_response(
                msg=f"数据源不存在: {datasource_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        metrics = [m.model_dump() for m in payload.metrics]
        dimensions = [d.model_dump() for d in payload.dimensions]
        relationships = [r.model_dump() for r in payload.relationships]
        structure_error = await validate_semantic_config_for_save(
            ds_manager,
            datasource_id,
            datasource,
            metrics,
            dimensions,
            relationships,
        )
        quality_findings: list = []
        if not structure_error:
            quality_findings = await quality_findings_for_save(
                ds_manager,
                datasource_id,
                name=payload.name,
                metrics=metrics,
                dimensions=dimensions,
                relationships=relationships,
                exclude_config_id=exclude_config_id or None,
                semantic_service=get_semantic_config_service(),
                user_id=user_id,
            )
        return business_response(
            data={"structure_error": structure_error or "", "quality_findings": quality_findings},
            msg="校验完成",
        )
    except Exception as e:
        logger.error(f"保存前校验失败: {str(e)}", exc_info=True)
        return business_response(msg=f"校验失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.post("/datasources/{datasource_id}/semantic-configs/validate")
async def validate_semantic_config(
    datasource_id: str,
    payload: SemanticConfigCreateRequest,
    request: Request,
    exclude_config_id: str = "",
) -> JSONResponse:
    """个人指标集保存前校验（dry-run，不落库）。"""
    return await _validate_semantic_config_with_scope(
        datasource_id, payload, request, exclude_config_id=exclude_config_id
    )


@router.post("/admin/datasources/{datasource_id}/semantic-configs/validate")
async def validate_admin_semantic_config(
    datasource_id: str,
    payload: SemanticConfigCreateRequest,
    request: Request,
    exclude_config_id: str = "",
) -> JSONResponse:
    """公共指标集保存前校验（dry-run，不落库）。"""
    _get_current_user(request)
    return await _validate_semantic_config_with_scope(
        datasource_id, payload, request, exclude_config_id=exclude_config_id
    )


async def _delete_semantic_config(
    datasource_id: str, config_id: str, request: Request, *, admin_manage: bool = False
) -> JSONResponse:
    """删除指标集。普通入口仅允许维护本人个人指标集；后台入口允许维护公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        semantic_service = get_semantic_config_service()
        current_config = await semantic_service.get_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(
                msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        if not can_mutate_semantic_config(current_config, user_id, admin_manage):
            return business_response(
                msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error"
            )

        success = await semantic_service.delete_async(
            datasource_id, config_id, update_by=user_id, update_by_name=username
        )
        if not success:
            return business_response(
                msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        logger.info(f"删除语义配置: {config_id}")
        return business_response(data={"config_id": config_id}, msg="删除成功")
    except binding_guard.SemanticOntologyBoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SEMANTIC_ONTOLOGY_BOUND, status="error")
    except Exception as e:
        logger.error(f"删除语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"删除失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


async def _toggle_semantic_config(
    datasource_id: str, config_id: str, enabled: bool, request: Request, *, admin_manage: bool = False
) -> JSONResponse:
    """切换指标集启用状态。普通入口仅允许维护本人个人指标集；后台入口允许维护公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        semantic_service = get_semantic_config_service()
        current_config = await semantic_service.get_config_async(datasource_id, config_id)
        if not current_config:
            return business_response(
                msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )
        if not can_mutate_semantic_config(current_config, user_id, admin_manage):
            return business_response(
                msg="无权限操作该指标集", code=ErrorCode.CHATDATA_SEMANTIC_CONFIG_PERMISSION_DENIED, status="error"
            )

        config = await semantic_service.update_async(
            datasource_id=datasource_id,
            config_id=config_id,
            enabled=enabled,
            update_by=user_id,
            update_by_name=username,
        )
        if not config:
            return business_response(
                msg=f"语义配置不存在: {config_id}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error"
            )

        logger.info(f"切换语义配置: {config_id}, enabled={enabled}")
        data = _with_semantic_config_permissions(config, user_id, username, admin_manage=admin_manage)
        return business_response(data=data, msg=f"语义配置已{'启用' if enabled else '禁用'}")
    except Exception as e:
        logger.error(f"切换语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"操作失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.put("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def update_semantic_config(
    datasource_id: str,
    config_id: str,
    payload: SemanticConfigUpdateRequest,
    request: Request,
) -> JSONResponse:
    """更新个人指标集。"""
    return await _update_semantic_config(datasource_id, config_id, payload, request, admin_manage=False)


@router.put("/admin/datasources/{datasource_id}/semantic-configs/{config_id}")
async def update_admin_semantic_config(
    datasource_id: str,
    config_id: str,
    payload: SemanticConfigUpdateRequest,
    request: Request,
) -> JSONResponse:
    """后台更新公共指标集。"""
    _get_current_user(request)
    return await _update_semantic_config(datasource_id, config_id, payload, request, admin_manage=True)


@router.delete("/datasources/{datasource_id}/semantic-configs/{config_id}")
async def delete_semantic_config(datasource_id: str, config_id: str, request: Request) -> JSONResponse:
    """删除个人指标集。"""
    return await _delete_semantic_config(datasource_id, config_id, request, admin_manage=False)


@router.delete("/admin/datasources/{datasource_id}/semantic-configs/{config_id}")
async def delete_admin_semantic_config(datasource_id: str, config_id: str, request: Request) -> JSONResponse:
    """后台删除公共指标集。"""
    _get_current_user(request)
    return await _delete_semantic_config(datasource_id, config_id, request, admin_manage=True)


def _build_batch_toggle_message(result: dict[str, Any], enabled: bool) -> str:
    """拼装批量切换结果提示：主结果 + 跳过原因统计。"""
    action = "启用" if enabled else "禁用"
    parts = [f"已{action} {len(result.get('updated_ids') or [])} 个指标集"]
    skipped_count = len(result.get("skipped_ids") or [])
    not_found_count = len(result.get("not_found_ids") or [])
    if skipped_count:
        parts.append(f"{skipped_count} 个无权限")
    if not_found_count:
        parts.append(f"{not_found_count} 个不存在")
    return "，".join(parts)


async def _batch_toggle_semantic_configs(
    datasource_id: str,
    payload: SemanticConfigBatchToggleRequest,
    request: Request,
    *,
    admin_manage: bool = False,
) -> JSONResponse:
    """批量切换指标集启用状态。普通入口仅允许维护本人个人指标集；后台入口允许维护公共指标集。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        username = str(getattr(current_user, "username", "") or "")
        config_ids = dedupe_semantic_config_ids(payload.config_ids)
        if not config_ids:
            return business_response(
                msg="请至少选择一个指标集", code=ErrorCode.CHATDATA_VALIDATION_FAILED, status="error"
            )
        if len(config_ids) > SEMANTIC_BATCH_TOGGLE_MAX_IDS:
            return business_response(
                msg=f"单次批量操作最多支持 {SEMANTIC_BATCH_TOGGLE_MAX_IDS} 个指标集",
                code=ErrorCode.CHATDATA_VALIDATION_FAILED,
                status="error",
            )

        result = await get_semantic_config_service().batch_toggle_enabled_async(
            datasource_id=datasource_id,
            config_ids=config_ids,
            enabled=payload.enabled,
            user_id=user_id,
            username=username,
            admin_manage=admin_manage,
        )
        logger.info(
            f"批量切换语义配置: datasource={datasource_id}, enabled={payload.enabled}, "
            f"成功={len(result['updated_ids'])}, 无权限={len(result['skipped_ids'])}, 不存在={len(result['not_found_ids'])}"
        )
        return business_response(data=result, msg=_build_batch_toggle_message(result, payload.enabled))
    except Exception as e:
        logger.error(f"批量切换语义配置失败: {str(e)}", exc_info=True)
        return business_response(msg=f"操作失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.patch("/datasources/{datasource_id}/semantic-configs/{config_id}/toggle")
async def toggle_semantic_config(datasource_id: str, config_id: str, enabled: bool, request: Request) -> JSONResponse:
    """切换个人指标集启用状态。"""
    return await _toggle_semantic_config(datasource_id, config_id, enabled, request, admin_manage=False)


@router.patch("/admin/datasources/{datasource_id}/semantic-configs/{config_id}/toggle")
async def toggle_admin_semantic_config(
    datasource_id: str, config_id: str, enabled: bool, request: Request
) -> JSONResponse:
    """后台切换公共指标集启用状态。"""
    _get_current_user(request)
    return await _toggle_semantic_config(datasource_id, config_id, enabled, request, admin_manage=True)


@router.patch("/datasources/{datasource_id}/semantic-configs/batch-toggle")
async def batch_toggle_semantic_configs(
    datasource_id: str, payload: SemanticConfigBatchToggleRequest, request: Request
) -> JSONResponse:
    """批量切换个人指标集启用状态。"""
    return await _batch_toggle_semantic_configs(datasource_id, payload, request, admin_manage=False)


@router.patch("/admin/datasources/{datasource_id}/semantic-configs/batch-toggle")
async def batch_toggle_admin_semantic_configs(
    datasource_id: str, payload: SemanticConfigBatchToggleRequest, request: Request
) -> JSONResponse:
    """后台批量切换公共指标集启用状态。"""
    _get_current_user(request)
    return await _batch_toggle_semantic_configs(datasource_id, payload, request, admin_manage=True)
