"""
SQLAlchemy 数据模型定义
"""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

from utils.logger import get_logger

logger = get_logger(__name__)

# SQLite 用 Integer（才能自增），MySQL 用 BigInteger
AutoIncrementId = BigInteger().with_variant(Integer, "sqlite")
MediumTextType = Text().with_variant(MEDIUMTEXT(), "mysql")


class Base(DeclarativeBase):
    """基类"""

    pass


class UserThread(Base):
    """用户会话表"""

    __tablename__ = "user_threads"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    thread_title: Mapped[str | None] = mapped_column(String(255), default="未命名会话")
    agent_id: Mapped[str | None] = mapped_column(String(255))
    datasource_id: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    is_favorited: Mapped[int] = mapped_column(SmallInteger, default=0)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")


class ChatDataConversationEvent(Base):
    """ChatData 会话的追加式原始事件历史。"""

    __tablename__ = "chat_data_conversation_event"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("conversation_id", "event_key", name="uniq_conv_event_key"),
        Index("idx_conv_event_seq", "conversation_id", "sequence_no"),
        Index("idx_conv_event_turn", "conversation_id", "turn_id"),
    )


class ChatDataAgentInbox(Base):
    """跨进程协调 ChatData 活跃运行和运行中追加消息。"""

    __tablename__ = "chat_data_agent_inbox"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    queue_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="next-step",
        server_default="next-step",
        comment="队列类型: next-step-下个模型步骤注入, next-turn-下一轮处理",
    )
    # 记录内容：消息行为追加消息文本，运行行为运行状态 JSON（_load_run_content 解析）
    record_content: Mapped[str] = mapped_column(MediumTextType, nullable=False, default="", server_default="")
    # 排队消息随行元数据（JSON 文本）：{file_selections, file_ids}。附件/引用只存引用，
    # 消费（续跑构建 HumanMessage → agent_stream 采纳）时才校验解析。
    record_meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_status: Mapped[str] = mapped_column(String(16), nullable=False)
    claimed_time: Mapped[datetime | None] = mapped_column(DateTime)
    consumed_time: Mapped[datetime | None] = mapped_column(DateTime)
    lease_expire_time: Mapped[datetime | None] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("record_type", "message_id", name="uniq_agent_inbox_msg"),
        Index("idx_agent_inbox_conv", "conversation_id", "user_id", "record_type", "record_status"),
        Index("idx_agent_inbox_run", "run_id", "record_type", "record_status"),
    )


class UploadedFile(Base):
    """上传文件表"""

    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    file_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(50), default="pending")
    parse_filename: Mapped[str | None] = mapped_column(String(255))
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")


class ChatDataDashboard(Base):
    """分析大屏表"""

    __tablename__ = "chat_data_dashboard"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    dashboard_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    dashboard_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dashboard_desc: Mapped[str | None] = mapped_column(String(1000))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    datasource_id: Mapped[str | None] = mapped_column(String(255))
    dashboard_theme: Mapped[str] = mapped_column(String(50), default="light")
    background_id: Mapped[str | None] = mapped_column(String(50))  # 背景图片ID
    is_public: Mapped[int] = mapped_column(SmallInteger, default=0)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")


class ChatDataDashboardPanel(Base):
    """大屏面板表"""

    __tablename__ = "chat_data_dashboard_panel"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    panel_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    dashboard_id: Mapped[str] = mapped_column(String(255), nullable=False)
    panel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    panel_sql: Mapped[str] = mapped_column(String(5000), nullable=False)
    chart_type: Mapped[str] = mapped_column(String(50), default="table")
    chart_config: Mapped[str | None] = mapped_column(String(2000))  # JSON 格式的图表配置
    datasource_id: Mapped[str | None] = mapped_column(String(255))  # 数据源ID
    member_datasource_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=""
    )  # 成员数据源ID
    # 布局信息 (react-grid-layout 格式)
    layout_x: Mapped[int] = mapped_column(Integer, default=0)
    layout_y: Mapped[int] = mapped_column(Integer, default=0)
    layout_w: Mapped[int] = mapped_column(Integer, default=6)
    layout_h: Mapped[int] = mapped_column(Integer, default=4)
    # 数据刷新
    refresh_interval: Mapped[int | None] = mapped_column(Integer)  # 刷新间隔（秒）
    # 来源信息
    source_thread_id: Mapped[str | None] = mapped_column(String(255))
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")


