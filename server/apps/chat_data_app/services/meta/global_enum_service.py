"""全局枚举服务：枚举值域唯一权威的 CRUD、引用保护与统一解析。

设计要点（2026-10-15 定稿）：
- 全局枚举表 chat_data_meta_global_enum 跨数据源、跨环境共享；唯一键
  (business_domain, enum_name)，不同业务域同名枚举互相隔离，防止跨域干扰与
  误引用。数据字典列通过 dict_json 中的 enum_ref 引用，二选一：enum_ref 非空
  时本地 enum_values 必须为空，解析逻辑统一走 resolve_column_dicts_async
  单一公共函数（enum_ref 拆分也统一走 DAO 的 _split_enum_ref）。
- 物化缓存失效不靠枚举版本盐：resolve 发生在 column_dicts 进入物化之前，
  解析后的 enum_values 内容参与 _batch_cache_salt 哈希，全局枚举一改内容即变，
  自动物化自然感知，无需任何手动刷新或全表版本号（避免全公司放大重物化）。
- 删除保护与引用计数遵循「禁止数据库原生 JSON 语法」：LIKE 粗筛 + 应用层
  JSON 精确解析两步法（DAO 内实现）。
"""

from __future__ import annotations

import re
from typing import Any

from utils.logger import get_logger

from ...daos.meta_global_enum_dao import MetaGlobalEnumDao, _split_enum_ref

logger = get_logger(__name__)

META_ENUM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,128}$")
_ENUM_NAME_ERROR_MSG = "全局枚举名仅允许字母、数字、下划线，长度 1-128"


class MetaGlobalEnumNameInvalidError(ValueError):
    """枚举名格式非法。"""


class MetaGlobalEnumInvalidError(ValueError):
    """枚举内容非法（枚举值为空、与本地枚举二选一冲突等）。"""


class MetaGlobalEnumNotFoundError(LookupError):
    """全局枚举不存在。"""


class MetaGlobalEnumAlreadyExistsError(RuntimeError):
    """同业务域下已存在同名活跃枚举，且本次写入不允许覆盖。"""


class MetaGlobalEnumInUseError(RuntimeError):
    """全局枚举仍被数据字典引用，禁止删除。"""


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


