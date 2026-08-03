# -*- coding: utf-8 -*-
"""Phase 21-B: ReAct observation governance tests.

Locks the invariant that every ReAct observation is compacted and gated:
allowed observations yield an injectable OBSERVATION_REF with zero raw rows,
anchor conflicts are quarantined (never injectable), and pivots signal replan.

Python 2.7 compatible.
"""
from __future__ import unicode_literals

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from react_observation import (ReActObservationGovernor, ACTION_ALLOW,
                               ACTION_QUARANTINE, ACTION_PIVOT)
from task_anchor import TaskAnchor, AnchorDecision, DECISION_PIVOT


def _anchor(metric="gmv", dimensions=None, task_type="descriptive"):
    return TaskAnchor(task_id="t1", intent="metric_query", task_type=task_type,
                      metric=metric, dimensions=dimensions or [],
                      time_range="last_7d", confidence=0.9, query=u"最近7天GMV")


def _observation(metric="gmv", status="ok", dataid="d123", rows=None):
    return {
        "status": status,
        "metric": metric,
        "dataid": dataid,
        "results": rows if rows is not None else [{"gmv": 1000, "dt": "2026-07-01"}],
    }


class _PivotPolicy(object):
    """Stub policy that always signals a pivot."""

    def apply(self, anchor, card):
        decision = AnchorDecision(DECISION_PIVOT, "forced_pivot", 0.5, ["forced"])
        return card, decision

    def compact_context(self, cards):
        return []


class ReActObservationTest(unittest.TestCase):

    def test_ok_observation_allows_and_emits_ref(self):
        gov = ReActObservationGovernor()
        out = gov.govern(_anchor(), 0, "sql_query", _observation())
        self.assertEqual(out["action"], ACTION_ALLOW)
        self.assertIsNotNone(out["injectable"])
        self.assertIn("evidence_id", out["injectable"])
        self.assertEqual(out["injectable"]["row_count"], 1)
        self.assertIn("gmv", out["injectable"]["columns"])

    def test_metric_mismatch_quarantined(self):
        gov = ReActObservationGovernor()
        # anchor targets gmv; observation returns a different metric
        obs = _observation(metric="dau", rows=[{"dau": 50, "dt": "2026-07-01"}])
        out = gov.govern(_anchor(metric="gmv"), 1, "sql_query", obs)
        self.assertEqual(out["action"], ACTION_QUARANTINE)
        self.assertIsNone(out["injectable"])
        self.assertIn("metric_mismatch", out["decision"]["conflicts"])

    def test_no_raw_rows_in_injectable(self):
        gov = ReActObservationGovernor()
        big_rows = [{"gmv": i, "dt": "2026-07-%02d" % (i + 1)} for i in range(500)]
        out = gov.govern(_anchor(), 0, "sql_query", _observation(rows=big_rows))
        ref = out["injectable"]
        # No bulk payload keys leak into the injectable reference
        for forbidden in ("results", "rows", "data", "payload", "records"):
            self.assertNotIn(forbidden, ref)
        # Row count is a scalar, not the rows themselves
        self.assertEqual(ref["row_count"], 500)

    def test_pivot_signals_replan(self):
        gov = ReActObservationGovernor(memory_policy=_PivotPolicy())
        out = gov.govern(_anchor(), 2, "sql_query", _observation())
        self.assertEqual(out["action"], ACTION_PIVOT)
        self.assertIsNone(out["injectable"])

    def test_anchorless_allows_but_compacts(self):
        gov = ReActObservationGovernor()
        out = gov.govern(None, 0, "sql_query", _observation())
        self.assertEqual(out["action"], ACTION_ALLOW)
        self.assertIsNotNone(out["injectable"])
        self.assertIsNone(out["decision"])

    def test_failed_observation_is_unverified(self):
        gov = ReActObservationGovernor()
        obs = _observation(status="error", dataid=None, rows=[])
        out = gov.govern(_anchor(), 0, "sql_query", obs)
        # error with no dataid -> unverified authority, still compacted
        self.assertEqual(out["evidence"]["authority"], "unverified")


if __name__ == "__main__":
    unittest.main()
