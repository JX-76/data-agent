"""Schema version migration for semantic layer YAML → database synchronization.

Tracks a version number in the database (in a `_schema_version` table),
compares against the semantic layer YAML definitions, and applies
incremental migrations when schemas diverge.

Usage:
    from schema_migration import migrate
    migrate(pool)  # Apply any pending migrations
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger("schema-migration")


# ── Migration AST ──

@dataclass
class ColumnDef:
    name: str
    type: str
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False


@dataclass
class TableDef:
    name: str
    columns: list[ColumnDef] = field(default_factory=list)
    indexes: list[str] = field(default_factory=list)


@dataclass
class MigrationStep:
    """A single DDL migration step."""
    version: int
    description: str
    sql_up: str
    sql_down: str
    checksum: str = ""


# ── Schema Loader ──

def _load_semantic_tables() -> list[TableDef]:
    """Load table definitions from semantic YAML files."""
    # Avoid circular import by loading YAML directly
    import yaml
    from pathlib import Path
    
    semantic_path = Path(__file__).resolve().parent.parent / "semantic" / "semantic_layer.yaml"
    if not semantic_path.exists():
        return []
    
    with open(semantic_path, 'r') as f:
        semantic = yaml.safe_load(f)
    
    tables = []
    tbl_block = semantic.get("tables", {}) if semantic else {}
    if not tbl_block:
        return tables
    if not tbl_block:
        return tables

    # tables block: {"version": "1.0", "tables": [...]}
    tbl_list = tbl_block.get("tables", [])
    if not tbl_list:
        return tables

    for tbl_def in tbl_list:
        tbl_name = tbl_def.get("name", "")
        if not tbl_name:
            continue
        cols = []
        for col_def in tbl_def.get("columns", []):
            if isinstance(col_def, str):
                cols.append(ColumnDef(name=col_def, type="TEXT"))
            else:
                cols.append(ColumnDef(
                    name=col_def.get("name", ""),
                    type=col_def.get("type", "TEXT"),
                    nullable=col_def.get("nullable", True),
                    primary_key=col_def.get("primary_key", False),
                ))
        indexes = tbl_def.get("indexes", [])
        tables.append(TableDef(name=tbl_name, columns=cols, indexes=indexes))

    return tables


def _compute_schema_hash(tables: list[TableDef]) -> str:
    """Compute a stable hash of the table definitions."""
    serialized = json.dumps([
        {"name": t.name, "columns": [
            {"name": c.name, "type": c.type, "nullable": c.nullable, "pk": c.primary_key}
            for c in t.columns
        ]}
        for t in tables
    ], sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# ── Built-in Migrations ──

BUILTIN_MIGRATIONS: list[MigrationStep] = [
    MigrationStep(
        version=1,
        description="Initial schema: fct_orders, dim_store, dim_product",
        sql_up="""CREATE TABLE IF NOT EXISTS fct_orders (
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
""",
        sql_down="DROP TABLE IF EXISTS fct_orders; DROP TABLE IF EXISTS dim_store; DROP TABLE IF EXISTS dim_product;",
    ),
    MigrationStep(
        version=2,
        description="Add indexes for common query patterns",
        sql_up="""CREATE INDEX IF NOT EXISTS idx_fct_channel ON fct_orders(channel);
CREATE INDEX IF NOT EXISTS idx_fct_paid_at ON fct_orders(paid_at);
CREATE INDEX IF NOT EXISTS idx_fct_store ON fct_orders(store_id);
CREATE INDEX IF NOT EXISTS idx_fct_product ON fct_orders(product_id);
CREATE INDEX IF NOT EXISTS idx_dim_region ON dim_store(region);
CREATE INDEX IF NOT EXISTS idx_dim_cat ON dim_product(category);
""",
        sql_down="""DROP INDEX IF EXISTS idx_fct_channel;
DROP INDEX IF EXISTS idx_fct_paid_at;
DROP INDEX IF EXISTS idx_fct_store;
DROP INDEX IF EXISTS idx_fct_product;
DROP INDEX IF EXISTS idx_dim_region;
DROP INDEX IF EXISTS idx_dim_cat;
""",
    ),
]


# ── Migration Engine ──

class SchemaMigrator:
    """Apply and track schema migrations."""

    def __init__(self, pool):
        self.pool = pool

    @property
    def dialect(self):
        return self.pool.dialect

    def current_version(self) -> int:
        """Get the current schema version from the database, or 0 if uninitialized."""
        try:
            rows = self.pool.execute(
                "SELECT MAX(version) AS v FROM _schema_version"
            )
            return rows[0].get("v") or 0 if rows else 0
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            return 0

    def ensure_migration_table(self):
        """Create the migration tracking table if absent."""
        try:
            self.pool.execute("""
                CREATE TABLE IF NOT EXISTS _schema_version (
                    version     INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    checksum    TEXT NOT NULL,
                    applied_at  TEXT NOT NULL,
                    duration_ms REAL NOT NULL
                )
            """)
        except Exception as e:
            logger.warning("migration_table_create_failed", error=str(e))

    def pending_migrations(self) -> list[MigrationStep]:
        """Return migrations not yet applied, ordered by version."""
        current = self.current_version()
        builtins = [m for m in BUILTIN_MIGRATIONS if m.version > current]

        # Also check for semantic-layer-driven migrations
        tables = _load_semantic_tables()
        schema_hash = _compute_schema_hash(tables)

        # Check if existing data needs new migration
        if builtins:
            return sorted(builtins, key=lambda m: m.version)

        return []

    def apply(self, target_version: int | None = None) -> int:
        """Apply all pending migrations. Returns new version."""
        self.ensure_migration_table()
        pending = self.pending_migrations()
        current = self.current_version()

        if not pending:
            logger.info("no_pending_migrations", current=current)
            return current

        applied = 0
        for migration in pending:
            if target_version is not None and migration.version > target_version:
                break

            logger.info("applying_migration",
                        version=migration.version,
                        description=migration.description)
            t0 = datetime.datetime.now()

            try:
                # Execute each statement separately
                for stmt in migration.sql_up.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        self.pool.execute(stmt)
            except Exception as e:
                logger.error("migration_failed",
                             version=migration.version, error=str(e))
                raise

            dt = (datetime.datetime.now() - t0).total_seconds() * 1000
            self.pool.execute(
                "INSERT INTO _schema_version (version, description, checksum, applied_at, duration_ms) VALUES (?, ?, ?, ?, ?)",
                (migration.version, migration.description,
                 migration.checksum or _compute_schema_hash(_load_semantic_tables()),
                 datetime.datetime.now().isoformat(), int(dt)),
            )
            applied += 1

        new_version = self.current_version()
        logger.info("migrations_applied",
                    applied=applied,
                    old_version=current,
                    new_version=new_version)
        return new_version

    def rollback(self, to_version: int = 0):
        """Rollback migrations down to target version (inclusive)."""
        current = self.current_version()
        if current <= to_version:
            return

        # Apply DOWN scripts in reverse order
        to_rollback = [m for m in BUILTIN_MIGRATIONS
                       if to_version < m.version <= current]
        to_rollback.sort(key=lambda m: m.version, reverse=True)

        for migration in to_rollback:
            logger.warning("rolling_back",
                           version=migration.version,
                           description=migration.description)
            for stmt in migration.sql_down.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        self.pool.execute(stmt)
                    except Exception as e:
                        logger.error("rollback_stmt_failed",
                                     version=migration.version, error=str(e))

            self.pool.execute(
                "DELETE FROM _schema_version WHERE version = ?",
                (migration.version,),
            )

    def status(self) -> dict:
        """Return migration status report."""
        current = self.current_version()
        pending = self.pending_migrations()

        # Load history
        try:
            history = self.pool.execute(
                "SELECT * FROM _schema_version ORDER BY version"
            )
        except Exception as e:
            logger.warning("bare_exception_caught", error=str(e))
            history = []

        return {
            "current_version": current,
            "pending_count": len(pending),
            "pending": [{"version": m.version, "description": m.description}
                       for m in pending],
            "history": history,
            "builtin_count": len(BUILTIN_MIGRATIONS),
        }


# ── Convenience ──

def migrate(pool, target_version: int | None = None) -> int:
    """Run pending migrations on the pool. Returns new version."""
    migrator = SchemaMigrator(pool)
    return migrator.apply(target_version)


def check_schema(pool) -> dict:
    """Check schema migration status."""
    migrator = SchemaMigrator(pool)
    return migrator.status()
