# -*- coding: utf-8 -*-
"""Readonly database adapter abstractions.

This layer keeps the execution pipeline decoupled from any concrete database
implementation. It supports a mock adapter for deterministic tests and a
readonly SQLite adapter for local/integration execution.
"""

import sqlite3
import time

try:
    TimeoutError
except NameError:  # pragma: no cover - Python 2 compatibility
    TimeoutError = RuntimeError


class ReadonlyDBAdapter(object):
    """Stable DataSource Adapter SPI for self-hosted deployments.

    Downloaders can subclass this adapter to connect their own warehouse, API or
    BI query service. The legacy ``execute`` method remains for compatibility,
    while the SPI methods below provide a safer release-facing boundary.
    Implementations must never expose write execution to the Agent and should
    return normalized ``ExecutionEnvelope`` dictionaries for readonly calls.
    """

    adapter_type = "custom"

    def healthcheck(self):
        return {"status": "ok", "adapter_type": self.adapter_type}

    def introspect_schema(self):
        return self.describe_schema()

    def execute_readonly(self, sql, params=None, context=None):
        try:
            result = self.execute(sql, limit=(context or {}).get("limit"), offset=(context or {}).get("offset", 0))
            if isinstance(result, dict) and result.get("status") == "error":
                return _execution_envelope_from_result(result, status="error", stage="db_execute")
            return _execution_envelope_from_result(result, status="ok", stage="db_execute")
        except Exception as exc:
            return _execution_envelope_from_result({"error": str(exc), "error_type": "adapter_error"}, status="error", stage="db_execute")

    def close(self):
        return None

    def execute(self, sql, limit=None, offset=0):
        raise NotImplementedError

    def describe_schema(self):
        return {}

    def fetch_preview(self, table_name, limit=5):
        return []


class MockDBAdapter(ReadonlyDBAdapter):
    adapter_type = "mock"

    def __init__(self, schema=None, previews=None, query_log=None):
        self._schema = schema or {}
        self._previews = previews or {}
        self._query_log = query_log if query_log is not None else []

    def execute(self, sql, limit=None, offset=0):
        self._query_log.append(sql)
        return {
            "rows": [],
            "row_count": 0,
            "sql": sql,
            "source": "mock",
            "limit": limit,
            "offset": offset,
        }

    def describe_schema(self):
        return dict(self._schema)

    def fetch_preview(self, table_name, limit=5):
        return list(self._previews.get(table_name, []))[:limit]


class SQLiteReadonlyDBAdapter(ReadonlyDBAdapter):
    adapter_type = "sqlite"

    """Readonly SQLite adapter with light guardrails.

    It intentionally accepts only SELECT/WITH statements. This is not a full SQL
    firewall; project-level SQL validation still lives in runtime_core. The
    adapter guard exists as defense-in-depth for real local execution.
    """

    def __init__(self, database_path=":memory:", connection=None, row_limit=1000, timeout_ms=1500):
        self.database_path = database_path
        self.connection = connection
        self.row_limit = int(row_limit or 1000)
        self.timeout_ms = int(timeout_ms or 1500)

    def _connect(self):
        if self.connection is not None:
            return self.connection, False
        conn = sqlite3.connect(self.database_path, timeout=max(self.timeout_ms / 1000.0, 0.1))
        return conn, True

    def healthcheck(self):
        conn = None
        should_close = False
        try:
            conn, should_close = self._connect()
            conn.execute("SELECT 1")
            return {"status": "ok", "adapter_type": self.adapter_type, "database_path": self.database_path}
        except Exception as exc:
            return {"status": "error", "adapter_type": self.adapter_type, "error": str(exc)}
        finally:
            if should_close and conn is not None:
                conn.close()

    def _ensure_readonly(self, sql):
        normalized = (sql or "").strip().lower()
        if ";" in normalized.rstrip(";"):
            raise ValueError("readonly sqlite adapter rejects multi-statement SQL")
        if not (normalized.startswith("select") or normalized.startswith("with")):
            raise ValueError("readonly sqlite adapter only accepts SELECT/WITH statements")
        import re
        tokens = re.findall(r"[a-z_]+", normalized)
        dangerous = set(['insert', 'update', 'delete', 'drop', 'alter', 'create', 'replace',
                         'truncate', 'attach', 'detach', 'pragma', 'vacuum', 'reindex'])
        found = [token for token in tokens if token in dangerous]
        if found:
            raise ValueError("readonly sqlite adapter rejects dangerous SQL token: %s" % found[0])

    def _classify_error(self, exc):
        text = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timeout" in text:
            return "timeout"
        if "readonly" in text or "only accepts" in text or "dangerous sql token" in text:
            return "readonly_violation"
        if "no such table" in text or "no such column" in text:
            return "schema_error"
        if "syntax" in text:
            return "sql_syntax_error"
        return "db_error"

    def _apply_pagination(self, sql, limit=None, offset=0):
        safe_limit = self.row_limit if limit is None else max(0, min(int(limit or self.row_limit), self.row_limit))
        safe_offset = max(0, int(offset or 0))
        normalized = (sql or "").strip().rstrip(";")
        low = normalized.lower()
        # A compiler-provided LIMIT is already bounded by SQL preflight. Do not
        # append OFFSET after it: SQLite accepts only one LIMIT clause.
        if "limit" in low:
            return normalized
        return "%s LIMIT %d OFFSET %d" % (normalized, safe_limit + 1, safe_offset)

    def execute(self, sql, limit=None, offset=0):
        conn = None
        should_close = False
        start = time.time()
        try:
            self._ensure_readonly(sql)
            conn, should_close = self._connect()
            cursor = conn.cursor()
            paged_sql = self._apply_pagination(sql, limit=limit, offset=offset)
            cursor.execute(paged_sql)
            columns = [col[0] for col in (cursor.description or [])]
            rows = []
            effective_limit = self.row_limit if limit is None else max(0, min(int(limit or self.row_limit), self.row_limit))
            for row in cursor.fetchmany(effective_limit + 1):
                if (time.time() - start) * 1000 > self.timeout_ms:
                    raise TimeoutError("sqlite query timeout after %sms" % self.timeout_ms)
                if len(rows) < effective_limit:
                    rows.append(dict(zip(columns, row)))
            truncated = len(rows) >= effective_limit
            return {
                "rows": rows,
                "row_count": len(rows),
                "sql": paged_sql,
                "source": "sqlite",
                "truncated": truncated,
                "elapsed_ms": int((time.time() - start) * 1000),
                "limit": effective_limit,
                "offset": max(0, int(offset or 0)),
                "error_type": None,
            }
        except Exception as exc:
            normalized = (sql or "").strip().lower()
            # Preserve the legacy adapter contract for obviously non-readonly
            # top-level statements, while still normalizing readonly-looking
            # SELECT/WITH payload failures into structured error results for
            # execution pipelines that must not leak KeyError/ValueError.
            if isinstance(exc, ValueError) and not (normalized.startswith("select") or normalized.startswith("with")):
                raise
            return {
                "rows": [],
                "row_count": 0,
                "sql": sql,
                "source": "sqlite",
                "status": "error",
                "error": str(exc),
                "error_type": self._classify_error(exc),
                "elapsed_ms": int((time.time() - start) * 1000),
            }
        finally:
            if should_close and conn is not None:
                conn.close()

    def describe_schema(self):
        conn, should_close = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            tables = [row[0] for row in cursor.fetchall()]
            schema = {}
            for table in tables:
                cursor.execute("PRAGMA table_info(%s)" % _quote_identifier(table))
                cols = []
                for row in cursor.fetchall():
                    cols.append({"name": row[1], "type": row[2], "nullable": not bool(row[3]), "primary_key": bool(row[5])})
                schema[table] = cols
            return schema
        finally:
            if should_close:
                conn.close()

    def fetch_preview(self, table_name, limit=5):
        safe_table = _quote_identifier(table_name)
        safe_limit = max(0, min(int(limit or 5), self.row_limit))
        result = self.execute("SELECT * FROM %s LIMIT %d" % (safe_table, safe_limit))
        return result.get("rows", [])


