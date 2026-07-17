import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[4]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ["ENCRYPT_KEY"] = "00112233445566778899aabbccddeeff"
os.environ["SERVICE_AUTH_TOKEN"] = "qP+o6uU+z1wAYeHWi7ODmQ==:CIvqkqQXRoZf69isR8yD2PKhVNUQwM9ahic5sA1UGW0="
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "test")

import auth  # noqa: E402
from apps.chat_data_app import router as chat_data_router  # noqa: E402
from apps.chat_data_app import routes as chat_data_routes  # noqa: E402
from apps.chat_data_app.routers import datasource_admin as datasource_admin_routes  # noqa: E402
from apps.chat_data_app.routers import datasource_public as datasource_public_routes  # noqa: E402
from apps.chat_data_app.services import query_providers  # noqa: E402
from utils.crypto import decrypt_env_value  # noqa: E402
from utils.response import chatdata_http_exception_handler, chatdata_validation_exception_handler  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

SERVICE_AUTH_TOKEN_PLAIN = decrypt_env_value("SERVICE_AUTH_TOKEN")
auth.SERVICE_AUTH_TOKEN = SERVICE_AUTH_TOKEN_PLAIN


app = FastAPI(title="ChatData API Contract Test")
app.add_exception_handler(StarletteHTTPException, chatdata_http_exception_handler)
app.add_exception_handler(RequestValidationError, chatdata_validation_exception_handler)
app.include_router(chat_data_router)


@pytest.fixture()
def auth_headers():
    return {
        "X-Service-Token": SERVICE_AUTH_TOKEN_PLAIN,
        "X-User-Id": "user_a",
        "X-Username": "测试用户".encode("utf-8"),
    }


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def patch_datasource_manager(monkeypatch):
    def _patch(manager):
        monkeypatch.setattr(datasource_admin_routes, "get_datasource_manager", lambda: manager)
        monkeypatch.setattr(datasource_public_routes, "get_datasource_manager", lambda: manager)
        monkeypatch.setattr(query_providers, "get_datasource_manager", lambda: manager)

    return _patch


