"""插件 zip 安装与卸载。

安全边界：
- 大小上限：压缩包 50MB（与上传文件限制一致）、解压总量 200MB、成员数 5000（防 zip 炸弹）；
- 路径安全：成员名禁止绝对路径与 ``..``，逐条 resolve 后必须落在插件目录内（防 zip-slip）；
- 禁止符号链接成员（防把管理域外内容带进插件目录）；
- manifest 必须存在且合法，``manifest.name`` 即插件目录名，目录名不一致拒绝安装。

卸载直接删除插件目录；启停见 ``loader.set_plugin_enabled``。
"""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

from utils.logger import get_logger

from .loader import clear_metadata_cache, plugin_dir
from .manifest import (
    PLUGIN_MANIFEST_FILENAME,
    PluginManifest,
    PluginManifestError,
    parse_plugin_manifest,
)

logger = get_logger(__name__)

PLUGIN_ZIP_MAX_BYTES = 50 * 1024 * 1024
PLUGIN_ZIP_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
PLUGIN_ZIP_MAX_MEMBERS = 5000


class PluginInstallError(ValueError):
    """插件安装失败（manifest 非法、包结构不安全、插件已存在等）。"""


class PluginNotFoundError(PluginInstallError):
    """插件不存在（卸载/启停目标缺失）。"""


def _find_zip_root(members: list[zipfile.ZipInfo]) -> str:
    """定位包内根目录：``plugin.json`` 在根则用根；否则唯一顶层目录才算。

    兼容 macOS 等把整个目录包成单层 ``<name>/`` 的打包习惯。
    """
    all_names = {member.filename.replace("\\", "/") for member in members}
    top_names = {name.split("/", 1)[0] for name in all_names if name.strip()}
    if PLUGIN_MANIFEST_FILENAME in top_names:
        return ""
    if len(top_names) == 1 and f"{next(iter(top_names))}/{PLUGIN_MANIFEST_FILENAME}" in all_names:
        return next(iter(top_names)) + "/"
    raise PluginInstallError(
        f"zip 根目录下必须包含 {PLUGIN_MANIFEST_FILENAME}（或仅有一个包含它的顶层目录）"
    )


def _validate_zip_members(archive: zipfile.ZipFile) -> None:
    """结构安全校验：成员数、解压总量、路径与符号链接。"""
    members = archive.infolist()
    if len(members) > PLUGIN_ZIP_MAX_MEMBERS:
        raise PluginInstallError(f"zip 成员数超过上限 {PLUGIN_ZIP_MAX_MEMBERS}")
    total_uncompressed = 0
    for member in members:
        if member.is_dir():
            continue
        total_uncompressed += int(member.file_size or 0)
        if total_uncompressed > PLUGIN_ZIP_MAX_UNCOMPRESSED_BYTES:
            raise PluginInstallError(f"zip 解压总量超过上限 {PLUGIN_ZIP_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)}MB")
        # Unix 文件类型位：S_IFLNK 表示符号链接成员。
        if ((member.external_attr >> 16) & 0o170000) == 0o120000:
            raise PluginInstallError(f"zip 内不允许符号链接成员: {member.filename}")
        name = member.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise PluginInstallError(f"zip 成员路径非法: {member.filename}")


def _safe_extract(archive: zipfile.ZipFile, members: list[zipfile.ZipInfo], target_root: Path, zip_root: str) -> None:
    """把包内 ``zip_root`` 下的成员解压到 ``target_root``，逐条防越界。"""
    target_root_resolved = target_root.resolve()
    for member in members:
        name = member.filename.replace("\\", "/")
        if zip_root and not name.startswith(zip_root):
            continue
        relative = name[len(zip_root):] if zip_root else name
        if not relative or relative.endswith("/"):
            # 目录成员由文件成员的 mkdir(parents=True) 隐式创建。
            continue
        destination = (target_root / relative).resolve()
        if destination != target_root_resolved and target_root_resolved not in destination.parents:
            raise PluginInstallError(f"zip 成员越界: {member.filename}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)


def install_plugin_from_zip_bytes(data: bytes) -> PluginManifest:
    """从 zip 字节流安装插件，成功返回解析后的 manifest。

    同名插件已存在时拒绝（先卸载再安装），避免半覆盖状态。
    """
    if len(data) > PLUGIN_ZIP_MAX_BYTES:
        raise PluginInstallError(f"插件包超过大小上限 {PLUGIN_ZIP_MAX_BYTES // (1024 * 1024)}MB")
    if not data:
        raise PluginInstallError("插件包内容为空")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise PluginInstallError("插件包不是合法 zip 文件") from err
    with archive:
        try:
            _validate_zip_members(archive)
            members = [member for member in archive.infolist() if not member.is_dir()]
            zip_root = _find_zip_root(members + [info for info in archive.infolist() if info.is_dir()])
            manifest_bytes = archive.read(f"{zip_root}{PLUGIN_MANIFEST_FILENAME}")
        except PluginInstallError:
            raise
        except KeyError as err:
            raise PluginInstallError(f"zip 内缺少 {PLUGIN_MANIFEST_FILENAME}") from err
        import json

        try:
            manifest_data = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as err:
            raise PluginInstallError(f"{PLUGIN_MANIFEST_FILENAME} 解析失败: {err}") from err
        try:
            manifest = parse_plugin_manifest(manifest_data)
        except PluginManifestError as err:
            raise PluginInstallError(str(err)) from err
        # 声明的技能必须真实存在且带 SKILL.md。
        for skill_name in manifest.skills:
            skill_member = f"{zip_root}skills/{skill_name}/SKILL.md"
            if skill_member not in {member.filename.replace("\\", "/") for member in members}:
                raise PluginInstallError(f"manifest 声明的技能缺少 SKILL.md: {skill_name}")
        target = plugin_dir(manifest.name)
        if target.exists():
            raise PluginInstallError(f"插件已存在，请先卸载再安装: {manifest.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f"{target.name}.installing")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            _safe_extract(archive, members, staging, zip_root)
            if not (staging / PLUGIN_MANIFEST_FILENAME).is_file():
                raise PluginInstallError(f"解压结果缺少 {PLUGIN_MANIFEST_FILENAME}")
            staging.rename(target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    clear_metadata_cache()
    logger.info("插件安装成功: name=%s version=%s", manifest.name, manifest.version)
    return manifest


def uninstall_plugin(name: str) -> PluginManifest:
    """卸载并删除插件目录；插件不存在抛 ``PluginNotFoundError``。"""
    from .loader import load_plugins_metadata

    metadata = load_plugins_metadata().get(name)
    if metadata is None:
        raise PluginNotFoundError(f"插件不存在: {name}")
    manifest = metadata.manifest
    shutil.rmtree(metadata.dir, ignore_errors=False)
    clear_metadata_cache()
    logger.info("插件已卸载: name=%s", name)
    return manifest


__all__ = [
    "PLUGIN_ZIP_MAX_BYTES",
    "PluginInstallError",
    "PluginNotFoundError",
    "install_plugin_from_zip_bytes",
    "uninstall_plugin",
]
