"""写路径适配器：与查询执行层分离，避免业务服务直连数据源。"""

from .hive import HiveWriteAdapter, get_write_adapter

__all__ = ["HiveWriteAdapter", "get_write_adapter"]
