# -*- coding: utf-8 -*-
"""Runtime resilience regression tests for cache scope and circuit breaker safety."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_result_cache_rejects_permission_masking_and_data_version_mismatch():
    from result_cache import CacheScope, ResultCache

    cache = ResultCache(ttl_seconds=300)
    query = u"最近7天GMV"
    result = {"status": "ok", "authority": "verified_execution", "results": [{"gmv": 1}]}

    analyst_v1 = {
        "tenant_id": "tenant_a",
        "user_id": "u1",
        "role": "analyst",
        "metadata": {"masked_fields": ["phone"]},
        "data_version": "v1",
    }
    scope_v1 = CacheScope.from_context(query=query, plan_hash="pre_plan", access_context=analyst_v1)
    store_decision = cache.set(query, result, plan_hash="pre_plan", scope=scope_v1)
    assert store_decision["action"] == "store"

    same_scope = CacheScope.from_context(query=query, plan_hash="pre_plan", access_context=dict(analyst_v1))
    cached, decision = cache.get(query, plan_hash="pre_plan", scope=same_scope, return_decision=True)
    assert cached == result
    assert decision["action"] == "hit"

    tenant_b = dict(analyst_v1)
    tenant_b["tenant_id"] = "tenant_b"
    cached, decision = cache.get(
        query, plan_hash="pre_plan",
        scope=CacheScope.from_context(query=query, plan_hash="pre_plan", access_context=tenant_b),
        return_decision=True)
    assert cached is None
    assert decision["action"] == "miss"

    unmasked = dict(analyst_v1)
    unmasked["metadata"] = {"masked_fields": []}
    cached, decision = cache.get(
        query, plan_hash="pre_plan",
        scope=CacheScope.from_context(query=query, plan_hash="pre_plan", access_context=unmasked),
        return_decision=True)
    assert cached is None
    assert decision["action"] == "miss"

    v2 = dict(analyst_v1)
    v2["data_version"] = "v2"
    cached, decision = cache.get(
        query, plan_hash="pre_plan",
        scope=CacheScope.from_context(query=query, plan_hash="pre_plan", access_context=v2),
        return_decision=True)
    assert cached is None
    assert decision["action"] == "miss"


def test_result_cache_never_stores_non_ok_or_unverified_results():
    from result_cache import CacheScope, ResultCache

    cache = ResultCache(ttl_seconds=300)
    scope = CacheScope.from_context(
        query="q", plan_hash="pre_plan",
        access_context={"tenant_id": "t1", "role": "analyst"})
    assert cache.set("q", {"status": "error"}, plan_hash="pre_plan", scope=scope)["action"] == "reject"
    assert cache.set("q", {"status": "ok", "authority": "unverified"}, plan_hash="pre_plan", scope=scope)["reason"] == "unverified_authority"
    assert cache.size() == 0


def test_facade_records_cache_hit_without_leaking_raw_query_or_scope_values():
    from agent_facade import AgentFacade
    from observability import ObservationRecorder
    from result_cache import ResultCache

    cache = ResultCache(ttl_seconds=300)
    access_context = {
        "tenant_id": "tenant_cache_test",
        "user_id": "user_cache_test",
        "role": "analyst",
        "data_version": "snapshot_1",
    }
    facade = AgentFacade(session_id="cache-trace", result_cache=cache, access_context=access_context)
    facade.observer = ObservationRecorder()

    first = facade.ask(u"看GMV", access_context=access_context)
    assert first["status"] == "ok"
    assert cache.size() == 1

    second = facade.ask(u"看GMV", access_context=access_context)
    assert second["status"] == "ok"
    assert second.get("from_cache") is True
    cache_events = [event for event in facade.get_trace() if event.get("name") == "result_cache"]
    assert cache_events[-1]["status"] == "hit"
    payload_text = repr(cache_events[-1].get("metadata") or {})
    assert u"看GMV" not in payload_text
    assert "tenant_cache_test" not in payload_text
    assert "user_cache_test" not in payload_text


def test_circuit_breaker_transitions_are_consistent_after_failures_and_recovery():
    from circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

    breaker = CircuitBreaker(name="resilience", failure_threshold=2, recovery_timeout=0.0,
                             half_open_max_calls=1, success_threshold=1)

    def fail():
        raise RuntimeError("boom")

    for _ in range(2):
        try:
            breaker.call(fail)
        except RuntimeError:
            pass
    assert breaker.stats["state"] == breaker.OPEN

    def ok():
        return "ok"

    assert breaker.call(ok) == "ok"
    assert breaker.stats["state"] == breaker.CLOSED

    breaker = CircuitBreaker(name="half_open_limit", failure_threshold=1, recovery_timeout=999.0)
    try:
        breaker.call(fail)
    except RuntimeError:
        pass
    try:
        breaker.call(ok)
        raised = False
    except CircuitBreakerOpenError:
        raised = True
    assert raised is True
