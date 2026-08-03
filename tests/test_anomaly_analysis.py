# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from analysis_strategies import AnomalyAnalysisStrategy
from analysis_output import standardize_analysis_output
from report_templates import AnomalyReportTemplate


def _analyze(rows, metric="order_count"):
    return AnomalyAnalysisStrategy().analyze({"task_type": "anomaly", "metric": metric}, {"status": "ok", "results": rows})


def test_anomaly_identifies_point():
    rows = [{"day": "d1", "order_count": 100}, {"day": "d2", "order_count": 102}, {"day": "d3", "order_count": 98}, {"day": "d4", "order_count": 200}]
    a = _analyze(rows)
    assert a["summary_facts"]["anomaly_count"] >= 1


def test_anomaly_severity_present():
    rows = [{"day": "d1", "order_count": 100}, {"day": "d2", "order_count": 100}, {"day": "d3", "order_count": 100}, {"day": "d4", "order_count": 250}]
    a = _analyze(rows)
    assert a["severity_summary"]["max_severity"] in ("medium", "high", "low", "none")


def test_anomaly_has_possible_causes_when_anomalous():
    rows = [{"day": "d1", "order_count": 100}, {"day": "d2", "order_count": 101}, {"day": "d3", "order_count": 99}, {"day": "d4", "order_count": 220}]
    a = _analyze(rows)
    assert a["possible_causes"]


def test_anomaly_next_steps_in_output():
    rows = [{"day": "d1", "order_count": 100}, {"day": "d2", "order_count": 101}, {"day": "d3", "order_count": 99}, {"day": "d4", "order_count": 220}]
    a = _analyze(rows)
    out = standardize_analysis_output({"task_type": "anomaly", "metric": "order_count"}, {"status": "ok", "analysis": a}, analysis=a)
    assert out["contract"] == "analysis_output_v1"
    assert out["next_steps"]


def test_anomaly_report_mentions_severity():
    rows = [{"day": "d1", "order_count": 100}, {"day": "d2", "order_count": 101}, {"day": "d3", "order_count": 99}, {"day": "d4", "order_count": 220}]
    a = _analyze(rows)
    report = AnomalyReportTemplate().render({"task_type": "anomaly", "metric": "order_count", "analysis": a})
    assert u"严重度" in report["headline"] or any(u"严重度" in x for x in report["key_findings"])
