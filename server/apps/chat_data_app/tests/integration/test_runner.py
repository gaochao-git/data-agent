"""
NL2SQL/NL2DSL 测试运行器

执行测试用例并生成评估报告
"""

import asyncio
import json
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Any
import httpx

# 添加 server 根目录到 Python 路径
tests_root = Path(__file__).resolve().parents[1]
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root))

# 加载环境变量
import os
from dotenv import load_dotenv

# 尝试从多个可能的位置加载 .env 文件
env_paths = [
    project_root / ".env",  # 从 server 目录
    Path.cwd() / ".env",  # 从当前目录
]

env_loaded = False
for env_file in env_paths:
    if env_file.exists():
        load_dotenv(env_file)
        env_loaded = True
        break

if not env_loaded:
    print(f"警告: 未找到 .env 文件，尝试的路径: {env_paths}")

# 验证关键环境变量
if not os.getenv("CHAT_DATA_API_KEY"):
    print(f"警告: CHAT_DATA_API_KEY 未设置，LLM 评估将失败")
else:
    api_key = os.getenv("CHAT_DATA_API_KEY")
    print(f"环境变量已加载，API KEY 前缀: {api_key[:10]}...")

from apps.chat_data_app.tests.evaluator import NL2SQLEvaluator, NL2DSLEvaluator


# 配置日志
def setup_logger(log_dir: Path) -> logging.Logger:
    """配置日志记录器"""
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"test_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger = logging.getLogger("test_runner")
    logger.setLevel(logging.INFO)

    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # 格式化器
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


