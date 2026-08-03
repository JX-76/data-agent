# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import release_api
from release_api import (ask_release, followup_release, release_dashboard, release_health,
                         release_history, record_gate_result, ecommerce_graph_release,
                         correct_analysis_release)
from circuit_breaker import get_circuit_breaker_registry
from ollama_adapter import OllamaAdapter, HTTPError


def test_release_envelope_has_additive_api_version_and_terminal():
    env = ask_release("最近7天GMV", session_id="r24_contract", use_llm=False)
    assert env["contract"] == "release_v1_envelope"
    assert env["api_version"] == "v1"
    assert env["terminal"] == env["status"]
    assert set(["summary", "table", "chart", "caveats", "next_steps"]).issubset(env["answer"])
    chain = env["release_chain"]
    assert chain["contract"] == "release_chain_v1"
    assert len(chain["steps"]) == 5
    assert "overall_confidence" in chain
    assert set(["analysis_code_view_v1", "hidden"]).issuperset([chain["code_view"]["contract"]])


def test_release_chain_is_safe_for_product_rendering_and_does_not_expose_internal_sql(monkeypatch):
    facade = _DummyFacade()
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    env = ask_release("最近7天GMV", session_id="chain_safe")
    chain = env["release_chain"]
    rendered = repr(chain).lower()
    assert "alice@example.com" not in rendered
    assert "13812345678" not in rendered
    assert "select " not in rendered
    assert "backend_source" in chain["hidden_internal_fields"]
    for step in chain["steps"]:
        assert "confidence" in step


def test_history_is_metadata_only_and_dashboard_is_safe_aggregate():
    ask_release("删除订单", session_id="r24_history", use_llm=False)
    history = release_history(limit=10)
    assert history["contract"] == "release_v1_history"
    assert "raw" not in history["items"][0]
    assert "sql" not in history["items"][0]
    dashboard = release_dashboard()
    assert dashboard["contract"] == "release_v1_dashboard"
    assert set(["success_rate", "terminal_breakdown", "failure_categories", "latency", "capability_coverage", "recent_gate"]).issubset(dashboard)
    assert "query" not in dashboard
    assert "raw" not in dashboard
    assert "sql" not in dashboard


def test_health_and_gate_result_are_ready_and_safe():
    record_gate_result("r24_test", True, total=3, failed=0, summary="safe aggregate")
    health = release_health()
    assert health["contract"] == "release_v1_health"
    assert health["ready"] is True
    assert health["recent_gate"]["name"] == "r24_test"
    assert health["components"]["masking"] == "ready"



def test_ollama_adapter_maps_provider_500_to_safe_runtime_category():
    def _raise_provider_error(request, timeout):
        raise HTTPError("http://fixture", 500, "internal provider diagnostic", {}, None)

    # The adapter converts provider diagnostics to a stable public category.
    adapter = OllamaAdapter(opener=_raise_provider_error)
    try:
        adapter._request_json("/api/chat", {"model": "fixture"})
        assert False, "expected normalized local provider error"
    except Exception as exc:
        # The public release path exposes only the category/message, never the
        # original provider body or CUDA/runtime diagnostic.
        assert getattr(exc, "code", None) == "provider_runtime_error"
        assert "diagnostic" not in getattr(exc, "message", str(exc)).lower()


class _DummyFacade(object):
    def __init__(self, mode="ok"):
        self.last_access_context = None
        self.access_context = None
        self.mode = mode

    def ask(self, query, use_llm=False, access_context=None, analysis_method=None):
        self.last_access_context = access_context
        if self.mode == "raise":
            raise RuntimeError("backend boom")
        if self.mode == "unknown_status":
            return {"status": "mystery", "summary": "bad terminal"}
        return {"status": "ok", "summary": "done", "results": [{"email": "alice@example.com", "phone": "13812345678"}], "tenant_id": access_context.get("tenant_id")}


def test_release_api_uses_trusted_header_identity_and_masks_payload(monkeypatch):
    facade = _DummyFacade()
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    env = ask_release(
        "查看 alice@example.com 的最近7天GMV",
        session_id="security_ctx",
        headers={"x-dev-user-id": "trusted-user", "x-dev-tenant-id": "tenant-sec", "x-dev-roles": "analyst"},
        user_id="body-spoof",
    )
    assert facade.last_access_context["user_id"] == "trusted-user"
    assert facade.last_access_context["tenant_id"] == "tenant-sec"
    text = repr(env)
    assert "alice@example.com" not in text
    assert "13812345678" not in text
    assert env["raw"]["tenant_id"] == "tenant-sec"


