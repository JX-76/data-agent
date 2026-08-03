# -*- coding: utf-8 -*-
"""Focused contract tests for the controlled Forecast task type."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _series(size=16):
    return [{"date": "2026-07-%02d" % (index + 1), "gmv": 100 + index * 5}
            for index in range(size)]


def test_forecast_execution_returns_controlled_contract():
    from forecast_execution import run_forecast

    result = run_forecast("gmv", horizon=3, series=_series())

    assert result["status"] == "ok"
    assert result["task_type"] == "forecast"
    assert len(result["results"]) == 3
    assert result["diagnostics"]["method"] == "seasonal_naive"
    assert result["diagnostics"]["backtest"]["available"] is True
    assert any(u"不构成业务承诺" in item for item in result["caveats"])


def test_forecast_invalid_input_needs_clarification_not_silent_fallback():
    from forecast_execution import run_forecast

    result = run_forecast("unsupported_metric", horizon=1, series=_series())

    assert result["status"] == "need_clarification"
    assert result["task_type"] == "forecast"
    assert result["diagnostics"]["forecast_errors"]


def test_forecast_analysis_output_and_report_are_stable():
    from analysis_output import standardize_analysis_output
    from analysis_strategies import analyze_execution_result
    from forecast_execution import run_forecast
    from report_generator import build_product_report

    plan = {"task_type": "forecast", "metric": "gmv", "time_dimension": "date"}
    execution = run_forecast("gmv", horizon=2, series=_series())
    analysis = analyze_execution_result(plan, execution)
    output = standardize_analysis_output(plan, execution, analysis=analysis)
    report = build_product_report(output).to_dict()

    assert output["contract"] == "analysis_output_v1"
    assert output["type"] == "forecast"
    assert output["chart"]["type"] == "forecast_trend_overlay"
    assert any(u"预测算法" in item for item in output["key_findings"])
    assert report["task_type"] == "forecast"
    assert u"预测分析" in report["headline"]
    assert u"不构成业务承诺" in report["methodology"]


def test_forecast_registry_is_declarative():
    from task_type_registry import get_task_type_registry

    definition = get_task_type_registry().get("forecast").to_dict()
    assert definition["analyzer_name"] == "forecast"
    assert definition["report_template_name"] == "forecast"
    assert definition["chart_policy"]["chart_type"] == "forecast_trend_overlay"
