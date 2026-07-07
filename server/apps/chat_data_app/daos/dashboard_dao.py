"""看板配置数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import or_, select, update

from utils.database import get_session as default_get_session
from utils.models import ChatDataDashboard, ChatDataDashboardPanel


class DashboardDao:
    """封装看板和面板配置表读写。"""

    def __init__(self, session_context: Optional[Callable] = None):
        self._session_context = session_context or default_get_session

    @staticmethod
    def _dashboard_owner_filters(dashboard_id: str, user_id: Optional[str]) -> list[Any]:
        filters = [ChatDataDashboard.dashboard_id == dashboard_id, ChatDataDashboard.delete_flag == 0]
        if user_id:
            filters.append(ChatDataDashboard.user_id == user_id)
        return filters

    @staticmethod
    def _dashboard_access_filters(dashboard_id: str, user_id: Optional[str], include_public: bool) -> list[Any]:
        filters = [ChatDataDashboard.dashboard_id == dashboard_id, ChatDataDashboard.delete_flag == 0]
        if user_id:
            access_filter = ChatDataDashboard.user_id == user_id
            if include_public:
                access_filter = or_(access_filter, ChatDataDashboard.is_public == 1)
            filters.append(access_filter)
        return filters

    @staticmethod
    def _panel_filters(panel_id: str, dashboard_id: Optional[str]) -> list[Any]:
        filters = [ChatDataDashboardPanel.panel_id == panel_id, ChatDataDashboardPanel.delete_flag == 0]
        if dashboard_id:
            filters.append(ChatDataDashboardPanel.dashboard_id == dashboard_id)
        return filters

    async def create_dashboard(
        self,
        *,
        dashboard_id: str,
        name: str,
        user_id: str,
        description: str,
        datasource_id: Optional[str],
        theme: str,
        operator_name: str,
        now: datetime,
    ) -> ChatDataDashboard:
        async with self._session_context() as session:
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
            await session.flush()
            return dashboard

    async def set_dashboard_public(
        self,
        *,
        dashboard_id: str,
        user_id: str,
        is_public: bool,
        operator_name: str,
        now: datetime,
    ) -> Optional[ChatDataDashboard]:
        async with self._session_context() as session:
            dashboard = await self._get_owner_dashboard(session, dashboard_id, user_id)
            if not dashboard:
                return None
            dashboard.is_public = 1 if is_public else 0
            dashboard.update_time = now
            dashboard.update_by = user_id
            dashboard.update_by_name = operator_name or ""
            await session.flush()
            return dashboard

    async def get_dashboard_with_panels(
        self,
        *,
        dashboard_id: str,
        user_id: Optional[str],
        include_public: bool,
    ) -> Tuple[Optional[ChatDataDashboard], List[ChatDataDashboardPanel]]:
        async with self._session_context() as session:
            result = await session.execute(
                select(ChatDataDashboard).where(*self._dashboard_access_filters(dashboard_id, user_id, include_public))
            )
            dashboard = result.scalar_one_or_none()
            if not dashboard:
                return None, []
            panels = await self.list_dashboard_panels(session, dashboard_id)
            return dashboard, panels

    async def list_dashboards(self, *, user_id: Optional[str], scope: str) -> List[ChatDataDashboard]:
        async with self._session_context() as session:
            query = select(ChatDataDashboard).where(ChatDataDashboard.delete_flag == 0)
            normalized_scope = (scope or "mine").strip().lower()
            if normalized_scope == "public":
                query = query.where(ChatDataDashboard.is_public == 1)
            elif user_id:
                query = query.where(ChatDataDashboard.user_id == user_id, ChatDataDashboard.is_public == 0)
            result = await session.execute(query.order_by(ChatDataDashboard.update_time.desc()))
            return list(result.scalars().all())

    async def update_dashboard(
        self,
        *,
        dashboard_id: str,
        user_id: Optional[str],
        values: Dict[str, Any],
        operator_name: str,
        now: datetime,
    ) -> Optional[ChatDataDashboard]:
        async with self._session_context() as session:
            dashboard = await self._get_owner_dashboard(session, dashboard_id, user_id)
            if not dashboard:
                return None
            self._apply_dashboard_values(dashboard, values, user_id, operator_name, now)
            await session.flush()
            return dashboard

    async def delete_dashboard(
        self,
        *,
        dashboard_id: str,
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> bool:
        async with self._session_context() as session:
            dashboard = await self._get_owner_dashboard(session, dashboard_id, user_id)
            if not dashboard:
                return False
            await self._mark_dashboard_panels_deleted(session, dashboard_id, user_id, operator_name, now)
            deleted = await self._mark_dashboard_deleted(session, dashboard_id, user_id, operator_name, now)
            return deleted

    async def add_panel(
        self,
        *,
        dashboard_id: str,
        panel_id: str,
        user_id: Optional[str],
        panel_values: Dict[str, Any],
        operator_name: str,
        now: datetime,
    ) -> Optional[ChatDataDashboardPanel]:
        async with self._session_context() as session:
            dashboard = await self._get_owner_dashboard(session, dashboard_id, user_id)
            if not dashboard:
                return None
            layout_x, layout_y = await self._resolve_panel_layout(session, dashboard_id, panel_values)
            panel = self._new_panel(
                dashboard_id=dashboard_id,
                panel_id=panel_id,
                user_id=user_id,
                operator_name=operator_name,
                now=now,
                layout_x=layout_x,
                layout_y=layout_y,
                panel_values=panel_values,
            )
            session.add(panel)
            self._touch_dashboard(dashboard, user_id, operator_name, now)
            await session.flush()
            return panel

    async def update_panel(
        self,
        *,
        dashboard_id: str,
        panel_id: str,
        user_id: Optional[str],
        values: Dict[str, Any],
        operator_name: str,
        now: datetime,
    ) -> Optional[ChatDataDashboardPanel]:
        async with self._session_context() as session:
            dashboard = await self._get_owner_dashboard(session, dashboard_id, user_id)
            if not dashboard:
                return None
            panel = await self._get_panel_for_update(session, dashboard_id, panel_id)
            if not panel:
                return None
            self._apply_panel_values(panel, values, user_id, operator_name, now)
            self._touch_dashboard(dashboard, user_id, operator_name, now)
            await session.flush()
            return panel

    async def update_panels_layout(
        self,
        *,
        dashboard_id: str,
        layouts: List[Dict[str, Any]],
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> Optional[int]:
        async with self._session_context() as session:
            dashboard = await self._get_owner_dashboard(session, dashboard_id, user_id)
            if not dashboard:
                return None
            panel_map = await self._load_layout_panel_map(session, dashboard_id, layouts)
            updated_count = self._apply_panel_layouts(panel_map, layouts, user_id, operator_name, now)
            self._touch_dashboard(dashboard, user_id, operator_name, now)
            await session.flush()
            return updated_count

    async def delete_panel(
        self,
        *,
        dashboard_id: str,
        panel_id: str,
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> bool:
        async with self._session_context() as session:
            dashboard = await self._get_owner_dashboard(session, dashboard_id, user_id)
            if not dashboard:
                return False
            result = await session.execute(
                update(ChatDataDashboardPanel)
                .where(
                    ChatDataDashboardPanel.dashboard_id == dashboard_id,
                    ChatDataDashboardPanel.panel_id == panel_id,
                    ChatDataDashboardPanel.delete_flag == 0,
                )
                .values(**self._delete_panel_values(user_id, operator_name, now))
            )
            deleted = result.rowcount > 0
            if deleted:
                self._touch_dashboard(dashboard, user_id, operator_name, now)
            return deleted

    async def get_panel(
        self,
        *,
        panel_id: str,
        dashboard_id: Optional[str],
        user_id: Optional[str],
        include_public: bool,
    ) -> Optional[ChatDataDashboardPanel]:
        async with self._session_context() as session:
            query = select(ChatDataDashboardPanel).join(
                ChatDataDashboard,
                ChatDataDashboard.dashboard_id == ChatDataDashboardPanel.dashboard_id,
            ).where(*self._panel_filters(panel_id, dashboard_id), ChatDataDashboard.delete_flag == 0)
            if user_id:
                access_filter = ChatDataDashboard.user_id == user_id
                if include_public:
                    access_filter = or_(access_filter, ChatDataDashboard.is_public == 1)
                query = query.where(access_filter)
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def _get_owner_dashboard(self, session: Any, dashboard_id: str, user_id: Optional[str]) -> Optional[ChatDataDashboard]:
        result = await session.execute(select(ChatDataDashboard).where(*self._dashboard_owner_filters(dashboard_id, user_id)))
        return result.scalar_one_or_none()

    async def list_dashboard_panels(self, session: Any, dashboard_id: str) -> List[ChatDataDashboardPanel]:
        result = await session.execute(
            select(ChatDataDashboardPanel).where(
                ChatDataDashboardPanel.dashboard_id == dashboard_id,
                ChatDataDashboardPanel.delete_flag == 0,
            )
        )
        return list(result.scalars().all())

    async def _resolve_panel_layout(self, session: Any, dashboard_id: str, values: Dict[str, Any]) -> Tuple[int, int]:
        layout_x = values.get("layout_x")
        layout_y = values.get("layout_y")
        if layout_x is not None and layout_y is not None:
            return layout_x, layout_y
        panels = await self.list_dashboard_panels(session, dashboard_id)
        return 0, max((panel.layout_y + panel.layout_h for panel in panels), default=0)

    async def _load_layout_panel_map(
        self,
        session: Any,
        dashboard_id: str,
        layouts: List[Dict[str, Any]],
    ) -> Dict[str, ChatDataDashboardPanel]:
        panel_ids = [layout.get("panel_id") or layout.get("i") for layout in layouts if layout.get("panel_id") or layout.get("i")]
        if not panel_ids:
            return {}
        result = await session.execute(
            select(ChatDataDashboardPanel).where(
                ChatDataDashboardPanel.dashboard_id == dashboard_id,
                ChatDataDashboardPanel.panel_id.in_(panel_ids),
                ChatDataDashboardPanel.delete_flag == 0,
            )
        )
        return {panel.panel_id: panel for panel in result.scalars().all()}

    async def _get_panel_for_update(self, session: Any, dashboard_id: str, panel_id: str) -> Optional[ChatDataDashboardPanel]:
        result = await session.execute(
            select(ChatDataDashboardPanel).where(
                ChatDataDashboardPanel.dashboard_id == dashboard_id,
                ChatDataDashboardPanel.panel_id == panel_id,
                ChatDataDashboardPanel.delete_flag == 0,
            )
        )
        return result.scalar_one_or_none()

    async def _mark_dashboard_panels_deleted(
        self,
        session: Any,
        dashboard_id: str,
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> None:
        await session.execute(
            update(ChatDataDashboardPanel)
            .where(ChatDataDashboardPanel.dashboard_id == dashboard_id)
            .values(**self._delete_panel_values(user_id, operator_name, now))
        )

    async def _mark_dashboard_deleted(
        self,
        session: Any,
        dashboard_id: str,
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> bool:
        result = await session.execute(
            update(ChatDataDashboard)
            .where(*self._dashboard_owner_filters(dashboard_id, user_id))
            .values(**self._delete_dashboard_values(user_id, operator_name, now))
        )
        return result.rowcount > 0

    @staticmethod
    def _apply_dashboard_values(
        dashboard: ChatDataDashboard,
        values: Dict[str, Any],
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> None:
        for key, value in values.items():
            setattr(dashboard, key, value)
        dashboard.update_time = now
        if user_id:
            dashboard.update_by = user_id
        dashboard.update_by_name = operator_name or ""

    @staticmethod
    def _apply_panel_values(
        panel: ChatDataDashboardPanel,
        values: Dict[str, Any],
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> None:
        for key, value in values.items():
            setattr(panel, key, value)
        panel.update_time = now
        if user_id:
            panel.update_by = user_id
        panel.update_by_name = operator_name or ""

    @classmethod
    def _apply_panel_layouts(
        cls,
        panel_map: Dict[str, ChatDataDashboardPanel],
        layouts: List[Dict[str, Any]],
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
    ) -> int:
        updated_count = 0
        for layout in layouts:
            panel_id = layout.get("panel_id") or layout.get("i")
            panel = panel_map.get(panel_id)
            if not panel:
                continue
            cls._apply_panel_values(panel, cls._layout_values(panel, layout), user_id, operator_name, now)
            updated_count += 1
        return updated_count

    @staticmethod
    def _layout_values(panel: ChatDataDashboardPanel, layout: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "layout_x": layout.get("x", panel.layout_x),
            "layout_y": layout.get("y", panel.layout_y),
            "layout_w": layout.get("w", panel.layout_w),
            "layout_h": layout.get("h", panel.layout_h),
        }

    @staticmethod
    def _touch_dashboard(dashboard: ChatDataDashboard, user_id: Optional[str], operator_name: str, now: datetime) -> None:
        dashboard.update_time = now
        if user_id:
            dashboard.update_by = user_id
        dashboard.update_by_name = operator_name or ""

    @staticmethod
    def _delete_panel_values(user_id: Optional[str], operator_name: str, now: datetime) -> Dict[str, Any]:
        values: Dict[str, Any] = {"delete_flag": 1, "update_time": now}
        if user_id:
            values["update_by"] = user_id
        if operator_name:
            values["update_by_name"] = operator_name
        return values

    @staticmethod
    def _delete_dashboard_values(user_id: Optional[str], operator_name: str, now: datetime) -> Dict[str, Any]:
        values: Dict[str, Any] = {"delete_flag": 1, "update_time": now}
        if user_id:
            values["update_by"] = user_id
        if operator_name:
            values["update_by_name"] = operator_name
        return values

    @staticmethod
    def _new_panel(
        *,
        dashboard_id: str,
        panel_id: str,
        user_id: Optional[str],
        operator_name: str,
        now: datetime,
        layout_x: int,
        layout_y: int,
        panel_values: Dict[str, Any],
    ) -> ChatDataDashboardPanel:
        return ChatDataDashboardPanel(
            panel_id=panel_id,
            dashboard_id=dashboard_id,
            panel_name=panel_values["panel_name"],
            panel_sql=panel_values["panel_sql"],
            chart_type=panel_values["chart_type"],
            chart_config=panel_values.get("chart_config"),
            layout_x=layout_x,
            layout_y=layout_y,
            layout_w=panel_values["layout_w"],
            layout_h=panel_values["layout_h"],
            refresh_interval=panel_values.get("refresh_interval"),
            source_thread_id=panel_values.get("source_thread_id"),
            datasource_id=panel_values.get("datasource_id"),
            member_datasource_id=str(panel_values.get("member_datasource_id") or "").strip(),
            create_time=now,
            update_time=now,
            create_by=user_id,
            create_by_name=operator_name or "",
            update_by=user_id,
            update_by_name=operator_name or "",
        )