class GlobalEnumService:
    """全局枚举业务规则与统一解析入口。"""

    def __init__(self, dao: MetaGlobalEnumDao | None = None) -> None:
        # 允许注入 DAO（测试用），默认单例 DAO
        self._dao = dao or MetaGlobalEnumDao()

    async def list_enums_async(self, keyword: str = "", business_domain: str | None = None) -> list[dict[str, Any]]:
        enums = await self._dao.list_enums_async(keyword, business_domain)
        counts = await self._dao.count_column_dict_refs_batch_async(
            [(str(item.get("business_domain") or ""), str(item.get("enum_name") or "")) for item in enums]
        )
        for item in enums:
            ref = (str(item.get("business_domain") or ""), str(item.get("enum_name") or ""))
            item["ref_count"] = counts.get(ref, 0)
        return enums

    async def list_enum_domains_async(self) -> list[str]:
        """已有业务域去重清单（供前端域选择器）。"""
        return await self._dao.list_enum_domains_async()

    async def get_enum_async(self, enum_name: str, business_domain: str = "") -> dict[str, Any] | None:
        return await self._dao.get_enum_by_name_async(enum_name, business_domain)

    async def list_enum_refs_async(self, enum_name: str, business_domain: str = "") -> list[dict[str, Any]]:
        """列出引用指定全局枚举的数据字典列（管理端「查看引用方」）。"""
        return await self._dao.find_column_dict_refs_async(enum_name, business_domain)

    async def upsert_enum_async(
        self,
        enum_name: str,
        enum_desc: str,
        enum_items: list[dict[str, Any]],
        business_domain: str = "",
        operator: str = "",
        operator_name: str = "",
        allow_overwrite: bool = False,
    ) -> dict[str, Any]:
        """按 (business_domain, enum_name) 创建/更新全局枚举。

        allow_overwrite=False（新建/存为模板）时，同域同名活跃枚举已存在则拒绝；
        True（编辑既有枚举）时正常覆盖。软删行复活不算覆盖。
        """
        name = _normalized_text(enum_name)
        if not META_ENUM_NAME_PATTERN.match(name):
            raise MetaGlobalEnumNameInvalidError(_ENUM_NAME_ERROR_MSG)
        items = [item for item in (enum_items or []) if _normalized_text((item or {}).get("value"))]
        if not items:
            raise MetaGlobalEnumInvalidError("全局枚举值列表不能为空")
        result = await self._dao.upsert_enum_async(
            _normalized_text(business_domain),
            name,
            _normalized_text(enum_desc)[:512],
            items,
            allow_overwrite=allow_overwrite,
            create_by=operator or "",
            create_by_name=operator_name or "",
            update_by=operator or "",
            update_by_name=operator_name or "",
        )
        if result is None:
            raise MetaGlobalEnumAlreadyExistsError(
                f"全局枚举已存在: {_normalized_text(business_domain)}:{name}，请换一个枚举名或直接编辑"
            )
        return result

    async def delete_enum_async(
        self, enum_name: str, business_domain: str = "", operator: str = "", operator_name: str = ""
    ) -> None:
        """软删全局枚举；仍被数据字典引用时拒绝删除。"""
        name = _normalized_text(enum_name)
        if not name:
            raise MetaGlobalEnumNotFoundError("全局枚举名不能为空")
        domain = _normalized_text(business_domain)
        existing = await self._dao.get_enum_by_name_async(name, domain)
        if existing is None:
            raise MetaGlobalEnumNotFoundError(f"全局枚举不存在: {domain}:{name}" if domain else name)
        refs = await self._dao.find_column_dict_refs_async(name, domain)
        if refs:
            sample = ", ".join(f"{ref['group_id']}/{ref['table_name']}/{ref['column_name']}" for ref in refs[:5])
            more = f" 等{len(refs)}处" if len(refs) > 5 else ""
            raise MetaGlobalEnumInUseError(f"全局枚举 {name} 仍被引用（{sample}{more}），请先解除引用")
        await self._dao.soft_delete_enum_async(
            name, domain, update_by=operator or "", update_by_name=operator_name or ""
        )

    async def validate_column_dict_for_write_async(self, column_dict: dict[str, Any]) -> None:
        """数据字典写入校验：enum_ref 二选一约束 + 引用存在性。

        供 datasource manager.replace_column_dict_async 写入前调用，所有
        破坏「引用态不含本地枚举」约束的写入在此拦截。
        """
        payload = column_dict if isinstance(column_dict, dict) else {}
        enum_ref = _normalized_text(payload.get("enum_ref"))
        if not enum_ref:
            return
        ref_domain, ref_name = _split_enum_ref(enum_ref)
        if not META_ENUM_NAME_PATTERN.match(ref_name):
            raise MetaGlobalEnumNameInvalidError(f"引用的全局枚举名非法（{_ENUM_NAME_ERROR_MSG}）: {enum_ref}")
        local_values = payload.get("enum_values") or payload.get("enums") or []
        if local_values:
            raise MetaGlobalEnumInvalidError(f"已引用全局枚举 {enum_ref}，不能再维护本地枚举值（二选一）")
        existing = await self._dao.get_enum_by_name_async(ref_name, ref_domain)
        if existing is None:
            raise MetaGlobalEnumNotFoundError(f"引用的全局枚举不存在: {enum_ref}")

    async def resolve_column_dicts_async(
        self, column_dicts: dict[Any, dict[str, Any] | None]
    ) -> dict[Any, dict[str, Any]]:
        """统一的全局枚举解析入口（所有消费方必须走此函数，不得各自实现）。

        输入为「列定位键 -> 字典 payload」映射（键可为 (table, column) 元组或
        列名，函数对键形态无感知）。enum_ref 非空的列以全局枚举内容覆盖
        enum_values（enum_ref 标记保留以便下游识别引用态）；全局枚举缺失或为空
        时降级保留本地内容并告警，不阻断物化链路。
        """
        refs = {
            _normalized_text((payload or {}).get("enum_ref"))
            for payload in column_dicts.values()
            if isinstance(payload, dict) and _normalized_text(payload.get("enum_ref"))
        }
        if not refs:
            return {key: payload for key, payload in column_dicts.items() if isinstance(payload, dict)}
        enum_items_by_ref: dict[str, list[dict[str, str]]] = {}
        for enum_ref in refs:
            ref_domain, ref_name = _split_enum_ref(enum_ref)
            enum_row = await self._dao.get_enum_by_name_async(ref_name, ref_domain)
            items = (enum_row or {}).get("enum_items") or []
            if not items:
                logger.warning("全局枚举不存在或为空，保留本地枚举: enum_ref=%s", enum_ref)
                continue
            enum_items_by_ref[enum_ref] = items
        resolved: dict[Any, dict[str, Any]] = {}
        for key, payload in column_dicts.items():
            if not isinstance(payload, dict):
                continue
            enum_ref = _normalized_text(payload.get("enum_ref"))
            items = enum_items_by_ref.get(enum_ref)
            if not enum_ref or not items:
                resolved[key] = payload
                continue
            merged = dict(payload)
            # 拷贝枚举项，避免多份物化缓存共享同一列表引用被意外修改
            merged["enum_values"] = [dict(item) for item in items]
            resolved[key] = merged
        return resolved


global_enum_service = GlobalEnumService()