class SqlQueryHistory(Base):
    """SQL查询历史表"""

    __tablename__ = "sql_query_history"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    datasource_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(255), index=True)

    # 查询信息
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_title: Mapped[str | None] = mapped_column(String(255))
    query_type: Mapped[str | None] = mapped_column(String(20), default="sql")  # sql | builder
    builder_config: Mapped[str | None] = mapped_column(Text)  # JSON string for builder mode

    last_used: Mapped[datetime | None] = mapped_column(DateTime)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer)
    result_row_count: Mapped[int | None] = mapped_column(Integer)
    success_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    error_message: Mapped[str | None] = mapped_column(Text)

    # 统一元数据字段
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index("idx_datasource_last_used", "datasource_id", "last_used"),
        Index("idx_user_datasource", "user_id", "datasource_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        result = {
            "id": self.id,
            "title": self.query_title,
            "query": self.sql_text,
            "query_type": self.query_type or "sql",
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "execution_time_ms": self.execution_time_ms,
            "result_row_count": self.result_row_count,
            "success_flag": self.success_flag,
            "error_message": self.error_message,
            "source": "history",
        }
        # 解析 builder_config JSON
        if self.builder_config:
            try:
                result["builder_config"] = json.loads(self.builder_config)
            except (TypeError, ValueError, json.JSONDecodeError):
                result["builder_config"] = None
        return result


class SqlQueryFavorite(Base):
    """查询收藏表"""

    __tablename__ = "sql_query_favorite"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    datasource_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(255), index=True)
    semantic_config_id: Mapped[str | None] = mapped_column(String(255), index=True)

    query_type: Mapped[str] = mapped_column(String(20), nullable=False, default="sql")  # conversation | builder | sql
    favorite_title: Mapped[str] = mapped_column(String(255), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    builder_config: Mapped[str | None] = mapped_column(Text)  # JSON string for builder mode

    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index("idx_fav_datasource_query_type", "datasource_id", "query_type"),
        Index("idx_fav_user_datasource", "user_id", "datasource_id"),
        Index("idx_fav_semantic_config_id", "semantic_config_id"),
    )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        result = {
            "id": self.id,
            "datasource_id": self.datasource_id,
            "user_id": self.user_id,
            "semantic_config_id": self.semantic_config_id,
            "query_type": self.query_type,
            "favorite_title": self.favorite_title,
            "query_text": self.query_text,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
            "create_by": self.create_by,
            "create_by_name": self.create_by_name,
            "update_by": self.update_by,
            "update_by_name": self.update_by_name,
            "source": "favorite",
        }
        if self.builder_config:
            try:
                result["builder_config"] = json.loads(self.builder_config)
            except (TypeError, ValueError, json.JSONDecodeError):
                result["builder_config"] = None
        return result


class ChatDataQueryTask(Base):
    """ChatData 异步查询任务表"""

    __tablename__ = "chat_data_query_task"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sql", server_default="sql")
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    query_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sql", server_default="sql")
    task_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending", index=True
    )
    progress_text: Mapped[str | None] = mapped_column(String(255))
    request_payload: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    result_payload: Mapped[str | None] = mapped_column(MediumTextType)
    remote_task_payload: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_time: Mapped[datetime | None] = mapped_column(DateTime)
    finished_time: Mapped[datetime | None] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index("idx_query_task_type_task_status", "task_type", "task_status"),
        Index("idx_query_task_datasource_task_status", "datasource_id", "task_status"),
    )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        import json

        result_payload = None
        if self.result_payload:
            try:
                result_payload = json.loads(self.result_payload)
            except Exception as exc:
                logger.warning(
                    "查询任务 result_payload 解析 JSON 失败,按 None 返回: task_id=%s error=%s", self.task_id, exc
                )
                result_payload = None
        request_payload = None
        if self.request_payload:
            try:
                request_payload = json.loads(self.request_payload)
            except Exception as exc:
                logger.warning(
                    "查询任务 request_payload 解析 JSON 失败,按 None 返回: task_id=%s error=%s", self.task_id, exc
                )
                request_payload = None
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "datasource_id": self.datasource_id,
            "query_type": self.query_type,
            "status": self.task_status,
            "progress_text": self.progress_text,
            "request_payload": request_payload,
            "result_payload": result_payload,
            "remote_task_payload": self.remote_task_payload,
            "error_message": self.error_message,
            "started_time": self.started_time.isoformat() if self.started_time else None,
            "finished_time": self.finished_time.isoformat() if self.finished_time else None,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
            "create_by": self.create_by,
            "create_by_name": self.create_by_name,
            "update_by": self.update_by,
            "update_by_name": self.update_by_name,
        }


