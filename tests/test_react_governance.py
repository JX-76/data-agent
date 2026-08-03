# -*- coding: utf-8 -*-
"""Phase 21-A: ReAct front governance hardening.

These tests lock the invariant that every mainline request emits explicit
`risk_assessed` and `human_gate` trace events, and that a high-risk assessment
forces `pending_human_review` instead of silently executing (including the
ReAct branch).

Python 2.7 compatible: no f-strings, no type hints, no dataclasses.
"""
from __future__ import unicode_literals

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_facade import AgentFacade
from risk_policy import RiskAssessment


class _HighRiskPolicy(object):
    """Stub risk policy that always escalates, to exercise the human gate."""

    def assess(self, query, plan=None):
        return RiskAssessment(
            level="high",
            reasons=["forced_high_for_test"],
            requires_human_review=True,
            confidence=0.8,
        )


def _event_names(trace):
    names = []
    for ev in trace or []:
        if isinstance(ev, dict):
            names.append(ev.get("event") or ev.get("name") or ev.get("stage"))
    return names


class ReActGovernanceTest(unittest.TestCase):

    def test_normal_query_emits_risk_and_human_gate_events(self):
        facade = AgentFacade()
        facade.ask(u"最近7天GMV")
        events = _event_names(facade.get_trace())
        self.assertIn("risk_assessed", events)
        self.assertIn("human_gate", events)
        # governance and route still precede them
        self.assertIn("governance", events)
        self.assertIn("route", events)

    def test_high_risk_forces_human_review(self):
        # `pending_human_review` is an internal terminal status; the response
        # contract normalizes it to `need_clarification` while preserving the
        # human-review flags. Either way it must NOT be `ok`.
        facade = AgentFacade(risk_policy=_HighRiskPolicy())
        result = facade.ask(u"最近7天GMV")
        self.assertNotEqual(result.get("status"), "ok")
        self.assertIn(result.get("status"), ("need_clarification", "pending_human_review"))
        self.assertTrue(result.get("requires_human_review"))
        self.assertEqual(result.get("risk_level"), "high")
        # human_gate event must still be recorded before short-circuit
        events = _event_names(facade.get_trace())
        self.assertIn("risk_assessed", events)
        self.assertIn("human_gate", events)

    def test_high_risk_does_not_reach_execution(self):
        facade = AgentFacade(risk_policy=_HighRiskPolicy())
        result = facade.ask(u"最近7天GMV按渠道")
        # human-review short-circuit is terminal: no SQL should be executed
        self.assertNotEqual(result.get("status"), "ok")
        self.assertTrue(result.get("requires_human_review"))
        self.assertIn(result.get("sql"), (None, "", u""))


    def test_human_gate_event_carries_approval_status(self):
        facade = AgentFacade()
        facade.ask(u"最近7天GMV")
        trace = facade.get_trace()
        gate_events = [ev for ev in trace if isinstance(ev, dict)
                       and (ev.get("event") or ev.get("name")) == "human_gate"]
        self.assertTrue(gate_events)
        meta = gate_events[-1].get("metadata") or {}
        self.assertIn("approval_status", meta)


if __name__ == "__main__":
    unittest.main()
