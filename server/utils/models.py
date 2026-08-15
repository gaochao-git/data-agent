"""
SQLAlchemy 数据模型定义
"""

from datetime import datetime
import json
from typing import Optional

from sqlalchemy import BigInteger, Integer, String, DateTime, SmallInteger, Text, Index, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

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
    thread_title: Mapped[Optional[str]] = mapped_column(String(255), default="未命名会话")
    agent_id: Mapped[Optional[str]] = mapped_column(String(255))
    datasource_id: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    is_favorited: Mapped[int] = mapped_column(SmallInteger, default=0)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
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
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
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
    owner_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    queue_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="next-step",
        server_default="next-step",
        comment="队列类型: next-step-下个模型步骤注入, next-turn-下一轮处理",
    )
    content: Mapped[str] = mapped_column(MediumTextType, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    claimed_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    consumed_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    lease_expire_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("record_type", "message_id", name="uniq_agent_inbox_msg"),
        Index("idx_agent_inbox_conv", "conversation_id", "user_id", "record_type", "status"),
        Index("idx_agent_inbox_run", "run_id", "record_type", "status"),
    )


class ChatDataAgentRunEvent(Base):
    """后台 Run 的可重放 SSE 事件。"""

    __tablename__ = "chat_data_agent_run_event"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint("run_id", "event_id", name="uniq_agent_run_event"),
        Index("idx_agent_run_event_seq", "run_id", "sequence_no"),
        Index("idx_agent_run_event_conv", "conversation_id", "user_id", "create_time"),
    )


class UploadedFile(Base):
    """上传文件表"""
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    file_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_dir: Mapped[str] = mapped_column(String(500), nullable=False)
    parse_status: Mapped[str] = mapped_column(String(50), default="pending")
    parse_filename: Mapped[Optional[str]] = mapped_column(String(255))
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
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
    dashboard_desc: Mapped[Optional[str]] = mapped_column(String(1000))
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    datasource_id: Mapped[Optional[str]] = mapped_column(String(255))
    dashboard_theme: Mapped[str] = mapped_column(String(50), default="light")
    background_id: Mapped[Optional[str]] = mapped_column(String(50))  # 背景图片ID
    is_public: Mapped[int] = mapped_column(SmallInteger, default=0)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
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
    chart_config: Mapped[Optional[str]] = mapped_column(String(2000))  # JSON 格式的图表配置
    datasource_id: Mapped[Optional[str]] = mapped_column(String(255))  # 数据源ID
    member_datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")  # 成员数据源ID
    # 布局信息 (react-grid-layout 格式)
    layout_x: Mapped[int] = mapped_column(Integer, default=0)
    layout_y: Mapped[int] = mapped_column(Integer, default=0)
    layout_w: Mapped[int] = mapped_column(Integer, default=6)
    layout_h: Mapped[int] = mapped_column(Integer, default=4)
    # 数据刷新
    refresh_interval: Mapped[Optional[int]] = mapped_column(Integer)  # 刷新间隔（秒）
    # 来源信息
    source_thread_id: Mapped[Optional[str]] = mapped_column(String(255))
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")


class SqlQueryHistory(Base):
    """SQL查询历史表"""
    __tablename__ = 'sql_query_history'

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    datasource_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    # 查询信息
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_title: Mapped[Optional[str]] = mapped_column(String(255))
    query_type: Mapped[Optional[str]] = mapped_column(String(20), default='sql')  # sql | builder
    builder_config: Mapped[Optional[str]] = mapped_column(Text)  # JSON string for builder mode

    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime)
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    result_row_count: Mapped[Optional[int]] = mapped_column(Integer)
    success_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # 统一元数据字段
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('idx_datasource_last_used', 'datasource_id', 'last_used'),
        Index('idx_user_datasource', 'user_id', 'datasource_id'),
    )

    def to_dict(self):
        """转换为字典"""
        result = {
            'id': self.id,
            'title': self.query_title,
            'query': self.sql_text,
            'query_type': self.query_type or 'sql',
            'last_used': self.last_used.isoformat() if self.last_used else None,
            'execution_time_ms': self.execution_time_ms,
            'result_row_count': self.result_row_count,
            'success_flag': self.success_flag,
            'error_message': self.error_message,
            'source': 'history'
        }
        # 解析 builder_config JSON
        if self.builder_config:
            try:
                result['builder_config'] = json.loads(self.builder_config)
            except (TypeError, ValueError, json.JSONDecodeError):
                result['builder_config'] = None
        return result


