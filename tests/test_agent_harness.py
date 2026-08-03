# -*- coding: utf-8 -*-
"""Unit tests for AgentHarness."""

from __future__ import unicode_literals

import codecs
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "src"))

from agent_harness import AgentHarness


class _FakeObserver(object):
    def __init__(self, events=None):
        self._events = events or []

    def summarize(self, trace_id=None):
        return {"trace_id": trace_id, "event_count": len(self._events)}


class _FakeFacade(object):
    def __init__(self, result=None, trace=None):
        self._result = result or {
            "status": "ok",
            "intent": "metric_query",
            "task_type": "descriptive",
            "metric": "gmv",
            "dimensions": [],
            "query": "mock query",
            "plan": {"task_type": "descriptive", "intent": "metric_query", "metric": "gmv", "dimensions": []},
            "sql": "SELECT sum(gmv) FROM orders",
            "analysis": {"type": "descriptive", "definition": {"task_type": "descriptive", "metric": "gmv", "dimensions": []}},
            "chart": {"type": "none"},
            "execution_mode": "plan_act",
            "results": [{"gmv": 100}],
            "results_summary": "gmv=100",
            "diagnostics": {},
            "report": "{}",
            "trace_id": "trace-1",
            "task_id": "task-1",
            "elapsed_ms": 10,
            "errors": [],
            "clarification": None,
            "blocked_reason": None,
            "session_id": "sess-1",
            "execution": {"used_db": True, "used_llm": False, "tool_calls": 0, "step_count": 1},
            "requires_human_review": False,
            "approval_status": "approved",
            "risk_level": "low",
            "review_checklist": [],
            "prompt_chain": ["router", "planner", "analyst", "report"],
            "prompt_specs": [
                {"prompt_id": "router", "prompt_version": "v1", "system_prompt": "router"},
                {"prompt_id": "planner", "prompt_version": "v1", "system_prompt": "planner"},
            ],
            "sandbox": {"subagent_type": "planner", "sandbox_backend": "docker"},
            "human_gate": {"status": "approved", "approval_status": "approved"},
            "fallback_reason": None,
        }
        self._trace = trace or [
            {"name": "governance"},
            {"name": "route"},
            {"name": "plan"},
            {"name": "complete"},
        ]
        self.observer = _FakeObserver(self._trace)

    def ask(self, query, use_llm=False):
        result = dict(self._result)
        result["query"] = query
        return result

    def get_trace(self, trace_id=None):
        return list(self._trace)


class AgentHarnessTest(unittest.TestCase):
    def test_load_cases_jsonl(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            with codecs.open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": "c1", "query": "最近7天GMV"}, ensure_ascii=False) + "\n")
                f.write("\n")
                f.write(json.dumps({"id": "c2", "query": "按渠道看GMV"}, ensure_ascii=False) + "\n")
            harness = AgentHarness(facade_factory=lambda: _FakeFacade())
            cases = harness.load_cases(path)
            self.assertEqual(2, len(cases))
            self.assertEqual(u"c1", cases[0]["id"])
            self.assertEqual(u"c2", cases[1]["id"])
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_evaluate_case_classifies_missing_contract_and_trace(self):
        harness = AgentHarness(facade_factory=lambda: _FakeFacade(trace=[{"name": "route"}]))
        case = {
            "id": "case-1",
            "query": "最近7天GMV",
            "expected": {
                "status": "ok",
                "intent": "metric_query",
                "task_type": "descriptive",
                "metric": "gmv",
                "dimensions": [],
                "contract_keys": ["status", "query", "intent", "plan", "sql", "results", "analysis", "diagnostics", "report", "trace_id"],
                "trace_events": ["governance", "route", "plan", "complete"],
            },
        }
        result = {
            "status": "ok",
            "query": "最近7天GMV",
            "intent": "metric_query",
            "task_type": "descriptive",
            "metric": "gmv",
            "dimensions": [],
            "plan": {"task_type": "descriptive", "intent": "metric_query", "metric": "gmv", "dimensions": []},
            "analysis": {"type": "descriptive", "definition": {"task_type": "descriptive", "metric": "gmv", "dimensions": []}},
            "results": [],
            "diagnostics": {},
            "report": "{}",
            "trace_id": "trace-1",
            "requires_human_review": False,
            "approval_status": "approved",
            "risk_level": "low",
            "review_checklist": [],
            "prompt_chain": [],
            "prompt_specs": [],
            "sandbox": {},
            "human_gate": {},
            "fallback_reason": None,
        }
        evaluated = harness.evaluate_case(case, result, trace=[{"name": "route"}], trace_summary={})
        self.assertFalse(evaluated["passed"])
        self.assertEqual("contract_error", evaluated["failure_type"])
        self.assertTrue(any("trace_missing_event" in err for err in evaluated["errors"]))

    def test_run_case_with_mock_facade(self):
        harness = AgentHarness(facade_factory=lambda: _FakeFacade())
        case = {
            "id": "ok-1",
            "query": "最近7天GMV",
            "expected": {
                "status": "ok",
                "intent": "metric_query",
                "task_type": "descriptive",
                "metric": "gmv",
                "dimensions": [],
                "trace_events": ["governance", "route", "plan", "complete"],
            },
        }
        evaluated = harness.run_case(case)
        self.assertTrue(evaluated["passed"])
        self.assertEqual("ok", evaluated["result"]["status"])
        self.assertEqual("metric_query", evaluated["result"]["intent"])
        self.assertIn("prompt_chain", evaluated["result"])
        self.assertIn("sandbox", evaluated["result"])
        self.assertIn("human_gate", evaluated["result"])

    def test_summarize_metrics(self):
        harness = AgentHarness(facade_factory=lambda: _FakeFacade())
        results = [
            {
                "passed": True,
                "failure_type": None,
                "expected": {"status": "ok", "intent": "metric_query", "task_type": "descriptive", "metric": "gmv", "dimensions": [], "trace_events": ["governance"]},
                "result": {"status": "ok", "intent": "metric_query", "task_type": "descriptive", "metric": "gmv", "dimensions": [], "plan": {}, "analysis": {}, "results": [], "diagnostics": {}, "report": "{}", "trace_id": "trace-1", "requires_human_review": False, "approval_status": "approved", "risk_level": "low", "review_checklist": [], "prompt_chain": [], "prompt_specs": [], "sandbox": {}, "human_gate": {}},
                "trace": [{"name": "governance"}],
            },
            {
                "passed": False,
                "failure_type": "intent_mismatch",
                "expected": {"status": "ok", "intent": "breakdown", "task_type": "descriptive", "metric": "gmv", "dimensions": ["channel"]},
                "result": {"status": "ok", "intent": "metric_query", "task_type": "descriptive", "metric": "gmv", "dimensions": [], "plan": {}, "analysis": {}, "results": [], "diagnostics": {}, "report": "{}", "trace_id": "trace-2", "requires_human_review": False, "approval_status": "approved", "risk_level": "low", "review_checklist": [], "prompt_chain": [], "prompt_specs": [], "sandbox": {}, "human_gate": {}},
                "trace": [],
            },
        ]
        metrics = harness.summarize(results)
        self.assertEqual(2, metrics["total"])
        self.assertEqual(1, metrics["passed"])
        self.assertEqual(1, metrics["failed"])
        self.assertIn("intent_mismatch", metrics["failure_breakdown"])
        self.assertAlmostEqual(0.5, metrics["pass_rate"])


if __name__ == "__main__":
    unittest.main()
