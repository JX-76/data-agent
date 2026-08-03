# -*- coding: utf-8 -*-
"""Tests for TaskDecomposer and RuleDecomposer.

Python 2.7 compatible.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from task_decomposer import TaskDecomposer, RuleDecomposer, DecompositionResult
from schemas import AnalysisPlan


class TestDecompositionResult(unittest.TestCase):

    def test_empty(self):
        r = DecompositionResult()
        self.assertEqual(r.sub_plans, [])
        self.assertEqual(r.strategy, "no_split")
        self.assertEqual(r.reason, "")
        self.assertEqual(r.diagnostics, {})

    def test_to_dict(self):
        r = DecompositionResult(
            sub_plans=[{"metric": "gmv"}],
            strategy="rule",
            reason="test",
            diagnostics={"key": "val"},
        )
        d = r.to_dict()
        self.assertEqual(d["strategy"], "rule")
        self.assertEqual(d["reason"], "test")
        self.assertEqual(d["sub_plan_count"], 1)
        self.assertEqual(d["diagnostics"]["key"], "val")


class TestRuleDecomposer(unittest.TestCase):

    def setUp(self):
        self.decomposer = RuleDecomposer(max_metrics=3, max_dimensions=2)

    def test_no_split_single_metric(self):
        plan = AnalysisPlan(query=u"最近7天GMV", metric="gmv", metrics=["gmv"])
        result = self.decomposer.decompose(plan)
        self.assertIsNone(result)

    def test_no_split_two_metrics(self):
        plan = AnalysisPlan(query=u"最近7天GMV和订单量", metric="gmv", metrics=["gmv", "order_count"])
        result = self.decomposer.decompose(plan)
        self.assertIsNone(result)

    def test_multi_metric_split(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV和订单量和客单价和退款率",
            metric="gmv",
            metrics=["gmv", "order_count", "avg_price", "refund_rate"],
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNotNone(result)
        self.assertEqual(result.strategy, "rule")
        self.assertIn("multi_metric", result.reason)
        self.assertEqual(len(result.sub_plans), 4)

    def test_multi_dimension_split(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV按渠道、品类、地区",
            metric="gmv",
            metrics=["gmv"],
            dimensions=["channel", "category", "region"],
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNotNone(result)
        self.assertEqual(result.strategy, "rule")
        self.assertIn("multi_dimension", result.reason)
        self.assertEqual(len(result.sub_plans), 3)

    def test_overview_expansion(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV和订单量",
            metric="gmv",
            metrics=["gmv", "order_count"],
            intent="metric_query",
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNotNone(result)
        self.assertEqual(result.strategy, "rule")
        self.assertIn("overview_expansion", result.reason)
        # overview + 2 breakdowns
        self.assertEqual(len(result.sub_plans), 3)

    def test_multi_intent_split(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV为什么下降，各渠道贡献如何",
            metric="gmv",
            metrics=["gmv"],
            intent="metric_query",
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNotNone(result)
        self.assertEqual(result.strategy, "rule")
        self.assertIn("multi_intent", result.reason)

    def test_multi_intent_comparison_anomaly(self):
        plan = AnalysisPlan(
            query=u"对比上月GMV，分析异常原因",
            metric="gmv",
            metrics=["gmv"],
            intent="metric_query",
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNotNone(result)
        self.assertEqual(result.strategy, "rule")
        self.assertIn("multi_intent", result.reason)

    def test_no_split_single_dimension(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV按渠道",
            metric="gmv",
            metrics=["gmv"],
            dimensions=["channel"],
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNone(result)

    def test_no_split_two_metrics_one_dimension(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV和订单量按渠道",
            metric="gmv",
            metrics=["gmv", "order_count"],
            dimensions=["channel"],
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNone(result)

    def test_split_by_metric_sub_plan_fields(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV和订单量和客单价和退款率",
            metric="gmv",
            metrics=["gmv", "order_count", "avg_price", "refund_rate"],
            dimensions=["channel"],
            time_range="last_7d",
        )
        result = self.decomposer.decompose(plan)
        self.assertIsNotNone(result)
        for sub in result.sub_plans:
            self.assertIn("metric", sub)
            self.assertIn("metrics", sub)
            self.assertEqual(len(sub["metrics"]), 1)
            self.assertIn("task_id", sub)
            self.assertIn("parent_task_id", sub)
            self.assertIn("decompose_strategy", sub)
            self.assertIn("decompose_reason", sub)
            # Should preserve other fields
            self.assertEqual(sub["time_range"], "last_7d")
            self.assertEqual(sub["dimensions"], ["channel"])


class TestTaskDecomposer(unittest.TestCase):

    def setUp(self):
        self.decomposer = TaskDecomposer(max_metrics=3, max_dimensions=2)

    def test_terminal_status_skips_decompose(self):
        plan = AnalysisPlan(query=u"删除数据库", status="blocked", metric="gmv")
        result = self.decomposer.decompose(plan)
        self.assertEqual(result.strategy, "no_split")
        self.assertIn("terminal_status", result.reason)
        self.assertEqual(len(result.sub_plans), 1)

    def test_fallback_no_split(self):
        plan = AnalysisPlan(query=u"最近7天GMV", metric="gmv", metrics=["gmv"])
        result = self.decomposer.decompose(plan)
        self.assertEqual(result.strategy, "no_split")
        self.assertEqual(len(result.sub_plans), 1)

    def test_rule_decompose_triggered(self):
        plan = AnalysisPlan(
            query=u"最近7天GMV和订单量和客单价和退款率",
            metric="gmv",
            metrics=["gmv", "order_count", "avg_price", "refund_rate"],
        )
        result = self.decomposer.decompose(plan)
        self.assertEqual(result.strategy, "rule")
        self.assertGreater(len(result.sub_plans), 1)

    def test_dict_plan_input(self):
        plan = {
            "query": u"最近7天GMV和订单量和客单价",
            "metric": "gmv",
            "metrics": ["gmv", "order_count", "avg_price"],
            "status": "ok",
            "intent": "metric_query",
        }
        result = self.decomposer.decompose(plan)
        self.assertEqual(result.strategy, "rule")
        # overview_expansion: 1 overview + 3 breakdowns = 4
        self.assertEqual(len(result.sub_plans), 4)



    def test_llm_fallback_to_rule(self):
        """Test that LLM decomposer failure falls back to rule."""
        class FailingLLMDecomposer(object):
            def decompose(self, plan, query=None):
                raise Exception("LLM failed")

        decomposer = TaskDecomposer(
            max_metrics=2,
            max_dimensions=2,
            use_llm=True,
            llm_decomposer=FailingLLMDecomposer(),
        )
        plan = AnalysisPlan(
            query=u"最近7天GMV和订单量和客单价",
            metric="gmv",
            metrics=["gmv", "order_count", "avg_price"],
        )
        result = decomposer.decompose(plan)
        self.assertEqual(result.strategy, "rule")
        self.assertEqual(len(result.sub_plans), 3)

    def test_llm_returns_result(self):
        """Test that LLM decomposer result is used when available."""
        class MockLLMDecomposer(object):
            def decompose(self, plan, query=None):
                return DecompositionResult(
                    sub_plans=[{"metric": "gmv"}, {"metric": "order_count"}],
                    strategy="llm",
                    reason="llm split",
                )

        decomposer = TaskDecomposer(
            max_metrics=2,
            max_dimensions=2,
            use_llm=True,
            llm_decomposer=MockLLMDecomposer(),
        )
        plan = AnalysisPlan(
            query=u"最近7天GMV和订单量",
            metric="gmv",
            metrics=["gmv", "order_count"],
        )
        result = decomposer.decompose(plan)
        self.assertEqual(result.strategy, "llm")
        self.assertEqual(len(result.sub_plans), 2)


if __name__ == "__main__":
    unittest.main()
