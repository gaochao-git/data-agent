"""
Dashboard 服务层

提供分析大屏的 CRUD 操作（异步）
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from utils.database import get_session
from utils.logger import get_logger
from utils.models import ChatDataDashboard, ChatDataDashboardPanel

from ...daos.dashboard_dao import DashboardDao

logger = get_logger(__name__)


@dataclass(frozen=True)
class PanelWritePayload:
    """面板写入内容的聚合对象（新增 / 更新共用）。

    面板属性字段（名称、SQL、图表配置、布局、刷新间隔、数据源绑定）成组出现且数量多，
    直接铺成服务方法形参会突破 AGENTS.md §3 的「形参 ≤13」红线，故收敛为单一对象。
    全部字段默认 ``None``：``update_panel`` 只写入显式提供的字段（保持局部更新语义），
    ``add_panel`` 的默认值（table / 4 / 4）在该方法内回落到 concrete 值。
    """

    name: str | None = None
    sql: str | None = None
    chart_type: str | None = None
    chart_config: dict | None = None
    layout_x: int | None = None
    layout_y: int | None = None
    layout_w: int | None = None
    layout_h: int | None = None
    refresh_interval: int | None = None
    source_thread_id: str | None = None
    datasource_id: str | None = None
    member_datasource_id: str | None = None


class DashboardService:
    """分析大屏服务（异步）"""

    def __init__(self, dao: DashboardDao | None = None) -> None:
        self.dao = dao or DashboardDao(session_context=lambda: get_session())

    async def create_dashboard(
        self,
        name: str,
        user_id: str,
        description: str = "",
        datasource_id: str | None = None,
        theme: str = "light",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """创建新的分析大屏"""
        dashboard_id = str(uuid.uuid4())
        now = datetime.now()
        dashboard = await self.dao.create_dashboard(
            dashboard_id=dashboard_id,
            name=name,
            user_id=user_id,
            description=description,
            datasource_id=datasource_id,
            theme=theme,
            operator_name=operator_name,
            now=now,
        )
        logger.info(f"创建大屏成功: {dashboard_id} - {name}")
        return self._dashboard_to_dict(dashboard)

    async def set_dashboard_public(
        self, dashboard_id: str, user_id: str, is_public: bool, operator_name: str = ""
    ) -> dict[str, Any] | None:
        """更新看板发布状态。"""
        dashboard = await self.dao.set_dashboard_public(
            dashboard_id=dashboard_id,
            user_id=user_id,
            is_public=is_public,
            operator_name=operator_name,
            now=datetime.now(),
        )
        if not dashboard:
            return None
        logger.info("更新看板发布状态: %s is_public=%s", dashboard_id, dashboard.is_public)
        return self._dashboard_to_dict(dashboard)

    async def get_dashboard(
        self, dashboard_id: str, user_id: str | None = None, include_public: bool = True
    ) -> dict[str, Any] | None:
        """获取大屏详情（含面板）"""
        dashboard, panels = await self.dao.get_dashboard_with_panels(
            dashboard_id=dashboard_id,
            user_id=user_id,
            include_public=include_public,
        )
        if not dashboard:
            return None
        dashboard_dict = self._dashboard_to_dict(dashboard)
        dashboard_dict["panels"] = [self._panel_to_dict(panel) for panel in panels]
        return dashboard_dict

    async def list_dashboards(self, user_id: str | None = None, scope: str = "mine") -> list[dict[str, Any]]:
        """获取大屏列表"""
        dashboards = await self.dao.list_dashboards(user_id=user_id, scope=scope)
        return [self._dashboard_to_dict(dashboard) for dashboard in dashboards]

    async def update_dashboard(
        self,
        dashboard_id: str,
        name: str | None = None,
        description: str | None = None,
        theme: str | None = None,
        background_id: str | None = None,
        user_id: str | None = None,
        operator_name: str = "",
    ) -> dict[str, Any] | None:
        """更新大屏信息"""
        dashboard = await self.dao.update_dashboard(
            dashboard_id=dashboard_id,
            user_id=user_id,
            values=self._dashboard_update_values(name, description, theme, background_id),
            operator_name=operator_name,
            now=datetime.now(),
        )
        if not dashboard:
            return None
        logger.info(f"更新大屏: {dashboard_id}")
        return self._dashboard_to_dict(dashboard)

    async def delete_dashboard(self, dashboard_id: str, user_id: str | None = None, operator_name: str = "") -> bool:
        """删除大屏（逻辑删除，同时删除所有面板）"""
        deleted = await self.dao.delete_dashboard(
            dashboard_id=dashboard_id,
            user_id=user_id,
            operator_name=operator_name,
            now=datetime.now(),
        )
        if deleted:
            logger.info(f"逻辑删除大屏: {dashboard_id}")
        return deleted

    # ============ 面板操作 ============

    async def add_panel(
        self,
        dashboard_id: str,
        payload: PanelWritePayload,
        user_id: str | None = None,
        operator_name: str = "",
    ) -> dict[str, Any] | None:
        """添加面板到大屏（面板属性经 ``PanelWritePayload`` 传入）。"""
        panel_id = str(uuid.uuid4())
        panel = await self.dao.add_panel(
            dashboard_id=dashboard_id,
            panel_id=panel_id,
            user_id=user_id,
            panel_values=self._panel_create_values(
                # add_panel 是创建语义：payload 缺省字段回落为 concrete 空值
                name=payload.name or "",
                sql=payload.sql or "",
                chart_type=payload.chart_type or "table",
                chart_config=payload.chart_config,
                layout_x=payload.layout_x,
                layout_y=payload.layout_y,
                layout_w=payload.layout_w if payload.layout_w is not None else 4,
                layout_h=payload.layout_h if payload.layout_h is not None else 4,
                refresh_interval=payload.refresh_interval,
                source_thread_id=payload.source_thread_id,
                datasource_id=payload.datasource_id,
                member_datasource_id=payload.member_datasource_id or "",
            ),
            operator_name=operator_name,
            now=datetime.now(),
        )
        if not panel:
            logger.warning(f"大屏不存在: {dashboard_id}")
            return None
        logger.info(f"添加面板: {panel_id} -> {dashboard_id}")
        return self._panel_to_dict(panel)

    async def update_panel(
        self,
        dashboard_id: str,
        panel_id: str,
        payload: PanelWritePayload,
        user_id: str | None = None,
        operator_name: str = "",
    ) -> dict[str, Any] | None:
        """更新面板（只写入 payload 中显式提供的字段）。"""
        panel = await self.dao.update_panel(
            dashboard_id=dashboard_id,
            panel_id=panel_id,
            user_id=user_id,
            values=self._panel_update_values(
                name=payload.name,
                sql=payload.sql,
                chart_type=payload.chart_type,
                chart_config=payload.chart_config,
                layout_x=payload.layout_x,
                layout_y=payload.layout_y,
                layout_w=payload.layout_w,
                layout_h=payload.layout_h,
                refresh_interval=payload.refresh_interval,
                member_datasource_id=payload.member_datasource_id,
            ),
            operator_name=operator_name,
            now=datetime.now(),
        )
        if not panel:
            return None
        logger.info(f"更新面板: {panel_id}")
        return self._panel_to_dict(panel)

    async def update_panels_layout(
        self,
        dashboard_id: str,
        layouts: list[dict[str, Any]],
        user_id: str | None = None,
        operator_name: str = "",
    ) -> int | None:
        """批量更新面板布局"""
        updated_count = await self.dao.update_panels_layout(
            dashboard_id=dashboard_id,
            layouts=layouts,
            user_id=user_id,
            operator_name=operator_name,
            now=datetime.now(),
        )
        if updated_count is None:
            return None
        logger.info(f"更新大屏布局: {dashboard_id}, {updated_count}/{len(layouts)} 个面板")
        return updated_count

    async def delete_panel(
        self, dashboard_id: str, panel_id: str, user_id: str | None = None, operator_name: str = ""
    ) -> bool:
        """删除面板（逻辑删除）"""
        deleted = await self.dao.delete_panel(
            dashboard_id=dashboard_id,
            panel_id=panel_id,
            user_id=user_id,
            operator_name=operator_name,
            now=datetime.now(),
        )
        if deleted:
            logger.info(f"逻辑删除面板: {panel_id}")
        return deleted

    async def get_panel(
        self, panel_id: str, dashboard_id: str | None = None, user_id: str | None = None, include_public: bool = True
    ) -> dict[str, Any] | None:
        """获取单个面板"""
        panel = await self.dao.get_panel(
            panel_id=panel_id,
            dashboard_id=dashboard_id,
            user_id=user_id,
            include_public=include_public,
        )
        return self._panel_to_dict(panel) if panel else None

    # ============ 辅助方法 ============

    @staticmethod
    def _dashboard_update_values(
        name: str | None = None,
        description: str | None = None,
        theme: str | None = None,
        background_id: str | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        if name is not None:
            values["dashboard_name"] = name
        if description is not None:
            values["dashboard_desc"] = description
        if theme is not None:
            values["dashboard_theme"] = theme
        if background_id is not None:
            values["background_id"] = background_id
        return values

    @staticmethod
    def _panel_create_values(
        *,
        name: str,
        sql: str,
        chart_type: str,
        chart_config: dict | None = None,
        layout_x: int | None = None,
        layout_y: int | None = None,
        layout_w: int = 4,
        layout_h: int = 4,
        refresh_interval: int | None = None,
        source_thread_id: str | None = None,
        datasource_id: str | None = None,
        member_datasource_id: str = "",
    ) -> dict[str, Any]:
        return {
            "panel_name": name,
            "panel_sql": sql,
            "chart_type": chart_type,
            "chart_config": json.dumps(chart_config) if chart_config else None,
            "layout_x": layout_x,
            "layout_y": layout_y,
            "layout_w": layout_w,
            "layout_h": layout_h,
            "refresh_interval": refresh_interval,
            "source_thread_id": source_thread_id,
            "datasource_id": datasource_id,
            "member_datasource_id": str(member_datasource_id or "").strip(),
        }

    @staticmethod
    def _panel_update_values(
        *,
        name: str | None = None,
        sql: str | None = None,
        chart_type: str | None = None,
        chart_config: dict | None = None,
        layout_x: int | None = None,
        layout_y: int | None = None,
        layout_w: int | None = None,
        layout_h: int | None = None,
        refresh_interval: int | None = None,
        member_datasource_id: str | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        optional_fields = {
            "panel_name": name,
            "panel_sql": sql,
            "chart_type": chart_type,
            "layout_x": layout_x,
            "layout_y": layout_y,
            "layout_w": layout_w,
            "layout_h": layout_h,
            "refresh_interval": refresh_interval,
        }
        for key, value in optional_fields.items():
            if value is not None:
                values[key] = value
        if chart_config is not None:
            values["chart_config"] = json.dumps(chart_config)
        if member_datasource_id is not None:
            values["member_datasource_id"] = str(member_datasource_id or "").strip()
        return values

    def _dashboard_to_dict(self, dashboard: ChatDataDashboard) -> dict[str, Any]:
        """将 ChatDataDashboard 对象转换为字典"""
        return {
            "id": dashboard.dashboard_id,
            "name": dashboard.dashboard_name,
            "description": dashboard.dashboard_desc,
            "user_id": dashboard.user_id,
            "datasource_id": dashboard.datasource_id,
            "theme": dashboard.dashboard_theme,
            "background_id": dashboard.background_id,
            "is_public": dashboard.is_public == 1,
            "create_time": dashboard.create_time.isoformat() if dashboard.create_time else None,
            "update_time": dashboard.update_time.isoformat() if dashboard.update_time else None,
            "create_by": dashboard.create_by,
            "create_by_name": getattr(dashboard, "create_by_name", ""),
            "update_by": dashboard.update_by,
            "update_by_name": getattr(dashboard, "update_by_name", ""),
        }

    def _panel_to_dict(self, panel: ChatDataDashboardPanel) -> dict[str, Any]:
        """将 ChatDataDashboardPanel 对象转换为字典"""
        chart_config = None
        if panel.chart_config:
            try:
                chart_config = json.loads(panel.chart_config)
            except json.JSONDecodeError:
                chart_config = None

        return {
            "id": panel.panel_id,
            "dashboard_id": panel.dashboard_id,
            "name": panel.panel_name,
            "sql": panel.panel_sql,
            "chart_type": panel.chart_type,
            "chart_config": chart_config,
            "layout": {
                "i": panel.panel_id,
                "x": panel.layout_x,
                "y": panel.layout_y,
                "w": panel.layout_w,
                "h": panel.layout_h,
            },
            "refresh_interval": panel.refresh_interval,
            "source_thread_id": panel.source_thread_id,
            "datasource_id": panel.datasource_id,
            "member_datasource_id": getattr(panel, "member_datasource_id", "") or "",
            "create_time": panel.create_time.isoformat() if panel.create_time else None,
            "update_time": panel.update_time.isoformat() if panel.update_time else None,
            "create_by": panel.create_by,
            "create_by_name": getattr(panel, "create_by_name", ""),
            "update_by": panel.update_by,
            "update_by_name": getattr(panel, "update_by_name", ""),
        }


# 单例
_dashboard_service = None


def get_dashboard_service() -> DashboardService:
    """获取 Dashboard 服务实例"""
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService()
    return _dashboard_service