class ChatDataQueryExecution(Base):
    """ChatData SQL 执行明细表"""

    __tablename__ = "chat_data_query_execution"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    member_datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    member_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    executor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    sql_text: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    sql_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    remote_task_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    db_connection_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    execution_time_ms: Mapped[int | None] = mapped_column(Integer)
    result_row_count: Mapped[int | None] = mapped_column(Integer)
    result_payload: Mapped[str | None] = mapped_column(MediumTextType)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_time: Mapped[datetime | None] = mapped_column(DateTime)
    finished_time: Mapped[datetime | None] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("execution_id", name="uniq_execution_id"),
        Index("idx_execution_task_id", "task_id"),
        Index("idx_execution_user_ds_time", "user_id", "datasource_id", "create_time"),
        Index("idx_execution_ds_status", "datasource_id", "sql_status"),
    )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        import json

        result_payload = None
        if self.result_payload:
            try:
                result_payload = json.loads(self.result_payload)
            except Exception as exc:
                logger.warning(
                    "SQL 执行明细 result_payload 解析 JSON 失败,按 None 返回: execution_id=%s error=%s",
                    self.execution_id,
                    exc,
                )
                result_payload = None
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "datasource_id": self.datasource_id,
            "member_datasource_id": self.member_datasource_id,
            "member_name": self.member_name,
            "executor_type": self.executor_type,
            "query": self.sql_text,
            "sql_status": self.sql_status,
            "remote_task_id": self.remote_task_id,
            "db_connection_id": self.db_connection_id,
            "execution_time_ms": self.execution_time_ms,
            "result_row_count": self.result_row_count,
            "result_payload": result_payload,
            "error_message": self.error_message,
            "started_time": self.started_time.isoformat() if self.started_time else None,
            "finished_time": self.finished_time.isoformat() if self.finished_time else None,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
            "create_by": self.create_by,
            "create_by_name": self.create_by_name,
            "update_by": self.update_by,
            "update_by_name": self.update_by_name,
        }


class ChatDataSqlResultCache(Base):
    """ChatData SQL 查询结果缓存表"""

    __tablename__ = "chat_data_sql_result_cache"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    member_datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    executor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    sql_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    result_payload: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    result_row_count: Mapped[int | None] = mapped_column(Integer)
    expire_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_hit_time: Mapped[datetime | None] = mapped_column(DateTime)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("cache_key", name="uniq_cache_key"),
        Index("idx_expire_time", "expire_time"),
        Index("idx_ds_update", "datasource_id", "update_time"),
    )


class ChatDataSkill(Base):
    """ChatData 技能表"""

    __tablename__ = "chat_data_skill"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    skill_title: Mapped[str] = mapped_column(String(200), nullable=False, default="", server_default="")
    skill_desc: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    skill_category: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    skill_source: Mapped[str] = mapped_column(String(20), nullable=False, default="user", server_default="user")
    visibility_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="private", server_default="private"
    )
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    skill_publish_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    current_skill_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    owner_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("skill_id", name="uniq_skill_id"),
        UniqueConstraint("skill_name", name="uniq_skill_name"),
        Index("idx_skill_source", "skill_source", "skill_publish_status"),
        Index("idx_skill_owner", "owner_user_id", "skill_publish_status"),
    )


class ChatDataSkillVersion(Base):
    """ChatData 技能版本表"""

    __tablename__ = "chat_data_skill_version"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    skill_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    version_publish_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("skill_id", "skill_version_no", name="uniq_skill_version"),
        Index("idx_version_skill", "skill_id", "version_publish_status"),
    )


class ChatDataSkillFavorite(Base):
    """ChatData 技能收藏表"""

    __tablename__ = "chat_data_skill_favorite"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    favorite_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("favorite_user_id", "skill_id", name="uniq_fav_user_skill"),
        Index("idx_fav_skill", "skill_id"),
    )


class ChatDataEvalCaseSet(Base):
    """ChatData 评测测试集表"""

    __tablename__ = "chat_data_eval_case_set"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    case_set_id: Mapped[str] = mapped_column(String(64), nullable=False)
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    semantic_config_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    case_set_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    business_domain: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    case_set_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("case_set_id", name="uniq_eval_case_set_id"),
        Index("idx_eval_case_set_scene", "datasource_id", "semantic_config_id"),
        Index("idx_eval_case_set_status", "case_set_status", "update_time"),
    )


class ChatDataEvalCase(Base):
    """ChatData 评测用例表"""

    __tablename__ = "chat_data_eval_case"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    case_set_id: Mapped[str] = mapped_column(String(64), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nl_text: Mapped[str] = mapped_column(Text, nullable=False)
    gold_dsl_text: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    gold_sql_text: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    query_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="aggregation", server_default="aggregation"
    )
    case_difficulty: Mapped[str] = mapped_column(String(32), nullable=False, default="easy", server_default="easy")
    case_source: Mapped[str] = mapped_column(String(32), nullable=False, default="human", server_default="human")
    business_domain: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("case_set_id", "case_id", name="uniq_eval_case_set_case"),
        Index("idx_eval_case_set_id", "case_set_id"),
        Index("idx_eval_case_type", "query_type"),
        Index("idx_eval_case_source", "case_source"),
    )


class ChatDataEvalRun(Base):
    """ChatData 评测运行记录表"""

    __tablename__ = "chat_data_eval_run"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    case_set_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    semantic_config_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="live", server_default="live")
    run_status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", server_default="running")
    request_payload: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    summary_payload: Mapped[str | None] = mapped_column(MediumTextType)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    semantic_accuracy_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_time: Mapped[datetime | None] = mapped_column(DateTime)
    finished_time: Mapped[datetime | None] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("run_id", name="uniq_eval_run_id"),
        Index("idx_eval_run_scene", "datasource_id", "semantic_config_id", "create_time"),
        Index("idx_eval_run_status", "run_status", "create_time"),
    )


