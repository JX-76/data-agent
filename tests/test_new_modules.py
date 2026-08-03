# -*- coding: utf-8 -*-
"""Smoke tests for Phase 2 new modules."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_contracts_normalize():
    from contracts import normalize_result
    r = normalize_result({"status": "ok"}, query="test query")
    assert r["query"] == "test query"
    assert r["status"] == "ok"
    assert "execution" in r
    assert "steps" in r


def test_contracts_analysis_plan():
    from contracts import AnalysisPlan
    p = AnalysisPlan(query="hello", intent="metric_query", metric="gmv")
    d = p.to_dict()
    assert d["query"] == "hello"
    assert d["intent"] == "metric_query"
    assert d["metric"] == "gmv"
    assert d["dimensions"] == []


def test_router_core_time_range():
    from router_core import parse_time_range_label
    import datetime as dt
    now = dt.datetime(2026, 7, 8, 12, 0, 0)
    start, end = parse_time_range_label("yesterday", now=now)
    assert start.day == 7
    assert end.day == 8


def test_router_core_blocked():
    from router_core import detect_blocked_query
    blocked, reason = detect_blocked_query("delete from users")
    assert blocked is True
    assert reason is not None


def test_router_core_defaults():
    from router_core import ensure_plan_defaults
    plan = ensure_plan_defaults({}, "safe query")
    assert plan["status"] == "ok"
    assert plan["metric"] == "gmv"
    assert plan["model"] == "order_detail"


def test_memory_store():
    from memory_store import MemoryStore
    store = MemoryStore()
    store.remember("session", "last_query", "hello")
    items = store.recall(scope="session")
    assert len(items) == 1
    assert items[0].value == "hello"
    store.clear(scope="session")
    assert store.recall(scope="session") == []


def test_observability():
    from observability import ObservationRecorder
    rec = ObservationRecorder()
    ev = rec.record("route", trace_id="t1", status="ok", intent="metric_query")
    assert ev.trace_id == "t1"
    assert ev.name == "route"
    events = rec.events(trace_id="t1")
    assert len(events) == 1
    rec.clear()
    assert rec.events() == []


def test_agent_extensions():
    from agent_extensions import build_task_plan
    plan = build_task_plan("complex query", steps=[
        {"name": "route", "purpose": "classify"},
        {"name": "execute", "purpose": "run sql"},
    ])
    assert plan.query == "complex query"
    assert len(plan.steps) == 2
    assert plan.steps[0].name == "route"
    assert plan.steps[1].status == "pending"


if __name__ == "__main__":
    test_contracts_normalize()
    test_contracts_analysis_plan()
    test_router_core_time_range()
    test_router_core_blocked()
    test_router_core_defaults()
    test_memory_store()
    test_observability()
    test_agent_extensions()
    print("All Phase 2 smoke tests passed!")
