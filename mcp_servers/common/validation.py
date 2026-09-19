"""工具参数的安全校验（防命令注入面：路径/过滤词/主机只允许白名单字符）。"""

import re

SAFE_PATH_PATTERN = re.compile(r'^[a-zA-Z0-9_/.-]+$')
SAFE_FILTER_PATTERN = re.compile(r'^[a-zA-Z0-9_.-]+$')


def validate_path(path: str) -> tuple[bool, str]:
    """校验路径参数安全性"""
    if not path:
        return False, "路径不能为空"
    if not SAFE_PATH_PATTERN.match(path):
        return False, "路径只允许字母、数字、下划线、斜杠、点、减号"
    return True, ""


def validate_filter(filter_str: str) -> tuple[bool, str]:
    """校验过滤词参数安全性"""
    if not filter_str:
        return True, ""
    if not SAFE_FILTER_PATTERN.match(filter_str):
        return False, "过滤词只允许字母、数字、下划线、点、减号"
    return True, ""


def validate_host(host: str) -> tuple[bool, str]:
    """校验主机地址参数安全性（IP 或域名）"""
    if not host:
        return False, "主机地址不能为空"
    if not SAFE_FILTER_PATTERN.match(host):
        return False, "主机地址只允许字母、数字、下划线、点、减号"
    return True, ""
