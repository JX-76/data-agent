# -*- coding: utf-8 -*-
"""Tests for chart spec + chart policy."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_chart_spec_defaults():
    from chart_spec import make_chart_spec, normalize_chart_spec

    spec = make_chart_spec()
    assert spec["type"] == "none"
    assert spec["data"] == []

    normalized = normalize_chart_spec({"type": "bar", "x": "channel", "y": "gmv"})
    assert normalized["type"] == "bar"
    assert normalized["x"] == "channel"
    assert normalized["y"] == "gmv"
    assert normalized["policy_id"] == "external_or_unspecified"
    assert normalized["explanation"] == ""


def test_chart_spec_adds_policy_explanation_from_reason():
    from chart_spec import make_chart_spec, normalize_chart_spec

    spec = make_chart_spec(type="line", reason="trend analysis")
    assert spec["policy_id"] == "time_trend"
    assert u"折线图" in spec["explanation"]

    normalized = normalize_chart_spec({"type": "bar", "reason": "dimension breakdown"})
    assert normalized["policy_id"] == "dimension_breakdown"
    assert u"柱状图" in normalized["explanation"]


def test_recommended_chart_policy_for_r17_task_types():
    from chart_spec import recommend_chart_for_task_type

    expected = {
        "descriptive": "bar",
        "comparison": "grouped_bar",
        "attribution": "waterfall",
        "anomaly": "line_with_anomaly",
        "funnel": "funnel",
    }
    for task_type, chart_type in expected.items():
        spec = recommend_chart_for_task_type(task_type)
        assert spec["type"] == chart_type
        assert spec["reason"]
        assert spec["explanation"]
        assert spec["policy_id"]
        assert spec["policy_id"] == "%s_default" % task_type


def test_recommended_chart_degrades_for_terminal_statuses_and_empty_result():
    from chart_spec import recommend_chart_for_task_type

    expected_policy_ids = {
        "blocked": "blocked_no_chart",
        "need_clarification": "clarification_no_chart",
        "error": "error_no_chart",
        "fallback": "fallback_no_chart",
        "pending_human_review": "pending_review_no_chart",
    }
    for status in ["blocked", "need_clarification", "error", "fallback", "pending_human_review"]:
        spec = recommend_chart_for_task_type("comparison", status=status)
        assert spec["type"] == "none"
        assert spec["reason"]
        assert spec["explanation"]
        assert spec["policy_id"] == expected_policy_ids[status]

    empty = recommend_chart_for_task_type("descriptive", empty_result=True)
    assert empty["type"] == "none"
    assert empty["policy_id"] == "empty_result_no_chart"


def test_chart_policy_task_type_bar_for_dimension_breakdown():
    from chart_policy import select_chart

    spec = select_chart({"task_type": "descriptive", "metric": "gmv", "dimensions": ["channel"]}, {"results": [{"channel": "淘宝", "gmv": 1}]})
    assert spec["type"] == "bar"
    assert spec["x"] == "channel"
    assert spec["y"] == "gmv"


def test_chart_policy_line_for_time_trend():
    from chart_policy import select_chart

    spec = select_chart({"task_type": "descriptive", "metric": "gmv", "dimensions": ["date"]}, {})
    assert spec["type"] == "line"
    assert spec["x"] == "date"


def test_chart_policy_comparison_grouped_bar():
    from chart_policy import select_chart

    spec = select_chart({"task_type": "comparison", "metric": "gmv", "dimensions": ["channel"]}, {})
    assert spec["type"] == "grouped_bar"
    assert spec["reason"] == "comparison"


def test_chart_policy_anomaly_line():
    from chart_policy import select_chart

    spec = select_chart({"task_type": "anomaly", "metric": "gmv"}, {"anomalies": [{"date": "2026-07-01"}]})
    assert spec["type"] == "line_with_anomaly"
    assert spec["annotations"]


def test_chart_policy_attribution_waterfall():
    from chart_policy import select_chart

    spec = select_chart({"task_type": "attribution", "metric": "gmv", "dimensions": ["channel"]}, {})
    assert spec["type"] == "waterfall"


def test_chart_policy_funnel_chart():
    from chart_policy import select_chart

    spec = select_chart({"task_type": "funnel", "metric": "users"}, {"results": [{"stage": "visit", "users": 100}]})
    assert spec["type"] == "funnel"
    assert spec["reason"] == "funnel conversion"


def test_chart_policy_none_for_blocked_status():
    from chart_policy import select_chart

    spec = select_chart({"status": "blocked", "task_type": "descriptive"}, {})
    assert spec["type"] == "none"


def test_chart_policy_none_for_empty_result_and_error():
    from chart_policy import select_chart

    empty = select_chart({"task_type": "descriptive"}, {"status": "ok", "results_summary": {"row_count": 0}})
    assert empty["type"] == "none"
    assert empty["policy_id"] == "empty_result_no_chart"

    error = select_chart({"task_type": "comparison"}, {"status": "error"})
    assert error["type"] == "none"
    assert error["policy_id"] == "error_no_chart"


def test_report_generator_uses_chart_spec_shape():
    from report_generator import build_product_report

    report = build_product_report({
        "query": "最近7天GMV",
        "status": "ok",
        "task_type": "descriptive",
        "metric": "gmv",
        "dimensions": ["channel"],
        "chart": {"type": "bar", "x": "channel", "y": "gmv"},
    }).to_dict()

    assert report["chart_hint"]["type"] == "bar"
    assert report["chart_hint"]["x"] == "channel"
    assert "data" in report["chart_hint"]
    assert "explanation" in report["chart_hint"]
    assert "policy_id" in report["chart_hint"]
    assert report["chart_hint"]["policy_id"] == "external_or_unspecified"
    assert report["chart"] == report["chart_hint"]


if __name__ == "__main__":
    test_chart_spec_defaults()
    test_chart_spec_adds_policy_explanation_from_reason()
    test_recommended_chart_policy_for_r17_task_types()
    test_recommended_chart_degrades_for_terminal_statuses_and_empty_result()
    test_chart_policy_task_type_bar_for_dimension_breakdown()
    test_chart_policy_line_for_time_trend()
    test_chart_policy_comparison_grouped_bar()
    test_chart_policy_anomaly_line()
    test_chart_policy_attribution_waterfall()
    test_chart_policy_funnel_chart()
    test_chart_policy_none_for_blocked_status()
    test_chart_policy_none_for_empty_result_and_error()
    test_report_generator_uses_chart_spec_shape()
    print("All chart spec tests passed!")
