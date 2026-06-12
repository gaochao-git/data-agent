"""
图表类型推荐服务

基于 SQL 和查询结果，使用 LLM 推荐最适合的图表类型
支持超时机制：5秒内无响应则返回默认表格类型
"""

import asyncio
import json
from typing import Optional, Dict, Any, List
from .llm_config import get_chat_data_llm
from utils.logger import get_logger

logger = get_logger(__name__)

# 支持的图表类型
CHART_TYPES = ["table", "bar", "line", "pie", "area", "card"]

# 推荐 prompt
CHART_RECOMMEND_PROMPT = """你是一个数据可视化专家。根据以下 SQL 查询和结果数据，推荐最适合的图表类型。

## SQL 查询
```sql
{sql}
```

## 查询结果
- 列名: {columns}
- 数据行数: {row_count}
- 示例数据（前3行）:
{sample_data}

## 可选的图表类型
1. table - 数据表格：适合明细数据、多列数据、需要精确查看的场景
2. bar - 柱状图：适合分类对比、排名展示
3. line - 折线图：适合趋势分析、时间序列数据
4. pie - 饼图：适合占比分析（类别不超过8个）
5. area - 面积图：适合趋势对比、堆叠展示
6. card - 指标卡：适合单一数值展示（如总数、平均值等，只有1行数据）

## 判断规则
- 只有1行数据且是聚合结果（如 COUNT、SUM、AVG）→ card
- 有时间维度（日期、月份、年份等）→ line 或 area
- 有分类维度 + 数值指标 → bar
- 需要展示占比且类别数 <= 8 → pie
- 多列数据或明细数据 → table

请直接返回 JSON 格式（不要包含其他文字）：
{{
  "chart_type": "bar|line|pie|area|card|table",
  "x_field": "X轴字段名或null",
  "y_field": "Y轴字段名",
  "reason": "简短说明原因"
}}
"""


async def recommend_chart_type(
    sql: str,
    columns: List[str],
    data: List[Dict[str, Any]],
    timeout: float = 5.0
) -> Dict[str, Any]:
    """
    使用 LLM 推荐图表类型

    Args:
        sql: SQL 查询语句
        columns: 列名列表
        data: 查询结果数据
        timeout: 超时时间（秒），默认5秒

    Returns:
        推荐结果：
        {
            "chart_type": "bar",  # 推荐的图表类型
            "x_field": "category",  # X 轴字段
            "y_field": "amount",  # Y 轴字段
            "reason": "有分类维度和数值指标"  # 推荐原因
        }
        超时或错误时返回默认值（table）
    """
    default_result = {
        "chart_type": "table",
        "x_field": None,
        "y_field": None,
        "reason": "默认表格展示"
    }

    try:
        # 准备示例数据（最多3行）
        sample_rows = data[:3] if data else []
        sample_data_str = json.dumps(sample_rows, ensure_ascii=False, indent=2)

        # 构建 prompt
        prompt = CHART_RECOMMEND_PROMPT.format(
            sql=sql,
            columns=columns,
            row_count=len(data),
            sample_data=sample_data_str
        )

        # 使用 asyncio.wait_for 实现超时
        async def call_llm():
            llm = get_chat_data_llm(streaming=False, temperature=0, max_tokens=200)
            response = await llm.ainvoke(prompt)
            return response.content

        try:
            response_text = await asyncio.wait_for(call_llm(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"图表推荐超时（{timeout}秒），使用默认表格")
            return default_result

        # 解析 JSON 响应
        # 尝试提取 JSON（可能包含 markdown 代码块）
        json_str = response_text.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        result = json.loads(json_str)

        # 验证图表类型
        chart_type = result.get("chart_type", "table")
        if chart_type not in CHART_TYPES:
            chart_type = "table"

        return {
            "chart_type": chart_type,
            "x_field": result.get("x_field"),
            "y_field": result.get("y_field"),
            "reason": result.get("reason", "")
        }

    except json.JSONDecodeError as e:
        logger.warning(f"图表推荐响应解析失败: {e}")
        return default_result
    except Exception as e:
        logger.error(f"图表推荐失败: {e}")
        return default_result


def quick_recommend_chart_type(
    columns: List[str],
    data: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    快速规则推荐（不调用 LLM，作为备用）

    基于简单规则快速判断图表类型
    """
    if not columns or not data:
        return {
            "chart_type": "table",
            "x_field": None,
            "y_field": None,
            "reason": "无数据"
        }

    row_count = len(data)
    col_count = len(columns)

    # 只有1行数据 → 指标卡
    if row_count == 1 and col_count <= 3:
        # 尝试找数值列
        first_row = data[0]
        numeric_cols = [
            col for col in columns
            if isinstance(first_row.get(col), (int, float))
        ]
        if numeric_cols:
            return {
                "chart_type": "card",
                "x_field": None,
                "y_field": numeric_cols[0],
                "reason": "单行聚合结果"
            }

    # 2列数据，一列文本一列数值 → 柱状图
    if col_count == 2 and row_count <= 50:
        first_row = data[0]
        str_col = None
        num_col = None
        for col in columns:
            val = first_row.get(col)
            if isinstance(val, (int, float)):
                num_col = col
            else:
                str_col = col
        if str_col and num_col:
            # 检查分类数
            if row_count <= 8:
                return {
                    "chart_type": "bar",
                    "x_field": str_col,
                    "y_field": num_col,
                    "reason": "分类对比数据"
                }

    # 默认表格
    return {
        "chart_type": "table",
        "x_field": None,
        "y_field": None,
        "reason": "默认表格展示"
    }
