# -*- coding: utf-8 -*-
"""Tests for task-specific post-execution analysis strategies."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_registry_uses_comparison_payload_contract():
    from analysis_strategies import analyze_execution_result

    result = analyze_execution_result(
        {"task_type": "comparison", "metric": "gmv", "previous_time_range": ["2026-01-01", "2026-01-31"]},
        {"results": [{"gmv": 100}, {"gmv": 160}], "diagnostics": {"quality": {"status": "ok"}}},
    )
    assert result["type"] == "comparison"
    assert result["comparison"]["delta"] == 60
    assert result["summary_facts"]["delta_pct"] == 0.6
    assert result["definition"]["previous_time_range"] == ["2026-01-01", "2026-01-31"]


def test_anomaly_payload_exposes_data_quality_and_anomalies():
    from analysis_strategies import analyze_execution_result

    result = analyze_execution_result(
        {"task_type": "anomaly", "metric": "gmv", "time_dimension": "day", "analysis_config": {"anomaly_threshold": 1.0}},
        {"results": [{"gmv": 10}, {"gmv": 11}, {"gmv": 100}], "diagnostics": {"quality": {"status": "ok", "messages": []}}},
    )
    assert result["type"] == "anomaly"
    assert result["data_quality"]["row_count"] == 3
    assert result["summary_facts"]["anomaly_count"] >= 1
    assert result["definition"]["time_dimension"] == "day"


def test_attribution_payload_has_drivers_and_definition():
    from analysis_strategies import analyze_execution_result

    result = analyze_execution_result(
        {"task_type": "attribution", "metric": "gmv", "dimensions": ["channel"]},
        {"results": [{"channel": "a", "current": 140, "previous": 100}, {"channel": "b", "current": 80, "previous": 100}], "diagnostics": {"quality": {"status": "ok"}}},
    )
    assert result["type"] == "attribution"
    assert result["definition"]["attribution_dimension"] == "channel"
    assert result["top_drivers"][0]["dimension"] == "a"


def test_descriptive_empty_result_is_explicitly_marked():
    from analysis_strategies import analyze_execution_result

    result = analyze_execution_result(
        {"task_type": "descriptive", "metric": "gmv"},
        {"results": [], "diagnostics": {"quality": {"empty_result": True, "status": "warning"}}},
    )
    assert result["type"] == "descriptive"
    assert result["status"] == "insufficient_data"
    assert result["data_quality"]["empty_result"] is True


if __name__ == "__main__":
    test_registry_uses_comparison_payload_contract()
    test_anomaly_payload_exposes_data_quality_and_anomalies()
    test_attribution_payload_has_drivers_and_definition()
    test_descriptive_empty_result_is_explicitly_marked()
    print("All analysis strategy tests passed!")
