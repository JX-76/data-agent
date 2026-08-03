# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import release_api
from ecommerce_graphs import GRAPH_METRIC_QUERY
from release_api import ask_release, ecommerce_graph_release
from report_generator import build_product_report
from answer_contracts import build_final_answer_contract
from final_output_evidence_gate import apply_final_output_evidence_gate


class _FactWithoutEvidenceFacade(object):
    def ask(self, query, use_llm=False, access_context=None, analysis_method=None):
        return {
            "status": "ok",
            "summary": "GMV is 100.",
            "final_answer": {
                "contract": "final_answer_contract_v2",
                "status": "ok",
                "answer_type": "analysis",
                "answer": "GMV is 100.",
                "facts": [{"text": "GMV is 100.", "evidence_ids": ["ev_missing"]}],
                "hypotheses": [],
                "citations": [],
                "limitations": [],
                "next_actions": [],
                "provenance": {"evidence_id": "ev_missing", "metric": "gmv", "time_range": "last_7_days"},
                "trace_id": "trace_missing",
                "task_id": "task_missing",
                "evidence_ids": ["ev_missing"],
            },
            "tenant_id": (access_context or {}).get("tenant_id"),
            "trace_id": "trace_missing",
        }


class _TrustedLegacyFacade(object):
    def ask(self, query, use_llm=False, access_context=None, analysis_method=None):
        return {"status": "ok", "summary": "done", "results": [{"gmv": 100}], "tenant_id": (access_context or {}).get("tenant_id")}


def test_release_api_boundary_demotes_facade_fact_without_evidence(monkeypatch):
    monkeypatch.setattr(release_api, "_facade", lambda session_id: (session_id or "sid", _FactWithoutEvidenceFacade()))
    env = ask_release("最近7天GMV", session_id="p0_missing_ev", access_context={"user_id": "u1", "tenant_id": "t1"})
    assert env["contract"] == "release_v1_envelope"
    assert env["final_output_evidence_gate"]["allowed"] is False
    assert env["answer_contract"]["facts"] == []
    assert env["answer_contract"]["hypotheses"]
    assert "final_output_evidence_gate" in env["provenance"]


def test_ecommerce_graph_release_passes_unified_boundary_with_verified_server_evidence():
    env = ecommerce_graph_release({
        "graph_type": GRAPH_METRIC_QUERY,
        "metric": "gmv",
        "time_range": "last_7_days",
        "rows": [{"gmv": 100}],
        "session_id": "p0_graph_ok",
    }, access_context={"user_id": "graph-user", "tenant_id": "graph-tenant", "role": "analyst"})
    assert env["status"] == "ok"
    assert env["final_output_evidence_gate"]["contract"] == "final_output_evidence_validation_v1"
    assert env["final_output_evidence_gate"]["allowed"] is True
    assert env["answer_contract"]["facts"]


def test_report_chart_legacy_path_can_be_wrapped_and_cannot_bypass_gate():
    report = build_product_report({
        "status": "ok",
        "task_type": "descriptive",
        "metric": "gmv",
        "analysis": {"summary": "GMV is 100", "key_findings": ["GMV is 100"]},
        "chart": {"type": "bar"},
        "facts": [{"text": "GMV is 100", "evidence_ids": ["ev_missing"]}],
    }).to_dict()
    result = {
        "status": "ok",
        "summary": report["summary"],
        "report": report,
        "chart": report["chart"],
        "facts": [{"text": "GMV is 100", "evidence_ids": ["ev_missing"]}],
        "trace_id": "trace_report",
    }
    envelope = {
        "contract": "report_final_output_v1",
        "status": "ok",
        "session_id": "report-case",
        "audit_id": "audit-report",
        "answer": {"summary": report["summary"], "table": [], "chart": report["chart"], "caveats": [], "next_steps": []},
        "provenance": {},
        "raw": result,
        "answer_contract": {
            "contract": "final_answer_contract_v2",
            "status": "ok",
            "answer_type": "report",
            "answer": report["summary"],
            "facts": [{"text": "GMV is 100", "evidence_ids": ["ev_missing"]}],
            "hypotheses": [],
            "citations": [],
            "limitations": [],
            "next_actions": [],
            "provenance": {"evidence_id": "ev_missing", "metric": "gmv", "time_range": "last_7_days"},
            "trace_id": "trace_report",
            "task_id": "task_report",
            "evidence_ids": ["ev_missing"],
        },
        "elapsed_ms": 1,
    }
    gated = apply_final_output_evidence_gate(envelope, evidence_bus=None, case_id="report-case",
                                             require_evidence_bus=True, entrypoint="report_generator")
    assert gated["final_output_evidence_gate"]["allowed"] is False
    assert gated["answer_contract"]["facts"] == []
    assert gated["answer_contract"]["answer_type"] == "evidence_limited"
