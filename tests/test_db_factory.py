# -*- coding: utf-8 -*-
"""Tests for DB adapter factory configuration."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_build_db_adapter_defaults_to_mock():
    from db_adapter import MockDBAdapter
    from db_factory import build_db_adapter

    adapter = build_db_adapter({})
    assert isinstance(adapter, MockDBAdapter)


def test_build_db_adapter_sqlite_from_config():
    from db_adapter import SQLiteReadonlyDBAdapter
    from db_factory import build_db_adapter

    adapter = build_db_adapter({
        "DATA_AGENT_DB_MODE": "sqlite",
        "DATA_AGENT_SQLITE_PATH": ":memory:",
        "DATA_AGENT_DB_ROW_LIMIT": "7",
        "DATA_AGENT_DB_TIMEOUT_MS": "321",
    })
    assert isinstance(adapter, SQLiteReadonlyDBAdapter)
    assert adapter.row_limit == 7
    assert adapter.timeout_ms == 321


def test_build_query_executor_uses_configured_adapter():
    from db_factory import build_query_executor

    executor = build_query_executor({"DATA_AGENT_DB_MODE": "mock"})
    result = executor.execute("SELECT 1")
    assert result["source"] == "mock"


if __name__ == "__main__":
    test_build_db_adapter_defaults_to_mock()
    test_build_db_adapter_sqlite_from_config()
    test_build_query_executor_uses_configured_adapter()
    print("All DB factory tests passed!")
