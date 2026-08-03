# -*- coding: utf-8 -*-
"""Tests for the extensible product-report template registry."""
from __future__ import unicode_literals

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

R17_FIELDS = set(["headline", "summary", "key_findings", "evidence", "chart", "caveats", "recommendations", "methodology"])
LEGACY_FIELDS = set(["conclusion", "data_scope", "next_actions", "chart_hint", "confidence", "task_type", "raw"])


def _analysis_output(task_type="descriptive", status="ok"):
    return {
        "contract": "analysis_output_v1",
        "type": task_type,
        "status": status,
        "summary": u"分析已完成。",
        "key_findings": [u"结果已按当前计划生成。"],
        "evidence": {"metric": "gmv", "dimensions": ["channel"], "row_count": 2},
        "chart": {"type": "bar", "x": "channel", "y": "gmv", "reason": "dimension breakdown"},
        "caveats": [],
        "next_steps": [u"继续下钻。"],
        "raw": {"source_analysis": {"type": task_type, "summary_facts": {"row_count": 2}}, "source_insight": {}},
    }


def test_registry_selects_task_specific_template():
    from report_templates import build_default_registry, ComparisonReportTemplate, FunnelReportTemplate

    registry = build_default_registry()
    assert isinstance(registry.resolve("comparison"), ComparisonReportTemplate)
    assert isinstance(registry.resolve("funnel"), FunnelReportTemplate)
    assert registry.resolve("future_task") is registry.fallback


def test_registry_has_stable_r17_task_types():
    from report_templates import build_default_registry

    registry = build_default_registry()
    assert set(["descriptive", "comparison", "attribution", "anomaly", "funnel"]).issubset(
        set(registry.registered_task_types()))


def test_registry_custom_template_is_injectable_without_contract_change():
    from report_generator import build_product_report, PRODUCT_REPORT_KEYS
    from report_templates import ReportTemplate, ReportTemplateRegistry

    class RetentionTemplate(ReportTemplate):
        task_type = "retention"

        def conclusion(self, status, metric, analysis):
            return u"留存分析模板已执行。"

    registry = ReportTemplateRegistry()
    registry.register(RetentionTemplate())
    report = build_product_report({"task_type": "retention", "metric": "retention_rate"}, registry=registry).to_dict()

    assert report["conclusion"] == u"留存分析模板已执行。"
    assert report["task_type"] == "retention"
    assert set(report.keys()) == PRODUCT_REPORT_KEYS
    assert R17_FIELDS.issubset(set(report.keys()))
    assert LEGACY_FIELDS.issubset(set(report.keys()))


def test_unknown_task_type_uses_safe_fallback_and_unicode_content():
    from report_generator import build_product_report

    report = build_product_report({
        "task_type": "forecast",
        "metric": "gmv",
        "dimensions": [u"渠道"],
        "analysis": {"summary": u"预测结果可供复核。", "summary_facts": {"row_count": 3}},
    }).to_dict()

    assert report["task_type"] == "forecast"
    assert report["key_findings"]
    assert any(u"渠道" in item for item in report["key_findings"])
    assert any(u"预测结果" in item for item in report["key_findings"])


def test_templates_consume_analysis_output_v1_shape():
    from report_generator import build_product_report

    for task_type in ["descriptive", "comparison", "attribution", "anomaly", "funnel"]:
        payload = _analysis_output(task_type=task_type)
        report = build_product_report(payload).to_dict()
        assert report["task_type"] == task_type
        assert report["summary"] == payload["summary"]
        assert report["evidence"]["metric"] == "gmv"
        assert report["chart"]["type"]
        assert report["methodology"]


if __name__ == "__main__":
    test_registry_selects_task_specific_template()
    test_registry_has_stable_r17_task_types()
    test_registry_custom_template_is_injectable_without_contract_change()
    test_unknown_task_type_uses_safe_fallback_and_unicode_content()
    test_templates_consume_analysis_output_v1_shape()
    print("All report template tests passed!")
