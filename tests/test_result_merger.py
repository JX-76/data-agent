# -*- coding: utf-8 -*-
"""Tests for ResultMerger and MergeResult.

Python 2.7 compatible.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from result_merger import ResultMerger, MergeResult


class TestMergeResult(unittest.TestCase):

    def test_empty(self):
        r = MergeResult()
        self.assertEqual(r.merged, {})
        self.assertEqual(r.strategy, "concat")
        self.assertEqual(r.sub_results, [])
        self.assertEqual(r.diagnostics, {})

    def test_to_dict(self):
        r = MergeResult(
            merged={"status": "ok"},
            strategy="concat",
            sub_results=[{"metric": "gmv"}],
            diagnostics={"count": 1},
        )
        d = r.to_dict()
        self.assertEqual(d["strategy"], "concat")
        self.assertEqual(d["merged"]["status"], "ok")
        self.assertEqual(d["diagnostics"]["count"], 1)


class TestResultMerger(unittest.TestCase):

    def setUp(self):
        self.merger = ResultMerger(strategy="auto")

    def test_no_results(self):
        result = self.merger.merge([])
        self.assertEqual(result.strategy, "noop")
        self.assertEqual(result.merged, {})

    def test_single_result(self):
        result = self.merger.merge([{"status": "ok", "metric": "gmv"}])
        self.assertEqual(result.strategy, "noop")
        self.assertEqual(result.merged["status"], "ok")

    def test_concat_two_results(self):
        results = [
            {"status": "ok", "metric": "gmv", "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "ok", "metric": "order_count", "results": [{"orders": 50}], "task_id": "t2"},
        ]
        result = self.merger.merge(results)
        self.assertEqual(result.strategy, "concat")
        self.assertEqual(len(result.merged["results"]), 2)
        self.assertTrue(result.merged["multi_result"])
        self.assertEqual(result.merged["sub_result_count"], 2)

    def test_concat_with_errors(self):
        results = [
            {"status": "ok", "metric": "gmv", "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "error", "metric": "refund_rate", "errors": ["db_error"], "task_id": "t2"},
        ]
        result = self.merger.merge(results)
        self.assertEqual(result.merged["status"], "error")
        self.assertIn("db_error", str(result.merged["errors"]))

    def test_concat_with_sql(self):
        results = [
            {"status": "ok", "metric": "gmv", "sql": "SELECT gmv FROM ...", "task_id": "t1"},
            {"status": "ok", "metric": "order_count", "sql": "SELECT orders FROM ...", "task_id": "t2"},
        ]
        result = self.merger.merge(results)
        # Multiple SQLs should be a list
        self.assertIsInstance(result.merged["sql"], list)
        self.assertEqual(len(result.merged["sql"]), 2)

    def test_group_strategy(self):
        results = [
            {"status": "ok", "metric": "gmv", "dimensions": ["channel"], "results": [{"channel": "online", "gmv": 100}], "task_id": "t1"},
            {"status": "ok", "metric": "gmv", "dimensions": ["category"], "results": [{"category": "electronics", "gmv": 80}], "task_id": "t2"},
        ]
        original_plan = {"decompose_reason": "multi_dimension_split"}
        result = self.merger.merge(results, original_plan=original_plan)
        self.assertEqual(result.strategy, "group")
        self.assertIn("grouped_results", result.merged)
        self.assertEqual(len(result.merged["grouped_results"]), 2)

    def test_nest_strategy(self):
        results = [
            {"status": "ok", "metric": "gmv", "intent": "metric_query", "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "ok", "metric": "gmv", "intent": "breakdown", "results": [{"channel": "online", "gmv": 60}], "task_id": "t2"},
            {"status": "ok", "metric": "order_count", "intent": "breakdown", "results": [{"channel": "online", "orders": 30}], "task_id": "t3"},
        ]
        original_plan = {"decompose_reason": "overview_expansion"}
        result = self.merger.merge(results, original_plan=original_plan)
        self.assertEqual(result.strategy, "nest")
        self.assertIn("overview", result.merged)
        self.assertIn("details", result.merged)
        self.assertEqual(len(result.merged["details"]), 2)

    def test_auto_select_concat(self):
        results = [
            {"status": "ok", "metric": "gmv", "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "ok", "metric": "order_count", "results": [{"orders": 50}], "task_id": "t2"},
        ]
        original_plan = {"decompose_reason": "multi_metric_split"}
        result = self.merger.merge(results, original_plan=original_plan)
        self.assertEqual(result.strategy, "concat")

    def test_auto_select_nest(self):
        results = [
            {"status": "ok", "metric": "gmv", "intent": "metric_query", "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "ok", "metric": "gmv", "intent": "breakdown", "results": [{"channel": "online", "gmv": 60}], "task_id": "t2"},
        ]
        original_plan = {"decompose_reason": "overview_expansion"}
        result = self.merger.merge(results, original_plan=original_plan)
        self.assertEqual(result.strategy, "nest")

    def test_auto_select_group(self):
        results = [
            {"status": "ok", "metric": "gmv", "dimensions": ["channel"], "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "ok", "metric": "gmv", "dimensions": ["category"], "results": [{"gmv": 80}], "task_id": "t2"},
        ]
        original_plan = {"decompose_reason": "multi_dimension_split"}
        result = self.merger.merge(results, original_plan=original_plan)
        self.assertEqual(result.strategy, "group")

    def test_force_strategy(self):
        results = [
            {"status": "ok", "metric": "gmv", "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "ok", "metric": "order_count", "results": [{"orders": 50}], "task_id": "t2"},
        ]
        merger = ResultMerger(strategy="concat")
        result = merger.merge(results)
        self.assertEqual(result.strategy, "concat")

    def test_nest_overview_status_used(self):
        results = [
            {"status": "ok", "metric": "gmv", "intent": "metric_query", "results": [{"gmv": 100}], "task_id": "t1"},
            {"status": "error", "metric": "gmv", "intent": "breakdown", "errors": ["failed"], "task_id": "t2"},
        ]
        original_plan = {"decompose_reason": "overview_expansion"}
        result = self.merger.merge(results, original_plan=original_plan)
        self.assertEqual(result.merged["status"], "ok")
        self.assertEqual(result.merged["results"], [{"gmv": 100}])


if __name__ == "__main__":
    unittest.main()
