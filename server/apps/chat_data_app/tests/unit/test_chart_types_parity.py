"""图表类型注册表前后端一致性护栏。

chart 类型在五处声明：后端 ``chart_types.py``、前端 ``chartTypes.ts``、前端
``CHART_TYPE_HELP_TEXT``、后端 ``get_chart_type_prompt_text``（提示词物料）、
dashboard-generation 技能文档（SKILL.md 与主指南）。任何一处新增/改名图表类型
而其余未同步时，本测试立即失败。新增图表类型的唯一合法流程是五处同步改动。
"""

import re
from pathlib import Path

import pytest

from apps.chat_data_app.chart_types import CHART_TYPE_ITEMS, get_chart_type_prompt_text

REPO_ROOT = Path(__file__).resolve().parents[5]
FRONTEND_CHART_TYPES_PATH = (
    REPO_ROOT / "web-vue" / "src" / "views" / "chat-data" / "chartTypes.ts"
)
# 图表类型清单的文档维护点是主指南（guide.md）；SKILL.md 不重复罗列类型
# （类型清单另有 chart_types.py 注册表与 get_chart_type_prompt_text 两处声明）。
SKILL_DOC_PATHS = [
    REPO_ROOT / "chat_data" / "skills" / "system" / "dashboard-generation" / "references" / "dashboard-generation-guide.md",
]

BACKEND_KEYS = [item["key"] for item in CHART_TYPE_ITEMS]


def _read(path: Path) -> str:
    assert path.exists(), f"被检文件不存在: {path}"
    return path.read_text(encoding="utf-8")


def _parse_frontend_registry(text: str) -> list[dict]:
    """解析 chartTypes.ts 中单行结构的 CHART_TYPES 数组项。"""
    block = re.search(r"export const CHART_TYPES = \[(.*?)\] as const", text, re.S)
    assert block, "chartTypes.ts 缺少 CHART_TYPES 数组"
    items = []
    for entry in re.findall(r"\{([^{}]+)\}", block.group(1)):
        def field(name: str) -> str | bool:
            match = re.search(rf"{name}:\s*'([^']+)'|{name}:\s*(true|false)", entry)
            assert match, f"CHART_TYPES 项缺少字段 {name}: {entry}"
            if match.group(1) is not None:
                return match.group(1)
            return match.group(2) == "true"

        items.append({
            "key": field("key"),
            "label": field("label"),
            "echarts": field("echarts"),
            "supports_dual_axis": field("supportsDualAxis"),
            "supports_series_type": field("supportsSeriesType"),
        })
    return items


def test_frontend_registry_keys_and_labels_match_backend():
    text = _read(FRONTEND_CHART_TYPES_PATH)
    frontend_items = _parse_frontend_registry(text)
    backend_items = [
        {
            "key": item["key"],
            "label": item["label"],
            "echarts": item["echarts"],
            "supports_dual_axis": item["supports_dual_axis"],
            "supports_series_type": item["supports_series_type"],
        }
        for item in CHART_TYPE_ITEMS
    ]
    assert frontend_items == backend_items, (
        "chartTypes.ts 与后端 chart_types.py 注册表不一致（顺序/key/label/能力标记），"
        "新增图表类型必须五处同步：后端注册表、前端注册表、CHART_TYPE_HELP_TEXT、"
        "get_chart_type_prompt_text、dashboard-generation 技能文档"
    )


def test_frontend_help_text_covers_all_keys():
    text = _read(FRONTEND_CHART_TYPES_PATH)
    help_block = re.search(r"CHART_TYPE_HELP_TEXT[^=]*= \{(.*?)\n\}", text, re.S)
    assert help_block, "chartTypes.ts 缺少 CHART_TYPE_HELP_TEXT"
    help_keys = set(re.findall(r"^\s{2}(\w+): \{", help_block.group(1), re.M))
    assert help_keys == set(BACKEND_KEYS), (
        f"CHART_TYPE_HELP_TEXT 与注册表 key 不一致: 缺少 {set(BACKEND_KEYS) - help_keys}, "
        f"多出 {help_keys - set(BACKEND_KEYS)}"
    )


def test_backend_prompt_text_contains_all_types():
    prompt_text = get_chart_type_prompt_text()
    for key in BACKEND_KEYS:
        assert f"`{key}`" in prompt_text, f"get_chart_type_prompt_text 缺少图表类型: {key}"


@pytest.mark.parametrize("doc_path", SKILL_DOC_PATHS)
def test_dashboard_skill_docs_list_all_types(doc_path):
    text = _read(doc_path)
    missing = [key for key in BACKEND_KEYS if f"`{key}`" not in text and key not in text]
    assert not missing, f"{doc_path.name} 缺少图表类型说明: {missing}"


@pytest.mark.parametrize("key", BACKEND_KEYS)
def test_backend_registry_items_are_well_formed(key):
    item = next(item for item in CHART_TYPE_ITEMS if item["key"] == key)
    assert item["label"], "label 不能为空"
    assert item["description"], "description 不能为空"
    assert item["data_shape"], "data_shape 不能为空"
