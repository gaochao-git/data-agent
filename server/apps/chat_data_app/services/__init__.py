"""
SQL Generator 服务层
"""

try:
    from .datasource.manager import DataSourceManager, get_datasource_manager
except Exception:  # pragma: no cover - 测试环境允许按需跳过运行期依赖
    get_datasource_manager = None
    DataSourceManager = None

__all__ = [
    "get_datasource_manager",
    "DataSourceManager",
]