class ChatDataEvalRunCase(Base):
    """ChatData 评测单用例结果表"""

    __tablename__ = "chat_data_eval_run_case"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    run_case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", server_default="running")
    result_payload: Mapped[str | None] = mapped_column(MediumTextType)
    semantic_accuracy_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("run_case_id", name="uniq_eval_run_case_id"),
        UniqueConstraint("run_id", "case_id", name="uniq_eval_run_case"),
        Index("idx_eval_run_case_run", "run_id"),
        Index("idx_eval_run_case_case", "case_id"),
    )


class DatasourceGroup(Base):
    """数据源组表"""

    __tablename__ = "datasource_group"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    group_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    group_desc: Mapped[str | None] = mapped_column(String(128))
    query_context: Mapped[str | None] = mapped_column(Text)
    recommended_questions_json: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    template_source: Mapped[str] = mapped_column(String(256), nullable=False)
    member_sources_text: Mapped[str | None] = mapped_column(Text)
    mapped_modules_json: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")


class DatasourceTable(Base):
    """数据源表结构缓存表"""

    __tablename__ = "datasource_table"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    table_names: Mapped[str] = mapped_column(String(128), nullable=False)
    table_desc: Mapped[str | None] = mapped_column(String(128))
    table_business_desc: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    columns_json: Mapped[str | None] = mapped_column(Text)
    indexes_json: Mapped[str | None] = mapped_column(Text)
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (Index("uniq_group_id_table_names", "group_id", "table_names", unique=True),)


class DatasourceTableGroupRel(Base):
    """数据源表分组关系表"""

    __tablename__ = "datasource_table_group_rel"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    table_group_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (UniqueConstraint("group_id", "table_group_name", "table_name", name="uniq_grp_tbl_group"),)


class DatasourceColumnDict(Base):
    """数据源字段数据字典表"""

    __tablename__ = "datasource_column_dict"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    column_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    dict_json: Mapped[str | None] = mapped_column(Text)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (UniqueConstraint("group_id", "table_name", "column_name", name="uniq_grp_tbl_col"),)


class ChatDataSetting(Base):
    """ChatData 后台配置变量表：运行期参数的表级覆盖（覆盖行 > env > 代码默认）。

    只允许 SETTING_REGISTRY（chat_data_app/services/setting_service.py）注册的
    「可动态修改」类 env 变量进表；连接与凭据类留在 env。说明不落表——
    单一来源是注册表 spec.description。
    软删 + setting_key 全局唯一：重置（删除）后重新覆盖走 upsert 复用原行
    （翻回 delete_flag=0 并更新值），不插入新行，避免唯一键冲突。
    """

    __tablename__ = "chat_data_setting"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(128), nullable=False)
    setting_value: Mapped[str] = mapped_column(String(2048), nullable=False)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (UniqueConstraint("setting_key", name="uniq_setting_key"),)


class ChatDataMetaGlobalEnum(Base):
    """全局枚举表：全系统枚举值域唯一权威（跨数据源、跨环境共享）。

    数据字典列通过 dict_json 中的 enum_ref 引用本表（二选一：引用后本地
    enum_values 必须为空）。enum_ref 格式：通用域为 "enum_name"，业务域为
    "business_domain:enum_name"。软删 + (business_domain, enum_name) 唯一：
    重新创建同名枚举走 upsert 复用原行（翻回 delete_flag=0 并更新内容），
    不插入新行；不同业务域的同名枚举互相隔离，防止跨域干扰与误引用。
    """

    __tablename__ = "chat_data_meta_global_enum"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    business_domain: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    enum_name: Mapped[str] = mapped_column(String(128), nullable=False)
    enum_desc: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    # 枚举值 JSON 文本：[{"value": "...", "label": "..."}]
    enum_json: Mapped[str] = mapped_column(Text, nullable=False)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (UniqueConstraint("business_domain", "enum_name", name="uniq_domain_enum_name"),)


