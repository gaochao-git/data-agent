from __future__ import annotations

import os
from typing import Optional

_CASE_ENV_LOADED = False


def ensure_case_env_loaded() -> None:
    """
    从本地 case_analysis.env 文件加载案例分析专用环境变量。
    - 只加载一次（通过 _CASE_ENV_LOADED 标记）
    - 不覆盖已存在的环境变量
    - 文件格式：KEY=VALUE，# 开头视为注释
    """
    global _CASE_ENV_LOADED
    if _CASE_ENV_LOADED:
        return

    env_path = os.path.join(os.path.dirname(__file__), "case_analysis.env")
    if not os.path.exists(env_path):
        _CASE_ENV_LOADED = True
        return

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                value = value.strip().strip('"').strip("'")
                if not name:
                    continue
                if name not in os.environ and value != "":
                    os.environ[name] = value
    except Exception:
        # 加载失败时不影响主流程，后续仍可依赖系统环境变量
        pass
    finally:
        _CASE_ENV_LOADED = True


def get_case_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    统一入口：先确保加载 case_analysis.env，再读取环境变量。
    """
    ensure_case_env_loaded()
    return os.getenv(name, default)


__all__ = ["ensure_case_env_loaded", "get_case_env"]

