# -*- coding: utf-8 -*-
"""Tests for append-only audit logger."""

import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_audit_logger_writes_jsonl_query_event():
    from audit_logger import AuditLogger

    path = os.path.join(tempfile.gettempdir(), "data_agent_audit_test.jsonl")
    if os.path.exists(path):
        os.remove(path)
    logger = AuditLogger(path=path)
    event = logger.log_query("u1", "select gmv", status="allow", sql="SELECT 1", trace_id="t1")

    assert event["user_id"] == "u1"
    assert event["sql_hash"]
    with open(path, "r") as f:
        lines = f.readlines()
    assert len(lines) == 1
    loaded = json.loads(lines[0])
    assert loaded["event_type"] == "query"
    assert loaded["trace_id"] == "t1"


def test_audit_logger_writes_blocked_reason():
    from audit_logger import AuditLogger

    path = os.path.join(tempfile.gettempdir(), "data_agent_audit_blocked_test.jsonl")
    if os.path.exists(path):
        os.remove(path)
    logger = AuditLogger(path=path)
    logger.log_query("u2", "delete from orders", status="blocked", blocked_reason="dangerous_query")

    with open(path, "r") as f:
        loaded = json.loads(f.readline())
    assert loaded["status"] == "blocked"
    assert loaded["blocked_reason"] == "dangerous_query"


if __name__ == "__main__":
    test_audit_logger_writes_jsonl_query_event()
    test_audit_logger_writes_blocked_reason()
    print("All audit logger tests passed!")
