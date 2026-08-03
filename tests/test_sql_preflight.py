# -*- coding: utf-8 -*-
"""Tests for the SQL preflight checker."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sql_preflight import validate_sql_preflight


def test_valid_cte_passes():
    sql = "WITH d1 AS (SELECT 1) SELECT * FROM d1"
    r = validate_sql_preflight(sql, require_runtime_cte=False)
    assert r["valid"] is True
    assert r["errors"] == []
    assert r["contract"] == "sql_preflight_v1"
    assert r["statement_type"] == "with"


def test_valid_select_passes_when_cte_not_required():
    sql = "SELECT gmv FROM orders WHERE dt = '2026-07-01'"
    r = validate_sql_preflight(sql, require_runtime_cte=False)
    assert r["valid"] is True


def test_empty_sql_fails():
    r = validate_sql_preflight("", require_runtime_cte=False)
    assert r["valid"] is False
    assert any("empty" in e for e in r["errors"])


def test_delete_is_blocked():
    r = validate_sql_preflight("DELETE FROM fct_orders", require_runtime_cte=False)
    assert r["valid"] is False
    assert any("mutating" in e for e in r["errors"])


def test_drop_is_blocked():
    r = validate_sql_preflight("DROP TABLE fct_orders", require_runtime_cte=False)
    assert r["valid"] is False


def test_insert_is_blocked():
    r = validate_sql_preflight("INSERT INTO t VALUES (1)", require_runtime_cte=False)
    assert r["valid"] is False


def test_update_is_blocked():
    r = validate_sql_preflight("UPDATE t SET x=1", require_runtime_cte=False)
    assert r["valid"] is False


def test_comment_is_blocked():
    r = validate_sql_preflight("SELECT 1 -- bypass", require_runtime_cte=False)
    assert r["valid"] is False
    assert any("comment" in e for e in r["errors"])


def test_multiple_statements_blocked():
    r = validate_sql_preflight("SELECT 1; SELECT 2", require_runtime_cte=False)
    assert r["valid"] is False


def test_require_runtime_cte_rejects_plain_select():
    r = validate_sql_preflight("SELECT * FROM orders", require_runtime_cte=True)
    assert r["valid"] is False


def test_require_runtime_cte_accepts_cte_chain():
    sql = "WITH d1 AS (SELECT 1) SELECT * FROM d1"
    r = validate_sql_preflight(sql, require_runtime_cte=True)
    assert r["valid"] is True


def test_legacy_validator_is_called():
    def _fail(sql):
        return False, "custom rejection"

    r = validate_sql_preflight("WITH d1 AS (SELECT 1) SELECT * FROM d1", validator=_fail, require_runtime_cte=False)
    assert r["valid"] is False
    assert "custom rejection" in r["errors"]
    assert r["legacy_validator"]["applied"] is True
    assert r["legacy_validator"]["valid"] is False


def test_legacy_validator_pass_propagates():
    def _pass(sql):
        return True, "ok"

    r = validate_sql_preflight("WITH d1 AS (SELECT 1) SELECT * FROM d1", validator=_pass, require_runtime_cte=False)
    assert r["valid"] is True
    assert r["legacy_validator"]["applied"] is True
    assert r["legacy_validator"]["valid"] is True


def test_ast_blocks_union_sensitive_table_exfiltration():
    sql = "SELECT gmv FROM orders UNION ALL SELECT password FROM users"
    r = validate_sql_preflight(sql, require_runtime_cte=False)
    assert r["valid"] is False
    assert any("UNION" in e for e in r["errors"])
    assert any("sensitive" in e for e in r["errors"])
    assert r["ast"]["has_union"] is True


def test_ast_blocks_nested_sensitive_subquery():
    sql = "SELECT * FROM (SELECT password FROM users) t"
    r = validate_sql_preflight(sql, require_runtime_cte=False)
    assert r["valid"] is False
    assert any("subquery" in e for e in r["errors"])
    assert any("sensitive" in e for e in r["errors"])
    assert r["ast"]["has_subquery"] is True


def test_ast_traverses_cte_and_metadata_table_allowlist():
    catalog = {"tables": {"orders": {"columns": ["gmv", "dt"]}}}
    sql = "WITH d1 AS (SELECT password FROM users) SELECT * FROM d1"
    r = validate_sql_preflight(sql, require_runtime_cte=False, metadata_catalog=catalog)
    assert r["valid"] is False
    assert "users" in r["ast"]["tables"]
    assert any("allowlist" in e or "sensitive" in e for e in r["errors"])


def test_ast_can_explicitly_allow_trusted_subquery_and_join():
    sql = "SELECT o.gmv FROM orders o JOIN stores s ON o.store_id=s.id WHERE o.gmv > 0"
    r = validate_sql_preflight(sql, require_runtime_cte=False, allow_join=True)
    assert r["valid"] is True
    assert r["ast"]["has_join"] is True


def test_report_contains_sql_length():
    sql = "WITH d1 AS (SELECT 1) SELECT * FROM d1"
    r = validate_sql_preflight(sql, require_runtime_cte=False)
    assert r["sql_length"] == len(sql)


def test_execution_engine_exposes_preflight_compatibly():
    """The main engine consumes the report while preserving its tuple API."""
    from execution_engine import ExecutionEngine

    engine = ExecutionEngine(validator=lambda sql: (True, "ok"), max_retries=0)
    report = engine._preflight_sql("WITH d1 AS (SELECT 1) SELECT * FROM d1")
    ok, reason = engine._validate_sql("WITH d1 AS (SELECT 1) SELECT * FROM d1")

    assert report["valid"] is True
    assert report["contract"] == "sql_preflight_v1"
    assert ok is True
    assert reason == "ok"


if __name__ == "__main__":
    test_valid_cte_passes()
    test_valid_select_passes_when_cte_not_required()
    test_empty_sql_fails()
    test_delete_is_blocked()
    test_drop_is_blocked()
    test_insert_is_blocked()
    test_update_is_blocked()
    test_comment_is_blocked()
    test_multiple_statements_blocked()
    test_require_runtime_cte_rejects_plain_select()
    test_require_runtime_cte_accepts_cte_chain()
    test_legacy_validator_is_called()
    test_legacy_validator_pass_propagates()
    test_ast_blocks_union_sensitive_table_exfiltration()
    test_ast_blocks_nested_sensitive_subquery()
    test_ast_traverses_cte_and_metadata_table_allowlist()
    test_ast_can_explicitly_allow_trusted_subquery_and_join()
    test_report_contains_sql_length()
    test_execution_engine_exposes_preflight_compatibly()
    print("All sql_preflight tests passed!")
