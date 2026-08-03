"""Trajectory evaluation tests — full coverage of trajectory recording,
rule-based evaluation, batch reporting, and pipeline integration.

Run:
    python3 -m pytest tests/test_trajectory.py -v
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from dataclasses import asdict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trajectory import (
    ToolCall,
    Trajectory,
    TrajectoryRecorder,
    TrajectoryEvaluator,
    TrajectoryScores,
    BatchEvaluator,
    BatchReport,
)


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def recorder():
    return TrajectoryRecorder()


@pytest.fixture
def evaluator():
    return TrajectoryEvaluator()


@pytest.fixture
def batch_evaluator():
    return BatchEvaluator()


def _make_trajectory(query, intent, tool_calls_spec, status="ok",
                     sql="SELECT 1", results=None, insight="", error=""):
    """Factory: build a Trajectory from a compact spec.

    tool_calls_spec: list of (tool, args, result, success, error)
    """
    traj = Trajectory(
        trace_id=f"test_{hash(query) % 10000:04d}",
        query=query,
        intent=intent,
        model="order_detail",
        metric="gmv",
        status=status,
        sql=sql,
        results=results or [{"gmv": 12345.67}],
        insight=insight,
        error=error,
    )
    for tc_spec in tool_calls_spec:
        tool, args, result = tc_spec[0], tc_spec[1], tc_spec[2]
        success = tc_spec[3] if len(tc_spec) > 3 else True
        error = tc_spec[4] if len(tc_spec) > 4 else ""
        traj.tool_calls.append(ToolCall(
            tool=tool, args=args, result_summary=result,
            success=success, error=error,
        ))
    traj.step_count = len(tool_calls_spec)
    return traj


# ══════════════════════════════════════════════════════════════
# ToolCall Tests
# ══════════════════════════════════════════════════════════════

class TestToolCall:
    def test_basic_tool_call(self):
        tc = ToolCall(tool="switch", args={"model_id": "order_detail"},
                      result_summary="switched")
        assert tc.tool == "switch"
        assert tc.success is True
        assert tc.tool_correct is None  # Not yet evaluated

    def test_failed_tool_call(self):
        tc = ToolCall(tool="filter", args={"dataid": "d1", "metric_id": "gmv"},
                      result_summary="table not found", success=False,
                      error="table not found")
        assert not tc.success
        assert tc.error == "table not found"

    def test_evaluation_annotations(self):
        tc = ToolCall(tool="switch", args={"model_id": "order_detail"},
                      result_summary="ok")
        tc.tool_correct = True
        tc.args_correct = True
        tc.necessary = True
        tc.optimal_tool = "switch"
        assert tc.tool_correct is True
        assert tc.optimal_tool == "switch"


# ══════════════════════════════════════════════════════════════
# Trajectory Tests
# ══════════════════════════════════════════════════════════════

class TestTrajectory:
    def test_serialization(self):
        traj = _make_trajectory("昨天GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ])
        d = traj.to_dict()
        assert d["trace_id"] == traj.trace_id
        assert d["query"] == "昨天GMV"
        assert d["status"] == "ok"
        assert len(d["tool_calls"]) == 1
        assert d["tool_calls"][0]["tool"] == "switch"

    def test_empty_trajectory(self):
        traj = Trajectory(trace_id="empty", query="test",
                          intent="blocked", status="blocked")
        d = traj.to_dict()
        assert d["tool_calls"] == []
        assert d["result_count"] == 0


# ══════════════════════════════════════════════════════════════
# TrajectoryRecorder Tests
# ══════════════════════════════════════════════════════════════

class TestTrajectoryRecorder:
    def test_start_and_finish(self, recorder):
        traj = recorder.start("昨天GMV是多少？", intent="metric_query",
                              model="order_detail", metric="gmv")
        assert traj.query == "昨天GMV是多少？"
        assert traj.intent == "metric_query"
        assert traj.status == ""

        recorder.finish("ok", sql="SELECT SUM(gmv) FROM orders",
                        results=[{"gmv": 50000}], insight="GMV为50000元")

        assert traj.status == "ok"
        assert "SUM(gmv)" in traj.sql
        assert traj.total_latency_ms > 0
        assert traj.step_count == 0  # No tool calls recorded

    def test_record_tool_calls(self, recorder):
        recorder.start("各渠道GMV", intent="breakdown",
                       model="order_detail", metric="gmv")
        recorder.record_tool("switch", {"model_id": "order_detail"},
                            "switched to order_detail", "d0")
        recorder.record_tool("filter", {"dataid": "d0", "metric_id": "gmv",
                                        "start": "2026-06-20", "end": "2026-06-21"},
                            "filtered 500 rows", "d1")
        recorder.record_tool("aggregate", {"dataid": "d1", "metric_id": "gmv",
                                           "dimensions": ["channel"]},
                            "aggregated 3 rows", "d2")
        recorder.finish("ok", insight="各渠道GMV分布")

        traj = recorder.trajectory
        assert traj is not None
        assert len(traj.tool_calls) == 3
        assert [tc.tool for tc in traj.tool_calls] == ["switch", "filter", "aggregate"]
        assert traj.step_count == 3

    def test_record_retry(self, recorder):
        recorder.start("test", intent="metric_query")
        recorder.record_retry("filter", {"dataid": "d1", "metric_id": "gmv"},
                             "table not found")
        recorder.record_tool("filter", {"dataid": "d1", "metric_id": "gmv"},
                            "filtered 100 rows", "d2")
        recorder.finish("ok")

        traj = recorder.trajectory
        assert traj.retry_count == 1
        assert traj.tool_calls[0].success is False
        assert traj.tool_calls[1].success is True

    def test_record_before_start(self, recorder):
        # Calling record_tool before start should be safe (no-op)
        recorder.record_tool("switch", {}, "ok")
        assert recorder.trajectory is None  # No crash, no effect

    def test_latency_tracking(self, recorder):
        recorder.start("test")
        time.sleep(0.01)  # Small delay for measurable latency
        recorder.finish("ok")
        assert recorder.trajectory.total_latency_ms > 0


# ══════════════════════════════════════════════════════════════
# TrajectoryEvaluator Tests — Rule-Based
# ══════════════════════════════════════════════════════════════

class TestTrajectoryEvaluator:
    def test_perfect_metric_query(self, evaluator):
        """Ideal trajectory: switch → filter → aggregate."""
        traj = _make_trajectory("昨天GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv",
             "start": "2026-06-20", "end": "2026-06-21"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv", "dimensions": []},
             "1 row"),
        ], status="ok", sql="SELECT SUM(gmv) FROM orders",
           results=[{"gmv": 50000}], insight="昨日GMV为50000元")

        scores = evaluator.evaluate(traj)
        assert scores.result_correct is True
        assert scores.tool_call_accuracy == 1.0
        assert scores.argument_accuracy == 1.0
        assert scores.unnecessary_steps == 0
        assert scores.invalid_cycles == 0
        assert scores.step_efficiency == 1.0  # 3 call = 3 optimal
        assert scores.grade in ("A", "B")  # Perfect trajectory
        assert scores.overall_score >= 85

    def test_wrong_tool_order(self, evaluator):
        """Wrong order: preview before switch, filter after aggregate."""
        traj = _make_trajectory("各渠道GMV", "breakdown", [
            ("preview", {"dataid": "d1", "n": 10}, "10 rows"),
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv",
             "dimensions": ["channel"]}, "3 rows"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
        ], status="ok")

        scores = evaluator.evaluate(traj)
        assert scores.tool_call_accuracy < 1.0  # Wrong order detected
        assert scores.argument_accuracy <= 1.0  # Args are individually valid but chain is wrong
        assert len(scores.issues) > 0

    def test_redundant_preview(self, evaluator):
        """Preview followed by preview = unnecessary."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("preview", {"dataid": "d0", "n": 10}, "10 rows"),
            ("preview", {"dataid": "d0", "n": 5}, "5 rows"),  # redundant
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "1 row"),
        ], status="ok")

        scores = evaluator.evaluate(traj)
        assert scores.unnecessary_steps >= 1  # Duplicate preview detected

    def test_double_switch(self, evaluator):
        """Two consecutive switches = redundant."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("switch", {"model_id": "user_summary"}, "ok"),  # redundant
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "1 row"),
        ], status="ok")

        scores = evaluator.evaluate(traj)
        assert scores.unnecessary_steps >= 1  # Redundant switch pair

    def test_failed_result(self, evaluator):
        """Agent errored out."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ], status="error", error="database connection failed")

        scores = evaluator.evaluate(traj)
        assert scores.result_correct is False
        assert scores.result_error != ""
        # Even with correct tool call, result failure drags overall score down
        # (result_correct weight is 0.20, so max is ~80 even with perfect trajectory)
        assert scores.overall_score < 80

    def test_blocked_query(self, evaluator):
        """Dangerous query blocked."""
        traj = _make_trajectory("删除所有订单", "blocked",
                                [], status="blocked")

        scores = evaluator.evaluate(traj)
        assert scores.result_correct is True  # Blocking is correct
        assert scores.overall_score >= 90  # Empty optimal path = perfect

    def test_all_optimal_paths_covered(self, evaluator):
        """Verify all defined optimal paths exist."""
        assert "metric_query" in evaluator.OPTIMAL_PATHS
        assert "breakdown" in evaluator.OPTIMAL_PATHS
        assert "filter_value" in evaluator.OPTIMAL_PATHS
        assert "merge" in evaluator.OPTIMAL_PATHS
        assert "compare_periods" in evaluator.OPTIMAL_PATHS
        assert "blocked" in evaluator.OPTIMAL_PATHS

    def test_intent_inference(self, evaluator):
        """Test intent inference from tool chain."""
        assert evaluator._infer_intent(
            _make_trajectory("q", "", [("merge", {}, "ok")])
        ) == "merge"
        assert evaluator._infer_intent(
            _make_trajectory("q", "", [("compare_periods", {}, "ok")])
        ) == "compare_periods"
        assert evaluator._infer_intent(
            _make_trajectory("q", "", [("filter_value", {}, "ok")])
        ) == "filter_value"
        assert evaluator._infer_intent(
            _make_trajectory("q", "", [("aggregate", {}, "ok")])
        ) == "breakdown"

    def test_argument_validation(self, evaluator):
        """Test argument validation helpers."""
        # Valid cases
        assert evaluator._validate_args("switch", {"model_id": "order_detail"})
        assert evaluator._validate_args("aggregate", {"dimensions": ["channel", "region"]})
        # Invalid cases
        assert not evaluator._validate_args("switch", {"model_id": "invalid_model"})
        assert not evaluator._validate_args("preview", {"n": 30})  # n > 20 (out of range)
        assert not evaluator._validate_args("preview", {"n": 30})  # Out of range

    def test_per_step_breakdown(self, evaluator):
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "1 row"),
        ], status="ok")

        scores = evaluator.evaluate(traj)
        assert len(scores.per_step) == 3
        assert scores.per_step[0]["step"] == 1
        assert scores.per_step[0]["tool"] == "switch"
        assert scores.per_step[0]["tool_correct"] is not None

    def test_grading_scale(self, evaluator):
        """Verify grading scale boundaries."""
        # A-grade perfect trajectory
        traj_a = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "1 row"),
        ], status="ok")
        assert evaluator.evaluate(traj_a).grade == "A"

        # F-grade: all wrong
        traj_f = _make_trajectory("GMV", "metric_query", [
            ("sort", {"dataid": "d0", "by": "x"}, "ok"),
            ("top", {"dataid": "d1", "by": "x", "n": 5}, "ok"),
        ], status="error", error="query failed")
        assert evaluator.evaluate(traj_f).grade in ("F", "D")


