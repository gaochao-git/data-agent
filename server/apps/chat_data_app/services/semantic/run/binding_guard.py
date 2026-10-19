"""语义模型 ↔ 本体映射绑定守护（悬空绑定硬阻断）。

本体映射表每行记着 semantic_config_id（挂在哪个语义模型上）。语义模型被删除、
或被删除了被映射引用的维度后，映射就成了悬空引用：本体目录的 file 字段指向
不存在的模型快照、关系列翻译失败、AI 读到坏条目。本模块在三条路径上做硬阻断：

- 单模型删除（SemanticConfigService.delete_async）；
- 模型更新删维度（update_async，按新旧维度名差集精确拦截，只拦受影响的绑定）；
- 数据源组删除（DatasourceManager.delete_datasource_async，组内模型批量软删）。

报错码统一 40070（CHATDATA_SEMANTIC_ONTOLOGY_BOUND），报错文案直接给出占用方
（对象名 + 被引用维度），引导用户先到本体管理解绑。独立成模块避免语义服务反向
依赖本体服务造成循环导入——这里只依赖 daos 层。

守护意图是防悬空，不是防删除本身：先解绑、再删除，数据始终一致。
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

from ....daos.ontology_dao import OntologyDao

logger = get_logger(__name__)


class SemanticOntologyBoundError(RuntimeError):
    """语义模型仍被本体对象类型映射占用（删除/删维度被阻断）→ 40070。"""


def removed_dimension_names(old_dimensions: Any, new_dimensions: Any) -> set[str]:
    """计算更新前后被删除（含改名，改名=旧名消失）的维度名集合。

    维度名是本体映射 column_mappings 的右值（本体层只引用模型维度名，不碰物理列），
    所以差集基准是维度 name，与 ontology_service._validate_mapping_dimensions 同口径。
    """

    def _names(dimensions: Any) -> set[str]:
        names: set[str] = set()
        for item in dimensions or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    names.add(name)
        return names

    return _names(old_dimensions) - _names(new_dimensions)


def filter_bindings_by_dimensions(bindings: list[dict[str, Any]], removed_names: set[str]) -> list[dict[str, Any]]:
    """筛出 column_mappings 引用了被删维度的绑定。

    返回 [{binding, referenced_dimensions: [...]}]，referenced_dimensions 为该绑定
    与被删维度名的交集（排序后输出，保证报错文案稳定）。
    """
    affected: list[dict[str, Any]] = []
    for binding in bindings or []:
        column_mappings = binding.get("column_mappings") or {}
        referenced = sorted(
            {str(dimension or "").strip() for dimension in column_mappings.values()} & set(removed_names)
        )
        if referenced:
            affected.append({"binding": binding, "referenced_dimensions": referenced})
    return affected


def build_unbound_message(bindings: list[dict[str, Any]], action: str) -> str:
    """整模型级阻断文案：列出占用对象，引导先解绑。"""
    owners = sorted(
        {
            f"{item.get('object_type_name') or item.get('object_type_id')}({item.get('semantic_config_id')})"
            for item in bindings
        }
    )
    return (
        f"无法{action}：语义模型仍被本体对象类型映射占用（{', '.join(owners)}），"
        "删除会导致本体映射悬空。请先在本体管理中解除对应对象的映射，再执行删除。"
    )


def build_dimension_message(affected: list[dict[str, Any]], config_id: str, action: str) -> str:
    """维度差集阻断文案：只列受影响的对象与被引用维度。"""
    parts = sorted(
        {
            f"{item['binding'].get('object_type_name') or item['binding'].get('object_type_id')}"
            f"（引用维度: {', '.join(item['referenced_dimensions'])}）"
            for item in affected
        }
    )
    return (
        f"无法{action}：模型 {config_id} 的被删维度仍被本体映射引用（{'; '.join(parts)}），"
        "删除会导致本体映射悬空。请先解除相关映射，或保留这些维度。"
    )


def raise_if_bound(bindings: list[dict[str, Any]], action: str) -> None:
    """有活跃绑定即抛 40070（单模型删除 / 组删除共用）。"""
    if bindings:
        raise SemanticOntologyBoundError(build_unbound_message(bindings, action))


def raise_if_dimensions_referenced(
    bindings: list[dict[str, Any]], removed_names: set[str], config_id: str, action: str
) -> None:
    """绑定引用了被删维度才抛 40070（模型更新路径，差集精确拦截）。"""
    affected = filter_bindings_by_dimensions(bindings, removed_names)
    if affected:
        raise SemanticOntologyBoundError(build_dimension_message(affected, config_id, action))


async def ensure_configs_unbound_async(config_ids: list[str], action: str = "删除") -> None:
    """校验语义模型集合均未被本体映射占用，否则抛 40070。"""
    bindings = await OntologyDao().find_bindings_for_configs_async(config_ids)
    raise_if_bound(bindings, action)


async def ensure_config_dimensions_unreferenced_async(
    config_id: str, removed_names: set[str], action: str = "更新"
) -> None:
    """校验被删维度未被该模型的本体绑定引用，否则抛 40070（无被删维度时不查库）。"""
    if not removed_names:
        return
    bindings = await OntologyDao().find_bindings_for_configs_async([config_id])
    raise_if_dimensions_referenced(bindings, removed_names, config_id, action)


async def ensure_group_unbound_async(group_id: str, action: str = "删除数据源组") -> None:
    """校验数据源组内模型均未被本体映射占用，且组未被本体领域引用，否则抛 40070。

    本体领域一对一绑定数据源组（领域是对象取数后端锚点）：组被领域引用时删除
    会让领域与对象映射悬空，因此与映射绑定同口径硬阻断。
    """
    dao = OntologyDao()
    bindings = await dao.find_bindings_for_group_async(group_id)
    raise_if_bound(bindings, action)
    domains = await dao.list_active_domain_names_by_group_async(group_id)
    if domains:
        raise SemanticOntologyBoundError(
            f"无法{action}：数据源组仍被本体领域引用（{', '.join(domains)}），"
            "删除会导致本体领域与对象映射悬空。请先删除或迁移相关本体领域后再执行删除。"
        )
