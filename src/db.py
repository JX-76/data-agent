"""Database abstraction layer: connection pool, dialect adapter, parameterized queries.

Supports: SQLite (in-memory/file), PostgreSQL, MySQL, DuckDB.

Usage:
    from db import get_pool, get_dialect
    pool = get_pool()
    with pool.connection() as conn:
        rows = conn.execute("SELECT * FROM fct_orders WHERE channel = ?", ("online",))
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Generator, Any

# ── SQL Dialect ──

@dataclass
class Dialect:
    """SQL dialect adapter. Maps common patterns to DB-specific SQL."""

    name: str  # sqlite | postgresql | mysql | duckdb

    # Placeholder style
    placeholder: str = "?"  # ? for sqlite/duckdb, %s for postgresql/mysql

    # Identifier quoting
    quote_char: str = '"'

    # Functions
    date_trunc_day: str = "DATE({col})"  # SQLite
    date_format: str = "strftime('{fmt}', {col})"
    random_sample: str = "RANDOM()"
    limit_clause: str = "LIMIT {n}"
    ilike: str = "{col} LIKE {val}"  # SQLite doesn't have ILIKE

    # DDL features
    supports_json_col: bool = False
    supports_cte: bool = True

    def apply(self, sql: str) -> str:
        """Apply dialect-specific transformations."""
        return sql

    @staticmethod
    def for_name(name: str) -> Dialect:
        return DIALECT_MAP.get(name.lower(), DIALECT_SQLITE)

    def quote(self, identifier: str) -> str:
        """Quote an identifier (table/column name)."""
        q = self.quote_char
        return f"{q}{identifier}{q}"


DIALECT_SQLITE = Dialect(name="sqlite", placeholder="?", quote_char='"',
                         date_trunc_day="DATE({col})", random_sample="RANDOM()")

DIALECT_POSTGRES = Dialect(name="postgresql", placeholder="%s", quote_char='"',
                           date_trunc_day="DATE_TRUNC('day', {col})",
                           date_format="TO_CHAR({col}, '{fmt}')",
                           random_sample="RANDOM()", ilike="{col} ILIKE {val}",
                           supports_json_col=True)

DIALECT_MYSQL = Dialect(name="mysql", placeholder="%s", quote_char='`',
                        date_trunc_day="DATE({col})",
                        date_format="DATE_FORMAT({col}, '{fmt}')",
                        random_sample="RAND()", limit_clause="LIMIT {n}",
                        supports_json_col=True)

DIALECT_DUCKDB = Dialect(name="duckdb", placeholder="?", quote_char='"',
                         date_trunc_day="DATE_TRUNC('day', {col})",
                         date_format="strftime({col}, '{fmt}')",
                         random_sample="RANDOM()", supports_json_col=True,
                         supports_cte=True)

DIALECT_MAP = {
    "sqlite": DIALECT_SQLITE,
    "postgresql": DIALECT_POSTGRES,
    "postgres": DIALECT_POSTGRES,
    "mysql": DIALECT_MYSQL,
    "duckdb": DIALECT_DUCKDB,
}


# ── Connection Pool ──

@dataclass
class PoolConfig:
    min_connections: int = 1
    max_connections: int = 5
    connection_timeout: float = 5.0  # seconds
    idle_timeout: float = 300.0      # seconds
    max_retries: int = 3
    retry_delay: float = 0.1


@dataclass
class PooledConnection:
    """A connection wrapper with usage tracking."""

    conn: Any
    created_at: float
    last_used_at: float
    in_use: bool = False
    broken: bool = False


class ConnectionPool:
    """Thread-safe connection pool with retry and health check."""

    def __init__(self, connect_fn, dialect: Dialect, config: PoolConfig | None = None):
        self._connect_fn = connect_fn
        self.dialect = dialect
        self.config = config or PoolConfig()
        self._pool: list[PooledConnection] = []
        self._lock = threading.Lock()
        self._total_created = 0
        self._total_acquired = 0
        self._total_failures = 0
        self._total_retries = 0

    def _create_connection(self) -> PooledConnection:
        """Create a new connection with retries."""
        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                conn = self._connect_fn()
                self._total_created += 1
                now = time.time()
                return PooledConnection(conn=conn, created_at=now, last_used_at=now)
            except Exception as e:
                last_error = e
                self._total_retries += 1
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))

        self._total_failures += 1
        raise ConnectionError(
            f"Failed to create connection after {self.config.max_retries} retries: {last_error}"
        )

    @contextmanager
    def connection(self) -> Generator:
        """Get a connection from the pool. Context manager ensures return."""
        entry = self._acquire()
        try:
            yield entry.conn
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            entry.broken = True
            raise
        finally:
            self._release(entry)

    def _acquire(self) -> PooledConnection:
        """Acquire a connection from the pool, blocking up to timeout."""
        deadline = time.time() + self.config.connection_timeout

        while time.time() < deadline:
            with self._lock:
                # 1. Find idle healthy connection
                for entry in self._pool:
                    if not entry.in_use and not entry.broken:
                        # Check idle timeout
                        if time.time() - entry.last_used_at > self.config.idle_timeout:
                            entry.broken = True
                            continue
                        entry.in_use = True
                        entry.last_used_at = time.time()
                        self._total_acquired += 1
                        return entry

                # 2. Create new if under max
                if self._total_in_use() < self.config.max_connections:
                    entry = self._create_connection()
                    entry.in_use = True
                    self._pool.append(entry)
                    self._total_acquired += 1
                    return entry

            # 3. Wait and retry
            time.sleep(0.05)

        raise TimeoutError(
            f"Connection pool exhausted ({self._total_in_use()}/{self.config.max_connections} in use)"
        )

    def _release(self, entry: PooledConnection):
        """Return connection to pool."""
        with self._lock:
            entry.in_use = False
            entry.last_used_at = time.time()
            if entry.broken:
                try:
                    entry.conn.close()
                except Exception as e:
                    logger.warning("bare_exception_caught", error=str(e))
                    pass
                self._pool.remove(entry)

    def _total_in_use(self) -> int:
        return sum(1 for e in self._pool if e.in_use)

    def stats(self) -> dict:
        with self._lock:
            total = len(self._pool)
            in_use = self._total_in_use()
            idle = total - in_use
            broken = sum(1 for e in self._pool if e.broken)
            return {
                "dialect": self.dialect.name,
                "total": total,
                "in_use": in_use,
                "idle": idle,
                "broken": broken,
                "max": self.config.max_connections,
                "total_created": self._total_created,
                "total_acquired": self._total_acquired,
                "total_failures": self._total_failures,
                "total_retries": self._total_retries,
            }

    def health_check(self) -> bool:
        """Ping all idle connections, mark broken ones."""
        with self._lock:
            for entry in self._pool:
                if not entry.in_use:
                    try:
                        entry.conn.execute("SELECT 1")
                    except Exception as e:
                        logger.warning("bare_exception_caught", error=str(e))
                        entry.broken = True
        return True

    def close(self):
        """Close all connections."""
        with self._lock:
            for entry in self._pool:
                try:
                    entry.conn.close()
                except Exception as e:
                    logger.warning("bare_exception_caught", error=str(e))
                    pass
            self._pool.clear()

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a parameterized query via pooled connection."""
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            # Convert to list[dict]
            cols = [desc[0] for desc in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in rows]

    def execute_cte(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a CTE SQL query (same as execute, separate API for clarity)."""
        return self.execute(sql, params)


# ── Factory Functions ──

def _sqlite_connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _pg_connect(host: str, port: int, dbname: str, user: str, password: str):
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn


def _mysql_connect(host: str, port: int, dbname: str, user: str, password: str):
    import mysql.connector
    return mysql.connector.connect(host=host, port=port, database=dbname, user=user, password=password)


def _duckdb_connect(path: str = ":memory:"):
    import duckdb
    return duckdb.connect(path)


def create_pool(config: dict | None = None) -> ConnectionPool:
    """Create a connection pool from configuration.

    config keys:
        type: sqlite | postgresql | mysql | duckdb
        path: file path (sqlite/duckdb)
        host, port, name, user, password (postgresql/mysql)
        pool_min, pool_max, pool_timeout
    """
    cfg = config or {}
    db_type = cfg.get("type", "sqlite").lower()
    dialect = Dialect.for_name(db_type)

    if db_type == "sqlite":
        path = cfg.get("path", ":memory:")
        connect_fn = lambda: _sqlite_connect(path)
    elif db_type == "postgresql":
        import psycopg2
        connect_fn = lambda: _pg_connect(
            cfg.get("host", "localhost"), cfg.get("port", 5432),
            cfg.get("name", ""), cfg.get("user", ""), cfg.get("password", "")
        )
    elif db_type == "mysql":
        connect_fn = lambda: _mysql_connect(
            cfg.get("host", "localhost"), cfg.get("port", 3306),
            cfg.get("name", ""), cfg.get("user", ""), cfg.get("password", "")
        )
    elif db_type == "duckdb":
        path = cfg.get("path", ":memory:")
        connect_fn = lambda: _duckdb_connect(path)
    else:
        raise ValueError(f"Unsupported database type: {db_type}")

    pool_config = PoolConfig(
        min_connections=cfg.get("pool_min", 1),
        max_connections=cfg.get("pool_max", 5),
        connection_timeout=cfg.get("pool_timeout", 5.0),
    )

    return ConnectionPool(connect_fn, dialect, pool_config)


# ── Global singleton ──

_pool: ConnectionPool | None = None


def get_pool(config: dict | None = None) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = create_pool(config)
    return _pool


def get_dialect() -> Dialect:
    return get_pool().dialect
