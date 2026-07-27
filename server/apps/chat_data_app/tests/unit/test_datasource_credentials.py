import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import datasource_credentials as credentials


def test_sanitize_source_config_removes_sensitive_fields():
    assert credentials.sanitize_source_config({
        "host": "127.0.0.1",
        "user": "root",
        "username": "hive",
        "password": "secret",
        "database": "demo",
    }) == {"host": "127.0.0.1", "database": "demo"}
    assert credentials.sanitize_source_config(None) == {}


def test_mysql_credentials_require_user_and_decrypt_password(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_MYSQL_USER", raising=False)
    monkeypatch.delenv("CHAT_DATA_MYSQL_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="CHAT_DATA_MYSQL_USER"):
        credentials.resolve_mysql_credentials({})

    monkeypatch.setenv("CHAT_DATA_MYSQL_USER", "mysql_user")
    assert credentials.resolve_mysql_credentials({}) == {"user": "mysql_user", "password": ""}

    monkeypatch.setenv("CHAT_DATA_MYSQL_PASSWORD", "encrypted")
    monkeypatch.setattr(credentials, "decrypt_password", lambda value: f"plain:{value}")
    assert credentials.resolve_mysql_credentials({}) == {"user": "mysql_user", "password": "plain:encrypted"}


def test_hive_credentials_allow_anonymous_and_require_user_when_password_exists(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_HIVE_USER", raising=False)
    monkeypatch.delenv("CHAT_DATA_HIVE_PASSWORD", raising=False)

    assert credentials.resolve_hive_credentials({}) == {}

    monkeypatch.setenv("CHAT_DATA_HIVE_PASSWORD", "encrypted")
    monkeypatch.setattr(credentials, "decrypt_password", lambda value: f"plain:{value}")
    with pytest.raises(ValueError, match="CHAT_DATA_HIVE_USER"):
        credentials.resolve_hive_credentials({})

    monkeypatch.setenv("CHAT_DATA_HIVE_USER", "hive_user")
    assert credentials.resolve_hive_credentials({}) == {"username": "hive_user", "password": "plain:encrypted"}


def test_hive_connect_params_skip_empty_auth():
    assert credentials.build_hive_connect_params("localhost") == {
        "host": "localhost",
        "port": 10000,
        "database": "default",
    }
    assert credentials.build_hive_connect_params("localhost", 10001, "ods", username="u", password="p") == {
        "host": "localhost",
        "port": 10001,
        "database": "ods",
        "username": "u",
        "password": "p",
    }
