"""ChatData 技能管理服务。"""

import asyncio
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from sqlalchemy.exc import IntegrityError

from apps.chat_data_app.daos.skill_dao import SkillDao
from apps.chat_data_app.services.agent_run.conversation_workspace import (
    WORKSPACE_RAW_PREVIEW_MAX_BYTES,
    _format_size_limit,
)
from apps.chat_data_app.services.agent_run.uploaded_file_service import (
    ALLOWED_FILE_EXTENSIONS,
    UPLOAD_CHUNK_SIZE,
)
from apps.chat_data_app.services.setting_service import get_runtime_setting, get_upload_size_limit
from utils.logger import get_logger
from utils.skill_paths import (
    SKILL_MD_MAX_BYTES,
    SYSTEM_SKILL_CATEGORY,
    USER_SKILL_CATEGORY,
    physical_skill_category_root,
)

from ..contracts.skill_source import get_skill_source, register_skill_snapshot_source
from . import SkillMetadata, activate_skill, clear_metadata_cache, load_metadata, parse_metadata

logger = get_logger(__name__)
USER_SKILL_ROOT = physical_skill_category_root(USER_SKILL_CATEGORY)
# 技能名口径对齐 Agent Skills 规范：仅小写字母、数字与连字符，不得以连字符开头/结尾，
# 不得出现连续连字符，且必须与父目录名一致。规范**不要求**必须含连字符（`dashboard` 这类
# 单词名合法），也**不禁止**数字（`pdf2x` 合法）——早前版本强制「至少一个中划线」并禁用数字，
# 比规范更严且与前端校验不一致（前端放行 `sales`、后端拒绝），故改为 `*` 并放开 0-9。
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# 技能固定目录只保留 references 与 assets。历史上还有 scripts（可复用脚本），
# 因一直未实际使用且脚本类内容存在安全顾虑，已于 2026-09-16 整体移除：
# 不再创建、不再列出、不再允许读写该目录。
SKILL_FIXED_DIRS = ("references", "assets")

# 技能文件上传大小上限：直接复用主对话上传口径（CHAT_DATA_MAX_UPLOAD_SIZE_MB，
# 经后台配置变量表读取，见 services/setting_service.py 的 get_upload_size_limit），
# 与主对话上传体验保持一致。
# 预览各项上限（文本/原始字节/Office 源文件）同样复用主对话工作区预览端点的常量，
# env 单一来源在 services/runtime_config.py，此处不重复定义。

# 技能文件上传类型白名单：直接复用主对话上传口径（ALLOWED_FILE_EXTENSIONS，见
# services/agent_run/uploaded_file_service.py），两处体验保持一致。
_SKILL_UPLOAD_ALLOWED_SUFFIXES = {f".{ext}" for ext in ALLOWED_FILE_EXTENSIONS}


def _user_skill_root() -> Path:
    """返回用户技能物理根目录。"""
    return USER_SKILL_ROOT.expanduser().resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_user_skill_path(path: Path, field_name: str = "技能路径") -> Path:
    """解析并确保用户技能路径没有越过用户技能根目录。"""
    root = _user_skill_root()
    resolved = Path(path).expanduser().resolve()
    if not _is_relative_to(resolved, root):
        raise ValueError(f"{field_name}越界")
    return resolved


def _derive_user_skill_root_dir(owner_user_id: Any, skill_name: Any) -> Path:
    """按 ``owner_user_id + skill_name`` 推导用户技能物理根目录。

    物理路径不入库，读取侧一律按归属用户与技能标识推导，并限制在用户技能根目录下。
    """
    user_part = str(owner_user_id or "").strip()
    name_part = str(skill_name or "").strip()
    base = _user_skill_root()
    candidate = base / user_part / name_part if user_part else base / name_part
    return _resolve_user_skill_path(candidate, "技能根目录")


def _derive_user_version_dir(owner_user_id: Any, skill_name: Any, version_no: Any) -> Path:
    """按 ``owner_user_id + skill_name + skill_version_no`` 推导用户技能版本物理目录。

    物理路径不入库，读取侧一律按归属用户、技能标识与版本号推导，并限制在用户技能根目录下。
    """
    root_dir = _derive_user_skill_root_dir(owner_user_id, skill_name)
    return _resolve_user_skill_path(root_dir / "versions" / f"v{int(version_no or 0)}", "技能版本目录")


def _resolve_user_skill_version_dir(path_value: Any, field_name: str = "技能版本目录") -> Path:
    """解析代码内部推导出的用户技能版本目录，并限制在用户技能根目录下。"""
    raw = str(path_value or "").strip()
    if not raw or raw == ".":
        raise ValueError("技能版本目录无效")
    return _resolve_user_skill_path(Path(raw), field_name)


def _list_skill_files(skill_path: str) -> dict[str, list[str]]:
    """列出技能参考文档和素材（含后缀的真实文件名，references 不限文档格式）。"""
    root = Path(skill_path).parent
    references_root = root / "references"
    assets_root = root / "assets"
    references = (
        [
            str(item.relative_to(references_root))
            for item in references_root.rglob("*")
            if item.is_file() and not item.is_symlink()
        ]
        if references_root.exists() and not references_root.is_symlink()
        else []
    )
    assets = (
        [
            str(item.relative_to(assets_root))
            for item in assets_root.rglob("*")
            if item.is_file() and not item.is_symlink()
        ]
        if assets_root.exists() and not assets_root.is_symlink()
        else []
    )
    return {
        "references": sorted(references),
        "assets": sorted(assets),
    }


def _ensure_skill_dirs(version_dir: Path) -> None:
    """确保技能版本固定目录存在。"""
    if str(version_dir).strip() in {"", "."}:
        raise ValueError("技能版本目录无效")
    for dir_name in SKILL_FIXED_DIRS:
        target_dir = version_dir / dir_name
        if target_dir.is_symlink():
            raise ValueError("技能目录不允许包含软链接")
        target_dir.mkdir(parents=True, exist_ok=True)


def _strip_frontmatter(text: str) -> str:
    """去掉 SKILL.md frontmatter，返回正文。"""
    match = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text or "", re.DOTALL)
    return match.group(1).strip() if match else str(text or "")


def _safe_copy_skill_subdir(source_subdir: Path, target_subdir: Path) -> None:
    """把单个技能固定子目录（`references`/`assets`）的文件复制到目标目录。

    沿用原 ``_safe_copy_skill_dir`` 的校验与跳过语义：软链接与非文件跳过/拒绝，
    含 ``..`` 的相对路径视为非法路径，逐文件创建父目录后复制。
    """
    for item in sorted(source_subdir.rglob("*")):
        if item.is_symlink():
            raise ValueError("技能目录不允许包含软链接")
        if not item.is_file():
            continue
        relative_path = item.relative_to(source_subdir)
        if ".." in relative_path.parts:
            raise ValueError("技能目录包含非法路径")
        target = target_subdir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


async def _append_runtime_user_skill_source(
    out: list[tuple[str, Path]],
    row: dict[str, Any],
    dao: "SkillDao",
) -> None:
    """按一行用户技能记录解析并追加其运行期版本目录到 ``out``。

    仅当该技能为 user 来源、存在当前版本号、已发布版本目录存在且不越界时追加；
    任何解析异常（权限/路径/缺失）交由 ``list_runtime_skill_sources_async`` 外层
    循环捕获后跳过该行，不中断整个快照构建。
    """
    if row.get("skill_source") != "user":
        return
    current_version_no = int(row.get("current_skill_version_no") or 0)
    if not current_version_no:
        return
    version = await dao.get_published_version_async(str(row.get("skill_id") or ""), current_version_no)
    if not version:
        return
    version_dir = _derive_user_version_dir(
        row.get("owner_user_id"),
        row.get("skill_name"),
        version.get("skill_version_no"),
    )
    if version_dir.is_dir() and not version_dir.is_symlink():
        out.append((str(row.get("skill_name") or ""), version_dir))


def _safe_copy_skill_dir(source_dir: Path, version_dir: Path) -> None:
    """复制技能固定目录文件到用户技能版本目录。"""
    target_version_dir = _resolve_user_skill_version_dir(version_dir)
    for dir_name in SKILL_FIXED_DIRS:
        source_subdir = source_dir / dir_name
        if not source_subdir.exists():
            continue
        if source_subdir.is_symlink() or not source_subdir.is_dir():
            raise ValueError(f"{dir_name} 必须是目录")
        target_subdir = target_version_dir / dir_name
        _safe_copy_skill_subdir(source_subdir, target_subdir)