class FakeDatasourceManager:
    def __init__(self):
        self.created_semantic_kwargs = None
        self.updated_semantic_kwargs = None
        self.deleted_config_id = None
        self.replaced_table_groups = None
        self.replaced_table_groups_operator = None
        self.replaced_column_dict = None
        self.replaced_column_dict_operator = None
        self.default_datasource_id = None
        self.default_update_by = None
        self.deleted_datasource_id = None
        self.delete_update_by = None
        self.datasources = {
            "ds_a": {
                "id": "ds_a",
                "group_name": "数据源A",
                "description": "测试数据源",
                "source_type": "mysql",
                "source_config": {"database": "demo"},
                "mapped_modules": [],
                "is_default": False,
                "table_groups": [
                    {"table_group_name": "交易域", "table_names": ["orders"]},
                ],
                "ds_tables": [
                    {
                        "table_name": "orders",
                        "description": "订单表",
                        "columns": [
                            {"name": "id", "type": "int"},
                            {"name": "amount", "type": "decimal"},
                            {"name": "city", "type": "varchar"},
                            {"name": "status", "type": "tinyint", "description": "订单状态"},
                        ],
                    }
                ],
                "semantic_configs": [
                    {
                        "id": "cfg_a",
                        "name": "订单分析",
                        "description": "订单指标",
                        "enabled": True,
                        "scope_type": "private",
                        "create_by": "user_a",
                        "metrics": [
                            {"name": "销售额", "expression": "SUM(amount)", "table": "orders"}
                        ],
                        "dimensions": [
                            {"name": "城市", "column": "city", "table": "orders", "logical_type": "text"}
                        ],
                        "relationships": [],
                    },
                    {
                        "id": "cfg_public",
                        "name": "公共指标",
                        "enabled": True,
                        "scope_type": "public",
                        "create_by": "admin",
                        "metrics": [],
                        "dimensions": [],
                        "relationships": [],
                    },
                ],
            }
        }

    async def get_all_datasources_async(self, include_tables: bool = True):
        result = []
        for datasource in self.datasources.values():
            item = dict(datasource)
            item["ds_tables"] = list(datasource.get("ds_tables") or []) if include_tables else []
            result.append(item)
        return result

    async def get_authorized_datasources_async(self, include_tables: bool = True, user_id: str = "", username: str = ""):
        return await self.get_all_datasources_async(include_tables=include_tables)

    async def has_datasource_async(self, datasource_id: str):
        return datasource_id in self.datasources

    async def get_datasource_async(self, datasource_id: str):
        datasource = self.datasources.get(datasource_id)
        return dict(datasource) if datasource else None

    async def get_datasource_meta_async(self, datasource_id: str):
        datasource = self.datasources.get(datasource_id)
        return dict(datasource) if datasource else None

    async def get_datasource_by_group_name_async(self, name: str):
        for datasource in self.datasources.values():
            if datasource.get("group_name") == name:
                return dict(datasource)
        return None

    async def get_datasource_meta_by_group_name_async(self, name: str):
        return await self.get_datasource_by_group_name_async(name)

    async def get_schema_async(self, datasource_id: str, source: str = "cache", table_names=None):
        datasource = self.datasources[datasource_id]
        tables = datasource.get("ds_tables") or []
        if table_names:
            table_set = set(table_names)
            return [item for item in tables if item.get("table_name") in table_set]
        return list(tables)

    async def get_semantic_configs_async(self, datasource_id: str, user_id: str = ""):
        configs = list((self.datasources.get(datasource_id) or {}).get("semantic_configs") or [])
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return configs
        return [
            config for config in configs
            if config.get("scope_type", "public") == "public"
            or (
                config.get("scope_type") == "private"
                and str(config.get("create_by") or "").strip() == normalized_user_id
            )
        ]

    async def get_semantic_config_async(self, datasource_id: str, config_id: str, user_id: str = ""):
        for config in await self.get_semantic_configs_async(datasource_id, user_id):
            if config.get("id") == config_id:
                return dict(config)
        return None

    async def get_runtime_semantic_config_async(self, datasource_id: str, config_id: str, user_id: str = ""):
        return await self.get_semantic_config_async(datasource_id, config_id, user_id)

    async def create_semantic_config_async(self, **kwargs):
        self.created_semantic_kwargs = dict(kwargs)
        config = {
            "id": "cfg_new",
            "scope_type": kwargs.get("scope_type"),
            "create_by": kwargs.get("create_by"),
            **kwargs,
        }
        self.datasources[kwargs["datasource_id"]]["semantic_configs"].append(config)
        return config

    async def update_semantic_config_async(self, **kwargs):
        self.updated_semantic_kwargs = dict(kwargs)
        config = await self.get_semantic_config_async(kwargs["datasource_id"], kwargs["config_id"])
        if not config:
            return None
        config.update({key: value for key, value in kwargs.items() if key not in {"datasource_id", "config_id"}})
        config["id"] = kwargs["config_id"]
        return config

    async def delete_semantic_config_async(self, datasource_id: str, config_id: str, update_by: str = "", update_by_name: str = ""):
        self.deleted_config_id = config_id
        return bool(await self.get_semantic_config_async(datasource_id, config_id))

    async def update_datasource_async(self, datasource_id: str, **kwargs):
        datasource = self.datasources.get(datasource_id)
        if not datasource:
            return None
        if kwargs.get("name") is not None:
            datasource["group_name"] = kwargs["name"]
        if "description" in kwargs:
            datasource["description"] = kwargs["description"]
        if "mapped_modules" in kwargs:
            datasource["mapped_modules"] = kwargs["mapped_modules"]
        if "tables" in kwargs:
            datasource["ds_tables"] = kwargs["tables"]
        if "source_config" in kwargs:
            datasource["source_config"] = kwargs["source_config"]
        return dict(datasource)

    async def get_table_groups_async(self, datasource_id: str):
        datasource = self.datasources.get(datasource_id) or {}
        return list(datasource.get("table_groups") or [])

    async def replace_table_groups_async(self, datasource_id: str, groups, operator: str = "", operator_name: str = ""):
        datasource = self.datasources.get(datasource_id)
        if not datasource:
            return False
        self.replaced_table_groups = list(groups)
        self.replaced_table_groups_operator = operator
        datasource["table_groups"] = list(groups)
        return True

    async def get_column_dict_async(self, datasource_id: str, table_name: str, column_name: str):
        datasource = self.datasources.get(datasource_id)
        if not datasource:
            return None
        for table in datasource.get("ds_tables") or []:
            if table.get("table_name") != table_name:
                continue
            if any(column.get("name") == column_name for column in table.get("columns") or []):
                return {"enum_values": [{"value": "1", "label": "有效"}], "synonyms": ["状态"], "storage_format": "", "unit": ""}
        return None

    async def replace_column_dict_async(self, datasource_id: str, table_name: str, column_name: str, column_dict, operator: str = "", operator_name: str = ""):
        current_dict = await self.get_column_dict_async(datasource_id, table_name, column_name)
        if current_dict is None:
            return None
        self.replaced_column_dict = dict(column_dict)
        self.replaced_column_dict_operator = operator
        return dict(column_dict)

    async def set_default_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        if datasource_id not in self.datasources:
            return False
        for datasource in self.datasources.values():
            datasource["is_default"] = False
        self.datasources[datasource_id]["is_default"] = True
        self.default_datasource_id = datasource_id
        self.default_update_by = update_by
        return True

    async def clear_default_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        datasource = self.datasources.get(datasource_id)
        if not datasource or not datasource.get("is_default"):
            return False
        datasource["is_default"] = False
        self.default_datasource_id = None
        self.default_update_by = update_by
        return True

    async def delete_datasource_async(self, datasource_id: str, update_by: str = "", update_by_name: str = ""):
        if datasource_id not in self.datasources:
            return False
        self.deleted_datasource_id = datasource_id
        self.delete_update_by = update_by
        return True


