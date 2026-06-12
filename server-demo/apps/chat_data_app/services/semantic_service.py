"""
语义层服务封装

为 chat_data_app 提供语义层调用接口。
"""

from typing import Dict, Optional, Any, List
import logging

from ..semantic import (
    SemanticEngine,
    SemanticConfig,
    SemanticQuery,
    QueryResult,
    create_engine_from_dict,
)

logger = logging.getLogger(__name__)


class ChatDataSemanticService:
    """
    chat_data_app 的语义层服务封装

    提供从数据源配置创建语义引擎，并处理查询请求。

    Example:
        service = ChatDataSemanticService(datasource_config)
        result = service.process_query("按产品类别统计销售额")
        print(result['sql'])
    """

    def __init__(
        self,
        datasource: Dict[str, Any],
        llm: Optional[Any] = None
    ):
        """
        初始化服务

        Args:
            datasource: 数据源配置字典
            llm: LLM 实例（可选）
        """
        self.datasource = datasource
        self._llm = llm
        self._engine: Optional[SemanticEngine] = None
        self._config: Optional[SemanticConfig] = None

        # 尝试初始化引擎
        self._init_engine()

    def _init_engine(self) -> None:
        """初始化语义引擎"""
        semantic_configs = self.datasource.get('semantic_configs', [])

        # 过滤启用的配置
        enabled_configs = [
            c for c in semantic_configs
            if c.get('enabled', True)
        ]

        if not enabled_configs:
            logger.info("未找到启用的语义配置")
            return

        # 使用第一个启用的配置（后续可扩展为 LLM 选择）
        config_dict = enabled_configs[0]

        # 确定 SQL 方言
        source_type = self.datasource.get('source_type', 'mysql')
        dialect = self._map_source_type_to_dialect(source_type)

        try:
            self._config = SemanticConfig.model_validate(config_dict)
            self._engine = SemanticEngine(
                self._config,
                dialect=dialect,
                llm=self._llm
            )
            logger.info(
                f"语义引擎初始化成功: {self._config.name}, "
                f"方言: {dialect}"
            )
        except Exception as e:
            logger.error(f"语义引擎初始化失败: {e}", exc_info=True)

    def _map_source_type_to_dialect(self, source_type: str) -> str:
        """将数据源类型映射到 SQL 方言"""
        mapping = {
            'mysql': 'mysql',
            'mariadb': 'mysql',
            'hive': 'hive',
            'spark': 'hive',
            'presto': 'hive',
            'postgresql': 'postgresql',
            'postgres': 'postgresql',
            'pg': 'postgresql',
        }
        return mapping.get(source_type.lower(), 'mysql')

    def set_llm(self, llm: Any) -> None:
        """设置 LLM 实例"""
        self._llm = llm
        if self._engine:
            self._engine.set_llm(llm)

    def is_available(self) -> bool:
        """检查语义层是否可用"""
        return self._engine is not None

    def process_query(self, query: str) -> Dict[str, Any]:
        """
        处理自然语言查询

        Args:
            query: 自然语言查询

        Returns:
            查询结果字典
        """
        if not self._engine:
            return {
                'success': False,
                'error': '语义层未配置或初始化失败'
            }

        try:
            result = self._engine.query(query)
            return result.to_dict()
        except Exception as e:
            logger.error(f"查询处理失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def execute_dsl(self, dsl: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行 DSL 查询

        Args:
            dsl: DSL 字典

        Returns:
            查询结果字典
        """
        if not self._engine:
            return {
                'success': False,
                'error': '语义层未配置或初始化失败'
            }

        try:
            result = self._engine.execute_dsl_dict(dsl)
            return result.to_dict()
        except Exception as e:
            logger.error(f"DSL 执行失败: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e)
            }

    def get_available_metrics(self) -> List[str]:
        """获取可用的指标列表"""
        if not self._engine:
            return []
        return self._engine.get_available_metrics()

    def get_available_dimensions(self) -> List[str]:
        """获取可用的维度列表"""
        if not self._engine:
            return []
        return self._engine.get_available_dimensions()

    def get_schema_summary(self) -> Optional[Dict]:
        """获取 Schema 摘要"""
        if not self._engine:
            return None
        return self._engine.get_schema_summary()

    def suggest(self, query: str) -> Dict:
        """
        获取查询建议

        Args:
            query: 查询文本

        Returns:
            建议信息
        """
        if not self._engine:
            return {'metrics': [], 'dimensions': []}
        return self._engine.suggest(query)


def create_semantic_service(
    datasource: Dict[str, Any],
    llm: Optional[Any] = None
) -> ChatDataSemanticService:
    """
    创建语义服务（工厂函数）

    Args:
        datasource: 数据源配置
        llm: LLM 实例

    Returns:
        语义服务实例
    """
    return ChatDataSemanticService(datasource, llm)


# 兼容旧 API 的包装函数
def process_semantic_query(
    datasource: Dict[str, Any],
    query: str,
    llm: Optional[Any] = None
) -> Dict[str, Any]:
    """
    处理语义查询（兼容函数）

    Args:
        datasource: 数据源配置
        query: 自然语言查询
        llm: LLM 实例

    Returns:
        查询结果
    """
    service = ChatDataSemanticService(datasource, llm)
    return service.process_query(query)
