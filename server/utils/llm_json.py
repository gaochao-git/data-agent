"""
LLM JSON 响应解析工具

提供统一的 JSON 解析功能，处理 LLM 返回的各种格式。
"""

import json
import re
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class JSONParseError(Exception):
    """JSON 解析错误"""
    pass


def parse_llm_json(content: str, strict: bool = True) -> Dict[str, Any]:
    """
    解析 LLM 返回的 JSON 内容

    支持以下格式：
    1. 纯 JSON 字符串
    2. ```json 代码块包裹的 JSON
    3. ``` 代码块包裹的 JSON
    4. 包含其他文本的 JSON（提取花括号内容）

    Args:
        content: LLM 返回的原始内容
        strict: 是否严格模式（默认 True，解析失败抛出异常）；显式传
            ``strict=False`` 表示调用方明确接受"失败静默回退空 dict"
            的宽松口径（如后台任务、可选生成类功能）

    Returns:
        解析后的字典

    Raises:
        JSONParseError: 解析失败时抛出（默认行为；仅 strict=False 时改为返回 {}）
    """
    if not content or not content.strip():
        if strict:
            raise JSONParseError("内容为空")
        return {}

    content = content.strip()

    # 方法1: 直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 方法2: 提取 ```json 代码块
    json_block_pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    match = re.search(json_block_pattern, content)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 方法3: 提取花括号内容（贪婪匹配最外层）
    brace_pattern = r'\{[\s\S]*\}'
    match = re.search(brace_pattern, content)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # 方法4: 尝试修复常见问题
    fixed_content = _try_fix_json(content)
    if fixed_content:
        try:
            return json.loads(fixed_content)
        except json.JSONDecodeError:
            pass

    # 解析失败
    error_msg = f"无法解析 JSON: {content[:200]}..."
    logger.warning(error_msg)

    if strict:
        raise JSONParseError(error_msg)

    return {}


def _try_fix_json(content: str) -> Optional[str]:
    """
    尝试修复常见的 JSON 格式问题

    Args:
        content: 原始内容

    Returns:
        修复后的内容，如果无法修复返回 None
    """
    # 提取可能的 JSON 部分
    match = re.search(r'\{[\s\S]*\}', content)
    if not match:
        return None

    json_str = match.group(0)

    # 修复1: 移除尾部逗号
    json_str = re.sub(r',\s*([}\]])', r'\1', json_str)

    # 修复2: 替换单引号为双引号（注意不要替换字符串内的单引号）
    # 这个修复比较危险，只在明显是单引号键值对时使用
    if "'" in json_str and '"' not in json_str:
        json_str = json_str.replace("'", '"')

    # 修复3: 修复未加引号的键
    # 例如: {key: "value"} -> {"key": "value"}
    json_str = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', json_str)

    return json_str
