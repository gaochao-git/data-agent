"""直连(direct)执行对象机构白名单,来自环境配置。

直连类型执行对象不在 HTTP 权限服务的管辖域内,命中本白名单 org_code 的
执行对象默认放行、不再查 HTTP 权限。白名单通过环境变量
CHAT_DATA_DIRECT_ORG_CODES 配置(逗号分隔):本地开发连不上权限服务时,
在 conf/.env-dev 配置占位 org code;生产环境按需配置真实机构,留空表示
直连执行对象对所有用户不可见。代码中不保留任何默认白名单。
"""

import os

DIRECT_ORG_CODES_ENV = "CHAT_DATA_DIRECT_ORG_CODES"


def get_direct_org_codes() -> set[str]:
    """读取直连执行对象机构白名单;未配置时返回空集。"""
    raw = os.getenv(DIRECT_ORG_CODES_ENV, "")
    return {item.strip() for item in raw.replace("，", ",").split(",") if item.strip()}
