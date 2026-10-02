"""ChatData deterministic calculator tool."""

import ast
import json
import operator
import re
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal, getcontext
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import Field

from utils.logger import get_logger

from ...schema import CalculatorItem

logger = get_logger(__name__)

# 一元(pos/neg)与二元(add/sub/mul/truediv)签名不同，统一按任意参数调用后返回 Decimal/Any；
# 键需同时容纳 ast.operator 与 ast.unaryop 两个分支，故用 ast.AST
_OPERATORS: dict[type[ast.AST], Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _format_decimal(value: Decimal) -> str:
    """格式化 Decimal，保留完整有效数字并去掉多余零。"""
    if value == value.to_integral_value():
        return str(value.to_integral_value())
    return format(value.normalize(), "f")


def _eval_decimal_expression(node: ast.AST) -> Decimal:
    """只计算安全的十进制四则表达式。"""
    if isinstance(node, ast.Expression):
        return _eval_decimal_expression(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        operand = _eval_decimal_expression(node.operand)
        return _OPERATORS[type(node.op)](operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left = _eval_decimal_expression(node.left)
        right = _eval_decimal_expression(node.right)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ValueError("除数不能为 0")
        return _OPERATORS[type(node.op)](left, right)
    raise ValueError("只支持数字、括号和 + - * / 四则运算")


def _calculate_item(item: Any) -> dict[str, Any]:
    """计算单个表达式并返回结构化结果。"""
    item_data = item.model_dump() if hasattr(item, "model_dump") else dict(item or {})
    name = str(item_data.get("name") or "").strip()
    expression = str(item_data.get("expression") or "").strip()
    decimal_places = item_data.get("decimal_places")
    result: dict[str, Any] = {"expression": expression}
    if name:
        result["name"] = name
    if not expression:
        result.update({"success": False, "error": "expression 不能为空"})
        return result
    if not re.fullmatch(r"[\d\s+\-*/().]+", expression):
        result.update({"success": False, "error": "expression 只能包含数字、括号和 + - * /"})
        return result
    try:
        parsed = ast.parse(expression, mode="eval")
        value = _eval_decimal_expression(parsed)
        if decimal_places is not None:
            quantize_unit = Decimal("1") if decimal_places == 0 else Decimal("1").scaleb(-int(decimal_places))
            value = value.quantize(quantize_unit, rounding=ROUND_HALF_UP)
        result.update({"success": True, "result": _format_decimal(value)})
        return result
    except Exception as err:
        logger.warning("calculator_tool 单项计算失败: expression=%s err=%s", expression, err)
        result.update({"success": False, "error": str(err)})
        return result


@tool("calculator_tool", parse_docstring=True)
async def calculator_tool(
    calculations: Annotated[list[CalculatorItem], Field(min_length=1, max_length=20)],
    intent: str = "",
) -> str:
    """对查询结果中的长数字、比例、差值或单位换算做确定性计算。

    Args:
        calculations: 批量计算项（1-20 条）；单个计算也只传一条记录，每项包含必填的 expression、可选的 name 和 decimal_places。
        intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
    """
    getcontext().prec = 50
    results = [_calculate_item(item) for item in calculations]
    return json.dumps(
        {
            "success": all(item.get("success") for item in results),
            "results": results,
        },
        ensure_ascii=False,
    )
