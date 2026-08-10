"""纯语义 SQL 编译的行数限制。"""


def normalize_row_limit(limit: int | None, max_rows: int = 100) -> int:
    """校验编译请求中的 LIMIT，不读取环境或业务配置。"""
    if limit is None:
        return max_rows
    if not isinstance(limit, int) or limit < 1:
        raise ValueError(f"查询结果行数限制必须为正整数，当前值：{limit}")
    if limit > max_rows:
        raise ValueError(f"查询结果最多返回 {max_rows} 条，请调整查询条件")
    return limit
