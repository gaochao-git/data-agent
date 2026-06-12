"""
SQLAlchemy 数据模型定义
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Integer, String, DateTime, SmallInteger, Text, Index
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy import Column

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
    is_favorited: Mapped[int] = mapped_column(SmallInteger, default=0)
    delete_flag: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0-未删除, 1-已删除
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    update_time: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")


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
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")


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
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")


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
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")


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
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('idx_datasource_last_used', 'datasource_id', 'last_used'),
        Index('idx_user_datasource', 'user_id', 'datasource_id'),
    )

    def to_dict(self):
        """转换为字典"""
        import json
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
            except:
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
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('idx_fav_datasource_query_type', 'datasource_id', 'query_type'),
        Index('idx_fav_user_datasource', 'user_id', 'datasource_id'),
        Index('idx_fav_semantic_config_id', 'semantic_config_id'),
    )

    def to_dict(self):
        """转换为字典"""
        import json
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
            'source': 'favorite',
        }
        if self.builder_config:
            try:
                result['builder_config'] = json.loads(self.builder_config)
            except:
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
    update_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")

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
        }


class DatasourceGroup(Base):
    """数据源组表"""
    __tablename__ = "datasource_group"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    group_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    group_desc: Mapped[Optional[str]] = mapped_column(String(128))
    template_datasource_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    mapped_modules_json: Mapped[Optional[str]] = mapped_column(Text)
    is_default: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")


class Datasource(Base):
    """数据源表"""
    __tablename__ = "datasource"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    datasource_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    db_host: Mapped[str] = mapped_column(String(128), nullable=False)
    db_port: Mapped[int] = mapped_column(Integer, nullable=False)
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    datasource_desc: Mapped[Optional[str]] = mapped_column(String(128))
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('uniq_db_host_db_port_database_name', 'db_host', 'db_port', 'database_name', unique=True),
    )


class DatasourceGroupMember(Base):
    """数据源组成员表"""
    __tablename__ = "datasource_group_member"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    member_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    datasource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('uniq_group_id_datasource_id', 'group_id', 'datasource_id', unique=True),
    )


class DatasourceTable(Base):
    """数据源表结构缓存表"""
    __tablename__ = "datasource_table"

    id: Mapped[int] = mapped_column(AutoIncrementId, primary_key=True, autoincrement=True)
    group_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    table_names: Mapped[str] = mapped_column(String(128), nullable=False)
    table_desc: Mapped[Optional[str]] = mapped_column(String(128))
    columns_json: Mapped[Optional[str]] = mapped_column(Text)
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")

    __table_args__ = (
        Index('uniq_group_id_table_names', 'group_id', 'table_names', unique=True),
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
    sort_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    delete_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    create_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    update_by: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
