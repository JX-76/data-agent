# -*- coding: utf-8 -*-
"""Tests for advanced analysis helpers and task-type presentation policies."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_comparison_delta():
    from advanced_analysis import build_comparison
    result = build_comparison([{"gmv": 100}, {"gmv": 150}], metric="gmv")
    assert result["status"] == "ok"
    assert result["delta"] == 50
    assert result["delta_pct"] == 0.5


def test_anomaly_detection_shape():
    from advanced_analysis import detect_anomalies
    result = detect_anomalies([{"gmv": 10}, {"gmv": 11}, {"gmv": 100}], metric="gmv", threshold=1.0)
    assert result["status"] == "ok"
    assert "items" in result


def test_attribution_top_drivers():
    from advanced_analysis import attribute_change
    result = attribute_change([
        {"channel": "a", "current": 150, "previous": 100},
        {"channel": "b", "current": 80, "previous": 100},
    ], metric="gmv", dimension="channel")
    assert result["top_drivers"][0]["dimension"] == "a"


def test_funnel_and_retention_shapes():
    from advanced_analysis import build_funnel, build_retention
    funnel = build_funnel([{"step": "visit", "users": 100}, {"step": "pay", "users": 20}])
    retention = build_retention([{"cohort": "2026-07", "period": "D1", "retention_rate": 0.4}])
    assert funnel["steps"][1]["step_conversion_rate"] == 0.2
    assert "2026-07" in retention["cohorts"]


def test_chart_policy_by_task_type():
    from chart_policy import select_chart
    assert select_chart({"task_type": "comparison"}, {})["type"] == "line"
    assert select_chart({"task_type": "funnel"}, {})["type"] == "funnel"
    assert select_chart({"task_type": "retention"}, {})["type"] == "heatmap"


def test_insight_contains_advanced_analysis():
    from result_explainer import build_insight_bundle
    analysis = {
        "type": "comparison",
        "status": "ok",
        "comparison": {"status": "ok", "delta": 50, "delta_pct": 0.5},
        "summary_facts": {"delta": 50, "delta_pct": 0.5},
    }
    insight = build_insight_bundle(
        {"status": "ok", "task_type": "comparison", "metric": "gmv"},
        {"status": "ok", "task_type": "comparison", "metric": "gmv", "results": [{"gmv": 100}, {"gmv": 150}], "analysis": analysis},
    ).to_dict()
    assert insight["raw"]["advanced_analysis"] == analysis
    assert "变化量" in insight["summary"]


if __name__ == "__main__":
    test_comparison_delta()
    test_anomaly_detection_shape()
    test_attribution_top_drivers()
    test_funnel_and_retention_shapes()
    test_chart_policy_by_task_type()
    test_insight_contains_advanced_analysis()
    print("All advanced analysis tests passed!")