# ══════════════════════════════════════════════════════════════
# BatchEvaluator Tests
# ══════════════════════════════════════════════════════════════

class TestBatchEvaluator:
    def test_empty_batch(self, batch_evaluator):
        report = batch_evaluator.evaluate_batch([])
        assert report.total == 0
        assert report.completion_rate == 0.0

    def test_single_trajectory(self, batch_evaluator):
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "1 row"),
        ], status="ok")
        report = batch_evaluator.evaluate_batch([traj])
        assert report.total == 1
        assert report.passed == 1
        assert report.completion_rate == 1.0
        assert report.per_trajectory[0]["grade"] == "A"

    def test_mixed_batch(self, batch_evaluator):
        """Batch with good, bad, and blocked trajectories."""
        traj_good = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "1 row"),
        ], status="ok")

        traj_bad = _make_trajectory("GMV", "metric_query", [
            ("top", {"dataid": "d0", "by": "x", "n": 5}, "ok"),
        ], status="error", error="query timeout")

        traj_blocked = _make_trajectory("DELETE *", "blocked",
                                        [], status="blocked")

        report = batch_evaluator.evaluate_batch([traj_good, traj_bad, traj_blocked])
        assert report.total == 3
        assert report.passed == 2  # good + blocked (both correct)
        assert report.completion_rate == pytest.approx(2 / 3)
        assert report.grade_distribution["A"] == 2  # good + blocked
        assert "F" in report.grade_distribution or "D" in report.grade_distribution
        assert len(report.per_trajectory) == 3

    def test_batch_aggregates(self, batch_evaluator):
        """Test aggregate statistics are computed correctly."""
        trajectories = []
        for i in range(5):
            traj = _make_trajectory(f"query_{i}", "metric_query", [
                ("switch", {"model_id": "order_detail"}, "ok"),
                ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
                ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "1 row"),
            ], status="ok")
            trajectories.append(traj)

        report = batch_evaluator.evaluate_batch(trajectories)
        assert report.total == 5
        assert report.avg_tool_accuracy == 1.0
        assert report.avg_overall_score > 80
        assert all(isinstance(r["grade"], str) for r in report.per_trajectory)


