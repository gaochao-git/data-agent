"""
本地 HTTP 查询模拟服务。

用途：
- 模拟 chat_data_app 的 HTTP 异步查询接口
- 便于本地联调 hive + http 查询链路

运行方式：
cd server && python scripts/mock_http_query_service.py

默认接口：
- POST /mock-http-query/submit
- GET /mock-http-query/status/{task_id}
- POST /meta/tables
- POST /meta/schema

默认端口：
- 18081
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(title="Mock HTTP Query Service", version="1.0.0")


class SubmitQueryRequest(BaseModel):
    """提交查询请求。"""

    sql: str = Field(..., description="查询 SQL")
    namespace: str = Field(..., description="执行 namespace")
    database: str = Field(default="", description="数据库名")
    orgCode: str = Field(default="", description="机构编码")
    orgName: str = Field(default="", description="机构名称")
    limit: int = Field(default=100, description="行数限制")
    user_id: str = Field(..., description="发起人用户ID")


class MetaRequest(BaseModel):
    """元数据请求。"""

    source_type: str = Field(default="hive", description="数据源类型")
    namespace: str = Field(..., description="执行 namespace")
    orgCode: str = Field(default="", description="机构编码")
    orgName: str = Field(default="", description="机构名称")
    host: str = Field(..., description="主机地址")
    port: int = Field(default=10000, description="端口")
    database: str = Field(default="default", description="数据库名")
    table_names: list[str] | None = Field(default=None, description="指定表名")


@dataclass
class MockQueryTask:
    """内存中的模拟任务。"""

    task_id: str
    sql: str
    namespace: str
    database: str
    orgCode: str
    orgName: str
    limit: int
    user_id: str
    created_at: float = field(default_factory=time.time)
    duration_seconds: float = 2.0
    should_fail: bool = False
    error_message: str = ""


TASK_STORE: dict[str, MockQueryTask] = {}

MOCK_SCHEMA_TABLES: list[dict[str, Any]] = [
    {
        "table_name": "orders",
        "description": "订单表",
        "columns": [
            {
                "name": "order_id",
                "type": "string",
                "hive_type": "string",
                "description": "订单ID",
                "is_partition": False,
            },
            {
                "name": "user_id",
                "type": "int",
                "hive_type": "int",
                "description": "用户ID",
                "is_partition": False,
            },
            {
                "name": "amount",
                "type": "decimal(12,2)",
                "hive_type": "decimal(12,2)",
                "description": "订单金额",
                "is_partition": False,
            },
            {
                "name": "order_time",
                "type": "timestamp",
                "hive_type": "timestamp",
                "description": "下单时间",
                "is_partition": False,
            },
            {
                "name": "dt",
                "type": "string",
                "hive_type": "string",
                "description": "分区日期",
                "is_partition": True,
            },
        ],
    },
    {
        "table_name": "order_items",
        "description": "订单明细表",
        "columns": [
            {
                "name": "item_id",
                "type": "string",
                "hive_type": "string",
                "description": "明细ID",
                "is_partition": False,
            },
            {
                "name": "order_id",
                "type": "string",
                "hive_type": "string",
                "description": "订单ID",
                "is_partition": False,
            },
            {
                "name": "product_id",
                "type": "int",
                "hive_type": "int",
                "description": "商品ID",
                "is_partition": False,
            },
            {
                "name": "quantity",
                "type": "int",
                "hive_type": "int",
                "description": "销量",
                "is_partition": False,
            },
        ],
    },
    {
        "table_name": "products",
        "description": "商品表",
        "columns": [
            {
                "name": "product_id",
                "type": "int",
                "hive_type": "int",
                "description": "商品ID",
                "is_partition": False,
            },
            {
                "name": "product_name",
                "type": "string",
                "hive_type": "string",
                "description": "商品名称",
                "is_partition": False,
            },
            {
                "name": "category_id",
                "type": "int",
                "hive_type": "int",
                "description": "类目ID",
                "is_partition": False,
            },
        ],
    },
]


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="启动本地 HTTP 查询模拟服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=18081, help="监听端口，默认 18081")
    return parser.parse_args()


def build_task_id() -> str:
    """生成模拟任务 ID。"""
    return f"task_{uuid.uuid4().hex[:12]}"


def should_fail_query(sql: str, namespace: str) -> tuple[bool, str]:
    """根据输入决定是否返回失败结果。"""
    normalized_sql = str(sql or "").lower()
    normalized_namespace = str(namespace or "").lower()
    if "mock_fail" in normalized_sql or "mock_fail" in normalized_namespace:
        return True, "模拟查询失败，请检查 SQL 或 namespace"
    return False, ""


def detect_duration_seconds(sql: str) -> float:
    """根据 SQL 关键字推导模拟执行耗时。"""
    normalized_sql = str(sql or "").lower()
    if "mock_slow" in normalized_sql:
        return 6.0
    if "mock_fast" in normalized_sql:
        return 0.5
    return 2.0


def normalize_status(task: MockQueryTask) -> str:
    """根据创建时间返回当前模拟状态。"""
    elapsed = time.time() - task.created_at
    if elapsed < 0.5:
        return "pending"
    if elapsed < task.duration_seconds:
        return "running"
    if task.should_fail:
        return "failed"
    return "succeeded"


def extract_limit(sql: str, fallback_limit: int) -> int:
    """简单解析 SQL 中的 LIMIT。"""
    normalized_sql = str(sql or "").lower()
    marker = " limit "
    if marker not in normalized_sql:
        return max(1, int(fallback_limit or 100))
    try:
        return max(1, int(normalized_sql.rsplit(marker, 1)[1].strip(" ;")))
    except Exception:
        return max(1, int(fallback_limit or 100))


def build_mock_columns() -> list[str]:
    """构造模拟列。"""
    return ["namespace", "row_num", "sql_preview"]


def build_mock_rows(task: MockQueryTask) -> list[dict[str, Any]]:
    """构造模拟行数据。"""
    row_count = min(extract_limit(task.sql, task.limit), 5)
    sql_preview = task.sql.strip().replace("\n", " ")[:120]
    return [
        {
            "namespace": task.namespace,
            "row_num": index,
            "sql_preview": sql_preview,
        }
        for index in range(1, row_count + 1)
    ]


def build_success_payload(task: MockQueryTask) -> dict[str, Any]:
    """构造成功响应。"""
    rows = build_mock_rows(task)
    return {
        "code": 20000,
        "data": {
            "status": "succeeded",
            "result": {
                "columns": build_mock_columns(),
                "data": rows,
                "total_count": len(rows),
                "execution_time_ms": int(task.duration_seconds * 1000),
            },
        },
        "msg": "执行成功",
    }


def filter_schema_tables(table_names: list[str] | None = None) -> list[dict[str, Any]]:
    """按表名过滤模拟元数据。"""
    if not table_names:
        return MOCK_SCHEMA_TABLES
    selected_names = {str(table_name or "").strip() for table_name in table_names if str(table_name or "").strip()}
    return [table for table in MOCK_SCHEMA_TABLES if table["table_name"] in selected_names]


def get_task_or_raise(task_id: str) -> MockQueryTask:
    """获取任务，不存在则抛错。"""
    task = TASK_STORE.get(str(task_id or "").strip())
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return task


@app.post("/mock-http-query/submit")
async def submit_query(body: SubmitQueryRequest) -> dict[str, Any]:
    """提交模拟查询任务。"""
    task_id = build_task_id()
    fail_flag, fail_message = should_fail_query(body.sql, body.namespace)
    task = MockQueryTask(
        task_id=task_id,
        sql=body.sql,
        namespace=body.namespace,
        database=body.database,
        orgCode=body.orgCode,
        orgName=body.orgName,
        limit=body.limit,
        user_id=body.user_id,
        duration_seconds=detect_duration_seconds(body.sql),
        should_fail=fail_flag,
        error_message=fail_message,
    )
    TASK_STORE[task_id] = task
    logger.info(
        "创建模拟 HTTP 查询任务: task_id=%s namespace=%s database=%s orgCode=%s orgName=%s user_id=%s limit=%s",
        task_id,
        body.namespace,
        body.database,
        body.orgCode,
        body.orgName,
        body.user_id,
        body.limit,
    )
    return {"code": 20000, "data": {"task_id": task_id}, "msg": "提交成功"}


@app.get("/mock-http-query/status/{task_id}")
async def query_status(task_id: str) -> dict[str, Any]:
    """按路径变量查询任务状态。"""
    task = get_task_or_raise(task_id)
    status = normalize_status(task)
    if status == "succeeded":
        return build_success_payload(task)
    if status == "failed":
        return {
            "code": 50003,
            "data": {"status": "failed"},
            "msg": task.error_message or "模拟查询失败",
        }
    return {"code": 20000, "data": {"status": status}, "msg": "执行中"}


@app.post("/meta/tables")
async def list_meta_tables(body: MetaRequest) -> dict[str, Any]:
    """获取模拟表名列表。"""
    logger.info("获取模拟元数据表列表: source_type=%s namespace=%s orgCode=%s orgName=%s host=%s port=%s database=%s", body.source_type, body.namespace, body.orgCode, body.orgName, body.host, body.port, body.database)
    tables = [table["table_name"] for table in MOCK_SCHEMA_TABLES]
    return {"code": 20000, "msg": "获取成功", "data": {"tables": tables}}


@app.post("/meta/schema")
async def get_meta_schema(body: MetaRequest) -> dict[str, Any]:
    """获取模拟完整表结构。"""
    logger.info("获取模拟元数据表结构: source_type=%s namespace=%s orgCode=%s orgName=%s host=%s port=%s database=%s tables=%s", body.source_type, body.namespace, body.orgCode, body.orgName, body.host, body.port, body.database, body.table_names)
    tables = filter_schema_tables(body.table_names)
    return {"code": 20000, "msg": "同步成功", "data": {"tables": tables}}


async def _log_startup(host: str, port: int) -> None:
    """打印启动提示。"""
    await asyncio.sleep(0)
    logger.info("Mock HTTP Query Service 已启动: submit=http://%s:%s/mock-http-query/submit status=http://%s:%s/mock-http-query/status/{task_id} meta_tables=http://%s:%s/meta/tables meta_schema=http://%s:%s/meta/schema", host, port, host, port, host, port, host, port)


def main() -> None:
    """启动服务。"""
    args = parse_args()
    asyncio.run(_log_startup(args.host, args.port))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
