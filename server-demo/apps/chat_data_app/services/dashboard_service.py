"""
Dashboard 服务层

提供分析大屏的 CRUD 操作（异步）
"""

import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from sqlalchemy import select, update

from utils.lang_util.database import get_session
from utils.lang_util.models import ChatDataDashboard, ChatDataDashboardPanel
from utils.logger import get_logger

logger = get_logger(__name__)


class DashboardService:
    """分析大屏服务（异步）"""

    def __init__(self):
        pass

    async def create_dashboard(
        self,
        name: str,
        user_id: str,
        description: str = "",
        datasource_id: str = None,
        theme: str = "light"
    ) -> Dict[str, Any]:
        """创建新的分析大屏"""
        dashboard_id = str(uuid.uuid4())
        now = datetime.now()

        async with get_session() as session:
            dashboard = ChatDataDashboard(
                dashboard_id=dashboard_id,
                dashboard_name=name,
                dashboard_desc=description,
                user_id=user_id,
                datasource_id=datasource_id,
                dashboard_theme=theme,
                create_time=now,
                update_time=now,
                create_by=user_id,
                update_by=user_id
            )
            session.add(dashboard)
            # commit 由上下文管理器处理

            logger.info(f"创建大屏成功: {dashboard_id} - {name}")

            return self._dashboard_to_dict(dashboard)

    async def get_dashboard(self, dashboard_id: str) -> Optional[Dict[str, Any]]:
        """获取大屏详情（含面板）"""
        async with get_session() as session:
            result = await session.execute(
                select(ChatDataDashboard).where(
                    ChatDataDashboard.dashboard_id == dashboard_id,
                    ChatDataDashboard.delete_flag == 0
                )
            )
            dashboard = result.scalar_one_or_none()

            if not dashboard:
                return None

            dashboard_dict = self._dashboard_to_dict(dashboard)

            # 获取面板（只查询未删除的）
            panels_result = await session.execute(
                select(ChatDataDashboardPanel).where(
                    ChatDataDashboardPanel.dashboard_id == dashboard_id,
                    ChatDataDashboardPanel.delete_flag == 0
                )
            )
            panels = panels_result.scalars().all()

            dashboard_dict["panels"] = [self._panel_to_dict(p) for p in panels]

            return dashboard_dict

    async def list_dashboards(self, user_id: str = None) -> List[Dict[str, Any]]:
        """获取大屏列表"""
        async with get_session() as session:
            query = select(ChatDataDashboard).where(ChatDataDashboard.delete_flag == 0)
            if user_id:
                query = query.where(ChatDataDashboard.user_id == user_id)
            query = query.order_by(ChatDataDashboard.update_time.desc())

            result = await session.execute(query)
            dashboards = result.scalars().all()

            dashboard_list = []
            for d in dashboards:
                dashboard_dict = self._dashboard_to_dict(d)
                # 获取面板数量（只统计未删除的）
                panel_result = await session.execute(
                    select(ChatDataDashboardPanel).where(
                        ChatDataDashboardPanel.dashboard_id == d.dashboard_id,
                        ChatDataDashboardPanel.delete_flag == 0
                    )
                )
                panels = panel_result.scalars().all()
                dashboard_dict["panel_count"] = len(panels)
                dashboard_list.append(dashboard_dict)

            return dashboard_list

    async def update_dashboard(
        self,
        dashboard_id: str,
        name: str = None,
        description: str = None,
        theme: str = None,
        background_id: str = None,
        user_id: str = None
    ) -> Optional[Dict[str, Any]]:
        """更新大屏信息"""
        async with get_session() as session:
            result = await session.execute(
                select(ChatDataDashboard).where(
                    ChatDataDashboard.dashboard_id == dashboard_id,
                    ChatDataDashboard.delete_flag == 0
                )
            )
            dashboard = result.scalar_one_or_none()

            if not dashboard:
                return None

            if name is not None:
                dashboard.dashboard_name = name
            if description is not None:
                dashboard.dashboard_desc = description
            if theme is not None:
                dashboard.dashboard_theme = theme
            if background_id is not None:
                dashboard.background_id = background_id
            dashboard.update_time = datetime.now()
            if user_id:
                dashboard.update_by = user_id

            logger.info(f"更新大屏: {dashboard_id}")

            return self._dashboard_to_dict(dashboard)

    async def delete_dashboard(self, dashboard_id: str) -> bool:
        """删除大屏（逻辑删除，同时删除所有面板）"""
        async with get_session() as session:
            now = datetime.now()
            # 先逻辑删除面板
            await session.execute(
                update(ChatDataDashboardPanel)
                .where(ChatDataDashboardPanel.dashboard_id == dashboard_id)
                .values(delete_flag=1, update_time=now)
            )
            # 逻辑删除大屏
            result = await session.execute(
                update(ChatDataDashboard)
                .where(
                    ChatDataDashboard.dashboard_id == dashboard_id,
                    ChatDataDashboard.delete_flag == 0
                )
                .values(delete_flag=1, update_time=now)
            )

            deleted = result.rowcount > 0
            if deleted:
                logger.info(f"逻辑删除大屏: {dashboard_id}")

            return deleted

    # ============ 面板操作 ============

    async def add_panel(
        self,
        dashboard_id: str,
        name: str,
        sql: str,
        chart_type: str = "table",
        chart_config: Dict = None,
        layout_x: int = None,
        layout_y: int = None,
        layout_w: int = 4,
        layout_h: int = 4,
        refresh_interval: int = None,
        source_thread_id: str = None,
        datasource_id: str = None,
        user_id: str = None
    ) -> Optional[Dict[str, Any]]:
        """添加面板到大屏"""
        async with get_session() as session:
            # 检查大屏是否存在且未删除
            result = await session.execute(
                select(ChatDataDashboard).where(
                    ChatDataDashboard.dashboard_id == dashboard_id,
                    ChatDataDashboard.delete_flag == 0
                )
            )
            dashboard = result.scalar_one_or_none()

            if not dashboard:
                logger.warning(f"大屏不存在: {dashboard_id}")
                return None

            # 如果没有指定位置，自动放置在最下方
            if layout_x is None or layout_y is None:
                # 获取现有面板的最大 y + h 值（只统计未删除的）
                panels_result = await session.execute(
                    select(ChatDataDashboardPanel).where(
                        ChatDataDashboardPanel.dashboard_id == dashboard_id,
                        ChatDataDashboardPanel.delete_flag == 0
                    )
                )
                existing_panels = panels_result.scalars().all()

                if existing_panels:
                    # 找到最大的 y + h 值作为新面板的 y
                    max_bottom = max(p.layout_y + p.layout_h for p in existing_panels)
                    layout_y = max_bottom
                    layout_x = 0
                else:
                    layout_x = 0
                    layout_y = 0

            panel_id = str(uuid.uuid4())
            now = datetime.now()

            panel = ChatDataDashboardPanel(
                panel_id=panel_id,
                dashboard_id=dashboard_id,
                panel_name=name,
                panel_sql=sql,
                chart_type=chart_type,
                chart_config=json.dumps(chart_config) if chart_config else None,
                layout_x=layout_x,
                layout_y=layout_y,
                layout_w=layout_w,
                layout_h=layout_h,
                refresh_interval=refresh_interval,
                source_thread_id=source_thread_id,
                datasource_id=datasource_id,
                create_time=now,
                update_time=now,
                create_by=user_id,
                update_by=user_id
            )
            session.add(panel)

            # 更新大屏修改时间
            dashboard.update_time = now
            if user_id:
                dashboard.update_by = user_id

            logger.info(f"添加面板: {panel_id} -> {dashboard_id}")

            return self._panel_to_dict(panel)

    async def update_panel(
        self,
        panel_id: str,
        name: str = None,
        sql: str = None,
        chart_type: str = None,
        chart_config: Dict = None,
        layout_x: int = None,
        layout_y: int = None,
        layout_w: int = None,
        layout_h: int = None,
        refresh_interval: int = None,
        user_id: str = None
    ) -> Optional[Dict[str, Any]]:
        """更新面板"""
        async with get_session() as session:
            result = await session.execute(
                select(ChatDataDashboardPanel).where(
                    ChatDataDashboardPanel.panel_id == panel_id,
                    ChatDataDashboardPanel.delete_flag == 0
                )
            )
            panel = result.scalar_one_or_none()

            if not panel:
                return None

            if name is not None:
                panel.panel_name = name
            if sql is not None:
                panel.panel_sql = sql
            if chart_type is not None:
                panel.chart_type = chart_type
            if chart_config is not None:
                panel.chart_config = json.dumps(chart_config)
            if layout_x is not None:
                panel.layout_x = layout_x
            if layout_y is not None:
                panel.layout_y = layout_y
            if layout_w is not None:
                panel.layout_w = layout_w
            if layout_h is not None:
                panel.layout_h = layout_h
            if refresh_interval is not None:
                panel.refresh_interval = refresh_interval

            panel.update_time = datetime.now()
            if user_id:
                panel.update_by = user_id

            logger.info(f"更新面板: {panel_id}")

            return self._panel_to_dict(panel)

    async def update_panels_layout(
        self,
        dashboard_id: str,
        layouts: List[Dict[str, Any]],
        user_id: str = None
    ) -> bool:
        """批量更新面板布局"""
        async with get_session() as session:
            now = datetime.now()
            for layout in layouts:
                panel_id = layout.get("panel_id") or layout.get("i")
                if not panel_id:
                    continue

                result = await session.execute(
                    select(ChatDataDashboardPanel).where(
                        ChatDataDashboardPanel.panel_id == panel_id,
                        ChatDataDashboardPanel.delete_flag == 0
                    )
                )
                panel = result.scalar_one_or_none()

                if panel:
                    panel.layout_x = layout.get("x", panel.layout_x)
                    panel.layout_y = layout.get("y", panel.layout_y)
                    panel.layout_w = layout.get("w", panel.layout_w)
                    panel.layout_h = layout.get("h", panel.layout_h)
                    panel.update_time = now
                    if user_id:
                        panel.update_by = user_id

            # 更新大屏修改时间
            result = await session.execute(
                select(ChatDataDashboard).where(
                    ChatDataDashboard.dashboard_id == dashboard_id,
                    ChatDataDashboard.delete_flag == 0
                )
            )
            dashboard = result.scalar_one_or_none()
            if dashboard:
                dashboard.update_time = now
                if user_id:
                    dashboard.update_by = user_id

            logger.info(f"更新大屏布局: {dashboard_id}, {len(layouts)} 个面板")

            return True

    async def delete_panel(self, panel_id: str) -> bool:
        """删除面板（逻辑删除）"""
        async with get_session() as session:
            result = await session.execute(
                update(ChatDataDashboardPanel)
                .where(
                    ChatDataDashboardPanel.panel_id == panel_id,
                    ChatDataDashboardPanel.delete_flag == 0
                )
                .values(delete_flag=1, update_time=datetime.now())
            )

            deleted = result.rowcount > 0
            if deleted:
                logger.info(f"逻辑删除面板: {panel_id}")

            return deleted

    async def get_panel(self, panel_id: str) -> Optional[Dict[str, Any]]:
        """获取单个面板"""
        async with get_session() as session:
            result = await session.execute(
                select(ChatDataDashboardPanel).where(
                    ChatDataDashboardPanel.panel_id == panel_id,
                    ChatDataDashboardPanel.delete_flag == 0
                )
            )
            panel = result.scalar_one_or_none()

            if not panel:
                return None

            return self._panel_to_dict(panel)

    # ============ 辅助方法 ============

    def _dashboard_to_dict(self, dashboard: ChatDataDashboard) -> Dict[str, Any]:
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
            "update_by": dashboard.update_by
        }

    def _panel_to_dict(self, panel: ChatDataDashboardPanel) -> Dict[str, Any]:
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
                "h": panel.layout_h
            },
            "refresh_interval": panel.refresh_interval,
            "source_thread_id": panel.source_thread_id,
            "datasource_id": panel.datasource_id,
            "create_time": panel.create_time.isoformat() if panel.create_time else None,
            "update_time": panel.update_time.isoformat() if panel.update_time else None,
            "create_by": panel.create_by,
            "update_by": panel.update_by
        }


# 单例
_dashboard_service = None


def get_dashboard_service() -> DashboardService:
    """获取 Dashboard 服务实例"""
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService()
    return _dashboard_service
