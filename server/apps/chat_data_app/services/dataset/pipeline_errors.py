"""Dataset Pipeline 错误类型。

错误码不新增编号：pipeline 错误是 Dataset 领域错误的子类，沿用统一错误码定义中
已声明的 CHATDATA_DATASET_* 码（invalid → 40076 / not found → 40489 /
build 失败 → 50103），路由层统一映射。
"""

from __future__ import annotations

from .dataset_service import DatasetError, DatasetInvalidError, DatasetNotFoundError


class PipelineError(DatasetError):
    """Pipeline 业务错误基类。"""


class PipelineNotFoundError(DatasetNotFoundError):
    """Pipeline 或 run 不存在。"""


class PipelineInvalidError(DatasetInvalidError):
    """Pipeline 定义/入参不合法。"""


class PipelineBuildError(PipelineError):
    """Pipeline build 失败（源读取、transform 执行或输出提交失败）。"""


__all__ = [
    "PipelineBuildError",
    "PipelineError",
    "PipelineInvalidError",
    "PipelineNotFoundError",
]
