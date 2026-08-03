# -*- coding: utf-8 -*-
"""Tests for evaluation baseline contract."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_default_baseline_shape():
    from eval_baseline import BASELINE_DEFAULTS, get_eval_baseline

    baseline = get_eval_baseline()
    data = baseline.to_dict()

    assert data["name"] == "default"
    assert "metrics" in data
    assert data["metrics"]["route_accuracy_min"] == BASELINE_DEFAULTS["route_accuracy_min"]
    assert data["metrics"]["task_type_accuracy_min"] == BASELINE_DEFAULTS["task_type_accuracy_min"]
    assert data["metrics"]["contract_pass_rate_min"] == BASELINE_DEFAULTS["contract_pass_rate_min"]
    assert data["metrics"]["sql_success_min"] == BASELINE_DEFAULTS["sql_success_min"]
    assert data["metrics"]["avg_latency_ms_max"] == BASELINE_DEFAULTS["avg_latency_ms_max"]


def test_evaluate_cases_metrics_and_failures():
    from eval_baseline import evaluate_cases

    cases = [
        {
            "id": "c1",
            "query": "最近7天GMV",
            "expected": {"status": "ok", "intent": "metric_query", "task_type": "descriptive", "metric": "gmv", "dimensions": [], "requires_sql": True},
        },
        {
            "id": "c2",
            "query": "GMV异常吗",
            "expected": {"status": "ok", "task_type": "anomaly", "metric": "gmv", "requires_sql": False},
        },
        {
            "id": "c3",
            "query": "看一下数据",
            "expected": {"status": "need_clarification"},
        },
    ]

    def runner(query):
        if "异常" in query:
            return {"status": "ok", "task_type": "anomaly", "metric": "gmv"}
        if "看一下" in query:
            return {"status": "need_clarification"}
        return {"status": "ok", "intent": "metric_query", "task_type": "descriptive", "metric": "gmv", "dimensions": [], "sql": "SELECT 1"}

    result = evaluate_cases(cases, runner).to_dict()
    assert result["total"] == 3
    assert result["passed"] == 3
    assert result["failed"] == []
    assert result["metrics"]["task_type_accuracy"] == 1.0
    assert result["metrics"]["contract_pass_rate"] == 1.0
    assert result["metrics"]["clarification_hit_rate"] == 1.0


def test_evaluate_cases_detects_mismatch():
    from eval_baseline import evaluate_cases

    cases = [{"id": "bad", "query": "各渠道GMV", "expected": {"status": "ok", "metric": "gmv", "dimensions": ["channel"], "requires_sql": True}}]
    result = evaluate_cases(cases, lambda query: {"status": "ok", "metric": "order_count", "dimensions": [], "sql": "SELECT 1"}).to_dict()
    assert result["passed"] == 0
    assert len(result["failed"]) == 1
    assert "metric expected=gmv" in result["failed"][0]["errors"][0]


def test_evaluate_gate_pass_and_fail():
    from eval_baseline import EvalBaseline, evaluate_gate

    baseline = EvalBaseline(name="ci", metrics={"route_accuracy_min": 0.8, "avg_latency_ms_max": 1000})
    passed = evaluate_gate({"metrics": {"route_accuracy": 0.9, "avg_latency_ms": 500}}, baseline).to_dict()
    failed = evaluate_gate({"metrics": {"route_accuracy": 0.7, "avg_latency_ms": 1200}}, baseline).to_dict()

    assert passed["passed"] is True
    assert passed["failures"] == []
    assert "route_accuracy_min" in passed["checked"]
    assert failed["passed"] is False
    assert len(failed["failures"]) == 2
    assert "below min" in failed["failures"][0] or "above max" in failed["failures"][0]


def test_custom_baseline_roundtrip():
    from eval_baseline import EvalBaseline

    baseline = EvalBaseline(
        name="ci",
        metrics={"route_accuracy_min": 0.9, "avg_latency_ms_max": 900},
        metadata={"version": "1.0"},
    )
    data = baseline.to_dict()
    restored = EvalBaseline.from_dict(data)

    assert restored.name == "ci"
    assert restored.metrics["route_accuracy_min"] == 0.9
    assert restored.metrics["avg_latency_ms_max"] == 900
    assert restored.metadata["version"] == "1.0"


if __name__ == "__main__":
    test_default_baseline_shape()
    test_evaluate_cases_metrics_and_failures()
    test_evaluate_cases_detects_mismatch()
    test_evaluate_gate_pass_and_fail()
    test_custom_baseline_roundtrip()
    print("All eval baseline tests passed!")