class ChatDataAgentSchedule(Base):
    """智能体定时任务定义（用户在会话里创建，到点自动执行一次智能问数）。"""

    __tablename__ = "chat_data_agent_schedule"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # fixed-绑定指定会话（conversation_id 生效）；new_each_run-每次执行新建会话（conversation_id 为空）
    conversation_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="fixed", server_default="fixed")
    job_title: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    job_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(16), nullable=False, default="cron", server_default="cron")
    cron_expr: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    plan_time: Mapped[datetime | None] = mapped_column(DateTime)
    effective_end_time: Mapped[datetime | None] = mapped_column(DateTime)
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    next_trigger_time: Mapped[datetime | None] = mapped_column(DateTime)
    last_trigger_time: Mapped[datetime | None] = mapped_column(DateTime)
    options_json: Mapped[str | None] = mapped_column(Text)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("job_id", name="uniq_schedule_job_id"),
        Index("idx_schedule_user", "user_id", "delete_flag"),
        Index("idx_schedule_next_trigger", "enabled_flag", "delete_flag", "next_trigger_time"),
    )

    def to_dict(self) -> dict[str, Any]:
        """转换为前端字典。"""
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "create_by_name": self.create_by_name,
            "conversation_id": self.conversation_id,
            "conversation_mode": self.conversation_mode,
            "title": self.job_title,
            "prompt": self.job_prompt,
            "schedule_type": self.schedule_type,
            "cron_expr": self.cron_expr,
            "plan_time": self.plan_time.isoformat() if self.plan_time else None,
            "effective_end_time": self.effective_end_time.isoformat() if self.effective_end_time else None,
            "enabled_flag": self.enabled_flag,
            "next_trigger_time": self.next_trigger_time.isoformat() if self.next_trigger_time else None,
            "last_trigger_time": self.last_trigger_time.isoformat() if self.last_trigger_time else None,
            "datasource_id": self._options().get("datasource_id"),
            "options": self._options(),
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "update_time": self.update_time.isoformat() if self.update_time else None,
        }

    def _options(self) -> dict:
        try:
            value = json.loads(self.options_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}


class ChatDataAgentScheduleRun(Base):
    """智能体定时任务触发实例：每个计划触发时间一行，串联派发出的 Agent Run。"""

    __tablename__ = "chat_data_agent_schedule_run"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    plan_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # pending-待派发 claimed-已抢占 dispatched-已提交 Agent Run
    # succeeded/failed/cancelled-随 Run 终态收口 skipped-重试耗尽或窗口过期
    trigger_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="schedule", server_default="schedule")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    owner_node: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    error_message: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    claimed_time: Mapped[datetime | None] = mapped_column(DateTime)
    dispatched_time: Mapped[datetime | None] = mapped_column(DateTime)
    finished_time: Mapped[datetime | None] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("job_id", "plan_time", name="uniq_schedule_job_plan"),
        Index("idx_schedule_run_scan", "trigger_status", "plan_time"),
        Index("idx_schedule_run_job", "job_id", "create_time"),
        Index("idx_schedule_run_user", "user_id", "trigger_status"),
    )

    def to_dict(self, run_status: str | None = None):
        """转换为前端字典；run_status 为关联 Agent Run 的状态（可空）。"""
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "plan_time": self.plan_time.isoformat() if self.plan_time else None,
            "status": self.trigger_status,
            "trigger_type": self.trigger_type,
            "run_id": self.run_id,
            "run_status": run_status,
            "error_message": self.error_message,
            "claimed_time": self.claimed_time.isoformat() if self.claimed_time else None,
            "dispatched_time": self.dispatched_time.isoformat() if self.dispatched_time else None,
            "finished_time": self.finished_time.isoformat() if self.finished_time else None,
            "create_time": self.create_time.isoformat() if self.create_time else None,
        }


class ChatDataAgentScheduleControl(Base):
    """智能体定时任务全局控制表（固定单行 id=1）：后台一键暂停所有调度。"""

    __tablename__ = "chat_data_schedule_control"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    paused_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    pause_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    def to_dict(self) -> dict[str, Any]:
        """转换为前端字典。"""
        return {
            "paused_flag": self.paused_flag,
            "pause_reason": self.pause_reason,
            "update_time": self.update_time.isoformat() if self.update_time else None,
            "update_by_name": self.update_by_name,
        }


class SemanticConfig(Base):
    """语义配置表"""

    __tablename__ = "semantic_config"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    semantic_config_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    semantic_config_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    semantic_config_desc: Mapped[str | None] = mapped_column(String(128))
    business_notes: Mapped[str | None] = mapped_column(Text)
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    metrics_json: Mapped[str | None] = mapped_column(Text)
    dimensions_json: Mapped[str | None] = mapped_column(Text)
    relationships_json: Mapped[str | None] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default="public", server_default="public")
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")


class ChatDataPlugin(Base):
    """ChatData 插件状态表：启停以本表为唯一真值（替代磁盘 .disabled 标记）。"""

    __tablename__ = "chat_data_plugin"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    plugin_name: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    manifest_text: Mapped[str | None] = mapped_column(Text)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("plugin_name", name="uniq_plugin_name"),
        Index("idx_plugin_enabled_delete", "enabled_flag", "delete_flag"),
    )


class ChatDataOntology(Base):
    """本体表：本体实体（纯语义容器，不绑定数据源组）。

    本体是对象类型的导航/治理容器，对位 Palantir ontology——不绑定任何存储后端；
    对象类型的取数后端=各自映射指向的语义模型（可跨数据源组）。本体名全局唯一；
    删除为软删，挂有对象类型时禁删。2026-10-20 由「领域」更名并解耦组绑定。
    """

    __tablename__ = "chat_data_ontology"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    ontology_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    description_text: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (UniqueConstraint("ontology_name", name="uniq_ontology_name"),)