def test_followup_reuses_governed_ask_pipeline_and_trusted_access_context(monkeypatch):
    facade = _DummyFacade()
    session_id = "followup_governed_session"
    access = {"user_id": "followup-user", "tenant_id": "followup-tenant", "role": "analyst"}
    release_api._SESSION_ACCESS_INDEX[session_id] = {
        "user_id": access["user_id"], "tenant_id": access["tenant_id"], "roles": ["analyst"], "created_at": 0,
    }
    monkeypatch.setattr(release_api, "_facade", lambda requested: (requested, facade))

    env = followup_release("按地区拆解", session_id=session_id, access_context=access)

    assert facade.last_access_context["user_id"] == "followup-user"
    assert facade.last_access_context["tenant_id"] == "followup-tenant"
    assert env["raw"].get("error", {}).get("code") != "runtime_exception"
    assert "AgentFacade" not in repr(env["raw"])


def test_release_api_runtime_exception_returns_structured_error(monkeypatch):
    facade = _DummyFacade(mode="raise")
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    env = ask_release("最近7天GMV", session_id="runtime_error")
    assert env["contract"] == "release_v1_envelope"
    assert env["status"] == "error"
    assert env["terminal"] == "error"
    assert env["raw"]["error"]["code"] == "runtime_exception"
    assert env["raw"]["authority"] == "unverified"
    assert "trace_" in (env["raw"].get("trace_id") or "")
    assert env["safe_error"] == {
        "contract": "safe_error_v1", "stage": "release_api",
        "code": "runtime_exception", "retryable": True,
        "remediation": "retry_or_contact_operator",
        "trace_ref": env["audit_id"],
    }
    assert "boom" not in repr(env["safe_error"])


def test_release_api_unknown_status_is_not_allowed_to_escape(monkeypatch):
    facade = _DummyFacade(mode="unknown_status")
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    env = ask_release("最近7天GMV", session_id="unknown_status")
    assert env["status"] == "error"
    assert env["raw"]["error"]["code"] == "unknown_status"
    assert "mystery" in env["raw"]["error"]["message"]


def test_release_health_exposes_runtime_readiness_without_sensitive_scope():
    health = release_health()
    assert health["contract"] == "release_v1_health"
    assert "runtime" in health
    assert "timeout_seconds" in health["runtime"]
    assert "circuit_breakers" in health["runtime"]
    text = repr(health["runtime"])
    assert "alice@example.com" not in text
    assert "tenant-sec" not in text


def test_release_api_open_circuit_returns_retryable_error(monkeypatch):
    facade = _DummyFacade(mode="raise")
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", facade))
    breaker = get_circuit_breaker_registry().get("release_api_agent", failure_threshold=1, recovery_timeout=999.0)
    breaker._reset()
    breaker.failure_threshold = 1
    breaker.recovery_timeout = 999.0
    try:
        ask_release("第一次触发后端失败", session_id="circuit_open")
        env = ask_release("第二次应熔断", session_id="circuit_open")
        assert env["status"] == "error"
        assert env["raw"]["error"]["code"] == "circuit_open"
        assert env["raw"]["error"]["retryable"] is True
    finally:
        breaker._reset()


def test_ecommerce_graph_release_attaches_analysis_contract_and_evidence_bound_claims():
    env = ecommerce_graph_release({
        "graph_type": "metric_query", "metric": "gmv", "time_range": "last_7_days",
        "dimensions": ["region"], "filters": {"region": "east"},
        "rows": [{"region": "east", "gmv": 100}], "session_id": "analysis_release_graph",
    }, access_context={"user_id": "analysis-user", "tenant_id": "analysis-tenant", "role": "analyst"})
    assert env["status"] == "ok"
    control = env["analysis_control"]
    assert control["enabled"] is True
    assert control["analysis_contract"]["metric_definition"] == "gmv"
    assert control["analysis_contract"]["data_version"] == "release_controlled_fixture_v1"
    assert control["analysis_contract"]["evidence_ids"]
    assert all(claim["evidence_ids"] for claim in control["insight_claims"])


def test_analysis_correction_rejects_cross_session_and_invalidates_data_dependents():
    access = {"user_id": "correct-user", "tenant_id": "correct-tenant", "role": "analyst"}
    env = ecommerce_graph_release({
        "graph_type": "metric_query", "metric": "gmv", "time_range": "last_7_days",
        "dimensions": ["region"], "filters": {"region": "all"},
        "rows": [{"region": "east", "gmv": 100}], "session_id": "analysis_correct_session",
    }, access_context=access)
    response = correct_analysis_release({
        "session_id": "analysis_correct_session", "feedback": "不要全国数据，换成华东",
        "patch_ops": [{"field": "filters", "value": {"region": "east"}}],
    }, access_context=access)
    assert response["status"] == "needs_recompute"
    assert response["analysis_contract"]["evidence_ids"] == []
    assert response["stale_plan"]["state"] == "stale"
    denied = correct_analysis_release({"session_id": "analysis_correct_session", "feedback": "改图"},
                                      access_context={"user_id": "other", "tenant_id": "other-tenant", "role": "analyst"})
    assert denied["status"] == "blocked"
    assert denied["blocked_reason"] == "session_access_denied"
