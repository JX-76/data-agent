# -*- coding: utf-8 -*-
"""Phase 21-C: AgentFacade react-branch governance integration tests.

Verifies that when a plan is routed to execution_mode == "react" and the
single-plan execution path runs, the facade:
  1. routes the observation through the ReActObservationGovernor
  2. emits a governed "react_observation" trace event
  3. records the governed outcome in ctx["react_observations"]
  4. marks diagnostics.react_runtime as "governed_plan_act"

These tests must not depend on any external service and stay Python 2.7
compatible (no f-strings, no type hints, no dataclasses).
"""

import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from agent_facade import AgentFacade


class _ActionsGovernor(object):
    """Deterministic governor for end-to-end bounded-loop integration tests."""

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def govern(self, task_anchor, step_index, tool_name, observation,
               trace_id=None, task_id=None, session_id=None):
        action = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        return {
            "action": action,
            "evidence": {"summary": "test governed observation"},
            "injectable": {"evidence_id": "allowed-ref"} if action == "allow" else None,
            "decision": {"reason": "forced_%s" % action, "conflicts": []},
        }


# Query that routes to anomaly/react execution (see harness eco_diagnosis_001).
_REACT_QUERY = u"\u4e0a\u5468GMV\u4e3a\u4ec0\u4e48\u4e0b\u964d"


class ReactFacadeIntegrationTest(unittest.TestCase):

    def setUp(self):
        self.facade = AgentFacade()

    def _plan_execution_mode(self, ctx):
        plan = ctx.get("plan")
        if isinstance(plan, dict):
            return plan.get("execution_mode")
        return getattr(plan, "execution_mode", None)

    def _plan_diagnostics(self, ctx):
        plan = ctx.get("plan")
        if isinstance(plan, dict):
            return plan.get("diagnostics") or {}
        return getattr(plan, "diagnostics", {}) or {}

    def _trace_names(self):
        return [e.get("name") for e in self.facade.get_trace()]

    def test_react_execution_emits_governed_observation(self):
        result = self.facade.ask(_REACT_QUERY)
        self.assertEqual(result.get("status"), "ok")
        trace_events = self._trace_names()

        # react_selected always emitted; react_observation only when the
        # governed single-plan execution path runs.
        self.assertIn("react_selected", trace_events)
        self.assertIn("react_observation", trace_events)

    def test_govern_react_observation_populates_ctx(self):
        # Drive the stages directly so we can inspect ctx (ask() returns a
        # normalized result that drops internal keys).
        self.facade._trace_id = "t-react"
        self.facade._task_id = "task-react"
        ctx = {"query": _REACT_QUERY, "use_llm": False}
        ctx = self.facade._stage_governance(ctx)
        ctx = self.facade._stage_planning(ctx)
        self.assertEqual(self._plan_execution_mode(ctx), "react")
        ctx = self.facade._stage_decompose(ctx)
        ctx = self.facade._stage_execution(ctx)

        observations = ctx.get("react_observations")
        self.assertTrue(observations, "expected at least one governed observation")
        outcome = observations[0]
        self.assertIn("action", outcome)
        self.assertIn(outcome.get("action"), ("allow", "quarantine", "pivot"))
        self.assertGreaterEqual(ctx.get("react_step_count", 0), 1)
        self.assertLessEqual(ctx.get("react_step_count", 0), 2)

    def test_diagnostics_marked_governed(self):
        self.facade._trace_id = "t-react-2"
        self.facade._task_id = "task-react-2"
        ctx = {"query": _REACT_QUERY, "use_llm": False}
        ctx = self.facade._stage_governance(ctx)
        ctx = self.facade._stage_planning(ctx)
        ctx = self.facade._stage_decompose(ctx)
        ctx = self.facade._stage_execution(ctx)

        diagnostics = self._plan_diagnostics(ctx)
        self.assertEqual(diagnostics.get("react_runtime"), "governed_plan_act")
        self.assertTrue(diagnostics.get("react_selected"))

    def test_react_runtime_keeps_single_action_on_allow(self):
        self.facade._trace_id = "t-react-loop"
        self.facade._task_id = "task-react-loop"
        ctx = {"query": _REACT_QUERY, "use_llm": False}
        ctx = self.facade._stage_governance(ctx)
        ctx = self.facade._stage_planning(ctx)
        ctx = self.facade._stage_decompose(ctx)
        ctx = self.facade._stage_execution(ctx)
        self.assertEqual(1, ctx.get("react_step_count"))
        self.assertEqual("allow", ctx.get("react_terminal_action"))
        self.assertEqual([], ctx.get("react_replans"))


    def test_react_quarantine_stops_after_one_action_and_exports_control_metadata(self):
        self.facade.react_governor = _ActionsGovernor(["quarantine"])
        result = self.facade.ask(_REACT_QUERY)
        loop = result.get("diagnostics", {}).get("react_loop", {})
        self.assertEqual("quarantine", loop.get("terminal_action"))
        self.assertEqual(1, loop.get("steps"))
        self.assertEqual(0, loop.get("replan_count"))
        self.assertNotIn("react_replan", self._trace_names())

    def test_react_pivot_replans_once_then_allows(self):
        self.facade.react_governor = _ActionsGovernor(["pivot", "allow"])
        result = self.facade.ask(_REACT_QUERY)
        loop = result.get("diagnostics", {}).get("react_loop", {})
        self.assertEqual("allow", loop.get("terminal_action"))
        self.assertEqual(2, loop.get("steps"))
        self.assertEqual(1, loop.get("replan_count"))
        self.assertIn("react_replan", self._trace_names())

    def test_non_react_query_has_no_react_observation(self):
        result = self.facade.ask(u"\u6700\u8fd17\u5929GMV")
        self.assertEqual(result.get("status"), "ok")
        trace_events = self._trace_names()
        self.assertNotIn("react_observation", trace_events)


    def test_govern_react_observation_never_raises(self):
        # Governor failures must not break the loop; a malformed exec_result
        # (non-dict) should simply be ignored.
        self.facade._trace_id = "t-react-3"
        self.facade._task_id = "task-react-3"
        ctx = {}
        # Should silently no-op, not raise.
        self.facade._govern_react_observation(ctx, None, {}, step_index=0)
        self.assertNotIn("react_observations", ctx)


if __name__ == "__main__":
    unittest.main(verbosity=2)
