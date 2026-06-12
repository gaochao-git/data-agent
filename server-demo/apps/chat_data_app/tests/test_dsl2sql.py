"""
DSL2SQL 单元测试

测试 SemanticLayer 的 SQL 生成功能，特别是树形过滤条件的处理。
测试数据从 dsl2sql_tests/ 目录下的 JSON 文件加载。
"""

import pytest
import json
import os
import sys
from pydantic import ValidationError

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from apps.chat_data_app.semantic import (
    SQLTranslator,
    SemanticQuery,
    SemanticConfig,
    FilterValidationError,
)
from apps.chat_data_app.schema import ExecuteSemanticInput


# ============ 加载测试数据 ============

TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), 'dsl2sql_tests')


def load_semantic_configs():
    """加载语义配置"""
    config_path = os.path.join(TEST_DATA_DIR, 'semantic_configs.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_test_cases():
    """加载测试用例"""
    cases_path = os.path.join(TEST_DATA_DIR, 'test_cases.json')
    with open(cases_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# 全局加载配置和测试用例
SEMANTIC_CONFIGS = load_semantic_configs()
TEST_CASES = load_test_cases()


def get_config(config_name: str) -> dict:
    """获取指定名称的语义配置"""
    return SEMANTIC_CONFIGS.get(config_name, SEMANTIC_CONFIGS['two_table_config'])


def get_test_cases_by_category(category: str) -> list:
    """按类别筛选测试用例"""
    return [tc for tc in TEST_CASES if tc['category'] == category]


def normalize_relationships_config(config: dict, config_name: str) -> dict:
    """将旧版 join 配置统一规范为新版 relationships.join_conditions。"""
    normalized = dict(config)
    if 'id' not in normalized:
        normalized['id'] = config_name
    if 'name' not in normalized:
        normalized['name'] = config_name

    if 'joins' in normalized and 'relationships' not in normalized:
        normalized['relationships'] = normalized['joins']

    relationships = []
    for rel in normalized.get('relationships', []):
        rel_item = dict(rel)
        if not rel_item.get('join_conditions'):
            left_col = rel_item.get('left_column')
            right_col = rel_item.get('right_column')
            if left_col and right_col:
                rel_item['join_conditions'] = [{
                    'left_column': left_col,
                    'right_column': right_col
                }]
        relationships.append(rel_item)
    normalized['relationships'] = relationships
    return normalized


# ============ Mock LLM ============

from unittest.mock import patch, MagicMock

# 全局 Mock（用于 SemanticSQLGenerator 需要 LLM）
_mock_llm_patcher = None

def setup_module(module):
    """模块级别 setup：Mock LLM"""
    global _mock_llm_patcher
    _mock_llm_patcher = patch('utils.lang_util.llm.get_llm')
    mock_get_llm = _mock_llm_patcher.start()
    mock_get_llm.return_value = MagicMock()

def teardown_module(module):
    """模块级别 teardown：停止 Mock"""
    global _mock_llm_patcher
    if _mock_llm_patcher:
        _mock_llm_patcher.stop()


# ============ 测试辅助函数 ============

def run_translate(config_name: str, input_data: dict) -> tuple:
    """执行 DSL 到 SQL 的转换（聚合查询）"""
    config = normalize_relationships_config(get_config(config_name), config_name)
    # 创建 SemanticConfig 对象
    semantic_config = SemanticConfig.model_validate(config)
    dialect = config.get('source_type', 'mysql')
    translator = SQLTranslator(semantic_config, dialect=dialect)

    dsl = SemanticQuery(
        metrics=input_data.get('metrics', []),
        dimensions=input_data.get('dimensions', []),
        filters=input_data.get('filters', []),
        having=input_data.get('having', []),
        order_by=input_data.get('order_by', []),
        limit=input_data.get('limit'),
        offset=input_data.get('offset')
    )

    result = translator.translate(dsl)
    if not result.success:
        raise ValueError(result.error)
    return result.sql, result.explanation, result.summary


def run_detail_translate(config_name: str, input_data: dict) -> tuple:
    """执行明细查询 SQL 生成"""
    from apps.chat_data_app.services.semantic_sql_generator import SemanticSQLGenerator

    config = normalize_relationships_config(get_config(config_name), config_name)
    generator = SemanticSQLGenerator(config)

    result = generator.generate_sql(
        metrics=input_data.get('metrics', []),
        dimensions=input_data.get('dimensions', []),
        filters=input_data.get('filters', []),
        having=input_data.get('having', []),
        order_by=input_data.get('order_by', []),
        limit=input_data.get('limit'),
        compare=input_data.get('compare')
    )

    if not result.get('success'):
        raise ValueError(result.get('error', '未知错误'))

    return result['sql'], result.get('params_block', '')


def normalize_sql(sql: str) -> str:
    """标准化 SQL，去除格式差异便于比对"""
    import re
    sql = sql.replace('`', '')
    # 统一移除列名前的表名前缀（统一引擎在单表场景会省略表前缀）
    sql = re.sub(r'\b[a-zA-Z_]\w*\.', '', sql)
    # 去除多余空白，统一为单空格
    sql = ' '.join(sql.split())
    # 统一大小写（保留字符串字面量）
    # 简化处理：不做大小写转换，保持原样比对
    return sql.strip()


def verify_expected(sql: str, expected: dict):
    """验证 SQL 是否符合预期"""
    # 1. 精确 SQL 比对（优先级最高）
    if 'expected_sql' in expected:
        actual_normalized = normalize_sql(sql)
        expected_normalized = normalize_sql(expected['expected_sql'])
        assert actual_normalized == expected_normalized, \
            f"\n=== SQL 不匹配 ===\n期望:\n{expected['expected_sql']}\n\n实际:\n{sql}"
        return  # 精确匹配成功，跳过其他检查

    # 2. 特征匹配（兼容旧测试用例）
    # 检查必须包含的内容
    for item in expected.get('contains', []):
        assert item in sql, f"Expected '{item}' in SQL, but not found. SQL: {sql}"

    # 检查不应包含的内容
    for item in expected.get('not_contains', []):
        assert item not in sql, f"Expected '{item}' NOT in SQL, but found. SQL: {sql}"

    # 检查 JOIN 数量
    if 'min_join_count' in expected:
        join_count = sql.upper().count('JOIN')
        assert join_count >= expected['min_join_count'], \
            f"Expected at least {expected['min_join_count']} JOINs, got {join_count}. SQL: {sql}"

    # 检查 WHERE 在 HAVING 之前
    if expected.get('where_before_having'):
        where_pos = sql.find('WHERE')
        having_pos = sql.find('HAVING')
        assert where_pos < having_pos, \
            f"Expected WHERE before HAVING. SQL: {sql}"


# ============ 平铺格式过滤测试 ============

class TestFlatFilters:
    """测试平铺格式过滤条件"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('flat_filter'),
                             ids=lambda tc: tc['id'])
    def test_flat_filter(self, test_case):
        """参数化测试：平铺格式过滤"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 树形格式过滤测试 ============

class TestTreeFilters:
    """测试树形格式过滤条件（OR/AND）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('tree_filter'),
                             ids=lambda tc: tc['id'])
    def test_tree_filter(self, test_case):
        """参数化测试：树形格式过滤"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 多表关联测试 ============

class TestJoins:
    """测试多表关联（JOIN）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('join'),
                             ids=lambda tc: tc['id'])
    def test_join(self, test_case):
        """参数化测试：多表关联"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 排序和分页测试 ============

class TestOrderAndLimit:
    """测试排序和分页"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('order_limit'),
                             ids=lambda tc: tc['id'])
    def test_order_limit(self, test_case):
        """参数化测试：排序和分页"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ HAVING 测试 ============

class TestHaving:
    """测试 HAVING 条件"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('having'),
                             ids=lambda tc: tc['id'])
    def test_having(self, test_case):
        """参数化测试：HAVING 条件"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 明细查询测试 ============

class TestDetailQuery:
    """测试明细查询（detail mode）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('detail'),
                             ids=lambda tc: tc['id'])
    def test_detail_query(self, test_case):
        """参数化测试：明细查询"""
        sql, params_block = run_detail_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


class TestCompareStrategyCalc:
    """测试同比/环比运行时计算 SQL。"""

    @pytest.mark.parametrize(
        "input_data, expected_contains",
        [
            (
                {
                    "metrics": ["订单总金额"],
                    "dimensions": [{"name": "下单日期", "time_grain": "quarter"}],
                    "filters": [],
                    "having": [],
                    "order_by": [],
                    "limit": 100,
                    "compare": {"type": "yoy", "mode": "trend", "strategy": "calc"},
                },
                [
                    "DATE_ADD(MAKEDATE(YEAR(`order_date`), 1), INTERVAL QUARTER(`order_date`) - 1 QUARTER) AS stat_time",
                    "CONCAT(YEAR(cur.stat_time), '-Q', QUARTER(cur.stat_time))",
                    "DATE_SUB(cur.stat_time, INTERVAL 1 YEAR)",
                ],
            ),
        ],
        ids=["compare_quarter_yoy"],
    )
    def test_compare_strategy_calc(self, input_data, expected_contains):
        sql, _ = run_detail_translate("ecommerce_config", input_data)
        for item in expected_contains:
            assert item in sql, f"Expected '{item}' in SQL, but not found. SQL: {sql}"

    def test_compare_strategy_calc_week_rejected(self):
        input_data = {
            "metrics": ["订单总金额"],
            "dimensions": [{"name": "下单日期", "time_grain": "week"}],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 100,
            "compare": {"type": "mom", "mode": "trend", "strategy": "calc"},
        }

        with pytest.raises(ValueError, match="暂不支持 week"):
            run_detail_translate("ecommerce_config", input_data)


# ============ 枚举值校验测试 ============

class TestAllowedValues:
    """测试 allowed_values 枚举值校验"""

    @pytest.mark.parametrize("test_case",
                             [tc for tc in get_test_cases_by_category('allowed_values')
                              if not tc['expected'].get('should_raise')],
                             ids=lambda tc: tc['id'])
    def test_valid_allowed_value(self, test_case):
        """测试有效的枚举值"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])

    @pytest.mark.parametrize("test_case",
                             [tc for tc in get_test_cases_by_category('allowed_values')
                              if tc['expected'].get('should_raise')],
                             ids=lambda tc: tc['id'])
    def test_invalid_allowed_value(self, test_case):
        """测试无效的枚举值应抛出异常"""
        expected = test_case['expected']

        with pytest.raises((ValueError, FilterValidationError)) as excinfo:
            run_translate(test_case['config'], test_case['input'])

        if 'error_contains' in expected:
            assert expected['error_contains'] in str(excinfo.value)


# ============ 时间粒度测试 ============

class TestTimeGrain:
    """测试时间粒度（year/month/day/hour）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('time_grain'),
                             ids=lambda tc: tc['id'])
    def test_time_grain(self, test_case):
        """参数化测试：时间粒度"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 比较操作符测试 ============

class TestComparison:
    """测试比较操作符（>, <, >=, <=, !=）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('comparison'),
                             ids=lambda tc: tc['id'])
    def test_comparison(self, test_case):
        """参数化测试：比较操作符"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 其他操作符测试 ============

class TestOperators:
    """测试其他操作符（LIKE, NOT IN, IS NULL, BETWEEN）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('operators'),
                             ids=lambda tc: tc['id'])
    def test_operators(self, test_case):
        """参数化测试：其他操作符"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 分页测试 ============

class TestPagination:
    """测试分页（LIMIT + OFFSET）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('pagination'),
                             ids=lambda tc: tc['id'])
    def test_pagination(self, test_case):
        """参数化测试：分页"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 同义词映射测试 ============

class TestMapping:
    """测试同义词映射"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('mapping'),
                             ids=lambda tc: tc['id'])
    def test_mapping(self, test_case):
        """参数化测试：同义词映射"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 维度表达式测试 ============

class TestExpression:
    """测试维度表达式（CASE WHEN）"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('expression'),
                             ids=lambda tc: tc['id'])
    def test_expression(self, test_case):
        """参数化测试：维度表达式"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ Hive 方言测试 ============

class TestHiveDialect:
    """测试 Hive 方言"""

    @pytest.mark.parametrize("test_case", get_test_cases_by_category('hive_dialect'),
                             ids=lambda tc: tc['id'])
    def test_hive_dialect(self, test_case):
        """参数化测试：Hive 方言"""
        sql, explanation, summary = run_translate(
            test_case['config'],
            test_case['input']
        )
        verify_expected(sql, test_case['expected'])


# ============ 边界情况测试 ============

class TestEdgeCases:
    """测试边界情况"""

    def test_empty_filters(self):
        """测试空过滤条件"""
        sql, explanation, summary = run_translate('two_table_config', {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": None
        })

        assert "SELECT" in sql
        assert "FROM" in sql
        assert "WHERE" not in sql

    def test_empty_or_node(self):
        """测试空 OR 节点"""
        sql, explanation, summary = run_translate('two_table_config', {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [{"or": []}],
            "having": [],
            "order_by": [],
            "limit": None
        })

        # 空节点应该被忽略
        assert "WHERE" not in sql or "WHERE ()" not in sql

    def test_single_leaf_in_or(self):
        """测试 OR 节点中只有一个叶子"""
        sql, explanation, summary = run_translate('two_table_config', {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [{
                "or": [
                    {"dimension": "cluster_db_type", "operator": "=", "value": "MySQL"}
                ]
            }],
            "having": [],
            "order_by": [],
            "limit": None
        })

        assert "WHERE" in sql
        assert "db_type = 'MySQL'" in sql
        # 只有一个条件，不应该有 OR
        assert " OR " not in sql


# ============ 树形过滤检测测试（功能测试，不再直接测试内部方法）============

class TestTreeFilterDetection:
    """测试树形过滤检测功能（通过 SQL 输出验证）"""

    def test_flat_filters_generates_and(self):
        """平铺格式应该生成 AND 连接"""
        sql, _, _ = run_translate('two_table_config', {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [
                {"dimension": "cluster_db_type", "operator": "=", "value": "MySQL"},
                {"dimension": "cluster_status", "operator": "=", "value": "running"}
            ],
            "having": [],
            "order_by": [],
            "limit": None
        })
        # 多个平铺过滤条件应该用 AND 连接
        assert "AND" in sql.upper()

    def test_or_filter_generates_or(self):
        """OR 节点应该生成 OR 连接"""
        sql, _, _ = run_translate('two_table_config', {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [{
                "or": [
                    {"dimension": "cluster_db_type", "operator": "=", "value": "MySQL"},
                    {"dimension": "cluster_db_type", "operator": "=", "value": "PostgreSQL"}
                ]
            }],
            "having": [],
            "order_by": [],
            "limit": None
        })
        assert "OR" in sql.upper()

    def test_and_filter_generates_and(self):
        """AND 节点应该生成 AND 连接"""
        sql, _, _ = run_translate('two_table_config', {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [{
                "and": [
                    {"dimension": "cluster_db_type", "operator": "=", "value": "MySQL"},
                    {"dimension": "cluster_status", "operator": "=", "value": "running"}
                ]
            }],
            "having": [],
            "order_by": [],
            "limit": None
        })
        assert "AND" in sql.upper()


# ============ JOIN 类型测试 ============

class TestJoinTypes:
    """测试不同的 JOIN 类型"""

    def _create_join_config(self, join_type: str) -> dict:
        """创建指定 JOIN 类型的配置"""
        return {
            "id": "test_join",
            "name": "测试JOIN配置",
            "metrics": [
                {"name": "count", "expression": "COUNT(*)", "table": "t1"}
            ],
            "dimensions": [
                {"name": "dim1", "column": "col1", "table": "t2", "type": "categorical"}
            ],
            "relationships": [
                {
                    "left_table": "t1",
                    "right_table": "t2",
                    "left_column": "id",
                    "right_column": "t1_id",
                    "join_conditions": [{"left_column": "id", "right_column": "t1_id"}],
                    "relationship_type": "one_to_many",
                    "join_type": join_type
                }
            ]
        }

    def test_left_join(self):
        """测试 LEFT JOIN"""
        config = self._create_join_config("left")
        semantic_config = SemanticConfig.model_validate(config)
        translator = SQLTranslator(semantic_config, dialect='mysql')

        dsl = SemanticQuery(
            metrics=["count"],
            dimensions=["dim1"],
            filters=[],
            having=[],
            order_by=[],
            limit=None
        )

        result = translator.translate(dsl)
        assert result.success
        assert "LEFT JOIN" in result.sql.upper()

    def test_inner_join(self):
        """测试 INNER JOIN"""
        config = self._create_join_config("inner")
        semantic_config = SemanticConfig.model_validate(config)
        translator = SQLTranslator(semantic_config, dialect='mysql')

        dsl = SemanticQuery(
            metrics=["count"],
            dimensions=["dim1"],
            filters=[],
            having=[],
            order_by=[],
            limit=None
        )

        result = translator.translate(dsl)
        assert result.success
        # INNER JOIN 或者只是 JOIN（不带 LEFT/RIGHT）
        sql_upper = result.sql.upper()
        assert "INNER JOIN" in sql_upper or ("JOIN" in sql_upper and "LEFT" not in sql_upper and "RIGHT" not in sql_upper)

    def test_right_join(self):
        """测试 RIGHT JOIN"""
        config = self._create_join_config("right")
        semantic_config = SemanticConfig.model_validate(config)
        translator = SQLTranslator(semantic_config, dialect='mysql')

        dsl = SemanticQuery(
            metrics=["count"],
            dimensions=["dim1"],
            filters=[],
            having=[],
            order_by=[],
            limit=None
        )

        result = translator.translate(dsl)
        assert result.success
        assert "RIGHT JOIN" in result.sql.upper()


class TestFilterValueType:
    """测试过滤值 value_type 处理"""

    def test_sql_function_value_type_keeps_function_unquoted(self):
        sql, _, _ = run_translate(
            "advanced_config",
            {
                "metrics": ["订单数"],
                "dimensions": [],
                "filters": [
                    {"dimension": "订单日期", "operator": "<=", "value": "current_date()", "value_type": "sql_function"}
                ],
                "having": [],
                "order_by": [],
                "limit": None
            }
        )
        assert "current_date()" in sql
        assert "'current_date()'" not in sql

    def test_literal_value_type_forces_string_quote(self):
        sql, _, _ = run_translate(
            "advanced_config",
            {
                "metrics": ["订单数"],
                "dimensions": [],
                "filters": [
                    {"dimension": "订单日期", "operator": "<=", "value": "current_date()", "value_type": "literal"}
                ],
                "having": [],
                "order_by": [],
                "limit": None
            }
        )
        assert "'current_date()'" in sql

    def test_invalid_sql_function_fallbacks_to_literal(self):
        sql, _, _ = run_translate(
            "advanced_config",
            {
                "metrics": ["订单数"],
                "dimensions": [],
                "filters": [
                    {"dimension": "订单日期", "operator": "=", "value": "unknown_func(abc)", "value_type": "sql_function"}
                ],
                "having": [],
                "order_by": [],
                "limit": None
            }
        )
        assert "'unknown_func(abc)'" in sql

    def test_compat_quoted_function_without_value_type(self):
        sql, _, _ = run_translate(
            "advanced_config",
            {
                "metrics": ["订单数"],
                "dimensions": [],
                "filters": [
                    {"dimension": "订单日期", "operator": "<=", "value": "'current_date()'"}
                ],
                "having": [],
                "order_by": [],
                "limit": None
            }
        )
        assert "current_date()" in sql
        assert "'current_date()'" not in sql


class TestSemanticToolInput:
    """测试语义工具入参模型（mode 已废弃）"""

    def test_execute_semantic_input_accepts_without_mode(self):
        payload = {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 10
        }
        model = ExecuteSemanticInput.model_validate(payload)
        assert model.metrics == ["cluster_count"]

    def test_execute_semantic_input_rejects_mode(self):
        payload = {
            "mode": "detail",
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 10
        }
        with pytest.raises(ValidationError):
            ExecuteSemanticInput.model_validate(payload)

    def test_execute_semantic_input_allows_injected_state(self):
        payload = {
            "metrics": ["cluster_count"],
            "dimensions": [],
            "filters": [],
            "having": [],
            "order_by": [],
            "limit": 10,
            "state": {"messages": []}
        }
        model = ExecuteSemanticInput.model_validate(payload)
        assert model.metrics == ["cluster_count"]


# ============ 运行测试 ============

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
