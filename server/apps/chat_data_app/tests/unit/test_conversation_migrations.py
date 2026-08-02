from types import SimpleNamespace

from apps.chat_data_app.migrations.backfill_conversation_datasource import _legacy_datasource_id
from utils import schema


def test_conversation_event_schema_is_in_authoritative_table_list():
    assert schema.MYSQL_CHAT_DATA_CONVERSATION_EVENT_TABLE in schema.MYSQL_TABLES
    assert "chat_data_conversation_event" in schema.MYSQL_CHAT_DATA_CONVERSATION_EVENT_TABLE
    assert "datasource_id" in schema.MYSQL_USER_THREADS_TABLE


def test_legacy_datasource_id_reads_only_old_checkpoint_channel():
    checkpoint = SimpleNamespace(checkpoint={
        "channel_values": {"conversation_datasource_id": " ds_a ", "messages": ["large"]},
    })

    assert _legacy_datasource_id(checkpoint) == "ds_a"
    assert _legacy_datasource_id(None) == ""
