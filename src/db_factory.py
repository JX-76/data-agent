# -*- coding: utf-8 -*-
"""Database adapter factory driven by environment/config values."""

import os

from db_adapter import MockDBAdapter, ReadonlyQueryExecutor, SQLiteReadonlyDBAdapter


# Product/demo default must be runnable without a company database.
# Use DATA_AGENT_DB_MODE=mock/sqlite/postgres... explicitly in tests or prod when needed.
DEFAULT_DB_MODE = "sandbox"


def build_db_adapter(config=None):
    config = config or {}
    mode = (config.get("DATA_AGENT_DB_MODE") or os.environ.get("DATA_AGENT_DB_MODE") or DEFAULT_DB_MODE).strip().lower()
    row_limit = int(config.get("DATA_AGENT_DB_ROW_LIMIT") or os.environ.get("DATA_AGENT_DB_ROW_LIMIT") or 1000)
    timeout_ms = int(config.get("DATA_AGENT_DB_TIMEOUT_MS") or os.environ.get("DATA_AGENT_DB_TIMEOUT_MS") or 1500)

    if mode == "mock":
        return MockDBAdapter()
    if mode == "sqlite":
        db_path = config.get("DATA_AGENT_SQLITE_PATH") or os.environ.get("DATA_AGENT_SQLITE_PATH") or ":memory:"
        return SQLiteReadonlyDBAdapter(database_path=db_path, row_limit=row_limit, timeout_ms=timeout_ms)
    if mode == "sandbox":
        from sandbox_data_factory import build_sandbox_connection
        return SQLiteReadonlyDBAdapter(connection=build_sandbox_connection(), row_limit=row_limit, timeout_ms=timeout_ms)
    raise ValueError("unsupported DATA_AGENT_DB_MODE: %s" % mode)


def build_query_executor(config=None):
    return ReadonlyQueryExecutor(db=build_db_adapter(config=config))


__all__ = ["DEFAULT_DB_MODE", "build_db_adapter", "build_query_executor"]
