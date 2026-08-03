# -*- coding: utf-8 -*-
"""Tests for product-facing report generation."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


R17_FIELDS = ["headline", "summary", "key_findings", "evidence", "chart", "caveats", "recommendations", "methodology"]


def _analysis_output(task_type="descriptive", status="ok", chart_type="bar", row_count=2):
    return {
        "contract": "analysis_output_v1",
        "type": task_type,
        "status": status,
        "summary": u"分析已完成。" if status == "ok" else u"当前请求未生成正常分析结果。",
        "key_findings": [u"结果已按当前计划生成。"] if status == "ok" else [],
        "evidence": {"metric": "gmv", "dimensions": ["channel"], "row_count": row_count, "source": "mock"},
        "chart": {"type": chart_type, "x": "channel", "y": "gmv", "reason": "dimension breakdown"} if chart_type else {"type": "none"},
        "caveats": [],
        "next_steps": [u"继续下钻"],
        "raw": {"source_analysis": {"type": task_type, "summary_facts": {"row_count": row_count}}, "source_insight": {}},
    }


def _assert_r17_shape(report):
    for key in R17_FIELDS:
        assert key in report
    # legacy compatibility
    for key in ["conclusion", "data_scope", "next_actions", "chart_hint", "confidence", "task_type", "raw"]:
        assert key in report


def test_product_report_descriptive_shape():
    from report_generator import build_product_report

    report = build_product_report({
        "query": "最近7天GMV",
        "status": "ok",
        "task_type": "descriptive",
        "metric": "gmv",
        "dimensions": ["channel"],
        "results_summary": {"row_count": 2, "source": "mock"},
        "chart": {"type": "bar"},
        "analysis": {"summary": "分析已完成。", "next_steps": ["继续下钻"]},
    }).to_dict()

    _assert_r17_shape(report)
    assert "conclusion" in report
    assert report["task_type"] == "descriptive"
    assert report["data_scope"]["metric"] == "gmv"
    assert report["data_scope"]["dimensions"] == ["channel"]
    assert report["chart_hint"]["type"] == "bar"
    assert report["chart"]["type"] == "bar"
    assert report["key_findings"]
    assert "继续下钻" in report["next_actions"]
    assert "继续下钻" in report["recommendations"]


def test_product_report_task_type_templates():
    from report_generator import build_product_report

    comparison = build_product_report({"status": "ok", "task_type": "comparison", "metric": "gmv"}).to_dict()
    anomaly = build_product_report({"status": "ok", "task_type": "anomaly", "metric": "gmv"}).to_dict()
    attribution = build_product_report({"status": "ok", "task_type": "attribution", "metric": "gmv"}).to_dict()
    funnel = build_product_report({"status": "ok", "task_type": "funnel", "metric": "gmv"}).to_dict()
    blocked = build_product_report({"status": "blocked", "task_type": "descriptive"}).to_dict()

    assert "对比分析" in comparison["conclusion"]
    assert "异常检测" in anomaly["conclusion"]
    assert "归因分析" in attribution["conclusion"]
    assert "漏斗分析" in funnel["conclusion"]
    assert "拦截" in blocked["conclusion"]


def test_default_report_includes_analysis_section():
    from report_generator import generate_report

    report = generate_report({
        "title": "对比分析",
        "summary": "对比分析已完成。",
        "analysis": {"type": "comparison", "status": "ok", "summary_facts": {"delta": 50, "delta_pct": 0.5}},
        "chart": {"type": "line"},
        "results": [{"gmv": 100}, {"gmv": 150}],
    }).to_dict()

    section_titles = [section["title"] for section in report["sections"]]
    assert "Analysis" in section_titles
    analysis_section = [section for section in report["sections"] if section["title"] == "Analysis"][0]
    assert analysis_section["metadata"]["analysis_type"] == "comparison"
    assert analysis_section["metadata"]["analysis_status"] == "ok"


def test_product_report_uses_analysis_summary_facts():
    from report_generator import build_product_report

    comparison = build_product_report({
        "status": "ok",
        "task_type": "comparison",
        "metric": "gmv",
        "analysis": {"type": "comparison", "summary_facts": {"delta": 50, "delta_pct": 0.5}},
    }).to_dict()
    anomaly = build_product_report({
        "status": "ok",
        "task_type": "anomaly",
        "metric": "gmv",
        "analysis": {"type": "anomaly", "summary_facts": {"anomaly_count": 2}},
    }).to_dict()
    attribution = build_product_report({
        "status": "ok",
        "task_type": "attribution",
        "metric": "gmv",
        "analysis": {"type": "attribution", "top_drivers": [{"dimension": "channel_a", "delta": 20}]},
    }).to_dict()

    assert any("变化量" in item for item in comparison["key_findings"])
    assert any("异常点" in item for item in anomaly["key_findings"])
    assert any("首要驱动" in item for item in attribution["key_findings"])
    assert comparison["raw"]["analysis"]["type"] == "comparison"


def test_report_generator_product_method():
    from report_generator import ReportGenerator

    generator = ReportGenerator()
    report = generator.generate_product_report({"status": "need_clarification", "query": "看一下数据"}).to_dict()
    assert report["data_scope"]["query"] == "看一下数据"
    assert "需要补充" in report["conclusion"]
    assert report["chart"]["type"] == "none"


def test_product_report_consumes_analysis_output_v1_for_all_r17_task_types():
    from report_generator import build_product_report

    expected_chart = {
        "descriptive": "bar",
        "comparison": "grouped_bar",
        "attribution": "waterfall",
        "anomaly": "line_with_anomaly",
        "funnel": "funnel",
    }
    for task_type, chart_type in expected_chart.items():
        payload = _analysis_output(task_type=task_type, chart_type=chart_type)
        report = build_product_report(payload).to_dict()
        _assert_r17_shape(report)
        assert report["task_type"] == task_type
        assert report["summary"] == payload["summary"]
        assert report["evidence"]["metric"] == "gmv"
        assert report["chart"]["type"] == chart_type
        assert report["recommendations"]
        assert report["methodology"]


def test_product_report_terminal_and_empty_result_cases():
    from report_generator import build_product_report

    cases = [
        ("blocked", u"拦截"),
        ("need_clarification", u"补充"),
        ("error", u"失败"),
    ]
    for status, keyword in cases:
        payload = _analysis_output(status=status, chart_type="none", row_count=0)
        payload["summary"] = u"终态摘要"
        report = build_product_report(payload).to_dict()
        _assert_r17_shape(report)
        assert keyword in report["headline"]
        assert report["chart"]["type"] == "none"

    empty = _analysis_output(status="ok", chart_type="none", row_count=0)
    empty["caveats"] = [u"查询结果为空，可能是时间范围无数据或过滤条件过严。"]
    report = build_product_report(empty).to_dict()
    _assert_r17_shape(report)
    assert report["evidence"]["row_count"] == 0
    assert report["chart"]["type"] == "none"
    assert report["caveats"]


if __name__ == "__main__":
    test_product_report_descriptive_shape()
    test_product_report_task_type_templates()
    test_default_report_includes_analysis_section()
    test_product_report_uses_analysis_summary_facts()
    test_report_generator_product_method()
    test_product_report_consumes_analysis_output_v1_for_all_r17_task_types()
    test_product_report_terminal_and_empty_result_cases()
    print("All report generator tests passed!")
