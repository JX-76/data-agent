# -*- coding: utf-8 -*-
"""R11-B cross-entry terminal status contract tests.

Locks the invariant that facade/graph-style entrypoints expose the same
normalized terminal status fields. Python 2.7 compatible.
"""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent_facade import AgentFacade
from contracts import normalize_result, normalize_status, validate_response_contract
from risk_policy import RiskAssessment


class _HighRiskPolicy(object):
    def assess(self, query, plan=None):
        return RiskAssessment(
            level="high",
            reasons=["forced_high_for_contract_test"],
            requires_human_review=True,
            confidence=0.9,
        )


def _assert_contract(result):
    data = result.to_dict() if hasattr(result, "to_dict") else result
    ok, missing = validate_response_contract(data)
    assert ok is True
    assert missing == []
    assert data["diagnostics"]["terminal"]["status"] == data["status"]
    return data


def test_legacy_terminal_statuses_are_mapped_without_losing_reason():
    samples = [
        ({"status": "rejected", "reason": "human_rejected"}, "blocked", "human_rejected"),
        ({"status": "failed", "reason": "dependency_failed"}, "error", "dependency_failed"),
        ({"status": "insufficient_data", "reason": "not_enough_rows"}, "error", "not_enough_rows"),
    ]

    for raw, expected_status, expected_reason in samples:
        result = _assert_contract(normalize_result(raw, query="legacy terminal"))
        assert result["status"] == expected_status
        assert result["diagnostics"]["terminal"]["reason"] == expected_reason


def test_normalize_status_maps_rejected_to_blocked():
    assert normalize_status("rejected") == "blocked"
    assert normalize_status("human_rejected") == "blocked"
    assert normalize_status("failed") == "error"
    assert normalize_status("insufficient_data") == "error"


def test_facade_blocked_terminal_contract_has_reason():
    result = _assert_contract(AgentFacade().ask("drop table orders"))
    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "dangerous query"
    assert result["diagnostics"]["terminal"]["reason"] == "dangerous query"


def test_facade_human_review_terminal_contract_is_not_clarification():
    facade = AgentFacade(risk_policy=_HighRiskPolicy())
    result = _assert_contract(facade.ask("最近7天GMV"))
    assert result["status"] == "pending_human_review"
    assert result["requires_human_review"] is True
    assert result["approval_status"] == "pending"
    assert result["diagnostics"]["terminal"]["reason"] == "pending"


def test_graph_clarification_terminal_contract_carries_clarification():
    from graph_agent import run_graph

    result = _assert_contract(run_graph("GMV 口径是什么？", use_db=True, use_llm=False))
    assert result["status"] == "need_clarification"
    assert isinstance(result.get("clarification"), dict)
    assert result["clarification"].get("question")
    assert result["diagnostics"]["terminal"].get("reason")


def test_graph_resume_error_is_normalized_contract():
    from graph_agent import resume_graph

    result = _assert_contract(resume_graph({"query": "GMV 口径是什么？"}, executor=None, user_choice=None))
    assert result["status"] == "error"
    assert result["diagnostics"]["terminal"]["reason"] == "No user choice provided"
