"""
SQL查询元数据生成服务
使用LLM自动生成查询的标题、描述、分类
"""
import json
from .llm_config import get_chat_data_llm
from utils.logger import get_logger

logger = get_logger(__name__)

# 分类对应的图标和颜色
CATEGORY_STYLES = {
    "订单分析": {"icon": "fa-shopping-cart", "color": "#1677FF"},
    "销售分析": {"icon": "fa-chart-line", "color": "#10B981"},
    "用户统计": {"icon": "fa-users", "color": "#8B5CF6"},
    "商品分析": {"icon": "fa-fire", "color": "#F59E0B"},
    "库存管理": {"icon": "fa-box", "color": "#EF4444"},
    "客户分析": {"icon": "fa-crown", "color": "#F59E0B"},
    "趋势分析": {"icon": "fa-chart-area", "color": "#3B82F6"},
    "财务分析": {"icon": "fa-dollar-sign", "color": "#10B981"},
    "数据统计": {"icon": "fa-chart-simple", "color": "#06B6D4"},
    "其他": {"icon": "fa-database", "color": "#6B7280"},
}


async def generate_query_metadata(sql_text: str) -> dict:
    """
    使用LLM分析SQL，生成标题、描述、分类

    Args:
        sql_text: SQL查询语句

    Returns:
        {
            "title": "订单总数统计",
            "description": "查看订单总量",
            "category": "订单分析"
        }
    """
    try:
        llm = get_chat_data_llm(streaming=False)

        prompt = f'''分析以下SQL查询，生成简洁的中文标题、描述和分类。

SQL:
```sql
{sql_text}
```

要求：
1. 标题：5-10个字，概括查询目的
2. 描述：10-20个字，说明查询内容
3. 分类：从以下选择一个最合适的
   - 订单分析
   - 销售分析
   - 用户统计
   - 商品分析
   - 库存管理
   - 客户分析
   - 趋势分析
   - 财务分析
   - 数据统计
   - 其他

返回JSON格式（不要包含markdown代码块标记）：
{{"title": "...", "description": "...", "category": "..."}}'''

        response = await llm.ainvoke(prompt)
        content = response.content.strip()

        # 移除可能的markdown代码块标记
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1]) if len(lines) > 2 else content

        metadata = json.loads(content)

        # 验证必需字段
        if not all(k in metadata for k in ['title', 'description', 'category']):
            raise ValueError("Missing required fields")

        return metadata

    except Exception as e:
        logger.error(f"生成查询元数据失败: {str(e)}")
        # 返回默认值
        return {
            "title": "SQL查询",
            "description": "数据查询",
            "category": "其他"
        }


def assign_card_style(category: str) -> dict:
    """
    根据分类分配图标和颜色

    Args:
        category: 查询分类

    Returns:
        {"icon": "fa-shopping-cart", "color": "#1677FF"}
    """
    return CATEGORY_STYLES.get(category, CATEGORY_STYLES["其他"])
