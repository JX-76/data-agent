# -*- coding: utf-8 -*-
"""Tests for readonly DB adapters and schema introspection."""

import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (order_id INTEGER PRIMARY KEY, channel TEXT, gmv REAL)")
    conn.execute("INSERT INTO orders (order_id, channel, gmv) VALUES (1, 'taobao', 100.5)")
    conn.execute("INSERT INTO orders (order_id, channel, gmv) VALUES (2, 'jd', 80.0)")
    conn.commit()
    return conn


def test_sqlite_readonly_execute_and_preview():
    from db_adapter import ReadonlyQueryExecutor, SQLiteReadonlyDBAdapter

    conn = _make_conn()
    adapter = SQLiteReadonlyDBAdapter(connection=conn, row_limit=10)
    executor = ReadonlyQueryExecutor(db=adapter)

    result = executor.execute("SELECT channel, gmv FROM orders ORDER BY order_id")
    assert result["source"] == "sqlite"
    assert result["row_count"] == 2
    assert result["rows"][0]["channel"] == "taobao"

    preview = executor.fetch_preview("orders", limit=1)
    assert len(preview) == 1
    assert preview[0]["order_id"] == 1


def test_sqlite_rejects_non_readonly_sql():
    from db_adapter import SQLiteReadonlyDBAdapter

    adapter = SQLiteReadonlyDBAdapter(connection=_make_conn())
    try:
        adapter.execute("DELETE FROM orders")
        assert False, "expected readonly guard to reject DELETE"
    except ValueError as exc:
        assert "readonly" in str(exc)


def test_schema_introspection_and_semantic_table_index():
    from db_adapter import SQLiteReadonlyDBAdapter
    from schema_introspector import SchemaIntrospector, build_semantic_table_index

    adapter = SQLiteReadonlyDBAdapter(connection=_make_conn())
    introspector = SchemaIntrospector(adapter)
    schema = introspector.describe_schema()

    assert "orders" in schema
    assert schema["orders"][0]["name"] == "order_id"
    assert introspector.list_tables() == ["orders"]

    semantic_tables = build_semantic_table_index(adapter)
    assert semantic_tables["orders"]["name"] == "orders"
    assert semantic_tables["orders"]["columns"][0]["name"] == "order_id"


if __name__ == "__main__":
    test_sqlite_readonly_execute_and_preview()
    test_sqlite_rejects_non_readonly_sql()
    test_schema_introspection_and_semantic_table_index()
    print("All DB adapter tests passed!")