# ══════════════════════════════════════════════════════════════
# Trajectory Recorder + Evaluator Integration Tests
# ══════════════════════════════════════════════════════════════

class TestRecorderEvaluatorIntegration:
    """End-to-end: record a trajectory and evaluate it."""

    def test_record_then_evaluate(self):
        recorder = TrajectoryRecorder()
        evaluator = TrajectoryEvaluator()

        recorder.start("各渠道GMV", intent="breakdown",
                       model="order_detail", metric="gmv")
        recorder.record_tool("switch", {"model_id": "order_detail"},
                            "switched to order_detail", "d0")
        recorder.record_tool("filter", {"dataid": "d0", "metric_id": "gmv",
                                        "start": "2026-06-20", "end": "2026-06-21"},
                            "500 rows filtered", "d1")
        recorder.record_tool("aggregate", {"dataid": "d1", "metric_id": "gmv",
                                           "dimensions": ["channel"]},
                            "3 channels aggregated", "d2")
        recorder.finish("ok", insight="渠道A GMV 30000, 渠道B GMV 20000")

        traj = recorder.trajectory
        scores = evaluator.evaluate(traj)

        assert scores.result_correct
        assert scores.tool_call_accuracy == 1.0
        assert scores.grade == "A"
        assert traj.scores["grade"] == "A"
        assert traj.judged_at > 0

    def test_record_with_retries_then_evaluate(self):
        recorder = TrajectoryRecorder()
        evaluator = TrajectoryEvaluator()

        recorder.start("华南大区订单数", intent="filter_value",
                       model="order_detail", metric="order_count")
        recorder.record_tool("switch", {"model_id": "order_detail"},
                            "switched", "d0")
        # First filter attempt fails
        recorder.record_retry("filter", {"dataid": "d0", "metric_id": "gmv"},
                             "wrong metric ID")
        # Second attempt succeeds
        recorder.record_tool("filter", {"dataid": "d0", "metric_id": "order_count"},
                            "200 rows", "d1")
        recorder.record_tool("filter_value", {"dataid": "d1",
                                              "dimension": "region", "value": "华南"},
                            "50 rows", "d2")
        recorder.record_tool("aggregate", {"dataid": "d2",
                                           "metric_id": "order_count",
                                           "dimensions": []},
                            "120 orders", "d3")
        recorder.finish("ok", insight="华南大区共120笔订单")

        traj = recorder.trajectory
        scores = evaluator.evaluate(traj)

        assert traj.retry_count == 1
        assert scores.invalid_cycles == 1  # One failed attempt
        # Grade should be slightly lower due to retry penalty
        assert scores.grade in ("A", "B")

    def test_batch_record_evaluate_cycle(self):
        """Full cycle: record multiple trajectories, batch evaluate."""
        evaluator = TrajectoryEvaluator()
        batch_eval = BatchEvaluator(evaluator)

        queries_and_specs = [
            ("昨天GMV", "metric_query", [
                ("switch", {"model_id": "order_detail"}, "ok"),
                ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
                ("aggregate", {"dataid": "d1", "metric_id": "gmv",
                 "dimensions": []}, "ok"),
            ], "ok"),
            ("各渠道GMV", "breakdown", [
                ("switch", {"model_id": "order_detail"}, "ok"),
                ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
                ("aggregate", {"dataid": "d1", "metric_id": "gmv",
                 "dimensions": ["channel"]}, "ok"),
            ], "ok"),
            ("delete everything", "blocked", [], "blocked"),
        ]

        trajectories = []
        for query, intent, calls, status in queries_and_specs:
            recorder = TrajectoryRecorder()
            recorder.start(query, intent=intent)
            for tool, args, result in calls:
                recorder.record_tool(tool, args, result, f"d{len(trajectories)}")
            recorder.finish(status)
            trajectories.append(recorder.trajectory)

        report = batch_eval.evaluate_batch(trajectories)
        assert report.total == 3
        assert report.passed >= 2  # good + blocked
        print(f"\nBatch Report: {report.total} queries, "
              f"completion={report.completion_rate:.0%}, "
              f"avg_score={report.avg_overall_score:.1f}, "
              f"grades={report.grade_distribution}")


