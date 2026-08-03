# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from analysis_strategies import AttributionAnalysisStrategy
from analysis_output import standardize_analysis_output
from report_templates import AttributionReportTemplate


def _analyze(rows, metric="gmv", dimensions=None):
    return AttributionAnalysisStrategy().analyze({"task_type": "attribution", "metric": metric, "dimensions": dimensions or ["channel"]}, {"status": "ok", "results": rows})


def test_attribution_top_drivers():
    a = _analyze([{"channel": "app", "gmv": 80}, {"channel": "web", "gmv": 20}])
    assert a["top_drivers"][0]["dimension"] == "app"
    assert a["summary_facts"]["driver_count"] == 2


def test_attribution_pareto_cutoff():
    a = _analyze([{"channel": "app", "gmv": 80}, {"channel": "web", "gmv": 20}])
    assert a["pareto"]["pareto_cutoff"] == 1


def test_attribution_contribution_breakdown():
    a = _analyze([{"channel": "app", "gmv": 70}, {"channel": "web", "gmv": 30}])
    assert a["contribution"]["shares"][0]["share_pct"] == 0.7


def test_attribution_primary_driver_pct_in_output():
    a = _analyze([{"channel": "app", "gmv": 70}, {"channel": "web", "gmv": 30}])
    out = standardize_analysis_output({"task_type": "attribution", "metric": "gmv", "dimensions": ["channel"]}, {"status": "ok", "analysis": a}, analysis=a)
    assert any(u"首要驱动因素" in x for x in out["key_findings"])


def test_attribution_report_mentions_pareto():
    a = _analyze([{"channel": "app", "gmv": 85}, {"channel": "web", "gmv": 15}])
    report = AttributionReportTemplate().render({"task_type": "attribution", "metric": "gmv", "analysis": a})
    assert any(u"80%" in x for x in report["key_findings"])
