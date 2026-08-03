# -*- coding: utf-8 -*-
"""Production gate tests for launch-blocking controls."""

from io import open
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_identity_resolution_from_headers():
    from identity import resolve_identity

    ctx = resolve_identity({"X-User-ID": "u-prod", "X-Tenant-ID": "tenant-a"})
    data = ctx.to_dict()
    assert data["authenticated"] is True
    assert data["user_id"] == "u-prod"
    assert data["tenant_id"] == "tenant-a"
    assert data["quota_key"] == "tenant-a:u-prod"


def test_governance_uses_header_identity_and_persistent_audit():
    from audit_logger import AuditLogger
    from governance import GovernanceFacade, _InMemoryQuota

    fd, path = tempfile.mkstemp(prefix="audit_prod_", suffix=".jsonl")
    os.close(fd)
    try:
        audit = AuditLogger(path=path)
        gov = GovernanceFacade(tenant_manager=None, quota=_InMemoryQuota(daily_limit=1))
        gov.audit = audit
        first = gov.check_query("昨天GMV是多少？", headers={"X-User-ID": "u1", "X-Tenant-ID": "t1"}, trace_id="tr-1")
        second = gov.check_query("今天GMV是多少？", headers={"X-User-ID": "u1", "X-Tenant-ID": "t1"}, trace_id="tr-2")

        assert first.allowed is True
        assert second.allowed is False
        assert second.decision_type == "quota_exceeded"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "t1:u1" in content
        assert "quota_exceeded" in content
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def test_sqlite_pagination_and_error_classification():
    import sqlite3
    from db_adapter import SQLiteReadonlyDBAdapter

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (id INTEGER, gmv REAL)")
    for i in range(5):
        conn.execute("INSERT INTO orders (id, gmv) VALUES (?, ?)", (i, i * 10.0))
    conn.commit()

    adapter = SQLiteReadonlyDBAdapter(connection=conn, row_limit=2)
    ok = adapter.execute("SELECT id, gmv FROM orders ORDER BY id", limit=2, offset=1)
    assert ok["row_count"] == 2
    assert ok["rows"][0]["id"] == 1
    assert ok["truncated"] is True

    bad = adapter.execute("SELECT missing_col FROM orders")
    assert bad["status"] == "error"
    assert bad["error_type"] == "schema_error"


def test_secret_scan_release_gate_clean():
    from secret_scan import assert_no_secrets

    assert assert_no_secrets(PROJECT_ROOT) is True


if __name__ == "__main__":
    test_identity_resolution_from_headers()
    test_governance_uses_header_identity_and_persistent_audit()
    test_sqlite_pagination_and_error_classification()
    test_secret_scan_release_gate_clean()
    print("All production gate tests passed!")
