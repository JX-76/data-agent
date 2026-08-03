# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))

from answer_contracts import build_answer_envelope, build_final_answer_contract
from agent_harness import AgentHarness


class _Facade(object):
    def ask(self, query):
        return {
            "status": "ok", "query": query, "trace_id": "trace-1",
            "task_id": "task-1", "session_id": "session-1",
            "metric": "gmv", "dimensions": [], "filters": {},
            "execution": {"used_db": True, "tool_calls": 1},
            "results": [{"gmv": 100}],
            "analysis": {"summary": u"GMV 为 100 元", "key_findings": [u"GMV 为 100 元"]},
            "diagnostics": {"evidence_cards": [{"id": "e-1", "tool": "sql", "result": {"gmv": 100}, "row_count": 1}]},
            "claim_audit": {"status": "ok"},
        }

    def get_trace(self, trace_id):
        return []


class AnswerContractsTest(unittest.TestCase):
    def test_envelope_is_typed_and_redacts_internal_details(self):
        envelope = build_answer_envelope({
            "status": "degraded", "query": "查询", "trace_id": "t", "task_id": "k", "session_id": "s",
            "message": "sqlite exception: SELECT * FROM dim_store",
            "diagnostics": {"evidence_cards": [{"tool": "sql", "params": {"token": "secret"}}]},
        })
        self.assertEqual("answer_envelope_v1", envelope["contract"])
        self.assertEqual("degraded", envelope["status"])
        self.assertNotIn("dim_store", envelope["user_answer"])
        self.assertEqual("[REDACTED]", envelope["evidence_refs"][0]["parameters_summary"]["token"])

    def test_numeric_claim_is_not_implicitly_evidence_backed(self):
        envelope = build_answer_envelope({
            "status": "ok", "analysis": {"key_findings": [u"GMV 增长 12%"]},
            "execution": {"used_db": True}, "results": [{"gmv": 1}],
        })
        self.assertTrue(envelope["claims"][0]["numeric"])
        self.assertEqual([], envelope["claims"][0]["evidence_ids"])
        self.assertTrue(envelope["evidence_refs"])

    def test_verified_execution_evidence_can_back_explicit_fact(self):
        contract = build_final_answer_contract({
            "status": "ok", "metric": "gmv", "facts": [{
                "text": u"GMV 为 100 元", "evidence_ids": ["exec-1"]}],
            "execution_envelope": {"status": "ok", "authority": "verified_execution",
                                   "evidence_id": "exec-1", "query_id": "q-1",
                                   "row_count": 1, "time_range": "last_7_days"},
        })
        self.assertEqual(["exec-1"], contract["facts"][0]["evidence_ids"])
        self.assertEqual(["exec-1"], contract["evidence_ids"])

    def test_unverified_execution_cannot_back_explicit_fact(self):
        contract = build_final_answer_contract({
            "status": "ok", "facts": [{"text": u"GMV 为 100 元", "evidence_ids": ["bad"]}],
            "execution_envelope": {"status": "error", "authority": "unverified", "evidence_id": "bad"},
        })
        self.assertEqual([], contract["facts"])
        self.assertEqual(u"GMV 为 100 元", contract["hypotheses"][0]["text"])

    def test_harness_exposes_safe_answer_report_projection(self):
        harness = AgentHarness(facade_factory=lambda: _Facade())
        evaluated = harness.run_case({"id": "c", "query": "GMV", "expected": {"status": "ok"}})
        self.assertEqual("answer_envelope_v1", evaluated["answer_envelope"]["contract"])
        self.assertIn("evidence_summary", evaluated)
        self.assertIn("claim_audit", evaluated)
        self.assertIn("quality_score", evaluated)


if __name__ == "__main__":
    unittest.main()
