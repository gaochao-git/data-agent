"""
Artifact Store - 上下文卸载存储

基于 LangGraph 的 content_and_artifact 模式设计，用于将大型数据对象从 LLM 上下文中卸载，
避免消耗过多 Token 并防止上下文污染。

当前实现的策略（参考 LangChain Context Engineering）：
- Write: 将大数据写入外部存储（save_artifact）
- Select: 按需检索相关数据（load_artifact）

待扩展策略：
- Compress: LLM 看到数据摘要而非仅 ID（如字段名、行数、样例数据）
- Isolate: 多代理架构下的上下文隔离

使用方式：
1. 工具执行时调用 save_artifact() 保存大数据，返回 artifact_id
2. 工具通过结构化 JSON 的 meta.artifact_id 返回引用，不把完整大结果送入 LLM 上下文
3. 前端通过 API 调用 load_artifact() 获取完整数据

支持的 Artifact 类型：
- query_result: SQL 查询结果
- chart_config: 图表配置
- 可按需扩展其他类型

参考资料：
- https://blog.langchain.com/context-engineering-for-agents/
- https://forum.langchain.com/t/best-way-to-handle-very-large-tool-outputs-in-langgraph
"""

import os
import json
import uuid
from typing import Optional
from pathlib import Path

from utils.logger import get_logger
from utils.storage_paths import CHAT_DATA_HOME

logger = get_logger(__name__)

# 默认 Artifact 存储根目录。
_DEFAULT_ARTIFACT_DIR = Path(CHAT_DATA_HOME) / "artifacts"


