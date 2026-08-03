# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from evidence_bus import EvidenceBus
from final_output_evidence_gate import (
    apply_final_output_evidence_gate,
    validate_final_output_boundary,
)


def _execution_envelope(evidence_id="ev_good", metric="gmv", time_range="last_7_days",
                        tenant_id="tenant-a", user_id="user-a", data_version="dv1"):
    return {
        "status": "ok",
        "authority": "verified_execution",
        "evidence_id": evidence_id,
        "query_id": "q1",
        "tool_call_id": "tool1",
        "dataid": "orders",
        "data_version": data_version,
        "row_count": 1,
        "time_range": time_range,
        "provenance": {"trace_id": "trace1", "tenant_id": tenant_id, "user_id": user_id},
        "metadata": {"metric": metric, "tenant_id": tenant_id, "user_id": user_id, "filters": {}},
    }


def _answer_contract(evidence_id="ev_good", metric="gmv", time_range="last_7_days",
                     data_version="dv1", dataid="orders", citations=None):
    return {
        "contract": "final_answer_contract_v2",
        "status": "ok",
        "answer_type": "analysis",
        "answer": "GMV is 100.",
        "facts": [{"text": "GMV is 100.", "evidence_ids": [evidence_id]}],
        "hypotheses": [],
        "citations": citations or [],
        "limitations": [],
        "next_actions": [],
        "provenance": {
            "query_id": "q1",
            "tool_call_id": "tool1",
            "evidence_id": evidence_id,
            "dataid": dataid,
            "data_version": data_version,
            "row_count": 1,
            "time_range": time_range,
            "metric": metric,
        },
        "trace_id": "trace1",
        "task_id": "task1",
        "evidence_ids": [evidence_id],
    }


def _envelope(answer_contract=None, raw=None, session_id="case-a"):
    return {
        "contract": "release_v1_envelope",
        "status": "ok",
        "session_id": session_id,
        "audit_id": "audit1",
        "answer": {"summary": "GMV is 100.", "table": [], "chart": {}, "caveats": [], "next_steps": []},
        "provenance": {},
        "raw": raw or {},
        "answer_contract": answer_contract or _answer_contract(),
        "elapsed_ms": 1,
    }


def _bus(case_id="case-a", envelope=None, recorded_at=None):
    bus = EvidenceBus()
    record = bus.record_envelope(envelope or _execution_envelope(), case_id=case_id, trace_id="trace1")
    if recorded_at is not None:
        record["recorded_at"] = recorded_at
    return bus


def _codes(report):
    return set([item.get("code") for item in report.get("findings") or []])


def test_final_output_allows_current_case_linked_evidence():
    env = _envelope(raw={"execution_envelope": _execution_envelope(), "tenant_id": "tenant-a", "user_id": "user-a"})
    report = validate_final_output_boundary(
        env,
        evidence_bus=_bus(),
        case_id="case-a",
        access_context={"tenant_id": "tenant-a", "user_id": "user-a"},
        entrypoint="unit_release",
    )
    assert report["contract"] == "final_output_evidence_validation_v1"
    assert report["allowed"] is True
    assert report["metrics"]["final_output_lineage_coverage"] == 1.0


def test_missing_evidence_bus_demotes_verified_facts():
    env = _envelope()
    gated = apply_final_output_evidence_gate(env, evidence_bus=None, case_id="case-a", require_evidence_bus=True)
    assert gated["final_output_evidence_gate"]["allowed"] is False
    assert "missing_evidence_bus" in _codes(gated["final_output_evidence_gate"])
    assert gated["answer_contract"]["facts"] == []
    assert gated["answer_contract"]["hypotheses"]
    assert gated["answer_contract"]["answer_type"] == "evidence_limited"


def test_scope_ttl_permission_and_case_link_fail_closed():
    access = {"tenant_id": "tenant-a", "user_id": "user-a"}
    env = _envelope()

    stale = validate_final_output_boundary(
        env, evidence_bus=_bus(recorded_at=time.time() - 1000), case_id="case-a",
        access_context=access, ttl_seconds=1)
    assert "evidence_ttl_expired" in _codes(stale)
    assert stale["allowed"] is False

    wrong_metric = _bus(envelope=_execution_envelope(metric="orders"))
    scope = validate_final_output_boundary(env, evidence_bus=wrong_metric, case_id="case-a", access_context=access)
    assert "evidence_scope_mismatch" in _codes(scope)
    assert scope["metrics"]["scope_leakage_attempt_rate"] > 0

    wrong_user = _bus(envelope=_execution_envelope(user_id="other-user"))
    permission = validate_final_output_boundary(env, evidence_bus=wrong_user, case_id="case-a", access_context=access)
    assert "evidence_scope_mismatch" in _codes(permission)

    unlinked_bus = _bus(case_id="other-case")
    case_scope = validate_final_output_boundary(env, evidence_bus=unlinked_bus, case_id="case-a", access_context=access)
    assert "evidence_not_linked_to_case" in _codes(case_scope)


def test_citation_must_reference_valid_evidence():
    ac = _answer_contract(citations=[{"evidence_id": "missing_citation", "source": "doc"}])
    env = _envelope(answer_contract=ac)
    report = validate_final_output_boundary(env, evidence_bus=_bus(), case_id="case-a",
                                            access_context={"tenant_id": "tenant-a", "user_id": "user-a"})
    assert "missing_evidence_ref" in _codes(report)
    assert "citation_validation_failed" in _codes(report)
    assert report["metrics"]["citation_validation_failure_rate"] > 0


def test_legacy_exception_cannot_expose_verified_facts():
    env = _envelope()
    gated = apply_final_output_evidence_gate(
        env,
        evidence_bus=_bus(),
        case_id="case-a",
        legacy_exception={"reason": "legacy_metadata_only"},
    )
    assert "legacy_high_confidence_fact" in _codes(gated["final_output_evidence_gate"])
    assert gated["answer_contract"]["facts"] == []
