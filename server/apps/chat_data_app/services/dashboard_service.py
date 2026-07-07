"""
Dashboard 服务层

提供分析大屏的 CRUD 操作（异步）
"""

import json
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from sqlalchemy import or_, select, update

from utils.database import get_session
from utils.models import ChatDataDashboard, ChatDataDashboardPanel
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
        theme: str = "light",
        operator_name: str = "",
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
                is_public=0,
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=operator_name or "",
                update_by=user_id,
                update_by_name=operator_name or "",
            )
            session.add(dashboard)
            # commit 由上下文管理器处理

            logger.info(f"创建大屏成功: {dashboard_id} - {name}")

            return self._dashboard_to_dict(dashboard)

    async def set_dashboard_public(self, dashboard_id: str, user_id: str, is_public: bool, operator_name: str = "") -> Optional[Dict[str, Any]]:
        """更新看板发布状态。"""
        async with get_session() as session:
            result = await session.execute(
                select(ChatDataDashboard).where(
                    ChatDataDashboard.dashboard_id == dashboard_id,
                    ChatDataDashboard.user_id == user_id,
                    ChatDataDashboard.delete_flag == 0,
                )
            )
            dashboard = result.scalar_one_or_none()
            if not dashboard:
                return None

            dashboard.is_public = 1 if is_public else 0
            dashboard.update_time = datetime.now()
            dashboard.update_by = user_id
            dashboard.update_by_name = operator_name or ""
            logger.info("更新看板发布状态: %s is_public=%s", dashboard_id, dashboard.is_public)
            dashboard_dict = self._dashboard_to_dict(dashboard)
            return dashboard_dict

    async def get_dashboard(self, dashboard_id: str, user_id: str = None, include_public: bool = True) -> Optional[Dict[str, Any]]:
        """获取大屏详情（含面板）"""
        async with get_session() as session:
            filters = [
                ChatDataDashboard.dashboard_id == dashboard_id,
                ChatDataDashboard.delete_flag == 0,
            ]
            if user_id:
                access_filter = ChatDataDashboard.user_id == user_id
                if include_public:
                    access_filter = or_(access_filter, ChatDataDashboard.is_public == 1)
                filters.append(access_filter)
            result = await session.execute(
                select(ChatDataDashboard).where(*filters)
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

    async def list_dashboards(self, user_id: str = None, scope: str = "mine") -> List[Dict[str, Any]]:
        """获取大屏列表"""
        async with get_session() as session:
            query = select(ChatDataDashboard).where(ChatDataDashboard.delete_flag == 0)
            normalized_scope = (scope or "mine").strip().lower()
            if normalized_scope == "public":
                query = query.where(ChatDataDashboard.is_public == 1)
            elif user_id:
                query = query.where(
                    ChatDataDashboard.user_id == user_id,
                    ChatDataDashboard.is_public == 0,
                )
            query = query.order_by(ChatDataDashboard.update_time.desc())

            result = await session.execute(query)
            dashboards = result.scalars().all()

            dashboard_list = []
            for d in dashboards:
                dashboard_dict = self._dashboard_to_dict(d)
                dashboard_list.append(dashboard_dict)

            return dashboard_list

    async def update_dashboard(
        self,
        dashboard_id: str,
        name: str = None,
        description: str = None,
        theme: str = None,
        background_id: str = None,
        user_id: str = None,
        operator_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """更新大屏信息"""
        async with get_session() as session:
            filters = [
                ChatDataDashboard.dashboard_id == dashboard_id,
                ChatDataDashboard.delete_flag == 0,
            ]
            if user_id:
                filters.append(ChatDataDashboard.user_id == user_id)
            result = await session.execute(select(ChatDataDashboard).where(*filters))
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
            dashboard.update_by_name = operator_name or ""

            logger.info(f"更新大屏: {dashboard_id}")

            return self._dashboard_to_dict(dashboard)

    async def delete_dashboard(self, dashboard_id: str, user_id: str = None, operator_name: str = "") -> bool:
        """删除大屏（逻辑删除，同时删除所有面板）"""
        async with get_session() as session:
            now = datetime.now()
            dashboard_filters = [
                ChatDataDashboard.dashboard_id == dashboard_id,
                ChatDataDashboard.delete_flag == 0,
            ]
            if user_id:
                dashboard_filters.append(ChatDataDashboard.user_id == user_id)
            dashboard_result = await session.execute(select(ChatDataDashboard).where(*dashboard_filters))
            dashboard = dashboard_result.scalar_one_or_none()
            if not dashboard:
                return False
            panel_values = {"delete_flag": 1, "update_time": now}
            dashboard_values = {"delete_flag": 1, "update_time": now}
            if user_id:
                panel_values["update_by"] = user_id
                dashboard_values["update_by"] = user_id
            if operator_name:
                panel_values["update_by_name"] = operator_name
                dashboard_values["update_by_name"] = operator_name
            # 先逻辑删除面板
            await session.execute(
                update(ChatDataDashboardPanel)
                .where(ChatDataDashboardPanel.dashboard_id == dashboard_id)
                .values(**panel_values)
            )
            # 逻辑删除大屏
            result = await session.execute(
                update(ChatDataDashboard)
                .where(*dashboard_filters)
                .values(**dashboard_values)
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
        member_datasource_id: str = "",
        user_id: str = None,
        operator_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """添加面板到大屏"""
        async with get_session() as session:
            # 检查大屏是否存在且未删除
            dashboard_filters = [
                ChatDataDashboard.dashboard_id == dashboard_id,
                ChatDataDashboard.delete_flag == 0,
            ]
            if user_id:
                dashboard_filters.append(ChatDataDashboard.user_id == user_id)
            result = await session.execute(select(ChatDataDashboard).where(*dashboard_filters))
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
                member_datasource_id=str(member_datasource_id or "").strip(),
                create_time=now,
                update_time=now,
                create_by=user_id,
                create_by_name=operator_name or "",
                update_by=user_id,
                update_by_name=operator_name or "",
            )
            session.add(panel)

            # 更新大屏修改时间
            dashboard.update_time = now
            if user_id:
                dashboard.update_by = user_id
            dashboard.update_by_name = operator_name or ""

            logger.info(f"添加面板: {panel_id} -> {dashboard_id}")

            return self._panel_to_dict(panel)

    async def update_panel(
        self,
        dashboard_id: str,
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
        member_datasource_id: str = None,
        user_id: str = None,
        operator_name: str = "",
    ) -> Optional[Dict[str, Any]]:
        """更新面板"""
        async with get_session() as session:
            dashboard_filters = [
                ChatDataDashboard.dashboard_id == dashboard_id,
                ChatDataDashboard.delete_flag == 0,
            ]
            if user_id:
                dashboard_filters.append(ChatDataDashboard.user_id == user_id)
            dashboard_result = await session.execute(select(ChatDataDashboard).where(*dashboard_filters))
            dashboard = dashboard_result.scalar_one_or_none()
            if not dashboard:
                return None

            result = await session.execute(
                select(ChatDataDashboardPanel).where(
                    ChatDataDashboardPanel.dashboard_id == dashboard_id,
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
            if member_datasource_id is not None:
                panel.member_datasource_id = str(member_datasource_id or "").strip()

            now = datetime.now()
            panel.update_time = now
            if user_id:
                panel.update_by = user_id
            panel.update_by_name = operator_name or ""

            dashboard.update_time = now
            if user_id:
                dashboard.update_by = user_id
            dashboard.update_by_name = operator_name or ""

            logger.info(f"更新面板: {panel_id}")

            return self._panel_to_dict(panel)

    async def update_panels_layout(
        self,
        dashboard_id: str,
        layouts: List[Dict[str, Any]],
        user_id: str = None,
        operator_name: str = "",
    ) -> Optional[int]:
        """批量更新面板布局"""
        async with get_session() as session:
            now = datetime.now()
            dashboard_filters = [
                ChatDataDashboard.dashboard_id == dashboard_id,
                ChatDataDashboard.delete_flag == 0,
            ]
            if user_id:
                dashboard_filters.append(ChatDataDashboard.user_id == user_id)
            dashboard_result = await session.execute(select(ChatDataDashboard).where(*dashboard_filters))
            dashboard = dashboard_result.scalar_one_or_none()
            if not dashboard:
                return None

            updated_count = 0
            panel_ids = []
            for layout in layouts:
                panel_id = layout.get("panel_id") or layout.get("i")
                if not panel_id:
                    continue
                panel_ids.append(panel_id)

            panel_map: Dict[str, ChatDataDashboardPanel] = {}
            if panel_ids:
                panel_result = await session.execute(
                    select(ChatDataDashboardPanel).where(
                        ChatDataDashboardPanel.dashboard_id == dashboard_id,
                        ChatDataDashboardPanel.panel_id.in_(panel_ids),
                        ChatDataDashboardPanel.delete_flag == 0,
                    )
                )
                panel_map = {panel.panel_id: panel for panel in panel_result.scalars().all()}

            for layout in layouts:
                panel_id = layout.get("panel_id") or layout.get("i")
                if not panel_id:
                    continue
                panel = panel_map.get(panel_id)

                if panel:
                    panel.layout_x = layout.get("x", panel.layout_x)
                    panel.layout_y = layout.get("y", panel.layout_y)
                    panel.layout_w = layout.get("w", panel.layout_w)
                    panel.layout_h = layout.get("h", panel.layout_h)
                    panel.update_time = now
                    if user_id:
                        panel.update_by = user_id
                    panel.update_by_name = operator_name or ""
                    updated_count += 1

            # 更新大屏修改时间
            dashboard.update_time = now
            if user_id:
                dashboard.update_by = user_id
            dashboard.update_by_name = operator_name or ""

            logger.info(f"更新大屏布局: {dashboard_id}, {updated_count}/{len(layouts)} 个面板")

            return updated_count

    async def delete_panel(self, dashboard_id: str, panel_id: str, user_id: str = None, operator_name: str = "") -> bool:
        """删除面板（逻辑删除）"""
        async with get_session() as session:
            now = datetime.now()
            dashboard_filters = [
                ChatDataDashboard.dashboard_id == dashboard_id,
                ChatDataDashboard.delete_flag == 0,
            ]
            if user_id:
                dashboard_filters.append(ChatDataDashboard.user_id == user_id)
            dashboard_result = await session.execute(select(ChatDataDashboard).where(*dashboard_filters))
            dashboard = dashboard_result.scalar_one_or_none()
            if not dashboard:
                return False
            panel_values = {"delete_flag": 1, "update_time": now}
            dashboard_values = {"update_time": now}
            if user_id:
                panel_values["update_by"] = user_id
                dashboard_values["update_by"] = user_id
            if operator_name:
                panel_values["update_by_name"] = operator_name
                dashboard_values["update_by_name"] = operator_name
            result = await session.execute(
                update(ChatDataDashboardPanel)
                .where(
                    ChatDataDashboardPanel.dashboard_id == dashboard_id,
                    ChatDataDashboardPanel.panel_id == panel_id,
                    ChatDataDashboardPanel.delete_flag == 0
                )
                .values(**panel_values)
            )

            deleted = result.rowcount > 0
            if deleted:
                await session.execute(
                    update(ChatDataDashboard)
                    .where(
                        ChatDataDashboard.dashboard_id == dashboard_id,
                        ChatDataDashboard.delete_flag == 0
                    )
                    .values(**dashboard_values)
                )
                logger.info(f"逻辑删除面板: {panel_id}")

            return deleted

    async def get_panel(self, panel_id: str, dashboard_id: str = None, user_id: str = None, include_public: bool = True) -> Optional[Dict[str, Any]]:
        """获取单个面板"""
        async with get_session() as session:
            filters = [
                ChatDataDashboardPanel.panel_id == panel_id,
                ChatDataDashboardPanel.delete_flag == 0,
            ]
            if dashboard_id:
                filters.append(ChatDataDashboardPanel.dashboard_id == dashboard_id)
            query = select(ChatDataDashboardPanel).join(
                ChatDataDashboard,
                ChatDataDashboard.dashboard_id == ChatDataDashboardPanel.dashboard_id,
            ).where(
                *filters,
                ChatDataDashboard.delete_flag == 0,
            )
            if user_id:
                access_filter = ChatDataDashboard.user_id == user_id
                if include_public:
                    access_filter = or_(access_filter, ChatDataDashboard.is_public == 1)
                query = query.where(access_filter)
            result = await session.execute(query)
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
            "create_by_name": getattr(dashboard, "create_by_name", ""),
            "update_by": dashboard.update_by,
            "update_by_name": getattr(dashboard, "update_by_name", ""),
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
