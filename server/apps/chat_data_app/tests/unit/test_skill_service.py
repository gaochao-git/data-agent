import os
import sys
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.skills import clear_metadata_cache, format_skills_prompt, parse_metadata
from apps.chat_data_app.agent.runtime_options import AgentRuntimeOptions
from apps.chat_data_app.agent.skills import skill_service as skill_service_module
from apps.chat_data_app.agent.skills.skill_service import SkillService, skill_service


@pytest.fixture(autouse=True)
def _patch_user_skill_root(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)


def test_parse_metadata_supports_nested_skill_metadata(tmp_path):
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: demo-skill
title: 示例技能
description: 示例说明
metadata:
  domain: 数据分析
  category: analysis_assistant
  output_type: answer_plan
  input_schema:
    fields:
      - name: target
        required: true
---

# 示例技能
""",
        encoding="utf-8",
    )

    metadata = parse_metadata(skill_file)

    assert metadata is not None
    assert metadata["name"] == "demo-skill"
    assert metadata["title"] == "示例技能"
    assert metadata["domain"] == "数据分析"
    assert metadata["category"] == "analysis_assistant"
    assert metadata["output_type"] == "answer_plan"
    assert metadata["input_schema"]["fields"][0]["name"] == "target"


def test_skill_service_lists_and_filters_skills():
    clear_metadata_cache()

    skills = asyncio.run(skill_service.list_skills_async(search="看板", category="dashboard_generation"))

    risk_skill = next(item for item in skills if item["name"] == "dashboard-generation")
    assert risk_skill["title"] == "看板生成技能"
    assert risk_skill["output_type"] == "dashboard"
    assert risk_skill["input_schema"]["fields"][0]["name"] == "goal"


def test_skill_service_get_skill_returns_content_and_files():
    clear_metadata_cache()

    skill = asyncio.run(skill_service.get_skill_async("dashboard-generation"))

    assert skill is not None
    assert skill["name"] == "dashboard-generation"
    assert "看板生成技能" in skill["content"]
    assert isinstance(skill["references"], list)
    assert "scripts" not in skill
    assert "dashboard-generation-guide.md" in skill["references"]
    assert "examples/comprehensive-dashboard.json" in skill["assets"]


def test_officecli_system_skill_includes_standard_report_template():
    clear_metadata_cache()

    skill = asyncio.run(skill_service.get_skill_async("officecli"))

    assert skill is not None
    assert "standard-report.md" in skill["references"]
    assert "standard-report-template.docx" in skill["assets"]


def test_skill_service_gets_skill_creator_system_skill():
    clear_metadata_cache()

    skill = asyncio.run(skill_service.get_skill_async("skill-creator"))

    assert skill is not None
    assert skill["name"] == "skill-creator"
    assert skill["title"] == "技能生成技能"
    assert skill["category"] == "skill_creation"
    assert "output-patterns.md" in skill["references"]
    assert "workflows.md" in skill["references"]
    assert "scripts" not in skill


def test_skill_service_get_skill_metadata_does_not_return_content():
    clear_metadata_cache()

    skill = asyncio.run(skill_service.resolve_skill_path_async("看板生成技能"))

    assert skill is not None
    assert skill["name"] == "dashboard-generation"
    assert skill["title"] == "看板生成技能"
    assert skill["source"] == "system"
    assert "content" not in skill
    assert "path" in skill


def test_skill_service_reads_system_skill_file():
    clear_metadata_cache()

    file_data = asyncio.run(skill_service.read_skill_version_file_async(
        "skill-creator",
        1,
        "references",
        "workflows",
        "test-user",
    ))

    assert file_data["dir_name"] == "references"
    assert file_data["file_path"] == "references/workflows.md"
    assert "工作流程" in file_data["content"]


def test_skill_service_rejects_system_skill_file_write():
    clear_metadata_cache()

    try:
        asyncio.run(skill_service.save_skill_version_file_async(
            "skill-creator",
            1,
            "references",
            "workflows.md",
            "# changed",
            "test-user",
        ))
    except ValueError as exc:
        assert str(exc) == "技能不存在"
    except PermissionError as exc:
        assert str(exc) == "无权管理该技能文件"
    else:
        raise AssertionError("system skill file write should be rejected")


def test_skill_service_creates_fixed_skill_dirs(tmp_path):
    version_dir = tmp_path / "skill" / "versions" / "v1"
    skill_service_module._write_skill_md(version_dir, {
        "skill_name": "demo-skill",
        "skill_title": "示例技能",
        "skill_desc": "示例说明",
        "skill_category": "custom",
        "content": "# 示例技能",
    })

    assert (version_dir / "references").is_dir()
    assert (version_dir / "assets").is_dir()
    assert not (version_dir / "scripts").is_dir()


def test_skill_service_rejects_missing_skill_name():
    try:
        skill_service_module._validate_skill_payload({
            "skill_title": "示例技能",
            "content": "# 示例技能",
        })
    except ValueError as exc:
        assert str(exc) == "技能名称只允许小写字母、数字和中划线，不能以中划线开头或结尾，也不能出现连续中划线"
    else:
        raise AssertionError("missing skill_name should be rejected")


def test_skill_service_accepts_single_segment_skill_name():
    """Agent Skills 规范不要求必须含中划线，单个单词是合法技能名（早前版本会误拒）。"""
    skill_service_module._validate_skill_payload({
        "skill_name": "dashboard",
        "skill_title": "看板生成",
        "content": "# 看板生成",
    })


def test_skill_service_accepts_digits_in_skill_name():
    """规范允许 0-9（`a-z`、`0-9`、`-`），早前版本会误拒含数字的名字。"""
    skill_service_module._validate_skill_payload({
        "skill_name": "pdf2x",
        "skill_title": "PDF 转换",
        "content": "# PDF 转换",
    })


def test_skill_service_rejects_malformed_skill_name():
    """放宽后仍须拒绝真正非法的名字：首尾中划线、连续中划线、下划线、空格、中文、大写。"""
    for bad in ("-sales", "sales-", "sales--analysis", "sales_analysis", "sales analysis", "销售分析", "Sales"):
        with pytest.raises(ValueError, match="技能名称"):
            skill_service_module._validate_skill_payload({
                "skill_name": bad,
                "skill_title": "销售分析",
                "content": "# 销售分析",
            })


def test_skill_service_accepts_hyphenated_letters_skill_name():
    skill_service_module._validate_skill_payload({
        "skill_name": "sales-analysis",
        "skill_title": "销售分析",
        "content": "# 销售分析",
    })


def test_skill_service_helper_branches_copy_and_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    source = tmp_path / "source"
    version = tmp_path / "version"
    (source / "references" / "nested").mkdir(parents=True)
    (source / "assets").mkdir()
    (source / "references" / "nested" / "guide.md").write_text("# Guide", encoding="utf-8")
    (source / "assets" / "logo.txt").write_text("asset", encoding="utf-8")

    skill_service_module._safe_copy_skill_dir(source, version)
    assert (version / "references" / "nested" / "guide.md").exists()
    assert (version / "assets" / "logo.txt").exists()
    assert not (version / "scripts").exists()
    assert skill_service_module._list_skill_files(str(version / "SKILL.md")) == {
        "references": ["nested/guide.md"],
        "assets": ["logo.txt"],
    }
    assert skill_service_module._strip_frontmatter("---\nname: a\n---\n# 正文") == "# 正文"
    assert skill_service_module._alpha_suffix(1) == "a"
    assert skill_service_module._alpha_suffix(27) == "aa"


def test_skill_service_helper_rejects_invalid_source_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "references").write_text("not-dir", encoding="utf-8")

    with pytest.raises(ValueError, match="references 必须是目录"):
        skill_service_module._safe_copy_skill_dir(source, tmp_path / "target")

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        skill_service_module._copy_version_dir(tmp_path / "missing", existing)


def test_skill_service_safe_db_reads_fallback_to_system_skills(monkeypatch):
    service = SkillService()

    class BadStore:
        async def list_skills_async(self, **_kwargs):
            raise RuntimeError("table missing")

    service._dao = BadStore()
    fake_skill_path = Path("/tmp/SKILL.md")
    monkeypatch.setattr(skill_service_module, "load_metadata", lambda: {
        "system-skill": {
            "name": "system-skill",
            "title": "系统技能",
            "description": "说明",
            "category": "demo",
            "path": str(fake_skill_path),
        }
    })

    assert asyncio.run(service._safe_db_skills_async()) is None
    assert asyncio.run(service.list_skills_async(category="demo"))[0]["name"] == "system-skill"


def test_skill_service_db_metadata_filters_and_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    version_dir = tmp_path / "u1" / "sales-analysis" / "versions" / "v1"
    skill_md = skill_service_module._write_skill_md(version_dir, {
        "skill_name": "sales-analysis",
        "skill_title": "销售分析",
        "skill_desc": "描述",
        "skill_category": "custom",
        "content": "# 销售分析\n\n正文",
    })
    skill_service_module._write_skill_md(tmp_path / "u1" / "sales-analysis" / "versions" / "v2", {
        "skill_name": "sales-analysis",
        "skill_title": "销售分析",
        "skill_desc": "描述",
        "skill_category": "custom",
        "content": "# 销售分析\n\n正文",
    })
    row = {
        "skill_id": "skill_1",
        "skill_name": "sales-analysis",
        "skill_title": "销售分析",
        "skill_desc": "描述",
        "skill_category": "custom",
        "skill_source": "user",
        "skill_publish_status": "published",
        "current_skill_version_no": 1,
        "draft_skill_version_no": 2,
        "owner_user_id": "u1",
        "create_by": "u1",
        "enabled_flag": 1,
    }

    class Store:
        def __init__(self, source_row):
            self.source_row = source_row

        async def get_skill_async(self, _name, user_id=""):
            return self.source_row

        async def get_version_async(self, _skill_id, _version_no):
            return {"skill_id": "skill_1", "skill_version_no": _version_no, "skill_version_dir": str(version_dir), "version_publish_status": "published"}

        async def get_published_version_async(self, _skill_id, _version_no):
            return await self.get_version_async(_skill_id, _version_no)

        async def get_latest_draft_version_async(self, _skill_id):
            return await self.get_version_async(_skill_id, 2)

        async def get_latest_published_version_async(self, _skill_id):
            return await self.get_version_async(_skill_id, 1)

    service = SkillService()
    service._dao = Store(dict(row))
    async_metadata = asyncio.run(service._get_db_skill_metadata_async("sales-analysis", user_id="u1", user_names={"u1": "张三"}))
    async_detail = asyncio.run(service._get_db_skill_detail_async("sales-analysis", user_id="u1", user_names={"u1": "张三"}))

    assert async_metadata["path"] == str(skill_md)
    assert async_metadata["creator_name"] == "张三"
    # DB 技能详情的权威出口必须包含 name（原样透传，无 title 那样的 or 兜底）。
    assert async_detail["name"] == "sales-analysis"
    assert async_detail["title"] == "销售分析"
    assert async_detail["content"].startswith("# 销售分析")

    draft_row = dict(row, skill_publish_status="draft", current_skill_version_no=0)
    service._dao = Store(draft_row)
    assert asyncio.run(service._get_db_skill_metadata_async("sales-analysis", include_draft=False, user_id="u1")) is None
    assert asyncio.run(service._get_db_skill_metadata_async("sales-analysis", include_draft=True, user_id="u1")) is not None


def test_skill_service_db_metadata_handles_dao_errors(monkeypatch):
    service = SkillService()

    class BadStore:
        async def get_skill_async(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    service._dao = BadStore()

    assert asyncio.run(service._get_db_skill_metadata_async("missing")) is None


def test_skill_service_remaining_private_helper_edges(tmp_path, monkeypatch):
    service = SkillService()

    class MissingPathStore:
        async def get_skill_async(self, *_args, **_kwargs):
            return {
                "skill_id": "skill_1",
                "skill_name": "missing-skill",
                "skill_source": "user",
                "owner_user_id": "u1",
                "current_skill_version_no": 1,
            }

        async def get_published_version_async(self, *_args, **_kwargs):
            return {"skill_version_dir": str(tmp_path / "missing")}

        async def get_latest_draft_version_async(self, *_args, **_kwargs):
            return None

        async def get_latest_published_version_async(self, *_args, **_kwargs):
            return None

    service._dao = MissingPathStore()
    assert asyncio.run(service._get_db_skill_detail_async("missing-skill", user_id="u1")) is None

    class EditableStore:
        async def get_skill_async(self, *_args, **_kwargs):
            return {"skill_id": "skill_1", "skill_source": "user", "owner_user_id": "u1"}

        async def get_version_async(self, *_args, **_kwargs):
            return None

    service._dao = EditableStore()
    with pytest.raises(ValueError, match="技能版本不存在"):
        asyncio.run(service._get_editable_version_dir_async("skill_1", 1, "u1"))

    class CopyStore:
        def __init__(self, draft=None, published=None, latest=None):
            self.draft = draft
            self.published = published
            self.latest = latest

        async def get_latest_draft_version_async(self, *_args, **_kwargs):
            return self.draft

        async def get_published_version_async(self, *_args, **_kwargs):
            return self.published

        async def get_latest_published_version_async(self, *_args, **_kwargs):
            return self.latest

    draft = {"skill_version_no": 2}
    service._dao = CopyStore(draft=draft)
    assert asyncio.run(service._get_copy_source_version_async({"skill_id": "skill_1", "owner_user_id": "u1"}, "u1")) is draft

    latest = {"skill_version_no": 1}
    service._dao = CopyStore(latest=latest)
    assert asyncio.run(service._get_copy_source_version_async({"skill_id": "skill_1", "owner_user_id": "u2", "current_skill_version_no": 0}, "u1")) is latest

    service._dao = CopyStore()
    with pytest.raises(ValueError, match="没有可复制的技能版本"):
        asyncio.run(service._get_copy_source_version_async({"skill_id": "skill_1", "owner_user_id": "u2", "current_skill_version_no": 0}, "u1"))

    system_dir = tmp_path / "system-skill"
    system_dir.mkdir()
    (system_dir / "SKILL.md").write_text("---\nname: system-skill\n---\n# System", encoding="utf-8")

    async def system_metadata(name):
        return {"path": str(system_dir / "SKILL.md")} if name == "system-skill" else None

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", system_metadata)
    assert asyncio.run(service._get_system_version_dir_async("system-skill", 1)) == system_dir
    assert asyncio.run(service._get_system_version_dir_async("system-skill", 2)) is None
    assert asyncio.run(service._get_readable_version_dir_async("system-skill", 1, "u1")) == system_dir

    service._resolve_file_under_version_dir(tmp_path, "references", "guide")
    # scripts 目录已整体移除，按非法目录拒绝
    with pytest.raises(ValueError, match="只支持"):
        service._resolve_file_under_version_dir(tmp_path, "scripts", "../bad.py")

    archived = {}

    class ArchiveStore:
        async def delete_skill_async(self, skill_id, archive_name, **_kwargs):
            archived["skill_id"] = skill_id
            archived["archive_name"] = archive_name

    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    service._dao = ArchiveStore()
    asyncio.run(service._archive_user_skill_async(
        {"skill_id": "skill_1", "skill_name": "demo-skill", "owner_user_id": "u1"},
        "u1",
    ))
    assert archived["skill_id"] == "skill_1"
    assert archived["archive_name"].startswith("demo-skill--deleted-")

    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    cleanup_dir = tmp_path / "cleanup-skill"
    cleanup_dir.mkdir()
    service.cleanup_user_skill_dir("cleanup-skill")
    assert not cleanup_dir.exists()


def test_create_user_skill_async_uses_async_dao(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    archived = {}
    created = {}
    existing = {
        "skill_id": "old-id",
        "skill_name": "demo-skill",
        "skill_source": "user",
        "owner_user_id": "u1",
        "skill_publish_status": "disabled",
    }

    class FakeStore:
        async def get_skill_async(self, name, user_id=""):
            if name == "demo-skill":
                return existing if not archived else None
            if name == "new-id":
                return {"skill_id": "new-id", "skill_name": "demo-skill"}
            return None

        async def skill_name_exists_async(self, skill_name):
            return False

        async def create_skill_with_version_async(self, skill, version):
            created["skill"] = skill
            created["version"] = version

    async def fake_archive(skill, user_id, operator_name=""):
        archived["skill"] = skill
        archived["user_id"] = user_id
        archived["operator_name"] = operator_name

    async def fake_get_skill(skill_id, include_draft=False, user_id=""):
        return {"skill_id": skill_id, "name": "demo-skill"}

    monkeypatch.setattr(service, "_dao", FakeStore())
    monkeypatch.setattr(skill_service_module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "new-id"})())
    monkeypatch.setattr(service, "_archive_user_skill_async", fake_archive)
    monkeypatch.setattr(service, "get_skill_async", fake_get_skill)
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)

    result = asyncio.run(service.create_user_skill_async({
        "skill_name": "demo-skill",
        "skill_title": "示例技能",
        "content": "# 示例技能",
    }, user_id="u1"))

    assert archived["skill"] == existing
    assert archived["user_id"] == "u1"
    assert created["skill"]["skill_name"] == "demo-skill"
    # skill_root_dir / skill_version_dir 为已删除的废弃列，创建 payload 不携带
    assert "skill_root_dir" not in created["skill"]
    assert "skill_version_dir" not in created["version"]
    assert result["name"] == "demo-skill"


def test_save_skill_file_reuses_existing_draft_version(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    draft_dir = tmp_path / "u1" / "demo-skill" / "versions" / "v2"
    (draft_dir / "assets").mkdir(parents=True)

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_source": "user",
                "owner_user_id": "u1",
            }

        async def get_latest_draft_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 2}

    monkeypatch.setattr(service, "_dao", FakeStore())

    result = asyncio.run(service.save_skill_version_file_async("skill-1", 1, "assets", "demo.txt", "print('draft')\n", "u1"))

    assert result["skill_version_no"] == 2
    assert (draft_dir / "assets" / "demo.txt").read_text(encoding="utf-8") == "print('draft')\n"


def test_upload_skill_file_writes_bytes_and_validates(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    draft_dir = tmp_path / "u1" / "demo-skill" / "versions" / "v2"
    (draft_dir / "assets").mkdir(parents=True)

    class FakeUploadStream:
        """模拟 FastAPI UploadFile 的 async read(n) 分块读取接口。"""

        def __init__(self, data: bytes):
            self._data = data
            self._offset = 0

        async def read(self, size: int) -> bytes:
            chunk = self._data[self._offset:self._offset + size]
            self._offset += len(chunk)
            return chunk

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_source": "user",
                "owner_user_id": "u1",
            }

        async def get_latest_draft_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 2}

    monkeypatch.setattr(service, "_dao", FakeStore())

    # assets 目录允许二进制素材；路径穿越部分被 basename 剥离
    result = asyncio.run(service.upload_skill_version_file_async("skill-1", 1, "assets", "../logo.png", FakeUploadStream(b"\x89PNG\r\n"), "u1"))
    assert result["uploaded"] is True
    assert result["file_path"] == "assets/logo.png"
    assert (draft_dir / "assets" / "logo.png").read_bytes() == b"\x89PNG\r\n"

    # references 不再限定 .md：与主对话上传同一份类型白名单
    result = asyncio.run(service.upload_skill_version_file_async("skill-1", 1, "references", "notes.txt", FakeUploadStream(b"hello"), "u1"))
    assert result["uploaded"] is True
    assert result["file_path"] == "references/notes.txt"
    assert (draft_dir / "references" / "notes.txt").read_bytes() == b"hello"

    # 同名文件按保存语义原子覆盖
    result = asyncio.run(service.upload_skill_version_file_async("skill-1", 1, "assets", "logo.png", FakeUploadStream(b"\x89PNG-overwrite"), "u1"))
    assert result["uploaded"] is True
    assert (draft_dir / "assets" / "logo.png").read_bytes() == b"\x89PNG-overwrite"

    async def run_invalid():
        with pytest.raises(ValueError, match="文件名无效"):
            await service.upload_skill_version_file_async("skill-1", 1, "assets", "", FakeUploadStream(b"x"), "u1")
        # 不在主对话上传白名单内的类型拒绝（两个目录同口径）
        with pytest.raises(ValueError, match="不支持的文件类型"):
            await service.upload_skill_version_file_async("skill-1", 1, "assets", "tool.exe", FakeUploadStream(b"x"), "u1")
        with pytest.raises(ValueError, match="不支持的文件类型"):
            await service.upload_skill_version_file_async("skill-1", 1, "references", "tool.exe", FakeUploadStream(b"x"), "u1")
        # 大小上限复用主对话 CHAT_DATA_MAX_UPLOAD_SIZE_MB 口径（monkeypatch 调小以便验证）
        monkeypatch.setattr(skill_service_module, "MAX_UPLOAD_SIZE", 4)
        monkeypatch.setattr(skill_service_module, "MAX_UPLOAD_SIZE_MB", 1)
        with pytest.raises(ValueError, match="文件大小不能超过1MB"):
            await service.upload_skill_version_file_async("skill-1", 1, "assets", "big.pdf", FakeUploadStream(b"x" * 5), "u1")
        # 超限失败后不残留临时文件、不落目标文件（流式写盘的清理语义）
        assert (draft_dir / "assets" / "big.pdf").exists() is False
        assert list(draft_dir.rglob(".*.upload")) == []

    asyncio.run(run_invalid())


def test_save_skill_file_async_creates_draft_from_published_version(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    root_dir = tmp_path / "u1" / "demo-skill"
    published_dir = root_dir / "versions" / "v1"
    (published_dir / "assets").mkdir(parents=True)
    (published_dir / "assets" / "old.txt").write_text("print('old')\n", encoding="utf-8")
    inserted = {}

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_source": "user",
                "owner_user_id": "u1",
                "current_skill_version_no": 1,
            }

        async def get_latest_draft_version_async(self, skill_id):
            return None

        async def next_version_no_async(self, skill_id):
            return 2

        async def get_version_async(self, skill_id, version_no):
            return {"skill_id": skill_id, "skill_version_no": 1}

        async def get_latest_published_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 1}

        async def insert_version_async(self, version):
            inserted.update(version)

    monkeypatch.setattr(service, "_dao", FakeStore())

    result = asyncio.run(service.save_skill_version_file_async("skill-1", 1, "assets", "new.txt", "print('new')\n", "u1", operator_name="测试用户"))
    draft_dir = root_dir / "versions" / "v2"

    assert result["skill_version_no"] == 2
    assert inserted["skill_version_no"] == 2
    assert inserted["create_by"] == "u1"
    assert inserted["create_by_name"] == "测试用户"
    assert inserted["update_by"] == "u1"
    assert inserted["update_by_name"] == "测试用户"
    assert (draft_dir / "assets" / "old.txt").read_text(encoding="utf-8") == "print('old')\n"
    assert (draft_dir / "assets" / "new.txt").read_text(encoding="utf-8") == "print('new')\n"


def test_delete_skill_file_creates_draft_from_published_version(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    root_dir = tmp_path / "skill"
    published_dir = root_dir / "versions" / "v1"
    (published_dir / "assets").mkdir(parents=True)
    (published_dir / "assets" / "old.txt").write_text("print('old')\n", encoding="utf-8")
    inserted = {}

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_source": "user",
                "owner_user_id": "u1",
                "current_skill_version_no": 1,
            }

        async def get_latest_draft_version_async(self, skill_id):
            return None

        async def next_version_no_async(self, skill_id):
            return 2

        async def get_version_async(self, skill_id, version_no):
            return {"skill_id": skill_id, "skill_version_no": 1, "skill_version_dir": str(published_dir)}

        async def get_latest_published_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 1, "skill_version_dir": str(published_dir)}

        async def insert_version_async(self, version):
            inserted.update(version)

    monkeypatch.setattr(service, "_dao", FakeStore())

    result = asyncio.run(service.delete_skill_version_file_async("skill-1", 1, "assets", "old.txt", "u1", operator_name="测试用户"))
    draft_dir = root_dir / "versions" / "v2"

    assert result["skill_version_no"] == 2
    assert inserted["skill_version_no"] == 2
    assert inserted["create_by"] == "u1"
    assert inserted["create_by_name"] == "测试用户"
    assert inserted["update_by"] == "u1"
    assert inserted["update_by_name"] == "测试用户"
    assert (published_dir / "assets" / "old.txt").exists()
    assert not (draft_dir / "assets" / "old.txt").exists()


def test_update_skill_metadata_async_does_not_create_draft_or_touch_skill_body(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    draft_dir = tmp_path / "skill" / "versions" / "v2"
    draft_dir.mkdir(parents=True)
    (draft_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ntitle: Old title\ndescription: Old desc\nmetadata:\n  category: custom\n  output_type: text\n---\n\n# Keep me\n",
        encoding="utf-8",
    )
    updated = {}

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_title": "Old title",
                "skill_desc": "Old desc",
                "skill_category": "custom",
                "visibility_type": "private",
                "skill_source": "user",
                "owner_user_id": "u1",
                "current_skill_version_no": 1,
            }

        async def get_latest_draft_version_async(self, skill_id):
            raise AssertionError("metadata update should not touch draft version")

        async def update_skill_async(self, skill_id, values, update_by="", update_by_name=""):
            updated.update(values)

    async def fake_get_skill(skill_id, include_draft=False, user_id=""):
        return {"skill_id": skill_id, "name": "demo-skill"}

    monkeypatch.setattr(service, "_dao", FakeStore())
    monkeypatch.setattr(service, "get_skill_async", fake_get_skill)

    asyncio.run(service.update_user_skill_draft_async("skill-1", {"skill_title": "New title", "skill_desc": "New desc"}, "u1"))

    saved = (draft_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "title: Old title" in saved
    assert "# Keep me" in saved
    assert updated["skill_title"] == "New title"
    assert updated["skill_publish_status"] == "published"


def test_skill_service_helper_conversions_and_name_generation(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()

    row = {
        "skill_id": "skill-1",
        "skill_name": "sales-analysis",
        "skill_title": "",
        "skill_desc": None,
        "skill_category": "custom",
        "skill_source": "user",
        "visibility_type": "",
        "skill_publish_status": "",
        "current_skill_version_no": "2",
        "draft_skill_version_no": "3",
        "owner_user_id": "u1",
        "create_by": "creator",
        "favorite_flag": 1,
        "enabled_flag": 0,
    }
    info = skill_service_module._skill_row_to_info(row, user_id="u1", user_names={"creator": "创建人"})
    version = skill_service_module._skill_version_to_info({"skill_id": "skill-1", "skill_version_no": "2", "version_publish_status": ""}, current_version_no=2)
    system = skill_service_module._system_metadata_to_info({"name": "system-skill", "title": "", "description": None, "input_schema": {"type": "object"}})

    class FakeStore:
        def __init__(self):
            self.seen = []

        async def get_skill_async(self, name, user_id=""):
            self.seen.append(name)
            return {"skill_id": name} if name == "sales-analysis-copy" else None

    dao = FakeStore()
    monkeypatch.setattr(service, "_dao", dao)

    assert skill_service_module.resolve_skill_name("风险分析技能") == "dashboard-generation"
    assert skill_service_module._alpha_suffix(1) == "a"
    assert skill_service_module._alpha_suffix(27) == "aa"
    assert info["title"] == "sales-analysis"
    assert info["creator_name"] == "创建人"
    assert info["can_manage"] is True
    assert info["has_draft"] is True
    assert info["favorite"] is True
    assert info["enabled"] is False
    assert version["current"] is True
    assert version["version_publish_status"] == "draft"
    assert system["title"] == "system-skill"
    assert system["input_schema"]["type"] == "object"
    assert asyncio.run(service._next_copy_skill_name_async("sales-analysis", "u1")) == "sales-analysis-copy-a"
    assert dao.seen == ["sales-analysis-copy", "sales-analysis-copy-a"]


def test_skill_service_file_path_validation_and_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    service = skill_service_module.SkillService()
    version_dir = tmp_path / "skill" / "versions" / "v1"
    (version_dir / "references" / "nested").mkdir(parents=True)
    (version_dir / "assets").mkdir()
    (version_dir / "references" / "nested" / "guide.md").write_text("guide", encoding="utf-8")
    (version_dir / "assets" / "demo.txt").write_text("asset", encoding="utf-8")

    target = service._resolve_file_under_version_dir(version_dir, "references", "nested/guide")

    assert target.name == "guide.md"
    assert skill_service_module._list_skill_files(str(version_dir / "SKILL.md")) == {
        "references": ["nested/guide.md"],
        "assets": ["demo.txt"],
    }
    for dir_name, file_path, message in [
        ("bad", "a.md", "只支持"),
        ("references", "../a.md", "文件路径无效"),
        ("references", "/a.md", "文件路径无效"),
    ]:
        try:
            service._resolve_file_under_version_dir(version_dir, dir_name, file_path)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("invalid skill file path should be rejected")

    with pytest.raises(ValueError, match="技能版本目录无效"):
        service._resolve_file_under_version_dir(Path(""), "references", "guide")


def test_skill_service_rejects_paths_outside_user_skill_root(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path / "skills")
    service = skill_service_module.SkillService()

    outside_version = tmp_path / "outside" / "versions" / "v1"
    outside_version.mkdir(parents=True)

    with pytest.raises(ValueError, match="越界"):
        service._resolve_user_file_under_version_dir(outside_version, "references", "guide")
    with pytest.raises(ValueError, match="越界"):
        skill_service_module._copy_version_dir(outside_version, tmp_path / "skills" / "demo-skill" / "versions" / "v2")


def test_skill_service_rejects_symlink_files(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path / "skills")
    source = tmp_path / "source"
    source_references = source / "references"
    source_references.mkdir(parents=True)
    secret = tmp_path / "secret.md"
    secret.write_text("secret", encoding="utf-8")
    (source_references / "link.md").symlink_to(secret)

    target = tmp_path / "skills" / "demo-skill" / "versions" / "v1"
    with pytest.raises(ValueError, match="软链接"):
        skill_service_module._safe_copy_skill_dir(source, target)


def test_skill_service_rejects_symlink_fixed_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path / "skills")
    version_dir = tmp_path / "skills" / "demo-skill" / "versions" / "v1"
    external_references = tmp_path / "external-references"
    external_references.mkdir(parents=True)
    version_dir.mkdir(parents=True)
    (version_dir / "references").symlink_to(external_references, target_is_directory=True)

    with pytest.raises(ValueError, match="软链接"):
        skill_service_module._ensure_skill_dirs(version_dir)


def test_list_skills_async_filters_user_and_system_metadata(monkeypatch):
    service = skill_service_module.SkillService()
    rows = [
        {"skill_id": "u1-skill", "skill_name": "private-skill", "skill_title": "私有技能", "skill_desc": "描述", "skill_category": "custom", "skill_source": "user", "owner_user_id": "u1", "current_skill_version_no": 1, "favorite_flag": 1},
        {"skill_id": "u2-skill", "skill_name": "other-skill", "skill_title": "他人技能", "skill_desc": "描述", "skill_category": "custom", "skill_source": "user", "owner_user_id": "u2", "current_skill_version_no": 0, "favorite_flag": 0},
    ]

    async def fake_safe_db_skills_async(user_id="", include_disabled=False):
        return rows

    monkeypatch.setattr(service, "_safe_db_skills_async", fake_safe_db_skills_async)
    monkeypatch.setattr(skill_service_module, "load_metadata", lambda: {
        "metadata-only": {"name": "metadata-only", "title": "元数据技能", "category": "system"},
    })

    runtime = asyncio.run(service.list_skills_async(scope="runtime", user_id="u1"))
    mine = asyncio.run(service.list_skills_async(scope="mine", user_id="u1"))

    assert [item["name"] for item in runtime] == ["metadata-only", "private-skill"]
    assert [item["name"] for item in mine] == ["private-skill"]


def test_db_skill_metadata_and_detail_resolve_draft_and_published_versions(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    # 版本目录按 owner_user_id + skill_name + version_no 推导，DB 列为废弃占位。
    user_root = tmp_path.resolve()
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", user_root)
    draft_dir = user_root / "u1" / "demo-skill" / "versions" / "v2"
    published_dir = user_root / "u1" / "demo-skill" / "versions" / "v1"
    for version_dir, title in [(draft_dir, "草稿标题"), (published_dir, "发布标题")]:
        version_dir.mkdir(parents=True)
        (version_dir / "SKILL.md").write_text(
            f"---\nname: demo-skill\ntitle: {title}\ndescription: 描述\nmetadata:\n  category: custom\n  output_type: text\n---\n\n# {title}\n",
            encoding="utf-8",
        )
    row = {
        "skill_id": "skill-1",
        "skill_name": "demo-skill",
        "skill_title": "数据库标题",
        "skill_desc": "数据库描述",
        "skill_category": "custom",
        "skill_source": "user",
        "visibility_type": "global",
        "skill_publish_status": "published",
        "current_skill_version_no": 1,
        "draft_skill_version_no": 2,
        "owner_user_id": "u1",
        "create_by": "u1",
        "enabled_flag": 1,
    }

    class FakeStore:
        async def get_skill_async(self, name, user_id=""):
            return row

        async def get_latest_draft_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 2, "skill_version_dir": str(draft_dir)}

        async def get_published_version_async(self, skill_id, version_no):
            return {"skill_id": skill_id, "skill_version_no": 1, "skill_version_dir": str(published_dir)}

        async def get_latest_published_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 1, "skill_version_dir": str(published_dir)}

    monkeypatch.setattr(service, "_dao", FakeStore())

    metadata = asyncio.run(service._get_db_skill_metadata_async("demo-skill", include_draft=True, user_id="u1", user_names={"u1": "用户一"}))
    detail = asyncio.run(service._get_db_skill_detail_async("demo-skill", include_draft=True, user_id="u1", user_names={"u1": "用户一"}))
    published_meta = asyncio.run(service._get_db_skill_metadata_async("demo-skill", include_draft=False, user_id="u1"))
    other_user_metadata = asyncio.run(service._get_db_skill_metadata_async("demo-skill", include_draft=True, user_id="u2"))
    other_user_detail = asyncio.run(service._get_db_skill_detail_async("demo-skill", include_draft=True, user_id="u2"))

    assert metadata["path"] == str(draft_dir / "SKILL.md")
    assert metadata["creator_name"] == "用户一"
    assert metadata["can_manage"] is True
    assert metadata["has_draft"] is True
    assert detail["content"] == "# 草稿标题"
    assert detail["publish_status"] == "published"
    assert published_meta["path"] == str(published_dir / "SKILL.md")
    assert other_user_metadata["path"] == str(published_dir / "SKILL.md")
    assert other_user_metadata["can_manage"] is False
    assert other_user_metadata["has_draft"] is False
    assert other_user_metadata["draft_version_no"] == 0
    assert other_user_detail["content"] == "# 发布标题"


def test_db_skill_metadata_ignores_legacy_system_record(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    root_dir = tmp_path / "system-skill"
    root_dir.mkdir()
    (root_dir / "SKILL.md").write_text(
        "---\nname: system-skill\ntitle: 系统技能\ndescription: 描述\nmetadata:\n  category: system\n---\n\n# 系统技能\n",
        encoding="utf-8",
    )
    row = {
        "skill_id": "sys-1",
        "skill_name": "system-skill",
        "skill_title": "系统技能",
        "skill_desc": "描述",
        "skill_category": "system",
        "skill_source": "system",
        "skill_publish_status": "published",
        "current_skill_version_no": 1,
        "enabled_flag": 1,
    }

    class FakeStore:
        async def get_skill_async(self, name, user_id=""):
            return row

    monkeypatch.setattr(service, "_dao", FakeStore())

    metadata = asyncio.run(service._get_db_skill_metadata_async("system-skill", include_draft=False, user_id="u1"))
    detail = asyncio.run(service._get_db_skill_detail_async("system-skill", include_draft=False, user_id="u1"))

    assert metadata is None
    assert detail is None


def test_async_publish_versions_rollback_favorite_and_delete_lifecycle(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    calls = []
    skill = {"skill_id": "skill-1", "skill_name": "demo-skill", "skill_source": "user", "owner_user_id": "u1", "current_skill_version_no": 1}
    version_dir = tmp_path / "demo-skill" / "versions" / "v1"

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return skill if skill_id in {"skill-1", "demo-skill"} else None

        async def get_latest_draft_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 2}

        async def publish_version_async(self, skill_id, version_no, update_by="", update_by_name=""):
            calls.append(("publish", skill_id, version_no, update_by))

        async def list_versions_async(self, skill_id):
            return [{"skill_id": skill_id, "skill_version_no": 1, "version_publish_status": "published"}]

        async def get_version_async(self, skill_id, version_no):
            return {
                "skill_id": skill_id,
                "skill_version_no": version_no,
                "skill_version_dir": str(version_dir),
                "version_publish_status": "published",
            }

        async def activate_version_async(self, skill_id, version_no, update_by="", update_by_name=""):
            calls.append(("activate", skill_id, version_no, update_by))

        async def set_favorite_async(self, user_id, skill_id, favorite, operator="", operator_name=""):
            calls.append(("favorite", user_id, skill_id, favorite, operator))

    async def fake_get_skill(skill_id, include_draft=False, user_id=""):
        return {"skill_id": skill_id, "ok": True}

    async def fake_archive(item, user_id, operator_name=""):
        calls.append(("archive", item["skill_id"], user_id, operator_name))

    monkeypatch.setattr(service, "_dao", FakeStore())
    monkeypatch.setattr(service, "get_skill_async", fake_get_skill)
    monkeypatch.setattr(service, "_archive_user_skill_async", fake_archive)

    async def run_checks():
        assert (await service.publish_user_skill_async("skill-1", "u1"))["ok"] is True
        assert (await service.list_skill_versions_async("skill-1", "u1"))[0]["current"] is True
        assert (await service.get_skill_version_detail_async("skill-1", 1, "u1"))["current"] is True
        assert (await service.rollback_user_skill_async("skill-1", 1, "u1"))["ok"] is True
        assert await service.favorite_skill_async("demo-skill", "u1", False) == {"skill_id": "skill-1", "favorite": False}
        await service.delete_user_skill_async("skill-1", "u1")

    asyncio.run(run_checks())

    assert ("publish", "skill-1", 2, "u1") in calls
    assert ("activate", "skill-1", 1, "u1") in calls
    assert ("favorite", "u1", "skill-1", False, "u1") in calls


def test_publish_user_skill_async_rejects_system_name_conflict(monkeypatch):
    service = skill_service_module.SkillService()
    skill = {"skill_id": "skill-1", "skill_name": "demo-skill", "skill_source": "user", "owner_user_id": "u1"}
    calls = []

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id="", include_draft=False):
            return skill

        async def get_latest_draft_version_async(self, skill_id):
            return {"skill_version_no": 1}

        async def publish_version_async(self, skill_id, version_no, update_by="", update_by_name=""):
            calls.append(("publish", skill_id, version_no, update_by))

    monkeypatch.setattr(service, "_dao", FakeStore())

    async def _system_meta(name):
        return {"name": name, "source": "system"}

    async def _no_meta(name):
        return None

    async def run_conflict_publish():
        # 命中系统技能同名 -> 拒绝并提示改名
        monkeypatch.setattr(service, "_get_system_skill_metadata_async", _system_meta)
        with pytest.raises(ValueError, match="与系统技能冲突"):
            await service.publish_user_skill_async("skill-1", "u1")

    async def run_normal_publish():
        # 无系统技能冲突 -> 正常发布，且真正走发布流程
        monkeypatch.setattr(service, "_get_system_skill_metadata_async", _no_meta)
        await service.publish_user_skill_async("skill-1", "u1")

    asyncio.run(run_conflict_publish())
    asyncio.run(run_normal_publish())
    assert ("publish", "skill-1", 1, "u1") in calls


def test_skill_payload_build_and_validation_edge_cases(tmp_path, monkeypatch):
    version_dir = tmp_path / "skill" / "versions" / "v1"

    frontmatter = skill_service_module._build_skill_md({
        "skill_name": "demo-skill",
        "skill_title": "Demo",
        "content": "---\nname: demo-skill\n---\n# Demo",
    })
    assert frontmatter.endswith("\n")
    assert frontmatter.count("---") == 2

    long_skill_name = "a" * 63 + "-b"
    for payload, message in [
        ({"skill_name": long_skill_name, "skill_title": "标题"}, "长度必须在"),
        ({"skill_name": "demo-skill", "skill_title": ""}, "技能标题不能为空"),
    ]:
        with pytest.raises(ValueError, match=message):
            skill_service_module._validate_skill_payload(payload)

    monkeypatch.setattr(skill_service_module, "parse_metadata", lambda _path: None)
    with pytest.raises(ValueError, match="SKILL.md 元数据无效"):
        skill_service_module._write_skill_md(version_dir, {"skill_name": "demo-skill", "skill_title": "标题"})


def test_import_user_skill_from_directory_async_copies_fixed_dirs(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    source_dir = tmp_path / "source-skill"
    (source_dir / "references").mkdir(parents=True)
    (source_dir / "assets").mkdir()
    (source_dir / "SKILL.md").write_text(
        "---\nname: async-skill\ntitle: 异步技能\ndescription: 异步导入\nmetadata:\n  category: custom\n---\n\n# 异步技能\n",
        encoding="utf-8",
    )
    (source_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")
    created = {}

    class FakeStore:
        async def get_skill_async(self, name, user_id=""):
            if name == "async-skill":
                return None
            if name == "new-id":
                return {"skill_id": "new-id", "skill_name": "async-skill", "skill_source": "user", "owner_user_id": "u1"}
            return None

        async def create_skill_with_version_async(self, skill, version):
            created["skill"] = skill
            created["version"] = version

        async def skill_name_exists_async(self, skill_name):
            return False

        async def get_version_async(self, skill_id, version_no):
            return {"skill_id": skill_id, "skill_version_no": version_no, "skill_version_dir": str(tmp_path / "user" / "async-skill" / "versions" / "v1")}

    async def fake_get_skill(skill_id, include_draft=False, user_id=""):
        return {"skill_id": skill_id, "name": "async-skill", "draft_version_no": 1}

    monkeypatch.setattr(service, "_dao", FakeStore())
    monkeypatch.setattr(service, "get_skill_async", fake_get_skill)
    monkeypatch.setattr(skill_service_module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "new-id"})())
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path / "user")

    result = asyncio.run(service.import_user_skill_from_directory_async(source_dir, user_id="u1", operator_name="测试用户"))

    assert result["name"] == "async-skill"
    assert created["skill"]["skill_name"] == "async-skill"
    assert created["skill"]["create_by"] == "u1"
    assert created["skill"]["create_by_name"] == "测试用户"
    assert created["version"]["update_by"] == "u1"
    assert created["version"]["update_by_name"] == "测试用户"
    assert (tmp_path / "user" / "u1" / "async-skill" / "versions" / "v1" / "references" / "guide.md").read_text(encoding="utf-8") == "guide"


def test_import_user_skill_from_directory_async_rejects_invalid_inputs(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()

    async def run_checks():
        with pytest.raises(ValueError, match="技能目录不存在"):
            await service.import_user_skill_from_directory_async(tmp_path / "missing", user_id="u1")

        source = tmp_path / "source"
        source.mkdir()
        with pytest.raises(ValueError, match="缺少 SKILL.md"):
            await service.import_user_skill_from_directory_async(source, user_id="u1")

        (source / "SKILL.md").write_text("# invalid", encoding="utf-8")
        monkeypatch.setattr(skill_service_module, "parse_metadata", lambda _path: None)
        with pytest.raises(ValueError, match="元数据无效"):
            await service.import_user_skill_from_directory_async(source, user_id="u1")

    asyncio.run(run_checks())


def test_copy_user_skill_async_uses_owner_draft_and_rejects_missing_versions(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    user_skill_root = tmp_path / "user"
    # 源版本目录按 owner_user_id + skill_name + version_no 推导。
    draft_dir = user_skill_root / "u1" / "source-skill" / "versions" / "v2"
    draft_dir.mkdir(parents=True)
    (draft_dir / "SKILL.md").write_text(
        "---\nname: source-skill\ntitle: 草稿技能\nmetadata:\n  category: custom\n---\n\n# 草稿内容\n",
        encoding="utf-8",
    )
    created = {}

    class FakeStore:
        def __init__(self, with_version=True):
            self.with_version = with_version

        async def get_skill_async(self, name, user_id=""):
            if name == "source-id":
                return {"skill_id": "source-id", "skill_name": "source-skill", "skill_title": "源技能", "skill_source": "user", "owner_user_id": "u1", "current_skill_version_no": 0}
            if name in {"source-skill-copy", "new-id"}:
                return None if name == "source-skill-copy" else {"skill_id": "new-id", "skill_name": "source-skill-copy", "skill_source": "user", "owner_user_id": "u1"}
            return None

        async def get_latest_draft_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 2, "skill_version_dir": str(draft_dir)} if self.with_version else None

        async def get_published_version_async(self, skill_id, version_no):
            return None

        async def get_latest_published_version_async(self, skill_id):
            return None

        async def create_skill_with_version_async(self, skill, version):
            created["skill"] = skill
            created["version"] = version

        async def skill_name_exists_async(self, skill_name):
            return False

        async def get_version_async(self, skill_id, version_no):
            return {"skill_id": skill_id, "skill_version_no": version_no, "skill_version_dir": str(user_skill_root / "source-skill-copy" / "versions" / "v1")}

    async def fake_get_skill(skill_id, include_draft=False, user_id=""):
        return {"skill_id": skill_id, "name": "source-skill-copy", "draft_version_no": 1}

    async def run_checks():
        monkeypatch.setattr(service, "_dao", FakeStore())
        monkeypatch.setattr(service, "get_skill_async", fake_get_skill)
        monkeypatch.setattr(skill_service_module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "new-id"})())
        monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", user_skill_root)
        result = await service.copy_user_skill_async("source-id", user_id="u1")
        assert result["name"] == "source-skill-copy"
        assert created["skill"]["skill_title"] == "源技能 副本"

        monkeypatch.setattr(service, "_dao", FakeStore(with_version=False))
        with pytest.raises(ValueError, match="没有可复制的技能版本"):
            await service._get_copy_source_version_async({"skill_id": "source-id", "owner_user_id": "other", "current_skill_version_no": 0}, "u1")

    asyncio.run(run_checks())


def test_read_list_delete_skill_files_async_and_error_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    version_dir = tmp_path / "skill" / "versions" / "v1"
    (version_dir / "references").mkdir(parents=True)
    (version_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")

    async def fake_readable(*_args, **_kwargs):
        return version_dir

    class MissingStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return None

    async def run_checks():
        monkeypatch.setattr(service, "_get_readable_version_dir_async", fake_readable)
        listed = await service.list_skill_version_files_async("skill-1", 1, "u1")
        read = await service.read_skill_version_file_async("skill-1", 1, "references", "guide", "u1")
        assert listed["references"] == ["guide.md"]
        assert read["content"] == "guide"
        with pytest.raises(ValueError, match="文件不存在"):
            await service.read_skill_version_file_async("skill-1", 1, "references", "missing", "u1")

        monkeypatch.setattr(service, "_dao", MissingStore())
        with pytest.raises(ValueError, match="技能不存在"):
            await service.save_skill_version_file_async("skill-1", 1, "assets", "a.txt", "", "u1")
        with pytest.raises(ValueError, match="技能不存在"):
            await service.delete_skill_version_file_async("skill-1", 1, "assets", "a.txt", "u1")

    asyncio.run(run_checks())


def test_read_raw_and_office_preview_skill_files_async(tmp_path, monkeypatch):
    """raw 读取返回原始字节与 mime；office 预览复用工作区 OfficeCLI 渲染引擎。

    哨兵自检：captured 记录 fake 渲染引擎的入参，若 service 未真正复用引擎
    （workspace_root / file_path / max_chars 传错）本用例必然失败。
    """
    service = skill_service_module.SkillService()
    version_dir = tmp_path / "skill" / "versions" / "v1"
    (version_dir / "assets").mkdir(parents=True)
    (version_dir / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n")
    (version_dir / "references").mkdir(parents=True)
    (version_dir / "references" / "report.docx").write_bytes(b"PK\x03\x04")
    (version_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")

    async def fake_readable(*_args, **_kwargs):
        return version_dir

    monkeypatch.setattr(service, "_get_readable_version_dir_async", fake_readable)

    captured = {}

    class FakePreviewService:
        _result: dict = {}

        async def render_html(self, runtime, *, file_path, max_chars):
            captured["workspace_root"] = runtime.workspace_root
            captured["user_id"] = runtime.user_id
            captured["backend"] = runtime.backend_name
            captured["file_path"] = file_path
            captured["max_chars"] = max_chars
            # 暂存文件在 TemporaryDirectory 退出后即清理，存在性只能在调用时点断言
            captured["staged_exists"] = (Path(runtime.workspace_root) / "workspace" / Path(file_path).name).is_file()
            return dict(self._result)

    def patch_office(result):
        fake_service = FakePreviewService()
        fake_service._result = result
        monkeypatch.setattr(
            "apps.chat_data_app.agent.workspace.workspace_special_services.workspace_office_preview_service",
            fake_service,
        )
        monkeypatch.setattr("apps.chat_data_app.agent.workspace.runtime.OFFICE_PREVIEW_MAX_CHARS", 12345)

    async def run_checks():
        raw, content_type = await service.read_skill_version_file_raw_async("skill-1", 1, "assets", "logo.png", "u1")
        assert raw == b"\x89PNG\r\n"
        assert content_type == "image/png"
        with pytest.raises(ValueError, match="文件不存在"):
            await service.read_skill_version_file_raw_async("skill-1", 1, "assets", "missing.png", "u1")

        # 正常渲染：复用引擎，源文件暂存到临时 workspace/ 结构、以标准虚拟路径渲染
        patch_office({"renderer": "officecli-html", "html": "<html>ok</html>", "truncated": False})
        preview = await service.render_skill_office_preview_async("skill-1", 1, "references", "report.docx", "u1")
        assert preview == {"available": True, "renderer": "officecli-html", "html": "<html>ok</html>", "truncated": False}
        assert captured["staged_exists"] is True
        assert captured["user_id"] == "u1"
        assert captured["backend"] == "local"
        assert captured["file_path"] == "/mnt/user-data/workspace/report.docx"
        assert captured["max_chars"] == 12345

        # 截断：转换为与主对话一致的 available=False / preview_too_large
        patch_office({"renderer": "officecli-html", "html": "", "truncated": True})
        preview = await service.render_skill_office_preview_async("skill-1", 1, "references", "report.docx", "u1")
        assert preview == {"available": False, "reason": "preview_too_large"}

        # 引擎返回空 HTML：视为失败
        patch_office({"renderer": "officecli-html", "html": "", "truncated": False})
        with pytest.raises(RuntimeError, match="OfficeCLI"):
            await service.render_skill_office_preview_async("skill-1", 1, "references", "report.docx", "u1")

        # 非 Office 后缀：提前拦截
        with pytest.raises(ValueError, match="Office 预览仅支持"):
            await service.render_skill_office_preview_async("skill-1", 1, "assets", "logo.png", "u1")

        with pytest.raises(ValueError, match="文件不存在"):
            await service.render_skill_office_preview_async("skill-1", 1, "references", "missing.docx", "u1")

        # 预览上限与主对话工作区端点同源：文本 5MB / raw 20MB / Office 源 50MB
        # （文本/raw 的上限是默认参数、def 时求值，测试显式传参验证；
        #   Office 源上限在函数体内取模块常量，monkeypatch 生效）
        with pytest.raises(ValueError, match="文件过大"):
            await service.read_skill_version_file_async("skill-1", 1, "references", "guide.md", "u1", max_bytes=4)
        text = await service.read_skill_version_file_async("skill-1", 1, "references", "guide.md", "u1")
        assert text["content"] == "guide"

        with pytest.raises(ValueError, match="文件过大"):
            await service.read_skill_version_file_raw_async("skill-1", 1, "assets", "logo.png", "u1", max_bytes=4)

        monkeypatch.setattr(skill_service_module, "WORKSPACE_OFFICE_SOURCE_MAX_BYTES", 3)
        with pytest.raises(ValueError, match="Office 文件"):
            await service.render_skill_office_preview_async("skill-1", 1, "references", "report.docx", "u1")

    asyncio.run(run_checks())
    service = skill_service_module.SkillService()
    system_dir = tmp_path / "system"
    system_dir.mkdir()
    (system_dir / "SKILL.md").write_text("---\nname: system-skill\n---\n# System", encoding="utf-8")

    class StoreRaises:
        async def get_skill_async(self, skill_id, user_id=""):
            raise RuntimeError("table missing")

    async def system_metadata(name):
        return {"path": str(system_dir / "SKILL.md")} if name == "system-skill" else None

    async def run_checks():
        monkeypatch.setattr(service, "_dao", StoreRaises())
        monkeypatch.setattr(service, "_get_system_skill_metadata_async", system_metadata)
        assert await service._get_readable_version_dir_async("system-skill", 1, "u1") == system_dir
        with pytest.raises(PermissionError, match="无权管理"):
            await service._get_editable_version_dir_async("system-skill", 1, "u1")
        assert await service._get_system_version_dir_async("system-skill", 2) is None

    asyncio.run(run_checks())


def test_archive_user_skill_async_renames_root_and_releases_name(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    service = skill_service_module.SkillService()
    root_dir = tmp_path / "demo-skill"
    version_dir = root_dir / "versions" / "v1"
    version_dir.mkdir(parents=True)
    deleted = {}

    class FakeStore:
        async def delete_skill_async(self, skill_id, archive_name, update_by="", update_by_name=""):
            deleted.update({"skill_id": skill_id, "archive_name": archive_name, "update_by": update_by})

    monkeypatch.setattr(service, "_dao", FakeStore())
    monkeypatch.setattr(skill_service_module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "abcdef123456"})())

    asyncio.run(service._archive_user_skill_async({"skill_id": "skill-1", "skill_name": "demo-skill"}, "u1"))

    assert not root_dir.exists()
    assert (tmp_path / "demo-skill--deleted-abcdef12").exists()
    assert deleted["archive_name"] == "demo-skill--deleted-abcdef12"


def test_skill_service_additional_helper_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)

    target_dir = tmp_path / "target"
    skill_service_module._copy_version_dir(tmp_path / "missing-source", target_dir)
    assert (target_dir / "references").is_dir()
    assert (target_dir / "assets").is_dir()
    assert not (target_dir / "scripts").is_dir()

    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    cleanup_dir = tmp_path / "cleanup-skill"
    cleanup_dir.mkdir()
    service.cleanup_user_skill_dir("cleanup-skill")
    assert not cleanup_dir.exists()

    async def run_async_check():
        # 覆盖「循环耗尽」分支，需要一个在正则下**仍非法**的源名：下划线不在允许字符集内，
        # 702 次候选名（`sales_v2-copy`、`sales_v2-copy-a` …）会全部被拒。
        # 早前这里传纯数字 "1"；放宽后数字已合法，那个输入不再触发该分支。
        with pytest.raises(ValueError, match="无法生成可用的复制技能名称"):
            await service._next_copy_skill_name_async("sales_v2", "u1")

    asyncio.run(run_async_check())


def test_list_skills_async_filter_skip_branches(monkeypatch):
    service = skill_service_module.SkillService()
    rows = [
        {"skill_id": "u1-skill", "skill_name": "private-skill", "skill_title": "私有技能", "skill_desc": "描述", "skill_category": "custom", "skill_source": "user", "owner_user_id": "u1", "current_skill_version_no": 1, "favorite_flag": 0},
        {"skill_id": "sys", "skill_name": "duplicate-skill", "skill_title": "数据库内置", "skill_category": "system", "skill_source": "system", "current_skill_version_no": 1, "favorite_flag": 0},
    ]

    async def fake_safe_db_skills_async(user_id="", include_disabled=False):
        return rows

    monkeypatch.setattr(service, "_safe_db_skills_async", fake_safe_db_skills_async)
    monkeypatch.setattr(skill_service_module, "load_metadata", lambda: {
        "duplicate-skill": {"name": "duplicate-skill", "title": "重复内置", "category": "system"},
        "metadata-only": {"name": "metadata-only", "title": "元数据技能", "category": "system"},
    })

    assert asyncio.run(service.list_skills_async(scope="favorites", user_id="u1")) == []
    assert [item["name"] for item in asyncio.run(service.list_skills_async(scope="system", user_id="u1"))] == ["duplicate-skill", "metadata-only"]
    assert asyncio.run(service.list_skills_async(category="missing", user_id="u1")) == []
    assert asyncio.run(service.list_skills_async(search="不存在", user_id="u1")) == []


def test_get_skill_async_and_system_metadata_async_branches(monkeypatch):
    service = skill_service_module.SkillService()

    async def db_detail(*_args, **_kwargs):
        return {"name": "db-skill", "content": "# DB"}

    monkeypatch.setattr(service, "_get_db_skill_detail_async", db_detail)
    assert asyncio.run(service.get_skill_async("db-skill"))["content"] == "# DB"

    calls = {"activate": 0, "clear": 0, "load": 0}

    async def no_db_detail(*_args, **_kwargs):
        return None

    def fake_activate(name):
        calls["activate"] += 1
        return None if calls["activate"] == 1 else {"name": name, "title": "异步技能", "content": "# 异步技能"}

    def fake_load_metadata():
        calls["load"] += 1
        return {"async-skill": {"name": "async-skill", "path": ""}} if calls["load"] >= 2 else {}

    monkeypatch.setattr(service, "_get_db_skill_detail_async", no_db_detail)
    monkeypatch.setattr(skill_service_module, "activate_skill", fake_activate)
    monkeypatch.setattr(skill_service_module, "load_metadata", fake_load_metadata)
    monkeypatch.setattr(skill_service_module, "clear_metadata_cache", lambda: calls.__setitem__("clear", calls["clear"] + 1))

    skill = asyncio.run(service.get_skill_async("async-skill"))
    assert skill["name"] == "async-skill"
    assert calls["activate"] == 2
    assert calls["clear"] == 1

    async def db_meta(*_args, **_kwargs):
        return {"name": "db-meta"}

    monkeypatch.setattr(service, "_get_db_skill_metadata_async", db_meta)
    assert asyncio.run(service.resolve_skill_path_async("db-meta")) == {"name": "db-meta"}

    async def no_db_meta(*_args, **_kwargs):
        return None

    calls["load"] = 0
    monkeypatch.setattr(service, "_get_db_skill_metadata_async", no_db_meta)
    assert asyncio.run(service.resolve_skill_path_async("async-skill")) == {
        "name": "async-skill",
        "path": "",
        "source": "system",
    }

    monkeypatch.setattr(skill_service_module, "activate_skill", lambda name: None)
    monkeypatch.setattr(skill_service_module, "load_metadata", lambda: {})
    assert asyncio.run(service.get_skill_async("missing-skill")) is None
    assert asyncio.run(service._get_system_skill_metadata_async("missing-skill")) is None


def test_create_user_skill_async_conflict_and_visibility_normalization(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    created = {}

    class ConflictStore:
        async def get_skill_async(self, name, user_id=""):
            return {"skill_id": "exists", "skill_source": "user", "owner_user_id": "u1", "skill_publish_status": "published"}

    async def run_conflict():
        monkeypatch.setattr(service, "_dao", ConflictStore())
        with pytest.raises(ValueError, match="技能标识已存在"):
            await service.create_user_skill_async({"skill_name": "demo-skill", "skill_title": "示例", "content": "# 示例"}, "u1")

    asyncio.run(run_conflict())

    class CreateStore:
        async def get_skill_async(self, name, user_id=""):
            return None

        async def skill_name_exists_async(self, skill_name):
            return False

        async def create_skill_with_version_async(self, skill, version):
            created.update(skill)

    async def fake_get_skill(*_args, **_kwargs):
        return {"skill_id": "new-id", "name": "demo-skill"}

    async def run_create():
        monkeypatch.setattr(service, "_dao", CreateStore())
        monkeypatch.setattr(skill_service_module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "new-id"})())
        monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
        monkeypatch.setattr(service, "get_skill_async", fake_get_skill)
        await service.create_user_skill_async({"skill_name": "demo-skill", "skill_title": "示例", "visibility_type": "bad", "content": "# 示例"}, "u1")

    asyncio.run(run_create())

    assert created["visibility_type"] == "private"


def test_create_user_skill_async_rejects_globally_occupied_name(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()

    class OccupiedStore:
        async def get_skill_async(self, name, user_id=""):
            # 他人私有或已删除技能对当前用户不可见
            return None

        async def skill_name_exists_async(self, skill_name):
            return True

    async def run_create():
        monkeypatch.setattr(service, "_dao", OccupiedStore())
        with pytest.raises(ValueError, match="技能标识已存在"):
            await service.create_user_skill_async({"skill_name": "demo-skill", "skill_title": "示例", "content": "# 示例"}, "u1")

    asyncio.run(run_create())


def test_create_user_skill_async_converts_integrity_error(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()

    class RaceStore:
        async def get_skill_async(self, name, user_id=""):
            return None

        async def skill_name_exists_async(self, skill_name):
            return False

        async def create_skill_with_version_async(self, skill, version):
            raise IntegrityError("INSERT INTO chat_data_skill ...", {}, Exception("Duplicate entry"))

    async def fake_get_skill(*_args, **_kwargs):
        return {"skill_id": "new-id", "name": "demo-skill"}

    async def run_create():
        monkeypatch.setattr(service, "_dao", RaceStore())
        monkeypatch.setattr(skill_service_module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "new-id"})())
        monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
        monkeypatch.setattr(service, "get_skill_async", fake_get_skill)
        with pytest.raises(ValueError, match="技能标识已存在"):
            await service.create_user_skill_async({"skill_name": "demo-skill", "skill_title": "示例", "content": "# 示例"}, "u1")

    asyncio.run(run_create())


def test_copy_user_skill_async_missing_system_and_invalid_version(monkeypatch, tmp_path):
    service = skill_service_module.SkillService()

    class MissingStore:
        async def get_skill_async(self, name, user_id=""):
            return None

    async def run_missing():
        monkeypatch.setattr(service, "_dao", MissingStore())
        with pytest.raises(ValueError, match="技能不存在"):
            await service.copy_user_skill_async("missing", "u1")

    asyncio.run(run_missing())

    class SystemStore:
        async def get_skill_async(self, name, user_id=""):
            return {"skill_id": "sys", "skill_source": "system"}

    async def run_system():
        monkeypatch.setattr(service, "_dao", SystemStore())
        with pytest.raises(PermissionError, match="系统技能不支持复制"):
            await service.copy_user_skill_async("sys", "u1")

    asyncio.run(run_system())

    class UserStore:
        async def get_skill_async(self, name, user_id=""):
            return {"skill_id": "skill-1", "skill_name": "demo-skill", "skill_source": "user", "owner_user_id": "u1", "current_skill_version_no": 1}

    async def fake_source_version(*_args, **_kwargs):
        return {"skill_version_dir": str(tmp_path / "missing-version")}

    async def run_invalid_version():
        monkeypatch.setattr(service, "_dao", UserStore())
        monkeypatch.setattr(service, "_get_copy_source_version_async", fake_source_version)
        with pytest.raises(ValueError, match="技能版本目录无效"):
            await service.copy_user_skill_async("skill-1", "u1")

    asyncio.run(run_invalid_version())


def test_update_user_skill_draft_async_content_and_error_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    # 草稿目录按 owner_user_id + skill_name + version_no 推导。
    draft_dir = tmp_path / "u1" / "demo-skill" / "versions" / "v2"
    updated = {}
    skill = {
        "skill_id": "skill-1",
        "skill_name": "demo-skill",
        "skill_title": "旧标题",
        "skill_desc": "旧描述",
        "skill_category": "custom",
        "visibility_type": "global",
        "skill_source": "user",
        "owner_user_id": "u1",
        "current_skill_version_no": 0,
    }

    class FakeStore:
        def __init__(self, source_skill):
            self.source_skill = source_skill

        async def get_skill_async(self, skill_id, user_id=""):
            return self.source_skill

        async def update_skill_async(self, skill_id, values, update_by="", update_by_name=""):
            updated.update(values)

    async def fake_draft(*_args, **_kwargs):
        return {"skill_version_no": 2, "skill_version_dir": str(draft_dir)}

    async def fake_get_skill(*_args, **_kwargs):
        return {"skill_id": "skill-1", "name": "demo-skill", "draft_version_no": 2}

    async def run_checks():
        monkeypatch.setattr(service, "_dao", FakeStore(skill))
        monkeypatch.setattr(service, "_ensure_user_skill_draft_version_async", fake_draft)
        monkeypatch.setattr(service, "get_skill_async", fake_get_skill)
        result = await service.update_user_skill_draft_async(
            "skill-1",
            {"skill_title": "新标题", "skill_desc": None, "skill_category": None, "visibility_type": "bad", "content": "# 新内容"},
            "u1",
        )
        assert result["draft_version_no"] == 2
        assert (draft_dir / "SKILL.md").exists()
        assert updated["visibility_type"] == "private"
        assert updated["skill_publish_status"] == "draft"

        monkeypatch.setattr(service, "_dao", FakeStore(None))
        with pytest.raises(ValueError, match="技能不存在"):
            await service.update_user_skill_draft_async("missing", {"skill_title": "新标题"}, "u1")

        monkeypatch.setattr(service, "_dao", FakeStore({**skill, "skill_source": "system"}))
        with pytest.raises(PermissionError, match="无权编辑"):
            await service.update_user_skill_draft_async("skill-1", {"skill_title": "新标题"}, "u1")

    asyncio.run(run_checks())


def test_async_lifecycle_rejects_missing_wrong_owner_and_invalid_versions(monkeypatch):
    service = skill_service_module.SkillService()

    class FakeStore:
        def __init__(self, skill, draft=None, version=None):
            self.skill = skill
            self.draft = draft
            self.version = version

        async def get_skill_async(self, skill_id, user_id=""):
            return self.skill

        async def get_latest_draft_version_async(self, skill_id):
            return self.draft

        async def get_version_async(self, skill_id, version_no):
            return self.version

    async def run_checks():
        for method_name, args, expected in [
            ("publish_user_skill_async", ("skill-1", "u1"), "技能不存在"),
            ("list_skill_versions_async", ("skill-1", "u1"), "技能不存在"),
            ("get_skill_version_detail_async", ("skill-1", 1, "u1"), "技能不存在"),
            ("rollback_user_skill_async", ("skill-1", 1, "u1"), "技能不存在"),
            ("favorite_skill_async", ("skill-1", "u1", True), "技能不存在"),
            ("delete_user_skill_async", ("skill-1", "u1"), "技能不存在"),
        ]:
            monkeypatch.setattr(service, "_dao", FakeStore(None))
            with pytest.raises(ValueError, match=expected):
                await getattr(service, method_name)(*args)

        wrong_owner = {"skill_id": "skill-1", "skill_source": "user", "owner_user_id": "u2", "current_skill_version_no": 1}
        for method_name, args in [
            ("publish_user_skill_async", ("skill-1", "u1")),
            ("list_skill_versions_async", ("skill-1", "u1")),
            ("get_skill_version_detail_async", ("skill-1", 1, "u1")),
            ("rollback_user_skill_async", ("skill-1", 1, "u1")),
            ("delete_user_skill_async", ("skill-1", "u1")),
        ]:
            monkeypatch.setattr(service, "_dao", FakeStore(wrong_owner))
            with pytest.raises(PermissionError, match="无权"):
                await getattr(service, method_name)(*args)

        skill = {"skill_id": "skill-1", "skill_source": "user", "owner_user_id": "u1", "current_skill_version_no": 1}
        monkeypatch.setattr(service, "_dao", FakeStore(skill, draft=None, version=None))
        with pytest.raises(ValueError, match="没有可发布的草稿版本"):
            await service.publish_user_skill_async("skill-1", "u1")
        with pytest.raises(ValueError, match="技能版本不存在"):
            await service.get_skill_version_detail_async("skill-1", 1, "u1")
        with pytest.raises(ValueError, match="技能版本不存在"):
            await service.rollback_user_skill_async("skill-1", 1, "u1")

        monkeypatch.setattr(service, "_dao", FakeStore(skill, version={"version_publish_status": "draft"}))
        with pytest.raises(ValueError, match="只有已发布版本"):
            await service.rollback_user_skill_async("skill-1", 1, "u1")

    asyncio.run(run_checks())


def test_file_save_delete_async_permission_and_dao_exception_branches(monkeypatch):
    service = skill_service_module.SkillService()

    class SystemStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {"skill_id": "sys", "skill_source": "system", "owner_user_id": ""}

    async def system_metadata(skill_id):
        return {"path": "/tmp/SKILL.md"} if skill_id == "system" else None

    async def run_checks():
        monkeypatch.setattr(service, "_dao", SystemStore())
        with pytest.raises(PermissionError, match="无权管理"):
            await service.save_skill_version_file_async("sys", 1, "assets", "a.txt", "", "u1")
        with pytest.raises(PermissionError, match="无权管理"):
            await service.delete_skill_version_file_async("sys", 1, "assets", "a.txt", "u1")

        class RaisingStore:
            async def get_skill_async(self, skill_id, user_id=""):
                raise RuntimeError("db down")

        monkeypatch.setattr(service, "_dao", RaisingStore())
        monkeypatch.setattr(service, "_get_system_skill_metadata_async", system_metadata)
        with pytest.raises(PermissionError, match="无权管理"):
            await service.save_skill_version_file_async("system", 1, "assets", "a.txt", "", "u1")
        with pytest.raises(PermissionError, match="无权管理"):
            await service.delete_skill_version_file_async("system", 1, "assets", "a.txt", "u1")
        with pytest.raises(RuntimeError, match="db down"):
            await service.save_skill_version_file_async("user", 1, "assets", "a.txt", "", "u1")
        with pytest.raises(RuntimeError, match="db down"):
            await service.delete_skill_version_file_async("user", 1, "assets", "a.txt", "u1")

    asyncio.run(run_checks())


def test_db_skill_metadata_async_filter_and_missing_path_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()

    class Store:
        def __init__(self, row):
            self.row = row

        async def get_skill_async(self, name, user_id=""):
            return self.row

        async def get_published_version_async(self, skill_id, version_no):
            return None

        async def get_latest_published_version_async(self, skill_id):
            return None

    async def run_checks():
        draft_row = {"skill_id": "skill-1", "skill_name": "demo-skill", "skill_source": "user", "skill_publish_status": "draft", "current_skill_version_no": 1}
        monkeypatch.setattr(service, "_dao", Store(draft_row))
        assert await service._get_db_skill_metadata_async("demo-skill", include_draft=False) is None

        no_current_row = {"skill_id": "skill-1", "skill_name": "demo-skill", "skill_source": "user", "skill_publish_status": "published", "current_skill_version_no": 0}
        monkeypatch.setattr(service, "_dao", Store(no_current_row))
        assert await service._get_db_skill_metadata_async("demo-skill", include_draft=False) is None

        monkeypatch.setattr(service, "_dao", Store({**no_current_row, "current_skill_version_no": 1}))
        assert await service._get_db_skill_metadata_async("demo-skill", include_draft=False) is None
        assert await service._get_db_skill_detail_async("demo-skill", include_draft=False) is None

    asyncio.run(run_checks())


def test_editable_version_dir_and_draft_creation_async_edge_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()

    class Store:
        def __init__(self, skill=None, version=None):
            self.skill = skill
            self.version = version
            self.inserted = None

        async def get_skill_async(self, skill_id, user_id=""):
            return self.skill

        async def get_version_async(self, skill_id, version_no):
            return self.version

        async def get_latest_draft_version_async(self, skill_id):
            return None

        async def next_version_no_async(self, skill_id):
            return 2

        async def get_latest_published_version_async(self, skill_id):
            return None

        async def insert_version_async(self, version):
            self.inserted = version

    async def run_checks():
        for dao, error in [
            (Store(None), "技能不存在"),
            (Store({"skill_id": "skill-1", "skill_source": "user", "owner_user_id": "u2"}), "无权管理"),
            (Store({"skill_id": "skill-1", "skill_source": "user", "owner_user_id": "u1"}, None), "技能版本不存在"),
        ]:
            monkeypatch.setattr(service, "_dao", dao)
            with pytest.raises((ValueError, PermissionError), match=error):
                await service._get_editable_version_dir_async("skill-1", 1, "u1")

        dao = Store({"skill_id": "skill-1", "skill_name": "demo-skill", "skill_source": "user", "owner_user_id": "u1", "current_skill_version_no": 0})
        monkeypatch.setattr(service, "_dao", dao)
        monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
        draft = await service._ensure_user_skill_draft_version_async(dao.skill, "u1")
        assert draft["skill_version_no"] == 2
        assert (tmp_path / "u1" / "demo-skill" / "versions" / "v2" / "references").is_dir()
        assert dao.inserted["skill_version_no"] == 2

    asyncio.run(run_checks())


def test_skill_service_remaining_metadata_and_version_dir_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)

    class EmptyStore:
        async def get_skill_async(self, name, user_id=""):
            return None

    class DraftFallbackStore:
        async def get_skill_async(self, name, user_id=""):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_title": "Demo",
                "skill_source": "user",
                "skill_publish_status": "published",
                "current_skill_version_no": 1,
                "owner_user_id": "u1",
            }

        async def get_latest_draft_version_async(self, skill_id):
            return None

        async def get_published_version_async(self, skill_id, version_no):
            return None

        async def get_latest_published_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 1, "skill_version_dir": str(published_dir)}

    published_dir = (tmp_path / "u1" / "demo-skill" / "versions" / "v1").resolve()
    published_dir.mkdir(parents=True)
    (published_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# Demo", encoding="utf-8")

    async def run_checks():
        monkeypatch.setattr(service, "_dao", EmptyStore())
        assert await service._get_db_skill_metadata_async("missing") is None

        monkeypatch.setattr(service, "_dao", DraftFallbackStore())
        metadata = await service._get_db_skill_metadata_async("demo-skill", include_draft=True, user_id="u1")
        assert metadata["path"] == str(published_dir / "SKILL.md")

        system_version_dir = tmp_path / "system-version"
        system_version_dir.mkdir()
        (system_version_dir / "SKILL.md").write_text("---\nname: sys\n---\n# Sys", encoding="utf-8")

        async def system_metadata(name):
            return {"path": str(system_version_dir / "SKILL.md")} if name == "sys" else None

        monkeypatch.setattr(service, "_get_system_skill_metadata_async", system_metadata)
        assert await service._get_system_version_dir_async("sys", 1) == system_version_dir
        assert await service._get_system_version_dir_async("sys", 2) is None
        assert await service._get_system_version_dir_async("missing", 1) is None

    asyncio.run(run_checks())


def test_skill_service_remaining_file_and_archive_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()
    draft_dir = tmp_path / "skill" / "versions" / "v2"
    (draft_dir / "assets").mkdir(parents=True)

    class DraftStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_source": "user",
                "owner_user_id": "u1",
            }

        async def get_latest_draft_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 2, "skill_version_dir": str(draft_dir)}

    async def run_delete_missing():
        monkeypatch.setattr(service, "_dao", DraftStore())
        result = await service.delete_skill_version_file_async("skill-1", 1, "assets", "missing.txt", "u1")
        assert result["deleted"] is True
        assert result["file_path"] == "assets/missing.txt"

    asyncio.run(run_delete_missing())

    root_dir = tmp_path / "u1" / "demo-skill"
    root_dir.mkdir(parents=True, exist_ok=True)
    deleted = {}

    class ArchiveStore:
        async def delete_skill_async(self, skill_id, archive_name, update_by="", update_by_name=""):
            deleted["archive_name"] = archive_name

    monkeypatch.setattr(service, "_dao", ArchiveStore())
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    monkeypatch.setattr(skill_service_module.uuid, "uuid4", lambda: type("Uuid", (), {"hex": "abcdefff0000"})())
    asyncio.run(service._archive_user_skill_async({"skill_id": "skill-1", "skill_name": "demo-skill", "owner_user_id": "u1"}, "u1"))

    assert not root_dir.exists()
    assert (tmp_path / "u1" / "demo-skill--deleted-abcdefff").exists()
    assert deleted["archive_name"] == "demo-skill--deleted-abcdefff"


def test_skill_service_extra_private_helper_branches(tmp_path, monkeypatch):
    service = skill_service_module.SkillService()

    class DetailStore:
        async def get_skill_async(self, *_args, **_kwargs):
            return {
                "skill_id": "skill-1",
                "skill_name": "demo-skill",
                "skill_title": "Demo",
                "skill_source": "user",
                "skill_publish_status": "published",
                "current_skill_version_no": 1,
            }

        async def get_published_version_async(self, *_args, **_kwargs):
            return {"skill_version_dir": str(tmp_path / "missing")}

        async def get_latest_published_version_async(self, *_args, **_kwargs):
            return None

        async def get_latest_draft_version_async(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(service, "_dao", DetailStore())
    assert asyncio.run(service._get_db_skill_detail_async("demo-skill")) is None

    class BuiltinStoreError:
        async def get_skill_async(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    async def no_system_skill(_skill_id):
        return None

    monkeypatch.setattr(service, "_dao", BuiltinStoreError())
    monkeypatch.setattr(service, "_get_system_skill_metadata_async", no_system_skill)
    with pytest.raises(RuntimeError, match="db down"):
        asyncio.run(service._get_editable_version_dir_async("skill-1", 1, "u1"))

    class CurrentVersionStore:
        async def get_latest_draft_version_async(self, *_args, **_kwargs):
            return None

        async def get_published_version_async(self, *_args, **_kwargs):
            return {"skill_version_no": 3}

        async def get_latest_published_version_async(self, *_args, **_kwargs):
            raise AssertionError("current published version should be used")

    monkeypatch.setattr(service, "_dao", CurrentVersionStore())
    assert asyncio.run(service._get_copy_source_version_async(
        {"skill_id": "skill-1", "owner_user_id": "u2", "current_skill_version_no": 3},
        "u1",
    )) == {"skill_version_no": 3}

    class UserReadableStore:
        async def get_skill_async(self, *_args, **_kwargs):
            return {"skill_id": "skill-1", "skill_source": "user", "owner_user_id": "u1"}

    monkeypatch.setattr(service, "_dao", UserReadableStore())
    monkeypatch.setattr(service, "_get_editable_version_dir_async", lambda *args, **kwargs: asyncio.sleep(0, result=tmp_path))
    assert asyncio.run(service._get_readable_version_dir_async("skill-1", 1, "u1")) == tmp_path

    system_root = tmp_path / "system-root"
    system_root.mkdir()
    (system_root / "SKILL.md").write_text("---\nname: sys\n---\n# Sys", encoding="utf-8")

    async def system_metadata(skill_id):
        return {"path": str(system_root / "SKILL.md")} if skill_id == "sys" else None

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", system_metadata)
    assert asyncio.run(service._get_system_version_dir_async("sys", 1)) == system_root

    assert service._resolve_file_under_version_dir(tmp_path, "references", "guide").name == "guide.md"
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "references" / "link.md"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="文件路径越界"):
        service._resolve_file_under_version_dir(tmp_path, "references", "link.md")

    cleanup_missing = tmp_path / "missing-cleanup"
    monkeypatch.setattr(skill_service_module, "USER_SKILL_ROOT", tmp_path)
    service.cleanup_user_skill_dir(cleanup_missing.name)


def test_list_runtime_skill_sources_async_includes_system_skills(monkeypatch, tmp_path):
    # 回归测试：list_runtime_skill_sources_async 必须在运行期真实可调用。
    # 曾因 skill_service 漏导入 SYSTEM_SKILL_CATEGORY，在运行期抛 NameError，
    # 导致整个 runtime-skills 快照构建失败（handlers 的 try/except 将其吞掉，
    # 最终 runtime-skills 目录为空）。本测试直接走真实函数体，覆盖该引用。
    system_root = tmp_path / "system"
    skill_dir = system_root / "demo-sys"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-sys\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        skill_service_module,
        "load_metadata",
        lambda: {"demo-sys": {"name": "demo-sys", "path": str(skill_dir / "SKILL.md")}},
    )
    monkeypatch.setattr(
        skill_service_module,
        "physical_skill_category_root",
        lambda category, chat_data_home=None: system_root if category == "system" else tmp_path,
    )

    # 无用户技能（DB 不可用），仅验证系统技能纳入快照来源集合。
    async def fake_list_skills(*_args, **_kwargs):
        return None

    monkeypatch.setattr(skill_service_module.skill_service.dao, "list_skills_async", fake_list_skills)

    async def run():
        return await skill_service_module.skill_service.list_runtime_skill_sources_async("u1")

    sources = asyncio.run(run())
    names = [name for name, _ in sources]
    assert "demo-sys" in names
    # system 类别来源不依赖 DB，单条异常不应中断整体
    assert all(isinstance(p, Path) and p.is_dir() for _, p in sources)
