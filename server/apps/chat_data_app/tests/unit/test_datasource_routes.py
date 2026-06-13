import asyncio
import copy
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app import routes
from apps.chat_data_app.route_modules import dashboard as dashboard_routes
from apps.chat_data_app.route_modules import common as common_routes
from apps.chat_data_app.route_modules import semantic_config as semantic_config_routes
from apps.chat_data_app.services import dashboard_spec_service
from apps.chat_data_app.routes import (
    cancel_query_task,
    clear_default_datasource,
    create_query_task,
    create_datasource_group,
    delete_datasource,
    execute_sql_direct,
    get_query_task,
    get_datasources,
    get_datasource_column_dict,
    get_datasource_table_column_dicts,
    get_datasource_table_groups,
    generate_datasource_column_dict,
    _parse_column_dict_generate_response,
    set_default_datasource,
    update_datasource_column_dict,
    update_datasource_table_business_desc,
    update_datasource_table_groups,
    update_datasource_group_config,
    update_datasource_meta,
    update_mapped_modules,
)
from apps.chat_data_app.handlers import _build_execution_target_info_async
from apps.chat_data_app.schema import (
    DashboardFromSpecRequest,
    DbConnectionRequest,
    DatasourceGroupCreateRequest,
    DatasourceGroupUpdateRequest,
    DatasourceColumnDictGenerateRequest,
    DatasourceColumnDictUpdateRequest,
    DatasourceTableBusinessDescUpdateRequest,
    DatasourceTableGroupsUpdateRequest,
    DatasourceUpdateRequest,
    LayoutUpdateRequest,
    PanelBatchExecuteRequest,
    PanelUpdateRequest,
    PanelExecuteRequest,
    QuickSqlRequest,
    QueryTaskCreateRequest,
)


def test_query_history_filter_normalizers():
    assert routes._normalize_query_history_source_filter("all") is None
    assert routes._normalize_query_history_source_filter("sql") == ["sql", ""]
    assert routes._normalize_query_history_source_filter("agent") == ["agent", "conversation"]
    assert routes._normalize_query_history_source_filter("builder") == ["builder"]
    assert routes._normalize_query_history_source_filter("dashboard") == ["dashboard"]
    assert routes._normalize_query_history_source_filter("unknown") is None
    assert routes._normalize_query_history_status_filter("succeeded") == "succeeded"
    assert routes._normalize_query_history_status_filter("cancelled") == "cancelled"
    assert routes._normalize_query_history_status_filter("all") is None


def _payload(resp):
    return json.loads(resp.body.decode("utf-8"))