def _reject_symlink_tree(root: Path) -> None:
    """拒绝复制包含软链接的技能版本目录。"""
    if root.is_symlink():
        raise ValueError("技能目录不允许包含软链接")
    for item in root.rglob("*"):
        if item.is_symlink():
            raise ValueError("技能目录不允许包含软链接")


def _copy_version_dir(source_dir: Path, target_dir: Path) -> None:
    """复制技能版本目录作为新草稿基础。"""
    target = _resolve_user_skill_version_dir(target_dir)
    if target.exists():
        raise FileExistsError(f"技能版本目录已存在: {target}")
    if source_dir.exists():
        source = _resolve_user_skill_version_dir(source_dir, "来源技能版本目录")
        _reject_symlink_tree(source)
        shutil.copytree(source, target, symlinks=False, ignore_dangling_symlinks=False)
    else:
        _ensure_skill_dirs(target)


def _alpha_suffix(index: int) -> str:
    """生成不含数字的序号后缀。"""
    value = int(index or 0)
    chars: list[str] = []
    while value > 0:
        value -= 1
        chars.append(chr(ord("a") + value % 26))
        value //= 26
    return "".join(reversed(chars)) or "a"


def _get_creator_name(row: dict[str, Any], user_names: dict[str, str] | None = None) -> str:
    creator_id = str(row.get("create_by") or row.get("owner_user_id") or "").strip()
    return str(row.get("create_by_name") or (user_names or {}).get(creator_id) or "").strip()


def _is_skill_owner(row: dict[str, Any], user_id: str = "") -> bool:
    return bool(user_id and str(row.get("owner_user_id") or "") == str(user_id))


def _can_manage_skill(row: dict[str, Any], user_id: str = "") -> bool:
    return bool(row.get("skill_source") == "user" and _is_skill_owner(row, user_id))


def _skill_row_to_info(
    row: dict[str, Any], user_id: str = "", user_names: dict[str, str] | None = None
) -> dict[str, Any]:
    """将数据库技能行转换为前端技能对象。"""
    can_manage = _can_manage_skill(row, user_id)
    info = _skill_row_base_info(row)
    info.update(_skill_row_manage_info(row, can_manage, user_id, user_names))
    return info


def _skill_row_base_info(row: dict[str, Any]) -> dict[str, Any]:
    """技能行的身份与展示字段。"""
    return {
        "skill_id": row.get("skill_id"),
        "name": row.get("skill_name"),
        "title": row.get("skill_title") or row.get("skill_name"),
        "description": row.get("skill_desc") or "",
        "category": row.get("skill_category") or "",
        "output_type": "text",
        "source": row.get("skill_source") or "user",
        "visibility_type": row.get("visibility_type") or "private",
        "publish_status": row.get("skill_publish_status") or "draft",
    }


def _skill_row_manage_info(
    row: dict[str, Any],
    can_manage: bool,
    user_id: str,
    user_names: dict[str, str] | None,
) -> dict[str, Any]:
    """技能行的版本/权限/审计字段。"""
    creator_id = str(row.get("create_by") or row.get("owner_user_id") or "").strip()
    return {
        "current_version_no": int(row.get("current_skill_version_no") or 0),
        "draft_version_no": int(row.get("draft_skill_version_no") or 0) if can_manage else 0,
        "has_draft": bool(row.get("draft_skill_version_no")) if can_manage else False,
        "owner_user_id": row.get("owner_user_id") or "",
        "creator_id": creator_id,
        "creator_name": _get_creator_name(row, user_names=user_names),
        "can_manage": can_manage,
        "favorite": bool(row.get("favorite_flag")),
        "enabled": bool(row.get("enabled_flag", 1)),
        "create_time": str(row.get("create_time") or ""),
        "update_time": str(row.get("update_time") or ""),
        "create_by_name": row.get("create_by_name") or "",
        "update_by": row.get("update_by") or "",
        "update_by_name": row.get("update_by_name") or "",
    }


def _skill_version_to_info(version: dict[str, Any], current_version_no: int = 0) -> dict[str, Any]:
    """将技能版本行转换为前端版本对象。"""
    version_no = int(version.get("skill_version_no") or 0)
    return {
        "skill_id": version.get("skill_id"),
        "skill_version_no": version_no,
        "version_publish_status": version.get("version_publish_status") or "draft",
        "current": bool(current_version_no and version_no == int(current_version_no or 0)),
        "create_time": str(version.get("create_time") or ""),
        "update_time": str(version.get("update_time") or ""),
        "create_by": version.get("create_by") or "",
        "create_by_name": version.get("create_by_name") or "",
        "update_by": version.get("update_by") or "",
        "update_by_name": version.get("update_by_name") or "",
    }


def _system_metadata_to_info(item: Mapping[str, Any]) -> dict[str, Any]:
    """将文件系统系统技能元数据转换为前端技能对象。"""
    return {
        "skill_id": item.get("name"),
        "name": item.get("name"),
        "title": item.get("title") or item.get("name"),
        "description": item.get("description") or "",
        "domain": item.get("domain") or "",
        "category": item.get("category") or "",
        "output_type": item.get("output_type") or "text",
        "input_schema": item.get("input_schema") or {},
        "source": "system",
        "visibility_type": "global",
        "publish_status": "published",
        "current_version_no": 1,
        "can_manage": False,
        "favorite": False,
        "enabled": True,
    }


def _plugin_skill_rows(existing_names: set[str]) -> list[dict[str, Any]]:
    """将已启用插件声明的技能转为前端展示行（source=plugin）。

    与会话技能快照同语义：与系统/用户技能同名时跳过（快照为扁平 /mnt/skills/<name>）。
    """
    from ..contracts.skill_source import get_skill_source
    from .loader import parse_metadata

    rows: list[dict[str, Any]] = []
    source = get_skill_source()
    for name, skill_dir in source.enabled_plugin_skill_sources() if source is not None else []:
        if name in existing_names:
            continue
        try:
            meta = parse_metadata(skill_dir / "SKILL.md")
        except Exception as err:
            logger.warning("读取插件技能元数据失败，跳过展示: %s: %s", name, err)
            continue
        if not meta:
            continue
        existing_names.add(name)
        info = _system_metadata_to_info(meta)
        info["source"] = "plugin"
        info["skill_id"] = f"plugin:{name}"
        rows.append(info)
    return rows