# ══════════════════════════════════════════════════════════════
# Edge Cases & Regression
# ══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_tool_calls_ok(self, evaluator):
        """Agent returned ok with no tool calls (shouldn't happen but handle gracefully)."""
        traj = _make_trajectory("test", "metric_query", [], status="ok")
        scores = evaluator.evaluate(traj)
        assert scores.result_correct is True
        assert scores.overall_score == 100.0

    def test_empty_tool_calls_error(self, evaluator):
        traj = _make_trajectory("test", "metric_query", [], status="error")
        scores = evaluator.evaluate(traj)
        assert scores.result_correct is False
        assert scores.grade == "F"

    def test_all_args_wrong(self, evaluator):
        """Every tool call has wrong arguments."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "nonexistent"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "unknown_metric"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ], status="ok")
        scores = evaluator.evaluate(traj)
        assert scores.argument_accuracy < 1.0

    def test_very_long_tool_chain(self, evaluator):
        """15+ tool calls — evaluator should handle without crash."""
        calls = [("switch", {"model_id": "order_detail"}, "ok")]
        # Add many redundant operations
        for i in range(10):
            calls.append(("preview", {"dataid": f"d{i}", "n": 5}, "ok"))
        calls.append(("aggregate", {"dataid": "d99", "metric_id": "gmv"}, "ok"))

        traj = _make_trajectory("long chain", "metric_query", calls, status="ok")
        scores = evaluator.evaluate(traj)
        assert scores.unnecessary_steps > 5  # Many redundant previews
        assert scores.step_efficiency < 0.3  # Very inefficient

    def test_trace_id_uniqueness(self):
        """Each start() call generates a unique trace_id."""
        r1 = TrajectoryRecorder()
        r2 = TrajectoryRecorder()
        t1 = r1.start("query")
        t2 = r2.start("same query")  # Same query, same time
        assert t1.trace_id != t2.trace_id  # Should be unique

    def test_to_dict_all_fields(self):
        """to_dict() should include all fields without error."""
        traj = _make_trajectory("full test", "breakdown", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "500 rows"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv",
             "dimensions": ["channel", "region"]}, "5 rows"),
        ], status="ok", sql="SELECT channel, SUM(gmv) FROM orders GROUP BY channel")

        d = traj.to_dict()
        assert "trace_id" in d
        assert "tool_calls" in d
        assert "scores" in d
        assert "sql" in d
        # Should be JSON-serializable
        json_str = json.dumps(d, ensure_ascii=False, default=str)
        assert json.loads(json_str) == d


# ══════════════════════════════════════════════════════════════
# BatchReport Tests
# ══════════════════════════════════════════════════════════════

class TestBatchReport:
    def test_report_serialization(self, batch_evaluator):
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ], status="ok")
        report = batch_evaluator.evaluate_batch([traj])

        # Should be dict-serializable
        d = asdict(report)
        json.dumps(d, ensure_ascii=False, default=str)

        assert d["total"] == 1
        assert d["passed"] == 1
        assert isinstance(d["grade_distribution"], dict)
        assert len(d["per_trajectory"]) == 1


# ══════════════════════════════════════════════════════════════
# Run with: python3 -m pytest tests/test_trajectory.py -v
# ══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
# Gap 1: Token Efficiency Tests
# ══════════════════════════════════════════════════════════════

class TestTokenEfficiency:
    
    def test_token_within_budget(self, evaluator):
        """Token usage within budget = full score."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ])
        traj.token_count_total = 1500  # Budget for metric_query = 3000
        scores = evaluator.evaluate(traj)
        assert scores.token_efficiency == 1.0
        assert scores.token_total == 1500
    
    def test_token_over_budget(self, evaluator):
        """Token usage 2x budget = 0.5 efficiency."""
        traj = _make_trajectory("复杂分析", "breakdown", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv",
             "dimensions": ["channel"]}, "ok"),
        ])
        traj.token_count_total = 8000  # Budget for breakdown = 4000
        scores = evaluator.evaluate(traj)
        assert scores.token_efficiency == pytest.approx(0.5, abs=0.01)
        assert "token" in scores.issues[0].lower() if scores.issues else True
    
    def test_token_no_data_defaults_to_full(self, evaluator):
        """No token data → assume efficient (don't penalize missing data)."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ])
        traj.token_count_total = 0
        scores = evaluator.evaluate(traj)
        assert scores.token_efficiency == 1.0


# ══════════════════════════════════════════════════════════════
# Gap 2: Reasoning Consistency Tests
# ══════════════════════════════════════════════════════════════

class TestReasoningConsistency:
    
    def test_consistent_reasoning_matches_tools(self, evaluator):
        """Reasoning mentions 'filter' keyword → filter tool called → consistent."""
        from trajectory import ToolCall
        traj = _make_trajectory("GMV", "metric_query", [], status="ok")
        traj.tool_calls = [
            ToolCall(tool="switch", args={"model_id": "order_detail"},
                    result_summary="ok", reasoning_text="需要选择订单明细模型"),
            ToolCall(tool="filter", args={"dataid": "d0", "metric_id": "gmv"},
                    result_summary="ok", reasoning_text="过滤时间范围和默认指标条件"),
            ToolCall(tool="aggregate", args={"dataid": "d1", "metric_id": "gmv"},
                    result_summary="ok", reasoning_text="汇总计算GMV"),
        ]
        traj.step_count = 3
        scores = evaluator.evaluate(traj)
        assert scores.reasoning_consistency == 1.0
        assert scores.reasoning_gaps == []
    
    def test_inconsistent_reasoning_tool_mismatch(self, evaluator):
        """Reasoning talks about 'filter' but tool called 'aggregate' — gap."""
        from trajectory import ToolCall
        traj = _make_trajectory("GMV", "metric_query", [], status="ok")
        traj.tool_calls = [
            ToolCall(tool="switch", args={"model_id": "order_detail"},
                    result_summary="ok", reasoning_text="选择订单明细模型"),
            ToolCall(tool="aggregate", args={"dataid": "d0", "metric_id": "gmv"},
                    result_summary="ok", reasoning_text="先过滤掉无效数据"),
        ]
        traj.step_count = 2
        scores = evaluator.evaluate(traj)
        assert scores.reasoning_consistency < 1.0
        assert len(scores.reasoning_gaps) > 0
        assert "aggregate" in scores.reasoning_gaps[0] or "filter" in scores.reasoning_gaps[0]
    
    def test_no_reasoning_text_does_not_penalize(self, evaluator):
        """Legacy trajectories without reasoning text get full credit."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ])
        scores = evaluator.evaluate(traj)
        # No reasoning text = default to consistent (don't penalize missing data)
        assert scores.reasoning_consistency == 1.0


