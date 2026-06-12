import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")

from apps.chat_data_app.services.semantic_tools import SemanticToolHandler, extract_datasource_id


def test_extract_datasource_id_from_state():
    assert extract_datasource_id({"datasource_id": "ds_001"}) == "ds_001"
    assert extract_datasource_id({"datasource_id": "  ds_002  "}) == "ds_002"
    assert extract_datasource_id({"datasource_id": ""}) is None
    assert extract_datasource_id({}) is None
    assert extract_datasource_id(None) is None


def test_validate_sql_mock_accepts_datasource_id():
    handler = SemanticToolHandler({"metrics": [], "dimensions": [], "relationships": []})

    result = handler.validate_sql("select 1", user_query="查一下", datasource_id="ds_001")

    assert result == "✓ SQL验证通过"