class TestRunner:
    """测试运行器"""

    def __init__(
        self,
        api_base_url: str = "http://127.0.0.1:5001",
        datasource_id: str | None = None,
        logger: logging.Logger | None = None,
    ):
        self.api_base_url = api_base_url
        self.datasource_id = datasource_id
        self.nl2sql_evaluator = NL2SQLEvaluator()
        self.nl2dsl_evaluator = NL2DSLEvaluator()
        self.logger = logger or logging.getLogger("test_runner")

    def load_test_cases(self, test_dir: Path) -> list[dict]:
        """加载测试用例"""
        test_cases = []
        for json_file in test_dir.rglob("test_cases.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                cases = json.load(f)
                test_cases.extend(cases)
                self.logger.info(f"加载测试用例: {json_file} ({len(cases)} 个)")
        self.logger.info(f"总共加载 {len(test_cases)} 个测试用例")
        return test_cases

    async def run_nl2sql_test(self, test_case: dict) -> dict[str, Any]:
        """运行单个 NL2SQL 测试用例"""
        question = test_case["question"]
        ground_truth = test_case["ground_truth"]

        self.logger.info(f"测试 {test_case['id']}: {question}")

        # 调用 API
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self.api_base_url}/api/chatdata/nl2dsl2sql",
                    json={
                        "query": question,
                        "datasource_id": self.datasource_id,
                        "mode": "generate",
                    },
                )

                if response.status_code != 200:
                    self.logger.error(f"{test_case['id']} 失败: API error {response.status_code}")
                    return {
                        "test_case_id": test_case["id"],
                        "question": question,
                        "error": f"API error: {response.status_code}",
                        "success": False,
                    }

                # 解析流式响应，提取最终的 SQL
                predicted_sql = self._extract_sql_from_stream(response.text)

                if not predicted_sql:
                    self.logger.error(f"{test_case['id']} 失败: No SQL generated")
                    return {
                        "test_case_id": test_case["id"],
                        "question": question,
                        "error": "No SQL generated",
                        "success": False,
                    }

                # 传统评估
                evaluation = self.nl2sql_evaluator.evaluate(predicted_sql, ground_truth)

                # LLM 评估（如果启用）
                llm_result = None
                if self.nl2sql_evaluator.use_llm_judge:
                    llm_result = await self.nl2sql_evaluator.evaluate_with_llm(
                        question=question,
                        predicted_sql=predicted_sql,
                        expected_sql=ground_truth.get("sql", ""),
                    )
                    evaluation["llm_judge"] = llm_result

                # 判断最终是否正确（优先使用 LLM 评估，否则使用精确匹配）
                is_correct = (
                    llm_result["is_correct"]
                    if llm_result
                    else evaluation["exact_match"]
                )

                match_status = "✓" if is_correct else "✗"
                log_msg = f"{test_case['id']} {match_status} - 精确匹配:{evaluation['exact_match']}, 组件匹配:{evaluation['component_match_rate']:.2f}, 相似度:{evaluation['similarity']:.2f}"
                if llm_result:
                    log_msg += f", LLM判断:{llm_result['is_correct']}(置信度:{llm_result['confidence']:.2f})"
                self.logger.info(log_msg)

                # 打印标准答案和实际生成的SQL（压缩为单行便于对比）
                import re
                predicted_sql_oneline = re.sub(r'\s+', ' ', predicted_sql).strip()
                self.logger.info(f"  标准SQL: {ground_truth.get('sql', 'N/A')}")
                self.logger.info(f"  生成SQL: {predicted_sql_oneline}")
                if llm_result and llm_result.get("reason"):
                    self.logger.info(f"  LLM评价: {llm_result['reason']}")

                return {
                    "test_case_id": test_case["id"],
                    "category": test_case.get("category"),
                    "difficulty": test_case.get("difficulty"),
                    "question": question,
                    "evaluation": evaluation,
                    "success": True,
                }

            except Exception as e:
                self.logger.error(f"{test_case['id']} 异常: {str(e)}")
                return {
                    "test_case_id": test_case["id"],
                    "question": question,
                    "error": str(e),
                    "success": False,
                }

    async def run_nl2dsl_test(
        self, test_case: dict, semantic_config: dict
    ) -> dict[str, Any]:
        """运行单个 NL2DSL 测试用例（通过流式 NL2SQL 接口提取 DSL）"""
        question = test_case["question"]
        ground_truth = test_case["ground_truth"]

        self.logger.info(f"测试 {test_case['id']}: {question}")

        # 调用流式 NL2SQL 接口，并从事件流中提取 DSL
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self.api_base_url}/api/chatdata/nl2dsl2sql",
                    json={
                        "query": question,
                        "mode": "generate",
                        "semantic_config": semantic_config,
                        "semantic_config_id": semantic_config.get("id") if isinstance(semantic_config, dict) else None,
                    },
                )

                if response.status_code != 200:
                    self.logger.error(f"{test_case['id']} 失败: API error {response.status_code}: {response.text}")
                    return {
                        "test_case_id": test_case["id"],
                        "question": question,
                        "error": f"API error: {response.status_code}",
                        "success": False,
                    }

                stream_text = response.text
                predicted_dsl = self._extract_dsl_from_stream(stream_text)

                if not predicted_dsl:
                    self.logger.error(f"{test_case['id']} 失败: No DSL generated")
                    return {
                        "test_case_id": test_case["id"],
                        "question": question,
                        "error": "No DSL generated",
                        "success": False,
                    }

                # 评估
                evaluation = self.nl2dsl_evaluator.evaluate(predicted_dsl, ground_truth)

                match_status = "✓" if evaluation["exact_match"] else "✗"
                self.logger.info(
                    f"{test_case['id']} {match_status} - "
                    f"精确匹配:{evaluation['exact_match']}, "
                    f"组件匹配:{evaluation['component_match_rate']:.2f}, "
                    f"综合准确率:{evaluation['overall_accuracy']:.2f}"
                )

                # 打印标准答案和实际生成的DSL
                self.logger.info(f"  标准DSL: {json.dumps(ground_truth.get('dsl', {}), ensure_ascii=False)}")
                self.logger.info(f"  生成DSL: {json.dumps(predicted_dsl, ensure_ascii=False)}")

                return {
                    "test_case_id": test_case["id"],
                    "category": test_case.get("category"),
                    "difficulty": test_case.get("difficulty"),
                    "question": question,
                    "evaluation": evaluation,
                    "success": True,
                }

            except Exception as e:
                self.logger.error(f"{test_case['id']} 异常: {str(e)}")
                return {
                    "test_case_id": test_case["id"],
                    "question": question,
                    "error": str(e),
                    "success": False,
                }

    def _extract_sql_from_stream(self, stream_text: str) -> str | None:
        """从流式响应中提取 SQL"""
        import re

        # 收集所有AIMessageChunk的content
        content_parts = []
        for line in stream_text.strip().split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    # 收集 AIMessageChunk 的 content
                    if data.get("type") == "AIMessageChunk" and "content" in data:
                        content = data["content"]
                        if content and isinstance(content, str):
                            content_parts.append(content)
                except json.JSONDecodeError:
                    continue

        # 组合完整内容
        full_content = "".join(content_parts)

        if not full_content:
            self.logger.warning(f"无法提取内容，响应前500字符: {stream_text[:500]}")
            return None

        # 从完整内容中提取SQL
        # 1. 尝试提取SQL代码块
        sql_match = re.search(r'```sql\s+(.*?)\s+```', full_content, re.DOTALL | re.IGNORECASE)
        if sql_match:
            return sql_match.group(1).strip()

        # 2. 尝试提取纯代码块（可能没有sql标记）
        code_match = re.search(r'```\s*(SELECT.*?)\s*```', full_content, re.DOTALL | re.IGNORECASE)
        if code_match:
            return code_match.group(1).strip()

        # 3. 直接查找SELECT语句（没有代码块标记）
        select_match = re.search(r'(SELECT\s+.*?(?:;|$))', full_content, re.DOTALL | re.IGNORECASE)
        if select_match:
            return select_match.group(1).strip().rstrip(';')

        self.logger.warning(f"无法从内容中提取SQL，完整内容前500字符: {full_content[:500]}")
        return None

    def _extract_dsl_from_stream(self, stream_text: str) -> dict | None:
        """从流式响应中提取 DSL（从工具调用参数中提取）"""
        import re

        # 用于调试：记录所有找到的tool_calls
        found_tool_calls = []

        # 遍历所有流式消息，查找完整的tool_calls
        for line in stream_text.strip().split("\n"):
            if not line.startswith("data: "):
                continue

            try:
                data = json.loads(line[6:])

                # 方法1: 从AIMessageChunk的tool_calls中提取（完整的工具调用）
                if data.get("type") == "AIMessageChunk":
                    tool_calls = data.get("tool_calls", [])
                    if tool_calls:
                        found_tool_calls.extend(tool_calls)

                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue

                        # 检查是否是语义工具
                        tool_name = tool_call.get("name", "")
                        if "semantic" in tool_name.lower():
                            args = tool_call.get("args", {})
                            # 检查args是否包含DSL参数
                            if isinstance(args, dict) and ("metrics" in args or "dimensions" in args):
                                self.logger.info(f"从tool_calls提取DSL: {args}")
                                return {
                                    "metrics": args.get("metrics", []),
                                    "dimensions": args.get("dimensions", []),
                                    "filters": args.get("filters", []),
                                    "having": args.get("having", [])
                                }

            except json.JSONDecodeError:
                continue

        # 调试：记录找到的所有tool_calls
        if found_tool_calls:
            self.logger.debug(f"找到的所有tool_calls: {json.dumps(found_tool_calls, ensure_ascii=False)}")

        # 备用方法：从文本内容中提取（如果LLM返回了<semantic_params>块）
        content_parts = []
        for line in stream_text.strip().split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if data.get("type") in ("AIMessageChunk", "ai") and "content" in data:
                        content = data["content"]
                        if content and isinstance(content, str):
                            content_parts.append(content)
                except json.JSONDecodeError:
                    continue

        full_content = "".join(content_parts)

        if full_content:
            # 尝试从<semantic_params>块中提取DSL
            semantic_match = re.search(r'<semantic_params>(.*?)</semantic_params>', full_content, re.DOTALL)
            if semantic_match:
                params_text = semantic_match.group(1).strip()
                # 解析语义参数文本
                dsl = {"metrics": [], "dimensions": [], "filters": [], "having": []}

                # 提取指标
                metrics_match = re.search(r'指标:\s*(.+?)(?:\n|$)', params_text)
                if metrics_match:
                    dsl["metrics"] = [m.strip() for m in metrics_match.group(1).split(',')]

                # 提取维度
                dimensions_match = re.search(r'维度:\s*(.+?)(?:\n|$)', params_text)
                if dimensions_match:
                    dim_text = dimensions_match.group(1).strip()
                    if dim_text and dim_text != '无':
                        dsl["dimensions"] = [d.strip() for d in dim_text.split(',')]

                # 提取筛选
                filters_match = re.search(r'筛选:\s*(.+?)(?:\n|$)', params_text)
                if filters_match:
                    filter_text = filters_match.group(1).strip()
                    if filter_text and filter_text != '无':
                        # 简单解析，实际可能需要更复杂的逻辑
                        pass

                if dsl["metrics"]:
                    self.logger.info(f"从semantic_params提取DSL: {dsl}")
                    return dsl

        self.logger.warning(f"无法从响应中提取DSL")

        # 保存原始响应用于调试（仅保存第一个失败的）
        debug_file = tests_root / "reports" / "debug_dsl_response.txt"
        if not debug_file.exists():
            try:
                debug_file.parent.mkdir(parents=True, exist_ok=True)
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(stream_text)
                self.logger.info(f"已保存原始响应到: {debug_file}")
            except Exception as e:
                self.logger.warning(f"保存调试文件失败: {e}")

        self.logger.debug(f"响应前1000字符: {stream_text[:1000]}")
        return None

    def generate_report(
        self, test_type: str, results: list[dict], output_path: Path
    ) -> dict[str, Any]:
        """生成测试报告"""
        total = len(results)
        successful = sum(1 for r in results if r["success"])
        failed = total - successful

        # 按类别统计
        category_stats = {}
        difficulty_stats = {}

        if test_type == "nl2sql":
            exact_matches = sum(
                1
                for r in results
                if r["success"] and r["evaluation"]["exact_match"]
            )
            avg_component_match = (
                sum(
                    r["evaluation"]["component_match_rate"]
                    for r in results
                    if r["success"]
                )
                / successful
                if successful > 0
                else 0
            )
            avg_similarity = (
                sum(r["evaluation"]["similarity"] for r in results if r["success"])
                / successful
                if successful > 0
                else 0
            )

            summary = {
                "test_type": test_type,
                "total_cases": total,
                "successful": successful,
                "failed": failed,
                "exact_match_count": exact_matches,
                "exact_match_rate": exact_matches / total if total > 0 else 0,
                "avg_component_match_rate": avg_component_match,
                "avg_similarity": avg_similarity,
            }

        else:  # nl2dsl
            exact_matches = sum(
                1
                for r in results
                if r["success"] and r["evaluation"]["exact_match"]
            )
            avg_component_match = (
                sum(
                    r["evaluation"]["component_match_rate"]
                    for r in results
                    if r["success"]
                )
                / successful
                if successful > 0
                else 0
            )
            avg_overall_accuracy = (
                sum(
                    r["evaluation"]["overall_accuracy"]
                    for r in results
                    if r["success"]
                )
                / successful
                if successful > 0
                else 0
            )

            # 计算各字段平均准确率
            field_accuracy_sum = {
                "metrics": 0,
                "dimensions": 0,
                "filters": 0,
                "having": 0,
            }
            for r in results:
                if r["success"]:
                    for field, acc in r["evaluation"]["field_accuracy"].items():
                        field_accuracy_sum[field] += acc

            avg_field_accuracy = {
                field: acc / successful if successful > 0 else 0
                for field, acc in field_accuracy_sum.items()
            }

            summary = {
                "test_type": test_type,
                "total_cases": total,
                "successful": successful,
                "failed": failed,
                "exact_match_count": exact_matches,
                "exact_match_rate": exact_matches / total if total > 0 else 0,
                "avg_component_match_rate": avg_component_match,
                "avg_overall_accuracy": avg_overall_accuracy,
                "avg_field_accuracy": avg_field_accuracy,
            }

        # 按类别和难度统计
        for r in results:
            if not r["success"]:
                continue

            category = r.get("category", "unknown")
            difficulty = r.get("difficulty", "unknown")

            if category not in category_stats:
                category_stats[category] = {"total": 0, "exact_match": 0}
            category_stats[category]["total"] += 1
            if r["evaluation"]["exact_match"]:
                category_stats[category]["exact_match"] += 1

            if difficulty not in difficulty_stats:
                difficulty_stats[difficulty] = {"total": 0, "exact_match": 0}
            difficulty_stats[difficulty]["total"] += 1
            if r["evaluation"]["exact_match"]:
                difficulty_stats[difficulty]["exact_match"] += 1

        # 计算各类别和难度的准确率
        for category, stats in category_stats.items():
            stats["accuracy"] = (
                stats["exact_match"] / stats["total"] if stats["total"] > 0 else 0
            )

        for difficulty, stats in difficulty_stats.items():
            stats["accuracy"] = (
                stats["exact_match"] / stats["total"] if stats["total"] > 0 else 0
            )

        report = {
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "category_stats": category_stats,
            "difficulty_stats": difficulty_stats,
            "detailed_results": results,
        }

        # 保存报告
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        return summary

    async def run_nl2sql_tests(self, output_dir: Path, limit: int | None = None) -> dict[str, Any]:
        """运行所有 NL2SQL 测试"""
        self.logger.info("=" * 80)
        self.logger.info("开始 NL2SQL 测试")
        self.logger.info("=" * 80)

        test_dir = tests_root / "nl2sql_tests"
        test_cases = self.load_test_cases(test_dir)

        # 限制测试用例数量
        if limit:
            test_cases = test_cases[:limit]
            self.logger.info(f"限制测试用例数: {limit}")

        results = []
        for i, test_case in enumerate(test_cases, 1):
            self.logger.info(f"进度: {i}/{len(test_cases)}")
            result = await self.run_nl2sql_test(test_case)
            results.append(result)

        # 生成报告
        output_path = output_dir / f"nl2sql_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        summary = self.generate_report("nl2sql", results, output_path)

        self.logger.info(f"NL2SQL 测试报告已保存: {output_path}")
        self.logger.info(f"测试总结:")
        self.logger.info(f"  总用例数: {summary['total_cases']}")
        self.logger.info(f"  成功: {summary['successful']}, 失败: {summary['failed']}")
        self.logger.info(f"  精确匹配率: {summary['exact_match_rate']:.2%}")
        self.logger.info(f"  组件匹配率: {summary['avg_component_match_rate']:.2%}")
        self.logger.info(f"  相似度: {summary['avg_similarity']:.2%}")

        return summary

    async def run_nl2dsl_tests(
        self, semantic_config: dict, output_dir: Path, limit: int | None = None
    ) -> dict[str, Any]:
        """运行所有 NL2DSL 测试"""
        self.logger.info("=" * 80)
        self.logger.info("开始 NL2DSL 测试")
        self.logger.info("=" * 80)

        test_dir = tests_root / "nl2dsl_tests"
        test_cases = self.load_test_cases(test_dir)

        # 限制测试用例数量
        if limit:
            test_cases = test_cases[:limit]
            self.logger.info(f"限制测试用例数: {limit}")

        results = []
        for i, test_case in enumerate(test_cases, 1):
            self.logger.info(f"进度: {i}/{len(test_cases)}")
            result = await self.run_nl2dsl_test(test_case, semantic_config)
            results.append(result)

        # 生成报告
        output_path = output_dir / f"nl2dsl_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        summary = self.generate_report("nl2dsl", results, output_path)

        self.logger.info(f"NL2DSL 测试报告已保存: {output_path}")
        self.logger.info(f"测试总结:")
        self.logger.info(f"  总用例数: {summary['total_cases']}")
        self.logger.info(f"  成功: {summary['successful']}, 失败: {summary['failed']}")
        self.logger.info(f"  精确匹配率: {summary['exact_match_rate']:.2%}")
        self.logger.info(f"  组件匹配率: {summary['avg_component_match_rate']:.2%}")
        self.logger.info(f"  综合准确率: {summary['avg_overall_accuracy']:.2%}")

        return summary