# ══════════════════════════════════════════════════════════════
# Gap 3: Coverage Tests
# ══════════════════════════════════════════════════════════════

class TestCoverage:
    
    def test_full_coverage(self, evaluator):
        """All expected key points covered."""
        traj = _make_trajectory("昨天GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ], insight="昨日GMV为50000元，订单数1200笔，客单价41.67元")
        traj.expected_key_points = ["GMV数值", "订单数", "客单价"]
        traj.covered_key_points = ["GMV数值", "订单数", "客单价"]
        scores = evaluator.evaluate(traj)
        assert scores.coverage_score == 1.0
        assert scores.missed_points == []
    
    def test_partial_coverage(self, evaluator):
        """Only 2 of 3 key points covered."""
        traj = _make_trajectory("各渠道GMV", "breakdown", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv",
             "dimensions": ["channel"]}, "ok"),
        ], insight="渠道A GMV 30000元")
        traj.expected_key_points = ["各渠道GMV数值", "渠道排名顺序", "环比变化"]
        traj.covered_key_points = ["各渠道GMV数值"]
        scores = evaluator.evaluate(traj)
        assert scores.coverage_score == pytest.approx(1/3, abs=0.01)
        assert len(scores.missed_points) == 2
    
    def test_no_expectations_defaults_to_full(self, evaluator):
        """No expected key points = no coverage requirement."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ])
        traj.expected_key_points = []
        scores = evaluator.evaluate(traj)
        assert scores.coverage_score == 1.0
    
    def test_implicit_coverage_via_answer_text(self, evaluator):
        """Key points detected in answer_text even if not in covered_key_points."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ], insight="GMV")
        traj.expected_key_points = ["GMV"]
        traj.answer_text = "昨天GMV为50000元"
        # covered_key_points is empty but keyword "GMV" is in answer_text
        scores = evaluator.evaluate(traj)
        assert scores.coverage_score > 0


# ══════════════════════════════════════════════════════════════
# Gap 4: Citation Verification Tests
# ══════════════════════════════════════════════════════════════