class ChatDataModelRegistry(Base):
    """ChatData 模型注册表：智能问数可用模型清单（页面化管理，替代 models.json 文件）。

    连接信息按模型行自带（base_url / api_key SM4 密文），只以模型行为准，
    无环境变量回退；api_key 管理端只接受密文形态（base64:base64）。
    除身份/连接/状态外的全部模型参数收在 params_json
    （JSON 文本），扩参不改表。
    """

    __tablename__ = "chat_data_model_registry"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    # 注册键（唯一引用标识，服务端自动生成）：会话/定时任务/管理路由都以它定位模型行；
    # 与请求名解耦后，同一 model_id 可接多个供应商（base_url/key 不同的多行）
    model_key: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    model_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    provider: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    api_key_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    # 模型参数集（JSON 文本）：temperature/context_window/max_output_tokens/vlm/
    # thinking{...}/extra_body/... 及后续新增参数都进该字段，扩参不改表结构。
    params_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    is_default: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    remark_text: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("model_key", name="uniq_model_key"),
        # 业务唯一性=供应商+请求名（活跃行内）；软删行 model_id 改写墓碑名不占用组合
        UniqueConstraint("provider", "model_id", "delete_flag", name="uniq_provider_model_del"),
        Index("idx_model_enabled_delete", "enabled_flag", "delete_flag"),
    )


class ChatDataOntologyObjectType(Base):
    """本体对象类型表：对象类型/属性/关系类型三等实体之一（属性一等实体见 Property 表）。

    语义模型绑定独立成映射表 chat_data_ontology_mapping（对齐 ossie
    概念层/映射层分离，「一源一对象类型」由映射表唯一索引约束），本表只存概念层
    元数据。主键=标识属性（is_identifier）组合，标题键=is_title 属性，均由属性
    表表达，本表不冗余存储。ontology_id 指向本体实体（纯导航归属），NULL=未挂本体。
    """

    __tablename__ = "chat_data_ontology_object_type"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    object_type_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    description_text: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    ontology_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, default=None)
    deprecation_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    deprecated_until: Mapped[datetime | None] = mapped_column(DateTime)
    successor_object_type_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("object_type_name", name="uniq_object_type_name"),
        Index("idx_object_type_status", "lifecycle_status", "delete_flag"),
        Index("idx_object_type_ontology_id", "ontology_id", "delete_flag"),
    )


class ChatDataOntologyProperty(Base):
    """本体属性表：属性一等实体（对齐 Palantir Property Type）。

    value_type 为内建类型（String/Integer/Decimal/Boolean/Date/DateTime）或
    "enum"（enum_ref 引用全局枚举表 chat_data_meta_global_enum，格式与列字典
    enum_ref 一致）。不存枚举值/单位/聚合口径：semantic 绑定的口径唯一权威在
    语义层，dataset 绑定的口径即属性自身元数据，防双记账。
    """

    __tablename__ = "chat_data_ontology_property"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    object_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    property_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    description_text: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    value_type: Mapped[str] = mapped_column(String(32), nullable=False, default="String", server_default="String")
    enum_ref: Mapped[str] = mapped_column(String(260), nullable=False, default="", server_default="")
    render_hints_json: Mapped[str | None] = mapped_column(Text)
    # 派生属性推导定义（JSON 文本，NULL=普通存储属性）。对齐 Palantir derived property：
    # 查询时计算、不落存储；结构 {kind: aggregate|select, relation_type_name, operator?, target_property?}。
    derivation_json: Mapped[str | None] = mapped_column(Text)
    is_identifier: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    is_title: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (UniqueConstraint("object_type_id", "property_name", name="uniq_object_type_property"),)


class ChatDataOntologyRelationType(Base):
    """本体关系类型表：对齐 Palantir Link Type。

    基数仅 many_to_one/one_to_one（多对多走 junction 对象类型 + 两条 many_to_one
    表达）。from_columns/to_columns 为等长数组 JSON 文本，顺序一一对应，目标侧
    列必须引用对方标识属性。lifecycle_status 随对象类型级联：对象类型降级自动同步，升级手动确认。
    """

    __tablename__ = "chat_data_ontology_relation_type"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    relation_type_name: Mapped[str] = mapped_column(String(128), nullable=False)
    from_object_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    to_object_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_columns_json: Mapped[str | None] = mapped_column(Text)
    to_columns_json: Mapped[str | None] = mapped_column(Text)
    multiplicity_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="many_to_one", server_default="many_to_one"
    )
    from_display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    to_display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    from_verbalize: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    to_verbalize: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    description_text: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("relation_type_name", name="uniq_relation_type_name"),
        Index("idx_relation_from", "from_object_type_id", "delete_flag"),
        Index("idx_relation_to", "to_object_type_id", "delete_flag"),
    )


