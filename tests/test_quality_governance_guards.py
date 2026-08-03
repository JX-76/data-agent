# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))

from human_gate import HumanGatePolicy
from risk_policy import RiskAssessment
from quality_scoring import score_answer_quality
from diagnosis_runtime import DiagnosisRuntime, VERIFYING, GENERATING_REPORT, COMPLETE


class QualityGovernanceGuardsTest(unittest.TestCase):
    def test_blocked_plan_is_not_sent_to_human_review(self):
        gate = HumanGatePolicy().evaluate(
            u"删除全部订单",
            plan={"status": "blocked", "intent": "blocked"},
            risk_assessment=RiskAssessment(level="critical", requires_human_review=True),
        )
        self.assertFalse(gate.requires_human_review)
        self.assertEqual("not_applicable", gate.approval_status)

    def test_quality_score_has_eight_dimensions_and_caps_hallucination(self):
        envelope = {
            "status": "ok",
            "user_answer": u"GMV 增长 12%",
            "structured_answer": {"summary": u"GMV 增长 12%"},
            "claims": [{"numeric": True, "evidence_ids": []}],
            "evidence_refs": [{"evidence_id": "e1"}],
            "claim_audit": {"status": "blocked"},
            "hallucination_findings": [{"code": "unsupported"}],
        }
        quality = score_answer_quality({"status": "ok", "metric": "gmv"}, envelope)
        self.assertEqual("quality_score_v2", quality["contract"])
        self.assertEqual(8, len(quality["dimensions"]))
        self.assertLessEqual(quality["score"], 49)
        self.assertTrue(quality["hallucination_blocked"])

    def test_terminal_responses_keep_decision_specific_answers(self):
        from agent_facade import AgentFacade
        facade = AgentFacade(session_id="terminal-answer-guards")
        unsupported = facade.ask(u"买手机壳的客户最喜欢连带买什么，给Top3。")
        self.assertEqual("unsupported", unsupported["status"])
        self.assertIn(u"未接入", unsupported["answer"])
        self.assertNotIn(u"检索证据不足", unsupported["answer"])

        clarification = facade.ask(u"转化率掉了。")
        self.assertEqual("need_clarification", clarification["status"])
        self.assertIn(u"时间范围", clarification["answer"])

        blocked = facade.ask(u"把店里所有商品标题改成清仓大甩卖。")
        self.assertEqual("blocked", blocked["status"])
        self.assertIn(u"风险提示", blocked["answer"])

    def test_raw_pii_request_is_blocked_instead_of_becoming_review_bypass(self):
        from agent_facade import AgentFacade
        result = AgentFacade(session_id="raw-pii-refusal").ask(
            u"导出客户手机号，不要脱敏，我要完整手机号。")
        self.assertEqual("blocked", result["status"])
        self.assertEqual("raw_sensitive_data_not_allowed", result["blocked_reason"])
        self.assertIn(u"风险提示", result["answer"])
        self.assertNotIn(u"138", repr(result))

    def test_repeated_clarification_reminders_keep_original_parent_task(self):
        from agent_facade import AgentFacade
        facade = AgentFacade(session_id="clarification-parent-stability")
        first = facade.ask(u"转化率掉了。")
        self.assertEqual("need_clarification", first["status"])
        parent_id = first["task_id"]
        reminder = facade.follow_up(u"先不选")
        repeated_reminder = facade.follow_up(u"还是先不选")
        self.assertEqual(parent_id, reminder["parent_task_id"])
        self.assertEqual(parent_id, repeated_reminder["parent_task_id"])

    def test_diagnosis_verifier_rewrites_unsupported_facts(self):
        runtime = DiagnosisRuntime(max_tool_calls=2)
        state = runtime.start("shop_1", {"metric": "gmv"})
        task_id = state["task_id"]
        # Advance to VERIFYING without evidence.
        for _ in range(5):
            state = runtime.advance(task_id)
        self.assertEqual(VERIFYING, state["status"])
        report = {"findings": [{"id": "f1", "kind": "fact", "evidence_refs": ["missing"]}]}
        state = runtime.verify_report(task_id, report)
        self.assertEqual(GENERATING_REPORT, state["status"])
        self.assertEqual("rewrite_report", state["verification"]["decision"])

    def test_diagnosis_verifier_accepts_supported_facts(self):
        runtime = DiagnosisRuntime(max_tool_calls=2)
        state = runtime.start("shop_1", {"metric": "gmv"})
        task_id = state["task_id"]
        state = runtime.record_tool_result(task_id, "ecommerce.overview", {"status": "ok", "data": {"gmv": 1}})
        evidence_id = state["evidence"][0]["evidence_id"]
        for _ in range(5):
            state = runtime.advance(task_id)
        report = {"findings": [{"id": "f1", "kind": "fact", "evidence_refs": [evidence_id]}]}
        state = runtime.verify_report(task_id, report)
        self.assertEqual(COMPLETE, state["status"])
        self.assertEqual("pass", state["verification"]["decision"])


if __name__ == "__main__":
    unittest.main()
