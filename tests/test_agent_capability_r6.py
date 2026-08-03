# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent_capability import capability_catalog, business_coverage
from agent_monitoring import AgentMonitoring
from observability import ObservationRecorder
from human_review_state import HumanReviewStateMachine
from feedback_loop import FeedbackLoop


class AgentCapabilityR6Tests(unittest.TestCase):
    def test_business_coverage_declares_ecommerce_boundary(self):
        output = business_coverage({"task_type": "comparison", "intent": "comparison"})
        self.assertEqual("business_coverage_v1", output["contract"])
        self.assertEqual("partial", output["coverage_level"])
        self.assertTrue(output["scenarios"])
        self.assertEqual("agent_capability_v1", capability_catalog()["contract"])

    def test_monitoring_locates_error_stage_and_latency_dashboard(self):
        observer = ObservationRecorder()
        observer.record("route", trace_id="t1", task_id="task-1", status="ok")
        observer.record("execution", trace_id="t1", task_id="task-1", status="error",
                        stage="sql_execute", failure_type="timeout")
        monitor = AgentMonitoring(observer)
        card = monitor.record_completed("t1", {"status": "error", "task_id": "task-1", "intent": "comparison"})
        self.assertEqual("sql_execute", card["failure_stage"])
        self.assertTrue(card["timeout"])
        dashboard = monitor.dashboard()
        self.assertEqual(1, dashboard["timeout_count"])
        self.assertEqual(1, dashboard["failure_stages"]["sql_execute"])

    def test_human_review_approval_and_rejection_are_explicit(self):
        state = HumanReviewStateMachine()
        state.begin("s1", "高风险查询", {"task_id": "p1", "status": "pending_human_review"}, checklist=["只读"])
        invalid = state.decide("s1", "maybe")
        self.assertEqual("pending_human_review", invalid["status"])
        approved = state.decide("s1", "approve", reviewer_id="risk-owner")
        self.assertEqual("ok", approved["status"])
        self.assertEqual("approved", approved["plan"]["approval_status"])
        state.begin("s2", "高风险查询", {"task_id": "p2"})
        rejected = state.decide("s2", "reject", reviewer_id="risk-owner")
        self.assertEqual("blocked", rejected["status"])

    def test_low_score_creates_human_governed_feedback_proposal(self):
        loop = FeedbackLoop(pass_threshold=0.8)
        proposal = loop.ingest({"pass_count": 1, "total_count": 3,
                                "scores": [{"name": "intent", "pass": False},
                                           {"name": "sql_structure", "pass": False}]}, query="按店铺看GMV")
        self.assertTrue(proposal["created"])
        self.assertIn("semantic_or_routing_rules", proposal["recommended_targets"])
        approved = loop.decide(proposal["proposal_id"], "approve", reviewer_id="analyst")
        self.assertEqual("approved_backlog", approved["status"])


if __name__ == "__main__":
    unittest.main()