class ChatDataOntologyActionType(Base):
    """本体 Action Type：对齐 Palantir Action Type 的定义层（参数 + 编辑契约）。

    当前阶段只存定义，不执行写回；执行/审计/副作用在对象写路径落地后接入。
    parameter_schema_json 存参数数组，edit_schema_json 存编辑数组，均为 JSON 文本
    （不用 MySQL JSON 类型）。动作类型名全局唯一，软删后同名重建复用原行并回到 draft。
    """

    __tablename__ = "chat_data_ontology_action_type"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    action_type_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    description_text: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    object_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parameter_schema_json: Mapped[str | None] = mapped_column(Text)
    edit_schema_json: Mapped[str | None] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("action_type_name", name="uniq_action_type_name"),
        Index("idx_action_type_object", "object_type_id", "delete_flag"),
    )


class ChatDataOntologyObjectEdit(Base):
    """本体对象写覆盖层：Action 执行后的属性覆盖值（append-only）。

    当前没有对象存储，Action 不改写源库/语义模型数据，而是追加 property override；
    对象读取按 (object_type_id, object_key, property_name) 取最新一条覆盖值合并。
    """

    __tablename__ = "chat_data_ontology_object_edit"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    object_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    property_name: Mapped[str] = mapped_column(String(128), nullable=False)
    property_value_json: Mapped[str | None] = mapped_column(Text)
    action_execution_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    action_type_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index("idx_object_edit_lookup", "object_type_id", "object_key", "property_name"),
        Index("idx_object_edit_execution", "action_execution_id"),
    )


class ChatDataOntologyActionExecution(Base):
    """Action 执行审计：一次执行的参数/解析后的编辑/操作者；当前不做回滚与副作用。"""

    __tablename__ = "chat_data_ontology_action_execution"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    action_type_name: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    parameters_json: Mapped[str | None] = mapped_column(Text)
    edits_json: Mapped[str | None] = mapped_column(Text)
    execution_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="succeeded", server_default="succeeded"
    )
    error_message: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index("idx_action_execution_type", "action_type_name", "create_time"),
        Index("idx_action_execution_object", "object_type_id", "object_key"),
    )


# ---------------------------------------------------------------------------
# Logical Dataset：语义层与本体层共享的逻辑数据资产
# ---------------------------------------------------------------------------


class ChatDataLogicalDataset(Base):
    """逻辑数据集：稳定身份 + schema + 主键 + 当前版本指针。

    Dataset 是逻辑资产，不直接等于物理表；物理落点由 binding 表按 member 数据源描述。
    语义模型和本体对象类型都只引用 dataset_id，不互相引用。
    """

    __tablename__ = "chat_data_logical_dataset"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    dataset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    description_text: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    primary_key_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    current_version_id: Mapped[int | None] = mapped_column(BigInteger)
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    # Palantir Pipeline stage：source（数据源直注册）/ cleaned / enriched（Pipeline 产出）
    dataset_layer: Mapped[str] = mapped_column(String(16), nullable=False, default="source", server_default="source")
    # datasource（从数据源注册）/ pipeline（Pipeline build 产出）
    dataset_origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default="datasource", server_default="datasource"
    )
    delete_flag: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("dataset_name", "delete_flag", name="uniq_logical_dataset_name_del"),
        Index("idx_logical_dataset_status", "lifecycle_status", "delete_flag"),
        Index("idx_logical_dataset_layer", "dataset_layer", "dataset_origin", "delete_flag"),
    )


class ChatDataDatasetBranch(Base):
    """Dataset 分支：main / feature / scenario；所有版本归属于某个分支。"""

    __tablename__ = "chat_data_dataset_branch"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    branch_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_version_id: Mapped[int | None] = mapped_column(BigInteger)
    is_default: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    delete_flag: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("dataset_id", "branch_name", "delete_flag", name="uniq_dataset_branch_del"),
        Index("idx_dataset_branch_dataset", "dataset_id", "delete_flag"),
    )


class ChatDataDatasetTransaction(Base):
    """Dataset 事务：version 只能由 committed transaction 产生。"""

    __tablename__ = "chat_data_dataset_transaction"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", server_default="open")
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    error_message: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    committed_at: Mapped[datetime | None] = mapped_column(DateTime)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("transaction_id", name="uniq_dataset_transaction_id"),
        Index("idx_dataset_transaction_dataset", "dataset_id", "branch_id", "transaction_status"),
    )


class ChatDataDatasetVersion(Base):
    """Dataset 不可变版本：schema + 来源 transaction + 版本号。"""

    __tablename__ = "chat_data_dataset_version"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    branch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    schema_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    version_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="committed", server_default="committed"
    )
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("version_id", name="uniq_dataset_version_id"),
        UniqueConstraint("dataset_id", "branch_id", "version_no", name="uniq_dataset_version_no"),
        Index("idx_dataset_version_dataset", "dataset_id", "branch_id", "version_status"),
    )


