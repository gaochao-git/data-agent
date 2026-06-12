"""
语义配置选择器

使用 LLM 自动选择最合适的语义配置。
"""

from typing import Dict, List, Optional
import json
import logging

logger = logging.getLogger(__name__)


class SemanticConfigSelector:
    """
    语义配置选择器

    当存在多个语义配置时，使用 LLM 自动选择最合适的配置。
    """

    def __init__(self, llm=None):
        """
        初始化配置选择器

        Args:
            llm: LLM 实例（可选，默认自动获取）
        """
        if llm is None:
            from ...services.llm_config import get_chat_data_llm
            self.llm = get_chat_data_llm(temperature=0, max_tokens=300)
        else:
            self.llm = llm

    def select(self, query: str, configs: List[Dict]) -> Optional[Dict]:
        """
        根据用户查询选择最合适的语义配置

        Args:
            query: 用户自然语言查询
            configs: 可用的语义配置列表

        Returns:
            选中的配置，如果没有合适的配置返回 None
        """
        from ...agent.prompts import SEMANTIC_CONFIG_SELECT_PROMPT

        enabled_configs = [c for c in configs if c.get('enabled', True)]

        if not enabled_configs:
            logger.info("没有启用的语义配置")
            return None

        if len(enabled_configs) == 1:
            logger.info(f"只有一个配置，直接选择: {enabled_configs[0].get('id')}")
            return enabled_configs[0]

        try:
            prompt = SEMANTIC_CONFIG_SELECT_PROMPT.format(
                configs_desc=self._build_configs_desc(enabled_configs),
                query=query
            )

            response = self.llm.invoke(prompt)
            content = response.content.strip()

            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()

            result = json.loads(content)
            logger.info(f"LLM 配置选择结果: {result}")

            selected_id = result.get('selected_config_id')
            confidence = result.get('confidence', 0.0)

            if not selected_id or confidence < 0.5:
                logger.info(f"没有匹配的配置或置信度过低: {confidence}")
                return None

            for config in enabled_configs:
                if config.get('id') == selected_id:
                    logger.info(f"选择配置: {selected_id}, 置信度: {confidence}")
                    return config

            return None

        except json.JSONDecodeError as e:
            logger.error(f"LLM 返回的 JSON 解析失败: {e}")
            return enabled_configs[0] if enabled_configs else None
        except Exception as e:
            logger.error(f"配置选择失败: {e}", exc_info=True)
            return enabled_configs[0] if enabled_configs else None

    def _build_configs_desc(self, configs: List[Dict]) -> str:
        """构建配置描述"""
        lines = []
        for config in configs:
            if not config.get('enabled', True):
                continue

            config_id = config.get('id', '')
            name = config.get('name', '')
            description = config.get('description', '')

            metrics = [m.get('name', '') for m in config.get('metrics', [])]
            metrics_str = ', '.join(metrics[:5])
            if len(metrics) > 5:
                metrics_str += f" 等{len(metrics)}个指标"

            dimensions = [d.get('name', '') for d in config.get('dimensions', [])]
            dims_str = ', '.join(dimensions[:5])
            if len(dimensions) > 5:
                dims_str += f" 等{len(dimensions)}个维度"

            lines.append(f"""配置ID: {config_id}
名称: {name}
描述: {description or '无'}
指标: {metrics_str or '无'}
维度: {dims_str or '无'}
""")

        return '\n---\n'.join(lines) if lines else "（暂无可用配置）"

    def select_by_metrics(self, metrics: List[str], configs: List[Dict]) -> Optional[Dict]:
        """
        根据指标名称选择配置（简单匹配，不使用 LLM）

        Args:
            metrics: 需要查询的指标列表
            configs: 可用的语义配置列表

        Returns:
            包含所有指标的配置
        """
        enabled_configs = [c for c in configs if c.get('enabled', True)]

        for config in enabled_configs:
            config_metrics = {m.get('name') for m in config.get('metrics', [])}
            if all(m in config_metrics for m in metrics):
                logger.info(f"根据指标匹配选择配置: {config.get('id')}")
                return config

        return None