class ArtifactStore:
    """Artifact 存储 - 用于上下文卸载"""

    def __init__(self, base_dir: str | Path = None):
        """
        初始化 Artifact 存储

        Args:
            base_dir: 存储根目录，默认为 CHAT_DATA_HOME/artifacts
        """
        self.base_dir = Path(base_dir) if base_dir else _DEFAULT_ARTIFACT_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_type_dir(self, artifact_type: str) -> Path:
        """获取指定类型的存储目录"""
        type_dir = self.base_dir / artifact_type
        type_dir.mkdir(parents=True, exist_ok=True)
        return type_dir

    def _get_file_path(self, artifact_type: str, artifact_id: str) -> Path:
        """获取 Artifact 文件路径（按类型分目录）"""
        return self._get_type_dir(artifact_type) / f"{artifact_id}.json"

    def _find_file_path(self, artifact_id: str) -> Optional[Path]:
        """
        根据 artifact_id 查找文件路径（不需要知道类型）

        遍历所有类型目录查找，用于简化 API 调用
        """
        for type_dir in self.base_dir.iterdir():
            if type_dir.is_dir():
                file_path = type_dir / f"{artifact_id}.json"
                if file_path.exists():
                    return file_path
        return None

    def save(
        self,
        artifact_type: str,
        data: dict,
        artifact_id: str = None,
        conversation_id: str = None
    ) -> str:
        """
        保存 Artifact

        Args:
            artifact_type: Artifact 类型（如 query_result, chart_config）
            data: 要保存的数据
            artifact_id: 指定 ID（可选，不指定则自动生成）
            conversation_id: 关联的会话 ID（可选，用于按会话组织数据）

        Returns:
            artifact_id
        """
        if not artifact_id:
            artifact_id = str(uuid.uuid4())

        # 包装数据，添加元信息
        wrapped_data = {
            "id": artifact_id,
            "type": artifact_type,
            "conversation_id": conversation_id,
            "data": data
        }

        file_path = self._get_file_path(artifact_type, artifact_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(wrapped_data, f, ensure_ascii=False, indent=2)

        logger.debug(f"Artifact 已保存: type={artifact_type}, id={artifact_id}")
        return artifact_id

    def load(self, artifact_type: str, artifact_id: str) -> Optional[dict]:
        """
        加载 Artifact 数据

        Args:
            artifact_type: Artifact 类型
            artifact_id: Artifact ID

        Returns:
            数据内容，不存在返回 None
        """
        if not artifact_id:
            return None

        file_path = self._get_file_path(artifact_type, artifact_id)
        if not file_path.exists():
            logger.warning(f"Artifact 不存在: type={artifact_type}, id={artifact_id}")
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                wrapped_data = json.load(f)
            return wrapped_data.get("data")
        except Exception as e:
            logger.error(f"加载 Artifact 失败: type={artifact_type}, id={artifact_id}, error={e}")
            return None

    def load_with_meta(self, artifact_type: str, artifact_id: str) -> Optional[dict]:
        """
        加载 Artifact（包含元信息）

        Args:
            artifact_type: Artifact 类型
            artifact_id: Artifact ID

        Returns:
            完整的包装数据（含 id, type, conversation_id, data）
        """
        if not artifact_id:
            return None

        file_path = self._get_file_path(artifact_type, artifact_id)
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载 Artifact 失败: type={artifact_type}, id={artifact_id}, error={e}")
            return None

    def delete(self, artifact_type: str, artifact_id: str) -> bool:
        """
        删除 Artifact

        Args:
            artifact_type: Artifact 类型
            artifact_id: Artifact ID

        Returns:
            是否删除成功
        """
        file_path = self._get_file_path(artifact_type, artifact_id)
        if file_path.exists():
            file_path.unlink()
            logger.debug(f"Artifact 已删除: type={artifact_type}, id={artifact_id}")
            return True
        return False

    def exists(self, artifact_type: str, artifact_id: str) -> bool:
        """检查 Artifact 是否存在"""
        return self._get_file_path(artifact_type, artifact_id).exists()

    def load_by_id(self, artifact_id: str) -> Optional[dict]:
        """
        仅通过 artifact_id 加载数据（不需要知道类型）

        Args:
            artifact_id: Artifact ID

        Returns:
            数据内容，不存在返回 None
        """
        if not artifact_id:
            return None

        file_path = self._find_file_path(artifact_id)
        if not file_path:
            logger.warning(f"Artifact 不存在: id={artifact_id}")
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                wrapped_data = json.load(f)
            return wrapped_data.get("data")
        except Exception as e:
            logger.error(f"加载 Artifact 失败: id={artifact_id}, error={e}")
            return None

    def load_meta_by_id(self, artifact_id: str) -> Optional[dict]:
        """仅通过 artifact_id 加载包装数据（含元信息）。"""
        if not artifact_id:
            return None

        file_path = self._find_file_path(artifact_id)
        if not file_path:
            logger.warning(f"Artifact 不存在: id={artifact_id}")
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载 Artifact 元信息失败: id={artifact_id}, error={e}")
            return None

    def list_ids(self, artifact_type: str) -> list[str]:
        """
        列出指定类型的所有 Artifact ID

        Args:
            artifact_type: Artifact 类型

        Returns:
            Artifact ID 列表
        """
        type_dir = self._get_type_dir(artifact_type)
        return [f.stem for f in type_dir.glob("*.json")]


# 全局单例
_default_store: Optional[ArtifactStore] = None


def get_artifact_store() -> ArtifactStore:
    """获取默认 Artifact 存储实例"""
    global _default_store
    if _default_store is None:
        _default_store = ArtifactStore()
    return _default_store


# ============ 便捷函数 ============

def save_artifact(artifact_type: str, data: dict, artifact_id: str = None, conversation_id: str = None) -> str:
    """保存 Artifact（便捷函数）"""
    return get_artifact_store().save(artifact_type, data, artifact_id, conversation_id)


def load_artifact(artifact_type: str, artifact_id: str) -> Optional[dict]:
    """加载 Artifact（便捷函数，需要指定类型）"""
    return get_artifact_store().load(artifact_type, artifact_id)


def load_artifact_by_id(artifact_id: str) -> Optional[dict]:
    """加载 Artifact（便捷函数，仅通过 ID，自动查找类型）"""
    return get_artifact_store().load_by_id(artifact_id)


def load_artifact_meta_by_id(artifact_id: str) -> Optional[dict]:
    """加载 Artifact 包装数据（含 id/type/conversation_id/data）。"""
    return get_artifact_store().load_meta_by_id(artifact_id)


def delete_artifact(artifact_type: str, artifact_id: str) -> bool:
    """删除 Artifact（便捷函数）"""
    return get_artifact_store().delete(artifact_type, artifact_id)


# ============ Artifact 类型常量 ============

class ArtifactType:
    """Artifact 类型常量"""
    QUERY_RESULT = "query_result"
    CHART_CONFIG = "chart_config"
