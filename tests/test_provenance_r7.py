# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from clarification_state import ClarificationStateMachine, JsonFileClarificationStore
from provenance import build_provenance
from agent_facade import AgentFacade


class ProvenanceR7Tests(unittest.TestCase):
    def test_provenance_is_safe_and_versioned(self):
        plan = {"plan_version": "v2", "metric": "gmv", "dimensions": ["channel"], "model": "order_detail"}
        result = {"status": "ok", "sql": "select * from orders", "results": [{"gmv": 1}],
                  "diagnostics": {"sql_preflight": {"valid": True}}, "execution": {"used_db": True}}
        out = build_provenance(plan, result)
        self.assertEqual("provenance_v1", out["contract"])
        self.assertEqual("gmv", out["semantic"]["metric"])
        self.assertTrue(out["semantic"]["version"])
        self.assertEqual(1, out["execution"]["row_count"])
        self.assertNotIn("select *", str(out))

    def test_clarification_store_survives_new_state_machine(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        try:
            store = JsonFileClarificationStore(path)
            state = ClarificationStateMachine(store=store, ttl_seconds=60, now=lambda: 100.0)
            plan = {"status": "need_clarification", "intent": "ambiguous", "metric": "gmv",
                    "clarification": {"question": "怎么拆？", "options": [{"id": "breakdown"}, {"id": "metric_query"}]}}
            pending = state.begin("s1", "看GMV", plan, task_id="t1")
            self.assertTrue(pending["pending"])
            state2 = ClarificationStateMachine(store=JsonFileClarificationStore(path), ttl_seconds=60, now=lambda: 101.0)
            resolved = state2.resolve("s1", "breakdown")
            self.assertEqual("ok", resolved["status"])
            self.assertEqual("clarification_resume_v1", resolved["plan"]["resume_payload"]["contract"])
            self.assertFalse(state2.has_pending("s1"))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_facade_exports_provenance_contract(self):
        facade = AgentFacade()
        result = facade.ask("最近7天GMV")
        self.assertIn("provenance", result)
        self.assertEqual("provenance_v1", result["provenance"]["contract"])
        self.assertIn("credibility", result)


if __name__ == "__main__":
    unittest.main()