class SqlQueryFavorite(Base):
    """查询收藏表"""
    __tablename__ = 'sql_query_favorite'

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    datasource_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    semantic_config_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    query_type: Mapped[str] = mapped_column(String(20), nullable=False, default='sql')  # conversation | builder | sql
    favorite_title: Mapped[str] = mapped_column(String(255), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    builder_config: Mapped[Optional[str]] = mapped_column(Text)  # JSON string for builder mode

    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('idx_fav_datasource_query_type', 'datasource_id', 'query_type'),
        Index('idx_fav_user_datasource', 'user_id', 'datasource_id'),
        Index('idx_fav_semantic_config_id', 'semantic_config_id'),
    )

    def to_dict(self):
        """转换为字典"""
        result = {
            'id': self.id,
            'datasource_id': self.datasource_id,
            'user_id': self.user_id,
            'semantic_config_id': self.semantic_config_id,
            'query_type': self.query_type,
            'favorite_title': self.favorite_title,
            'query_text': self.query_text,
            'create_time': self.create_time.isoformat() if self.create_time else None,
            'update_time': self.update_time.isoformat() if self.update_time else None,
            'create_by': self.create_by,
            'create_by_name': self.create_by_name,
            'update_by': self.update_by,
            'update_by_name': self.update_by_name,
            'source': 'favorite',
        }
        if self.builder_config:
            try:
                result['builder_config'] = json.loads(self.builder_config)
            except (TypeError, ValueError, json.JSONDecodeError):
                result['builder_config'] = None
        return result


class ChatDataQueryTask(Base):
    """ChatData 异步查询任务表"""
    __tablename__ = "chat_data_query_task"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sql", server_default="sql")
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    query_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sql", server_default="sql")
    task_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending", index=True)
    progress_text: Mapped[Optional[str]] = mapped_column(String(255))
    request_payload: Mapped[str] = mapped_column(MediumTextType, nullable=False)
    result_payload: Mapped[Optional[str]] = mapped_column(MediumTextType)
    remote_task_payload: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('idx_query_task_type_task_status', 'task_type', 'task_status'),
        Index('idx_query_task_datasource_task_status', 'datasource_id', 'task_status'),
    )

    def to_dict(self):
        """转换为字典"""
        import json
        result_payload = None
        if self.result_payload:
            try:
                result_payload = json.loads(self.result_payload)
            except Exception:
                result_payload = None
        request_payload = None
        if self.request_payload:
            try:
                request_payload = json.loads(self.request_payload)
            except Exception:
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
    execution_time_ms: Mapped[Optional[int]] = mapped_column(Integer)
    result_row_count: Mapped[Optional[int]] = mapped_column(Integer)
    result_payload: Mapped[Optional[str]] = mapped_column(MediumTextType)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('execution_id', name='uniq_execution_id'),
        Index('idx_execution_task_id', 'task_id'),
        Index('idx_execution_user_ds_time', 'user_id', 'datasource_id', 'create_time'),
        Index('idx_execution_ds_status', 'datasource_id', 'sql_status'),
    )

    def to_dict(self):
        """转换为字典"""
        import json
        result_payload = None
        if self.result_payload:
            try:
                result_payload = json.loads(self.result_payload)
            except Exception:
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
    result_row_count: Mapped[Optional[int]] = mapped_column(Integer)
    expire_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_hit_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('cache_key', name='uniq_cache_key'),
        Index('idx_expire_time', 'expire_time'),
        Index('idx_ds_update', 'datasource_id', 'update_time'),
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
    skill_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="analyze", server_default="analyze")
    skill_source: Mapped[str] = mapped_column(String(20), nullable=False, default="user", server_default="user")
    visibility_type: Mapped[str] = mapped_column(String(20), nullable=False, default="private", server_default="private")
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    skill_publish_status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    current_skill_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    owner_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    skill_root_dir: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('skill_id', name='uniq_skill_id'),
        UniqueConstraint('skill_name', name='uniq_skill_name'),
        Index('idx_skill_source', 'skill_source', 'skill_publish_status'),
        Index('idx_skill_owner', 'owner_user_id', 'skill_publish_status'),
    )