async def main():
    """主函数"""
    import argparse

    # 解析命令行参数
    parser = argparse.ArgumentParser(description="NL2SQL/NL2DSL 测试运行器")
    parser.add_argument(
        "--test-type",
        choices=["nl2sql", "nl2dsl", "both"],
        default="both",
        help="测试类型: nl2sql, nl2dsl, 或 both (默认: both)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制每种类型的测试用例数量（用于快速测试）",
    )
    args = parser.parse_args()

    # 配置
    API_BASE_URL = "http://127.0.0.1:5001"
    DATASOURCE_ID = None  # 设置你的数据源 ID，或者 None 使用默认数据源

    # 语义配置（用于 NL2DSL 测试）
    SEMANTIC_CONFIG = {
        "metrics": [
            {"name": "cluster_count", "expression": "COUNT(DISTINCT db_cluster.cluster_id)", "table": "db_cluster"},
            {"name": "instance_count", "expression": "COUNT(DISTINCT db_cluster_instance.ins_id)", "table": "db_cluster_instance"},
            {"name": "cluster_group_count", "expression": "COUNT(DISTINCT db_cluster_group.cluster_group_id)", "table": "db_cluster_group"},
        ],
        "dimensions": [
            {"name": "cluster_db_type", "column": "db_cluster.cluster_db_type", "table": "db_cluster"},
            {"name": "cluster_ha_type", "column": "db_cluster.cluster_ha_type", "table": "db_cluster"},
            {"name": "cluster_role", "column": "db_cluster.cluster_role", "table": "db_cluster"},
            {"name": "ins_type", "column": "db_cluster_instance.ins_type", "table": "db_cluster_instance"},
            {"name": "ins_role", "column": "db_cluster_instance.ins_role", "table": "db_cluster_instance"},
            {"name": "ins_status", "column": "db_cluster_instance.ins_status", "table": "db_cluster_instance"},
            {"name": "cluster_group_name", "column": "db_cluster_group.cluster_group_name", "table": "db_cluster_group"},
        ],
    }

    # 创建输出目录
    output_dir = tests_root / "reports"
    output_dir.mkdir(exist_ok=True)

    # 初始化日志
    logger = setup_logger(output_dir)
    logger.info("NL2SQL/NL2DSL 测试开始")
    logger.info(f"API Base URL: {API_BASE_URL}")
    logger.info(f"Datasource ID: {DATASOURCE_ID}")
    logger.info(f"测试类型: {args.test_type}")
    if args.limit:
        logger.info(f"限制用例数: {args.limit}")

    # 创建测试运行器
    runner = TestRunner(
        api_base_url=API_BASE_URL,
        datasource_id=DATASOURCE_ID,
        logger=logger
    )

    nl2sql_summary = None
    nl2dsl_summary = None

    # 根据参数运行相应的测试
    if args.test_type in ["nl2sql", "both"]:
        nl2sql_summary = await runner.run_nl2sql_tests(output_dir, limit=args.limit)

    if args.test_type in ["nl2dsl", "both"]:
        nl2dsl_summary = await runner.run_nl2dsl_tests(SEMANTIC_CONFIG, output_dir, limit=args.limit)

    # 最终总结
    logger.info("")
    logger.info("=" * 80)
    logger.info("最终测试总结")
    logger.info("=" * 80)

    if nl2sql_summary:
        logger.info(f"\nNL2SQL:")
        logger.info(f"  总用例数: {nl2sql_summary['total_cases']}")
        logger.info(f"  成功: {nl2sql_summary['successful']}, 失败: {nl2sql_summary['failed']}")
        logger.info(f"  精确匹配率: {nl2sql_summary['exact_match_rate']:.2%} ({nl2sql_summary['exact_match_count']}/{nl2sql_summary['total_cases']})")
        logger.info(f"  组件匹配率: {nl2sql_summary['avg_component_match_rate']:.2%}")
        logger.info(f"  相似度: {nl2sql_summary['avg_similarity']:.2%}")
        if nl2sql_summary.get('llm_correct_count') is not None:
            logger.info(f"  LLM评估正确率: {nl2sql_summary['llm_correct_rate']:.2%} ({nl2sql_summary['llm_correct_count']}/{nl2sql_summary['total_cases']})")

    if nl2dsl_summary:
        logger.info(f"\nNL2DSL:")
        logger.info(f"  总用例数: {nl2dsl_summary['total_cases']}")
        logger.info(f"  成功: {nl2dsl_summary['successful']}, 失败: {nl2dsl_summary['failed']}")
        logger.info(f"  精确匹配率: {nl2dsl_summary['exact_match_rate']:.2%} ({nl2dsl_summary['exact_match_count']}/{nl2dsl_summary['total_cases']})")
        logger.info(f"  组件匹配率: {nl2dsl_summary['avg_component_match_rate']:.2%}")
        logger.info(f"  综合准确率: {nl2dsl_summary['avg_overall_accuracy']:.2%}")

    logger.info("")
    logger.info("测试完成！")

    return {"nl2sql": nl2sql_summary, "nl2dsl": nl2dsl_summary}


if __name__ == "__main__":
    asyncio.run(main())
