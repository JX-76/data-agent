# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from analysis_strategies import ComparisonAnalysisStrategy
from analysis_output import standardize_analysis_output
from report_templates import ComparisonReportTemplate


def _analyze(rows, metric="gmv", dimensions=None):
    return ComparisonAnalysisStrategy().analyze({"task_type": "comparison", "metric": metric, "dimensions": dimensions or []}, {"status": "ok", "results": rows})


def test_comparison_current_previous_delta_pct():
    a = _analyze([{"period": "previous", "gmv": 100}, {"period": "current", "gmv": 130}])
    assert a["current_value"] == 130
    assert a["previous_value"] == 100
    assert a["delta"] == 30
    assert round(a["delta_pct"], 2) == 0.30


def test_comparison_top_increase_and_decrease_by_dimension():
    rows = [{"channel": "app", "current": 150, "previous": 100}, {"channel": "web", "current": 70, "previous": 100}]
    a = _analyze(rows, dimensions=["channel"])
    assert a["top_increase"]["dimension"] == "app"
    assert a["top_decrease"]["dimension"] == "web"


def test_comparison_direction_decrease():
    a = _analyze([{"current_value": 80, "previous_value": 100}])
    assert a["direction"] == "decrease"
    assert a["delta"] == -20


def test_comparison_analysis_output_contains_findings():
    a = _analyze([{"period": "previous", "gmv": 100}, {"period": "current", "gmv": 120}])
    out = standardize_analysis_output({"task_type": "comparison", "metric": "gmv"}, {"status": "ok", "analysis": a}, analysis=a)
    assert out["contract"] == "analysis_output_v1"
    assert any(u"增长" in x for x in out["key_findings"])


def test_comparison_report_mentions_values():
    a = _analyze([{"period": "previous", "gmv": 100}, {"period": "current", "gmv": 120}])
    report = ComparisonReportTemplate().render({"task_type": "comparison", "metric": "gmv", "analysis": a})
    assert u"当前值" in report["headline"]
