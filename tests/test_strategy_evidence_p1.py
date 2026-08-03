# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis_strategies import AnomalyAnalysisStrategy, AttributionAnalysisStrategy, ForecastAnalysisStrategy
from experiment_analysis import analyze_experiment
from experiment_registry import ExperimentDefinition
from strategy_evidence import assess_strategy_evidence, has_verified_execution


def _verified_execution(rows):
    return {
        "status": "ok",
        "results": rows,
        "execution_envelope": {
            "status": "ok",
            "authority": "verified_execution",
            "evidence_id": "ev_test_1",
        },
    }


def test_strategy_evidence_requires_verified_execution_for_attribution():
    rows = [{"channel": "app", "gmv": 80}, {"channel": "web", "gmv": 20}]
    unverified = {"status": "ok", "results": rows}

    assessment = assess_strategy_evidence("attribution", {"dimensions": ["channel"], "metric": "gmv"}, unverified)

    assert assessment["ok"] is False
    assert "verified_execution_evidence_missing" in assessment["reasons"]
    assert has_verified_execution(unverified) is False


def test_attribution_empty_execution_returns_need_more_data_not_driver_claim():
    result = AttributionAnalysisStrategy().analyze(
        {"task_type": "attribution", "metric": "gmv", "dimensions": ["channel"]},
        {"status": "ok", "results": [], "diagnostics": {"quality": {"empty_result": True}}},
    )

    assert result["status"] == "need_more_data"
    assert result["items"] == []
    assert result["summary_facts"]["row_count"] == 0
    assert result["evidence_assessment"]["reasons"]


def test_anomaly_short_history_returns_need_more_data_not_no_anomaly_claim():
    result = AnomalyAnalysisStrategy().analyze(
        {"task_type": "anomaly", "metric": "order_count"},
        _verified_execution([{"day": "d1", "order_count": 100}, {"day": "d2", "order_count": 101}]),
    )

    assert result["status"] == "need_more_data"
    assert "insufficient_rows:need_4_got_2" in result["evidence_assessment"]["reasons"]
    assert "anomalies" not in result


def test_forecast_failed_execution_returns_need_more_data():
    result = ForecastAnalysisStrategy().analyze(
        {"task_type": "forecast", "metric": "gmv"},
        {"status": "error", "results": [], "diagnostics": {"failure_type": "db_timeout"}},
    )

    assert result["status"] == "need_more_data"
    assert any(x.startswith("execution_status_not_ok") for x in result["evidence_assessment"]["reasons"])


def test_experiment_small_sample_returns_need_more_data_not_significance():
    definition = ExperimentDefinition("e", "conversion", "binary_conversion", "uid", minimum_group_size=5)
    rows = [
        {"uid": "a1", "variant": "A", "conversion": 0},
        {"uid": "b1", "variant": "B", "conversion": 1},
    ]

    result = analyze_experiment(definition, rows)

    assert result["status"] == "need_more_data"
    assert result["results"] == []
    assert "experiment_minimum_group_size_not_met" in result["diagnostics"]["errors"]
