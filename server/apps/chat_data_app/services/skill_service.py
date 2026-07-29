"""ChatData 技能管理服务。"""

import re
import shutil
import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from utils.logger import get_logger
from utils.skill_paths import USER_SKILL_CATEGORY, physical_skill_category_root
from ..agent.skills import activate_skill, clear_metadata_cache, load_metadata, parse_metadata
from ..daos.skill_dao import SkillDao

logger = get_logger(__name__)
USER_SKILL_ROOT = physical_skill_category_root(USER_SKILL_CATEGORY)
SKILL_NAME_PATTERN = re.compile(r"^[a-z]+(?:-[a-z]+)+$")
SKILL_FIXED_DIRS = ("references", "assets", "scripts")

SKILL_ALIASES = {
    "risk-analysis": "dashboard-generation",
    "organization-profile": "dashboard-generation",
    "机构画像分析": "dashboard-generation",
    "风险分析技能": "dashboard-generation",
    "看板生成技能": "dashboard-generation",
}
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


def _resolve_user_skill_root_dir(skill_name: str) -> Path:
    """根据技能标识计算用户技能根目录，不从外部入参接收目录。"""
    return _resolve_user_skill_path(_user_skill_root() / str(skill_name or "").strip(), "技能根目录")


def _resolve_user_skill_root_value(path_value: Any, fallback_name: str) -> Path:
    """解析数据库中的用户技能根目录，缺省时按技能标识计算。"""
    raw = str(path_value or "").strip()
    if raw:
        return _resolve_user_skill_path(Path(raw), "技能根目录")
    return _resolve_user_skill_root_dir(fallback_name)


def _resolve_user_skill_version_dir(path_value: Any, field_name: str = "技能版本目录") -> Path:
    """解析数据库中的用户技能版本目录，并限制在用户技能根目录下。"""
    raw = str(path_value or "").strip()
    if not raw:
        raise ValueError("技能版本目录无效")
    return _resolve_user_skill_path(Path(raw), field_name)


def resolve_skill_name(name: str) -> str:
    """解析技能别名，兼容前端残留标题或旧技能名。"""
    normalized = str(name or "").strip()
    return SKILL_ALIASES.get(normalized, normalized)