class FakeDashboardService:
    def __init__(self):
        self.created_dashboard = None
        self.added_panel = None
        self.updated_dashboard = None
        self.updated_panel = None
        self.updated_layouts = None
        self.deleted_dashboard_id = None
        self.deleted_panel_id = None
        self.public_state = None
        self.dashboard = {
            "dashboard_id": "dash_a",
            "name": "经营看板",
            "description": "核心指标",
            "user_id": "user_a",
            "scope": "mine",
            "is_public": False,
            "theme": "light",
            "panels": [
                {
                    "panel_id": "panel_a",
                    "dashboard_id": "dash_a",
                    "name": "城市销售额",
                    "sql": "select city, sum(amount) amount from orders group by city",
                    "chart_type": "bar",
                    "datasource_id": "ds_a",
                    "layout_x": 0,
                    "layout_y": 0,
                    "layout_w": 6,
                    "layout_h": 4,
                }
            ],
        }

    async def list_dashboards(self, user_id: str, scope: str = "mine"):
        return [
            {
                "dashboard_id": "dash_a",
                "name": "经营看板",
                "user_id": user_id,
                "scope": scope,
                "is_public": scope == "public",
            }
        ]

    async def create_dashboard(self, **kwargs):
        self.created_dashboard = dict(kwargs)
        return {"dashboard_id": "dash_new", **kwargs}

    async def get_dashboard(self, dashboard_id: str, user_id: str, include_public: bool = False):
        if dashboard_id != self.dashboard["dashboard_id"]:
            return None
        return dict(self.dashboard)

    async def update_dashboard(self, **kwargs):
        self.updated_dashboard = dict(kwargs)
        if kwargs.get("dashboard_id") != self.dashboard["dashboard_id"]:
            return None
        if kwargs.get("user_id") and kwargs.get("user_id") != self.dashboard.get("user_id"):
            return None
        self.dashboard.update({key: value for key, value in kwargs.items() if value is not None})
        return dict(self.dashboard)

    async def delete_dashboard(self, dashboard_id: str, user_id: str, operator_name: str = ""):
        if dashboard_id != self.dashboard["dashboard_id"] or user_id != self.dashboard.get("user_id"):
            return False
        self.deleted_dashboard_id = dashboard_id
        return True

    async def set_dashboard_public(self, dashboard_id: str, user_id: str, is_public: bool, operator_name: str = ""):
        if dashboard_id != self.dashboard["dashboard_id"] or user_id != self.dashboard.get("user_id"):
            return None
        self.public_state = is_public
        self.dashboard["is_public"] = is_public
        self.dashboard["scope"] = "public" if is_public else "mine"
        return dict(self.dashboard)

    async def add_panel(self, **kwargs):
        self.added_panel = dict(kwargs)
        return {"panel_id": "panel_new", **kwargs}

    async def update_panel(self, **kwargs):
        self.updated_panel = dict(kwargs)
        if kwargs.get("panel_id") != "panel_a":
            return None
        panel = dict(self.dashboard["panels"][0])
        panel.update({key: value for key, value in kwargs.items() if value is not None})
        return panel

    async def delete_panel(self, dashboard_id: str, panel_id: str, user_id: str, operator_name: str = ""):
        if dashboard_id != self.dashboard["dashboard_id"] or panel_id != "panel_a":
            return False
        self.deleted_panel_id = panel_id
        return True

    async def update_panels_layout(self, dashboard_id: str, layouts, user_id: str, operator_name: str = ""):
        if dashboard_id != self.dashboard["dashboard_id"]:
            return None
        self.updated_layouts = list(layouts)
        return len(layouts)


@pytest.fixture()
def fake_datasource_manager():
    return FakeDatasourceManager()


@pytest.fixture()
def fake_dashboard_service():
    return FakeDashboardService()


@pytest.fixture()
def sample_query_task():
    return {
        "task_id": "task_a",
        "task_type": "sql",
        "datasource_id": "ds_a",
        "query_type": "sql",
        "status": "pending",
        "progress_text": "任务已创建，等待执行",
        "request_payload": {
            "sql": "select * from orders limit 10",
            "datasource_id": "ds_a",
            "query_type": "sql",
            "user_id": "user_a",
        },
        "result_payload": None,
        "remote_tasks": [],
        "error_message": None,
        "started_time": None,
        "finished_time": None,
        "create_time": "2026-06-01 00:00:00",
        "update_time": "2026-06-01 00:00:00",
    }


@pytest.fixture()
def fake_user():
    return SimpleNamespace(user_id="user_a", username="测试用户")