def _build_skill_md(payload: dict[str, Any]) -> str:
    """根据用户输入生成标准 SKILL.md 内容。"""
    skill_name = str(payload.get("skill_name") or payload.get("name") or "").strip()
    skill_title = str(payload.get("skill_title") or payload.get("title") or skill_name).strip()
    skill_desc = str(payload.get("skill_desc") or payload.get("description") or "").strip()
    skill_category = str(payload.get("skill_category") or payload.get("category") or "custom").strip()
    content = str(payload.get("content") or "").strip()
    if content.startswith("---"):
        return content + "\n"
    body = (
        content
        or f"# {skill_title}\n\n## 适用目标\n\n## 输入信息\n\n## 工作流程\n\n## 约束规则\n\n## 输出格式\n\n## 完成判定\n"
    )
    return (
        "---\n"
        f"name: {skill_name}\n"
        f"title: {skill_title}\n"
        f"description: {skill_desc}\n"
        "metadata:\n"
        f"  category: {skill_category}\n"
        "  output_type: text\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )


def _validate_skill_payload(payload: dict[str, Any]) -> None:
    """校验用户技能基础字段。"""
    skill_name = str(payload.get("skill_name") or payload.get("name") or "").strip()
    skill_title = str(payload.get("skill_title") or payload.get("title") or "").strip()
    if not SKILL_NAME_PATTERN.match(skill_name):
        raise ValueError("技能名称只允许小写字母、数字和中划线，不能以中划线开头或结尾，也不能出现连续中划线")
    if len(skill_name) < 3 or len(skill_name) > 64:
        raise ValueError("技能名称长度必须在 3-64 之间")
    if not skill_title:
        raise ValueError("技能标题不能为空")


def _assert_skill_md_size(text: str) -> None:
    """SKILL.md 入口大小上限；超限要求拆分到 references。"""
    size = len(str(text).encode("utf-8"))
    if size > SKILL_MD_MAX_BYTES:
        raise ValueError(
            f"SKILL.md 不能超过 {SKILL_MD_MAX_BYTES // 1024}KB（当前 {size / 1024:.1f}KB）；"
            "请精简正文，把详细资料拆到 references/ 目录"
        )


def _write_skill_md(version_dir: Path, payload: dict[str, Any]) -> Path:
    """写入技能版本目录的 SKILL.md 并校验元数据。"""
    version_dir.mkdir(parents=True, exist_ok=True)
    _ensure_skill_dirs(version_dir)
    skill_md_path = version_dir / "SKILL.md"
    content = _build_skill_md(payload)
    _assert_skill_md_size(content)
    skill_md_path.write_text(content, encoding="utf-8")
    parsed = parse_metadata(skill_md_path)
    if not parsed:
        raise ValueError("SKILL.md 元数据无效")
    return skill_md_path


def _resolve_new_skill_text_fields(
    normalized_payload: dict[str, Any], parsed: Mapping[str, Any], skill_name: str
) -> tuple:
    """按 payload 别名、SKILL.md 元数据、默认值的优先级解析标题/描述/分类。"""
    title = (
        normalized_payload.get("skill_title") or normalized_payload.get("title") or parsed.get("title") or skill_name
    )
    desc = (
        normalized_payload.get("skill_desc") or normalized_payload.get("description") or parsed.get("description") or ""
    )
    category = (
        normalized_payload.get("skill_category")
        or normalized_payload.get("category")
        or parsed.get("category")
        or "custom"
    )
    return title, desc, category


class SkillService:
    """提供技能发现、详情和缓存刷新能力。"""

    def __init__(self) -> None:
        self._dao: SkillDao | None = None

    @property
    def dao(self) -> SkillDao:
        """懒加载数据库访问层，避免无数据库环境导入即失败。"""
        if self._dao is None:
            self._dao = SkillDao()
        return self._dao

    async def _safe_db_skills_async(
        self, user_id: str = "", include_disabled: bool = False
    ) -> list[dict[str, Any]] | None:
        """异步读取数据库用户技能；表未初始化时仍可使用文件系统系统技能。"""
        try:
            return await self.dao.list_skills_async(user_id=user_id, include_disabled=include_disabled)
        except Exception as err:
            logger.warning("读取技能表失败，仅返回系统技能: %s", err)
            return None

    def _collect_skill_source_rows(
        self,
        db_rows: list[dict[str, Any]] | None,
        system_metadata: dict[str, SkillMetadata],
        user_id: str,
        user_names: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        """合并 DB 用户技能行、文件系统系统技能与插件技能为统一的展示行。"""
        source_rows = (
            [
                _skill_row_to_info(row, user_id=user_id, user_names=user_names)
                for row in (db_rows or [])
                if row.get("skill_source") == "user"
            ]
            if db_rows is not None
            else []
        )
        for item in system_metadata.values():
            source_rows.append(_system_metadata_to_info(item))
        source_rows.extend(_plugin_skill_rows(existing_names={str(item.get("name") or "") for item in source_rows}))
        return source_rows

    @staticmethod
    def _skill_matches_scope(item: dict[str, Any], scope: str, user_id: str) -> bool:
        if scope == "favorites" and not item.get("favorite"):
            return False
        if scope == "mine" and str(item.get("owner_user_id") or "") != str(user_id or ""):
            return False
        if scope == "system" and item.get("source") != "system":
            return False
        return not (
            scope == "runtime" and item.get("source") == "user" and not int(item.get("current_version_no") or 0)
        )

    @staticmethod
    def _skill_matches_filter(item: dict[str, Any], keyword: str, normalized_category: str) -> bool:
        if normalized_category and item.get("category") != normalized_category:
            return False
        searchable = " ".join(
            [
                str(item.get("name") or ""),
                str(item.get("title") or ""),
                str(item.get("description") or ""),
                str(item.get("domain") or ""),
                str(item.get("category") or ""),
            ]
        ).lower()
        return not (keyword and keyword not in searchable)

    async def list_skills_async(
        self,
        search: str | None = None,
        category: str | None = None,
        user_id: str = "",
        scope: str = "all",
        user_names: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        source = get_skill_source()
        if source is not None:
            await source.refresh_plugin_state()
        db_rows = await self._safe_db_skills_async(user_id=user_id)
        keyword = str(search or "").strip().lower()
        normalized_category = str(category or "").strip()
        skills: list[dict[str, Any]] = []
        system_metadata = await asyncio.to_thread(load_metadata)
        source_rows = self._collect_skill_source_rows(db_rows, system_metadata, user_id, user_names)
        for item in source_rows:
            if not self._skill_matches_scope(item, scope, user_id):
                continue
            if not self._skill_matches_filter(item, keyword, normalized_category):
                continue
            skills.append(item)
        return sorted(skills, key=lambda value: (str(value.get("source") or ""), str(value.get("name") or "")))

    async def get_skill_async(
        self, name: str, include_draft: bool = False, user_id: str = "", user_names: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        skill = await self._get_system_skill_detail_async(name)
        if skill:
            return skill
        return await self._get_db_skill_detail_async(
            name, include_draft=include_draft, user_id=user_id, user_names=user_names
        )

    async def resolve_skill_path_async(self, name: str, user_id: str = "") -> dict[str, Any] | None:
        """异步解析可加载技能的 SKILL.md 路径。"""
        system_meta = await self._get_system_skill_metadata_async(name)
        if system_meta:
            return system_meta
        return await self._get_db_skill_metadata_async(name, user_id=user_id)

    async def list_runtime_skill_sources_async(self, user_id: str) -> list[tuple[str, Path]]:
        """返回运行期可加载技能的 ``(name, 当前版本目录)``。

        可见集合 = 系统技能 + 当前用户自己的当前版本 + 他人 ``visibility_type=global``
        的当前版本。``uniq_skill_name`` 保证名称全局唯一，调用方据此构建扁平快照。
        """
        source = get_skill_source()
        if source is not None:
            await source.refresh_plugin_state()
        out: list[tuple[str, Path]] = []
        system_root = physical_skill_category_root(SYSTEM_SKILL_CATEGORY)
        system_metadata = await asyncio.to_thread(load_metadata)
        for name in system_metadata:
            skill_dir = system_root / name
            if skill_dir.is_dir() and not skill_dir.is_symlink():
                out.append((name, skill_dir))
        # 插件技能：与系统/用户技能同名时跳过（快照是扁平 /mnt/skills/<name>，不允许重名）。
        existing_names = {name for name, _dir in out}
        plugin_sources = source.enabled_plugin_skill_sources() if source is not None else []
        for name, skill_dir in plugin_sources:
            if name in existing_names:
                logger.warning("插件技能与现有技能同名，跳过快照纳入: %s", name)
                continue
            existing_names.add(name)
            out.append((name, skill_dir))
        db_rows = await self._safe_db_skills_async(user_id=user_id) or []
        for row in db_rows:
            # 单个用户技能路径异常（如历史绝对路径、跨 CHAT_DATA_HOME 残留）不应中断
            # 整个快照构建，跳过该技能即可，系统技能与其他正常技能仍纳入快照。
            try:
                await _append_runtime_user_skill_source(out, row, self.dao)
            except Exception as err:
                logger.warning("跳过无法纳入快照的用户技能 %s: %s", row.get("skill_name"), err)
                continue
        return out

    async def _get_system_skill_metadata_async(self, name: str) -> dict[str, Any] | None:
        """异步读取文件系统系统技能元数据，不访问技能数据库。"""
        metadata = (await asyncio.to_thread(load_metadata)).get(name)
        if not metadata:
            clear_metadata_cache()
            metadata = (await asyncio.to_thread(load_metadata)).get(name)
        if not metadata:
            return None
        return {
            **dict(metadata),
            "source": "system",
        }

    async def _get_system_skill_detail_async(self, name: str) -> dict[str, Any] | None:
        """读取部署管理的系统技能详情。"""
        skill = await asyncio.to_thread(activate_skill, name)
        if not skill:
            clear_metadata_cache()
            skill = await asyncio.to_thread(activate_skill, name)
        if not skill:
            return None
        metadata = dict((await asyncio.to_thread(load_metadata)).get(name) or {})
        for key in ("name", "title", "description", "domain", "category", "output_type", "input_schema"):
            if key not in metadata and key in skill:
                metadata[key] = skill[key]
        file_info = await asyncio.to_thread(_list_skill_files, str((metadata or {}).get("path") or ""))
        return {
            **_system_metadata_to_info(metadata),
            "content": skill.get("content") or "",
            **file_info,
        }

    async def create_user_skill_async(
        self, payload: dict[str, Any], user_id: str, operator_name: str = ""
    ) -> dict[str, Any]:
        """异步创建用户技能草稿版本。"""
        normalized_payload = dict(payload or {})
        _validate_skill_payload(normalized_payload)
        skill_name = str(normalized_payload.get("skill_name") or normalized_payload.get("name") or "").strip()
        if await self._get_system_skill_metadata_async(skill_name):
            raise ValueError(f"技能标识已被系统技能占用: {skill_name}")
        existing = await self.dao.get_skill_async(skill_name, user_id=user_id)
        if (
            existing
            and existing.get("skill_source") == "user"
            and existing.get("owner_user_id") == user_id
            and existing.get("skill_publish_status") == "disabled"
        ):
            await self._archive_user_skill_async(existing, user_id, operator_name=operator_name)
        elif existing:
            raise ValueError(f"技能标识已存在: {skill_name}")
        if await self.dao.skill_name_exists_async(skill_name):
            raise ValueError(f"技能标识已存在: {skill_name}")
        skill_id = uuid.uuid4().hex
        version_no = 1
        # 物理路径不入库，按 owner_user_id + skill_name + version_no 推导。
        version_dir = _derive_user_version_dir(user_id, skill_name, version_no)
        skill_md_path = await asyncio.to_thread(_write_skill_md, version_dir, normalized_payload)
        # SKILL.md 由上一步刚写入，正常必可解析；防御解析失败时不再 AttributeError
        parsed_metadata = await asyncio.to_thread(parse_metadata, skill_md_path)
        parsed: Mapping[str, Any] = parsed_metadata or {}
        visibility_type = str(normalized_payload.get("visibility_type") or "private").strip()
        if visibility_type not in {"private", "global"}:
            visibility_type = "private"
        skill_title, skill_desc, skill_category = _resolve_new_skill_text_fields(normalized_payload, parsed, skill_name)
        try:
            await self.dao.create_skill_with_version_async(
                {
                    "skill_id": skill_id,
                    "skill_name": skill_name,
                    "skill_title": skill_title,
                    "skill_desc": skill_desc,
                    "skill_category": skill_category,
                    "skill_source": "user",
                    "visibility_type": visibility_type,
                    "enabled_flag": 1,
                    "skill_publish_status": "draft",
                    "current_skill_version_no": 0,
                    "owner_user_id": user_id,
                    "create_by": user_id,
                    "create_by_name": operator_name,
                    "update_by": user_id,
                    "update_by_name": operator_name,
                },
                {
                    "skill_id": skill_id,
                    "skill_version_no": version_no,
                    "version_publish_status": "draft",
                    "create_by": user_id,
                    "create_by_name": operator_name,
                    "update_by": user_id,
                    "update_by_name": operator_name,
                },
            )
        except IntegrityError as exc:
            # 并发创建同名技能时唯一键兜底，转成用户可理解的业务错误
            raise ValueError(f"技能标识已存在: {skill_name}") from exc
        return await self.get_skill_async(skill_id, include_draft=True, user_id=user_id) or {
            "skill_id": skill_id,
            "name": skill_name,
        }

    async def import_user_skill_from_directory_async(
        self, source_dir: Path, user_id: str, operator_name: str = ""
    ) -> dict[str, Any]:
        """异步从已生成的技能目录导入为用户技能草稿。"""
        resolved_source = source_dir.resolve()
        if not await asyncio.to_thread(resolved_source.is_dir):
            raise ValueError("技能目录不存在")
        skill_md_path = resolved_source / "SKILL.md"
        if await asyncio.to_thread(skill_md_path.is_symlink):
            raise ValueError("技能目录不允许包含软链接")
        if not await asyncio.to_thread(skill_md_path.is_file):
            raise ValueError("技能目录缺少 SKILL.md")
        parsed = await asyncio.to_thread(parse_metadata, skill_md_path)
        if not parsed:
            raise ValueError("SKILL.md 元数据无效")
        content = await asyncio.to_thread(skill_md_path.read_text, encoding="utf-8", errors="replace")
        _assert_skill_md_size(content)
        skill = await self.create_user_skill_async(
            {
                "skill_name": parsed.get("name") or resolved_source.name,
                "skill_title": parsed.get("title") or parsed.get("name") or resolved_source.name,
                "skill_desc": parsed.get("description") or "",
                "skill_category": parsed.get("category") or "custom",
                "visibility_type": "private",
                "content": content,
            },
            user_id=user_id,
            operator_name=operator_name,
        )
        skill_id = str(skill.get("skill_id") or "")
        draft_version_no = int(skill.get("draft_version_no") or 1)
        version_dir = await self._get_editable_version_dir_async(skill_id, draft_version_no, user_id)
        await asyncio.to_thread(_safe_copy_skill_dir, resolved_source, version_dir)
        clear_metadata_cache()
        return await self.get_skill_async(skill_id, include_draft=True, user_id=user_id) or skill

    async def copy_user_skill_async(self, skill_id: str, user_id: str, operator_name: str = "") -> dict[str, Any]:
        """异步复制可见用户技能为当前用户私有草稿。"""
        source_skill = await self._resolve_copy_source_skill_async(skill_id, user_id)
        source_version = await self._get_copy_source_version_async(source_skill, user_id)
        source_dir = _derive_user_version_dir(
            source_skill.get("owner_user_id"),
            source_skill.get("skill_name"),
            source_version.get("skill_version_no"),
        )
        parsed, content = await self._read_copy_source_content_async(source_dir)
        source_name = str(source_skill.get("skill_name") or parsed.get("name") or "user-skill").strip()
        source_title = str(source_skill.get("skill_title") or parsed.get("title") or source_name).strip()
        copied_name = await self._next_copy_skill_name_async(source_name, user_id)
        copied_skill = await self.create_user_skill_async(
            {
                "skill_name": copied_name,
                "skill_title": f"{source_title} 副本",
                "skill_desc": source_skill.get("skill_desc") or parsed.get("description") or "",
                "skill_category": source_skill.get("skill_category") or parsed.get("category") or "custom",
                "visibility_type": "private",
                "content": content,
            },
            user_id=user_id,
            operator_name=operator_name,
        )
        return await self._finalize_copied_skill_async(copied_skill, source_dir, user_id)

    async def _resolve_copy_source_skill_async(self, skill_id: str, user_id: str) -> dict[str, Any]:
        """校验并取回复制来源技能：系统技能不支持复制，不存在抛 ValueError。"""
        if await self._get_system_skill_metadata_async(skill_id):
            raise PermissionError("系统技能不支持复制")
        source_skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not source_skill:
            raise ValueError("技能不存在")
        if source_skill.get("skill_source") == "system":
            raise PermissionError("系统技能不支持复制")
        return source_skill

    async def _read_copy_source_content_async(self, source_dir: Path) -> tuple[dict[str, Any], str]:
        """读取来源 SKILL.md：目录无效抛 ValueError，返回 (frontmatter 元数据, 去 frontmatter 正文)。"""
        skill_md_path = source_dir / "SKILL.md"
        if not await asyncio.to_thread(skill_md_path.is_file):
            raise ValueError("技能版本目录无效")
        parsed = cast("dict[str, Any]", await asyncio.to_thread(parse_metadata, skill_md_path) or {})
        raw_content = await asyncio.to_thread(skill_md_path.read_text, encoding="utf-8", errors="replace")
        _assert_skill_md_size(raw_content)
        return parsed, _strip_frontmatter(raw_content)

    async def _finalize_copied_skill_async(
        self,
        copied_skill: dict[str, Any],
        source_dir: Path,
        user_id: str,
    ) -> dict[str, Any]:
        """把来源目录整体拷入新技能草稿版本，清缓存并返回最终详情。"""
        copied_skill_id = str(copied_skill.get("skill_id") or "")
        draft_version_no = int(copied_skill.get("draft_version_no") or 1)
        version_dir = await self._get_editable_version_dir_async(copied_skill_id, draft_version_no, user_id)
        await asyncio.to_thread(_safe_copy_skill_dir, source_dir, version_dir)
        clear_metadata_cache()
        return await self.get_skill_async(copied_skill_id, include_draft=True, user_id=user_id) or copied_skill

    async def update_user_skill_draft_async(
        self, skill_id: str, payload: dict[str, Any], user_id: str, operator_name: str = ""
    ) -> dict[str, Any]:
        """异步更新用户技能；只有正文变更才写入草稿版本。"""
        skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权编辑该技能")
        normalized_payload = {
            **payload,
            "skill_name": skill.get("skill_name"),
            "skill_title": payload.get("skill_title") or skill.get("skill_title") or skill.get("skill_name"),
        }
        _validate_skill_payload(normalized_payload)
        parsed = await self._write_skill_draft_if_needed(
            skill, normalized_payload, user_id, operator_name=operator_name
        )
        visibility_type = self._resolve_skill_visibility(payload, skill)
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        await self.dao.update_skill_async(
            str(skill.get("skill_id")),
            self._build_skill_update_fields(
                payload,
                skill,
                normalized_payload,
                parsed,
                visibility_type,
                current_version_no,
            ),
            update_by=user_id,
            update_by_name=operator_name,
        )
        clear_metadata_cache()
        return await self.get_skill_async(str(skill.get("skill_id")), include_draft=True, user_id=user_id) or {}

    async def _write_skill_draft_if_needed(
        self,
        skill: dict[str, Any],
        normalized_payload: dict[str, Any],
        user_id: str,
        *,
        operator_name: str,
    ) -> dict[str, Any]:
        """payload 携带正文时写入草稿版本并解析元数据；否则返回空 dict。"""
        if "content" not in normalized_payload:
            return {}
        draft = await self._ensure_user_skill_draft_version_async(skill, user_id, operator_name=operator_name)
        version_dir = _derive_user_version_dir(
            skill.get("owner_user_id"),
            skill.get("skill_name"),
            draft.get("skill_version_no"),
        )
        skill_md_path = await asyncio.to_thread(_write_skill_md, version_dir, normalized_payload)
        return cast("dict[str, Any]", await asyncio.to_thread(parse_metadata, skill_md_path) or {})

    @staticmethod
    def _resolve_skill_visibility(payload: dict[str, Any], skill: dict[str, Any]) -> str:
        """可见性取 payload > 现值 > private，非法值回退 private。"""
        visibility_type = str(payload.get("visibility_type") or skill.get("visibility_type") or "private").strip()
        if visibility_type not in {"private", "global"}:
            return "private"
        return visibility_type

    @staticmethod
    def _build_skill_update_fields(
        payload: dict[str, Any],
        skill: dict[str, Any],
        normalized_payload: dict[str, Any],
        parsed: dict[str, Any],
        visibility_type: str,
        current_version_no: int,
    ) -> dict[str, Any]:
        return {
            "skill_title": normalized_payload.get("skill_title") or parsed.get("title") or skill.get("skill_name"),
            "skill_desc": payload.get("skill_desc")
            if payload.get("skill_desc") is not None
            else skill.get("skill_desc"),
            "skill_category": payload.get("skill_category")
            if payload.get("skill_category") is not None
            else skill.get("skill_category"),
            "visibility_type": visibility_type,
            "skill_publish_status": "published" if current_version_no > 0 else "draft",
        }

    async def publish_user_skill_async(self, skill_id: str, user_id: str, operator_name: str = "") -> dict[str, Any]:
        """异步发布用户技能最新草稿版本。"""
        skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权发布该技能")
        if await self._get_system_skill_metadata_async(str(skill.get("skill_name") or "")):
            raise ValueError(f"技能标识与系统技能冲突: {skill.get('skill_name')}，请复制技能换新名称后再发布")
        draft = await self.dao.get_latest_draft_version_async(str(skill.get("skill_id")))
        if not draft:
            raise ValueError("没有可发布的草稿版本")
        await self.dao.publish_version_async(
            str(skill.get("skill_id")),
            int(draft.get("skill_version_no") or 0),
            update_by=user_id,
            update_by_name=operator_name,
        )
        clear_metadata_cache()
        return await self.get_skill_async(str(skill.get("skill_id")), include_draft=True, user_id=user_id) or {}

    async def list_skill_versions_async(self, skill_id: str, user_id: str) -> list[dict[str, Any]]:
        """列出系统技能固定版本或用户技能版本。"""
        system_version_dir = await self._get_system_version_dir_async(skill_id, 1)
        if system_version_dir is not None:
            return [
                {
                    "skill_id": skill_id,
                    "skill_version_no": 1,
                    "version_publish_status": "published",
                    "current": True,
                }
            ]
        skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权查看该技能版本")
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        versions = await self.dao.list_versions_async(str(skill.get("skill_id")))
        return [_skill_version_to_info(version, current_version_no=current_version_no) for version in versions]

    async def get_skill_version_detail_async(
        self, skill_id: str, skill_version_no: int, user_id: str
    ) -> dict[str, Any]:
        """读取系统技能固定版本或用户技能指定版本详情。"""
        system_version_dir = await self._get_system_version_dir_async(skill_id, skill_version_no)
        if system_version_dir is not None:
            skill_md = system_version_dir / "SKILL.md"
            content = await asyncio.to_thread(skill_md.read_text, encoding="utf-8", errors="replace")
            file_info = await asyncio.to_thread(_list_skill_files, str(skill_md))
            return {
                "skill_id": skill_id,
                "skill_version_no": 1,
                "version_publish_status": "published",
                "current": True,
                "content": _strip_frontmatter(content),
                **file_info,
            }
        skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权查看该技能版本")
        version = await self.dao.get_version_async(str(skill.get("skill_id")), int(skill_version_no or 0))
        if not version:
            raise ValueError("技能版本不存在")
        version_dir = _derive_user_version_dir(
            skill.get("owner_user_id"),
            skill.get("skill_name"),
            version.get("skill_version_no"),
        )
        await asyncio.to_thread(_ensure_skill_dirs, version_dir)
        skill_md = version_dir / "SKILL.md"
        content = (
            await asyncio.to_thread(skill_md.read_text, encoding="utf-8", errors="replace") if skill_md.exists() else ""
        )
        file_info = await asyncio.to_thread(_list_skill_files, str(skill_md))
        return {
            **_skill_version_to_info(version, current_version_no=int(skill.get("current_skill_version_no") or 0)),
            "content": _strip_frontmatter(content),
            **file_info,
        }

    async def list_skill_version_files_async(
        self, skill_id: str, skill_version_no: int, user_id: str
    ) -> dict[str, list[str]]:
        """列出系统技能或用户技能版本固定目录文件。"""
        version_dir = await self._get_readable_version_dir_async(skill_id, skill_version_no, user_id)
        return await asyncio.to_thread(_list_skill_files, str(version_dir / "SKILL.md"))

    async def read_skill_version_file_async(
        self,
        skill_id: str,
        skill_version_no: int,
        dir_name: str,
        file_path: str,
        user_id: str,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """异步读取用户技能版本目录文件。

        上限语义与主对话工作区文本预览（read_workspace_text_file）对齐：
        超过 max_bytes 直接拒绝预览，不做截断；None=运行期经配置服务解析
        （CHAT_DATA_TEXT_PREVIEW_MAX_MB，表覆盖 > env > 默认）。
        """
        if max_bytes is None:
            text_preview_max_mb = await get_runtime_setting("CHAT_DATA_TEXT_PREVIEW_MAX_MB")
            max_bytes = text_preview_max_mb * 1024 * 1024
        target = await self._resolve_readable_skill_file_path_async(
            skill_id, skill_version_no, dir_name, file_path, user_id
        )
        if not target.exists() or not target.is_file():
            raise ValueError("文件不存在")
        if target.stat().st_size > max_bytes:
            raise ValueError(f"文件过大，暂不支持预览超过 {_format_size_limit(max_bytes)} 的文件")
        raw = await asyncio.to_thread(target.read_bytes)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as err:
            # 上传通道允许二进制素材；可预览的类型（图片/PDF/Word）走 raw 端点，其余类型不提供在线查看
            raise ValueError("该文件类型不支持在线预览") from err
        return {
            "dir_name": dir_name,
            "file_path": str(target.relative_to(target.parents[1])),
            "content": content,
        }

    async def read_skill_version_file_raw_async(
        self,
        skill_id: str,
        skill_version_no: int,
        dir_name: str,
        file_path: str,
        user_id: str,
        max_bytes: int = WORKSPACE_RAW_PREVIEW_MAX_BYTES,
    ) -> tuple:
        """异步读取用户技能版本目录文件的原始字节，用于在线预览（图片/PDF）。

        上限与默认值同主对话工作区原始字节预览（read_workspace_raw_file 共用
        WORKSPACE_RAW_PREVIEW_MAX_BYTES 单一来源）；返回 (原始字节, content_type)，
        鉴权与路径校验同 read_skill_version_file_async。
        """
        target = await self._resolve_readable_skill_file_path_async(
            skill_id, skill_version_no, dir_name, file_path, user_id
        )
        if not target.exists() or not target.is_file():
            raise ValueError("文件不存在")
        size = target.stat().st_size
        if size > max_bytes:
            raise ValueError(f"文件过大，暂不支持预览超过 {_format_size_limit(max_bytes)} 的文件")
        content_type, _ = mimetypes.guess_type(target.name)
        if not content_type:
            content_type = "application/octet-stream"
        raw = await asyncio.to_thread(target.read_bytes)
        return raw, content_type

    async def render_skill_office_preview_async(
        self, skill_id: str, skill_version_no: int, dir_name: str, file_path: str, user_id: str
    ) -> dict[str, Any]:
        """渲染技能版本目录 Office 文件的 HTML 预览。

        复用主对话工作区的 OfficeCLI 渲染引擎（WorkspaceOfficePreviewService）。
        引擎的路径策略要求虚拟路径以 /mnt/user-data/ 开头且位于 workspace/outputs/uploads
        之下（resolve_workspace_path），技能版本目录不在该空间内，因此把源文件暂存到
        临时目录的 workspace/ 结构（硬链接优先、跨设备退回复制），以标准虚拟路径渲染；
        鉴权与路径校验同 read_skill_version_file_async。
        返回形状与 render_conversation_workspace_office_preview 一致：
        {"available": bool, "reason"?: str, "renderer"?: "officecli-html", "html"?: str}。
        """
        target = await self._resolve_readable_skill_file_path_async(
            skill_id, skill_version_no, dir_name, file_path, user_id
        )
        if not target.exists() or not target.is_file():
            raise ValueError("文件不存在")
        if target.suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
            raise ValueError("Office 预览仅支持 .docx、.xlsx、.pptx 文件")
        # 源文件上限与主对话 Office 预览校验（_validate_office_preview_source）同源：
        # 运行期经配置服务读取（CHAT_DATA_OFFICE_PREVIEW_SOURCE_MAX_MB）。
        office_source_max_mb = await get_runtime_setting("CHAT_DATA_OFFICE_PREVIEW_SOURCE_MAX_MB")
        office_source_max_bytes = office_source_max_mb * 1024 * 1024
        if target.stat().st_size > office_source_max_bytes:
            raise ValueError(f"文件过大，暂不支持预览超过 {_format_size_limit(office_source_max_bytes)} 的 Office 文件")
        # 局部导入：agent.workspace 链路较重，且避免 agent/skills 与 agent/workspace 潜在循环导入
        from apps.chat_data_app.agent.workspace.runtime import OFFICE_PREVIEW_MAX_CHARS, ConversationWorkspaceRuntime
        from apps.chat_data_app.agent.workspace.workspace_special_services import workspace_office_preview_service

        with tempfile.TemporaryDirectory(prefix="skill-office-preview-") as stage_root:
            stage_dir = Path(stage_root) / "workspace"
            stage_dir.mkdir()
            staged = stage_dir / target.name
            try:
                # 同盘硬链接零拷贝；跨设备等失败时退回复制
                os.link(target, staged)
            except OSError:
                await asyncio.to_thread(shutil.copyfile, target, staged)
            runtime = ConversationWorkspaceRuntime(
                workspace_root=stage_root,
                user_id=user_id,
                backend="local",
            )
            preview = await workspace_office_preview_service.render_html(
                runtime,
                file_path=f"/mnt/user-data/workspace/{target.name}",
                max_chars=OFFICE_PREVIEW_MAX_CHARS,
            )
        if bool(preview.get("truncated")):
            return {"available": False, "reason": "preview_too_large"}
        html = str(preview.get("html") or "")
        if not html:
            raise RuntimeError("OfficeCLI 未返回 HTML 预览内容")
        return {
            "available": True,
            "renderer": "officecli-html",
            "html": html,
            "truncated": False,
        }

    async def _resolve_writable_draft_file_async(
        self, skill_id: str, dir_name: str, file_path: str, user_id: str, operator_name: str = ""
    ) -> tuple:
        """校验归属与权限后，解析可写的用户技能草稿文件目标路径。

        save / upload / delete 三条文件写链路共用的前置逻辑：系统技能只读、
        仅属主可写、写操作一律落到草稿版本目录（与调用方传入的 version_no 无关，
        该参数仅是路由 API 形状，不参与本解析，故不接收）。
        返回 ``(draft, target_path)``。
        """
        try:
            skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        except Exception:
            if await self._get_system_skill_metadata_async(skill_id):
                raise PermissionError("无权管理该技能文件") from None
            raise
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权管理该技能文件")
        draft = await self._ensure_user_skill_draft_version_async(skill, user_id, operator_name=operator_name)
        target = self._resolve_user_file_under_version_dir(draft.get("skill_version_dir"), dir_name, file_path)
        return draft, target

    async def save_skill_version_file_async(
        self,
        skill_id: str,
        skill_version_no: int,
        dir_name: str,
        file_path: str,
        content: str,
        user_id: str,
        operator_name: str = "",
    ) -> dict[str, Any]:
        """异步保存用户技能版本目录文件。"""
        draft, target = await self._resolve_writable_draft_file_async(
            skill_id, dir_name, file_path, user_id, operator_name=operator_name
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, str(content or ""), encoding="utf-8")
        clear_metadata_cache()
        return {
            "dir_name": dir_name,
            "file_path": str(target.relative_to(target.parents[1])),
            "skill_version_no": int(draft.get("skill_version_no") or 0),
            "saved": True,
        }

    async def upload_skill_version_file_async(
        self,
        skill_id: str,
        skill_version_no: int,
        dir_name: str,
        filename: str,
        file_stream: Any,
        user_id: str,
        operator_name: str = "",
    ) -> dict[str, Any]:
        """异步上传二进制/文本文件到用户技能草稿版本固定目录。

        file_stream 为带 ``async read(n)`` 的文件对象（FastAPI UploadFile）：流式落盘
        并边写边限长（与主对话 uploaded_file_service.upload_file 同模式），不在内存整读。
        仅取原始文件名的 basename 落盘（防路径穿越）；同名文件按保存语义原子覆盖。
        """
        raw_name = Path(str(filename or "").replace("\\", "/")).name.strip()
        if not raw_name or raw_name in {".", ".."}:
            raise ValueError("文件名无效")
        suffix = Path(raw_name).suffix.lower()
        if suffix not in _SKILL_UPLOAD_ALLOWED_SUFFIXES:
            # 与主对话上传同一份类型白名单（含 references/assets 两个目录），防止绕过前端校验
            raise ValueError(
                f"不支持的文件类型: {suffix or '(无后缀)'}，仅支持 {', '.join(sorted(ALLOWED_FILE_EXTENSIONS))}"
            )
        draft, target = await self._resolve_writable_draft_file_async(
            skill_id, dir_name, raw_name, user_id, operator_name=operator_name
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target.parent / f".{target.name}.{uuid.uuid4().hex}.upload"
        file_size = 0
        max_upload_size_mb, max_upload_size = await get_upload_size_limit()
        try:
            with temporary_path.open("xb") as output:
                while chunk := await file_stream.read(UPLOAD_CHUNK_SIZE):
                    file_size += len(chunk)
                    if file_size > max_upload_size:
                        raise ValueError(f"文件大小不能超过{max_upload_size_mb}MB")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, target)
        finally:
            temporary_path.unlink(missing_ok=True)
        clear_metadata_cache()
        return {
            "dir_name": dir_name,
            "file_path": str(target.relative_to(target.parents[1])),
            "skill_version_no": int(draft.get("skill_version_no") or 0),
            "uploaded": True,
        }

    async def delete_skill_version_file_async(
        self, skill_id: str, skill_version_no: int, dir_name: str, file_path: str, user_id: str, operator_name: str = ""
    ) -> dict[str, Any]:
        """异步删除用户技能草稿版本目录文件。"""
        draft, target = await self._resolve_writable_draft_file_async(
            skill_id, dir_name, file_path, user_id, operator_name=operator_name
        )
        if target.exists() and target.is_file():
            target.unlink()
        clear_metadata_cache()
        return {
            "dir_name": dir_name,
            "file_path": str(target.relative_to(target.parents[1])),
            "skill_version_no": int(draft.get("skill_version_no") or 0),
            "deleted": True,
        }

    async def rollback_user_skill_async(
        self, skill_id: str, skill_version_no: int, user_id: str, operator_name: str = ""
    ) -> dict[str, Any]:
        """异步将用户技能切回指定历史版本。"""
        skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权切换该技能版本")
        version = await self.dao.get_version_async(str(skill.get("skill_id")), int(skill_version_no or 0))
        if not version:
            raise ValueError("技能版本不存在")
        if version.get("version_publish_status") != "published":
            raise ValueError("只有已发布版本可以选择为当前版本")
        await self.dao.activate_version_async(
            str(skill.get("skill_id")), int(skill_version_no or 0), update_by=user_id, update_by_name=operator_name
        )
        clear_metadata_cache()
        return await self.get_skill_async(str(skill.get("skill_id")), user_id=user_id) or {}

    async def favorite_skill_async(
        self, skill_id_or_name: str, user_id: str, favorite: bool, operator_name: str = ""
    ) -> dict[str, Any]:
        """异步收藏或取消收藏技能。"""
        if await self._get_system_skill_metadata_async(skill_id_or_name):
            raise ValueError("系统技能不支持收藏")
        skill = await self.dao.get_skill_async(skill_id_or_name, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        await self.dao.set_favorite_async(
            user_id, str(skill.get("skill_id")), favorite, operator=user_id, operator_name=operator_name
        )
        return {"skill_id": skill.get("skill_id"), "favorite": favorite}

    @staticmethod
    def _db_skill_row_loadable(row: dict[str, Any], include_draft: bool) -> bool:
        """用户技能行是否可加载：仅 published（或管理者看 draft）且已有版本号。"""
        if row.get("skill_source") != "user":
            return False
        if row.get("skill_publish_status") != "published" and not include_draft:
            return False
        # 等价正向写法：草稿放行，或已有正式版本号即视为有效（写成 not(not A and not B) 反而难读）
        return include_draft or bool(int(row.get("current_skill_version_no") or 0))

    @staticmethod
    def _build_db_skill_metadata(
        row: dict[str, Any],
        skill_path: Path,
        can_manage: bool,
        user_names: dict[str, str],
    ) -> dict[str, Any]:
        parsed = parse_metadata(skill_path)
        base = dict(parsed or {})
        base.update(
            {
                "skill_id": row.get("skill_id"),
                "name": row.get("skill_name"),
                "title": row.get("skill_title") or row.get("skill_name"),
                "description": row.get("skill_desc") or "",
                "category": row.get("skill_category") or "",
                "output_type": "text",
                "path": str(skill_path),
                "source": row.get("skill_source") or "user",
                "visibility_type": row.get("visibility_type") or "private",
                "publish_status": row.get("skill_publish_status") or "draft",
                "current_version_no": int(row.get("current_skill_version_no") or 0),
                "draft_version_no": int(row.get("draft_skill_version_no") or 0) if can_manage else 0,
                "has_draft": bool(row.get("draft_skill_version_no")) if can_manage else False,
                "owner_user_id": row.get("owner_user_id") or "",
                "creator_id": str(row.get("create_by") or row.get("owner_user_id") or "").strip(),
                "creator_name": _get_creator_name(row, user_names=user_names),
                "can_manage": can_manage,
                "enabled": bool(row.get("enabled_flag", 1)),
                "favorite": bool(row.get("favorite_flag")),
            }
        )
        return base

    async def _get_db_skill_metadata_async(
        self, name: str, include_draft: bool = False, user_id: str = "", user_names: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """异步从数据库读取可加载技能元数据。"""
        try:
            row = await self.dao.get_skill_async(name, user_id=user_id)
            if not row:
                return None
            if not self._db_skill_row_loadable(row, include_draft):
                return None
            can_manage = _can_manage_skill(row, user_id)
            effective_include_draft = include_draft and can_manage
            skill_path = await self._resolve_db_skill_md_path_async(row, include_draft=effective_include_draft)
            if not skill_path:
                return None
            base = self._build_db_skill_metadata(row, skill_path, can_manage, user_names or {})
            file_info = await asyncio.to_thread(_list_skill_files, str(skill_path))
            return {**base, **file_info}
        except Exception as err:
            logger.warning("读取数据库技能元数据失败: name=%s error=%s", name, err)
            return None

    _DETAIL_FIELD_DEFAULTS = {
        "title": None,  # 特殊：默认取 name
        "description": "",
        "domain": "",
        "category": "",
        "output_type": "text",
        "input_schema": None,  # 特殊：空 dict
        "references": None,  # 特殊：空 list
        "assets": None,  # 特殊：空 list
        "source": "user",
        "visibility_type": "private",
        "publish_status": "published",
        "current_version_no": 0,
        "draft_version_no": 0,
        "owner_user_id": "",
        "creator_id": "",
        "creator_name": "",
    }

    @classmethod
    def _apply_metadata_defaults(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        """为详情字段填充与原 `or` 链一致的缺省值；name 原样透传（无兜底）。"""
        detail: dict[str, Any] = {"name": metadata.get("name")}
        for key, default in cls._DETAIL_FIELD_DEFAULTS.items():
            value = metadata.get(key)
            if default is None:
                # 特殊默认：title 回退 name，容器字段回退空容器
                if key == "title":
                    detail[key] = value or metadata.get("name")
                elif key == "input_schema":
                    detail[key] = value or {}
                else:
                    detail[key] = value or []
            else:
                detail[key] = value or default
        return detail

    async def _get_db_skill_detail_async(
        self, name: str, include_draft: bool = False, user_id: str = "", user_names: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """异步从数据库读取技能详情。"""
        metadata = await self._get_db_skill_metadata_async(
            name, include_draft=include_draft, user_id=user_id, user_names=user_names
        )
        if not metadata:
            return None
        path = Path(str(metadata.get("path") or ""))
        if not path.exists():
            return None
        text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        detail = self._apply_metadata_defaults(metadata)
        detail.update(
            {
                "content": _strip_frontmatter(text),
                "skill_id": metadata.get("skill_id"),
                "has_draft": bool(metadata.get("has_draft")),
                "can_manage": bool(metadata.get("can_manage")),
                "enabled": bool(metadata.get("enabled", True)),
                "favorite": bool(metadata.get("favorite")),
            }
        )
        return detail

    async def _resolve_db_skill_md_path_async(self, row: dict[str, Any], include_draft: bool = False) -> Path | None:
        """异步根据技能主表和版本表定位 SKILL.md。"""
        version = None
        if include_draft:
            version = await self.dao.get_latest_draft_version_async(str(row.get("skill_id") or ""))
        if not version:
            version = await self.dao.get_published_version_async(
                str(row.get("skill_id") or ""),
                int(row.get("current_skill_version_no") or 0),
            )
        if include_draft and not version:
            version = await self.dao.get_latest_published_version_async(str(row.get("skill_id") or ""))
        if not version:
            raise ValueError("技能版本目录无效")
        version_dir = _derive_user_version_dir(
            row.get("owner_user_id"),
            row.get("skill_name"),
            version.get("skill_version_no"),
        )
        skill_md = version_dir / "SKILL.md"
        return skill_md if skill_md.exists() else None

    async def _get_editable_version_dir_async(self, skill_id: str, skill_version_no: int, user_id: str) -> Path:
        """异步校验用户技能版本权限并返回版本目录。"""
        try:
            skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        except Exception:
            if await self._get_system_skill_metadata_async(skill_id):
                raise PermissionError("无权管理该技能文件") from None
            raise
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权管理该技能文件")
        version = await self.dao.get_version_async(str(skill.get("skill_id")), int(skill_version_no or 0))
        if not version:
            raise ValueError("技能版本不存在")
        return _derive_user_version_dir(
            skill.get("owner_user_id"),
            skill.get("skill_name"),
            version.get("skill_version_no"),
        )

    async def _ensure_user_skill_draft_version_async(
        self, skill: dict[str, Any], user_id: str, operator_name: str = ""
    ) -> dict[str, Any]:
        """异步复用已有草稿；没有草稿时基于当前发布版本复制出新草稿。"""
        skill_id = str(skill.get("skill_id") or "")
        skill_name = str(skill.get("skill_name") or skill_id)
        owner_user_id = str(skill.get("owner_user_id") or "")
        draft = await self.dao.get_latest_draft_version_async(skill_id)
        if draft:
            # 版本表不存物理路径，附上按推导规则计算的物理目录。
            draft["skill_version_dir"] = str(
                _derive_user_version_dir(owner_user_id, skill_name, draft.get("skill_version_no"))
            )
            return draft
        version_no = await self.dao.next_version_no_async(skill_id)
        version_dir = _derive_user_version_dir(owner_user_id, skill_name, version_no)
        source_version = None
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        if current_version_no:
            source_version = await self.dao.get_version_async(skill_id, current_version_no)
        if not source_version:
            source_version = await self.dao.get_latest_published_version_async(skill_id)
        if source_version:
            source_dir = _derive_user_version_dir(owner_user_id, skill_name, source_version.get("skill_version_no"))
            await asyncio.to_thread(_copy_version_dir, source_dir, version_dir)
        else:
            await asyncio.to_thread(_ensure_skill_dirs, version_dir)
        await self.dao.insert_version_async(
            {
                "skill_id": skill_id,
                "skill_version_no": version_no,
                "version_publish_status": "draft",
                "create_by": user_id,
                "create_by_name": operator_name,
                "update_by": user_id,
                "update_by_name": operator_name,
            }
        )
        return {
            "skill_id": skill_id,
            "skill_version_no": version_no,
            "skill_version_dir": str(version_dir),
            "version_publish_status": "draft",
            "create_by": user_id,
            "create_by_name": operator_name,
            "update_by": user_id,
            "update_by_name": operator_name,
        }

    async def _get_copy_source_version_async(self, skill: dict[str, Any], user_id: str) -> dict[str, Any]:
        """异步确定复制技能时使用的源版本。"""
        skill_id = str(skill.get("skill_id") or "")
        if str(skill.get("owner_user_id") or "") == user_id:
            draft = await self.dao.get_latest_draft_version_async(skill_id)
            if draft:
                return draft
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        version = (
            await self.dao.get_published_version_async(skill_id, current_version_no) if current_version_no else None
        )
        if not version:
            version = await self.dao.get_latest_published_version_async(skill_id)
        if not version:
            raise ValueError("没有可复制的技能版本")
        return version

    async def _next_copy_skill_name_async(self, source_name: str, user_id: str) -> str:
        """异步生成当前用户可用的复制技能标识。"""
        normalized_name = str(source_name or "user-skill").strip().lower()
        for index in range(0, 702):
            suffix = "" if index == 0 else f"-{_alpha_suffix(index)}"
            prefix_max_len = 64 - len("-copy") - len(suffix)
            prefix = normalized_name[:prefix_max_len].rstrip("-")
            candidate = f"{prefix}-copy{suffix}"
            if not SKILL_NAME_PATTERN.match(candidate):
                continue
            if not await self.dao.get_skill_async(candidate, user_id=user_id):
                return candidate
        raise ValueError("无法生成可用的复制技能名称")

    async def _get_readable_version_dir_async(self, skill_id: str, skill_version_no: int, user_id: str) -> Path:
        """校验技能版本读取权限并返回版本目录。"""
        system_version_dir = await self._get_system_version_dir_async(skill_id, skill_version_no)
        if system_version_dir is not None:
            return system_version_dir
        try:
            skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        except Exception as err:
            logger.warning("读取技能表失败，回退系统技能文件: skill_id=%s error=%s", skill_id, err)
            skill = None
        if not skill:
            raise ValueError("技能不存在")
        return await self._get_editable_version_dir_async(skill_id, skill_version_no, user_id)

    async def _get_system_version_dir_async(self, skill_id: str, skill_version_no: int) -> Path | None:
        """返回文件系统系统技能的唯一只读版本目录。"""
        if int(skill_version_no or 0) != 1:
            return None
        metadata = await self._get_system_skill_metadata_async(skill_id)
        if not metadata:
            return None
        skill_md = Path(str(metadata.get("path") or ""))
        if not await asyncio.to_thread(skill_md.exists):
            return None
        return skill_md.parent

    async def _resolve_readable_skill_file_path_async(
        self, skill_id: str, skill_version_no: int, dir_name: str, file_path: str, user_id: str
    ) -> Path:
        """异步解析并校验可读技能固定目录文件路径。"""
        version_dir = await self._get_readable_version_dir_async(skill_id, skill_version_no, user_id)
        system_version_dir = await self._get_system_version_dir_async(skill_id, skill_version_no)
        return self._resolve_file_under_version_dir(
            version_dir,
            dir_name,
            file_path,
            create_fixed_dirs=system_version_dir is None,
        )

    def _resolve_user_file_under_version_dir(self, version_dir: Any, dir_name: str, file_path: str) -> Path:
        """解析用户技能版本目录下的文件，先校验版本目录归属。"""
        safe_version_dir = _resolve_user_skill_version_dir(version_dir)
        return self._resolve_file_under_version_dir(safe_version_dir, dir_name, file_path)

    def _resolve_file_under_version_dir(
        self,
        version_dir: Path,
        dir_name: str,
        file_path: str,
        *,
        create_fixed_dirs: bool = True,
    ) -> Path:
        """解析并校验技能版本固定目录下的相对文件路径。"""
        raw_version_dir = str(version_dir or "").strip()
        if raw_version_dir in {"", "."}:
            raise ValueError("技能版本目录无效")
        safe_version_dir = Path(version_dir).expanduser().resolve()
        normalized_dir = str(dir_name or "").strip()
        if normalized_dir not in SKILL_FIXED_DIRS:
            raise ValueError("只支持 references、assets 目录")
        normalized_file = str(file_path or "").strip().replace("\\", "/")
        if not normalized_file or normalized_file.startswith("/") or ".." in Path(normalized_file).parts:
            raise ValueError("文件路径无效")
        if create_fixed_dirs:
            _ensure_skill_dirs(safe_version_dir)
        base_dir = (safe_version_dir / normalized_dir).resolve()
        target_input = base_dir / normalized_file
        if normalized_dir == "references" and not target_input.suffix:
            target_input = target_input.with_suffix(".md")
        target = target_input.resolve()
        if base_dir != target and base_dir not in target.parents:
            raise ValueError("文件路径越界")
        if target.is_symlink():
            raise ValueError("文件路径无效")
        return target

    async def delete_user_skill_async(self, skill_id: str, user_id: str, operator_name: str = "") -> None:
        """异步逻辑删除用户技能，释放技能名并保留归档目录。"""
        skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权删除该技能")
        await self._archive_user_skill_async(skill, user_id, operator_name=operator_name)

    async def _archive_user_skill_async(self, skill: dict[str, Any], user_id: str, operator_name: str = "") -> None:
        """异步归档用户技能记录和目录，释放技能名。

        物理目录随技能根目录整体改名归档；物理路径不入库，无需回填归档后的路径。
        """
        original_name = str(skill.get("skill_name") or skill.get("skill_id") or "skill").strip()
        archive_name = f"{original_name}--deleted-{uuid.uuid4().hex[:8]}"
        root_dir = _derive_user_skill_root_dir(skill.get("owner_user_id"), original_name)
        archive_root_dir = _resolve_user_skill_path(root_dir.parent / archive_name, "技能归档目录")
        if root_dir.exists() and root_dir != archive_root_dir:
            await asyncio.to_thread(root_dir.rename, archive_root_dir)
        await self.dao.delete_skill_async(
            str(skill.get("skill_id")),
            archive_name,
            update_by=user_id,
            update_by_name=operator_name,
        )

    def cleanup_user_skill_dir(self, skill_id: str) -> None:
        """测试辅助：清理用户技能目录。"""
        target = _resolve_user_skill_path(_user_skill_root() / skill_id, "技能清理目录")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


skill_service = SkillService()


class _SkillSnapshotSource:
    """把 SkillService 的技能源查询适配成 workspace 快照端口。"""

    async def list_runtime_skill_sources_async(self, user_id: str) -> list[tuple[str, Path]]:
        return await skill_service.list_runtime_skill_sources_async(user_id)


register_skill_snapshot_source(_SkillSnapshotSource())
