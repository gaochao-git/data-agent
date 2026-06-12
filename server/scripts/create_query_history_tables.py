"""
创建SQL查询历史表

运行方式：
python -m scripts.create_query_history_tables
"""
from utils.lang_util.database import get_engine
from utils.lang_util.models import Base, SqlQueryFavorite, ChatDataQueryTask, ChatDataQueryExecution
from utils.logger import get_logger
import asyncio

logger = get_logger(__name__)


async def create_tables():
    """创建查询历史和异步任务表"""
    try:
        engine = get_engine()

        # 创建表（异步）
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[
                SqlQueryFavorite.__table__,
                ChatDataQueryTask.__table__,
                ChatDataQueryExecution.__table__,
            ])

        logger.info("✅ 查询收藏/异步任务/执行明细表创建成功")
        logger.info(f"  - {SqlQueryFavorite.__tablename__}")
        logger.info(f"  - {ChatDataQueryTask.__tablename__}")
        logger.info(f"  - {ChatDataQueryExecution.__tablename__}")

    except Exception as e:
        logger.error(f"❌ 创建表失败: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(create_tables())
