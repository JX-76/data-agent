"""Execution layer: mock data generation + CTE SQL execution.

Bridges generated CTE SQL to real query results via db.ConnectionPool.
Supports SQLite (in-memory default), PostgreSQL, MySQL, DuckDB.
All queries use parameterized execution for SQL injection protection.
"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DB_TYPE, DB_PATH
from db import ConnectionPool, Dialect, create_pool, get_pool, get_dialect
from schema_migration import migrate, check_schema


# ── Programmatic mock data (deterministic seed, no LLM needed) ──

def _generate_mock_data():
    """Generate realistic mock data for fct_orders, dim_store, dim_product."""
    random.seed(42)

    regions = ["华东", "华南", "华北", "西南", "华中"]
    channels = ["online", "offline", "live"]
    statuses = ["paid", "completed", "completed", "completed",
                "paid", "paid", "paid", "completed", "cancelled", "refunded"]

    stores = []
    store_counter = 1
    for r in regions:
        for i in range(1, 4):
            sid = f"S{store_counter:03d}"
            store_counter += 1
            stores.append({"store_id": sid, "store_name": f"{r}{i}号门店", "region": r})

    categories = {
        "女装": ("连衣裙", "T恤", "外套", "半身裙", "卫衣"),
        "男装": ("衬衫", "夹克", "西裤", "POLO衫", "牛仔裤"),
        "数码": ("蓝牙耳机", "充电宝", "手机壳", "数据线", "平板支架"),
        "家居": ("四件套", "枕头", "台灯", "收纳盒", "地毯"),
        "美妆": ("口红", "面膜", "精华液", "粉底液", "眼影盘"),
    }
    products = []
    pid = 1
    for cat, items in categories.items():
        for item_name in items:
            products.append({
                "product_id": f"P{pid:04d}",
                "product_name": f"{cat}-{item_name}",
                "category": cat,
                "unit_price": round(random.uniform(29.9, 699.0), 2),
            })
            pid += 1

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    base_date = today - timedelta(days=27)
    orders = []
    for i in range(120):
        days_ago = random.randint(0, 27)
        paid_dt = base_date + timedelta(
            days=days_ago, hours=random.randint(8, 22), minutes=random.randint(0, 59)
        )
        status = random.choice(statuses)
        sell = round(random.uniform(50, 800), 2)
        store = random.choice(stores)
        orders.append({
            "order_id": f"ORD{i+1:05d}",
            "store_id": store["store_id"],
            "product_id": random.choice(products)["product_id"],
            "sell_through": sell,
            "channel": random.choice(channels),
            "order_status": status,
            "paid_at": paid_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": f"USR{random.randint(1, 200):04d}",
        })

    return {"dim_store": stores, "dim_product": products, "fct_orders": orders}


# ── DB Executor ──

class DbExecutor:
    """Database executor with connection pooling and mock data support.

    For SQLite in-memory mode, creates tables and loads mock data on init.
    For other dialects, expects pre-existing schema (use schema migrations).
    """

    def __init__(self, pool: Optional[ConnectionPool] = None):
        self.pool = pool or get_pool()
        self.dialect = self.pool.dialect
        # Only auto-init mock data for SQLite in-memory (dev/test mode)
        self._is_sqlite_memory = (
            self.dialect.name == "sqlite" and DB_PATH == ":memory:"
        )

        if self._is_sqlite_memory:
            self._init_sqlite()

    def _init_sqlite(self):
        """Create tables and load mock data for SQLite in-memory mode."""
        create_ddl = """
        CREATE TABLE IF NOT EXISTS fct_orders (
            order_id    TEXT PRIMARY KEY,
            store_id    TEXT NOT NULL,
            product_id  TEXT NOT NULL,
            sell_through REAL NOT NULL,
            channel     TEXT NOT NULL,
            order_status TEXT NOT NULL,
            paid_at     TEXT NOT NULL,
            user_id     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dim_store (
            store_id   TEXT PRIMARY KEY,
            store_name TEXT NOT NULL,
            region     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dim_product (
            product_id   TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            category     TEXT NOT NULL,
            unit_price   REAL NOT NULL
        );
        """
        for stmt in create_ddl.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self.pool.execute(stmt)

        # Apply migrations
        try:
            migrate(self.pool)
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass

        # Load mock data
        data = _generate_mock_data()
        for s in data["dim_store"]:
            self.pool.execute(
                "INSERT INTO dim_store(store_id, store_name, region) VALUES (?,?,?)",
                (s["store_id"], s["store_name"], s["region"]),
            )
        for p in data["dim_product"]:
            self.pool.execute(
                "INSERT INTO dim_product(product_id, product_name, category, unit_price) VALUES (?,?,?,?)",
                (p["product_id"], p["product_name"], p["category"], p["unit_price"]),
            )
        for o in data["fct_orders"]:
            self.pool.execute(
                "INSERT INTO fct_orders(order_id, store_id, product_id, sell_through, channel, order_status, paid_at, user_id) VALUES (?,?,?,?,?,?,?,?)",
                (o["order_id"], o["store_id"], o["product_id"],
                 o["sell_through"], o["channel"], o["order_status"],
                 o["paid_at"], o["user_id"]),
            )

        import structlog
        logger = structlog.get_logger("db-executor")
        logger.info("sqlite_memory_initialized",
                    fct_rows=self.pool.execute("SELECT COUNT(*) AS c FROM fct_orders")[0].get("c", 0),
                    dim_rows=self.pool.execute("SELECT COUNT(*) AS c FROM dim_store")[0].get("c", 0),
                    migration_status=check_schema(self.pool) if self._is_sqlite_memory else {})

    def execute_cte(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a CTE SQL query with parameterized bindings.

        Args:
            sql: The CTE SQL string (use ? placeholders for params)
            params: Tuple of parameter values (positional binding)

        Returns:
            List of result rows as dicts, or [{"error": "message"}] on failure
        """
        import sqlite3
        try:
            return self.pool.execute(sql, params)
        except (sqlite3.Error, Exception) as e:
            return [{"error": str(e)}]

    def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a non-CTE parameterized query."""
        return self.execute_cte(sql, params)

    def row_count(self, table: str) -> int:
        # table name from semantic layer → safe, but validate anyway
        safe_table = self._sanitize_identifier(table)
        rows = self.pool.execute(f"SELECT COUNT(*) AS c FROM {safe_table}")
        return rows[0].get("c", 0) if rows else 0
    
    def _sanitize_identifier(self, name: str) -> str:
        """Sanitize SQL identifier to prevent injection."""
        import re
        # Only allow alphanumeric and underscore
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '', name)
        return sanitized

    def table_names(self) -> list[str]:
        if self.dialect.name in ("sqlite", "duckdb"):
            rows = self.pool.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [r.get("name", "") for r in rows if not r.get("name", "").startswith("_")]
        elif self.dialect.name == "postgresql":
            rows = self.pool.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'"
            )
            return [r.get("tablename", "") for r in rows]
        elif self.dialect.name == "mysql":
            rows = self.pool.execute("SHOW TABLES")
            return [list(r.values())[0] for r in rows]
        return []

    def pool_stats(self) -> dict:
        return self.pool.stats()

    def health_check(self) -> bool:
        return self.pool.health_check()


# ── Global singleton ──

_DB: DbExecutor | None = None


def get_db() -> DbExecutor:
    global _DB
    if _DB is None:
        _DB = DbExecutor()
    return _DB


def reset_db():
    """Reset the global DB singleton (useful for tests)."""
    global _DB
    if _DB is not None:
        try:
            _DB.pool.close()
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass
        _DB = None

    # Also reset the global pool
    from db import _pool as _global_pool
    import db as db_module
    if db_module._pool is not None:
        try:
            db_module._pool.close()
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            pass
        db_module._pool = None