def _list_skill_files(skill_path: str) -> Dict[str, List[str]]:
    """列出技能参考文档、脚本和素材。"""
    root = Path(skill_path).parent
    references_root = root / "references"
    scripts_root = root / "scripts"
    assets_root = root / "assets"
    references = [
        str(item.relative_to(references_root).with_suffix(""))
        for item in references_root.rglob("*.md")
        if not item.is_symlink()
    ] if references_root.exists() and not references_root.is_symlink() else []
    scripts = [
        str(item.relative_to(scripts_root))
        for item in scripts_root.rglob("*.py")
        if not item.is_symlink()
    ] if scripts_root.exists() and not scripts_root.is_symlink() else []
    assets = [
        str(item.relative_to(assets_root))
        for item in assets_root.rglob("*")
        if item.is_file() and not item.is_symlink()
    ] if assets_root.exists() and not assets_root.is_symlink() else []
    return {
        "references": sorted(references),
        "scripts": sorted(scripts),
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
    match = re.match(r'^---\s*\n.*?\n---\s*\n(.*)$', text or "", re.DOTALL)
    return match.group(1).strip() if match else str(text or "")


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
    chars: List[str] = []
    while value > 0:
        value -= 1
        chars.append(chr(ord("a") + value % 26))
        value //= 26
    return "".join(reversed(chars)) or "a"


def _get_creator_name(row: Dict[str, Any], user_names: Optional[Dict[str, str]] = None) -> str:
    creator_id = str(row.get("create_by") or row.get("owner_user_id") or "").strip()
    return str(row.get("create_by_name") or (user_names or {}).get(creator_id) or "").strip()


def _is_skill_owner(row: Dict[str, Any], user_id: str = "") -> bool:
    return bool(user_id and str(row.get("owner_user_id") or "") == str(user_id))


def _can_manage_skill(row: Dict[str, Any], user_id: str = "") -> bool:
    return bool(row.get("skill_source") == "user" and _is_skill_owner(row, user_id))


def _skill_row_to_info(row: Dict[str, Any], user_id: str = "", user_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """将数据库技能行转换为前端技能对象。"""
    creator_id = str(row.get("create_by") or row.get("owner_user_id") or "").strip()
    can_manage = _can_manage_skill(row, user_id)
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


def _skill_version_to_info(version: Dict[str, Any], current_version_no: int = 0) -> Dict[str, Any]:
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


def _system_metadata_to_info(item: Dict[str, Any]) -> Dict[str, Any]:
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


def _build_skill_md(payload: Dict[str, Any]) -> str:
    """根据用户输入生成标准 SKILL.md 内容。"""
    skill_name = str(payload.get("skill_name") or payload.get("name") or "").strip()
    skill_title = str(payload.get("skill_title") or payload.get("title") or skill_name).strip()
    skill_desc = str(payload.get("skill_desc") or payload.get("description") or "").strip()
    skill_category = str(payload.get("skill_category") or payload.get("category") or "custom").strip()
    content = str(payload.get("content") or "").strip()
    if content.startswith("---"):
        return content + "\n"
    body = content or f"# {skill_title}\n\n## 适用目标\n\n## 输入信息\n\n## 工作流程\n\n## 约束规则\n\n## 输出格式\n\n## 完成判定\n"
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


def _validate_skill_payload(payload: Dict[str, Any]) -> None:
    """校验用户技能基础字段。"""
    skill_name = str(payload.get("skill_name") or payload.get("name") or "").strip()
    skill_title = str(payload.get("skill_title") or payload.get("title") or "").strip()
    if not SKILL_NAME_PATTERN.match(skill_name):
        raise ValueError("技能名称必须使用小写字母和中划线，格式为 xxx-xxx，至少包含一个中划线，不支持数字、下划线、空格或中文")
    if len(skill_name) < 3 or len(skill_name) > 64:
        raise ValueError("技能名称长度必须在 3-64 之间")
    if not skill_title:
        raise ValueError("技能标题不能为空")


def _write_skill_md(version_dir: Path, payload: Dict[str, Any]) -> Path:
    """写入技能版本目录的 SKILL.md 并校验元数据。"""
    version_dir.mkdir(parents=True, exist_ok=True)
    _ensure_skill_dirs(version_dir)
    skill_md_path = version_dir / "SKILL.md"
    skill_md_path.write_text(_build_skill_md(payload), encoding="utf-8")
    parsed = parse_metadata(skill_md_path)
    if not parsed:
        raise ValueError("SKILL.md 元数据无效")
    return skill_md_path


def _resolve_new_skill_text_fields(normalized_payload: Dict[str, Any], parsed: Dict[str, Any], skill_name: str) -> tuple:
    """按 payload 别名、SKILL.md 元数据、默认值的优先级解析标题/描述/分类。"""
    title = normalized_payload.get("skill_title") or normalized_payload.get("title") or parsed.get("title") or skill_name
    desc = normalized_payload.get("skill_desc") or normalized_payload.get("description") or parsed.get("description") or ""
    category = normalized_payload.get("skill_category") or normalized_payload.get("category") or parsed.get("category") or "custom"
    return title, desc, category


class SkillService:
    """提供技能发现、详情和缓存刷新能力。"""

    def __init__(self):
        self._dao: SkillDao | None = None

    @property
    def dao(self) -> SkillDao:
        """懒加载数据库访问层，避免无数据库环境导入即失败。"""
        if self._dao is None:
            self._dao = SkillDao()
        return self._dao

    async def _safe_db_skills_async(self, user_id: str = "", include_disabled: bool = False) -> Optional[List[Dict[str, Any]]]:
        """异步读取数据库用户技能；表未初始化时仍可使用文件系统系统技能。"""
        try:
            return await self.dao.list_skills_async(user_id=user_id, include_disabled=include_disabled)
        except Exception as err:
            logger.warning("读取技能表失败，仅返回系统技能: %s", err)
            return None

    async def list_skills_async(self, search: Optional[str] = None, category: Optional[str] = None, user_id: str = "", scope: str = "all", user_names: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        db_rows = await self._safe_db_skills_async(user_id=user_id)
        keyword = str(search or "").strip().lower()
        normalized_category = str(category or "").strip()
        skills: List[Dict[str, Any]] = []
        system_metadata = await asyncio.to_thread(load_metadata)
        source_rows = [
            _skill_row_to_info(row, user_id=user_id, user_names=user_names)
            for row in (db_rows or [])
            if row.get("skill_source") == "user"
        ] if db_rows is not None else []
        for item in system_metadata.values():
            source_rows.append(_system_metadata_to_info(item))
        for item in source_rows:
            if scope == "favorites" and not item.get("favorite"):
                continue
            if scope == "mine" and str(item.get("owner_user_id") or "") != str(user_id or ""):
                continue
            if scope == "system" and item.get("source") != "system":
                continue
            if scope == "runtime" and item.get("source") == "user" and not int(item.get("current_version_no") or 0):
                continue
            if normalized_category and item.get("category") != normalized_category:
                continue
            searchable = " ".join([
                str(item.get("name") or ""),
                str(item.get("title") or ""),
                str(item.get("description") or ""),
                str(item.get("domain") or ""),
                str(item.get("category") or ""),
            ]).lower()
            if keyword and keyword not in searchable:
                continue
            skills.append(item)
        return sorted(skills, key=lambda value: (str(value.get("source") or ""), str(value.get("name") or "")))

    async def get_skill_async(self, name: str, include_draft: bool = False, user_id: str = "", user_names: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        resolved_name = resolve_skill_name(name)
        skill = await self._get_system_skill_detail_async(resolved_name)
        if skill:
            return skill
        return await self._get_db_skill_detail_async(resolved_name, include_draft=include_draft, user_id=user_id, user_names=user_names)

    async def reload_async(self) -> Dict[str, Any]:
        """异步刷新专家技能元数据缓存。"""
        clear_metadata_cache()
        metadata = await asyncio.to_thread(load_metadata)
        return {
            "total": len(metadata),
            "skills": sorted(metadata.keys()),
        }

    async def resolve_skill_path_async(self, name: str, user_id: str = "") -> Optional[Dict[str, Any]]:
        """异步解析可加载技能的 SKILL.md 路径。"""
        system_meta = await self._get_system_skill_metadata_async(name)
        if system_meta:
            return system_meta
        return await self._get_db_skill_metadata_async(name, user_id=user_id)

    async def _get_system_skill_metadata_async(self, name: str) -> Optional[Dict[str, Any]]:
        """异步读取文件系统系统技能元数据，不访问技能数据库。"""
        resolved_name = resolve_skill_name(name)
        metadata = (await asyncio.to_thread(load_metadata)).get(resolved_name)
        if not metadata:
            clear_metadata_cache()
            metadata = (await asyncio.to_thread(load_metadata)).get(resolved_name)
        if not metadata:
            return None
        return {
            **dict(metadata),
            "source": "system",
        }

    async def _get_system_skill_detail_async(self, name: str) -> Optional[Dict[str, Any]]:
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

    async def create_user_skill_async(self, payload: Dict[str, Any], user_id: str, operator_name: str = "") -> Dict[str, Any]:
        """异步创建用户技能草稿版本。"""
        normalized_payload = dict(payload or {})
        _validate_skill_payload(normalized_payload)
        skill_name = str(normalized_payload.get("skill_name") or normalized_payload.get("name") or "").strip()
        if await self._get_system_skill_metadata_async(skill_name):
            raise ValueError(f"技能标识已被系统技能占用: {skill_name}")
        existing = await self.dao.get_skill_async(skill_name, user_id=user_id)
        if existing and existing.get("skill_source") == "user" and existing.get("owner_user_id") == user_id and existing.get("skill_publish_status") == "disabled":
            await self._archive_user_skill_async(existing, user_id, operator_name=operator_name)
        elif existing:
            raise ValueError(f"技能标识已存在: {skill_name}")
        if await self.dao.skill_name_exists_async(skill_name):
            raise ValueError(f"技能标识已存在: {skill_name}")
        skill_id = uuid.uuid4().hex
        version_no = 1
        skill_root_dir = _resolve_user_skill_root_dir(skill_name)
        version_dir = skill_root_dir / "versions" / f"v{version_no}"
        skill_md_path = await asyncio.to_thread(_write_skill_md, version_dir, normalized_payload)
        parsed = await asyncio.to_thread(parse_metadata, skill_md_path)
        visibility_type = str(normalized_payload.get("visibility_type") or "private").strip()
        if visibility_type not in {"private", "global"}:
            visibility_type = "private"
        skill_title, skill_desc, skill_category = _resolve_new_skill_text_fields(normalized_payload, parsed, skill_name)
        try:
            await self.dao.create_skill_with_version_async({
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
                "skill_root_dir": str(skill_root_dir),
                "create_by": user_id,
                "create_by_name": operator_name,
                "update_by": user_id,
                "update_by_name": operator_name,
            }, {
                "skill_id": skill_id,
                "skill_version_no": version_no,
                "skill_version_dir": str(version_dir),
                "version_publish_status": "draft",
                "create_by": user_id,
                "create_by_name": operator_name,
                "update_by": user_id,
                "update_by_name": operator_name,
            })
        except IntegrityError as exc:
            # 并发创建同名技能时唯一键兜底，转成用户可理解的业务错误
            raise ValueError(f"技能标识已存在: {skill_name}") from exc
        return await self.get_skill_async(skill_id, include_draft=True, user_id=user_id) or {"skill_id": skill_id, "name": skill_name}

    async def import_user_skill_from_directory_async(self, source_dir: Path, user_id: str, operator_name: str = "") -> Dict[str, Any]:
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
        skill = await self.create_user_skill_async({
            "skill_name": parsed.get("name") or resolved_source.name,
            "skill_title": parsed.get("title") or parsed.get("name") or resolved_source.name,
            "skill_desc": parsed.get("description") or "",
            "skill_category": parsed.get("category") or "custom",
            "visibility_type": "private",
            "content": content,
        }, user_id=user_id, operator_name=operator_name)
        skill_id = str(skill.get("skill_id") or "")
        draft_version_no = int(skill.get("draft_version_no") or 1)
        version_dir = await self._get_editable_version_dir_async(skill_id, draft_version_no, user_id)
        await asyncio.to_thread(_safe_copy_skill_dir, resolved_source, version_dir)
        clear_metadata_cache()
        return await self.get_skill_async(skill_id, include_draft=True, user_id=user_id) or skill

    async def copy_user_skill_async(self, skill_id: str, user_id: str, operator_name: str = "") -> Dict[str, Any]:
        """异步复制可见用户技能为当前用户私有草稿。"""
        if await self._get_system_skill_metadata_async(skill_id):
            raise PermissionError("系统技能不支持复制")
        source_skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not source_skill:
            raise ValueError("技能不存在")
        if source_skill.get("skill_source") == "system":
            raise PermissionError("系统技能不支持复制")
        source_version = await self._get_copy_source_version_async(source_skill, user_id)
        source_dir = _resolve_user_skill_version_dir(source_version.get("skill_version_dir"), "来源技能版本目录")
        skill_md_path = source_dir / "SKILL.md"
        if not await asyncio.to_thread(skill_md_path.is_file):
            raise ValueError("技能版本目录无效")
        parsed = await asyncio.to_thread(parse_metadata, skill_md_path) or {}
        raw_content = await asyncio.to_thread(skill_md_path.read_text, encoding="utf-8", errors="replace")
        content = _strip_frontmatter(raw_content)
        source_name = str(source_skill.get("skill_name") or parsed.get("name") or "user-skill").strip()
        source_title = str(source_skill.get("skill_title") or parsed.get("title") or source_name).strip()
        copied_name = await self._next_copy_skill_name_async(source_name, user_id)
        copied_skill = await self.create_user_skill_async({
            "skill_name": copied_name,
            "skill_title": f"{source_title} 副本",
            "skill_desc": source_skill.get("skill_desc") or parsed.get("description") or "",
            "skill_category": source_skill.get("skill_category") or parsed.get("category") or "custom",
            "visibility_type": "private",
            "content": content,
        }, user_id=user_id, operator_name=operator_name)
        copied_skill_id = str(copied_skill.get("skill_id") or "")
        draft_version_no = int(copied_skill.get("draft_version_no") or 1)
        version_dir = await self._get_editable_version_dir_async(copied_skill_id, draft_version_no, user_id)
        await asyncio.to_thread(_safe_copy_skill_dir, source_dir, version_dir)
        clear_metadata_cache()
        return await self.get_skill_async(copied_skill_id, include_draft=True, user_id=user_id) or copied_skill

    async def update_user_skill_draft_async(self, skill_id: str, payload: Dict[str, Any], user_id: str, operator_name: str = "") -> Dict[str, Any]:
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
        parsed: Dict[str, Any] = {}
        if "content" in payload:
            draft = await self._ensure_user_skill_draft_version_async(skill, user_id, operator_name=operator_name)
            version_dir = _resolve_user_skill_version_dir(draft.get("skill_version_dir"))
            skill_md_path = await asyncio.to_thread(_write_skill_md, version_dir, normalized_payload)
            parsed = await asyncio.to_thread(parse_metadata, skill_md_path) or {}
        visibility_type = str(payload.get("visibility_type") or skill.get("visibility_type") or "private").strip()
        if visibility_type not in {"private", "global"}:
            visibility_type = "private"
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        await self.dao.update_skill_async(str(skill.get("skill_id")), {
            "skill_title": normalized_payload.get("skill_title") or parsed.get("title") or skill.get("skill_name"),
            "skill_desc": payload.get("skill_desc") if payload.get("skill_desc") is not None else skill.get("skill_desc"),
            "skill_category": payload.get("skill_category") if payload.get("skill_category") is not None else skill.get("skill_category"),
            "visibility_type": visibility_type,
            "skill_publish_status": "published" if current_version_no > 0 else "draft",
        }, update_by=user_id, update_by_name=operator_name)
        clear_metadata_cache()
        return await self.get_skill_async(str(skill.get("skill_id")), include_draft=True, user_id=user_id) or {}

    async def publish_user_skill_async(self, skill_id: str, user_id: str, operator_name: str = "") -> Dict[str, Any]:
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
        await self.dao.publish_version_async(str(skill.get("skill_id")), int(draft.get("skill_version_no") or 0), update_by=user_id, update_by_name=operator_name)
        clear_metadata_cache()
        return await self.get_skill_async(str(skill.get("skill_id")), include_draft=True, user_id=user_id) or {}

    async def list_skill_versions_async(self, skill_id: str, user_id: str) -> List[Dict[str, Any]]:
        """列出系统技能固定版本或用户技能版本。"""
        system_version_dir = await self._get_system_version_dir_async(skill_id, 1)
        if system_version_dir is not None:
            return [{
                "skill_id": skill_id,
                "skill_version_no": 1,
                "version_publish_status": "published",
                "current": True,
            }]
        skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权查看该技能版本")
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        versions = await self.dao.list_versions_async(str(skill.get("skill_id")))
        return [_skill_version_to_info(version, current_version_no=current_version_no) for version in versions]

    async def get_skill_version_detail_async(self, skill_id: str, skill_version_no: int, user_id: str) -> Dict[str, Any]:
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
        version_dir = _resolve_user_skill_version_dir(version.get("skill_version_dir"))
        await asyncio.to_thread(_ensure_skill_dirs, version_dir)
        skill_md = version_dir / "SKILL.md"
        content = await asyncio.to_thread(skill_md.read_text, encoding="utf-8", errors="replace") if skill_md.exists() else ""
        file_info = await asyncio.to_thread(_list_skill_files, str(skill_md))
        return {
            **_skill_version_to_info(version, current_version_no=int(skill.get("current_skill_version_no") or 0)),
            "content": _strip_frontmatter(content),
            **file_info,
        }

    async def list_skill_version_files_async(self, skill_id: str, skill_version_no: int, user_id: str) -> Dict[str, List[str]]:
        """列出系统技能或用户技能版本固定目录文件。"""
        version_dir = await self._get_readable_version_dir_async(skill_id, skill_version_no, user_id)
        return await asyncio.to_thread(_list_skill_files, str(version_dir / "SKILL.md"))

    async def read_skill_version_file_async(self, skill_id: str, skill_version_no: int, dir_name: str, file_path: str, user_id: str) -> Dict[str, Any]:
        """异步读取用户技能版本目录文件。"""
        target = await self._resolve_readable_skill_file_path_async(skill_id, skill_version_no, dir_name, file_path, user_id)
        if not target.exists() or not target.is_file():
            raise ValueError("文件不存在")
        content = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        return {
            "dir_name": dir_name,
            "file_path": str(target.relative_to(target.parents[1])),
            "content": content,
        }

    async def save_skill_version_file_async(self, skill_id: str, skill_version_no: int, dir_name: str, file_path: str, content: str, user_id: str, operator_name: str = "") -> Dict[str, Any]:
        """异步保存用户技能版本目录文件。"""
        try:
            skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        except Exception:
            if await self._get_system_skill_metadata_async(skill_id):
                raise PermissionError("无权管理该技能文件")
            raise
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权管理该技能文件")
        draft = await self._ensure_user_skill_draft_version_async(skill, user_id, operator_name=operator_name)
        target = self._resolve_user_file_under_version_dir(draft.get("skill_version_dir"), dir_name, file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, str(content or ""), encoding="utf-8")
        clear_metadata_cache()
        return {
            "dir_name": dir_name,
            "file_path": str(target.relative_to(target.parents[1])),
            "skill_version_no": int(draft.get("skill_version_no") or 0),
            "saved": True,
        }

    async def delete_skill_version_file_async(self, skill_id: str, skill_version_no: int, dir_name: str, file_path: str, user_id: str, operator_name: str = "") -> Dict[str, Any]:
        """异步删除用户技能草稿版本目录文件。"""
        try:
            skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        except Exception:
            if await self._get_system_skill_metadata_async(skill_id):
                raise PermissionError("无权管理该技能文件")
            raise
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权管理该技能文件")
        draft = await self._ensure_user_skill_draft_version_async(skill, user_id, operator_name=operator_name)
        target = self._resolve_user_file_under_version_dir(draft.get("skill_version_dir"), dir_name, file_path)
        if target.exists() and target.is_file():
            target.unlink()
        clear_metadata_cache()
        return {
            "dir_name": dir_name,
            "file_path": str(target.relative_to(target.parents[1])),
            "skill_version_no": int(draft.get("skill_version_no") or 0),
            "deleted": True,
        }

    async def rollback_user_skill_async(self, skill_id: str, skill_version_no: int, user_id: str, operator_name: str = "") -> Dict[str, Any]:
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
        await self.dao.activate_version_async(str(skill.get("skill_id")), int(skill_version_no or 0), update_by=user_id, update_by_name=operator_name)
        clear_metadata_cache()
        return await self.get_skill_async(str(skill.get("skill_id")), user_id=user_id) or {}

    async def favorite_skill_async(self, skill_id_or_name: str, user_id: str, favorite: bool, operator_name: str = "") -> Dict[str, Any]:
        """异步收藏或取消收藏技能。"""
        if await self._get_system_skill_metadata_async(skill_id_or_name):
            raise ValueError("系统技能不支持收藏")
        skill = await self.dao.get_skill_async(skill_id_or_name, user_id=user_id)
        if not skill:
            raise ValueError("技能不存在")
        await self.dao.set_favorite_async(user_id, str(skill.get("skill_id")), favorite, operator=user_id, operator_name=operator_name)
        return {"skill_id": skill.get("skill_id"), "favorite": favorite}

    async def _get_db_skill_metadata_async(self, name: str, include_draft: bool = False, user_id: str = "", user_names: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """异步从数据库读取可加载技能元数据。"""
        try:
            row = await self.dao.get_skill_async(resolve_skill_name(name), user_id=user_id)
            if not row:
                return None
            if row.get("skill_source") != "user":
                return None
            if row.get("skill_source") == "user" and row.get("skill_publish_status") != "published" and not include_draft:
                return None
            if row.get("skill_source") == "user" and not include_draft and not int(row.get("current_skill_version_no") or 0):
                return None
            can_manage = _can_manage_skill(row, user_id)
            effective_include_draft = include_draft and can_manage
            skill_path = await self._resolve_db_skill_md_path_async(row, include_draft=effective_include_draft)
            if not skill_path:
                return None
            parsed = parse_metadata(skill_path)
            base = dict(parsed or {})
            base.update({
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
            })
            file_info = await asyncio.to_thread(_list_skill_files, str(skill_path))
            return {**base, **file_info}
        except Exception as err:
            logger.warning("读取数据库技能元数据失败: name=%s error=%s", name, err)
            return None

    async def _get_db_skill_detail_async(self, name: str, include_draft: bool = False, user_id: str = "", user_names: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """异步从数据库读取技能详情。"""
        metadata = await self._get_db_skill_metadata_async(name, include_draft=include_draft, user_id=user_id, user_names=user_names)
        if not metadata:
            return None
        path = Path(str(metadata.get("path") or ""))
        if not path.exists():
            return None
        text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        return {
            "name": metadata.get("name"),
            "title": metadata.get("title") or metadata.get("name"),
            "description": metadata.get("description") or "",
            "domain": metadata.get("domain") or "",
            "category": metadata.get("category") or "",
            "output_type": metadata.get("output_type") or "text",
            "input_schema": metadata.get("input_schema") or {},
            "content": _strip_frontmatter(text),
            "references": metadata.get("references") or [],
            "scripts": metadata.get("scripts") or [],
            "assets": metadata.get("assets") or [],
            "source": metadata.get("source") or "user",
            "skill_id": metadata.get("skill_id"),
            "visibility_type": metadata.get("visibility_type") or "private",
            "publish_status": metadata.get("publish_status") or "published",
            "current_version_no": metadata.get("current_version_no") or 0,
            "draft_version_no": metadata.get("draft_version_no") or 0,
            "has_draft": bool(metadata.get("has_draft")),
            "owner_user_id": metadata.get("owner_user_id") or "",
            "creator_id": metadata.get("creator_id") or "",
            "creator_name": metadata.get("creator_name") or "",
            "can_manage": bool(metadata.get("can_manage")),
            "enabled": bool(metadata.get("enabled", True)),
            "favorite": bool(metadata.get("favorite")),
        }

    async def _resolve_db_skill_md_path_async(self, row: Dict[str, Any], include_draft: bool = False) -> Optional[Path]:
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
        version_dir = _resolve_user_skill_version_dir((version or {}).get("skill_version_dir"))
        skill_md = version_dir / "SKILL.md"
        return skill_md if skill_md.exists() else None

    async def _get_editable_version_dir_async(self, skill_id: str, skill_version_no: int, user_id: str) -> Path:
        """异步校验用户技能版本权限并返回版本目录。"""
        try:
            skill = await self.dao.get_skill_async(skill_id, user_id=user_id)
        except Exception:
            if await self._get_system_skill_metadata_async(skill_id):
                raise PermissionError("无权管理该技能文件")
            raise
        if not skill:
            raise ValueError("技能不存在")
        if skill.get("skill_source") != "user" or skill.get("owner_user_id") != user_id:
            raise PermissionError("无权管理该技能文件")
        version = await self.dao.get_version_async(str(skill.get("skill_id")), int(skill_version_no or 0))
        if not version:
            raise ValueError("技能版本不存在")
        return _resolve_user_skill_version_dir(version.get("skill_version_dir"))

    async def _ensure_user_skill_draft_version_async(self, skill: Dict[str, Any], user_id: str, operator_name: str = "") -> Dict[str, Any]:
        """异步复用已有草稿；没有草稿时基于当前发布版本复制出新草稿。"""
        skill_id = str(skill.get("skill_id") or "")
        draft = await self.dao.get_latest_draft_version_async(skill_id)
        if draft:
            return draft
        version_no = await self.dao.next_version_no_async(skill_id)
        skill_root_dir = _resolve_user_skill_root_value(skill.get("skill_root_dir"), str(skill.get("skill_name") or skill_id))
        version_dir = skill_root_dir / "versions" / f"v{version_no}"
        source_version = None
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        if current_version_no:
            source_version = await self.dao.get_version_async(skill_id, current_version_no)
        if not source_version:
            source_version = await self.dao.get_latest_published_version_async(skill_id)
        source_dir_value = str((source_version or {}).get("skill_version_dir") or "").strip()
        if source_dir_value:
            source_dir = _resolve_user_skill_version_dir(source_dir_value, "来源技能版本目录")
            await asyncio.to_thread(_copy_version_dir, source_dir, version_dir)
        else:
            await asyncio.to_thread(_ensure_skill_dirs, version_dir)
        await self.dao.insert_version_async({
            "skill_id": skill_id,
            "skill_version_no": version_no,
            "skill_version_dir": str(version_dir),
            "version_publish_status": "draft",
            "create_by": user_id,
            "create_by_name": operator_name,
            "update_by": user_id,
            "update_by_name": operator_name,
        })
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

    async def _get_copy_source_version_async(self, skill: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """异步确定复制技能时使用的源版本。"""
        skill_id = str(skill.get("skill_id") or "")
        if str(skill.get("owner_user_id") or "") == user_id:
            draft = await self.dao.get_latest_draft_version_async(skill_id)
            if draft:
                return draft
        current_version_no = int(skill.get("current_skill_version_no") or 0)
        version = await self.dao.get_published_version_async(skill_id, current_version_no) if current_version_no else None
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

    async def _resolve_readable_skill_file_path_async(self, skill_id: str, skill_version_no: int, dir_name: str, file_path: str, user_id: str) -> Path:
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
            raise ValueError("只支持 references、assets、scripts 目录")
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

    async def _archive_user_skill_async(self, skill: Dict[str, Any], user_id: str, operator_name: str = "") -> None:
        """异步归档用户技能记录和目录，释放技能名。"""
        original_name = str(skill.get("skill_name") or skill.get("skill_id") or "skill").strip()
        archive_name = f"{original_name}--deleted-{uuid.uuid4().hex[:8]}"
        root_dir = _resolve_user_skill_root_value(skill.get("skill_root_dir"), original_name)
        archive_root_dir = root_dir.parent / archive_name
        archive_root_dir = _resolve_user_skill_path(archive_root_dir, "技能归档目录")
        if root_dir.exists() and root_dir != archive_root_dir:
            await asyncio.to_thread(root_dir.rename, archive_root_dir)
        versions = await self.dao.list_versions_async(str(skill.get("skill_id")))
        for version in versions:
            version_no = int(version.get("skill_version_no") or 0)
            try:
                version_dir = _resolve_user_skill_version_dir(version.get("skill_version_dir"))
            except ValueError:
                logger.warning("技能版本目录越界，归档时重置为归档目录: skill_id=%s version_no=%s", skill.get("skill_id"), version_no)
                version_dir = root_dir / "versions" / f"v{version_no}"
            try:
                archived_version_dir = archive_root_dir / version_dir.relative_to(root_dir)
            except ValueError:
                archived_version_dir = archive_root_dir / "versions" / f"v{version_no}"
            archived_version_dir = _resolve_user_skill_path(archived_version_dir, "技能归档版本目录")
            await self.dao.update_version_dir_async(str(skill.get("skill_id")), version_no, str(archived_version_dir), update_by=user_id, update_by_name=operator_name)
        await self.dao.delete_skill_async(str(skill.get("skill_id")), archive_name, str(archive_root_dir), update_by=user_id, update_by_name=operator_name)

    def cleanup_user_skill_dir(self, skill_id: str) -> None:
        """测试辅助：清理用户技能目录。"""
        target = _resolve_user_skill_path(_user_skill_root() / skill_id, "技能清理目录")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


skill_service = SkillService()