class CustomAdapterTemplate(ReadonlyDBAdapter):
    """Copy/subclass template for external data sources.

    Override ``execute`` or ``execute_readonly`` to call your controlled query
    service. Keep readonly enforcement, permission checks and evidence metadata
    at this boundary so Agent callers receive a safe ``ExecutionEnvelope``.
    """

    adapter_type = "custom_template"

    def __init__(self, adapter_name="custom_template", schema=None):
        self.adapter_type = adapter_name
        self._schema = schema or {}

    def healthcheck(self):
        return {"status": "blocked", "adapter_type": self.adapter_type,
                "message": "template adapter is not configured"}

    def describe_schema(self):
        return dict(self._schema)

    def execute(self, sql, limit=None, offset=0):
        return {"status": "error", "error_type": "not_configured", "error": "custom adapter template is not configured",
                "rows": [], "row_count": 0, "source": self.adapter_type, "sql": sql}


def _execution_envelope_from_result(result, status="ok", stage="db_execute"):
    from contracts import build_execution_envelope
    result = result if isinstance(result, dict) else {}
    error_code = result.get("error_type") or result.get("error_code")
    message = result.get("error") or result.get("message")
    evidence_id = result.get("evidence_id")
    return build_execution_envelope(
        status=status,
        stage=stage,
        error_code=error_code if status != "ok" else None,
        retryable=bool(result.get("retryable")),
        message=message,
        query_id=result.get("query_id"),
        tool_call_id=result.get("tool_call_id"),
        evidence_id=evidence_id,
        dataid=result.get("dataid") or result.get("source"),
        data_version=result.get("data_version"),
        row_count=result.get("row_count") or len(result.get("rows") or []),
        time_range=result.get("time_range"),
        authority="verified_execution" if status == "ok" and evidence_id else "unverified",
        provenance=result.get("provenance") or {},
        metadata={"source": result.get("source"), "truncated": result.get("truncated"), "sql": result.get("sql")},
    )


def _quote_identifier(name):
    text = str(name or "")
    if not text.replace("_", "").isalnum():
        raise ValueError("invalid identifier: %s" % text)
    return '"%s"' % text.replace('"', '""')


class ReadonlyQueryExecutor(object):
    def __init__(self, db=None):
        self.db = db or MockDBAdapter()

    def execute(self, sql, limit=None, offset=0):
        return self.db.execute(sql, limit=limit, offset=offset)

    def describe_schema(self):
        return self.db.describe_schema()

    def fetch_preview(self, table_name, limit=5):
        return self.db.fetch_preview(table_name, limit=limit)


__all__ = ["ReadonlyDBAdapter", "MockDBAdapter", "SQLiteReadonlyDBAdapter", "CustomAdapterTemplate", "ReadonlyQueryExecutor"]