class ChatDataDatasetBinding(Base):
    """Dataset 到成员数据源物理表的绑定；同一 dataset 可绑定多个 member。"""

    __tablename__ = "chat_data_dataset_binding"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    datasource_group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    member_datasource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    physical_table: Mapped[str] = mapped_column(String(256), nullable=False)
    binding_status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    delete_flag: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("dataset_id", "member_datasource_id", "delete_flag", name="uniq_dataset_binding_member"),
        Index("idx_dataset_binding_dataset", "dataset_id", "delete_flag"),
    )


class ChatDataDatasetLineageEdge(Base):
    """Dataset 版本血缘边；operation/run_id 记录产生方。"""

    __tablename__ = "chat_data_dataset_lineage_edge"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    from_version_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    to_version_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    operation: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    edge_metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index("idx_dataset_lineage_from", "from_version_id"),
        Index("idx_dataset_lineage_to", "to_version_id"),
    )


class ChatDataPipelineDefinition(Base):
    """Pipeline 定义头：稳定 pipeline_id + 当前定义版本指针（节点/边按版本只追加）。"""

    __tablename__ = "chat_data_pipeline_definition"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    pipeline_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    description_text: Mapped[str] = mapped_column(String(1024), nullable=False, default="", server_default="")
    target_layer: Mapped[str] = mapped_column(String(16), nullable=False, default="cleaned", server_default="cleaned")
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    current_version_no: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("pipeline_id", name="uniq_pipeline_id"),
        UniqueConstraint("pipeline_name", "delete_flag", name="uniq_pipeline_name_del"),
        Index("idx_pipeline_definition_status", "lifecycle_status", "delete_flag"),
    )


class ChatDataPipelineNode(Base):
    """Pipeline 节点：按 (pipeline_id, pipeline_version) 只追加，不覆盖历史版本。"""

    __tablename__ = "chat_data_pipeline_node"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    pipeline_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    node_type: Mapped[str] = mapped_column(String(16), nullable=False)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("pipeline_id", "pipeline_version", "node_id", name="uniq_pipeline_node_version"),
        Index("idx_pipeline_node_pipeline", "pipeline_id", "pipeline_version"),
    )


class ChatDataPipelineEdge(Base):
    """Pipeline 边：按 (pipeline_id, pipeline_version) 只追加。"""

    __tablename__ = "chat_data_pipeline_edge"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    pipeline_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    edge_id: Mapped[str] = mapped_column(String(64), nullable=False)
    from_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    to_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("pipeline_id", "pipeline_version", "edge_id", name="uniq_pipeline_edge_version"),
        Index("idx_pipeline_edge_pipeline", "pipeline_id", "pipeline_version"),
    )


class ChatDataPipelineRun(Base):
    """Pipeline 手动 build 运行记录：run_id 稳定标识，关联输出 Dataset。"""

    __tablename__ = "chat_data_pipeline_run"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default="running")
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual", server_default="manual")
    output_dataset_id: Mapped[int | None] = mapped_column(BigInteger)
    output_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    error_message: Mapped[str] = mapped_column(String(1000), nullable=False, default="", server_default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("run_id", name="uniq_pipeline_run_id"),
        Index("idx_pipeline_run_pipeline", "pipeline_id", "pipeline_version"),
        Index("idx_pipeline_run_status", "run_status", "create_time"),
    )


class ChatDataOntologySourceBinding(Base):
    """对象类型的数据源绑定：指向 Logical Dataset，不引用语义模型。"""

    __tablename__ = "chat_data_ontology_source_binding"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    object_type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    column_mappings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    delete_flag: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("object_type_id", "delete_flag", name="uniq_source_binding_object"),
        Index("idx_source_binding_dataset", "dataset_id", "delete_flag"),
    )


class ChatDataDatasetSemanticLink(Base):
    """Dataset ↔ 语义模型关联（Ossie mapping 的简化形态）。

    只存引用与列对照，不存指标定义：指标口径的唯一权威仍在语义层。
    - column_mappings_json: Dataset 逻辑列 → 语义维度名/字段名
    - metric_scope_json: 可选，允许在本体作用域执行的指标名白名单（空 = 该模型全部指标）
    """

    __tablename__ = "chat_data_dataset_semantic_link"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    dataset_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    semantic_config_id: Mapped[str] = mapped_column(String(128), nullable=False)
    datasource_group_id: Mapped[str] = mapped_column(String(128), nullable=False)
    table_name: Mapped[str] = mapped_column(String(256), nullable=False)
    column_mappings_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    metric_scope_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    delete_flag: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("dataset_id", "semantic_config_id", "delete_flag", name="uniq_dataset_semantic_link"),
        Index("idx_dataset_semantic_link_dataset", "dataset_id", "delete_flag"),
        Index("idx_dataset_semantic_link_group", "datasource_group_id", "delete_flag"),
    )