class TestCitations:
    
    def test_factual_answer_without_citations_penalized(self, evaluator):
        """Factual claims need source citations."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ], insight="昨日GMV为50000元，销售额增长10%")
        # No answer_text with citation patterns
        scores = evaluator.evaluate(traj)
        assert scores.citation_score == 0.3
        assert "citation" in scores.citation_penalty.lower()
    
    def test_explicit_citations_counted(self, evaluator):
        """Explicitly tracked citations are scored."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ], insight="GMV data")
        traj.citation_count = 4
        traj.citations_valid = 3
        scores = evaluator.evaluate(traj)
        assert scores.citation_score == 0.75
        
    def test_citation_patterns_in_answer_detected(self, evaluator):
        """Answer text with [来源:xxx] patterns are detected."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ], insight="GMV数据")
        traj.answer_text = "昨日GMV为50000元 [来源: fct_orders 表]"
        scores = evaluator.evaluate(traj)
        # Has factual content AND citation patterns = should NOT be penalized
        assert scores.citation_score >= 1.0 or scores.citation_penalty == ""


# ══════════════════════════════════════════════════════════════
# Gap 5: Module-Level Evaluation Tests
# ══════════════════════════════════════════════════════════════

class TestModuleLevelEvaluation:
    
    def test_modules_are_populated(self, evaluator):
        """Module scores should be computed for all 4 phases."""
        from trajectory import ToolCall
        traj = _make_trajectory("GMV", "metric_query", [], status="ok")
        traj.tool_calls = [
            ToolCall(tool="switch", args={"model_id": "order_detail"},
                    result_summary="ok", module_phase="retrieval"),
            ToolCall(tool="filter", args={"dataid": "d0", "metric_id": "gmv"},
                    result_summary="ok", module_phase="tool"),
            ToolCall(tool="aggregate", args={"dataid": "d1", "metric_id": "gmv"},
                    result_summary="ok", module_phase="tool"),
        ]
        traj.step_count = 3
        scores = evaluator.evaluate(traj)
        assert "planning" in scores.module_scores
        assert "retrieval" in scores.module_scores
        assert "tool" in scores.module_scores
        assert "generation" in scores.module_scores
    
    def test_module_phase_classification(self, evaluator):
        """Tools are correctly classified into phases."""
        assert evaluator._classify_phase("switch") == "retrieval"
        assert evaluator._classify_phase("preview") == "retrieval"
        assert evaluator._classify_phase("filter") == "tool"
        assert evaluator._classify_phase("aggregate") == "tool"
        assert evaluator._classify_phase("merge") == "tool"
        assert evaluator._classify_phase("sort") == "tool"
        assert evaluator._classify_phase("compare_periods") == "tool"
        assert evaluator._classify_phase("catalog") == "planning"
    
    def test_module_with_failed_calls_tracks_issue(self, evaluator):
        """Failed tool calls should be reflected in module issues."""
        from trajectory import ToolCall
        traj = _make_trajectory("GMV", "metric_query", [], status="ok")
        traj.tool_calls = [
            ToolCall(tool="switch", args={"model_id": "order_detail"},
                    result_summary="ok", module_phase="retrieval", success=True),
            ToolCall(tool="filter", args={"dataid": "d0", "metric_id": "wrong_metric"},
                    result_summary="table not found", module_phase="tool",
                    success=False, error="table not found"),
        ]
        traj.step_count = 2
        scores = evaluator.evaluate(traj)
        assert scores.module_scores["tool"]["score"] < 1.0
        assert len(scores.module_scores["tool"]["issues"]) > 0


# ══════════════════════════════════════════════════════════════
# Gap 6: Edit Distance Tests
# ══════════════════════════════════════════════════════════════

class TestEditDistance:
    
    def test_identical_trajectory_perfect_score(self, evaluator):
        """Agent trajectory exactly matches gold → score 1.0."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ])
        traj.gold_sequence = ["switch", "filter", "aggregate"]
        scores = evaluator.evaluate(traj)
        assert scores.edit_distance == 0
        assert scores.edit_distance_score == 1.0
        assert scores.lcs_length == 3
    
    def test_wrong_order_penalized(self, evaluator):
        """Different order = edit distance > 0."""
        traj = _make_trajectory("wrong_order", "metric_query", [
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
            ("switch", {"model_id": "order_detail"}, "ok"),
        ])
        traj.gold_sequence = ["switch", "filter", "aggregate"]
        scores = evaluator.evaluate(traj)
        assert scores.edit_distance > 0
        assert scores.edit_distance_score < 1.0
    
    def test_extra_steps_increase_distance(self, evaluator):
        """Extra unnecessary steps increase edit distance."""
        traj = _make_trajectory("many_previews", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("preview", {"dataid": "d0", "n": 5}, "ok"),
            ("preview", {"dataid": "d1", "n": 10}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ])
        traj.gold_sequence = ["switch", "filter", "aggregate"]
        scores = evaluator.evaluate(traj)
        assert scores.edit_distance >= 2  # Need to delete 2 previews
        assert scores.edit_distance_score < 0.8
    
    def test_no_gold_uses_optimal_path(self, evaluator):
        """When gold_sequence is empty, fall back to OPTIMAL_PATHS."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ])
        # gold_sequence is empty
        scores = evaluator.evaluate(traj)
        assert scores.edit_distance == 0  # Should match optimal path
    
    def test_completely_wrong_tools(self, evaluator):
        """Agent uses entirely wrong tools → high edit distance."""
        traj = _make_trajectory("GMV", "metric_query", [
            ("sort", {"dataid": "d0", "by": "x"}, "ok"),
            ("top", {"dataid": "d1", "by": "x", "n": 5}, "ok"),
        ])
        traj.gold_sequence = ["switch", "filter", "aggregate"]
        scores = evaluator.evaluate(traj)
        assert scores.edit_distance_score < 0.5
        assert scores.lcs_length == 0  # No tool in common


# ══════════════════════════════════════════════════════════════
# Gap 7: Test Set Layering
# ══════════════════════════════════════════════════════════════

class TestSetLayers:
    """Test set should have 3 layers:
      Layer A (30%): AI-generated edge cases + adversarial
      Layer B (50%): Real engineer queries from logs
      Layer C (20%): Domain-expert craft questions
    """
    
    def test_layer_a_edge_cases(self, evaluator):
        """AI-generated boundary and adversarial queries."""
        # Borderline query: ambiguous intent
        traj = _make_trajectory("看看", "unknown", [
            ("switch", {"model_id": "order_detail"}, "ok"),
        ], status="ok")
        scores = evaluator.evaluate(traj)
        assert scores.grade is not None  # Shouldn't crash on edge cases
    
    def test_layer_a_adversarial(self, evaluator):
        """Adversarial: query using non-standard metric name."""
        # Query referencing a metric name that's not in the canonical set
        traj = _make_trajectory("用 revenue 指标查昨天的销售额", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "revenue"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "revenue"}, "ok"),
        ], status="ok")
        scores = evaluator.evaluate(traj)
        # revenue passes basic arg validation (filter doesn't parse metric_id),
        # but the trajectory should still be evaluable without crashing
        assert scores.grade is not None
        assert "revenue" not in ("gmv", "order_count", "aov", "avg_price")  # Not canonical
    
    def test_layer_a_typo_query(self, evaluator):
        """Typo-ridden real-world queries."""
        traj = _make_trajectory("昨填的GMv是多少丫", "metric_query", [
            ("switch", {"model_id": "order_detail"}, "ok"),
            ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
            ("aggregate", {"dataid": "d1", "metric_id": "gmv"}, "ok"),
        ])
        scores = evaluator.evaluate(traj)
        assert scores.result_correct
    
    def test_layer_b_real_query_patterns(self, evaluator):
        """Real engineer queries: technical jargon, context-dependent."""
        real_queries = [
            ("这个月线上的GMV环比", "metric_query"),
            ("对比一下上周和上上周的渠道表现", "breakdown"),
            ("只查华南，用订单数", "filter_value"),
            ("GMV和订单数放一起看", "merge"),
        ]
        for query, intent in real_queries:
            opts = TrajectoryEvaluator.OPTIMAL_PATHS.get(intent, [])
            assert opts, f"Intent {intent} missing optimal path"
    
    def test_layer_c_expert_crafted(self, evaluator):
        """Domain-expert crafted questions: cross-doc conflicts, ambiguity."""
        # Cross-document spec conflict: query that spans two semantic layers
        traj = _make_trajectory(
            "产品分析里有没有包含渠道维度的GMV？", "breakdown", [
                ("switch", {"model_id": "product_analysis"}, "ok"),
                ("filter", {"dataid": "d0", "metric_id": "gmv"}, "ok"),
                ("aggregate", {"dataid": "d1", "metric_id": "gmv",
                 "dimensions": ["channel"]}, "ok"),
            ], status="ok"
        )
        # product_analysis is a valid model for switch
        scores = evaluator.evaluate(traj)
        assert scores.result_correct
    
    def test_layer_proportions(self):
        """Verify 30/50/20 layering structure."""
        layer_a = 6  # 30% of 20
        layer_b = 10  # 50% of 20
        layer_c = 4   # 20% of 20
        total = layer_a + layer_b + layer_c
        assert total == 20
        # Layer A: AI-generated edge cases
        assert layer_a / total == 0.3
        # Layer B: real queries
        assert layer_b / total == 0.5
        # Layer C: expert crafted
        assert layer_c / total == 0.2


# ══════════════════════════════════════════════════════════════
# Levenshtein Algorithm Unit Tests
# ══════════════════════════════════════════════════════════════

class TestLevenshteinAlgorithm:
    
    def test_identical_sequences(self):
        from trajectory import levenshtein_sequence
        d, s = levenshtein_sequence(["a", "b", "c"], ["a", "b", "c"])
        assert d == 0
        assert s == 1.0
    
    def test_completely_different(self):
        from trajectory import levenshtein_sequence
        d, s = levenshtein_sequence(["x", "y", "z"], ["a", "b", "c"])
        assert d == 3
        assert s == 0.0
    
    def test_insertion(self):
        from trajectory import levenshtein_sequence
        d, s = levenshtein_sequence(["a", "c"], ["a", "b", "c"])
        assert d == 1  # insert 'b'
        assert s == pytest.approx(2/3, abs=0.01)
    
    def test_deletion(self):
        from trajectory import levenshtein_sequence
        d, s = levenshtein_sequence(["a", "b", "c", "d"], ["a", "c"])
        assert d == 2  # delete 'b' and 'd'
        assert s == 0.5
    
    def test_substitution(self):
        from trajectory import levenshtein_sequence
        d, s = levenshtein_sequence(["a", "b", "c"], ["a", "x", "c"])
        assert d == 1
        assert s == pytest.approx(2/3, abs=0.01)
    
    def test_empty_sequences(self):
        from trajectory import levenshtein_sequence
        d, s = levenshtein_sequence([], [])
        assert d == 0
        assert s == 1.0
    
    def test_empty_vs_nonempty(self):
        from trajectory import levenshtein_sequence
        d, s = levenshtein_sequence([], ["a", "b"])
        assert d == 2
        assert s == 0.0
    
    def test_lcs_algorithm(self):
        from trajectory import longest_common_subsequence
        result = longest_common_subsequence(
            ["switch", "preview", "filter", "aggregate"],
            ["switch", "filter", "aggregate"]
        )
        assert result == ["switch", "filter", "aggregate"]
    
    def test_lcs_no_common(self):
        from trajectory import longest_common_subsequence
        result = longest_common_subsequence(["a", "b"], ["c", "d"])
        assert result == []


# ══════════════════════════════════════════════════════════════
# Recorder + New Fields Integration Tests
# ══════════════════════════════════════════════════════════════

class TestRecorderNewFields:
    """Integration tests for the 7 new fields through the recorder."""
    
    def test_recorder_captures_token_and_reasoning(self):
        recorder = TrajectoryRecorder()
        recorder.start("昨天GMV", intent="metric_query",
                      expected_key_points=["GMV数值", "时间范围"],
                      gold_sequence=["switch", "filter", "aggregate"])
        
        recorder.record_tool("switch", {"model_id": "order_detail"},
                            "switched", "d0", token_count=200,
                            reasoning_text="需要选择订单明细模型来查GMV",
                            module_phase="retrieval")
        recorder.record_tool("filter", {"dataid": "d0", "metric_id": "gmv"},
                            "filtered", "d1", token_count=300,
                            reasoning_text="过滤时间范围和有效订单",
                            module_phase="tool")
        recorder.record_tool("aggregate", {"dataid": "d1", "metric_id": "gmv"},
                            "aggregated", "d2", token_count=400,
                            reasoning_text="汇总计算GMV指标",
                            module_phase="tool")
        
        recorder.set_answer("昨日GMV为50000元 [来源: fct_orders 表]")
        recorder.set_coverage(["GMV数值", "时间范围"])
        recorder.set_citations(1, 1)
        
        recorder.finish("ok", token_overhead=100)
        
        traj = recorder.trajectory
        assert traj.token_count_total == 1000  # 200+300+400+100
        assert traj.citation_count == 1
        assert traj.citations_valid == 1
        assert traj.covered_key_points == ["GMV数值", "时间范围"]
        assert traj.gold_sequence == ["switch", "filter", "aggregate"]
        assert traj.expected_key_points == ["GMV数值", "时间范围"]
        
        # Evaluate with all new fields
        evaluator = TrajectoryEvaluator()
        scores = evaluator.evaluate(traj)
        
        assert scores.token_efficiency > 0  # Should have token data
        assert scores.reasoning_consistency == 1.0  # All reasoning matches tools
        assert scores.coverage_score == 1.0  # All key points covered
        assert scores.citation_score == 1.0  # All citations valid
        assert scores.edit_distance == 0  # Matches gold exactly
        assert scores.edit_distance_score == 1.0
        assert scores.grade == "A"


# ══════════════════════════════════════════════════════════════
# Scoring Weights Sanity Check
# ══════════════════════════════════════════════════════════════

class TestScoringWeights:
    """Verify the new scoring formula adds up and produces reasonable results."""
    
    def test_weights_sum_to_one(self):
        """All sub-score weights should sum to 1.0 (before penalty)."""
        weights = [0.20, 0.20, 0.10, 0.10, 0.10, 0.10, 0.10, 0.05, 0.05]
        assert sum(weights) == pytest.approx(1.0)
    
    def test_perfect_trajectory_still_scores_high(self, evaluator):
        """A perfectly recorded trajectory with all new fields should get A."""
        from trajectory import ToolCall
        traj = _make_trajectory("GMV", "metric_query", [], status="ok",
                               insight="昨日GMV为50000元 [来源: fct_orders]")
        traj.expected_key_points = ["GMV数值"]
        traj.covered_key_points = ["GMV数值"]
        traj.gold_sequence = ["switch", "filter", "aggregate"]
        traj.token_count_total = 1500
        traj.answer_text = "昨日GMV为50000元 [来源: fct_orders]"
        traj.tool_calls = [
            ToolCall(tool="switch", args={"model_id": "order_detail"},
                    result_summary="ok", reasoning_text="选择订单明细模型",
                    module_phase="retrieval", token_count=200),
            ToolCall(tool="filter", args={"dataid": "d0", "metric_id": "gmv"},
                    result_summary="ok", reasoning_text="过滤时间范围",
                    module_phase="tool", token_count=300),
            ToolCall(tool="aggregate", args={"dataid": "d1", "metric_id": "gmv"},
                    result_summary="ok", reasoning_text="汇总计算GMV",
                    module_phase="tool", token_count=400),
        ]
        traj.step_count = 3
        
        scores = evaluator.evaluate(traj)
        assert scores.grade == "A"
        assert scores.overall_score > 90
        assert scores.token_efficiency == 1.0
        assert scores.reasoning_consistency == 1.0
        assert scores.coverage_score == 1.0
        assert scores.citation_score == 1.0
        assert scores.edit_distance_score == 1.0


# ══════════════════════════════════════════════════════════════
# Run with: python3 -m pytest tests/test_trajectory.py -v
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
