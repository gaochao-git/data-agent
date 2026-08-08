import pytest

from apps.chat_data_app.services.semantic_core.semantic_sql.join_compiler import build_join_clauses


def test_build_join_clauses_supports_alias_and_multiple_conditions():
    clauses = build_join_clauses(
        "orders",
        [{
            "rightTable": "users",
            "rightAlias": "u",
            "joinType": "LEFT",
            "joinConditions": [
                {"leftAlias": "o", "leftColumn": "user_id", "rightColumn": "id"},
                {"leftAlias": "o", "leftColumn": "tenant_id", "rightColumn": "tenant_id"},
            ],
        }],
        main_alias="o",
    )

    assert clauses == [
        "FROM orders o",
        "LEFT JOIN users u ON o.user_id = u.id AND o.tenant_id = u.tenant_id",
    ]


def test_build_join_clauses_rejects_join_without_condition():
    with pytest.raises(ValueError, match="缺少有效关联条件"):
        build_join_clauses("orders", [{"rightTable": "users"}])