class ChatDataSkillVersion(Base):
    """ChatData 技能版本表"""
    __tablename__ = "chat_data_skill_version"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    skill_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skill_version_dir: Mapped[str] = mapped_column(String(500), nullable=False, default="", server_default="")
    version_publish_status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('skill_id', 'skill_version_no', name='uniq_skill_version'),
        Index('idx_version_skill', 'skill_id', 'version_publish_status'),
    )


class ChatDataSkillFavorite(Base):
    """ChatData 技能收藏表"""
    __tablename__ = "chat_data_skill_favorite"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    favorite_user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    skill_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('favorite_user_id', 'skill_id', name='uniq_fav_user_skill'),
        Index('idx_fav_skill', 'skill_id'),
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
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('case_set_id', name='uniq_eval_case_set_id'),
        Index('idx_eval_case_set_scene', 'datasource_id', 'semantic_config_id'),
        Index('idx_eval_case_set_status', 'case_set_status', 'update_time'),
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
    query_type: Mapped[str] = mapped_column(String(32), nullable=False, default="aggregation", server_default="aggregation")
    case_difficulty: Mapped[str] = mapped_column(String(32), nullable=False, default="easy", server_default="easy")
    case_source: Mapped[str] = mapped_column(String(32), nullable=False, default="human", server_default="human")
    business_domain: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('case_set_id', 'case_id', name='uniq_eval_case_set_case'),
        Index('idx_eval_case_set_id', 'case_set_id'),
        Index('idx_eval_case_type', 'query_type'),
        Index('idx_eval_case_source', 'case_source'),
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
    summary_payload: Mapped[Optional[str]] = mapped_column(MediumTextType)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    semantic_accuracy_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('run_id', name='uniq_eval_run_id'),
        Index('idx_eval_run_scene', 'datasource_id', 'semantic_config_id', 'create_time'),
        Index('idx_eval_run_status', 'run_status', 'create_time'),
    )


class ChatDataEvalRunCase(Base):
    """ChatData 评测单用例结果表"""
    __tablename__ = "chat_data_eval_run_case"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    run_case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", server_default="running")
    result_payload: Mapped[Optional[str]] = mapped_column(MediumTextType)
    semantic_accuracy_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('run_case_id', name='uniq_eval_run_case_id'),
        UniqueConstraint('run_id', 'case_id', name='uniq_eval_run_case'),
        Index('idx_eval_run_case_run', 'run_id'),
        Index('idx_eval_run_case_case', 'case_id'),
    )


class DatasourceGroup(Base):
    """数据源组表"""
    __tablename__ = "datasource_group"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    group_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    group_desc: Mapped[Optional[str]] = mapped_column(String(128))
    query_context: Mapped[Optional[str]] = mapped_column(Text)
    recommended_questions_json: Mapped[Optional[str]] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    template_source: Mapped[str] = mapped_column(String(256), nullable=False)
    member_sources_text: Mapped[Optional[str]] = mapped_column(Text)
    mapped_modules_json: Mapped[Optional[str]] = mapped_column(Text)
    is_default: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
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
    table_desc: Mapped[Optional[str]] = mapped_column(String(128))
    table_business_desc: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    columns_json: Mapped[Optional[str]] = mapped_column(Text)
    indexes_json: Mapped[Optional[str]] = mapped_column(Text)
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('uniq_group_id_table_names', 'group_id', 'table_names', unique=True),
    )


class DatasourceTableGroupRel(Base):
    """数据源表分组关系表"""
    __tablename__ = "datasource_table_group_rel"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    table_group_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('group_id', 'table_group_name', 'table_name', name='uniq_grp_tbl_group'),
    )


class DatasourceColumnDict(Base):
    """数据源字段数据字典表"""
    __tablename__ = "datasource_column_dict"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    column_name: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    dict_json: Mapped[Optional[str]] = mapped_column(Text)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")

    __table_args__ = (
        UniqueConstraint('group_id', 'table_name', 'column_name', name='uniq_grp_tbl_col'),
    )


class SemanticConfig(Base):
    """语义配置表"""
    __tablename__ = "semantic_config"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    semantic_config_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    semantic_config_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    semantic_config_desc: Mapped[Optional[str]] = mapped_column(String(128))
    ontology_rules: Mapped[Optional[str]] = mapped_column(Text)
    enabled_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    metrics_json: Mapped[Optional[str]] = mapped_column(Text)
    dimensions_json: Mapped[Optional[str]] = mapped_column(Text)
    relationships_json: Mapped[Optional[str]] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default="public", server_default="public")
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    create_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by_name: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