class _FakeDatasourceManager:
    def __init__(self):
        self.datasources = {
            "ds_a": {
                "id": "ds_a",
                "group_id": "group_ds_a",
                "template_datasource_id": "phy_a",
                "group_name": "数据源A",
                "ds_description": "描述A",
                "source_type": "mysql",
                "query_executor_type": "direct",
                "source_config": {"host": "127.0.0.1", "port": 3306, "database": "db_a"},
                "ds_tables": [{"table_name": "t_a", "description": "t_a", "columns": [{"name": "status", "type": "tinyint", "description": "状态"}]}],
                "create_by": "10001",
                "update_by": "10001",
                "mapped_modules": ["投诉举报"],
                "is_default": True,
                "member_sources": [
                    {"member_datasource_id": "phy_a", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
                ],
                "semantic_configs": [],
            },
            "ds_b": {
                "id": "ds_b",
                "group_id": "group_ds_b",
                "template_datasource_id": "phy_b",
                "group_name": "数据源B",
                "ds_description": "描述B",
                "source_type": "mysql",
                "query_executor_type": "direct",
                "source_config": {"host": "127.0.0.2", "port": 3306, "database": "db_b"},
                "ds_tables": [{"table_name": "t_b", "description": "t_b", "columns": []}],
                "create_by": "10002",
                "update_by": "10002",
                "mapped_modules": [],
                "is_default": False,
                "member_sources": [
                    {"member_datasource_id": "phy_b", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
                ],
                "semantic_configs": [],
            },
        }
        self.last_update_call = None
        self.last_table_groups_operator = None
        self.last_table_business_desc_operator = None
        self.last_column_dict_operator = None
        self.last_delete_update_by = None
        self.deleted_ids = []
        self.column_dicts = {
            ("ds_a", "t_a", "status"): {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": ["状态"], "storage_format": "", "unit": ""},
        }
        self.table_groups = {
            "ds_a": [
                {"table_group_name": "核心表", "table_names": ["t_a"]},
            ],
        }
    def get_all_datasources(self, include_tables: bool = True):
        items = []
        for item in self.datasources.values():
            next_item = copy.deepcopy(item)
            next_item["ds_tables"] = list(item.get("ds_tables") or []) if include_tables else []
            items.append(next_item)
        return items

    async def get_all_datasources_async(self, include_tables: bool = True):
        return self.get_all_datasources(include_tables=include_tables)

    def get_datasource(self, datasource_id: str):
        item = self.datasources.get(datasource_id)
        return copy.deepcopy(item) if item else None

    async def get_datasource_async(self, datasource_id: str):
        return self.get_datasource(datasource_id)

    def has_datasource(self, datasource_id: str):
        return datasource_id in self.datasources

    async def has_datasource_async(self, datasource_id: str):
        return self.has_datasource(datasource_id)

    def get_semantic_configs(self, datasource_id: str):
        item = self.datasources.get(datasource_id)
        return copy.deepcopy(item.get("semantic_configs") or []) if item else []

    async def get_semantic_configs_async(self, datasource_id: str, user_id: str = ""):
        return self.get_semantic_configs(datasource_id)

    def get_semantic_config(self, datasource_id: str, config_id: str):
        for item in self.get_semantic_configs(datasource_id):
            if item.get("id") == config_id or item.get("semantic_config_id") == config_id:
                return item
        return None

    async def get_semantic_config_async(self, datasource_id: str, config_id: str):
        return self.get_semantic_config(datasource_id, config_id)

    def get_datasource_by_group_name(self, name: str):
        for item in self.datasources.values():
            if item["group_name"] == name:
                return copy.deepcopy(item)
        return None

    async def get_datasource_by_group_name_async(self, name: str):
        return self.get_datasource_by_group_name(name)

    def get_table_groups(self, datasource_id: str):
        return copy.deepcopy(self.table_groups.get(datasource_id, []))

    async def get_table_groups_async(self, datasource_id: str):
        return self.get_table_groups(datasource_id)

    def replace_table_groups(self, datasource_id: str, groups: list[dict], operator: str = "", operator_name: str = ""):
        if datasource_id not in self.datasources:
            return False
        self.last_table_groups_operator = operator
        self.table_groups[datasource_id] = copy.deepcopy(groups)
        return True

    async def replace_table_groups_async(self, datasource_id: str, groups: list[dict], operator: str = "", operator_name: str = ""):
        return self.replace_table_groups(datasource_id, groups, operator, operator_name)

    def update_table_business_desc(self, datasource_id: str, table_name: str, business_desc: str, operator: str = "", operator_name: str = ""):
        item = self.datasources.get(datasource_id)
        if not item:
            return None
        self.last_table_business_desc_operator = operator
        for table in item.get("ds_tables") or []:
            if table.get("table_name") == table_name:
                table["business_desc"] = business_desc
                item["update_by"] = operator
                item["update_by_name"] = operator_name
                return copy.deepcopy(table)
        return None

    async def update_table_business_desc_async(self, datasource_id: str, table_name: str, business_desc: str, operator: str = "", operator_name: str = ""):
        return self.update_table_business_desc(datasource_id, table_name, business_desc, operator, operator_name)

    def get_column_dict(self, datasource_id: str, table_name: str, column_name: str):
        datasource = self.datasources.get(datasource_id)
        if not datasource:
            return None
        for table in datasource.get("ds_tables") or []:
            if table.get("table_name") != table_name:
                continue
            if any(column.get("name") == column_name for column in table.get("columns") or []):
                return copy.deepcopy(self.column_dicts.get((datasource_id, table_name, column_name), {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}))
        return None

    async def get_column_dict_async(self, datasource_id: str, table_name: str, column_name: str):
        return self.get_column_dict(datasource_id, table_name, column_name)

    async def get_table_column_dicts_async(self, datasource_id: str, table_name: str):
        datasource = self.datasources.get(datasource_id)
        if not datasource:
            return None
        table = next((item for item in datasource.get("ds_tables") or [] if item.get("table_name") == table_name), None)
        if not table:
            return None
        column_names = {column.get("name") for column in table.get("columns") or []}
        return {
            column_name: copy.deepcopy(column_dict)
            for (ds_id, tbl_name, column_name), column_dict in self.column_dicts.items()
            if ds_id == datasource_id and tbl_name == table_name and column_name in column_names
        }

    def replace_column_dict(self, datasource_id: str, table_name: str, column_name: str, column_dict: dict, operator: str = "", operator_name: str = ""):
        current_dict = self.get_column_dict(datasource_id, table_name, column_name)
        if current_dict is None:
            return None
        self.last_column_dict_operator = operator
        self.column_dicts[(datasource_id, table_name, column_name)] = copy.deepcopy(column_dict)
        return copy.deepcopy(column_dict)

    async def replace_column_dict_async(self, datasource_id: str, table_name: str, column_name: str, column_dict: dict, operator: str = "", operator_name: str = ""):
        return self.replace_column_dict(datasource_id, table_name, column_name, column_dict, operator, operator_name)

    def update_datasource(self, datasource_id: str, **kwargs):
        item = self.datasources.get(datasource_id)
        if not item:
            return None
        self.last_update_call = {"datasource_id": datasource_id, **kwargs}
        if "name" in kwargs and kwargs["name"] is not None:
            item["group_name"] = kwargs["name"]
        if "description" in kwargs and kwargs["description"] is not None:
            item["ds_description"] = kwargs["description"]
        if "mapped_modules" in kwargs and kwargs["mapped_modules"] is not None:
            item["mapped_modules"] = list(kwargs["mapped_modules"])
        if "tables" in kwargs and kwargs["tables"] is not None:
            item["ds_tables"] = list(kwargs["tables"])
        if "source_config" in kwargs and kwargs["source_config"] is not None:
            item["source_config"] = dict(kwargs["source_config"])
        if kwargs.get("update_by"):
            item["update_by"] = kwargs["update_by"]
        return copy.deepcopy(item)

    async def update_datasource_async(self, datasource_id: str, **kwargs):
        return self.update_datasource(datasource_id, **kwargs)

    def update_datasource_group_from_sources(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict,
        member_source_configs: list[dict] | None = None,
        tables: list[dict] | None = None,
        update_by: str = "",
        update_by_name: str = "",
    ):
        item = self.datasources.get(datasource_id)
        if not item:
            return None
        item["group_name"] = group_name
        item["ds_description"] = group_desc
        item["source_type"] = source_type
        item["query_executor_type"] = template_source_config.get("query_executor_type") or "direct"
        item["source_config"] = dict(template_source_config)
        item["ds_tables"] = list(tables or [])
        item["update_by"] = update_by
        item["member_sources"] = [
            {"member_datasource_id": "phy_template", "source_type": source_type, **template_source_config},
            *[
                {"member_datasource_id": f"phy_{index}", "source_type": source_type, **cfg}
                for index, cfg in enumerate(member_source_configs or [], start=1)
            ],
        ]
        return copy.deepcopy(item)

    async def update_datasource_group_from_sources_async(
        self,
        datasource_id: str,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict,
        member_source_configs: list[dict] | None = None,
        tables: list[dict] | None = None,
        update_by: str = "",
        update_by_name: str = "",
    ):
        return self.update_datasource_group_from_sources(
            datasource_id,
            group_name,
            group_desc,
            source_type,
            template_source_config,
            member_source_configs=member_source_configs,
            tables=tables,
            update_by=update_by,
            update_by_name=update_by_name,
        )

    def set_default_datasource(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        if datasource_id not in self.datasources:
            return False
        for item in self.datasources.values():
            item["is_default"] = False
            if update_by:
                item["update_by"] = update_by
        self.datasources[datasource_id]["is_default"] = True
        return True

    async def set_default_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        return self.set_default_datasource(datasource_id, update_by=update_by, update_by_name=update_by_name)

    def clear_default_datasource(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        if datasource_id not in self.datasources or not self.datasources[datasource_id].get("is_default"):
            return False
        self.datasources[datasource_id]["is_default"] = False
        if update_by:
            self.datasources[datasource_id]["update_by"] = update_by
        return True

    async def clear_default_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        return self.clear_default_datasource(datasource_id, update_by=update_by, update_by_name=update_by_name)

    def delete_datasource(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        if datasource_id not in self.datasources:
            return False
        self.last_delete_update_by = update_by
        self.deleted_ids.append(datasource_id)
        del self.datasources[datasource_id]
        return True

    async def delete_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        return self.delete_datasource(datasource_id, update_by=update_by, update_by_name=update_by_name)

    def get_execution_datasources(self, datasource_id: str, member_datasource_ids: list[str] | None = None):
        item = self.datasources.get(datasource_id)
        if not item:
            raise ValueError(f"数据源不存在: {datasource_id}")
        members = list(item.get("member_sources") or [])
        if not members:
            raise ValueError("当前数据源组下没有可执行的数据源")
        member_map = {member["member_datasource_id"]: member for member in members if member.get("member_datasource_id")}
        selected_ids = [member_id for member_id in (member_datasource_ids or []) if member_id]
        if not selected_ids:
            selected_ids = [item.get("template_datasource_id") or members[0]["member_datasource_id"]]
        result = []
        for member_id in selected_ids:
            member = member_map.get(member_id)
            if not member:
                raise ValueError(f"成员数据源不存在或不属于当前组: {member_id}")
            result.append({
                "id": member_id,
                "member_datasource_id": member_id,
                "member_name": member.get("org_name") or member_id,
                "source_type": member.get("source_type") or item.get("source_type"),
                "query_executor_type": member.get("query_executor_type") or item.get("query_executor_type") or "direct",
                "source_config": {
                    "host": member.get("host"),
                    "port": member.get("port"),
                    "database": member.get("database"),
                },
            })
        return result

    async def get_execution_datasources_async(self, datasource_id: str, member_datasource_ids: list[str] | None = None):
        return self.get_execution_datasources(datasource_id, member_datasource_ids)

    def create_datasource_group_from_sources(
        self,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict,
        member_source_configs: list[dict] | None = None,
        tables: list[dict] | None = None,
        create_by: str = "",
    ):
        datasource_id = "ds_group_from_template"
        datasource = {
            "id": datasource_id,
            "group_id": f"group_{datasource_id}",
            "template_datasource_id": "phy_template",
            "member_datasource_ids": ["phy_template", "phy_member_a"],
            "group_name": group_name,
            "ds_description": group_desc,
            "source_type": source_type,
            "query_executor_type": template_source_config.get("query_executor_type") or "direct",
            "source_config": dict(template_source_config),
            "ds_tables": list(tables or []),
            "mapped_modules": [],
            "is_default": False,
            "create_by": create_by,
            "update_by": create_by,
            "semantic_configs": [],
        }
        self.datasources[datasource_id] = datasource
        return copy.deepcopy(datasource)

    async def create_datasource_group_from_sources_async(
        self,
        group_name: str,
        group_desc: str,
        source_type: str,
        template_source_config: dict,
        member_source_configs: list[dict] | None = None,
        tables: list[dict] | None = None,
        create_by: str = "",
        create_by_name: str = "",
    ):
        return self.create_datasource_group_from_sources(
            group_name,
            group_desc,
            source_type,
            template_source_config,
            member_source_configs=member_source_configs,
            tables=tables,
            create_by=create_by,
        )


class _FakeMySQLSyncService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def get_full_schema_async(self, table_names=None):
        names = table_names or ["orders", "users"]
        return [{"table_name": name, "description": name, "columns": [{"name": "id", "type": "bigint", "description": ""}]} for name in names]

    async def get_tables_async(self):
        return [{"table_name": "orders", "description": "订单表"}]


class _FakeHiveSyncService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def get_tables_async(self):
        return [{"table_name": "hive_orders", "description": "Hive订单表"}]


class _FakeSessionContext:
    def __init__(self):
        self.items = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def add(self, item):
        self.items.append(item)

    async def commit(self):
        self.committed = True


class _FakeExecutor:
    def __init__(self, datasource: dict):
        self.datasource = datasource

    async def execute(self, sql: str, limit: int = 100, timeout: int = 30, user_id: str | None = None):
        member_name = self.datasource.get("member_name") or self.datasource.get("member_datasource_id")
        if self.datasource.get("member_datasource_id") == "phy_error":
            return {
                "success": False,
                "columns": [],
                "data": [],
                "total_count": 0,
                "error": f"{member_name} 执行失败",
            }
        return {
            "success": True,
            "columns": ["member_name", "sql_text"],
            "data": [{"member_name": member_name, "sql_text": sql}],
            "total_count": 1,
            "execution_time_ms": 12,
        }

    async def close(self):
        return None


class _FakeDashboardService:
    def __init__(self):
        self.dashboards = {}
        self.panels = {
            "panel_a": {
                "id": "panel_a",
                "dashboard_id": "dash_a",
                "name": "面板A",
                "sql": "select 1",
                "chart_type": "table",
                "chart_config": {},
                "datasource_id": "ds_a",
            },
            "panel_b": {
                "id": "panel_b",
                "dashboard_id": "dash_a",
                "name": "面板B",
                "sql": "select 2",
                "chart_type": "table",
                "chart_config": {},
                "datasource_id": "ds_a",
            },
            "panel_c": {
                "id": "panel_c",
                "dashboard_id": "dash_b",
                "name": "面板C",
                "sql": "select 3",
                "chart_type": "table",
                "chart_config": {},
                "datasource_id": "ds_a",
            },
        }
        self.created_dashboard_id = "dash_created"

    async def get_panel(self, panel_id: str, dashboard_id: str = "", user_id: str = None, include_public: bool = True):
        panel = self.panels.get(panel_id)
        if dashboard_id and panel and panel.get("dashboard_id") != dashboard_id:
            return None
        return copy.deepcopy(panel) if panel else None

    async def get_dashboard(self, dashboard_id: str, user_id: str = None, include_public: bool = True):
        if dashboard_id == "dash_a":
            return {
                "id": dashboard_id,
                "panels": [copy.deepcopy(panel) for panel in self.panels.values() if panel.get("dashboard_id") == dashboard_id],
            }
        dashboard = self.dashboards.get(dashboard_id)
        if not dashboard:
            return None
        result = copy.deepcopy(dashboard)
        result["panels"] = [copy.deepcopy(panel) for panel in self.panels.values() if panel.get("dashboard_id") == dashboard_id]
        return result

    async def create_dashboard(self, name: str, user_id: str, description: str = "", datasource_id: str = None, theme: str = "light", operator_name: str = ""):
        dashboard = {
            "id": self.created_dashboard_id,
            "name": name,
            "description": description,
            "user_id": user_id,
            "datasource_id": datasource_id,
            "theme": theme,
            "create_by_name": operator_name,
            "update_by_name": operator_name,
        }
        self.dashboards[self.created_dashboard_id] = dashboard
        return copy.deepcopy(dashboard)

    async def add_panel(
        self,
        dashboard_id: str,
        name: str,
        sql: str,
        chart_type: str = "table",
        chart_config: dict = None,
        layout_x: int = None,
        layout_y: int = None,
        layout_w: int = 4,
        layout_h: int = 4,
        refresh_interval: int = None,
        source_thread_id: str = None,
        datasource_id: str = None,
        user_id: str = None,
        operator_name: str = "",
    ):
        panel_id = f"panel_created_{len(self.panels) + 1}"
        panel = {
            "id": panel_id,
            "dashboard_id": dashboard_id,
            "name": name,
            "sql": sql,
            "chart_type": chart_type,
            "chart_config": chart_config or {},
            "layout": {
                "x": layout_x,
                "y": layout_y,
                "w": layout_w,
                "h": layout_h,
            },
            "datasource_id": datasource_id,
            "source_thread_id": source_thread_id,
            "user_id": user_id,
            "create_by_name": operator_name,
            "update_by_name": operator_name,
        }
        self.panels[panel_id] = panel
        return copy.deepcopy(panel)

    async def update_panel(
        self,
        dashboard_id: str,
        panel_id: str,
        name: str = None,
        sql: str = None,
        chart_type: str = None,
        chart_config: dict = None,
        layout_x: int = None,
        layout_y: int = None,
        layout_w: int = None,
        layout_h: int = None,
        refresh_interval: int = None,
        user_id: str = None,
    ):
        panel = self.panels.get(panel_id)
        if not panel or panel.get("dashboard_id") != dashboard_id:
            return None
        if name is not None:
            panel["name"] = name
        if sql is not None:
            panel["sql"] = sql
        if chart_type is not None:
            panel["chart_type"] = chart_type
        if chart_config is not None:
            panel["chart_config"] = chart_config
        return copy.deepcopy(panel)

    async def update_panels_layout(self, dashboard_id: str, layouts: list[dict], user_id: str = None, operator_name: str = ""):
        if dashboard_id not in {"dash_a", "dash_b", self.created_dashboard_id}:
            return None
        updated = 0
        for layout in layouts:
            panel_id = layout.get("panel_id") or layout.get("i")
            panel = self.panels.get(panel_id)
            if not panel or panel.get("dashboard_id") != dashboard_id:
                continue
            panel["layout"] = {
                "x": layout.get("x", panel.get("layout", {}).get("x")),
                "y": layout.get("y", panel.get("layout", {}).get("y")),
                "w": layout.get("w", panel.get("layout", {}).get("w")),
                "h": layout.get("h", panel.get("layout", {}).get("h")),
            }
            updated += 1
        return updated

    async def delete_panel(self, dashboard_id: str, panel_id: str, user_id: str = None, operator_name: str = ""):
        panel = self.panels.get(panel_id)
        if not panel or panel.get("dashboard_id") != dashboard_id:
            return False
        self.panels.pop(panel_id, None)
        return True

    async def delete_dashboard(self, dashboard_id: str, user_id: str = None, operator_name: str = ""):
        self.dashboards.pop(dashboard_id, None)
        return True


async def _collect_stream_chunks(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return chunks


def _collect_stream_chunks_sync(response):
    return asyncio.run(_collect_stream_chunks(response))


def _mock_authenticated_user(monkeypatch, user_id: str = "10001", username: str = "测试用户"):
    monkeypatch.setattr(
        common_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id=user_id, username=username),
    )
    monkeypatch.setattr(
        routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id=user_id, username=username),
    )
    monkeypatch.setattr(
        dashboard_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id=user_id, username=username),
    )
    monkeypatch.setattr(
        semantic_config_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id=user_id, username=username),
    )


def _mock_query_tracking(monkeypatch, task_id: str = "task_001"):
    updates = []

    async def fake_create_task(_request, **_kwargs):
        return {"task_id": task_id, "task_type": "sql", "status": "pending"}

    async def fake_create_task_record(**kwargs):
        return {"task_id": task_id, "task_type": kwargs.get("task_type") or "sql", "status": "pending"}

    async def fake_create_executions(_task_id, _request, operator_name=None):
        assert _task_id == task_id
        return {}

    async def fake_update_task(_task_id, **kwargs):
        updates.append((_task_id, kwargs))
        return None

    monkeypatch.setattr(routes, "_create_task_for_execute_request", fake_create_task)
    monkeypatch.setattr(routes, "_create_task_record", fake_create_task_record)
    monkeypatch.setattr(routes, "_create_query_executions_for_task", fake_create_executions)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_task)
    return updates


def test_get_datasources_returns_group_datasource_list(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(get_datasources(SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert len(data["data"]["datasources"]) == 2
    assert data["data"]["datasources"][0]["group_id"].startswith("group_")


def test_get_datasources_supports_lightweight_mode(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(get_datasources(SimpleNamespace(), include_tables=False))
    data = _payload(resp)

    assert data["status"] == "success"
    assert all(item["ds_tables"] == [] for item in data["data"]["datasources"])


def test_get_datasources_does_not_build_display_name_from_current_user(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "_optional_current_user", lambda _request: SimpleNamespace(user_id="10001", username="测试用户"))

    resp = asyncio.run(get_datasources(request=SimpleNamespace()))
    data = _payload(resp)
    datasources = data["data"]["datasources"]

    assert datasources[0]["create_by"] == "10001"
    assert "create_by_name" not in datasources[0]
    assert "create_by_name" not in datasources[1]


def test_quick_sql_endpoint_returns_errors_for_missing_tables_and_conversation(monkeypatch):
    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[]))

    no_tables_response = asyncio.run(routes.quick_sql_endpoint(QuickSqlRequest(
        query="帮我生成 SQL",
        datasource_id="ds_a",
        conversation_id="conv_a",
    ), SimpleNamespace()))
    no_tables_chunks = _collect_stream_chunks_sync(no_tables_response)

    assert "数据源暂无表结构信息" in "".join(no_tables_chunks)

    monkeypatch.setattr(routes, "get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[
        {"table_name": "orders", "columns": [{"name": "id", "type": "bigint"}]},
    ]))
    monkeypatch.setattr(routes, "get_tables_create_format", lambda tables, names: "CREATE TABLE orders(id bigint)")
    monkeypatch.setattr(routes, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result="默认执行对象"))

    missing_conversation_response = asyncio.run(routes.quick_sql_endpoint(QuickSqlRequest(
        query="帮我生成 SQL",
        datasource_id="ds_a",
    ), SimpleNamespace()))
    missing_conversation_chunks = _collect_stream_chunks_sync(missing_conversation_response)

    assert "缺少 SQL 辅助会话ID" in "".join(missing_conversation_chunks)


def test_quick_sql_endpoint_streams_chunks_and_extracts_sql(monkeypatch):
    class FakeChunk:
        def __init__(self, content):
            self.content = content

    class FakeGraph:
        async def astream(self, initial_state, config=None, stream_mode=None):
            assert initial_state["datasource_id"] == "ds_a"
            assert initial_state["execution_target_info"] == "默认执行对象"
            assert config["configurable"]["thread_id"] == "conv_a"
            assert stream_mode == "messages"
            yield FakeChunk("忽略"), {"langgraph_node": "other"}
            yield FakeChunk("```sql\nSELECT 1\n```"), {"langgraph_node": "sql_assist"}

    class FakeCheckpointer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["source_type"] = "hive"
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[
        {"table_name": "orders", "columns": [{"name": "id", "type": "bigint"}]},
    ]))
    monkeypatch.setattr(routes, "get_tables_create_format", lambda tables, names: "CREATE TABLE orders(id bigint)")
    monkeypatch.setattr(routes, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result="默认执行对象"))
    monkeypatch.setattr(routes.conversation, "get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(routes.conversation, "get_agent_id", lambda *_args, **_kwargs: asyncio.sleep(0, result=routes.SQL_ASSIST_AGENT_ID))
    monkeypatch.setattr(routes, "get_sql_assist_graph", lambda *_args, **_kwargs: FakeGraph())
    _mock_authenticated_user(monkeypatch, user_id="sql_user", username="SQL用户")

    from apps.chat_data_app import agent as agent_module

    monkeypatch.setattr(agent_module, "get_checkpointer", lambda: FakeCheckpointer())

    response = asyncio.run(routes.quick_sql_endpoint(QuickSqlRequest(
        query="帮我生成 SQL",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a"],
        conversation_id="conv_a",
    ), SimpleNamespace()))
    chunks = _collect_stream_chunks_sync(response)
    text = "".join(chunks)

    assert '"type": "chunk"' in text
    assert "SELECT 1" in text
    assert '"type": "complete"' in text
    assert '"sql": "SELECT 1"' in text


def test_quick_sql_endpoint_streams_plain_content_when_no_sql_block(monkeypatch):
    class FakeChunk:
        def __init__(self, content):
            self.content = content

    class FakeGraph:
        async def astream(self, *_args, **_kwargs):
            yield FakeChunk("SELECT 2"), {"langgraph_node": "sql_assist"}

    class FakeCheckpointer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(routes, "get_datasource_manager", lambda: _FakeDatasourceManager())
    monkeypatch.setattr(routes, "get_all_tables_async", lambda _datasource_id: asyncio.sleep(0, result=[
        {"table_name": "orders", "columns": [{"name": "id", "type": "bigint"}]},
    ]))
    monkeypatch.setattr(routes, "get_tables_create_format", lambda tables, names: "CREATE TABLE orders(id bigint)")
    monkeypatch.setattr(routes, "_build_execution_target_info_async", lambda *_args, **_kwargs: asyncio.sleep(0, result="默认执行对象"))
    monkeypatch.setattr(routes.conversation, "get_or_create_conversation", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
    monkeypatch.setattr(routes.conversation, "get_agent_id", lambda *_args, **_kwargs: asyncio.sleep(0, result=routes.SQL_ASSIST_AGENT_ID))
    monkeypatch.setattr(routes, "get_sql_assist_graph", lambda *_args, **_kwargs: FakeGraph())
    from apps.chat_data_app import agent as agent_module

    monkeypatch.setattr(agent_module, "get_checkpointer", lambda: FakeCheckpointer())

    response = asyncio.run(routes.quick_sql_endpoint(QuickSqlRequest(
        query="帮我生成 SQL",
        datasource_id="ds_a",
        conversation_id="conv_a",
    ), None))

    assert '"sql": "SELECT 2"' in "".join(_collect_stream_chunks_sync(response))


def test_list_semantic_configs_uses_lightweight_existence_check(monkeypatch):
    manager = _FakeDatasourceManager()
    calls = []

    async def _get_datasource(_datasource_id: str):
        raise AssertionError("should not load datasource detail")

    async def _has_datasource(datasource_id: str):
        calls.append(("has_datasource", datasource_id))
        return datasource_id == "ds_a"

    async def _get_semantic_configs(datasource_id: str, user_id: str = ""):
        calls.append(("get_semantic_configs", datasource_id))
        return [{"semantic_config_id": "cfg_1"}]

    manager.get_datasource_async = _get_datasource
    manager.has_datasource_async = _has_datasource
    manager.get_semantic_configs_async = _get_semantic_configs
    monkeypatch.setattr(semantic_config_routes, "get_datasource_manager", lambda: manager)
    _mock_authenticated_user(monkeypatch)

    resp = asyncio.run(semantic_config_routes.list_semantic_configs("ds_a", SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["configs"][0]["semantic_config_id"] == "cfg_1"
    assert calls == [("has_datasource", "ds_a"), ("get_semantic_configs", "ds_a")]


def test_datasource_semantic_health_reports_missing_fields(monkeypatch):
    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["ds_tables"] = [
        {
            "table_name": "orders",
            "description": "orders",
            "columns": [
                {"name": "order_id", "type": "bigint"},
                {"name": "user_id", "type": "bigint"},
                {"name": "amount", "type": "decimal"},
            ],
        },
        {
            "table_name": "users",
            "description": "users",
            "columns": [{"name": "user_id", "type": "bigint"}],
        },
    ]
    manager.datasources["ds_a"]["semantic_configs"] = [{
        "id": "sc_a",
        "name": "订单分析",
        "metrics": [{"name": "销售额", "table": "orders", "expression": "SUM(pay_amount)"}],
        "dimensions": [{"name": "订单状态", "table": "orders", "column": "status", "expression": "`status`"}],
        "relationships": [{
            "left_table": "orders",
            "right_table": "users",
            "left_column": "buyer_id",
            "right_column": "user_id",
            "join_conditions": [{"left_column": "buyer_id", "right_column": "user_id"}],
        }],
    }]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    async def _fake_schema_health(datasource):
        return {
            "status": "ok",
            "summary": {"cached_table_count": 2, "physical_table_count": 2, "issue_count": 0, "error_count": 0, "warning_count": 0},
            "issues": [],
        }
    monkeypatch.setattr(routes, "_build_datasource_schema_health", _fake_schema_health)

    resp = asyncio.run(routes.get_datasource_semantic_health("ds_a"))
    data = _payload(resp)
    issue_types = {item["type"] for item in data["data"]["issues"]}

    assert data["status"] == "success"
    assert data["data"]["status"] == "invalid"
    assert "missing_column" in issue_types
    assert "missing_join_column" in issue_types
    assert "expression_column_missing" in issue_types


def test_datasource_semantic_health_allows_count_all_metric(monkeypatch):
    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["ds_tables"] = [
        {
            "table_name": "orders",
            "description": "orders",
            "columns": [{"name": "order_id", "type": "bigint"}],
        },
    ]
    manager.datasources["ds_a"]["semantic_configs"] = [{
        "id": "sc_a",
        "name": "订单分析",
        "metrics": [{"name": "订单数量", "table": "orders", "expression": "COUNT(*)"}],
        "dimensions": [],
        "relationships": [],
    }]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    async def _fake_schema_health(datasource):
        return {
            "status": "ok",
            "summary": {"cached_table_count": 1, "physical_table_count": 1, "issue_count": 0, "error_count": 0, "warning_count": 0},
            "issues": [],
        }
    monkeypatch.setattr(routes, "_build_datasource_schema_health", _fake_schema_health)

    resp = asyncio.run(routes.get_datasource_semantic_health("ds_a"))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["status"] == "ok"
    assert data["data"]["issues"] == []


def test_datasource_semantic_health_allows_common_sql_expressions(monkeypatch):
    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["ds_tables"] = [
        {
            "table_name": "orders",
            "description": "orders",
            "columns": [
                {"name": "order_id", "type": "bigint"},
                {"name": "user_id", "type": "bigint"},
                {"name": "amount", "type": "decimal"},
                {"name": "created_at", "type": "datetime"},
                {"name": "status", "type": "varchar"},
            ],
        },
    ]
    manager.datasources["ds_a"]["semantic_configs"] = [{
        "id": "sc_a",
        "name": "订单分析",
        "metrics": [
            {"name": "销售额", "table": "orders", "expression": "SUM(`orders`.`amount`)"},
            {"name": "用户数", "table": "orders", "expression": "COUNT(DISTINCT user_id)"},
        ],
        "dimensions": [
            {"name": "月份", "table": "orders", "column": "created_at", "expression": "DATE_FORMAT(created_at, '%Y-%m')"},
            {"name": "状态分组", "table": "orders", "column": "status", "expression": "CASE WHEN status = 'paid' THEN '已支付' ELSE '其他' END"},
        ],
        "relationships": [],
    }]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    async def _fake_schema_health(datasource):
        return {
            "status": "ok",
            "summary": {"cached_table_count": 1, "physical_table_count": 1, "issue_count": 0, "error_count": 0, "warning_count": 0},
            "issues": [],
        }
    monkeypatch.setattr(routes, "_build_datasource_schema_health", _fake_schema_health)

    resp = asyncio.run(routes.get_datasource_semantic_health("ds_a"))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["status"] == "ok"
    assert data["data"]["issues"] == []


def test_datasource_semantic_health_reports_physical_schema_drift(monkeypatch):
    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["ds_tables"] = [
        {
            "table_name": "orders",
            "description": "orders",
            "columns": [
                {"name": "order_id", "type": "bigint"},
                {"name": "old_status", "type": "varchar"},
            ],
        },
    ]
    manager.datasources["ds_a"]["semantic_configs"] = []
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    async def _fake_load_tables(source_type, source_item, table_names=None):
        return [
            {
                "table_name": "orders",
                "description": "orders",
                "columns": [
                    {"name": "order_id", "type": "bigint"},
                    {"name": "new_status", "type": "varchar"},
                ],
            },
            {
                "table_name": "payments",
                "description": "payments",
                "columns": [{"name": "payment_id", "type": "bigint"}],
            },
        ]

    monkeypatch.setattr(routes, "_load_tables_from_source", _fake_load_tables)

    resp = asyncio.run(routes.get_datasource_semantic_health("ds_a"))
    data = _payload(resp)
    schema_issue_types = {item["type"] for item in data["data"]["schema"]["issues"]}

    assert data["status"] == "success"
    assert data["data"]["status"] == "invalid"
    assert data["data"]["schema"]["status"] == "invalid"
    assert "physical_column_removed" in schema_issue_types
    assert "physical_column_added" not in schema_issue_types
    assert "physical_table_added" not in schema_issue_types


def test_semantic_prefill_passes_table_model_type_to_llm(monkeypatch):
    from apps.chat_data_app.schema import SemanticPrefillRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["semantic_configs"] = [{"id": "sc_a", "name": "订单分析"}]
    monkeypatch.setattr(semantic_config_routes, "get_datasource_manager", lambda: manager)

    captured = {}

    async def _fake_prefill(tables, table_model_type="detail", generate_business_flow=False, business_context=""):
        captured["tables"] = tables
        captured["table_model_type"] = table_model_type
        captured["generate_business_flow"] = generate_business_flow
        captured["business_context"] = business_context
        return {"fields": [{"table": "orders", "column": "amount_sum", "role": "metric", "expression": "`amount_sum`"}], "knowledge": {}}

    monkeypatch.setattr(semantic_config_routes, "_prefill_by_llm", _fake_prefill)

    resp = asyncio.run(semantic_config_routes.semantic_prefill(SemanticPrefillRequest(
        datasource_id="ds_a",
        config_id="sc_a",
        table_model_type="aggregated",
        tables=[{
            "table_name": "orders",
            "columns": [{"name": "amount_sum", "type": "decimal", "description": "销售额合计"}],
        }],
    )))
    data = _payload(resp)

    assert data["status"] == "success"
    assert captured["table_model_type"] == "aggregated"
    assert captured["tables"][0]["table_name"] == "orders"


def test_prefill_by_llm_detail_mode_uses_llm_display_names(monkeypatch):
    captured = {}

    async def _fake_recommend(table_payload, business_context="", max_metrics=10, **_kwargs):
        captured["table_payload"] = table_payload
        captured["business_context"] = business_context
        captured["max_metrics"] = max_metrics
        return {
            "fields": [
                {"table": "orders", "table_alias": "orders", "column": "order_id", "role": "dimension", "expression": "`order_id`", "name": "订单编号"},
                {"table": "orders", "table_alias": "orders", "column": "amount", "role": "dimension", "expression": "`amount`", "name": "订单金额"},
            ],
            "metrics": [],
        }

    monkeypatch.setattr(semantic_config_routes, "_call_metric_recommendation_llm", _fake_recommend)

    result = asyncio.run(semantic_config_routes._prefill_by_llm([{
        "table_name": "orders",
        "columns": [
            {"name": "order_id", "type": "bigint"},
            {"name": "amount", "type": "decimal"},
        ],
    }], "detail"))

    assert captured["table_payload"][0]["table"] == "orders"
    assert result["fields"] == [
        {"table": "orders", "table_alias": "orders", "column": "order_id", "role": "dimension", "expression": "`order_id`", "name": "订单编号"},
        {"table": "orders", "table_alias": "orders", "column": "amount", "role": "dimension", "expression": "`amount`", "name": "订单金额"},
    ]


def test_execute_sql_direct_returns_selected_member_results(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "org_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "org_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_b"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["member_datasource_id"] == "phy_b"
    assert len(data["data"]["results"]) == 1
    assert data["data"]["results"][0]["member_name"] == "成员B"


def test_execute_sql_direct_returns_multi_member_results(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "org_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "org_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a", "phy_b"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["member_datasource_id"] == "phy_a"
    assert len(data["data"]["results"]) == 2
    assert data["data"]["results"][0]["member_name"] == "成员A"
    assert data["data"]["results"][1]["member_name"] == "成员B"


def test_execute_sql_direct_does_not_persist_http_result_payload(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_http", "org_name": "HTTP成员", "member_name": "HTTP成员", "source_type": "mysql", "query_executor_type": "http", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    updates = _mock_query_tracking(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_http"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["member_datasource_id"] == "phy_http"
    assert updates[-1][1]["status"] == "succeeded"
    assert updates[-1][1]["result_payload"] is None


def test_create_query_task_schedules_background_run(monkeypatch):
    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    _mock_authenticated_user(monkeypatch)

    captured = {}

    async def fake_create_task(payload):
        captured["payload"] = payload
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "status": "pending",
        }

    def fake_schedule(task_id: str):
        captured["scheduled_task_id"] = task_id

    monkeypatch.setattr(routes, "_create_query_task", fake_create_task)
    monkeypatch.setattr(routes, "_schedule_query_task", fake_schedule)

    resp = asyncio.run(create_query_task(SimpleNamespace(), QueryTaskCreateRequest(
        task_type="sql",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a"],
        sql="select 1",
        limit=100,
        timeout=30,
        query_type="sql",
    )))

    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "task_001"
    assert data["data"]["status"] == "pending"
    assert captured["payload"].datasource_id == "ds_a"
    assert captured["payload"].user_id == "10001"
    assert captured["payload"].operator_name == "测试用户"
    assert captured["scheduled_task_id"] == "task_001"


def test_get_query_task_returns_task_status(monkeypatch):
    _mock_authenticated_user(monkeypatch)

    async def fake_get_query_task(task_id: str):
        assert task_id == "task_001"
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "builder",
            "status": "succeeded",
            "progress_text": "执行完成",
            "request_payload": "{\"sql\": \"select 1\", \"user_id\": \"10001\"}",
            "result_payload": "{\"sql\": \"select 1\", \"columns\": [\"col_a\"], \"data\": [{\"col_a\": 1}], \"total_count\": 1, \"results\": []}",
            "remote_task_payload": "[{\"member_datasource_id\": \"phy_a\", \"member_name\": \"成员A\", \"remote_task_id\": \"remote_001\"}]",
            "error_message": None,
            "started_time": "2026-03-20T10:00:00",
            "finished_time": "2026-03-20T10:00:05",
            "create_time": "2026-03-20T10:00:00",
            "update_time": "2026-03-20T10:00:05",
        }

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_executions", lambda _task_id: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(routes, "_build_live_remote_sql_result", lambda *_args: asyncio.sleep(0, result={
        "status": "succeeded",
        "progress_text": "执行完成",
        "error_message": None,
        "result_payload": {
            "success": True,
            "sql": "select 1",
            "columns": ["col_a"],
            "data": [{"col_a": 1}],
            "total_count": 1,
            "results": [{
                "member_datasource_id": "phy_a",
                "member_name": "成员A",
                "success": True,
                "columns": ["col_a"],
                "data": [{"col_a": 1}],
                "total_count": 1,
                "execution_time_ms": 12,
                "error": None,
            }],
        },
    }))

    resp = asyncio.run(get_query_task("task_001", SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "task_001"
    assert data["data"]["status"] == "succeeded"
    assert data["data"]["remote_tasks"] == [{"member_datasource_id": "phy_a", "member_name": "成员A", "remote_task_id": "remote_001", "source_type": "hive"}]
    assert data["data"]["result"] is None
    assert data["data"]["results"] == [{
        "member_datasource_id": "phy_a",
        "member_name": "成员A",
        "success": True,
        "columns": ["col_a"],
        "data": [{"col_a": 1}],
        "total_count": 1,
        "execution_time_ms": 12,
        "error": None,
    }]


def test_get_query_task_rejects_other_user(monkeypatch):
    _mock_authenticated_user(monkeypatch, user_id="user_b")

    async def fake_get_query_task(_task_id: str):
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "builder",
            "status": "succeeded",
            "progress_text": "执行完成",
            "request_payload": "{\"sql\": \"select 1\", \"user_id\": \"user_a\"}",
            "result_payload": "{\"success\": true, \"results\": []}",
            "remote_task_payload": None,
            "error_message": None,
            "started_time": None,
            "finished_time": None,
            "create_time": None,
            "update_time": None,
        }

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_executions", lambda _task_id: asyncio.sleep(0, result=[]))

    resp = asyncio.run(get_query_task("task_001", SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "error"
    assert data["code"] == 40404
    assert "任务不存在" in data["msg"]


def test_live_remote_sql_result_keeps_local_cancelled_status(monkeypatch):
    async def fake_fetch(_remote_task_id: str, _source_type: str = "hive", **_kwargs):
        return {
            "success": True,
            "status": "succeeded",
            "columns": ["col_a"],
            "data": [{"col_a": 1}],
            "total_count": 1,
            "execution_time_ms": 12,
            "error": None,
        }

    monkeypatch.setattr(routes, "fetch_http_query_task_result", fake_fetch)

    result = asyncio.run(routes._build_live_remote_sql_result(
        {
            "request_payload": {"sql": "select 1"},
            "remote_tasks": [{"member_datasource_id": "phy_a", "member_name": "成员A", "remote_task_id": "remote_001", "source_type": "hive"}],
            "result_payload": None,
        },
        [{"member_datasource_id": "phy_a", "sql_status": "cancelled", "error_message": "已人工取消"}],
    ))

    assert result["status"] == "cancelled"
    assert result["progress_text"] == "已停止"
    assert result["result_payload"]["success"] is False
    assert result["result_payload"]["results"][0]["error"] == "已人工取消"


def test_cancel_query_task_stops_remote_execution(monkeypatch):
    _mock_authenticated_user(monkeypatch)
    updates = []
    stopped = []
    now = routes.datetime.now()
    execution = SimpleNamespace(
        id=1,
        execution_id="exec_001",
        task_id="task_001",
        user_id="10001",
        datasource_id="ds_a",
        member_datasource_id="phy_a",
        member_name="成员A",
        executor_type="http",
        sql_text="select 1",
        sql_status="running",
        remote_task_id="remote_001",
        db_connection_id="",
        execution_time_ms=None,
        result_row_count=None,
        result_payload=None,
        error_message=None,
        started_time=now,
        finished_time=None,
        create_time=now,
        update_time=now,
        create_by="10001",
        update_by="10001",
    )

    async def fake_get_query_task(_task_id: str):
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "status": "running",
            "request_payload": json.dumps({"user_id": "10001", "operator_name": "测试用户"}),
        }

    async def fake_stop(remote_task_id: str, source_type: str = "hive", **kwargs):
        stopped.append((remote_task_id, source_type, kwargs))
        return {"success": True}

    async def fake_update_execution(execution_id: str, **kwargs):
        updates.append(("execution", execution_id, kwargs))

    async def fake_update_task(task_id: str, **kwargs):
        updates.append(("task", task_id, kwargs))

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_execution_models", lambda _task_id: asyncio.sleep(0, result=[execution]))
    monkeypatch.setattr(routes, "_resolve_execution_target", lambda _execution: asyncio.sleep(0, result={"source_type": "hive"}))
    monkeypatch.setattr(routes, "stop_http_query_task", fake_stop)
    monkeypatch.setattr(routes, "_update_query_execution", fake_update_execution)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_task)

    resp = asyncio.run(cancel_query_task("task_001", SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["status"] == "cancelled"
    assert stopped == [("remote_001", "hive", {"user_id": "10001", "username": "测试用户"})]
    assert updates[0][0] == "execution"
    assert updates[0][2]["sql_status"] == "cancelled"
    assert updates[-1][0] == "task"
    assert updates[-1][2]["status"] == "cancelled"


def test_cancel_query_task_rejects_other_user(monkeypatch):
    _mock_authenticated_user(monkeypatch, user_id="user_b", username="user_b")
    execution = SimpleNamespace(user_id="user_a")

    async def fake_get_query_task(_task_id: str):
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "status": "running",
            "request_payload": json.dumps({"user_id": "user_a"}),
        }

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_execution_models", lambda _task_id: asyncio.sleep(0, result=[execution]))

    resp = asyncio.run(cancel_query_task("task_001", SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "error"
    assert data["code"] == 40301


def test_cancel_query_task_cleans_stale_running_execution_when_task_finished(monkeypatch):
    _mock_authenticated_user(monkeypatch)
    updates = []
    execution = SimpleNamespace(
        execution_id="exec_001",
        task_id="task_001",
        user_id="10001",
        datasource_id="ds_a",
        member_datasource_id="phy_a",
        sql_status="running",
        remote_task_id="",
        db_connection_id="",
    )

    async def fake_get_query_task(_task_id: str):
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "status": "succeeded",
            "request_payload": json.dumps({"user_id": "10001"}),
        }

    async def fake_update_execution(execution_id: str, **kwargs):
        updates.append((execution_id, kwargs))

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_execution_models", lambda _task_id: asyncio.sleep(0, result=[execution]))
    monkeypatch.setattr(routes, "_get_query_execution_model", lambda _execution_id: asyncio.sleep(0, result=execution))
    monkeypatch.setattr(routes, "_update_query_execution", fake_update_execution)
    monkeypatch.setattr(routes, "_update_query_task", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应更新已结束任务主状态")))

    resp = asyncio.run(cancel_query_task("task_001", SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["status"] == "cancelled"
    assert updates[-1][1]["sql_status"] == "cancelled"
    assert updates[-1][1]["error_message"] == "已人工取消"


def test_cancel_query_task_kills_mysql_execution(monkeypatch):
    _mock_authenticated_user(monkeypatch)
    killed = []
    execution = SimpleNamespace(
        execution_id="exec_001",
        task_id="task_001",
        user_id="10001",
        datasource_id="ds_a",
        member_datasource_id="phy_a",
        sql_status="running",
        remote_task_id="",
        db_connection_id="123",
    )

    async def fake_get_query_task(_task_id: str):
        return {
            "task_id": "task_001",
            "task_type": "sql",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "status": "running",
            "request_payload": json.dumps({"user_id": "10001"}),
        }

    async def fake_kill(target: dict, connection_id: str):
        killed.append((target, connection_id))
        return {"success": True}

    monkeypatch.setattr(routes, "_get_query_task", fake_get_query_task)
    monkeypatch.setattr(routes, "_get_query_execution_models", lambda _task_id: asyncio.sleep(0, result=[execution]))
    monkeypatch.setattr(routes, "_resolve_execution_target", lambda _execution: asyncio.sleep(0, result={"source_type": "mysql"}))
    monkeypatch.setattr(routes, "kill_mysql_query", fake_kill)
    monkeypatch.setattr(routes, "_update_query_execution", lambda *_args, **_kwargs: asyncio.sleep(0))
    monkeypatch.setattr(routes, "_update_query_task", lambda *_args, **_kwargs: asyncio.sleep(0))

    resp = asyncio.run(cancel_query_task("task_001", SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "success"
    assert killed == [({"source_type": "mysql"}, "123")]


def test_cancel_query_execution_waits_for_mysql_connection_id(monkeypatch):
    killed = []
    updated = []
    execution = SimpleNamespace(
        execution_id="exec_001",
        datasource_id="ds_a",
        member_datasource_id="phy_a",
        sql_status="running",
        remote_task_id="",
        db_connection_id="",
    )
    latest_execution = SimpleNamespace(
        execution_id="exec_001",
        datasource_id="ds_a",
        member_datasource_id="phy_a",
        sql_status="running",
        remote_task_id="",
        db_connection_id="456",
    )

    async def fake_kill(target: dict, connection_id: str):
        killed.append((target, connection_id))
        return {"success": True}

    async def fake_update_execution(execution_id: str, **kwargs):
        updated.append((execution_id, kwargs))

    monkeypatch.setattr(routes, "CANCEL_TARGET_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(routes, "CANCEL_TARGET_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(routes, "_get_query_execution_model", lambda _execution_id: asyncio.sleep(0, result=latest_execution))
    monkeypatch.setattr(routes, "_resolve_execution_target", lambda _execution: asyncio.sleep(0, result={"source_type": "mysql"}))
    monkeypatch.setattr(routes, "kill_mysql_query", fake_kill)
    monkeypatch.setattr(routes, "_update_query_execution", fake_update_execution)

    result = asyncio.run(routes._cancel_query_execution(execution))

    assert result["status"] == "cancelled"
    assert killed == [({"source_type": "mysql"}, "456")]
    assert updated[-1][1]["sql_status"] == "cancelled"


def test_cancel_query_execution_running_without_target_marks_kill_failed(monkeypatch):
    updated = []
    execution = SimpleNamespace(
        execution_id="exec_001",
        datasource_id="ds_a",
        member_datasource_id="phy_a",
        sql_status="running",
        remote_task_id="",
        db_connection_id="",
    )

    async def fake_update_execution(execution_id: str, **kwargs):
        updated.append((execution_id, kwargs))

    monkeypatch.setattr(routes, "CANCEL_TARGET_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(routes, "CANCEL_TARGET_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(routes, "_get_query_execution_model", lambda _execution_id: asyncio.sleep(0, result=execution))
    monkeypatch.setattr(routes, "_update_query_execution", fake_update_execution)

    result = asyncio.run(routes._cancel_query_execution(execution))

    assert result["status"] == "kill_failed"
    assert result["success"] is False
    assert updated[-1][1]["sql_status"] == "kill_failed"


def test_run_sql_query_task_returns_when_already_cancelled(monkeypatch):
    called = {"build": False, "update": False}

    async def fake_build_result(*_args, **_kwargs):
        called["build"] = True
        return {}

    async def fake_update_task(*_args, **_kwargs):
        called["update"] = True
        return None

    monkeypatch.setattr(routes, "_is_query_task_cancelled", lambda _task_id: asyncio.sleep(0, result=True))
    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_task)

    asyncio.run(routes._run_sql_query_task("task_001", {
        "request_payload": json.dumps({
            "sql": "select 1",
            "datasource_id": "ds_a",
            "member_datasource_ids": ["phy_a"],
        })
    }))

    assert called == {"build": False, "update": False}


def test_run_sql_query_task_does_not_finish_after_cancelled(monkeypatch):
    updates = []
    cancel_checks = iter([False, False, True])

    async def fake_build_result(*_args, **_kwargs):
        return {
            "success": True,
            "sql": "select 1",
            "columns": ["col_a"],
            "data": [{"col_a": 1}],
            "total_count": 1,
            "execution_time_ms": 12,
            "results": [],
        }

    async def fake_update_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    async def fake_is_cancelled(_task_id):
        return next(cancel_checks)

    monkeypatch.setattr(routes, "_is_query_task_cancelled", fake_is_cancelled)
    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_task)
    monkeypatch.setattr(routes, "_are_all_sql_execution_targets_http", lambda _request: asyncio.sleep(0, result=False))
    monkeypatch.setattr(routes, "_create_query_executions_for_task", lambda _task_id, _request, operator_name=None: asyncio.sleep(0, result={}))

    asyncio.run(routes._run_sql_query_task("task_001", {
        "request_payload": json.dumps({
            "sql": "select 1",
            "datasource_id": "ds_a",
            "member_datasource_ids": ["phy_a"],
        })
    }))

    assert len(updates) == 1
    assert updates[0][1]["status"] == "running"


def test_run_sql_query_task_skips_result_payload_for_all_http_targets(monkeypatch):
    updates = []

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["query_executor_type"] = "http"
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_http", "org_name": "HTTP成员", "source_type": "mysql", "query_executor_type": "http", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    async def fake_build_result(_request, query_task_id=None, execution_map=None, **_kwargs):
        assert query_task_id == "task_001"
        assert execution_map == {}
        return {
            "success": True,
            "sql": "select 1",
            "columns": ["col_a"],
            "data": [{"col_a": 1}],
            "total_count": 1,
            "execution_time_ms": 12,
            "results": [],
        }

    async def fake_update_query_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)
    monkeypatch.setattr(routes, "_is_query_task_cancelled", lambda _task_id: asyncio.sleep(0, result=False))
    monkeypatch.setattr(routes, "_create_query_executions_for_task", lambda _task_id, _request, operator_name=None: asyncio.sleep(0, result={}))

    asyncio.run(routes._run_sql_query_task("task_001", {
        "request_payload": json.dumps({
            "sql": "select 1",
            "datasource_id": "ds_a",
            "member_datasource_ids": ["phy_http"],
            "operator_name": "TestUser",
        })
    }))

    assert updates[1][1]["status"] == "succeeded"
    assert updates[1][1]["result_payload"] is None


def test_execute_sql_direct_keeps_failed_member_result(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "org_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_error", "org_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a", "phy_error"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert len(data["data"]["results"]) == 2
    assert data["data"]["results"][1]["success"] is False
    assert "执行失败" in data["data"]["results"][1]["error"]


def test_execute_sql_uses_query_cache_when_enabled(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "QUERY_CACHE_ENABLED", True)
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    async def fake_get_cache(cache_identity):
        assert cache_identity["datasource_id"] == "ds_a"
        assert cache_identity["member_datasource_id"] == "phy_a"
        return {
            "success": True,
            "columns": ["cached_col"],
            "data": [{"cached_col": 1}],
            "total_count": 1,
            "execution_time_ms": 1,
            "cache_hit": True,
        }

    def raise_get_executor(_datasource):
        raise AssertionError("cache hit should not execute datasource")

    monkeypatch.setattr(routes, "_get_sql_result_cache", fake_get_cache)
    monkeypatch.setattr(routes, "get_executor", raise_get_executor)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a"],
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["cache_hit"] is True
    assert data["data"]["columns"] == ["cached_col"]


def test_get_sql_result_cache_returns_current_read_elapsed(monkeypatch):
    from apps.chat_data_app import routes

    cache_item = SimpleNamespace(
        result_payload=json.dumps({
            "success": True,
            "columns": ["用户数量"],
            "data": [{"用户数量": 50}],
            "total_count": 1,
            "execution_time_ms": 503,
            "error": None,
        }, ensure_ascii=False),
        create_time=datetime.now() - timedelta(seconds=20),
        expire_time=datetime.now() + timedelta(seconds=60),
        hit_count=0,
        last_hit_time=None,
        update_time=None,
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return cache_item

    class FakeSession:
        committed = False

        async def execute(self, _stmt):
            return FakeResult()

        async def commit(self):
            self.committed = True

    session = FakeSession()

    @asynccontextmanager
    async def fake_session():
        yield session

    perf_values = iter([100.0, 100.007])
    monkeypatch.setattr(routes, "get_session", fake_session)
    monkeypatch.setattr(routes.time, "perf_counter", lambda: next(perf_values))

    result = asyncio.run(routes._get_sql_result_cache({"cache_key": "cache_a"}))

    assert result["cache_hit"] is True
    assert result["execution_time_ms"] == 7
    assert result["total_count"] == 1
    assert cache_item.hit_count == 1
    assert session.committed is True


def test_execute_sql_skip_query_cache_ignores_cache(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    async def raise_get_cache(_cache_identity):
        raise AssertionError("skip_query_cache should not read cache")

    async def raise_save_cache(_cache_identity, _result, operator_id=None):
        raise AssertionError("skip_query_cache should not write cache")

    monkeypatch.setattr(routes, "_get_sql_result_cache", raise_get_cache)
    monkeypatch.setattr(routes, "_save_sql_result_cache", raise_save_cache)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a"],
        skip_query_cache=True,
    )))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["cache_hit"] is False
    assert data["data"]["columns"] == ["member_name", "sql_text"]


def test_execute_sql_query_cache_read_failure_falls_back_to_datasource(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    async def raise_get_cache(_cache_identity):
        raise RuntimeError("cache db down")

    monkeypatch.setattr(routes, "_get_sql_result_cache", raise_get_cache)
    monkeypatch.setattr(routes, "_save_sql_result_cache", lambda *_args, **_kwargs: asyncio.sleep(0))

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a"],
    )))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["cache_hit"] is False
    assert data["data"]["columns"] == ["member_name", "sql_text"]


def test_execute_sql_query_cache_write_failure_does_not_fail_query(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "QUERY_CACHE_ENABLED", True)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    async def fake_get_cache(_cache_identity):
        return None

    async def raise_save_cache(_cache_identity, _result, operator_id=None):
        raise RuntimeError("duplicate cache key")

    monkeypatch.setattr(routes, "_get_sql_result_cache", fake_get_cache)
    monkeypatch.setattr(routes, "_save_sql_result_cache", raise_save_cache)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_a"],
    )))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["cache_hit"] is False
    assert data["data"]["columns"] == ["member_name", "sql_text"]


def test_sql_result_cache_key_uses_datasource_member_and_effective_sql():
    target = {
        "id": "phy_a",
        "member_datasource_id": "phy_a",
        "query_executor_type": "direct",
        "source_type": "mysql",
    }
    same_sql_a = routes._build_sql_result_cache_identity(target, "select *   from orders LIMIT 10", datasource_id="ds_a")
    same_sql_b = routes._build_sql_result_cache_identity(
        {**target, "query_executor_type": "http", "source_type": "hive"},
        "select *   from orders LIMIT 10",
        datasource_id="ds_a",
    )
    whitespace_changed = routes._build_sql_result_cache_identity(target, "select * from orders LIMIT 10", datasource_id="ds_a")
    string_literal_changed = routes._build_sql_result_cache_identity(target, "select * from orders where name = 'A B' LIMIT 10", datasource_id="ds_a")
    string_literal_with_double_space = routes._build_sql_result_cache_identity(target, "select * from orders where name = 'A  B' LIMIT 10", datasource_id="ds_a")
    other_limit = routes._build_sql_result_cache_identity(target, "select * from orders LIMIT 100", datasource_id="ds_a")
    other_member = routes._build_sql_result_cache_identity(target, "select * from orders LIMIT 100", datasource_id="ds_b")

    assert same_sql_a["cache_key"] == same_sql_b["cache_key"]
    assert same_sql_a["cache_key"] != whitespace_changed["cache_key"]
    assert string_literal_changed["cache_key"] != string_literal_with_double_space["cache_key"]
    assert same_sql_a["cache_key"] != other_limit["cache_key"]
    assert same_sql_a["cache_key"] != other_member["cache_key"]


def test_execute_sql_direct_returns_error_when_all_members_fail(monkeypatch):
    from apps.chat_data_app import routes
    from apps.chat_data_app.schema import ExecuteSqlRequest

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_error", "org_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)

    resp = asyncio.run(execute_sql_direct(SimpleNamespace(), ExecuteSqlRequest(
        sql="select 1",
        datasource_id="ds_a",
        member_datasource_ids=["phy_error"],
    )))
    data = _payload(resp)
    assert data["status"] == "error"
    assert data["data"]["success"] is False
    assert data["data"]["error"] == "成员失败 执行失败"


def test_run_sql_query_task_marks_failed_when_all_members_fail(monkeypatch):
    from apps.chat_data_app import routes

    updates = []
    async def fake_build_result(_request, query_task_id=None, execution_map=None, **_kwargs):
        assert query_task_id == "task_001"
        assert execution_map == {}
        return {
            "success": False,
            "sql": "select 1",
            "columns": [],
            "data": [],
            "total_count": 0,
            "execution_time_ms": 9,
            "error": "成员失败 执行失败",
            "results": [
                {
                    "success": False,
                    "member_datasource_id": "phy_error",
                    "member_name": "成员失败",
                    "error": "成员失败 执行失败",
                }
            ],
        }

    async def fake_update_query_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)
    monkeypatch.setattr(routes, "_is_query_task_cancelled", lambda _task_id: asyncio.sleep(0, result=False))
    monkeypatch.setattr(routes, "_create_query_executions_for_task", lambda _task_id, _request, operator_name=None: asyncio.sleep(0, result={}))

    asyncio.run(routes._run_sql_query_task("task_001", {
        "request_payload": json.dumps({
            "sql": "select 1",
            "datasource_id": "ds_a",
            "member_datasource_ids": ["phy_error"],
        })
    }))

    assert updates[-1][0] == "task_001"
    assert updates[-1][1]["status"] == "failed"
    assert updates[-1][1]["error_message"] == "成员失败 执行失败"


def test_run_sql_query_task_marks_failed_on_unhandled_execute_error(monkeypatch):
    from apps.chat_data_app import routes

    updates = []

    async def fake_build_result(_request, query_task_id=None, execution_map=None, **_kwargs):
        raise RuntimeError("executor crashed")

    async def fake_update_query_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    monkeypatch.setattr(routes, "_are_all_sql_execution_targets_http", lambda _request: asyncio.sleep(0, result=False))
    monkeypatch.setattr(routes, "_build_execute_sql_result_data", fake_build_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)
    monkeypatch.setattr(routes, "_is_query_task_cancelled", lambda _task_id: asyncio.sleep(0, result=False))
    monkeypatch.setattr(routes, "_create_query_executions_for_task", lambda _task_id, _request, operator_name=None: asyncio.sleep(0, result={}))

    asyncio.run(routes._run_sql_query_task("task_001", {
        "request_payload": json.dumps({
            "sql": "select 1",
            "datasource_id": "ds_a",
            "member_datasource_ids": ["phy_a"],
        })
    }))

    assert updates[-1][0] == "task_001"
    assert updates[-1][1]["status"] == "failed"
    assert updates[-1][1]["error_message"] == "executor crashed"


def test_execute_panel_uses_selected_member_datasource(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "org_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "org_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "_create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result=None))

    resp = asyncio.run(dashboard_routes.execute_panel(
        "dash_a",
        "panel_a",
        SimpleNamespace(),
        PanelExecuteRequest(member_datasource_id="phy_b"),
    ))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["member_datasource_id"] == "phy_b"
    assert data["data"]["result"]["member_name"] == "成员B"


def test_execute_panel_returns_error_when_member_execution_fails(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_error", "org_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "_create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result=None))

    resp = asyncio.run(dashboard_routes.execute_panel(
        "dash_a",
        "panel_a",
        SimpleNamespace(),
        PanelExecuteRequest(member_datasource_id="phy_error"),
    ))
    data = _payload(resp)
    assert data["status"] == "error"
    assert data["msg"] == "成员失败 执行失败"


def test_create_panel_query_task_schedules_dashboard_panel_task(monkeypatch):
    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _FakeDashboardService())
    _mock_authenticated_user(monkeypatch)

    captured = {}

    async def fake_create_task_record(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "task_id": "panel_task_001",
            "task_type": "dashboard_batch",
            "status": "pending",
        }

    def fake_schedule(task_id: str):
        captured["task_id"] = task_id

    monkeypatch.setattr(routes, "_create_task_record", fake_create_task_record)
    monkeypatch.setattr(routes, "_schedule_query_task", fake_schedule)

    resp = asyncio.run(dashboard_routes.create_panel_query_task(
        "dash_a",
        "panel_a",
        SimpleNamespace(),
        PanelExecuteRequest(member_datasource_id="phy_b"),
    ))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "panel_task_001"
    assert captured["kwargs"]["task_type"] == "dashboard_batch"
    assert captured["kwargs"]["request_payload"]["panel_ids"] == ["panel_a"]
    assert captured["kwargs"]["request_payload"]["panel_member_datasource_ids"]["panel_a"] == "phy_b"
    assert captured["task_id"] == "panel_task_001"


def test_run_dashboard_panel_task_marks_failed_when_member_execution_fails(monkeypatch):
    from apps.chat_data_app import routes

    updates = []

    async def fake_load_panel(_panel_id: str, **_kwargs):
        return {
            "id": "panel_a",
            "name": "面板A",
            "chart_type": "table",
            "chart_config": {},
            "sql": "select 1",
            "datasource_id": "ds_a",
        }

    async def fake_build_panel_result(panel, member_datasource_id=None):
        assert panel["id"] == "panel_a"
        assert member_datasource_id == "phy_error"
        return {
            "panel_id": "panel_a",
            "name": "面板A",
            "chart_type": "table",
            "chart_config": {},
            "member_datasource_id": "phy_error",
            "member_name": "成员失败",
            "result": {
                "success": False,
                "error": "成员失败 执行失败",
                "member_datasource_id": "phy_error",
                "member_name": "成员失败",
            },
        }

    async def fake_update_query_task(task_id, **kwargs):
        updates.append((task_id, kwargs))
        return None

    monkeypatch.setattr(dashboard_routes, "_load_dashboard_panel_or_raise", fake_load_panel)
    monkeypatch.setattr(dashboard_routes, "_build_dashboard_panel_execution_result", fake_build_panel_result)
    monkeypatch.setattr(routes, "_update_query_task", fake_update_query_task)

    asyncio.run(dashboard_routes._run_dashboard_panel_task("panel_task_001", {
        "request_payload": json.dumps({
            "panel_id": "panel_a",
            "member_datasource_id": "phy_error",
        })
    }))

    assert updates[-1][0] == "panel_task_001"
    assert updates[-1][1]["status"] == "failed"
    assert updates[-1][1]["error_message"] == "成员失败 执行失败"


def test_execute_panels_batch_uses_panel_member_mapping(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "org_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "org_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "_create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(routes, "_is_query_task_cancelled", lambda _task_id: asyncio.sleep(0, result=False))

    response = asyncio.run(dashboard_routes.execute_panels_batch(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(
            panel_ids=["panel_a"],
            panel_member_datasource_ids={"panel_a": "phy_b"},
        ),
    ))
    chunks = asyncio.run(_collect_stream_chunks(response))
    payload = next(chunk for chunk in chunks if chunk.startswith("data: {"))
    result = json.loads(payload[len("data: "):].strip())
    assert result["panel_id"] == "panel_a"
    assert result["member_datasource_id"] == "phy_b"
    assert result["result"]["member_name"] == "成员B"


def test_execute_panels_batch_marks_failed_when_member_execution_fails(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_error", "org_name": "成员失败", "source_type": "mysql", "host": "127.0.0.3", "port": 3306, "database": "db_c"},
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    _mock_query_tracking(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "_create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(routes, "_is_query_task_cancelled", lambda _task_id: asyncio.sleep(0, result=False))

    response = asyncio.run(dashboard_routes.execute_panels_batch(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(
            panel_ids=["panel_a"],
            panel_member_datasource_ids={"panel_a": "phy_error"},
        ),
    ))
    chunks = asyncio.run(_collect_stream_chunks(response))
    payload = next(chunk for chunk in chunks if chunk.startswith("data: {"))
    result = json.loads(payload[len("data: "):].strip())
    assert result["panel_id"] == "panel_a"
    assert result["error"] == "成员失败 执行失败"


def test_execute_panels_batch_marks_cancelled_when_stream_is_closed(monkeypatch):
    from apps.chat_data_app import routes

    updates = _mock_query_tracking(monkeypatch)
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: _FakeDatasourceManager())
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _FakeDashboardService())
    monkeypatch.setattr(routes, "get_executor", lambda datasource: _FakeExecutor(datasource))
    _mock_authenticated_user(monkeypatch)
    monkeypatch.setattr(dashboard_routes, "_create_dashboard_panel_execution", lambda **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(routes, "_is_query_task_cancelled", lambda _task_id: asyncio.sleep(0, result=False))

    async def fake_build_panel_result(*_args, **_kwargs):
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr(dashboard_routes, "_build_dashboard_panel_execution_result", fake_build_panel_result)

    response = asyncio.run(dashboard_routes.execute_panels_batch(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(panel_ids=["panel_a"]),
    ))

    async def close_stream():
        iterator = response.body_iterator.__aiter__()
        task = asyncio.create_task(iterator.__anext__())
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(close_stream())

    assert updates[-1][0] == "task_001"
    assert updates[-1][1]["status"] == "cancelled"
    assert updates[-1][1]["error_message"] == "客户端取消看板执行"


def test_create_dashboard_query_task_schedules_dashboard_batch_task(monkeypatch):
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: _FakeDashboardService())
    _mock_authenticated_user(monkeypatch)

    captured = {}

    async def fake_create_task_record(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "task_id": "dash_task_001",
            "task_type": "dashboard_batch",
            "status": "pending",
        }

    def fake_schedule(task_id: str):
        captured["task_id"] = task_id

    monkeypatch.setattr(routes, "_create_task_record", fake_create_task_record)
    monkeypatch.setattr(routes, "_schedule_query_task", fake_schedule)

    resp = asyncio.run(dashboard_routes.create_dashboard_query_task(
        "dash_a",
        SimpleNamespace(),
        PanelBatchExecuteRequest(
            panel_ids=["panel_a"],
            panel_member_datasource_ids={"panel_a": "phy_b"},
        ),
    ))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["task_id"] == "dash_task_001"
    assert captured["kwargs"]["task_type"] == "dashboard_batch"
    assert captured["kwargs"]["request_payload"]["dashboard_id"] == "dash_a"
    assert captured["kwargs"]["request_payload"]["panel_member_datasource_ids"]["panel_a"] == "phy_b"
    assert captured["task_id"] == "dash_task_001"


def test_create_dashboard_from_spec_creates_dashboard_and_panels(monkeypatch):
    service = _FakeDashboardService()
    _mock_authenticated_user(monkeypatch, user_id="u_1", username="用户1")
    monkeypatch.setattr(dashboard_spec_service, "get_dashboard_service", lambda: service)
    monkeypatch.setattr(dashboard_spec_service, "get_dashboard_spec_semantic_config", lambda *_args, **_kwargs: asyncio.sleep(0, result={"id": "sem_1"}))
    monkeypatch.setattr(dashboard_spec_service, "execute_dashboard_panel_sql_preflight", lambda *_args, **_kwargs: asyncio.sleep(0, result=(True, "")))

    class FakeSqlGenerator:
        def __init__(self, _config):
            pass

        def generate_sql(self, **kwargs):
            metric = (kwargs.get("metrics") or ["cnt"])[0]
            return {"success": True, "sql": f"select 1 as `{metric}`"}

    monkeypatch.setattr(dashboard_spec_service, "SemanticSQLGenerator", FakeSqlGenerator)

    request = DashboardFromSpecRequest(
        datasource_id="ds_a",
        semantic_config_id="sem_1",
        conversation_id="conv_1",
        dashboard_spec={
            "dashboard_name": "风险分析看板",
            "risk_topic": "经营风险",
            "cards": [
                {
                    "title": "风险总览",
                    "chart_type": "kpi",
                    "metrics": ["风险事件数"],
                    "dimensions": [],
                    "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
                    "dsl": {"metrics": ["风险事件数"], "dimensions": [], "limit": 100},
                }
            ],
        },
    )

    resp = asyncio.run(dashboard_routes.create_dashboard_from_spec(request, SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["dashboard"]["id"] == "dash_created"
    assert data["data"]["panels"][0]["chart_type"] == "kpi"
    assert data["data"]["panels"][0]["layout"] == {"x": 0, "y": 0, "w": 3, "h": 2}
    assert "风险事件数" in data["data"]["panels"][0]["sql"]


def test_create_dashboard_from_spec_rejects_clarification_spec(monkeypatch):
    _mock_authenticated_user(monkeypatch, user_id="u_1", username="用户1")
    request = DashboardFromSpecRequest(
        datasource_id="ds_a",
        semantic_config_id="sem_1",
        conversation_id="conv_1",
        dashboard_spec={
            "need_clarify": True,
            "dashboard_name": "风险分析看板",
            "cards": [{"title": "风险总览", "metrics": ["风险事件数"]}],
        },
    )

    resp = asyncio.run(dashboard_routes.create_dashboard_from_spec(request, SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "error"
    assert "仍需补充确认" in data["msg"]


def test_update_panel_rejects_wrong_dashboard(monkeypatch):
    service = _FakeDashboardService()
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: service)

    resp = asyncio.run(dashboard_routes.update_panel(
        "dash_b",
        "panel_a",
        PanelUpdateRequest(name="不应更新"),
        SimpleNamespace(),
    ))
    data = _payload(resp)

    assert data["status"] == "error"
    assert service.panels["panel_a"]["name"] == "面板A"


def test_delete_panel_rejects_wrong_dashboard(monkeypatch):
    service = _FakeDashboardService()
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: service)

    resp = asyncio.run(dashboard_routes.delete_panel("dash_b", "panel_a", SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "error"
    assert "panel_a" in service.panels


def test_update_layout_only_updates_current_dashboard_panels(monkeypatch):
    service = _FakeDashboardService()
    monkeypatch.setattr(dashboard_routes, "get_dashboard_service", lambda: service)
    _mock_authenticated_user(monkeypatch)

    resp = asyncio.run(dashboard_routes.update_layout(
        "dash_a",
        LayoutUpdateRequest(layouts=[
            {"panel_id": "panel_a", "x": 1, "y": 2, "w": 3, "h": 4},
            {"panel_id": "panel_c", "x": 5, "y": 6, "w": 7, "h": 8},
        ]),
        SimpleNamespace(),
    ))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["updated"] == 1
    assert service.panels["panel_a"]["layout"] == {"x": 1, "y": 2, "w": 3, "h": 4}
    assert "layout" not in service.panels["panel_c"]


def test_build_execution_target_info_contains_selected_members(monkeypatch):
    from apps.chat_data_app import handlers

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "org_name": "投诉举报1", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "org_name": "投诉举报2", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]
    monkeypatch.setattr(handlers, "get_datasource_manager", lambda: manager)

    info = asyncio.run(_build_execution_target_info_async("ds_a", ["phy_a", "phy_b"]))

    assert "当前执行对象" in info
    assert "投诉举报1" in info
    assert "投诉举报2" in info


def test_quick_sql_endpoint_requires_conversation_id(monkeypatch):
    from apps.chat_data_app import routes

    async def _fake_get_all_tables(_datasource_id):
        return [{"table_name": "users", "description": "用户表", "columns": []}]

    monkeypatch.setattr(routes, "get_datasource_manager", lambda: _FakeDatasourceManager())
    monkeypatch.setattr(routes, "get_all_tables_async", _fake_get_all_tables)
    monkeypatch.setattr(routes, "get_tables_create_format", lambda _tables, _table_names: "CREATE TABLE users(id bigint);")

    response = asyncio.run(routes.quick_sql_endpoint(QuickSqlRequest(
        query="有多少个用户",
        datasource_id="ds_a",
        member_datasource_ids=["phy_b"],
    )))
    chunks = asyncio.run(_collect_stream_chunks(response))
    content = "".join(chunks)

    assert '"type": "error"' in content
    assert "缺少 SQL 辅助会话ID" in content


def test_quick_sql_endpoint_uses_sql_assist_conversation(monkeypatch):
    from apps.chat_data_app import routes

    class _FakeChunk:
        def __init__(self, content: str):
            self.content = content

    class _FakeGraph:
        async def astream(self, initial_state, config, stream_mode):
            captured["initial_state"] = initial_state
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            yield _FakeChunk("```sql\nSELECT 1\n```"), {"langgraph_node": "sql_assist"}

    captured = {}
    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["member_sources"] = [
        {"member_datasource_id": "phy_a", "org_name": "成员A", "source_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "db_a"},
        {"member_datasource_id": "phy_b", "org_name": "成员B", "source_type": "mysql", "host": "127.0.0.2", "port": 3306, "database": "db_b"},
    ]

    async def _fake_get_or_create_conversation(_db_path, agent_id, conversation_id, user_id):
        captured["agent_id"] = agent_id
        captured["conversation_id"] = conversation_id
        captured["user_id"] = user_id
        return conversation_id

    class _FakeCheckpointerContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

    async def _fake_get_all_tables(_datasource_id):
        return [{"table_name": "users", "description": "用户表", "columns": []}]

    async def _fake_build_execution_target_info(_datasource_id, _member_ids):
        return "## 当前执行对象\n1. 成员B"

    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "_build_execution_target_info_async", _fake_build_execution_target_info)
    monkeypatch.setattr(routes, "get_all_tables_async", _fake_get_all_tables)
    monkeypatch.setattr(routes, "get_tables_create_format", lambda _tables, _table_names: "CREATE TABLE users(id bigint);")
    monkeypatch.setattr(routes.conversation, "get_or_create_conversation", _fake_get_or_create_conversation)
    monkeypatch.setattr(routes.conversation, "get_agent_id", lambda *_args, **_kwargs: asyncio.sleep(0, result=routes.SQL_ASSIST_AGENT_ID))
    monkeypatch.setattr("apps.chat_data_app.agent.get_checkpointer", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(routes, "get_sql_assist_graph", lambda *_args, **_kwargs: _FakeGraph())

    response = asyncio.run(routes.quick_sql_endpoint(QuickSqlRequest(
        query="有多少个用户",
        datasource_id="ds_a",
        member_datasource_ids=["phy_b"],
        conversation_id="conv_sql_assist",
    )))
    chunks = asyncio.run(_collect_stream_chunks(response))
    content = "".join(chunks)

    assert '"type": "complete"' in content
    assert "SELECT 1" in content
    assert captured["agent_id"] == routes.SQL_ASSIST_AGENT_ID
    assert captured["conversation_id"] == "conv_sql_assist"
    assert captured["config"]["configurable"]["thread_id"] == "conv_sql_assist"
    assert captured["initial_state"]["execution_target_info"] == "## 当前执行对象\n1. 成员B"
    assert captured["initial_state"]["member_datasource_ids"] == ["phy_b"]


def test_update_datasource_meta_rejects_duplicate_name(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    _mock_authenticated_user(monkeypatch)

    resp = asyncio.run(update_datasource_meta("ds_a", DatasourceUpdateRequest(name="数据源B"), SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "error"
    assert "名称已存在" in data["msg"]


def test_update_mapped_modules_persists(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="mapper_1")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(update_mapped_modules("ds_b", {"mapped_modules": ["执法检查", "投诉举报"]}, SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert manager.datasources["ds_b"]["mapped_modules"] == ["执法检查", "投诉举报"]
    assert manager.datasources["ds_b"]["update_by"] == "mapper_1"


def test_get_datasource_table_groups(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(get_datasource_table_groups("ds_a"))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["groups"] == [{"table_group_name": "核心表", "table_names": ["t_a"]}]


def test_update_datasource_table_groups_persists(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="group_maintainer")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    body = DatasourceTableGroupsUpdateRequest(groups=[
        {"table_group_name": "交易域", "table_names": ["t_a", "t_b"]},
        {"table_group_name": "复用域", "table_names": ["t_a"]},
    ])
    resp = asyncio.run(update_datasource_table_groups("ds_a", body, SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert manager.last_table_groups_operator == "group_maintainer"
    assert manager.table_groups["ds_a"] == [
        {"table_group_name": "交易域", "table_names": ["t_a", "t_b"]},
        {"table_group_name": "复用域", "table_names": ["t_a"]},
    ]


def test_update_datasource_table_business_desc_persists(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="table_maintainer", username="表维护人")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    body = DatasourceTableBusinessDescUpdateRequest(business_desc="统计数据源 A 时优先查看该表")
    resp = asyncio.run(update_datasource_table_business_desc("ds_a", "t_a", body, SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["table"]["business_desc"] == "统计数据源 A 时优先查看该表"
    assert manager.last_table_business_desc_operator == "table_maintainer"
    assert manager.datasources["ds_a"]["ds_tables"][0]["business_desc"] == "统计数据源 A 时优先查看该表"


def test_datasource_column_dict_get_and_save(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="dict_maintainer", username="字典维护人")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    get_resp = asyncio.run(get_datasource_column_dict("ds_a", "t_a", "status", SimpleNamespace()))
    get_data = _payload(get_resp)
    assert get_data["status"] == "success"
    assert get_data["data"]["dict"]["enum_values"] == [{"value": "1", "label": "有效"}]

    body = DatasourceColumnDictUpdateRequest(column_dict={
        "enum_values": [
            {"value": "1", "label": "有效"},
            {"value": "0", "label": "无效"},
        ],
        "synonyms": ["状态"],
        "storage_format": "",
        "unit": "",
    })
    save_resp = asyncio.run(update_datasource_column_dict("ds_a", "t_a", "status", body, SimpleNamespace()))
    save_data = _payload(save_resp)
    assert save_data["status"] == "success"
    assert save_data["data"]["dict"]["enum_values"] == [
        {"value": "1", "label": "有效"},
        {"value": "0", "label": "无效"},
    ]
    assert manager.last_column_dict_operator == "dict_maintainer"

    list_resp = asyncio.run(get_datasource_table_column_dicts("ds_a", "t_a", SimpleNamespace()))
    list_data = _payload(list_resp)
    assert list_data["status"] == "success"
    assert list_data["data"]["dicts"]["status"]["enum_values"] == [
        {"value": "1", "label": "有效"},
        {"value": "0", "label": "无效"},
    ]


def test_datasource_column_dict_not_found_branches(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="dict_maintainer", username="字典维护人")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    get_resp = asyncio.run(get_datasource_column_dict("ds_a", "t_a", "missing", SimpleNamespace()))
    save_resp = asyncio.run(update_datasource_column_dict("ds_a", "t_a", "missing", DatasourceColumnDictUpdateRequest(column_dict={}), SimpleNamespace()))
    list_resp = asyncio.run(get_datasource_table_column_dicts("ds_a", "missing", SimpleNamespace()))
    gen_resp = asyncio.run(generate_datasource_column_dict("ds_a", "t_a", "missing", DatasourceColumnDictGenerateRequest(text="无"), SimpleNamespace()))

    assert _payload(get_resp)["code"] == 40404
    assert _payload(save_resp)["code"] == 40404
    assert _payload(list_resp)["code"] == 40404
    assert _payload(gen_resp)["code"] == 40404


def test_parse_column_dict_generate_response_normalizes_invalid_items():
    parsed = _parse_column_dict_generate_response(json.dumps({
        "dict": {
            "enums": [
                "bad",
                {"enum_value": "", "enum_label": "空"},
                {"enum_value": "1", "enum_label": "有效"},
                {"value": "1", "label": "重复"},
                {"value": "0", "label": "无效"},
            ],
            "synonyms": [" 状态 ", "", "状态", "启停状态"],
            "storage_format": "yyyyMMdd",
            "unit": "万元",
        }
    }, ensure_ascii=False))

    assert parsed == {
        "enum_values": [{"value": "1", "label": "有效"}, {"value": "0", "label": "无效"}],
        "synonyms": [],
        "storage_format": "",
        "unit": "万元",
    }
    assert _parse_column_dict_generate_response('{"dict": "bad"}') == {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}
    assert _parse_column_dict_generate_response('{"dict": {"enum_values": "bad", "synonyms": "bad"}}') == {"enum_values": [], "synonyms": [], "storage_format": "", "unit": ""}


def test_generate_datasource_column_dict_uses_llm(monkeypatch):
    from apps.chat_data_app import routes

    class _FakeLlm:
        async def ainvoke(self, messages):
            assert "1 表示有效" in messages[0].content
            return SimpleNamespace(content='{"dict":{"enum_values":[{"value":"1","label":"有效"},{"value":"0","label":"无效"}],"synonyms":["订单状态"],"storage_format":"","unit":""}}')

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="dict_generator", username="字典生成人")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_chat_data_llm", lambda **_kwargs: _FakeLlm())

    body = DatasourceColumnDictGenerateRequest(text="status 字段：1 表示有效，0 表示无效")
    resp = asyncio.run(generate_datasource_column_dict("ds_a", "t_a", "status", body, SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["dict"]["enum_values"] == [
        {"value": "1", "label": "有效"},
        {"value": "0", "label": "无效"},
    ]
    assert data["data"]["dict"]["synonyms"] == []


def test_generate_datasource_column_dict_accepts_non_message_response(monkeypatch):
    from apps.chat_data_app import routes

    class _FakeLlm:
        async def ainvoke(self, messages):
            assert "status" in messages[0].content
            return '{"dict":{"enums":[{"enum_value":"1","enum_label":"有效"}],"synonyms":"bad","storage_format":"tinyint","unit":""}}'

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="dict_generator", username="字典生成人")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "get_chat_data_llm", lambda **_kwargs: _FakeLlm())

    resp = asyncio.run(generate_datasource_column_dict("ds_a", "t_a", "status", DatasourceColumnDictGenerateRequest(text="1 有效"), SimpleNamespace()))
    data = _payload(resp)

    assert data["status"] == "success"
    assert data["data"]["dict"] == {
        "enum_values": [{"value": "1", "label": "有效"}],
        "synonyms": [],
        "storage_format": "",
        "unit": "",
    }


def test_create_datasource_group_from_template_source(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="creator_1", username="创建人1")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "MySQLSyncService", _FakeMySQLSyncService)
    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _: {"user": "tester", "password": "secret"})

    payload = DatasourceGroupCreateRequest(
        group_name="模板组",
        group_desc="描述",
        source_type="mysql",
        template_source={
            "org_name": "template_a",
            "org_code": "ORG_TEMPLATE",
            "query_executor_type": "direct",
            "host": "127.0.0.1",
            "port": 3306,
            "database": "db_template",
        },
        member_sources=[
            {
                "org_name": "member_a",
                "org_code": "ORG_MEMBER_A",
                "query_executor_type": "direct",
                "host": "127.0.0.2",
                "port": 3306,
                "database": "db_member",
            },
        ],
    )
    resp = asyncio.run(create_datasource_group(payload, SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["source_config"]["database"] == "db_template"
    assert data["data"]["member_datasource_ids"] == ["phy_template", "phy_member_a"]
    assert data["data"]["create_by"] == "creator_1"
    assert data["data"]["update_by"] == "creator_1"


def test_update_datasource_group_config_updates_group_members(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="updater_1", username="维护人1")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setattr(routes, "MySQLSyncService", _FakeMySQLSyncService)
    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _: {"user": "tester", "password": "secret"})

    payload = DatasourceGroupUpdateRequest(
        group_name="更新后的组",
        group_desc="新描述",
        source_type="mysql",
        template_source={
            "org_name": "template_new",
            "org_code": "ORG_TEMPLATE_NEW",
            "query_executor_type": "direct",
            "host": "127.0.0.9",
            "port": 3306,
            "database": "db_template_new",
        },
        template_table_names=["orders"],
        member_sources=[
            {
                "org_name": "member_new",
                "org_code": "ORG_MEMBER_NEW",
                "query_executor_type": "direct",
                "host": "127.0.0.8",
                "port": 3306,
                "database": "db_member_new",
            },
        ],
    )
    resp = asyncio.run(update_datasource_group_config("ds_a", payload, SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert data["data"]["group_name"] == "更新后的组"
    assert data["data"]["source_config"]["database"] == "db_template_new"
    assert len(data["data"]["member_sources"]) == 2
    assert data["data"]["update_by"] == "updater_1"


def test_create_datasource_group_validates_blank_and_duplicate_names(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="creator_1")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    blank_payload = DatasourceGroupCreateRequest(
        group_name=" ",
        source_type="mysql",
        template_source={
            "org_name": "template_a",
            "org_code": "ORG_TEMPLATE",
            "query_executor_type": "direct",
            "host": "127.0.0.1",
            "database": "db_template",
        },
    )
    blank_resp = asyncio.run(create_datasource_group(blank_payload, SimpleNamespace()))
    assert _payload(blank_resp)["code"] == 40001

    duplicate_payload = DatasourceGroupCreateRequest(
        group_name="数据源A",
        source_type="mysql",
        template_source={
            "org_name": "template_a",
            "org_code": "ORG_TEMPLATE",
            "query_executor_type": "direct",
            "host": "127.0.0.1",
            "database": "db_template",
        },
    )
    duplicate_resp = asyncio.run(create_datasource_group(duplicate_payload, SimpleNamespace()))
    duplicate_body = _payload(duplicate_resp)
    assert duplicate_body["status"] == "error"
    assert "名称已存在" in duplicate_body["msg"]


def test_update_datasource_group_config_errors_for_missing_duplicate_and_invalid_source(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="updater_1")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    valid_source = {
        "org_name": "template_new",
        "org_code": "ORG_TEMPLATE_NEW",
        "query_executor_type": "direct",
        "host": "127.0.0.9",
        "database": "db_template_new",
    }
    missing_resp = asyncio.run(update_datasource_group_config("missing_ds", DatasourceGroupUpdateRequest(
        group_name="新组",
        source_type="mysql",
        template_source=valid_source,
    ), SimpleNamespace()))
    assert _payload(missing_resp)["code"] == 40404

    duplicate_resp = asyncio.run(update_datasource_group_config("ds_a", DatasourceGroupUpdateRequest(
        group_name="数据源B",
        source_type="mysql",
        template_source=valid_source,
    ), SimpleNamespace()))
    assert _payload(duplicate_resp)["code"] == 40001

    blank_name_resp = asyncio.run(update_datasource_group_config("ds_a", DatasourceGroupUpdateRequest(
        group_name=" ",
        source_type="mysql",
        template_source=valid_source,
    ), SimpleNamespace()))
    blank_name_body = _payload(blank_name_resp)
    assert blank_name_body["code"] == 40001
    assert "名称不能为空" in blank_name_body["msg"]


def test_datasource_table_group_and_default_error_branches(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch)
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    missing_groups_resp = asyncio.run(get_datasource_table_groups("missing_ds"))
    assert _payload(missing_groups_resp)["code"] == 40404

    update_missing_resp = asyncio.run(update_datasource_table_groups("missing_ds", DatasourceTableGroupsUpdateRequest(groups=[]), SimpleNamespace()))
    assert _payload(update_missing_resp)["code"] == 40404

    set_missing_resp = asyncio.run(set_default_datasource("missing_ds", SimpleNamespace()))
    assert _payload(set_missing_resp)["code"] == 40404

    clear_non_default_resp = asyncio.run(clear_default_datasource("ds_b", SimpleNamespace()))
    assert _payload(clear_non_default_resp)["code"] == 40404


def test_get_schema_uses_env_default_and_handles_errors(monkeypatch):
    manager = _FakeDatasourceManager()
    calls = []

    async def fake_get_schema_async(datasource_id, source="cache", table_names=None, **kwargs):
        calls.append({"datasource_id": datasource_id, "source": source, "table_names": table_names, "kwargs": kwargs})
        return [{"table_name": "orders", "columns": [{"name": "id"}]}]

    manager.get_schema_async = fake_get_schema_async
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    monkeypatch.setenv("CHAT_DATA_SCHEMA_SOURCE", "live")
    _mock_authenticated_user(monkeypatch, user_id="user_a", username="测试用户")

    response = asyncio.run(routes.get_schema("ds_a", tables=" orders, ,users ", request=SimpleNamespace()))
    body = _payload(response)

    assert body["status"] == "success"
    assert body["data"]["source"] == "live"
    assert body["data"]["count"] == 1
    assert calls[0]["table_names"] == ["orders", "users"]
    assert calls[0]["kwargs"] == {"user_id": "user_a", "username": "测试用户"}

    async def raise_value(*_args, **_kwargs):
        raise ValueError("schema invalid")

    manager.get_schema_async = raise_value
    assert _payload(asyncio.run(routes.get_schema("ds_a")))["code"] == 40001

    async def raise_connection(*_args, **_kwargs):
        raise ConnectionError("connect failed")

    manager.get_schema_async = raise_connection
    connection_body = _payload(asyncio.run(routes.get_schema("ds_a")))
    assert connection_body["code"] == 50008
    assert connection_body["msg"] == "connect failed"

    async def raise_runtime(*_args, **_kwargs):
        raise RuntimeError("boom")

    manager.get_schema_async = raise_runtime
    runtime_body = _payload(asyncio.run(routes.get_schema("ds_a")))
    assert runtime_body["code"] == 50008
    assert "boom" in runtime_body["msg"]


def test_get_db_tables_supports_mysql_hive_http_direct_and_error_branches(monkeypatch):
    mysql_calls = []
    hive_direct_calls = []
    hive_http_calls = []

    class RecordingMySQLSyncService(_FakeMySQLSyncService):
        def __init__(self, **kwargs):
            mysql_calls.append(kwargs)
            super().__init__(**kwargs)

    class RecordingHiveSyncService(_FakeHiveSyncService):
        def __init__(self, **kwargs):
            hive_direct_calls.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(routes, "MySQLSyncService", RecordingMySQLSyncService)
    monkeypatch.setattr(routes, "HiveSyncService", RecordingHiveSyncService)
    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _cfg: {"user": "mysql_user", "password": "mysql_pwd"})
    monkeypatch.setattr(routes, "resolve_hive_credentials", lambda _cfg: {"username": "hive_user", "password": "hive_pwd"})

    mysql_response = asyncio.run(routes.get_db_tables(DbConnectionRequest(
        source_type="mysql",
        host="127.0.0.1",
        database="demo",
    )))
    mysql_body = _payload(mysql_response)
    assert mysql_body["status"] == "success"
    assert mysql_body["data"]["total"] == 1
    assert mysql_calls[0]["port"] == 3306
    assert mysql_calls[0]["user"] == "mysql_user"

    async def fake_get_hive_meta_tables_async(source_config, **kwargs):
        hive_http_calls.append({"source_config": source_config, "kwargs": kwargs})
        return ["http_orders"]

    monkeypatch.setattr(routes, "get_hive_meta_tables_async", fake_get_hive_meta_tables_async)
    _mock_authenticated_user(monkeypatch, user_id="user_a", username="测试用户")
    hive_http_response = asyncio.run(routes.get_db_tables(DbConnectionRequest(
        source_type="hive",
        query_executor_type="http",
        host="127.0.0.1",
        database="default",
        org_code="ORG_A",
        org_name="机构A",
    ), SimpleNamespace()))
    hive_http_body = _payload(hive_http_response)
    assert hive_http_body["status"] == "success"
    assert hive_http_body["data"]["tables"][0] == "http_orders"
    assert hive_http_calls[-1]["source_config"]["port"] == 10000
    assert hive_http_calls[-1]["source_config"]["org_code"] == "ORG_A"
    assert hive_http_calls[-1]["source_config"]["org_name"] == "机构A"
    assert hive_http_calls[-1]["source_config"]["query_executor_type"] == "http"
    assert "namespace" not in hive_http_calls[-1]["source_config"]
    assert hive_http_calls[-1]["kwargs"] == {"user_id": "user_a", "username": "测试用户"}

    hive_direct_response = asyncio.run(routes.get_db_tables(DbConnectionRequest(
        source_type="hive",
        query_executor_type="direct",
        host="127.0.0.1",
        port=10001,
        database="default",
    )))
    hive_direct_body = _payload(hive_direct_response)
    assert hive_direct_body["status"] == "success"
    assert hive_direct_body["data"]["tables"][0]["table_name"] == "hive_orders"
    assert hive_direct_calls[-1]["port"] == 10001
    assert hive_direct_calls[-1]["username"] == "hive_user"

    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _cfg: (_ for _ in ()).throw(ValueError("缺少账号")))
    value_error_body = _payload(asyncio.run(routes.get_db_tables(DbConnectionRequest(
        source_type="mysql",
        host="127.0.0.1",
        database="demo",
    ))))
    assert value_error_body["code"] == 40001
    assert value_error_body["msg"] == "缺少账号"

    class TimeoutMySQLSyncService(_FakeMySQLSyncService):
        async def get_tables_async(self):
            raise asyncio.TimeoutError()

    monkeypatch.setattr(routes, "resolve_mysql_credentials", lambda _cfg: {"user": "u", "password": "p"})
    monkeypatch.setattr(routes, "MySQLSyncService", TimeoutMySQLSyncService)
    timeout_body = _payload(asyncio.run(routes.get_db_tables(DbConnectionRequest(
        source_type="mysql",
        host="127.0.0.1",
        database="demo",
    ))))
    assert timeout_body["code"] == 50014
    assert "连接超时" in timeout_body["msg"]

    class FailingMySQLSyncService(_FakeMySQLSyncService):
        async def get_tables_async(self):
            raise RuntimeError("connect failed")

    monkeypatch.setattr(routes, "MySQLSyncService", FailingMySQLSyncService)
    failed_body = _payload(asyncio.run(routes.get_db_tables(DbConnectionRequest(
        source_type="mysql",
        host="127.0.0.1",
        database="demo",
    ))))
    assert failed_body["code"] == 50014
    assert "连接失败" in failed_body["msg"]


def test_datasource_schema_and_semantic_health_reports_specific_issues(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["ds_a"]["ds_tables"] = [
        {
            "table_name": "orders",
            "columns": [
                {"name": "order_id", "type": "bigint"},
                {"name": "amount", "type": "decimal"},
                {"name": "removed_col", "type": "string"},
            ],
        },
        {"table_name": "users", "columns": [{"name": "user_id", "type": "bigint"}]},
        {"table_name": "deleted_table", "columns": [{"name": "id", "type": "bigint"}]},
    ]
    manager.datasources["ds_a"]["semantic_configs"] = [
        {
            "id": "sc_a",
            "name": "订单指标",
            "dimensions": [
                {"name": "订单ID", "table": "orders", "column": "order_id"},
                {"name": "缺失维度", "table": "orders", "column": "missing_col"},
                {"name": "缺失表维度", "table": "missing_table", "column": "id"},
                {"name": "表达式维度", "table": "orders", "column": "order_id", "expression": "concat(order_id, missing_col)"},
            ],
            "metrics": [
                {"name": "订单数", "table": "orders", "expression": "COUNT(*)"},
                {"name": "销售额", "table": "orders", "expression": "SUM(amount)"},
                {"name": "缺失指标", "table": "orders", "expression": "SUM(missing_amount)"},
                {"name": "未知指标", "table": "orders", "expression": "1 + 2"},
            ],
            "relationships": [
                {"left_table": "orders", "right_table": "users", "left_column": "user_id", "right_column": "id"},
                {"left_table": "missing_table", "right_table": "orders", "left_column": "id", "right_column": "order_id"},
            ],
        }
    ]
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    async def fake_load_tables(source_type, source_item, table_names=None):
        return [
            {
                "table_name": "orders",
                "columns": [
                    {"name": "order_id", "type": "bigint"},
                    {"name": "amount", "type": "double"},
                ],
            }
        ]

    monkeypatch.setattr(routes, "_load_tables_from_source", fake_load_tables)

    resp = asyncio.run(routes.get_datasource_semantic_health("ds_a"))
    body = _payload(resp)
    issue_types = {item["type"] for item in body["data"]["issues"]}

    assert body["status"] == "success"
    assert body["data"]["status"] == "invalid"
    assert body["data"]["schema"]["status"] == "invalid"
    assert body["data"]["semantic"]["status"] == "invalid"
    assert "missing_column" in issue_types
    assert "expression_column_missing" in issue_types
    assert "missing_join_column" in issue_types
    assert body["data"]["schema"]["summary"]["error_count"] >= 1


def test_datasource_semantic_health_unsupported_and_load_failure(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    manager.datasources["unsupported"] = {
        "id": "unsupported",
        "group_name": "不支持",
        "source_type": "oracle",
        "ds_tables": [{"table_name": "t1", "columns": []}],
        "semantic_configs": [],
    }
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)
    unsupported_resp = asyncio.run(routes.get_datasource_semantic_health("unsupported"))
    unsupported_body = _payload(unsupported_resp)
    assert unsupported_body["data"]["status"] == "warning"
    assert unsupported_body["data"]["schema"]["issues"][0]["type"] == "physical_schema_unsupported"

    async def raise_load_error(*_args, **_kwargs):
        raise RuntimeError("connect failed")

    monkeypatch.setattr(routes, "_load_tables_from_source", raise_load_error)
    failed_resp = asyncio.run(routes.get_datasource_semantic_health("ds_a"))
    failed_body = _payload(failed_resp)
    assert failed_body["data"]["status"] == "invalid"
    assert failed_body["data"]["schema"]["issues"][0]["type"] == "physical_schema_check_failed"


def test_set_and_clear_default_datasource(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="default_operator")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    set_resp = asyncio.run(set_default_datasource("ds_b", SimpleNamespace()))
    set_data = _payload(set_resp)
    assert set_data["status"] == "success"
    assert manager.datasources["ds_b"]["is_default"] is True
    assert manager.datasources["ds_a"]["is_default"] is False
    assert manager.datasources["ds_b"]["update_by"] == "default_operator"

    clear_resp = asyncio.run(clear_default_datasource("ds_b", SimpleNamespace()))
    clear_data = _payload(clear_resp)
    assert clear_data["status"] == "success"
    assert manager.datasources["ds_b"]["is_default"] is False
    assert manager.datasources["ds_b"]["update_by"] == "default_operator"


def test_delete_datasource_removes_only_current_group(monkeypatch):
    from apps.chat_data_app import routes

    manager = _FakeDatasourceManager()
    _mock_authenticated_user(monkeypatch, user_id="delete_operator")
    monkeypatch.setattr(routes, "get_datasource_manager", lambda: manager)

    resp = asyncio.run(delete_datasource("ds_b", SimpleNamespace()))
    data = _payload(resp)
    assert data["status"] == "success"
    assert manager.deleted_ids == ["ds_b"]
    assert manager.last_delete_update_by == "delete_operator"
    assert "ds_b" not in manager.datasources
